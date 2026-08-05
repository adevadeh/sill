"""Every guard must reach the same verdict on both harnesses."""

import json, os, subprocess, sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[2] / "plugin" / "hooks"


def run(hook, payload, env_extra=None):
    env = {**os.environ, "SILL_BEAT_JOURNAL_DIRS": "journal/",
           "SILL_DB_CONTAINER": "sill_absent_container"}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(HOOKS / hook)],
                          input=json.dumps(payload), capture_output=True,
                          text=True, env=env, timeout=30)


TRAP = "echo === && ls"

@pytest.mark.parametrize("payload", [
    {"tool_name": "Bash", "tool_input": {"command": TRAP}},
    {"tool_name": "exec", "tool_input": {"command": TRAP}, "turn_id": "t1"},
    {"tool_name": "exec_command", "tool_input": {"command": TRAP}, "turn_id": "t1"},
])
def test_shell_guard_denies_the_trap_on_both_harnesses(payload):
    r = run("shell-idiom-guard.py", payload)
    assert r.returncode == 0
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("payload", [
    {"tool_name": "Write", "tool_input": {"file_path": "journal/r-1.md",
                                          "content": "The receipt arrived by Edit.\n"}},
    {"tool_name": "apply_patch", "turn_id": "t1",
     "tool_input": {"input": "*** Update File: journal/r-1.md\n+The receipt arrived by Edit.\n"}},
])
def test_witness_denies_on_both_harnesses(payload):
    r = run("tool-type-witness.py", payload)
    assert r.returncode == 0
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_no_guard_emits_a_permission_request_shaped_payload():
    """Codex fails CLOSED on PermissionRequest reserved fields — a Claude-shaped
    response there denies the call. Guards must emit PreToolUse decisions only."""
    for hook in ["shell-idiom-guard.py", "tool-type-witness.py", "stored-slot-guard.py"]:
        src = (HOOKS / hook).read_text()
        for reserved in ["updatedInput", "updatedPermissions", '"interrupt"']:
            assert reserved not in src, f"{hook} emits a PermissionRequest field"
