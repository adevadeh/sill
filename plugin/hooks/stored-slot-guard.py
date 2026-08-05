#!/usr/bin/env python3
# Ported from agi-memory .claude/hooks/stored-slot-guard.py (2026-08-04).
"""stored-slot-guard: Write/Edit-time check for mint receipts.

A receipt line naming a memory id that was never actually minted is a
fabrication, and it becomes permanent the moment the Write/Edit lands. This
guard closes that window: it checks any receipt-shaped id against the store
before the tool call is allowed through.

The canonical pre-mint form is the literal placeholder line exported as
RECEIPT_PLACEHOLDER by the mint module (backend/sill.py) — never an
invented id. Placeholder lines pass this guard by construction: the
placeholder text is not hex-id-shaped, so it never matches the receipt-line
pattern below.

Scope: opt-in via SILL_BEAT_JOURNAL_DIRS (colon-separated path fragments).
Unset or empty means no scope at all, so an install that never turned on
the beat worker pays nothing for this check.

Checked form: receipt lines — `Stored:` at line start with an id following.
Mention exemption (head-only): a line is skipped as specimen material when
its HEAD — the text before "Stored"/"stored", matched case-insensitively —
contains a backtick, or when the line is blockquoted. The exemption is
head-only: an id backticked AFTER a bare line-start `Stored:` is still
checked, so a confession should backtick or blockquote the whole receipt
line, not just the id. The head split is case-insensitive by design and
must stay that way: a case-sensitive split finds no match on a lowercase
receipt and returns the WHOLE LINE as head, so any backtick anywhere on
the line — even an unrelated trailing aside — falsely exempts it (this
guard's own 2026-08-04 bug, closed same-day). Known limit: a forgery
wearing full quote typography around the whole line also passes this guard
— a separate act-history check (tool-type-witness.py) and any downstream
record checks cover that side.

Fails OPEN when the store is unreachable — a down store must not block a
journal write. Each id is checked independently, so one unreachable lookup
skips only that id rather than abandoning the rest of the line's ids.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DB_CONTAINER = os.environ.get("SILL_DB_CONTAINER", "sill_db")
DB_USER = os.environ.get("SILL_DB_USER", "sill")
DB_NAME = os.environ.get("SILL_DB_NAME", "sill")

RECEIPT_LINE = re.compile(
    r"^[\s*\-#]*(?:\*\*)?Stored(?:\*\*)?\s*:\s*`?([0-9a-f]{8})[0-9a-f\-]*",
    re.IGNORECASE,
)


def _resolve_placeholder() -> str | None:
    """Resolve the mint module's receipt placeholder without retyping it —
    the literal (its dash is U+2014) is a wire contract, and a hand-typed
    copy is exactly how a wire contract drifts silently. Import the mint
    module if it is importable; otherwise read the one assignment line
    straight out of its source with a narrow regex. Returns None if both
    paths fail, so the caller can fall back to describing the slot
    generically instead of quoting a literal it could not confirm.
    """
    try:
        import sill
        return sill.RECEIPT_PLACEHOLDER
    except Exception:
        pass
    try:
        sill_py = Path(__file__).resolve().parents[2] / "backend" / "sill.py"
        text = sill_py.read_text(encoding="utf-8")
        m = re.search(r'^RECEIPT_PLACEHOLDER\s*=\s*"(.*)"\s*$', text, re.MULTILINE)
        return m.group(1) if m else None
    except Exception:
        return None


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
    if data.get("tool_name") not in ("Write", "Edit"):
        sys.exit(0)
    tool_input = data.get("tool_input") or {}
    path = tool_input.get("file_path") or ""
    if not _in_scope(path):
        sys.exit(0)
    text = tool_input.get("content") or tool_input.get("new_string") or ""
    if "stored" not in text.lower():
        sys.exit(0)

    ids = []
    for line in text.splitlines():
        head = re.split(r"stored", line, maxsplit=1, flags=re.IGNORECASE)[0]
        if "`" in head or line.lstrip().startswith(">"):
            continue  # quoted/backticked context: specimen material, not a receipt
        m = RECEIPT_LINE.match(line)
        if m:
            ids.append(m.group(1).lower())
    if not ids:
        sys.exit(0)

    missing = []
    for h in dict.fromkeys(ids):
        try:
            r = subprocess.run(
                ["docker", "exec", DB_CONTAINER, "psql", "-U", DB_USER, "-d",
                 DB_NAME, "-t", "-A", "-c",
                 f"SELECT count(*) FROM memories WHERE id::text LIKE '{h}%';"],
                capture_output=True, text=True, timeout=8,
            )
            if r.returncode != 0:
                continue  # store unreachable for this id: skip it, check the rest
            if r.stdout.strip() == "0":
                missing.append(h)
        except Exception:
            continue  # same: unreachable, skip only this id

    if missing:
        reason = (
            f"stored-slot-guard: receipt-shaped id(s) {missing} have no row "
            "in the store — a receipt line naming an id that was never "
            "minted. "
        )
        placeholder = _resolve_placeholder()
        if placeholder:
            reason += (
                "Until the real mint returns, the slot holds the literal "
                f"placeholder line {placeholder!r}. "
            )
        reason += (
            "Run the mint, then paste its printed receipt line verbatim by "
            "Edit. To quote a forged id as a specimen, backtick it or use "
            "quoted context — bare receipt lines are the checked form."
        )
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }))
    sys.exit(0)


main()
