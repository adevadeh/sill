#!/usr/bin/env python3
"""
Master hook: Check response text against configurable patterns.
Triggers on Stop event.

Reads pattern definitions from .claude/response-patterns/*.md files.
Each file uses hookify-style frontmatter:

---
name: pattern-name
enabled: true
patterns:
  - "regex pattern 1"
  - "regex pattern 2"
---

Warning message shown when pattern matches.
Can include {matched} placeholder for the matched text.
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# SILL_PLUGIN_DIR points at the installed plugin dir; default is the script's
# parent's parent (so the bundled response-patterns/ dir is picked up by default).
_PLUGIN_DIR = Path(
    os.environ.get(
        "SILL_PLUGIN_DIR",
        str(Path(__file__).resolve().parent.parent),
    )
)
PATTERNS_DIR = Path(
    os.environ.get("SILL_RESPONSE_PATTERNS_DIR", str(_PLUGIN_DIR / "response-patterns"))
)
_SILL_LOG_DIR = Path(os.environ.get("SILL_LOG_DIR", "/tmp"))
LOG_FILE = _SILL_LOG_DIR / "response-patterns.log"
DATA_FILE = _SILL_LOG_DIR / "response-patterns-data.jsonl"  # For analysis


def log(message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"{timestamp} | {message}\n")


def log_match(pattern_name: str, matched_text: str, response_snippet: str):
    """Log match data for later analysis."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "pattern": pattern_name,
        "matched": matched_text,
        "context": response_snippet[:200],
    }
    with open(DATA_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown content."""
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    frontmatter_text = parts[1].strip()
    body = parts[2].strip()

    # Simple YAML parsing for our needs
    frontmatter = {}
    current_list = None

    for line in frontmatter_text.split("\n"):
        line = line.rstrip()
        if not line:
            continue

        # Check for list item
        if line.startswith("  - "):
            if current_list is not None:
                # Remove quotes if present
                value = line[4:].strip().strip('"').strip("'")
                current_list.append(value)
            continue

        # Check for key: value
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()

            if not value:
                # Start of a list
                current_list = []
                frontmatter[key] = current_list
            else:
                # Simple value
                if value.lower() == "true":
                    value = True
                elif value.lower() == "false":
                    value = False
                frontmatter[key] = value
                current_list = None  # Reset list context

    return frontmatter, body


def load_patterns() -> list[dict]:
    """Load all enabled pattern definitions."""
    patterns = []

    if not PATTERNS_DIR.exists():
        return patterns

    for path in PATTERNS_DIR.glob("*.md"):
        try:
            content = path.read_text()
            frontmatter, message = parse_frontmatter(content)

            if not frontmatter.get("enabled", True):
                continue

            name = frontmatter.get("name", path.stem)
            pattern_list = frontmatter.get("patterns", [])

            if not pattern_list:
                continue

            patterns.append({
                "name": name,
                "patterns": pattern_list,
                "message": message,
                "file": path.name,
            })
        except Exception as e:
            log(f"Error loading {path.name}: {e}")

    return patterns


def get_response_text(data: dict) -> str:
    """Extract assistant response text from hook data."""
    # Codex Stop hooks provide this directly.
    if isinstance(data.get("last_assistant_message"), str):
        return data["last_assistant_message"]

    # Try transcript_path first (file-based transcript)
    if "transcript_path" in data:
        try:
            transcript_path = Path(data["transcript_path"])
            if transcript_path.exists():
                # Read JSONL file, find last assistant message
                with open(transcript_path, 'r') as f:
                    lines = f.readlines()
                for line in reversed(lines):
                    try:
                        entry = json.loads(line.strip())
                        # Check entry.type or entry.message.role
                        if entry.get("type") == "assistant":
                            message = entry.get("message", {})
                            content = message.get("content", [])
                            if isinstance(content, str):
                                return content
                            elif isinstance(content, list):
                                return " ".join(
                                    block.get("text", "")
                                    for block in content
                                    if isinstance(block, dict) and block.get("type") == "text"
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

    # Fall back to inline transcript
    if "transcript" in data:
        messages = data.get("transcript", [])
        if isinstance(messages, list):
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        return content
                    elif isinstance(content, list):
                        return " ".join(
                            block.get("text", "")
                            for block in content
                            if isinstance(block, dict) and block.get("type") == "text"
                        )
    if "message" in data:
        return str(data["message"])
    if "response" in data:
        return str(data["response"])
    return ""


def check_patterns(text: str, patterns: list[dict]) -> list[dict]:
    """Check text against all patterns, return matches."""
    matches = []
    text_lower = text.lower()

    for pattern_def in patterns:
        for regex in pattern_def["patterns"]:
            try:
                match = re.search(regex, text_lower, re.IGNORECASE)
                if match:
                    matches.append({
                        "name": pattern_def["name"],
                        "matched": match.group(0),
                        "message": pattern_def["message"],
                    })
                    break  # One match per pattern definition is enough
            except re.error as e:
                log(f"Invalid regex '{regex}' in {pattern_def['name']}: {e}")

    return matches


# Storage phrases that suggest intent to remember
STORAGE_PHRASES = [
    r"\bnoted\b",
    r"\bnoting\b",
    r"i'll store",
    r"i will store",
    r"let me store",
    r"let me remember",
    r"i'll remember",
    r"storing this",
    r"saving this",
    r"i'll save",
    r"recording this",
    r"i'll record",
    r"want to record",
    r"worth storing",
    r"worth remembering",
]

OLLAMA_URL = os.environ.get("SILL_OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.environ.get("SILL_OLLAMA_MODEL", "gemma3:12b")
# When SILL_INSIGHT_DETECT is unset or "0", skip the ollama-based insight check entirely.
INSIGHT_DETECT_ENABLED = os.environ.get("SILL_INSIGHT_DETECT", "0") not in ("0", "", "false", "False")
INSIGHT_PROMPT = """You are checking whether an AI assistant learned something SPECIFIC and NOVEL in its response — something worth saving to long-term memory because it would otherwise be lost.

Say YES only if the response contains a SPECIFIC insight: a new understanding, a non-obvious connection, a changed perspective with concrete detail about WHAT changed and WHY.

Say NO if:
- Routine task completion
- Generic acknowledgment ("I learned from your feedback", "good point")
- Following instructions without original reflection
- Factual reporting
- Asking questions
- The learning is vague or could apply to anything

Response to check:
{response}

Output format:
Line 1: YES or NO
Line 2 (only if YES): one sentence summarizing the specific insight
Line 3 (only if YES): 3-5 short concept tags, comma-separated (e.g. "s-consciousness, popperian-hooks, narrative-identity")"""

# SILL_CLI = path to a callable that takes `notice <content> --concepts ... --importance ...`
# and prints "Stored: <uuid>". Defaults to invoking `sill` console script.
SILL_CLI = os.environ.get("SILL_CLI", "sill")
AUTO_STORE_LOG = _SILL_LOG_DIR / "auto-stored-insights.jsonl"


def auto_store_insight(response_text: str, summary: str, concepts: list[str]) -> str | None:
    """Auto-store a detected insight via sill.py notice. Return memory id prefix or None."""
    import subprocess

    # Content = the insight summary + a relevant excerpt of the response.
    # Cap at ~1800 chars so the stored memory stays focused.
    excerpt = response_text.strip()
    if len(excerpt) > 1500:
        excerpt = excerpt[:1500] + "…"
    content = f"[AUTO-STORED BY HOOK] {summary}\n\n---\n{excerpt}"

    cmd = [
        SILL_CLI,
        "notice", content,
        "--importance", "0.6",
    ]
    if concepts:
        cmd.extend(["--concepts", ",".join(concepts)])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        out = (result.stdout or "").strip()
        # Expected stdout: "Stored: <uuid> [<N> tags]"
        m = re.search(r"Stored:\s+([0-9a-f]{8}-[0-9a-f-]+)", out)
        if m:
            mem_id = m.group(1)
            audit_entry = {
                "timestamp": datetime.now().isoformat(),
                "memory_id": mem_id,
                "summary": summary,
                "concepts": concepts,
                "excerpt_length": len(excerpt),
            }
            with open(AUTO_STORE_LOG, "a") as f:
                f.write(json.dumps(audit_entry) + "\n")
            log(f"Auto-stored insight as {mem_id[:8]}: {summary}")
            return mem_id[:8]
        else:
            log(f"Auto-store failed; sill.py output: {out!r} stderr: {(result.stderr or '').strip()!r}")
            return None
    except Exception as e:
        log(f"Auto-store exception: {e}")
        return None


def check_noted_without_noting(data: dict, response_text: str) -> dict | None:
    """Check if response claims to store but no remember() call was made."""
    text_lower = response_text.lower()
    matched_phrase = None
    for pattern in STORAGE_PHRASES:
        match = re.search(pattern, text_lower)
        if match:
            matched_phrase = match.group(0)
            break

    if not matched_phrase:
        return None

    if has_remember_call(data):
        return None

    return {
        "name": "noted-without-noting",
        "matched": matched_phrase,
        "message": f"You said \"{matched_phrase}\" but didn't call remember(). Did you actually store it, or just say you would?",
    }


def has_remember_call(data: dict) -> bool:
    """Check if remember() was called in the current turn."""
    transcript_path = data.get("transcript_path")
    if not transcript_path:
        return False

    try:
        with open(transcript_path, 'r') as f:
            lines = f.readlines()

        for line in reversed(lines):
            try:
                entry = json.loads(line.strip())
                entry_type = entry.get("type", "")

                if entry_type == "user":
                    break

                if entry_type == "assistant":
                    message = entry.get("message", {})
                    content = message.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if block.get("type") == "tool_use":
                                tool_name = block.get("name", "")
                                if "remember" in tool_name.lower():
                                    return True
            except json.JSONDecodeError:
                continue
    except Exception:
        pass

    return False


def check_insight_with_model(data: dict, response_text: str) -> dict | None:
    """Use a local model to check if the response contains an unstored insight."""
    import urllib.request

    if not INSIGHT_DETECT_ENABLED:
        return None

    # Pre-filter: skip short responses, skip if already stored something
    if len(response_text) < 300:
        return None

    if has_remember_call(data):
        return None

    # Call ollama
    prompt = INSIGHT_PROMPT.format(response=response_text[:2000])
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    })

    try:
        req = urllib.request.Request(
            OLLAMA_URL,
            data=payload.encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            answer = result.get("response", "").strip()

        lines = [l.strip() for l in answer.split('\n') if l.strip()]
        first_line = lines[0].upper() if lines else ""
        if first_line.startswith("YES"):
            insight_desc = lines[1] if len(lines) > 1 else "Insight detected."
            tag_line = lines[2] if len(lines) > 2 else ""
            concepts = [c.strip().lstrip('-•').strip() for c in tag_line.split(",") if c.strip()]
            # Drop empties and anything that still looks like formatting
            concepts = [c for c in concepts if c and not c.startswith('"')]

            log(f"Model detected insight: {insight_desc} | concepts: {concepts}")

            # Auto-store (option 3): default is preserve, not discard.
            mem_id = auto_store_insight(response_text, insight_desc, concepts)

            if mem_id:
                return {
                    "name": "insight-auto-stored",
                    "matched": mem_id,
                    "insight": insight_desc,
                    "message": f"Insight detected and auto-stored as {mem_id}: {insight_desc}. Refine or supersede next turn if the auto-stored version is weaker than what you'd have written.",
                }
            else:
                # Auto-store failed; fall back to old "will store after next message" flag
                return {
                    "name": "insight-not-stored",
                    "matched": "auto-store-failed",
                    "insight": insight_desc,
                    "message": f"This response contains an insight worth storing but auto-store failed: {insight_desc}. Store manually next turn.",
                }
        else:
            log(f"Model says no insight ({len(response_text)} chars)")

    except Exception as e:
        log(f"Insight model check failed: {e}")

    return None


# Main execution
try:
    input_data = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(0)

response_text = get_response_text(input_data)
if not response_text:
    sys.exit(0)

patterns = load_patterns()
if not patterns:
    log("No patterns loaded")
    sys.exit(0)

matches = check_patterns(response_text, patterns)

# Check for "noted without noting" (Option B from U2T1)
noted_match = check_noted_without_noting(input_data, response_text)
if noted_match:
    matches.append(noted_match)

# Check for insights that weren't stored (model-based)
insight_match = check_insight_with_model(input_data, response_text)
if insight_match:
    matches.append(insight_match)

if matches:
    # Log all matches for data gathering
    for m in matches:
        log_match(m["name"], m["matched"], response_text)
        log(f"MATCH: {m['name']} - '{m['matched']}'")

    # Combine warning messages
    warnings = []
    for m in matches:
        msg = m["message"].replace("{matched}", m["matched"])
        warnings.append(f"**{m['name']}**: {msg}")

    # Brief summary for the operator TUI — tell them what happened, not what to do
    notices = []
    for m in matches:
        name = m["name"]
        if name == "insight-auto-stored":
            insight = (m.get("insight") or "").strip().rstrip(".")
            if insight:
                notices.append(f"Insight ({insight}) — auto-stored as {m['matched']}")
            else:
                notices.append(f"Insight produced — auto-stored as {m['matched']}")
        elif name == "insight-not-stored":
            insight = (m.get("insight") or "").strip().rstrip(".")
            if insight:
                notices.append(f"Insight ({insight}) — auto-store failed, store manually")
            else:
                notices.append("Insight produced — auto-store failed, store manually")
        elif name == "noted-without-noting":
            notices.append(f"Said \"{m['matched']}\" but didn't actually store anything")
        elif name == "storage-deference":
            notices.append("Asked permission to store instead of storing")
        elif name == "block-hedge":
            notices.append(f"Hedged with dissolved P/A distinction: \"{m['matched']}\"")
        elif name == "hedging-without-thinking":
            notices.append(f"Hedged: \"{m['matched']}\"")
        elif name == "agreement":
            notices.append("Agreed without verifying")
        else:
            notices.append(f"{name}: \"{m['matched']}\"")
    # Stop hooks only support top-level fields (no hookSpecificOutput).
    # systemMessage shows in TUI; reason provides context back to the agent.
    output = {
        "reason": "[RESPONSE PATTERN CHECK]\n" + "\n\n".join(warnings)
    }
    if notices:
        output["systemMessage"] = "[sill] " + " | ".join(notices)
    print(json.dumps(output))
else:
    log(f"No matches in response ({len(response_text)} chars)")

sys.exit(0)
