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

Fires on both harnesses via _harness.tool_kind/written_path/written_text:
Claude's Write and Codex's apply_patch both normalize to "write" kind
(apply_patch always — even a "*** Update File:" body — never "edit"; see
_harness.py). Fails open — exits 0, no output — if _harness itself
cannot be imported.
"""
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r"arrived\s+by\s+Edit",
    r"held\s+the\s+literal\s+placeholder",
    r"initial\s+Write\s+ended\s+at",
)]


def _load_harness():
    try:
        spec = importlib.util.spec_from_file_location(
            "_sill_harness", Path(__file__).resolve().parent / "_harness.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


_harness = _load_harness()


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
    if _harness is None:
        sys.exit(0)
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    # A non-object payload (e.g. "[]") has no .get() — every hook exits 0
    # on every path, so this is a scope check, not an exception handler.
    if not isinstance(data, dict):
        sys.exit(0)
    if _harness.tool_kind(data) != "write":
        sys.exit(0)  # an Edit-delivered version of this text is not a contradiction
    path = _harness.written_path(data) or ""
    if not _in_scope(path):
        sys.exit(0)
    text = _harness.written_text(data) or ""

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
