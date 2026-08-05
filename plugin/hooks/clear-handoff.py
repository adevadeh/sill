#!/usr/bin/env python3
# Ported from agi-memory .claude/hooks/clear-handoff.py (2026-08-04).
"""SessionStart hook: after /clear, re-inject the previous session's final
assistant message — the natural handoff summary — as context.

Mechanism: /clear keeps the same Claude Code process but mints a new session
ID. So on every SessionStart we record {claude-pid -> session}, and on
source == "clear" the stale mapping for our own pid names exactly the session
this window just left (immune to other windows writing the same project's
transcript dir). Fallback when no mapping exists: newest interactive
transcript in the project dir, flagged as a heuristic pick.

Parses Claude Code transcript shapes (JSONL turns keyed by type/message/
isSidechain). Codex sessions carry a different shape, so a Codex
SessionStart event just won't match anything here and the hook exits
silently. Stdlib only; must never block or crash session start.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

MAP_DIR = Path(os.environ.get("SILL_LOG_DIR", "/tmp")) / "cc-session-by-pid"
MAX_CHARS = 10_000


def claude_ancestor_pid():
    """Walk up the process tree to the claude CLI process that spawned us.

    Hook and tool shells are sometimes spawned via short-lived intermediate
    processes that also match 'claude', so take the TOPMOST match in the
    chain — the persistent CLI process — not the first (first-match produced
    a different transient pid per invocation).
    """
    pid = os.getppid()
    topmost = None
    for _ in range(15):
        if pid <= 1:
            break
        try:
            out = subprocess.run(
                ["ps", "-o", "ppid=,command=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            if not out:
                break
            ppid_str, command = out.split(None, 1)
        except Exception:
            break
        if re.search(r"(^|/)claude([ -]|$)", command):
            topmost = pid
        pid = int(ppid_str)
    return topmost


def window_key():
    pid = claude_ancestor_pid()
    if pid:
        return f"pid-{pid}"
    term = os.environ.get("ITERM_SESSION_ID") or os.environ.get("TERM_SESSION_ID")
    if term:
        return "term-" + re.sub(r"[^A-Za-z0-9._-]", "_", term)
    return None


def read_mapping(key):
    try:
        with open(os.path.join(MAP_DIR, key + ".json")) as f:
            return json.load(f)
    except Exception:
        return None


def write_mapping(key, session_id, transcript_path):
    os.makedirs(MAP_DIR, exist_ok=True)
    with open(os.path.join(MAP_DIR, key + ".json"), "w") as f:
        json.dump({"session_id": session_id, "transcript_path": transcript_path}, f)


def last_assistant_message(transcript_path):
    """Return (text, iso_timestamp) of the final assistant text in a transcript."""
    text, ts = None, None
    try:
        with open(transcript_path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("type") != "assistant" or d.get("isSidechain"):
                    continue
                content = d.get("message", {}).get("content", [])
                if not isinstance(content, list):
                    continue
                for block in content:
                    if (isinstance(block, dict) and block.get("type") == "text"
                            and block.get("text", "").strip()):
                        text = block["text"]
                        ts = d.get("timestamp")
    except Exception:
        return None, None
    return text, ts


def is_interactive(transcript_path):
    """Interactive sessions open with a 'mode' entry; headless ones don't."""
    try:
        with open(transcript_path) as f:
            for _, line in zip(range(5), f):
                if json.loads(line).get("type") == "mode":
                    return True
    except Exception:
        pass
    return False


def fallback_predecessor(current_transcript):
    project_dir = os.path.dirname(current_transcript)
    current = os.path.basename(current_transcript)
    candidates = []
    try:
        for name in os.listdir(project_dir):
            if (not name.endswith(".jsonl") or name.startswith("agent-")
                    or name == current):
                continue
            path = os.path.join(project_dir, name)
            candidates.append((os.path.getmtime(path), path))
    except Exception:
        return None
    for _, path in sorted(candidates, reverse=True):
        if is_interactive(path):
            return path
    return None


def local_time(iso_ts):
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%a %Y-%m-%d %H:%M %Z")
    except Exception:
        return iso_ts or "unknown time"


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    session_id = data.get("session_id", "")
    transcript_path = data.get("transcript_path", "")
    source = data.get("source", "")

    key = window_key()

    context = None
    if source == "clear":
        prev_path, how = None, None
        mapping = read_mapping(key) if key else None
        if mapping and mapping.get("session_id") != session_id:
            prev_path, how = mapping.get("transcript_path"), "exact"
        if not prev_path or not os.path.exists(prev_path):
            prev_path, how = fallback_predecessor(transcript_path), "heuristic"
        if prev_path and os.path.exists(prev_path):
            text, ts = last_assistant_message(prev_path)
            if text:
                if len(text) > MAX_CHARS:
                    text = text[:MAX_CHARS] + "\n[... truncated by clear-handoff hook]"
                slug = os.path.basename(prev_path).split("-")[0]
                note = ("" if how == "exact" else
                        " — predecessor picked by recency heuristic; may be wrong"
                        " if another window was active")
                context = (
                    f"[CLEAR HANDOFF] Final assistant message of the session this"
                    f" window just cleared (session {slug}, last assistant message"
                    f" {local_time(ts)}{note}). Background context — where things"
                    f" stood, in that session's own closing words:\n\n{text}"
                )

    if key and session_id:
        try:
            write_mapping(key, session_id, transcript_path)
        except Exception:
            pass

    if context:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
