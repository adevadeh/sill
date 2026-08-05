#!/usr/bin/env python3
"""
Hook: Check if stopping with agreement phrases but no verification.
Triggers on Stop event.

This catches the OUTPUT pattern (agreeing without verifying) rather than
trying to infer INPUT intent (detecting corrections).
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

_SILL_LOG_DIR = Path(os.environ.get("SILL_LOG_DIR", "/tmp"))
STATE_FILE = _SILL_LOG_DIR / "verification-state.json"
LOG_FILE = _SILL_LOG_DIR / "agreement-hook.log"

# Agreement phrases that suggest accepting a correction without verification
AGREEMENT_PHRASES = [
    "you're right",
    "you are right",
    "you're correct",
    "you are correct",
    "my mistake",
    "my apologies",
    "i apologize",
    "sorry about that",
    "i was wrong",
    "let me fix that",
    "good catch",
    "thanks for catching",
    "i'll correct that",
]

def log(message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(f"{timestamp} | {message}\n")
    except Exception:
        pass

def was_verified() -> bool:
    """Check if verification happened this turn."""
    try:
        state = json.loads(STATE_FILE.read_text())
        return state.get("verified", False)
    except:
        return False

try:
    input_data = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(0)
if not isinstance(input_data, dict):
    # Valid JSON that isn't an object — a bare number, string, null, or
    # bool — survives get_response_text()'s list-shaped case (its "key" in
    # data checks are membership tests, harmless on a list) but not these:
    # "key" in 42 / in None / in True all raise TypeError.
    sys.exit(0)

def contains_agreement_phrase(text: str) -> bool:
    """Check if text contains any agreement phrases."""
    text_lower = text.lower()
    return any(phrase in text_lower for phrase in AGREEMENT_PHRASES)

def get_response_text(data: dict) -> str:
    """Extract assistant response text from hook data."""
    # Try various possible locations for response text
    if "transcript" in data:
        # Look for assistant messages in transcript
        messages = data.get("transcript", [])
        if isinstance(messages, list):
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        return content
                    elif isinstance(content, list):
                        # Content might be array of content blocks
                        return " ".join(
                            block.get("text", "")
                            for block in content
                            if isinstance(block, dict) and block.get("type") == "text"
                        )
    # Try direct message field
    if "message" in data:
        return str(data["message"])
    if "response" in data:
        return str(data["response"])
    return ""

verified = was_verified()
response_text = get_response_text(input_data)
has_agreement = contains_agreement_phrase(response_text)

# Only warn if: agreement phrases detected AND no verification
if has_agreement and not verified:
    log("STOP with agreement but no verification - injecting reminder")
    output = {
        "additionalContext": "[SELF-CHECK] Did you verify before agreeing? If you accepted a correction or said 'you're right' without using Bash/Read/Grep/memory to check, reconsider."
    }
    print(json.dumps(output))
elif verified:
    log("STOP with verification - OK")
else:
    log("STOP without agreement phrases - OK")

sys.exit(0)
