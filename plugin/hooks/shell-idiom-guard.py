#!/usr/bin/env python3
# Ported from agi-memory .claude/hooks/shell-idiom-guard.py (2026-08-04).
"""PreToolUse Bash guard: block the zsh `echo =word` separator trap.

Mechanism: zsh `=word` expansion, triggered by an unquoted word starting
with `=` right after `echo`, either substitutes a path in place of the
word or silently eats the rest of a compound line when the word doesn't
resolve. Both payloads corrupt the command without raising an error the
operator is likely to notice.

Fires on both harnesses via _harness.tool_kind/shell_command: Claude's
Bash and Codex's exec/exec_command all normalize to "shell" (see
_harness.py for the tool-name mapping). Fails open — exits 0, no
output — if _harness itself cannot be imported, rather than crash or
silently fall back to a Claude-only string match.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path


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
if _harness is None:
    sys.exit(0)

try:
    data = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(0)

if _harness.tool_kind(data) != "shell":
    sys.exit(0)

cmd = str(_harness.shell_command(data) or "")

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
