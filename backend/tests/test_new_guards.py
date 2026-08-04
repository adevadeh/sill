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


def test_clear_handoff_ignores_non_clear_sources(tmp_path):
    r = run("clear-handoff.py", {"source": "startup", "session_id": "s1"},
            {"SILL_LOG_DIR": str(tmp_path)})
    assert r.returncode == 0 and r.stdout.strip() == ""
