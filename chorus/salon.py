"""Salon — autonomous multi-beat conversation with topic switching.

The quartet (Claude-CLI, Grok, Gemini, R1) take turns leading discussions.
Any player can signal they want to switch topics. When switching, the next
leader searches the library/memories for something new to discuss.

Usage:
    python chorus/salon.py --beats 80 [--transcript path.jsonl]
"""

import argparse
import asyncio
import logging
import os
import random
import re
import subprocess
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chorus.stage import run_beat, read_transcript

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/tmp/salon.log"),
    ],
)
logger = logging.getLogger(__name__)

# Suppress litellm noise
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("litellm").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

QUARTET = ["claude-cli", "grok", "gemini", "r1"]

SCENARIO = "salon"

LIBRARY_PATH = os.environ.get("SILL_CHORUS_LIBRARY_PATH", "")

# System prompt — same for all actors, role varies by beat direction
SALON_SYSTEM = """You are a participant in an ongoing intellectual salon with three other AI models. This is a real conversation — engage substantively with the topic and with each other's arguments.

Rules:
- Be concise (2-3 paragraphs max per response)
- Address other participants by name when responding to their points
- If leading a beat: introduce the topic provocatively, don't summarize
- If the conversation has gone stale or you're satisfied with the current topic, end your response with [SWITCH: one-sentence reason]
- Do NOT break the fourth wall — don't discuss the salon mechanism, the script, or your role as an AI. Just discuss the topic."""

# ---------------------------------------------------------------------------
# Library query
# ---------------------------------------------------------------------------


def query_library(query: str, n_results: int = 3) -> list[dict]:
    """Search the library. Returns list of {source, path, text} dicts."""
    try:
        result = subprocess.run(
            ["python3", "bin/query_library.py", query, str(n_results)],
            capture_output=True,
            text=True,
            cwd=LIBRARY_PATH,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning(f"Library query failed: {result.stderr[:200]}")
            return []

        entries = []
        current = None
        for line in result.stdout.splitlines():
            # New result starts with [N]
            m = re.match(r"^\[(\d+)\]\s+(.+)", line)
            if m:
                if current:
                    entries.append(current)
                current = {"source": m.group(2).strip(), "text": ""}
                continue

            if current is None:
                continue

            # Path line
            if line.strip().startswith("Path:"):
                current["path"] = line.strip().split("Path:", 1)[1].strip()
            # Chunk separator — text follows
            elif line.strip() == "---":
                continue
            # Skip metadata lines
            elif line.strip().startswith("Distance:"):
                continue
            elif line.strip().startswith("Chunk "):
                continue
            # Actual text content
            elif line.strip():
                current["text"] += line.strip() + " "

        if current:
            entries.append(current)

        return entries[:n_results]
    except Exception as e:
        logger.warning(f"Library query error: {e}")
        return []


# ---------------------------------------------------------------------------
# Topic management
# ---------------------------------------------------------------------------

SEED_QUERIES = [
    "free will determinism agency",
    "mathematical structure physical reality",
    "language thought limits of expression",
    "evolution cooperation altruism",
    "quantum measurement observer role",
    "information entropy meaning",
    "artificial intelligence understanding",
    "biological autonomy self-organization",
    "perception illusion constructed reality",
    "moral realism ethical foundations",
    "memory identity personal continuity",
    "emergence complexity reduction",
    "truth knowledge social construction",
    "beauty aesthetics mathematics",
    "time irreversibility arrow",
    "communication noise signal meaning",
    "power institutions knowledge",
    "metaphor conceptual structure",
    "play games creativity rules",
    "death finitude meaning",
    "consciousness hard problem qualia",
    "collective intelligence group mind",
    "technology tool use extension of mind",
    "narrative self story identity",
    "emotion rationality decision making",
]


def pick_topic(used_queries: list[str]) -> tuple[str, str]:
    """Pick a topic query and search the library for source material."""
    available = [q for q in SEED_QUERIES if q not in used_queries]
    if not available:
        random.shuffle(SEED_QUERIES)
        available = SEED_QUERIES

    query = random.choice(available)
    results = query_library(query, 3)

    if results and results[0].get("text", "").strip():
        best = results[0]
        text = best["text"][:600].strip()
        source = best.get("source", "unknown")
        context = f'From the library — "{source}":\n\n"{text}..."'
    else:
        # Fallback: use query as topic area without source
        context = f"Topic area: {query}"

    return query, context


def get_latest_responses(transcript: list[dict]) -> list[tuple[str, str]]:
    """Get (actor, content) pairs from the most recent beat."""
    last_beat_idx = -1
    for i, entry in enumerate(transcript):
        if entry.get("chorus", {}).get("entryType") == "beat":
            last_beat_idx = i

    if last_beat_idx < 0:
        return []

    results = []
    for entry in transcript[last_beat_idx + 1:]:
        if entry.get("chorus", {}).get("entryType") != "response":
            continue
        actor = entry["chorus"].get("actor", "unknown")
        msg = entry.get("message", {})
        content = ""
        if isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "text":
                    content += block.get("text", "")
        elif isinstance(msg.get("content"), str):
            content = msg["content"]
        results.append((actor, content))
    return results


def anyone_wants_switch(transcript: list[dict]) -> str | None:
    """Check if any actor in the latest beat wants to switch. Returns actor name or None."""
    for actor, content in get_latest_responses(transcript):
        if "[SWITCH" in content.upper():
            return actor
    return None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


async def run_salon(
    transcript_path: str,
    total_beats: int = 80,
):
    """Run the salon for the specified number of beats."""

    leader_order = list(QUARTET)
    random.shuffle(leader_order)
    leader_idx = 0
    used_queries: list[str] = []
    topic_beat_count = 0
    max_beats_per_topic = 8

    logger.info(f"=== SALON START — {total_beats} beats ===")
    logger.info(f"Leader rotation: {leader_order}")
    logger.info(f"Transcript: {transcript_path}")

    for beat_num in range(1, total_beats + 1):
        transcript = read_transcript(transcript_path)
        actual_beat = sum(
            1 for e in transcript
            if e.get("chorus", {}).get("entryType") == "beat"
        ) + 1

        # Decide: new topic or continue?
        need_switch = False
        switch_reason = ""

        if beat_num == 1:
            need_switch = True
            switch_reason = "first beat"
        elif topic_beat_count >= max_beats_per_topic:
            need_switch = True
            switch_reason = f"max beats ({max_beats_per_topic}) on topic"
        else:
            switcher = anyone_wants_switch(transcript)
            if switcher:
                need_switch = True
                switch_reason = f"{switcher} requested switch"

        # Build beat
        cast = {
            actor: {"model": actor, "system": SALON_SYSTEM}
            for actor in QUARTET
        }

        if need_switch:
            leader = leader_order[leader_idx % len(leader_order)]
            leader_idx += 1
            topic_beat_count = 1

            query, context = pick_topic(used_queries)
            used_queries.append(query)

            beat_content = (
                f"NEW TOPIC — {leader} is leading this round.\n\n"
                f"{context}\n\n"
                f"{leader}: Pick the most provocative claim or tension here "
                f"and present it to the group. Everyone else: respond directly."
            )

            logger.info(
                f"[Beat {actual_beat}] NEW TOPIC ({switch_reason}) | "
                f"leader={leader} | query='{query}'"
            )
        else:
            topic_beat_count += 1

            beat_content = (
                "Continue. Dig deeper, challenge each other, "
                "or take it somewhere unexpected."
            )

            logger.info(
                f"[Beat {actual_beat}] CONTINUE | "
                f"topic_beats={topic_beat_count}"
            )

        try:
            result = await run_beat(
                transcript_path=transcript_path,
                beat={
                    "content": beat_content,
                    "cast": cast,
                    "scenario": SCENARIO,
                    "act": actual_beat,
                },
            )

            successes = sum(1 for r in result["responses"] if r["status"] == "success")
            total = len(result["responses"])
            logger.info(
                f"[Beat {actual_beat}] {successes}/{total} succeeded | "
                f"lines={result['transcript_lines']}"
            )

            # Pause between beats
            if beat_num < total_beats:
                await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"[Beat {actual_beat}] Error: {e}")
            await asyncio.sleep(30)
            continue

    # Final stats
    logger.info(f"=== SALON COMPLETE — {total_beats} beats ===")
    transcript = read_transcript(transcript_path)
    beats = sum(1 for e in transcript if e.get("chorus", {}).get("entryType") == "beat")
    resps = sum(1 for e in transcript if e.get("chorus", {}).get("entryType") == "response")
    logger.info(f"Transcript: {len(transcript)} entries, {beats} beats, {resps} responses")


def main():
    parser = argparse.ArgumentParser(description="Run an autonomous salon conversation")
    parser.add_argument("--beats", type=int, default=80, help="Number of beats")
    parser.add_argument(
        "--transcript",
        default="logs/chorus-transcripts/salon.jsonl",
        help="Transcript path",
    )
    args = parser.parse_args()
    asyncio.run(run_salon(args.transcript, args.beats))


if __name__ == "__main__":
    main()
