#!/usr/bin/env python3
"""
Hook: Reset verification state and optionally remind about corrections.
Triggers on UserPromptSubmit.
"""
import json
import os
import sys
import re
from datetime import datetime
from pathlib import Path

_SILL_LOG_DIR = Path(os.environ.get("SILL_LOG_DIR", "/tmp"))
LOG_FILE = _SILL_LOG_DIR / "correction-hook.log"
STATE_FILE = _SILL_LOG_DIR / "verification-state.json"

def reset_verification_state():
    """Clear verification state at start of each turn."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"verified": False, "turn_start": datetime.now().isoformat()}))
    except Exception:
        pass

# Patterns that suggest the user is correcting or challenging something
CORRECTION_PATTERNS = [
    r"\bthat's (not|wrong|incorrect|vague|unclear)\b",
    r"\byou're (wrong|mistaken|incorrect)\b",
    r"\bactually[,\s]",
    r"\bthat doesn't (work|make sense|seem right)\b",
    r"\bno[,\s]+(?:that|it|this)\b",
    r"\bi don't think that's (right|correct|true)\b",
    r"\bare you sure\b",
    r"\bdid you (check|verify|test)\b",
    r"\bthat's not what\b",
    r"\bwait[,\s]",
    r"\bhmm\b",
    r"\biffy\b",
]

try:
    input_data = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(0)

# Reset verification state at start of each turn
reset_verification_state()

# Get the user's message
prompt = input_data.get("prompt", "")
if not prompt:
    sys.exit(0)

prompt_lower = prompt.lower()

# Check if any correction pattern matches
is_correction = any(re.search(pattern, prompt_lower) for pattern in CORRECTION_PATTERNS)

def log(message: str, triggered: bool, matched_pattern: str | None = None):
    """Append timestamped entry to log file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    preview = message[:80].replace('\n', ' ')
    pattern_info = f" [pattern: {matched_pattern}]" if matched_pattern else ""
    status = "TRIGGERED" if triggered else "checked"
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(f"{timestamp} | {status}{pattern_info} | {preview}\n")
    except Exception:
        pass

# Find which pattern matched (if any)
matched = None
for pattern in CORRECTION_PATTERNS:
    if re.search(pattern, prompt_lower):
        matched = pattern
        break

if is_correction:
    log(prompt, triggered=True, matched_pattern=matched)
    output = {
        "systemMessage": "[sill] Correction detected — verifying before responding",
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "[STOP] The user may be correcting you. DO NOT say 'You're right' or agree. Your FIRST action must be a tool call (Bash, Read, Grep, memory recall) to verify the claim. Only AFTER seeing evidence may you agree or disagree. This is not optional."
        }
    }
    print(json.dumps(output))
else:
    # Log non-triggers too so we can see all checks
    log(prompt, triggered=False)

sys.exit(0)
