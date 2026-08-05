"""Harness normalization. Pure functions over payload dicts; no I/O."""

import importlib.util
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[2] / "plugin" / "hooks"
spec = importlib.util.spec_from_file_location("_harness", HOOKS / "_harness.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


CLAUDE_BASH = {"tool_name": "Bash", "tool_input": {"command": "echo hi"}}
CODEX_EXEC = {"tool_name": "exec", "tool_input": {"command": "echo hi"}}
CODEX_EXEC_CMD = {"tool_name": "exec_command", "tool_input": {"command": "echo hi"}}
CLAUDE_WRITE = {"tool_name": "Write",
                "tool_input": {"file_path": "a.md", "content": "x"}}
CODEX_PATCH = {"tool_name": "apply_patch",
               "tool_input": {"input": "*** Update File: a.md\n+x\n"}}


@pytest.mark.parametrize("payload", [CLAUDE_BASH, CODEX_EXEC, CODEX_EXEC_CMD])
def test_shell_kind_across_both_harnesses(payload):
    assert h.tool_kind(payload) == "shell"
    assert h.shell_command(payload) == "echo hi"


@pytest.mark.parametrize("payload", [CLAUDE_WRITE, CODEX_PATCH])
def test_write_kind_across_both_harnesses(payload):
    assert h.tool_kind(payload) in ("write", "edit")
    assert h.written_path(payload) == "a.md"


def test_unknown_tool_is_other_not_a_crash():
    assert h.tool_kind({"tool_name": "image_gen.imagegen", "tool_input": {}}) == "other"
    assert h.tool_kind({}) == "other"
    assert h.shell_command({"tool_name": "view_image", "tool_input": {}}) is None


def test_mcp_name_is_flat_on_the_hook_surface():
    p = {"tool_name": "mcp__sill__remember", "tool_input": {}}
    assert h.tool_kind(p) == "mcp"
    assert h.mcp_tool_name(p) == "mcp__sill__remember"


def test_mcp_name_is_joined_correctly_from_a_split_transcript_record():
    """Codex transcripts split namespace and name; joining without the
    separator yields 'mcp__sillrecall_batch', which only worked by accident
    under a substring filter."""
    rec = {"namespace": "mcp__sill", "name": "recall_batch"}
    assert h.join_mcp_name(rec) == "mcp__sill__recall_batch"
    assert h.join_mcp_name({"name": "exec"}) == "exec"


def test_assistant_text_prefers_the_direct_field():
    assert h.assistant_text({"last_assistant_message": "hello"}) == "hello"
    assert h.assistant_text({}) is None


def test_detect_identifies_each_harness():
    assert h.detect({"turn_id": "t1", "session_id": "s"}) == "codex"
    assert h.detect({"session_id": "s", "transcript_path": "/x.jsonl"}) in ("claude", "unknown")


def test_never_raises_on_garbage():
    for junk in [None, [], "string", {"tool_input": None}, {"tool_name": None}]:
        assert h.tool_kind(junk) == "other"
        assert h.shell_command(junk) is None
        assert h.written_path(junk) is None
