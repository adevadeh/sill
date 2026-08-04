#!/usr/bin/env python3
"""
Beat worker: a config-driven, alternating reflective loop.

Every SILL_BEAT_INTERVAL_SECONDS (default 7200 = 2h), spawns one headless
agent-CLI session running the next voice in a configured rotation (see
SILL_BEAT_CONFIG, default beats.json under the project root; example at
beats.example.json). Each voice gets its own standing prompt, its own
transcript directory, and — optionally — an output_glob checked after every
beat so an exit-0 subprocess that produced nothing does not get counted as
a success.

Detached by design: the subprocess has no interactive user, so anything
that would prompt for permission or wait for direction has nowhere to
route. A voice must decide and act on its own.

Rotation state persists outside /tmp (see default_state_path()) so a
worker restart, or an unrelated reboot that clears /tmp, cannot silently
reset which voice is due next with no record that it happened.

Ported from agi-memory's gnomon_worker.py (GNOMON_* env renamed to
SILL_BEAT_*, its bounded-trial correspondence channel excised entirely,
per-voice output verification added).
"""

import json
import logging
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration (env-driven; see docs/beats.md for the full surface)
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(os.environ.get("SILL_PROJECT_ROOT", ".")).resolve()
INTERVAL_SECONDS = int(os.environ.get("SILL_BEAT_INTERVAL_SECONDS", "7200"))  # 2h
TIMEOUT_SECONDS = int(os.environ.get("SILL_BEAT_TIMEOUT_SECONDS", "1800"))  # 30min
CLI = os.environ.get("SILL_BEAT_CLI", "claude")
WATCHDOG_SOCKET = os.environ.get("SILL_BEAT_WATCHDOG_SOCKET") or None
POST_HOOK = os.environ.get("SILL_BEAT_POST_HOOK") or None
LOG_PATH = Path(
    os.environ.get("SILL_BEAT_LOG_PATH")
    or (Path(os.environ.get("SILL_LOG_DIR", "/tmp")) / "beat-worker.log")
)

logger = logging.getLogger("beat_worker")


def _config_path() -> Path:
    override = os.environ.get("SILL_BEAT_CONFIG")
    return Path(override) if override else PROJECT_ROOT / "beats.json"


def _configure_logging() -> None:
    """Attach the beat-worker log file. Deferred out of module import (unlike
    the upstream worker, which did this at import time) so importing this
    module for tests never touches the filesystem as a side effect.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    if logger.handlers:
        return
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(LOG_PATH)
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.addHandler(file_handler)
    except OSError as exc:
        logger.warning(f"Could not attach beat-worker log file {LOG_PATH}: {exc}")


# ---------------------------------------------------------------------------
# Voice configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Voice:
    """One rotation slot, read verbatim from one entry of the `voices` array
    in the JSON config. Paths (prompt/transcripts/output_glob) stay relative
    strings — they're resolved against PROJECT_ROOT at spawn/check time, not
    here.
    """
    name: str
    prompt: str
    transcripts: str
    output_glob: str | None
    kickoff: str


def load_voices(path: Path) -> list[Voice]:
    """Read voice definitions from a beats.json config. Each entry in the
    top-level `voices` array becomes one Voice, in rotation order.
    """
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc

    raw_voices = data.get("voices", []) if isinstance(data, dict) else []
    if not raw_voices:
        raise ValueError(f"no voices defined in {path}")

    voices = []
    for i, entry in enumerate(raw_voices):
        if not isinstance(entry, dict):
            raise ValueError(f"voice entry {i} in {path} is not an object")
        try:
            voices.append(Voice(
                name=entry["name"],
                prompt=entry["prompt"],
                transcripts=entry["transcripts"],
                output_glob=entry.get("output_glob"),
                kickoff=entry.get("kickoff", "Begin."),
            ))
        except KeyError as exc:
            raise ValueError(f"voice entry {i} in {path} is missing required key {exc}") from exc
    return voices


# ---------------------------------------------------------------------------
# Rotation state (never /tmp — see default_state_path())
# ---------------------------------------------------------------------------

def default_state_path() -> Path:
    """Resolve where rotation state lives: SILL_BEAT_STATE_PATH if set, else
    the XDG state dir. Deliberately never /tmp: the upstream lesson this
    guards against is a reboot silently clearing /tmp and resetting rotation
    to voice 0 with nothing in the log to say so.
    """
    override = os.environ.get("SILL_BEAT_STATE_PATH")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "sill" / "beat-rotation.json"


def read_state(path: Path) -> int:
    """Read the rotation index, tolerating a missing or corrupt file. A beat
    worker must never crash its loop over a bad state file — worst case, it
    restarts rotation at voice 0.
    """
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    try:
        return int(data.get("next_index", 0))
    except (TypeError, ValueError):
        return 0


def write_state(path: Path, index: int) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"next_index": int(index)}))


def advance_if(path: Path, *, index: int, n_voices: int, success: bool) -> None:
    """Advance rotation past `index` only on verified success. On failure,
    the state file is left untouched, so the next scheduled beat retries
    the same voice rather than moving on.
    """
    if not success:
        return
    write_state(path, (index + 1) % n_voices)


# ---------------------------------------------------------------------------
# Transient-failure classification (diagnostic only — never changes whether
# rotation advances, only what gets logged and whether a transcript is kept)
# ---------------------------------------------------------------------------

_SKIP_PATTERNS = (
    "api error: unable to connect to api",
    "econnreset",
    "socket connection was closed unexpectedly",
    "failed to fetch",
    "connection error",
    "network error",
    "not logged in",
    "please run /login",
)


def classify_skip(output: str, duration: float) -> str | None:
    """Classify subprocess output as a transient infrastructure failure that
    should not consume the voice's rotation turn, as opposed to a real
    failure worth investigating. The caller is expected to call this only
    for a nonzero exit; a clean exit is never a "skip".
    """
    lowered = (output or "").lower()
    if any(pattern in lowered for pattern in _SKIP_PATTERNS):
        if "not logged in" in lowered or "please run /login" in lowered:
            return "the agent CLI is not logged in in this launch environment"
        if "econnreset" in lowered:
            return "the agent CLI's connection was reset"
        if "socket connection was closed unexpectedly" in lowered and duration > 600:
            return "the connection dropped for a long stretch (sleep or network loss)"
        if "socket connection was closed unexpectedly" in lowered:
            return "the agent CLI's socket closed unexpectedly"
        return "the agent CLI could not connect"

    # A timeout before any useful output is an infrastructure skip, not a beat.
    if duration >= TIMEOUT_SECONDS and not output.strip():
        return "the agent CLI subprocess timed out before producing output"

    return None


# ---------------------------------------------------------------------------
# Output verification — the guard for the silent-failure mode: a headless
# agent CLI with no permission allow-list gets its tools auto-denied rather
# than prompted, exits 0, and has done nothing. Exit code alone cannot see
# this; only checking for the voice's declared output can.
# ---------------------------------------------------------------------------

def snapshot_outputs(root: Path, glob: str) -> set[Path]:
    """Snapshot the files currently matching a voice's output_glob, so a
    later call can detect whether a beat added anything.
    """
    return set(Path(root).glob(glob))


def produced_output(root: Path, glob: str | None, before: set[Path]) -> bool:
    """True if `glob` gained a new file under `root` since `before`. A voice
    with no output_glob cannot be checked this way, so the answer is True by
    convention — absence of a way to verify is not evidence of failure.
    """
    if not glob:
        return True
    after = snapshot_outputs(root, glob)
    return bool(after - before)


# ---------------------------------------------------------------------------
# Best-effort child model detection (for the transcript header only; never
# load-bearing for control flow)
# ---------------------------------------------------------------------------

_MODEL_RE = re.compile(r'"model"\s*:\s*"([^"]+)"')


def _encode_project_path(root: Path) -> str:
    """Match the harness's session-directory naming: every non-alphanumeric
    character in the absolute project path becomes '-'.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(root).resolve()))


def _detect_child_model(project_root: Path, since_epoch: float) -> str | None:
    """Best-effort read of the child session's model id from its session
    transcript — the only place the substrate is recorded. Matches the
    newest session file written during this beat's window; skip entirely
    (return None -> "unknown" at the call site) if the sessions directory
    doesn't exist. Can misattribute if another interactive session is
    writing under the same project concurrently, hence "best-effort".
    """
    sessions_dir = Path.home() / ".claude" / "projects" / _encode_project_path(project_root)
    if not sessions_dir.exists():
        return None
    try:
        candidates = [f for f in sessions_dir.glob("*.jsonl")
                      if f.stat().st_mtime >= since_epoch - 5]
        if not candidates:
            return None
        newest = max(candidates, key=lambda f: f.stat().st_mtime)
        model = None
        with newest.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = _MODEL_RE.search(line)
                if m and not m.group(1).startswith("<"):
                    model = m.group(1)
        return model
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Optional log-only helpers (never block or fail a beat)
# ---------------------------------------------------------------------------

def _wedge_probe(socket_path: str) -> None:
    """Ping a local daemon socket before spawning a beat (e.g. a container
    engine the harness depends on). Log-only: never repairs anything, never
    blocks a beat — a WARNING is the whole effect, for a human to notice.
    """
    try:
        probe = subprocess.run(
            ["curl", "--max-time", "5", "--unix-socket", socket_path, "http://localhost/_ping"],
            capture_output=True, text=True, timeout=10,
        )
        if probe.stdout.strip() != "OK":
            logger.warning(
                f"[watchdog] socket ping failed before beat (stdout={probe.stdout!r}, "
                f"rc={probe.returncode}) — the dependency behind {socket_path} may be down"
            )
    except Exception as exc:
        logger.warning(f"[watchdog] probe error before beat: {exc}")


def _run_post_hook(command: str) -> None:
    """Run an optional command after a successful beat (e.g. a housekeeping
    script). Keyed on exit code only, never on output conventions, and never
    fails the beat that triggered it.
    """
    try:
        result = subprocess.run(
            shlex.split(command), capture_output=True, text=True,
            timeout=60, cwd=str(PROJECT_ROOT),
        )
        if result.returncode == 0:
            logger.info(f"[post-hook] {command!r} exited 0")
        else:
            detail = (result.stderr or result.stdout).strip()[:500]
            logger.warning(f"[post-hook] {command!r} exited {result.returncode}: {detail}")
    except Exception as exc:
        logger.warning(f"[post-hook] {command!r} failed to run: {exc}")


def _write_transcript(
    transcript_path: Path,
    label: str,
    result: subprocess.CompletedProcess,
    duration: float,
    spawned_at: str,
    model: str | None,
) -> None:
    """Write the final stdout/stderr record for a detached beat subprocess.
    SCOPE: this file is the subprocess's final message ONLY, not its full
    context window — a term's absence here is not evidence the process
    never saw it; the full tool-call record is the session transcript under
    ~/.claude/projects/<encoded-project-root>/ (match by mtime).
    """
    transcript_path.write_text(
        f"# {label}\n"
        f"# Spawned: {spawned_at} — worker-written wall-clock receipt at child "
        f"spawn; the child cannot fabricate this line\n"
        f"# Timestamp: {datetime.now().isoformat()}\n"
        f"# Duration: {duration:.1f}s\n"
        f"# Exit code: {result.returncode}\n"
        f"# Model: {model or 'unknown'} (best-effort mtime match on session transcript)\n"
        + "=" * 60
        + "\n\n"
        + (result.stdout or "")
        + (f"\n\n# STDERR:\n{result.stderr}" if result.stderr else "")
    )


# ---------------------------------------------------------------------------
# Spawning a beat
# ---------------------------------------------------------------------------

def spawn_beat(voice: Voice) -> tuple[bool, float, str]:
    """Spawn one headless agent-CLI session running `voice`. Returns
    (success, duration_seconds, transcript_path). Never raises: every
    failure mode is caught, logged, and reported through the return value
    so the caller can decide whether rotation advances.

    `success` means verified success: exit 0 AND (no declared output_glob,
    or the glob gained a new file). Exit 0 alone is not enough — see
    produced_output() above.
    """
    prompt_path = PROJECT_ROOT / voice.prompt
    transcripts_dir = PROJECT_ROOT / voice.transcripts

    # Fix: read the standing prompt defensively. Reading it outside a try
    # block (as upstream did) means a missing OR malformed prompt file
    # raises through the whole loop and kills the process — under a
    # keep-alive supervisor that becomes a restart storm, not a single
    # skipped beat. Path.read_text() raises OSError for a missing/unreadable
    # file, but UnicodeDecodeError — a ValueError, NOT an OSError subclass —
    # for a file that exists but holds bytes invalid for the codec, so the
    # `except OSError` guard alone let that second case straight through.
    # Widened to catch both rather than reading bytes and decoding with
    # errors="replace": this file's whole design is "on defect, skip and
    # retry" (see read_state()'s docstring above), not "proceed with
    # degraded input" — a garbled prompt handed to a live agent CLI could
    # produce a nonsense beat that still exits 0 and gets recorded as a
    # success.
    try:
        standing_prompt = prompt_path.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        logger.error(
            f"[{voice.name}] Could not read standing prompt {prompt_path}: {exc}. "
            "Skipping this beat without advancing rotation."
        )
        return False, 0.0, ""

    before = snapshot_outputs(PROJECT_ROOT, voice.output_glob) if voice.output_glob else set()
    prompt = f"{standing_prompt}\n\n---\n\n{voice.kickoff}\n"

    # Fix: same class of risk as the prompt read above — this call sat
    # outside any try/except (a gap upstream's spawn_gnomon_beat also has,
    # so guarding it here is a deliberate improvement over the port source,
    # not a port defect). An unwritable transcripts path, or one occupied by
    # a same-named file, raises straight out of the loop the same way the
    # previously-ungated prompt read used to.
    try:
        transcripts_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error(
            f"[{voice.name}] Could not create transcripts dir {transcripts_dir}: {exc}. "
            "Skipping this beat without advancing rotation."
        )
        return False, 0.0, ""

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    transcript_path = transcripts_dir / f"{timestamp}.txt"

    if WATCHDOG_SOCKET:
        _wedge_probe(WATCHDOG_SOCKET)

    spawn_clock = datetime.now().astimezone().isoformat(timespec="seconds")
    start_time = time.time()
    try:
        result = subprocess.run(
            [CLI, "--print", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            cwd=str(PROJECT_ROOT),
            # SILL_DETACHED_BEAT=1 is the authoritative headless flag sill's
            # own hooks gate on, so a spawned child does not try to inject
            # recall or advance any interactive-session clock.
            env={**os.environ, "SILL_DETACHED_BEAT": "1"},
        )
    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        logger.warning(
            f"[{voice.name}] Beat attempt skipped — subprocess timed out after "
            f"{TIMEOUT_SECONDS}s. Rotation remains on [{voice.name}]; the next "
            "scheduled beat retries the same voice."
        )
        return False, duration, ""
    except Exception as exc:
        duration = time.time() - start_time
        logger.error(f"[{voice.name}] Beat attempt error after {duration:.1f}s: {exc}.")
        return False, duration, ""

    duration = time.time() - start_time
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    model = _detect_child_model(PROJECT_ROOT, start_time)

    if result.returncode != 0:
        skip_reason = classify_skip(output, duration)
        if skip_reason:
            logger.warning(
                f"[{voice.name}] Beat attempt skipped after {duration:.1f}s — "
                f"{skip_reason}; exit {result.returncode}. Rotation remains on "
                f"[{voice.name}]; no transcript written."
            )
            return False, duration, ""
        _write_transcript(transcript_path, f"{voice.name.title()} beat", result, duration, spawn_clock, model)
        logger.error(
            f"[{voice.name}] Beat subprocess exited {result.returncode} after "
            f"{duration:.1f}s — transcript {transcript_path.name}."
        )
        return False, duration, str(transcript_path)

    _write_transcript(transcript_path, f"{voice.name.title()} beat", result, duration, spawn_clock, model)

    # Fix: verify every voice produced output before calling this a success.
    # Exit 0 is necessary but not sufficient — a headless agent CLI with no
    # tool-permission configuration gets its tools auto-denied rather than
    # prompted, and exits 0 having done nothing.
    if not produced_output(PROJECT_ROOT, voice.output_glob, before):
        logger.warning(
            f"[{voice.name}] Beat exited 0 in {duration:.1f}s but produced no file "
            f"matching {voice.output_glob!r} — the agent CLI may be denying tools "
            "non-interactively; see docs/beats.md permissions. Rotation remains "
            f"on [{voice.name}]."
        )
        return False, duration, str(transcript_path)

    logger.info(
        f"[{voice.name}] Beat complete in {duration:.1f}s — transcript {transcript_path.name}"
    )
    return True, duration, str(transcript_path)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_beat_loop() -> None:
    """Fire one beat every INTERVAL_SECONDS, cycling through the configured
    voices in order. Runs forever; the process is expected to live under a
    supervisor that restarts it on crash.
    """
    _configure_logging()
    voices = load_voices(_config_path())
    state_path = default_state_path()

    logger.info("Starting beat worker")
    logger.info(f"  Project root: {PROJECT_ROOT}")
    logger.info(f"  Config: {_config_path()}")
    logger.info(f"  Interval: {INTERVAL_SECONDS}s ({INTERVAL_SECONDS / 3600:.1f}h)")
    logger.info(f"  Beat timeout: {TIMEOUT_SECONDS}s")
    logger.info(f"  Rotation state: {state_path}")
    for v in voices:
        logger.info(f"  Voice [{v.name}]: prompt={v.prompt}, transcripts={v.transcripts}")

    while True:
        beat_start = time.time()
        index = read_state(state_path) % len(voices)
        voice = voices[index]

        success, _duration, _transcript = spawn_beat(voice)
        advance_if(state_path, index=index, n_voices=len(voices), success=success)
        if success and POST_HOOK:
            _run_post_hook(POST_HOOK)

        beat_total = time.time() - beat_start
        sleep_time = max(0, INTERVAL_SECONDS - beat_total)
        if sleep_time > 0:
            next_index = read_state(state_path) % len(voices)
            logger.info(f"Sleeping {sleep_time:.0f}s until next beat [{voices[next_index].name}]...")
            time.sleep(sleep_time)


if __name__ == "__main__":
    run_beat_loop()
