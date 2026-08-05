"""Scheduling templates: both exist, both substitute-and-parse cleanly, both
carry the platform-specific keys an operator needs, and neither leaks a
personal path or name. Companion doc (docs/beats.md) exists and covers the
interval env var and the mandatory permissions section.

Almost all of it is pure file/text checks — no DB, no network. The one
exception is at the bottom: `docs/RELEASE-REHEARSAL.md` listed "the launchd
and systemd templates were not loaded, so the token substitution has never
been executed" as unverified, and no amount of shape-checking closes that.
So on macOS the rendered plist is `launchctl bootstrap`ed under a throwaway
label with a harmless program, confirmed loaded, and booted out again.
"""

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEDULING = REPO_ROOT / "scheduling"
PLIST = SCHEDULING / "com.sill.beat-worker.plist.template"
SYSTEMD_UNIT = SCHEDULING / "sill-beat-worker.service.template"
BEATS_DOC = REPO_ROOT / "docs" / "beats.md"

TOKENS = {
    "{{SILL_PYTHON}}": "/opt/example/sill-venv/bin/python",
    "{{SILL_DIR}}": "/opt/example/sill",
    "{{SILL_LOG_DIR}}": "/opt/example/sill/logs",
    "{{SILL_BEAT_CLI}}": "/opt/example/.local/bin/claude",
}


def substitute(text: str) -> str:
    for token, value in TOKENS.items():
        text = text.replace(token, value)
    return text


def test_both_templates_exist():
    assert PLIST.is_file(), PLIST
    assert SYSTEMD_UNIT.is_file(), SYSTEMD_UNIT


def test_plist_parses_as_xml_after_token_substitution():
    rendered = substitute(PLIST.read_text())
    data = plistlib.loads(rendered.encode("utf-8"))
    assert isinstance(data, dict)


def test_plist_carries_runatload_keepalive_throttle_path_and_log_paths():
    rendered = substitute(PLIST.read_text())
    data = plistlib.loads(rendered.encode("utf-8"))

    assert data.get("RunAtLoad") is True
    assert data.get("KeepAlive") is True
    assert "ThrottleInterval" in data, "no throttle — a crash-loop would spin unbounded"

    env = data.get("EnvironmentVariables", {})
    assert "PATH" in env, "no explicit PATH — launchd's own default PATH is not guaranteed"
    assert env["PATH"], "PATH must not be empty"

    assert "StandardOutPath" in data
    assert "StandardErrorPath" in data
    assert TOKENS["{{SILL_LOG_DIR}}"] in data["StandardOutPath"]
    assert TOKENS["{{SILL_LOG_DIR}}"] in data["StandardErrorPath"]


def test_plist_program_arguments_invoke_the_beat_mode():
    rendered = substitute(PLIST.read_text())
    data = plistlib.loads(rendered.encode("utf-8"))
    args = data.get("ProgramArguments", [])
    assert TOKENS["{{SILL_PYTHON}}"] in args
    assert "--mode" in args and "beat" in args


def test_plist_pins_sill_beat_cli_to_an_absolute_path():
    """The default SILL_BEAT_CLI ('claude', resolved against PATH) isn't
    found under this plist's own fixed, minimal PATH — a normal install
    puts it in ~/.local/bin, which is not in
    /usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin. Pinning the resolved
    absolute path as its own env var sidesteps needing PATH to find it at
    scheduled-beat time at all."""
    rendered = substitute(PLIST.read_text())
    data = plistlib.loads(rendered.encode("utf-8"))
    env = data.get("EnvironmentVariables", {})
    assert env.get("SILL_BEAT_CLI") == TOKENS["{{SILL_BEAT_CLI}}"]


def test_systemd_unit_carries_restart_and_restartsec():
    text = SYSTEMD_UNIT.read_text()
    assert any(line.strip().startswith("Restart=") for line in text.splitlines()), \
        "no Restart= — a crashed unit would just stay dead"
    assert any(line.strip().startswith("RestartSec=") for line in text.splitlines()), \
        "no RestartSec= — restarts with no backoff at all"


def test_systemd_unit_carries_an_explicit_path_environment_line():
    text = SYSTEMD_UNIT.read_text()
    assert any(line.strip().startswith("Environment=PATH=") for line in text.splitlines()), \
        "no Environment=PATH= — systemd units get a minimal default PATH, not the login shell's"


def test_systemd_unit_pins_sill_beat_cli_to_an_absolute_path():
    """Same reasoning as the plist counterpart above: bare 'claude' is not
    on this unit's own fixed PATH (/usr/local/bin:/usr/bin:/bin), so the
    resolved absolute path has to travel as its own Environment= line."""
    rendered = substitute(SYSTEMD_UNIT.read_text())
    lines = [l.strip() for l in rendered.splitlines()
             if l.strip().startswith("Environment=SILL_BEAT_CLI=")]
    assert len(lines) == 1
    assert lines[0] == f"Environment=SILL_BEAT_CLI={TOKENS['{{SILL_BEAT_CLI}}']}"


def test_systemd_unit_names_loginctl_enable_linger():
    text = SYSTEMD_UNIT.read_text()
    assert "loginctl enable-linger" in text, (
        "a --user unit dies at logout without this — launchd gives the "
        "equivalent behavior for free, so this is the one asymmetry an "
        "operator following only the plist's mental model would miss"
    )


def test_systemd_unit_parses_with_the_tokens_substituted():
    """Not full INI validation (systemd's unit grammar isn't strict INI —
    e.g. repeated keys are meaningful), just: substitution didn't leave any
    template token behind, and the ExecStart line is well-formed."""
    rendered = substitute(SYSTEMD_UNIT.read_text())
    assert "{{" not in rendered and "}}" not in rendered
    exec_lines = [l for l in rendered.splitlines() if l.strip().startswith("ExecStart=")]
    assert len(exec_lines) == 1
    assert TOKENS["{{SILL_PYTHON}}"] in exec_lines[0]
    assert "--mode beat" in exec_lines[0]


HOUSE_MARKERS = ("/Users/", "William", "Taysom", "wtaysom")


def test_neither_template_leaks_a_personal_path_or_name():
    for path in (PLIST, SYSTEMD_UNIT):
        text = path.read_text()
        for marker in HOUSE_MARKERS:
            assert marker not in text, f"{path.name} contains {marker!r}"


def test_beats_doc_exists_and_covers_interval_and_permissions():
    assert BEATS_DOC.is_file(), BEATS_DOC
    text = BEATS_DOC.read_text()
    assert "SILL_BEAT_INTERVAL_SECONDS" in text
    assert "permission" in text.lower(), "docs/beats.md must have a permissions section"


def test_beats_doc_permissions_section_precedes_the_scheduling_section():
    """The whole reason this section exists: installing a schedule before
    fixing permissions gets an operator a system that ticks on time and
    thinks nothing. Enforce the ordering, not just presence."""
    text = BEATS_DOC.read_text().lower()
    permissions_idx = text.find("permission")
    scheduling_idx = text.find("scheduling")
    assert permissions_idx != -1
    assert scheduling_idx != -1
    assert permissions_idx < scheduling_idx, (
        "the permissions section must come before the scheduling section"
    )


# --- the plist actually loads --------------------------------------------------


needs_launchctl = pytest.mark.skipif(
    os.uname().sysname != "Darwin" or shutil.which("launchctl") is None,
    reason="launchd is macOS-only; on other hosts the plist is untestable "
           "beyond its shape (and the systemd unit is untestable here for the "
           "mirror-image reason)",
)


@needs_launchctl
def test_the_rendered_plist_loads_and_unloads(tmp_path):
    """`scheduling/README.md`'s own substitute-and-load steps, run.

    Deliberately harmless: the Label is a throwaway, the program is
    `/bin/echo` rather than the beat worker, and the plist lives in a temp
    directory rather than `~/Library/LaunchAgents`, so a failure between
    bootstrap and bootout cannot survive a reboot. RunAtLoad still fires,
    which is what proves ProgramArguments and the log-path token were
    substituted into something launchd can really run.
    """
    label = f"com.sill.test-{os.getpid()}"
    logs = tmp_path / "logs"
    workdir = tmp_path / "dir"
    logs.mkdir()
    workdir.mkdir()

    rendered = (PLIST.read_text()
                .replace("{{SILL_PYTHON}}", "/bin/echo")
                .replace("{{SILL_DIR}}", str(workdir))
                .replace("{{SILL_LOG_DIR}}", str(logs))
                .replace("{{SILL_BEAT_CLI}}", "/bin/echo")
                .replace("com.sill.beat-worker", label))
    assert "{{" not in rendered, "a token survived substitution"
    plist_path = tmp_path / f"{label}.plist"
    plist_path.write_text(rendered)

    domain = f"gui/{os.getuid()}"
    booted = subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)],
                            capture_output=True, text=True, timeout=60)
    try:
        assert booted.returncode == 0, (
            "launchd refused the rendered plist — this is the check that shape "
            f"tests cannot make: {booted.stderr.strip() or booted.stdout.strip()}"
        )
        printed = subprocess.run(["launchctl", "print", f"{domain}/{label}"],
                                 capture_output=True, text=True, timeout=60)
        assert printed.returncode == 0, printed.stderr
        assert "/bin/echo" in printed.stdout
        # RunAtLoad means the program has already run by now; its arguments
        # landing in StandardOutPath prove both the ProgramArguments array and
        # the {{SILL_LOG_DIR}} token survived into something launchd honours.
        stdout_log = logs / "beat-worker-launchd-stdout.log"
        for _ in range(50):
            if stdout_log.exists() and stdout_log.read_text().strip():
                break
            subprocess.run(["sleep", "0.1"], timeout=5)
        assert stdout_log.exists(), (
            f"RunAtLoad produced no {stdout_log.name} — the log-path token did "
            "not resolve to somewhere launchd could write"
        )
        assert "--mode beat" in stdout_log.read_text()
    finally:
        subprocess.run(["launchctl", "bootout", domain, str(plist_path)],
                       capture_output=True, text=True, timeout=60)

    gone = subprocess.run(["launchctl", "print", f"{domain}/{label}"],
                          capture_output=True, text=True, timeout=60)
    assert gone.returncode != 0, (
        f"{label} is still loaded after bootout — a test must not leave a "
        "launch agent behind"
    )
