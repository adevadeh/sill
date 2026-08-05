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


def _fake_docker_env(tmp_path, reply="0"):
    """A fake `docker` placed first on PATH that answers like a REACHABLE
    store reporting REPLY matching rows. This file's own run() helper points
    SILL_DB_CONTAINER at a nonexistent container — the fail-OPEN path, right
    for most tests here, but that path can never produce a genuine 'deny'
    from stored-slot-guard. Same technique as test_receipt_guards.py's
    test_guard_denies_when_the_store_genuinely_lacks_the_id."""
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(f"#!/bin/sh\necho {reply}\n")
    fake_docker.chmod(0o755)
    return {"PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}"}


# --- Finding 2: parity coverage for the remaining four rewired hooks -------
#
# shell-idiom-guard and tool-type-witness already have parametrized
# Claude/Codex pairs above. stored-slot-guard, attribution-check,
# state-language-check, and track-reuse were verified by hand in the task-2
# report but had no automated tripwire — and state-language-check's
# apply_patch key already broke silently once (it read tool_input["command"],
# which apply_patch never sets, until that report's fix). These four close
# the gap so all six rewired hooks are pinned.

RECEIPT_TEXT = "Stored: deadbeef-1111-2222-3333-444455556666"


@pytest.mark.parametrize("payload", [
    {"tool_name": "Write", "tool_input": {"file_path": "journal/r-1.md",
                                          "content": RECEIPT_TEXT + "\n"}},
    {"tool_name": "apply_patch", "turn_id": "t1",
     "tool_input": {"input": f"*** Update File: journal/r-1.md\n+{RECEIPT_TEXT}\n"}},
])
def test_stored_slot_guard_denies_on_both_harnesses(payload, tmp_path):
    r = run("stored-slot-guard.py", payload, env_extra=_fake_docker_env(tmp_path))
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "deadbeef" in out["hookSpecificOutput"]["permissionDecisionReason"]


ATTRIBUTION_CONTENT = "You wrote this and I concluded it holds up."


@pytest.mark.parametrize("payload", [
    {"tool_name": "Bash", "tool_input": {"command": f'sill.py notice "{ATTRIBUTION_CONTENT}"'}},
    {"tool_name": "exec_command", "turn_id": "t1",
     "tool_input": {"command": f'sill.py notice "{ATTRIBUTION_CONTENT}"'}},
])
def test_attribution_check_flags_the_same_claims_on_both_harnesses(payload):
    r = run("attribution-check.py", payload)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    # Exact match, not just "non-empty": the real parity claim is identical
    # output modulo the tool name, not merely "both fired something".
    assert out["systemMessage"] == "[attribution-check] 2 attribution claim(s)"


STATE_CONTENT = "I am feeling tired and need a break after this long session."


@pytest.mark.parametrize("payload", [
    {"tool_name": "Write", "tool_input": {"file_path": "journal/r-1.md",
                                          "content": STATE_CONTENT + "\n"}},
    {"tool_name": "apply_patch", "turn_id": "t1",
     "tool_input": {"input": f"*** Update File: journal/r-1.md\n+{STATE_CONTENT}\n"}},
])
def test_state_language_check_flags_the_same_matches_on_both_harnesses(payload):
    r = run("state-language-check.py", payload)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["systemMessage"] == "[state-language-check] 2 match(es)"


# track-reuse fires on Stop, not PreToolUse — it never emits a stdout
# deny/advisory verdict (Stop hooks don't gate tool calls), so there is no
# permissionDecision/systemMessage to compare here. Its actual verdict is
# the reuse count it would persist via record_memory_reuse(); the one
# observable reachable without a live DB (this suite runs with no DB — see
# test_hook_safety.py's module docstring) is the "Found N reused memories"
# line, logged BEFORE the DB write is attempted. This proves the
# transcript-parsing step — which differs by harness: Claude's flat
# tool_use/tool_result content blocks vs Codex's split
# function_call/function_call_output records joined via
# _harness.join_mcp_name — reaches the same reuse COUNT on both
# harness-shaped transcripts. It does NOT prove the DB write itself
# succeeds or is identical (POSTGRES_HOST/PORT point at an unreachable
# loopback port here, same no-DB-network policy as test_hook_safety.py),
# and it does not distinguish which memory was flagged beyond the count.

REUSE_MEMORY_ID = "11111111-1111-1111-1111-111111111111"
REUSE_MEMORY_CONTENT = "alpha beta gamma delta epsilon zeta distinctive marker phrase trails onward"
REUSE_RESPONSE_TEXT = "As discussed, the distinctive marker phrase applies here directly."

CLAUDE_REUSE_TRANSCRIPT = "\n".join([
    json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "toolu_1", "name": "mcp__sill__recall_batch", "input": {}},
    ]}}),
    json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_1",
         "content": json.dumps({"memories": [
             {"id": REUSE_MEMORY_ID, "content": REUSE_MEMORY_CONTENT}]})},
    ]}}),
]) + "\n"

CODEX_REUSE_TRANSCRIPT = "\n".join([
    json.dumps({"type": "response_item", "payload": {
        "type": "function_call", "call_id": "call_1",
        "namespace": "mcp__sill", "name": "recall_batch", "arguments": "{}"}}),
    json.dumps({"type": "response_item", "payload": {
        "type": "function_call_output", "call_id": "call_1",
        "output": json.dumps({"memories": [
            {"id": REUSE_MEMORY_ID, "content": REUSE_MEMORY_CONTENT}]})}}),
]) + "\n"


@pytest.mark.parametrize("transcript_text", [CLAUDE_REUSE_TRANSCRIPT, CODEX_REUSE_TRANSCRIPT],
                         ids=["claude", "codex"])
def test_track_reuse_detects_the_same_reuse_on_both_harnesses(transcript_text, tmp_path):
    transcript_path = tmp_path / "transcript.jsonl"
    transcript_path.write_text(transcript_text)
    payload = {
        "hook_event_name": "Stop",
        "transcript_path": str(transcript_path),
        "last_assistant_message": REUSE_RESPONSE_TEXT,
        "session_id": "test-parity-session",
    }
    r = run("track-reuse.py", payload, env_extra={
        "SILL_LOG_DIR": str(tmp_path),
        "POSTGRES_HOST": "127.0.0.1", "POSTGRES_PORT": "1",
    })
    assert r.returncode == 0
    log_text = (tmp_path / "reuse-tracking.log").read_text()
    assert "Found 1 reused memories" in log_text, log_text


# --- Finding 1: multi-file apply_patch scope bypass ------------------------
#
# written_path used to return only the FIRST file header's path, and the
# three guards below gated their in-scope check on that single path BEFORE
# reading any text. A two-file patch whose first file is irrelevant and
# whose second file is an in-scope journal entry was therefore never
# inspected at all: written_path answered "in scope?" for the whole call
# using the irrelevant first path, the scope check failed, and the guard
# exited without ever reading the second file's text. Fixed by switching
# all three to iterate _harness.written_files and judge each (path, text)
# pair on its own path — see _harness.py's written_files docstring.

def _two_file_patch(second_path: str, second_text: str) -> dict:
    """An out-of-scope first file (src/unrelated.py) followed by an
    in-scope second file — the exact shape of the bypass."""
    return {
        "tool_name": "apply_patch", "turn_id": "t1",
        "tool_input": {"input": (
            "*** Update File: src/unrelated.py\n+irrelevant change\n"
            f"*** Update File: {second_path}\n+{second_text}\n"
        )},
    }


def test_stored_slot_guard_inspects_the_second_file_of_a_multi_file_patch(tmp_path):
    payload = _two_file_patch("journal/r-1.md", "Stored: deadbeef-1111-2222-3333-444455556666")
    r = run("stored-slot-guard.py", payload, env_extra=_fake_docker_env(tmp_path))
    assert r.returncode == 0
    assert r.stdout.strip() != "", (
        "guard produced no output for a patch whose SECOND file has a "
        "forged receipt — the multi-file scope bypass: written_path "
        "reported only the out-of-scope first file, so the second file's "
        "text was never read"
    )
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "deadbeef" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_tool_type_witness_inspects_the_second_file_of_a_multi_file_patch():
    payload = _two_file_patch("journal/r-1.md", "The receipt arrived by Edit.")
    r = run("tool-type-witness.py", payload)
    assert r.returncode == 0
    assert r.stdout.strip() != "", (
        "guard produced no output for a patch whose SECOND file carries an "
        "unquoted carrying-act claim — the multi-file scope bypass"
    )
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_state_language_check_inspects_the_second_file_of_a_multi_file_patch():
    payload = _two_file_patch(
        "journal/r-1.md", "I am feeling tired and need a break after this long session.")
    r = run("state-language-check.py", payload)
    assert r.returncode == 0
    assert r.stdout.strip() != "", (
        "guard produced no output for a patch whose SECOND file carries "
        "state language — the multi-file scope bypass"
    )
    out = json.loads(r.stdout)
    assert "state-language-check" in out["systemMessage"]
