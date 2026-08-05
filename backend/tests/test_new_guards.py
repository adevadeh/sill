import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[2] / "plugin" / "hooks"


def run(hook, payload, env_extra=None):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run([sys.executable, str(HOOKS / hook)],
                          input=json.dumps(payload), capture_output=True,
                          text=True, env=env, timeout=30)


def test_shell_guard_denies_bare_equals_after_echo():
    r = run("shell-idiom-guard.py",
            {"tool_name": "Bash", "tool_input": {"command": "echo === && ls"}})
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_shell_guard_passes_quoted_and_inner_equals():
    for cmd in ["echo '==='", 'echo "a=b"', "grep foo=bar file", "echo ok"]:
        r = run("shell-idiom-guard.py",
                {"tool_name": "Bash", "tool_input": {"command": cmd}})
        assert r.returncode == 0 and r.stdout.strip() == "", cmd


def test_shell_guard_catches_the_trap_behind_a_leading_env_assignment():
    """A per-command environment-assignment prefix (bash/zsh: 'VAR=val cmd
    args', the same syntax `env` documents) puts 'echo' in command
    position exactly as much as the start of the line does — the TRAP
    regex's boundary alternation (^, or one of ;&|`( ) didn't account for
    it, so 'x=1 echo =y' reached command position completely unseen by the
    one hook in this suite whose whole job is to block this trap rather
    than just warn about it."""
    for cmd in ["x=1 echo =y", "FOO=bar BAZ=qux echo =trap", "x=1 echo === && ls"]:
        r = run("shell-idiom-guard.py",
                {"tool_name": "Bash", "tool_input": {"command": cmd}})
        assert r.returncode == 0, cmd
        assert r.stdout.strip(), f"expected a deny decision for {cmd!r}, got no output"
        out = json.loads(r.stdout)
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny", cmd


def test_shell_guard_still_passes_legitimate_assignments_before_other_commands():
    """The fix must not turn every leading assignment into a false
    positive — only an assignment immediately followed by the actual
    echo-=word trap shape."""
    for cmd in ["x=1 ls", "FOO=bar grep x=y file", "x=1 echo ok", "x=1 echo 'safe=quoted'"]:
        r = run("shell-idiom-guard.py",
                {"tool_name": "Bash", "tool_input": {"command": cmd}})
        assert r.returncode == 0 and r.stdout.strip() == "", cmd


def test_clear_handoff_ignores_non_clear_sources(tmp_path):
    r = run("clear-handoff.py", {"source": "startup", "session_id": "s1"},
            {"SILL_LOG_DIR": str(tmp_path)})
    assert r.returncode == 0 and r.stdout.strip() == ""
