#!/usr/bin/env python3
# Ported from agi-memory .claude/hooks/tool-type-witness.py (2026-08-04).
"""tool-type-witness: Write-time check for carrying-act claims.

A Write whose content claims its own carrying-act history — "this arrived
by Edit", "this held the literal placeholder", "the initial Write ended
at" — is a performative contradiction: the claim about how the text got
here is falsified by the name of the tool carrying it right now. This is a
pattern check only — no store lookup, no prose judgment.

Why there is no confession hole: an honest report of that history can only
be written from inside an Edit (this hook matches Write only), and an
honest quotation of one of these phrases wears quote typography — so a line
that states the phrase as unquoted prose inside a Write is exactly the
contradiction this hook exists to catch. Exemptions: blockquoted lines;
parity count on backticks and straight double quotes before the match;
open-exceeds-close on curly double quotes.

Scope: opt-in via SILL_BEAT_JOURNAL_DIRS (colon-separated path fragments).
Unset or empty means no scope at all, so an install that never turned on
the beat worker pays nothing for this check. Fails open on any parse
error. Registered on Write only — an Edit-delivered version of this text
is not a contradiction, so checking Edit would only cost a wasted
interpreter start on every edit.
"""
import json
import os
import re
import sys

PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r"arrived\s+by\s+Edit",
    r"held\s+the\s+literal\s+placeholder",
    r"initial\s+Write\s+ended\s+at",
)]


def quoted_at(line: str, pos: int) -> bool:
    """Is position `pos` inside quote/backtick/blockquote context on this line?"""
    if line.lstrip().startswith(">"):
        return True
    head = line[:pos]
    if head.count("`") % 2 == 1:
        return True
    if head.count('"') % 2 == 1:
        return True
    if head.count("“") > head.count("”"):
        return True
    return False


def _in_scope(path: str) -> bool:
    raw = os.environ.get("SILL_BEAT_JOURNAL_DIRS", "")
    fragments = [f for f in raw.split(":") if f]
    return any(f in path for f in fragments)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    # A non-object payload (e.g. "[]") has no .get() — every hook exits 0
    # on every path, so this is a scope check, not an exception handler.
    if not isinstance(data, dict):
        sys.exit(0)
    if data.get("tool_name") != "Write":
        sys.exit(0)  # an Edit-delivered version of this text is not a contradiction
    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path") or ""
    if not _in_scope(path):
        sys.exit(0)
    text = tool_input.get("content") or ""

    hits = []
    for line in text.splitlines():
        for pat in PATTERNS:
            m = pat.search(line)
            if m and not quoted_at(line, m.start()):
                hits.append(m.group(0))
    if not hits:
        sys.exit(0)

    uniq = list(dict.fromkeys(hits))
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"tool-type-witness: this Write claims a carrying-act history "
                f"it cannot have — {uniq} delivered by the Write tool is a "
                "performative contradiction (the honest version of this "
                "sentence can only be written from inside an Edit). If the "
                "acts already happened, deliver the sentence by Edit after "
                "them — or, if this Write is itself the act being "
                "described, say so in the present tense instead of naming "
                "the tool. To QUOTE such a phrase as specimen material, "
                "wrap it in double quotes or backticks, or blockquote the "
                "line."
            ),
        }
    }))
    sys.exit(0)


main()
