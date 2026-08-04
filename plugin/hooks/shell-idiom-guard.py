#!/usr/bin/env python3
# Ported from agi-memory .claude/hooks/shell-idiom-guard.py (2026-08-04).
"""PreToolUse Bash guard: block the zsh `echo =word` separator trap.

Mechanism: zsh `=word` expansion, triggered by an unquoted word starting
with `=` right after `echo`, either substitutes a path in place of the
word or silently eats the rest of a compound line when the word doesn't
resolve. Both payloads corrupt the command without raising an error the
operator is likely to notice.
"""
import json
import re
import sys

try:
    data = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(0)

if data.get("tool_name") != "Bash":
    sys.exit(0)

cmd = str((data.get("tool_input") or {}).get("command", ""))

# Blank out quoted spans first: zsh does not =expand inside quotes, so
# `echo '==='` is safe and so is a `===` buried in a quoted JSON/string
# argument. Approximation, fine for a guard.
_unquoted = re.sub(r"'[^']*'", "''", cmd)
_unquoted = re.sub(r'"[^"]*"', '""', _unquoted)

# `echo` in command position followed by any unquoted word starting with `=`.
# `echo =` alone, `echo a=b`, and quoted separators stay allowed.
TRAP = re.compile(r"(?:^|[;&|`(]\s*)echo\s+=\S", re.MULTILINE)

if TRAP.search(_unquoted):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "zsh =word trap: an unquoted word starting with '=' after "
                "echo either errors or silently swallows the rest of the "
                "line. Quote it ('===') or use printf."
            ),
        }
    }))
    sys.exit(0)

sys.exit(0)
