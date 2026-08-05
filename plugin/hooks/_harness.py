"""Harness normalization: one vocabulary for Claude Code and Codex.

Every hook that used to string-match "Bash", "Write", "Edit", "Read" — the
Claude-only tool names — silently never fired on Codex, whose PreToolUse
payloads carry "exec"/"exec_command" (shell), "apply_patch" (write), and
"view_image" (read) instead. This module is the one place that knows both
vocabularies, so every other hook asks it instead of rediscovering the
mapping (or a subset of it) on its own.

Two input shapes, not one:

- Hook payload dicts (`{"tool_name": ..., "tool_input": {...}}`, plus
  Stop-event fields like `last_assistant_message`) — what a
  PreToolUse/PostToolUse/Stop hook receives on stdin. Already flattened
  by the harness itself: an MCP tool name arrives as one flat string
  (`mcp__server__tool`) on both Claude Code and Codex.
- Raw session transcript JSONL records, read from disk by
  `iter_transcript_tool_uses` — the one function here that does I/O.
  Codex transcripts split an MCP call's name into `namespace` + `name`
  instead of leaving it flat; `join_mcp_name` reconstructs the flat form
  WITH the separator that ad hoc call sites were missing (joining
  `mcp__sill` + `recall_batch` with no separator produces
  `mcp__sillrecall_batch`, not `mcp__sill__recall_batch` — it only
  looked like it worked because a downstream filter used a substring
  test).

Every function below that takes a payload/record is total: garbage in
(None, a list, a bare string, a dict with None values, an empty dict)
returns the documented "nothing here" value rather than raising. Hooks
run on every tool call in every session; an exception escaping this
module is a broken harness, not a broken test.
"""
import json
import os
from typing import Iterator, Literal

Harness = Literal["claude", "codex", "unknown"]
ToolKind = Literal["shell", "write", "edit", "read", "mcp", "other"]

# Tool names as they appear in a normalized hook payload's "tool_name"
# field: Claude Code's own names plus Codex's equivalents (see module
# docstring). "mcp__"-prefixed names are matched by prefix, not
# membership, in tool_kind below.
_SHELL_NAMES = frozenset({"Bash", "exec", "exec_command"})
_WRITE_NAMES = frozenset({"Write", "apply_patch"})
_EDIT_NAMES = frozenset({"Edit", "MultiEdit"})
_READ_NAMES = frozenset({"Read", "view_image"})

_MCP_PREFIX = "mcp__"

# Codex's apply_patch body carries the path in its own header line
# (there is no separate file_path field). written_path/written_text read
# the first "*** Update File: <path>" / "*** Add File: <path>" line; a
# patch with neither (a bare hunk, an empty string, a Delete-only patch)
# is unparseable and yields None rather than a guess. Matched by literal
# prefix line-by-line — same convention as state-language-check.py's
# extract_relevant_patch_additions — not a multiline regex: a regex
# using \s* around the capture would match across the newline (\s
# includes \n) and, on a header line with a blank/whitespace-only tail,
# silently bleed the "path" capture into the next line's content.
_PATCH_HEADERS = ("*** Update File: ", "*** Add File: ")


def _as_dict(value: object) -> dict | None:
    return value if isinstance(value, dict) else None


def _as_str(value: object) -> str | None:
    """A non-empty string, or None — for anything else, including "" """
    return value if isinstance(value, str) and value else None


def _tool_name(payload: object) -> str | None:
    d = _as_dict(payload)
    return _as_str(d.get("tool_name")) if d is not None else None


def _tool_input(payload: object) -> dict | None:
    d = _as_dict(payload)
    return _as_dict(d.get("tool_input")) if d is not None else None


def detect(payload: object) -> Harness:
    """Identify which harness produced this hook payload.

    Codex-positive only: every Codex event carries "turn_id" and no
    Claude Code payload does, so its presence is a reliable marker.
    Claude Code payloads have no equivalent unique marker to key on, so
    they — and anything else — fall through to "unknown" rather than a
    guessed "claude". Do not invent a Claude-side positive marker here;
    none has been found to exist.
    """
    try:
        d = _as_dict(payload)
        if d is not None and _as_str(d.get("turn_id")) is not None:
            return "codex"
        return "unknown"
    except Exception:
        return "unknown"


def tool_kind(payload: object) -> ToolKind:
    """Classify a hook payload's tool into a harness-independent kind."""
    try:
        name = _tool_name(payload)
        if name is None:
            return "other"
        if name.startswith(_MCP_PREFIX):
            return "mcp"
        if name in _SHELL_NAMES:
            return "shell"
        if name in _WRITE_NAMES:
            return "write"
        if name in _EDIT_NAMES:
            return "edit"
        if name in _READ_NAMES:
            return "read"
        return "other"
    except Exception:
        return "other"


def mcp_tool_name(payload: object) -> str | None:
    """The flat `mcp__server__tool` name, on either harness's hook
    payload (both already flatten it — see join_mcp_name for the
    transcript records that don't)."""
    try:
        name = _tool_name(payload)
        if name is not None and name.startswith(_MCP_PREFIX):
            return name
        return None
    except Exception:
        return None


def join_mcp_name(record: object) -> str | None:
    """Reconstruct a flat `mcp__server__tool` name from a transcript
    record that splits it into `namespace` + `name` (Codex's transcript
    shape — hook payloads are already flat; see mcp_tool_name for those).

    Joins with "__" only when a namespace is present; a plain,
    non-MCP call like {"name": "exec"} carries no namespace and passes
    through unchanged rather than picking up a spurious separator.
    """
    try:
        d = _as_dict(record)
        if d is None:
            return None
        name = _as_str(d.get("name"))
        if name is None:
            return None
        namespace = _as_str(d.get("namespace"))
        return f"{namespace}__{name}" if namespace else name
    except Exception:
        return None


def shell_command(payload: object) -> str | None:
    """The shell command text, from Bash (Claude) or exec/exec_command
    (Codex) — all three carry it under tool_input.command."""
    try:
        if tool_kind(payload) != "shell":
            return None
        ti = _tool_input(payload)
        return _as_str(ti.get("command")) if ti is not None else None
    except Exception:
        return None


def _parse_patch_path(patch: str) -> str | None:
    for line in patch.splitlines():
        for header in _PATCH_HEADERS:
            if line.startswith(header):
                path = line[len(header):].strip()
                return path or None
    return None


def written_path(payload: object) -> str | None:
    """The path a Write/Edit/MultiEdit (Claude) or apply_patch (Codex)
    call touches. apply_patch has no file_path field — the path lives in
    the patch body's own header line, so it is parsed out of the body
    rather than looked up; see _parse_patch_path for the unparseable
    case.
    """
    try:
        kind = tool_kind(payload)
        if kind not in ("write", "edit"):
            return None
        ti = _tool_input(payload)
        if ti is None:
            return None
        if _tool_name(payload) == "apply_patch":
            patch = _as_str(ti.get("input"))
            return _parse_patch_path(patch) if patch is not None else None
        return _as_str(ti.get("file_path"))
    except Exception:
        return None


def _patch_added_text(patch: str) -> str | None:
    """Added ("+") lines, scoped to the FIRST file header only — the
    same file written_path returns. A multi-file patch (*** Update
    File: a.md / ... / *** Update File: b.md / ...) would otherwise
    return every file's additions concatenated, so a caller pairing this
    with written_path could judge file A's scope against file B's
    content. Stops collecting at the second header line (if any); a
    patch with 0 or 1 headers is unaffected — both are already
    unambiguous ("no file" or "the one file"), same as before this
    scoping was added.
    """
    lines = []
    headers_seen = 0
    for line in patch.splitlines():
        if any(line.startswith(header) for header in _PATCH_HEADERS):
            headers_seen += 1
            if headers_seen >= 2:
                break  # second file's header — stop; stay scoped to the first
            continue
        if line.startswith("+"):
            lines.append(line[1:])
    return "\n".join(lines) if lines else None


def _multi_edit_text(ti: dict) -> str | None:
    """MultiEdit's new_string values, newline-joined. MultiEdit nests
    its edits under an "edits" list instead of Edit's flat top-level
    new_string: {"file_path": ..., "edits": [{"old_string": ...,
    "new_string": ...}, ...]}. tool_kind classifies both as "edit", but
    only Edit's payload has a top-level new_string — reading that key
    directly for MultiEdit silently returns None for every call.
    A non-list "edits" (missing, wrong type) returns None so the caller
    falls back to the flat Edit shape; a malformed entry (not a dict, or
    a missing/non-string new_string) is skipped rather than raising.
    """
    edits = ti.get("edits")
    if not isinstance(edits, list):
        return None
    parts = [text for text in (
        _as_str(edit.get("new_string")) for edit in edits if isinstance(edit, dict)
    ) if text is not None]
    return "\n".join(parts) if parts else None


def written_text(payload: object) -> str | None:
    """The text a Write/Edit/MultiEdit (Claude) or apply_patch (Codex)
    call introduces: Write's full content, Edit's new_string (what's
    being introduced, not what it replaces), MultiEdit's new_string
    values newline-joined (see _multi_edit_text), or apply_patch's added
    ("+") lines scoped to the first file header (see _patch_added_text).
    """
    try:
        kind = tool_kind(payload)
        ti = _tool_input(payload)
        if ti is None:
            return None
        if _tool_name(payload) == "apply_patch":
            patch = _as_str(ti.get("input"))
            return _patch_added_text(patch) if patch is not None else None
        if kind == "write":
            return _as_str(ti.get("content"))
        if kind == "edit":
            multi = _multi_edit_text(ti)
            return multi if multi is not None else _as_str(ti.get("new_string"))
        return None
    except Exception:
        return None


def assistant_text(payload: object) -> str | None:
    """The assistant's turn text, when the hook payload carries it
    directly (Stop-event payloads on both harnesses expose
    last_assistant_message). No transcript fallback here: this module's
    payload-dict functions do no I/O; iter_transcript_tool_uses is the
    one exception, and it reads tool calls, not assistant prose.
    """
    try:
        d = _as_dict(payload)
        return _as_str(d.get("last_assistant_message")) if d is not None else None
    except Exception:
        return None


def _parse_call_arguments(raw: object) -> object:
    """Codex function_call `arguments` is typically a JSON-encoded
    string; parse it when possible, keep the raw value otherwise (never
    raise on malformed JSON) so a caller still gets something rather
    than nothing."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return raw
    return raw


def _iter_transcript_records(path: object) -> list:
    out: list = []
    # open() treats an int as a raw OS file descriptor, not a filename —
    # an int path (e.g. True, which is 1, which is stdout) would open
    # that live fd instead of failing, and the `with` block below would
    # then close it on the way out. That closes a fd the process needs
    # with no exception ever reaching the `except` below to report it.
    # Reject anything that isn't an actual path before open() ever sees
    # it, so this failure mode can't reach the open() call at all.
    if not isinstance(path, (str, os.PathLike)):
        return out
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return out

    for line in lines:
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if not isinstance(entry, dict):
            continue

        if entry.get("type") == "assistant":
            message = entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        out.append({
                            "id": block.get("id"),
                            "name": block.get("name"),
                            "input": block.get("input"),
                        })
            continue

        if entry.get("type") == "response_item":
            item = entry.get("payload")
            if not isinstance(item, dict):
                continue
            itype = item.get("type")
            if itype == "function_call":
                out.append({
                    "id": item.get("call_id"),
                    "name": join_mcp_name(item),
                    "input": _parse_call_arguments(item.get("arguments")),
                })
            elif itype == "custom_tool_call":
                # Codex's freeform-input record type — exec's own shape,
                # distinct from exec_command's function_call. Field names
                # (call_id/name/input) follow the same convention as
                # function_call; unverified against a real custom_tool_call
                # transcript sample (see task report).
                out.append({
                    "id": item.get("call_id"),
                    "name": item.get("name"),
                    "input": item.get("input"),
                })

    return out


def iter_transcript_tool_uses(path: object) -> Iterator[dict]:
    """Yield {id, name, input} for every tool call in a session
    transcript, across both JSONL schemas:

    - Claude Code: type=="assistant" entries; message.content blocks
      with type=="tool_use" (id, name, input already flat).
    - Codex: type=="response_item" entries wrapping a payload;
      payload.type=="function_call" (id=call_id, name via join_mcp_name
      since MCP calls split namespace/name here, input from its
      JSON-or-raw `arguments`) or payload.type=="custom_tool_call"
      (id=call_id, name, input taken as-is).

    Total: a non-path argument (including an int, which open() would
    treat as a raw fd rather than a filename — see _iter_transcript_records),
    a missing file, undecodable line, or any other read/parse failure
    yields nothing rather than raising. Built eagerly (not a generator)
    so a failure can never surface mid-iteration either — the returned
    iterator is exhausted-safe from the moment this function returns.
    """
    try:
        return iter(_iter_transcript_records(path))
    except Exception:
        return iter(())
