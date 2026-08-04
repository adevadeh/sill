#!/usr/bin/env python3
"""
Hook: Track verification tool calls within a turn.
Used by check-agreement hook to know if verification happened.
Triggers on PostToolUse for verification tools (Bash, Read, Grep, memory recall).
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

_SILL_LOG_DIR = Path(os.environ.get("SILL_LOG_DIR", "/tmp"))
STATE_FILE = _SILL_LOG_DIR / "verification-state.json"
LOG_FILE = _SILL_LOG_DIR / "agreement-hook.log"

def log(message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(f"{timestamp} | {message}\n")
    except Exception:
        pass

def mark_verified(tool_name: str):
    """Record that a verification tool was used."""
    state = {"verified": True, "tool": tool_name, "timestamp": datetime.now().isoformat()}
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state))
    except Exception:
        pass
    log(f"VERIFIED via {tool_name}")

try:
    input_data = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(0)

tool_name = input_data.get("tool_name", "")
# Mark verification for any of these tools
if tool_name:
    mark_verified(tool_name)

sys.exit(0)
