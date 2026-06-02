"""Stage — multi-actor conversation orchestration via litellm.

Manages JSONL transcripts and parallel model calls for chorus scenarios.
The director (Claude Code) controls scenario logic; this module handles
message routing, transcript keeping, and parallel execution.

Usage as script:
    python chorus/stage.py beat transcript.jsonl '{"content": "...", "cast": {...}}'

Usage as library:
    from chorus.stage import run_beat
    result = asyncio.run(run_beat(transcript_path, beat_config))
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import litellm
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load env vars — local .env first, then optional override file via env var
load_dotenv()
_extra_env = os.environ.get("SILL_CHORUS_ENV_FILE")
if _extra_env:
    _extra_env_path = Path(_extra_env)
    if _extra_env_path.exists():
        load_dotenv(_extra_env_path, override=False)

# Suppress litellm's verbose logging
litellm.suppress_debug_info = True

# ---------------------------------------------------------------------------
# Model alias map — short names mapped to litellm model strings.
# Maps short names to litellm model strings.
# ---------------------------------------------------------------------------
MODEL_ALIASES: dict[str, str] = {
    "gemini": "gemini/gemini-3-pro-preview",
    "gemini-flash": "gemini/gemini-3-flash-preview",
    "gemini-2.5": "gemini/gemini-2.5-pro",
    "deepseek": "openrouter/deepseek/deepseek-chat-v3-0324",
    "r1": "openrouter/deepseek/deepseek-r1",
    "qwen": "openrouter/qwen/qwen3-max-thinking",
    "grok": "openrouter/x-ai/grok-4.1-fast",
    "mistral": "openrouter/mistralai/mistral-large-2512",
    "kimi": "openrouter/moonshotai/kimi-k2.5",
    "mimo": "openrouter/xiaomi/mimo-v2-flash",
    "step": "openrouter/stepfun/step-3.5-flash:free",
    "gpt": "openrouter/openai/gpt-5.2",
    "gpt-mini": "openrouter/openai/gpt-5-mini",
    "haiku": "openrouter/anthropic/claude-haiku-4-5-20251001",
    "sonnet": "openrouter/anthropic/claude-sonnet-4-5-20250929",
    "nova": "openrouter/amazon/nova-premier-v1",
}


def resolve_model(name: str) -> str:
    """Resolve alias to litellm model string. Passes through if not an alias."""
    return MODEL_ALIASES.get(name, name)


# ---------------------------------------------------------------------------
# CLI model registry — for actors invoked via subprocess
# ---------------------------------------------------------------------------

CLI_MODELS: dict[str, dict] = {
    "gemini-cli": {
        "command": "gemini",
        "args": ["-o", "json", "--approval-mode", "auto_edit"],
        "parser": "json",
    },
    "codex-cli": {
        "command": "codex",
        "args": ["exec", "--json", "--full-auto"],
        "parser": "jsonl",
    },
    "claude-cli": {
        "command": "claude",
        "args": ["--print", "--output-format", "json", "--permission-mode", "acceptEdits"],
        "parser": "json",
    },
}


# ---------------------------------------------------------------------------
# Mask loading
# ---------------------------------------------------------------------------

# Mask directory — overridable so users can ship their own concetti
_MASKS_DIR = Path(
    os.environ.get(
        "SILL_CHORUS_MASKS_DIR",
        str(Path(__file__).resolve().parent.parent / ".claude" / "skills" / "chorus" / "masks"),
    )
)
_mask_cache: dict[str, str | None] = {}


def load_mask_concetto(mask_name: str) -> str | None:
    """Load concetto (system prompt) for a mask from its markdown file.

    Looks for .claude/skills/chorus/masks/{mask_name}.md and extracts
    the blockquote under "Concetto (prompt template):".
    Returns None if mask file doesn't exist or has no concetto.
    """
    if mask_name in _mask_cache:
        return _mask_cache[mask_name]

    mask_file = _MASKS_DIR / f"{mask_name}.md"
    if not mask_file.exists():
        _mask_cache[mask_name] = None
        return None

    text = mask_file.read_text()

    # Extract blockquote lines after "Concetto" header
    concetto_lines = []
    in_concetto = False
    for line in text.splitlines():
        if "concetto" in line.lower() and "prompt template" in line.lower():
            in_concetto = True
            continue
        if in_concetto:
            if line.startswith(">"):
                # Strip the > prefix and optional space
                concetto_lines.append(re.sub(r"^>\s?", "", line))
            elif concetto_lines:
                # End of blockquote
                break

    if concetto_lines:
        result = "\n".join(concetto_lines).strip()
        _mask_cache[mask_name] = result
        return result

    _mask_cache[mask_name] = None
    return None


# ---------------------------------------------------------------------------
# Transcript I/O
# ---------------------------------------------------------------------------


def read_transcript(path: str) -> list[dict]:
    """Read JSONL transcript. Returns [] if file doesn't exist."""
    if not os.path.exists(path):
        return []
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def append_transcript(path: str, entries: list[dict]) -> None:
    """Append entries to JSONL transcript. Creates parent dirs if needed."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _get_session_id(transcript: list[dict]) -> str:
    """Get session ID from existing transcript or generate new one."""
    for entry in transcript:
        if "sessionId" in entry:
            return entry["sessionId"]
    return f"chorus-{uuid.uuid4()}"


def _count_beats(transcript: list[dict]) -> int:
    """Count beat entries in transcript."""
    return sum(
        1 for entry in transcript
        if entry.get("chorus", {}).get("entryType") == "beat"
    )


# ---------------------------------------------------------------------------
# Script formatting
# ---------------------------------------------------------------------------


def format_script(transcript: list[dict]) -> str:
    """Format transcript as natural script for actors to read.

    Beats → [stage direction], responses → Mask (actor): text.
    """
    lines = []
    for entry in transcript:
        chorus = entry.get("chorus", {})
        entry_type = chorus.get("entryType")
        message = entry.get("message", {})

        if entry_type == "beat":
            content = message.get("content", "")
            lines.append(f"[{content}]")

        elif entry_type == "response":
            mask = chorus.get("mask") or chorus.get("actor", "unknown")
            actor = chorus.get("actor", "")

            content = message.get("content", "")
            if isinstance(content, list):
                text_parts = [
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict)
                ]
                content = "\n".join(text_parts)

            label = f"{mask} ({actor})" if actor else mask
            lines.append(f"{label}: {content}")

    return "\n\n".join(lines)


def _build_messages(
    alias: str,
    system: str | None,
    mask: str | None,
    prior_transcript: list[dict],
    beat_content: str,
) -> list[dict]:
    """Build [system, user] message pair for an actor."""
    # Priority: explicit system > mask concetto > generic fallback
    sys_prompt = system
    if not sys_prompt and mask:
        sys_prompt = load_mask_concetto(mask)
    if not sys_prompt:
        sys_prompt = f"You are participating in a multi-model conversation as {mask or alias}."

    # Constrain actor to their own voice only
    voice_name = mask or alias
    sys_prompt += (
        f"\n\nIMPORTANT: You are {voice_name}. Respond ONLY as {voice_name}. "
        f"Do NOT write dialogue for other characters. One voice only — yours."
    )
    messages = [{"role": "system", "content": sys_prompt}]

    parts = []
    if prior_transcript:
        script = format_script(prior_transcript)
        if script:
            parts.append(script)

    parts.append(f"[{beat_content}]")
    voice_name = mask or alias
    parts.append(f"Now respond as {voice_name} only.")

    messages.append({"role": "user", "content": "\n\n".join(parts)})
    return messages


# ---------------------------------------------------------------------------
# Model calling
# ---------------------------------------------------------------------------


async def _call_api(
    litellm_model: str,
    messages: list[dict],
) -> dict:
    """Call a model via litellm API. Returns {content, model, latency_ms, status, error}."""
    t0 = time.monotonic()
    try:
        response = await litellm.acompletion(
            model=litellm_model,
            messages=messages,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        content = response.choices[0].message.content or ""
        return {
            "content": content,
            "model": response.model or litellm_model,
            "latency_ms": latency_ms,
            "status": "success",
            "error": None,
        }
    except Exception as e:
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.error(f"[STAGE] API model {litellm_model} failed: {e}")
        return {
            "content": "",
            "model": litellm_model,
            "latency_ms": latency_ms,
            "status": "error",
            "error": str(e)[:500],
        }


async def _call_cli(
    model_name: str,
    cli_config: dict,
    prompt: str,
) -> dict:
    """Call a CLI model via subprocess. Returns {content, model, latency_ms, status, error}."""
    import shutil

    command = cli_config["command"]
    args = cli_config.get("args", [])
    parser = cli_config.get("parser", "text")

    if not shutil.which(command):
        return {
            "content": "",
            "model": model_name,
            "latency_ms": 0,
            "status": "error",
            "error": f"CLI command '{command}' not found in PATH",
        }

    t0 = time.monotonic()
    try:
        # Strip CLAUDECODE env var so nested claude-cli sessions aren't blocked
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        process = await asyncio.create_subprocess_exec(
            command, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(input=prompt.encode("utf-8")),
            timeout=300,  # 5 min timeout for CLI actors
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        if process.returncode != 0:
            stderr = stderr_bytes.decode("utf-8", errors="replace")[:500]
            return {
                "content": "",
                "model": model_name,
                "latency_ms": latency_ms,
                "status": "error",
                "error": f"CLI exit code {process.returncode}: {stderr}",
            }

        stdout = stdout_bytes.decode("utf-8", errors="replace")

        # Parse output based on format
        if parser == "json":
            try:
                data = json.loads(stdout)
                # claude-cli: {"type":"result","result":"..."}
                # gemini-cli: {"response":"..."}
                # generic: content, message, text
                content = (data.get("result") or data.get("response")
                           or data.get("content") or data.get("message")
                           or data.get("text") or stdout)
            except json.JSONDecodeError:
                content = stdout
        elif parser == "jsonl":
            # codex-cli: JSONL with item.completed entries containing item.text
            # Collect all agent_message texts in order
            texts = []
            for line in stdout.strip().splitlines():
                try:
                    data = json.loads(line)
                    if data.get("type") == "item.completed":
                        item = data.get("item", {})
                        if item.get("type") == "agent_message" and item.get("text"):
                            texts.append(item["text"])
                except json.JSONDecodeError:
                    continue
            content = "\n\n".join(texts) if texts else stdout
        else:
            content = stdout

        return {
            "content": content.strip(),
            "model": model_name,
            "latency_ms": latency_ms,
            "status": "success",
            "error": None,
        }

    except TimeoutError:
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.error(f"[STAGE] CLI {model_name} timed out")
        return {
            "content": "",
            "model": model_name,
            "latency_ms": latency_ms,
            "status": "error",
            "error": f"CLI '{command}' timed out after 300s",
        }
    except Exception as e:
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.error(f"[STAGE] CLI {model_name} failed: {e}")
        return {
            "content": "",
            "model": model_name,
            "latency_ms": latency_ms,
            "status": "error",
            "error": str(e)[:500],
        }


async def call_actor_model(
    model_name: str,
    messages: list[dict],
) -> dict:
    """Route to API or CLI based on model name."""
    if model_name in CLI_MODELS:
        # CLI models get the full prompt as a single string
        # Combine system + user messages into one prompt
        parts = []
        for msg in messages:
            if msg["role"] == "system":
                parts.append(f"System: {msg['content']}")
            else:
                parts.append(msg["content"])
        prompt = "\n\n".join(parts)
        return await _call_cli(model_name, CLI_MODELS[model_name], prompt)
    else:
        litellm_model = resolve_model(model_name)
        return await _call_api(litellm_model, messages)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def run_beat(
    transcript_path: str,
    beat: dict,
) -> dict:
    """Orchestrate one beat of a multi-actor conversation.

    Args:
        transcript_path: Path to JSONL transcript file.
        beat: Beat config with keys:
            content (str): The prompt/direction.
            cast (dict): Map of alias → {model, mask?, system?}.
            scenario (str?): Scenario name.
            act (int?): Act number.

    Returns:
        Summary dict with transcript_path, session_id, beat_number,
        responses, transcript_lines, status, summary.
    """
    content = beat["content"]
    cast = beat["cast"]
    scenario = beat.get("scenario")
    act = beat.get("act")

    # Read existing transcript
    prior_transcript = read_transcript(transcript_path)
    session_id = _get_session_id(prior_transcript)
    beat_number = _count_beats(prior_transcript) + 1

    logger.info(f"[STAGE] Beat {beat_number} — {len(cast)} actors — {transcript_path}")

    # Append beat entry
    now = datetime.now(timezone.utc).isoformat()
    beat_entry = {
        "type": "user",
        "timestamp": now,
        "sessionId": session_id,
        "message": {"role": "user", "content": content},
        "chorus": {
            "entryType": "beat",
            "scenario": scenario,
            "act": act,
            "cast": {alias: {k: v for k, v in cfg.items() if v is not None}
                     for alias, cfg in cast.items()},
        },
    }
    append_transcript(transcript_path, [beat_entry])

    # Call each actor in parallel
    async def call_actor(alias: str, cfg: dict):
        model_name = cfg.get("model", alias)
        mask = cfg.get("mask")
        system = cfg.get("system")

        messages = _build_messages(alias, system, mask, prior_transcript, content)
        result = await call_actor_model(model_name, messages)
        return alias, cfg, result

    tasks = [call_actor(alias, cfg) for alias, cfg in cast.items()]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results
    actor_results = []
    response_entries = []

    for result in results:
        if isinstance(result, BaseException):
            logger.error(f"[STAGE] Actor exception: {result}")
            continue

        alias, cfg, model_result = result
        resp_time = datetime.now(timezone.utc).isoformat()
        content_text = model_result["content"]

        response_entry = {
            "type": "assistant",
            "timestamp": resp_time,
            "sessionId": session_id,
            "message": {
                "role": "assistant",
                "model": model_result["model"],
                "content": [{"type": "text", "text": content_text}],
            },
            "chorus": {
                "entryType": "response",
                "actor": alias,
                "mask": cfg.get("mask"),
                "latency_ms": model_result["latency_ms"],
                "status": model_result["status"],
                **({"error": model_result["error"]} if model_result["error"] else {}),
            },
        }
        response_entries.append(response_entry)

        actor_results.append({
            "actor": alias,
            "mask": cfg.get("mask"),
            "model": model_result["model"],
            "status": model_result["status"],
            "preview": content_text[:200] if content_text else "",
            "latency_ms": model_result["latency_ms"],
            "error": model_result["error"],
        })

    # Append all responses
    append_transcript(transcript_path, response_entries)

    total_lines = len(prior_transcript) + 1 + len(response_entries)
    successes = sum(1 for r in actor_results if r["status"] == "success")
    total = len(actor_results)
    overall_status = "success" if successes == total else ("partial" if successes > 0 else "error")

    summary = {
        "transcript_path": transcript_path,
        "session_id": session_id,
        "beat_number": beat_number,
        "responses": actor_results,
        "transcript_lines": total_lines,
        "status": overall_status,
        "summary": f"Beat {beat_number}: {successes}/{total} actors responded successfully",
    }

    logger.info(f"[STAGE] {summary['summary']}")
    return summary


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main():
    """CLI: python chorus/stage.py beat <transcript_path> '<beat_json>'"""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if len(sys.argv) < 4 or sys.argv[1] != "beat":
        print("Usage: python chorus/stage.py beat <transcript.jsonl> '<beat_json>'")
        print('  beat_json: {"content": "...", "cast": {"alias": {"model": "...", "system": "..."}}}')
        sys.exit(1)

    transcript_path = sys.argv[2]
    beat = json.loads(sys.argv[3])

    result = asyncio.run(run_beat(transcript_path, beat))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
