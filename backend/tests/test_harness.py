"""Harness normalization. Pure functions over payload dicts; no I/O."""

import importlib.util
import subprocess
import sys
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
CLAUDE_EDIT = {"tool_name": "Edit",
               "tool_input": {"file_path": "a.md", "old_string": "foo", "new_string": "bar"}}
CLAUDE_MULTI_EDIT = {"tool_name": "MultiEdit",
                      "tool_input": {"file_path": "a.md", "edits": [
                          {"old_string": "foo", "new_string": "bar"},
                          {"old_string": "baz", "new_string": "qux"},
                      ]}}
CODEX_MULTI_FILE_PATCH = {"tool_name": "apply_patch",
                           "tool_input": {"input": "*** Update File: a.md\n+x\n"
                                                    "*** Update File: b.md\n+y\n"}}


@pytest.mark.parametrize("payload", [CLAUDE_BASH, CODEX_EXEC, CODEX_EXEC_CMD])
def test_shell_kind_across_both_harnesses(payload):
    assert h.tool_kind(payload) == "shell"
    assert h.shell_command(payload) == "echo hi"


@pytest.mark.parametrize("payload", [CLAUDE_WRITE, CODEX_PATCH])
def test_write_kind_across_both_harnesses(payload):
    assert h.tool_kind(payload) in ("write", "edit")
    assert h.written_path(payload) == "a.md"


def test_written_text_reads_flat_edit_new_string():
    assert h.written_text(CLAUDE_EDIT) == "bar"


def test_written_text_reads_multi_edit_nested_new_strings():
    """MultiEdit nests its edits under an "edits" list instead of Edit's
    flat top-level new_string, but tool_kind classifies both as "edit" —
    so the natural `if tool_kind(p) == "edit": written_text(p)` caller
    must not silently see nothing for every MultiEdit call."""
    text = h.written_text(CLAUDE_MULTI_EDIT)
    assert text is not None
    assert "bar" in text
    assert "qux" in text


def test_written_text_multi_edit_skips_malformed_entries_without_raising():
    payload = {"tool_name": "MultiEdit",
               "tool_input": {"file_path": "a.md", "edits": [
                   "not a dict",
                   {"old_string": "foo"},  # missing new_string
                   {"old_string": "baz", "new_string": "qux"},
               ]}}
    assert h.written_text(payload) == "qux"


def test_multi_file_patch_text_is_scoped_to_the_path_it_reports():
    """A patch touching two files must not let file B's content answer
    for file A's path: written_path reports the first file only, and
    written_text must be scoped to that same file, not the whole patch
    body — otherwise a caller checking 'is this path in scope, and does
    its text contain X' can judge file A's scope against file B's
    content."""
    assert h.written_path(CODEX_MULTI_FILE_PATCH) == "a.md"
    assert h.written_text(CODEX_MULTI_FILE_PATCH) == "x"


# --- written_files: the plural, multi-file-safe view -----------------------
#
# written_path/written_text only ever answer for the FIRST file header in an
# apply_patch body. A caller that gates scope on written_path() alone before
# reading written_text() answers "in scope?" for the whole call using only
# that first file's path — so a patch whose first file is irrelevant and
# whose second file is in scope is never inspected at all: written_path
# reports the irrelevant path, the scope check fails, and the caller never
# even looks at the second file's text. This is the bypass
# test_guards_on_both_harnesses.py's multi-file tests close at the guard
# level; these are the harness-level tests for the API those guards now use.

def test_written_files_single_file_matches_written_path_and_text():
    """For any single-file call, written_files is exactly the one-element
    [(written_path, written_text)] pair — the plural form must not silently
    diverge from the singular one it replaces for scope-gating."""
    for payload in (CLAUDE_WRITE, CODEX_PATCH, CLAUDE_EDIT, CLAUDE_MULTI_EDIT):
        assert h.written_files(payload) == [(h.written_path(payload), h.written_text(payload))]


def test_written_files_scopes_each_files_text_to_itself_on_a_multi_file_patch():
    """The core property: every file gets its OWN pair, text scoped to only
    that file's '+' lines — b.md's pair must not include a.md's 'alpha' and
    vice versa. Bumped to two added lines on the second file to also prove
    a file's own multi-line text is joined correctly, not just truncated to
    one line."""
    payload = {"tool_name": "apply_patch",
               "tool_input": {"input": "*** Update File: a.md\n+alpha\n"
                                        "*** Update File: b.md\n+bravo\n+charlie\n"}}
    assert h.written_files(payload) == [("a.md", "alpha"), ("b.md", "bravo\ncharlie")]


def test_written_files_skips_a_header_with_no_path_but_keeps_later_files():
    """Mirrors _parse_patch_path's 'blank-tail header' case (Task 1), but
    per-file: a header whose path strips to empty contributes no pair for
    that section — same 'don't guess a path' contract — without that
    dropping the file that follows it."""
    payload = {"tool_name": "apply_patch",
               "tool_input": {"input": "*** Update File:    \n+dropped\n"
                                        "*** Update File: b.md\n+kept\n"}}
    assert h.written_files(payload) == [("b.md", "kept")]


def test_written_files_empty_when_nothing_parseable():
    assert h.written_files({"tool_name": "apply_patch",
                             "tool_input": {"input": "no header in this body\n"}}) == []
    assert h.written_files({"tool_name": "Write", "tool_input": {"content": "x"}}) == []  # no file_path
    assert h.written_files(CLAUDE_BASH) == []  # wrong kind entirely
    assert h.written_files({}) == []


def test_written_files_never_raises_on_garbage():
    for junk in [None, [], "string", {"tool_input": None}, {"tool_name": None}]:
        assert h.written_files(junk) == []


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


def test_iter_transcript_tool_uses_returns_empty_for_non_path_types():
    """open() treats an int as a raw OS file descriptor, not a filename
    — isinstance-reject before it ever reaches open(), for every
    non-path shape, not just int."""
    for junk in [1, True, False, None, [], {}, 3.14, ["a.jsonl"]]:
        assert list(h.iter_transcript_tool_uses(junk)) == []


def test_iter_transcript_tool_uses_rejects_int_path_without_closing_stdout():
    """True == 1 == stdout's fd. open(True, ...) used to open that live
    fd (open() treats an int argument as a raw fd, not a filename), and
    the `with` block closed it on the way out — silently killing the
    process's stdout with no exception ever reaching the broad
    `except Exception` that wraps the call, since closing a fd doesn't
    raise. Run in a subprocess so the regression's actual symptom
    (stdout dies, a later print() vanishes, the interpreter exits
    nonzero at shutdown trying to flush the dead fd) is caught
    behaviorally — an isinstance assertion alone wouldn't catch a
    reintroduced call path that still reaches open() with an int.
    """
    harness_path = HOOKS / "_harness.py"
    code = (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('_harness', {str(harness_path)!r})\n"
        "h = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(h)\n"
        "list(h.iter_transcript_tool_uses(True))\n"
        "print('SENTINEL_OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr!r}"
    assert "SENTINEL_OK" in proc.stdout
