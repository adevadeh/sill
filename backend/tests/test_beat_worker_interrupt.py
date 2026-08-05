"""Ctrl-C stops the beat worker cleanly.

`docs/onboarding/03-first-beats.md` tells an operator to run the worker in
the foreground and stop it with Ctrl-C. The v0.2.0 clean-machine acceptance
rehearsal could not check that — it ran the worker detached, where SIGINT is
ignored by inheritance, and stopped it with SIGTERM — so
`docs/RELEASE-REHEARSAL.md` listed it unverified and guessed at the
behaviour. Run under a pty, the guess was half right: the worker printed an
eleven-frame traceback ending in `KeyboardInterrupt` and exited on the
signal, both mid-beat and while sleeping between beats. (The other half of
the guess — that a mid-beat Ctrl-C might hold the terminal until the 30
minute beat timeout — was wrong: it returned in 0.05 s, because
`subprocess.run()` kills the child on its way out.)

Unlike `test_beat_worker.py`, which monkeypatches `subprocess.run` and never
spawns anything, these tests need a real process with a real controlling
terminal: the defect is in signal delivery, which cannot be faked. The agent
CLI is a `/bin/sh` stub, so nothing here talks to a model, a database, or
the network.

Why a process group rather than `proc.send_signal()`: a terminal's ^C sends
SIGINT to the whole foreground process group, worker *and* child agent CLI
together. Signalling only the worker would test a case an operator never
hits and would miss whether the child is left orphaned.
"""

import json
import os
import pty
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKER_PY = BACKEND_ROOT / "worker.py"


def _build_project(root: Path, *, cli_sleep: float, produce_output: bool) -> Path:
    """A one-voice beat project whose 'agent CLI' is a shell stub."""
    (root / "prompts").mkdir(parents=True, exist_ok=True)
    (root / "prompts" / "probe.md").write_text("Standing prompt.\n")
    (root / "notes").mkdir(exist_ok=True)
    (root / "beats.json").write_text(json.dumps({
        "voices": [{
            "name": "probe",
            "prompt": "prompts/probe.md",
            "transcripts": "logs/probe",
            "output_glob": "notes/*.md",
            "kickoff": "Begin.",
        }],
    }))
    cli = root / "fake-cli"
    lines = ["#!/bin/sh"]
    if produce_output:
        lines.append(f'echo made-a-note > "{root}/notes/probe-$$.md"')
    lines.append(f"sleep {cli_sleep}")
    cli.write_text("\n".join(lines) + "\n")
    cli.chmod(0o755)
    return cli


def _run_until_interrupt(root: Path, cli: Path, *, settle: float) -> tuple[int, str]:
    """Start the worker on a pty in its own session, let it settle, then send
    SIGINT to its process group the way a terminal's ^C does. Returns
    (exit status, everything it printed)."""
    env = {
        **os.environ,
        "PYTHONPATH": str(BACKEND_ROOT),
        "PYTHONUNBUFFERED": "1",
        "SILL_PROJECT_ROOT": str(root),
        "SILL_BEAT_CLI": str(cli),
        "SILL_BEAT_INTERVAL_SECONDS": "120",
        "SILL_BEAT_TIMEOUT_SECONDS": "300",
        "SILL_BEAT_STATE_PATH": str(root / "state.json"),
        "SILL_LOG_DIR": str(root / "logs"),
    }
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, str(WORKER_PY), "--mode", "beat"],
        stdin=slave, stdout=slave, stderr=slave,
        env=env, cwd=str(root), start_new_session=True,
    )
    os.close(slave)
    captured: list[str] = []

    def drain(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([master], [], [], 0.05)
            if not ready:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:      # pty closes when the child exits
                return
            if not chunk:
                return
            captured.append(chunk.decode("utf-8", "replace"))

    try:
        drain(settle)
        os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        deadline = time.time() + 15
        while proc.poll() is None and time.time() < deadline:
            drain(0.2)
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait()
            pytest.fail(
                "the beat worker did not exit within 15s of Ctrl-C — output so "
                f"far:\n{''.join(captured)}"
            )
        drain(0.3)
        return proc.returncode, "".join(captured)
    finally:
        os.close(master)


@pytest.mark.skipif(os.name != "posix", reason="pty/process groups are POSIX")
def test_ctrl_c_mid_beat_stops_cleanly(tmp_path):
    """The hard case: a beat is in flight when the operator gives up on it."""
    cli = _build_project(tmp_path, cli_sleep=60, produce_output=False)
    status, output = _run_until_interrupt(tmp_path, cli, settle=3.0)

    assert "Traceback" not in output, (
        "Ctrl-C during a beat printed a Python traceback — the runbook's own "
        f"way of stopping a supervised run must not look like a crash:\n{output}"
    )
    assert "KeyboardInterrupt" not in output
    assert status == 0, f"expected a clean exit, got {status}:\n{output}"
    assert "Interrupted (Ctrl-C)" in output
    # Rotation must not advance past a beat that never finished. With one
    # voice the index is 0 either way, so check the stronger thing: an
    # interrupted beat writes no state file at all.
    assert not (tmp_path / "state.json").exists(), (
        "an interrupted beat wrote rotation state — it did not finish, so it "
        "must not count as taken"
    )


@pytest.mark.skipif(os.name != "posix", reason="pty/process groups are POSIX")
def test_ctrl_c_between_beats_stops_cleanly(tmp_path):
    """The common case: the operator has read the first beat's output and is
    done. The worker is asleep on `time.sleep(INTERVAL)`."""
    cli = _build_project(tmp_path, cli_sleep=0, produce_output=True)
    status, output = _run_until_interrupt(tmp_path, cli, settle=4.0)

    assert "Sleeping" in output, f"the worker never reached its sleep:\n{output}"
    assert "Traceback" not in output, output
    assert status == 0, f"expected a clean exit, got {status}:\n{output}"
    assert "Interrupted (Ctrl-C)" in output


@pytest.mark.skipif(os.name != "posix", reason="pty/process groups are POSIX")
def test_ctrl_c_mid_beat_leaves_no_orphan_child(tmp_path):
    """A stop that leaves the agent CLI running is not a stop. `subprocess.run`
    kills its child on any exception including KeyboardInterrupt; this pins
    that, because a future rewrite to Popen without the same care would leak a
    30-minute process every Ctrl-C."""
    cli = _build_project(tmp_path, cli_sleep=90, produce_output=False)
    marker = str(tmp_path / "fake-cli")
    _run_until_interrupt(tmp_path, cli, settle=3.0)

    time.sleep(0.5)
    survivors = subprocess.run(
        ["pgrep", "-f", marker], capture_output=True, text=True,
    )
    assert survivors.returncode != 0, (
        f"the beat's agent CLI survived Ctrl-C as an orphan: pids "
        f"{survivors.stdout.split()}"
    )
