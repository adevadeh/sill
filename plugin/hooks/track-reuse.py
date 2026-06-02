#!/usr/bin/env python3
"""
Reuse tracking hook: Detect when hydrated memories appear in responses.

Triggers on Stop event. Checks if memory IDs from recall/hydrate appear
in the response text, and calls touch_memory_reuse() for matches.

This provides a "value signal" - memories that get reused are more valuable
than those that are just accessed but never referenced.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import psycopg2

LOG_FILE = Path(os.environ.get("SILL_LOG_DIR", "/tmp")) / "reuse-tracking.log"

# Database connection
DB_HOST = os.environ.get("POSTGRES_HOST", "localhost")
DB_PORT = os.environ.get("POSTGRES_PORT", "5432")
DB_NAME = os.environ.get("POSTGRES_DB", "sill")
DB_USER = os.environ.get("POSTGRES_USER", "sill")
DB_PASS = os.environ.get("POSTGRES_PASSWORD", "sill_password")


def log(message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"{timestamp} | {message}\n")


def get_db_connection():
    try:
        return psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
        )
    except Exception as e:
        log(f"DB connection failed: {e}")
        return None


def get_response_text(data: dict) -> str:
    """Extract assistant response text from hook data."""
    if isinstance(data.get("last_assistant_message"), str):
        return data["last_assistant_message"]

    if "transcript_path" in data:
        try:
            transcript_path = Path(data["transcript_path"])
            if transcript_path.exists():
                with open(transcript_path, "r") as f:
                    lines = f.readlines()
                for line in reversed(lines):
                    try:
                        entry = json.loads(line.strip())
                        if entry.get("type") == "assistant":
                            message = entry.get("message", {})
                            content = message.get("content", [])
                            if isinstance(content, str):
                                return content
                            elif isinstance(content, list):
                                return " ".join(
                                    block.get("text", "")
                                    for block in content
                                    if isinstance(block, dict)
                                    and block.get("type") == "text"
                                )
                        payload = entry.get("payload", {})
                        if (
                            entry.get("type") == "response_item"
                            and isinstance(payload, dict)
                            and payload.get("type") == "message"
                            and payload.get("role") == "assistant"
                        ):
                            content = payload.get("content", [])
                            if isinstance(content, list):
                                return " ".join(
                                    block.get("text", "")
                                    for block in content
                                    if isinstance(block, dict) and block.get("type") == "output_text"
                                )
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            log(f"Error reading transcript_path: {e}")
    return ""


def get_recalled_memories(data: dict) -> list[dict]:
    """Extract memories from recall/hydrate tool results in this turn.

    Transcript shape (what Claude Code actually writes):
    - Top-level entries have type "user" or "assistant"
    - Tool USES are content blocks inside assistant messages: type=tool_use, id, name
    - Tool RESULTS are content blocks inside user messages: type=tool_result, tool_use_id, content
    - "Real" user typed messages have content as a string OR a list with text blocks

    We walk backwards collecting tool_use (id->name) and tool_result (use_id, content_str),
    stopping at the previous real user turn. Then match by use_id and extract memories
    from results whose tool name contains "recall" or "hydrate".
    """
    memories = []

    transcript_path = data.get("transcript_path")
    if not transcript_path:
        return memories

    try:
        with open(transcript_path, "r") as f:
            lines = f.readlines()

        tool_uses: dict[str, str] = {}  # tool_use_id -> tool_name
        tool_results: list[tuple[str, str]] = []  # (tool_use_id, content_str)

        for line in reversed(lines):
            try:
                entry = json.loads(line.strip())
            except json.JSONDecodeError:
                continue

            entry_type = entry.get("type", "")
            if entry_type not in ("user", "assistant"):
                continue

            message = entry.get("message", {})
            content = message.get("content", None)

            if entry_type == "user":
                # String content = real typed user input — end of turn going back
                if isinstance(content, str):
                    break
                if isinstance(content, list):
                    has_real_text = False
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type", "")
                        if btype == "tool_result":
                            tool_results.append(
                                (block.get("tool_use_id", ""), block.get("content", ""))
                            )
                        elif btype == "text":
                            # Real user text block (not tool_result) — boundary
                            has_real_text = True
                    if has_real_text:
                        break
            elif entry_type == "assistant":
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tool_uses[block.get("id", "")] = block.get("name", "")

        # Codex transcript shape: response_item/function_call and
        # response_item/function_call_output keyed by call_id.
        for line in reversed(lines):
            try:
                entry = json.loads(line.strip())
            except json.JSONDecodeError:
                continue

            payload = entry.get("payload", {})
            if not isinstance(payload, dict) or entry.get("type") != "response_item":
                continue

            if (
                payload.get("type") == "message"
                and payload.get("role") == "user"
                and payload.get("content")
            ):
                break

            if payload.get("type") == "function_call":
                namespace = payload.get("namespace", "")
                name = payload.get("name", "")
                tool_uses[payload.get("call_id", "")] = f"{namespace}{name}"
            elif payload.get("type") == "function_call_output":
                tool_results.append((payload.get("call_id", ""), payload.get("output", "")))

        # Match results to uses; extract from recall/hydrate results
        for tool_use_id, result_content in tool_results:
            tool_name = tool_uses.get(tool_use_id, "")
            if not ("recall" in tool_name or "hydrate" in tool_name):
                continue

            # Result content can be a JSON string or a list of content blocks
            parsed = parse_tool_result(result_content)
            if parsed is None:
                continue

            # Memories may be under "memories", "previews", or be top-level list
            mem_list = []
            if isinstance(parsed, dict):
                mem_list = parsed.get("memories") or parsed.get("previews") or []
            elif isinstance(parsed, list):
                mem_list = parsed

            for mem in mem_list:
                if isinstance(mem, dict) and "id" in mem:
                    text = mem.get("content") or mem.get("preview") or ""
                    memories.append({"id": mem["id"], "content": text[:200]})

    except Exception as e:
        log(f"Error extracting recalled memories: {e}")

    return memories


def parse_tool_result(result_content):
    """Parse Claude or Codex tool-result payloads into JSON when possible."""
    raw = result_content
    if isinstance(raw, list):
        raw = " ".join(
            b.get("text", "")
            for b in raw
            if isinstance(b, dict) and b.get("type") == "text"
        )
    if not isinstance(raw, str):
        return None

    candidates = [raw]
    if "\nOutput:\n" in raw:
        candidates.append(raw.rsplit("\nOutput:\n", 1)[1].strip())

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue

        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            text = parsed[0].get("text")
            if isinstance(text, str):
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return parsed
        return parsed

    return None


def check_memory_in_response(memory: dict, response: str) -> bool:
    """Check if a memory appears to be referenced in the response."""
    mem_id = memory.get("id", "")
    content = memory.get("content", "")

    # Check if memory ID is mentioned (unlikely but possible)
    if mem_id and mem_id in response:
        return True

    # Check if significant content phrases appear
    # Look for 3+ word phrases from the memory content
    if content:
        # Extract words, look for matching phrases
        words = content.split()
        if len(words) >= 3:
            # Check a few key phrases
            for i in range(0, min(len(words) - 2, 10)):
                phrase = " ".join(words[i : i + 3])
                # Skip very common phrases
                if len(phrase) > 15 and phrase.lower() in response.lower():
                    return True

    return False


def touch_memory_reuse(conn, memory_id: str):
    """Mark a memory as reused."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT touch_memory_reuse(%s::uuid)", (memory_id,))
            conn.commit()
            return True
    except Exception as e:
        log(f"Error touching memory {memory_id}: {e}")
        conn.rollback()
        return False


# Main execution
try:
    input_data = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(0)

# Unconditional fire-trace so we can confirm the hook runs at all in production.
log(f"hook invoked, transcript_path={input_data.get('transcript_path', 'MISSING')}")

response_text = get_response_text(input_data)
recalled_memories = get_recalled_memories(input_data)

log(f"response_text len={len(response_text)}, recalled_memories n={len(recalled_memories)}")

if not response_text or not recalled_memories:
    sys.exit(0)

log(f"Checking {len(recalled_memories)} recalled memories for reuse")

# Check each memory for reuse
reused = []
for mem in recalled_memories:
    if check_memory_in_response(mem, response_text):
        reused.append(mem["id"])

if reused:
    log(f"Found {len(reused)} reused memories")

    # Update database
    conn = get_db_connection()
    if conn:
        for mem_id in reused:
            if touch_memory_reuse(conn, mem_id):
                log(f"Marked as reused: {mem_id}")
        conn.close()
else:
    log("No memories reused in response")

sys.exit(0)
