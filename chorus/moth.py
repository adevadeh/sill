#!/usr/bin/env python3
"""Moth — three-beat research pipeline for source processing.

Reads a source text, chunks it, and runs each chunk through:
  Beat 1: claude-cli reads cold → Source Cards + synthesis
  Beat 2: gemini (Questioner) + r1 (Skeptic) challenge in parallel
  Beat 3: claude-cli revises based on both challenges

Final output: revised Source Cards ready for memory storage.

Usage:
    python chorus/moth.py <source_file> [--label "Source Label"] [--chunk-words 900]
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chorus.stage import call_actor_model
from cognitive_memory_api import CognitiveMemory, MemoryType

# ---------------------------------------------------------------------------
# Prompts — refined through 20 dry-run configs (batches 1-2, pipeline, trio)
# ---------------------------------------------------------------------------

BEAT1_SYSTEM = """\
You are the lead researcher processing source material for a long-term memory system. Your job:

1. Read the passage carefully and identify the 2-4 most significant insights.
2. For each insight, produce a Source Card in EXACTLY this format:
   **Quote:** [verbatim from passage, 1-3 sentences]
   **Claim:** [precise statement in your words]
   **Why it matters:** [significance — what does this change or challenge?]
   **Discriminator:** [what evidence would falsify or revise this?]
   **Concepts:** [comma-separated tags]

3. Write a brief synthesis (2-3 paragraphs): What is the author's central argument? What's genuinely novel? What questions does it raise?

Be honest about what the source actually establishes vs. what it merely gestures at.

IMPORTANT: Do NOT call any tools or store any memories. Only produce text output."""

SKEPTIC_SYSTEM = """\
You are a Skeptic reviewing a researcher's analysis. Your job: find what's wrong, missing, or unexamined.

For each Source Card: Is the claim actually supported by the quote? Is the discriminator real or hand-waving? Did the researcher miss the weakest link?

For the synthesis: Where did the researcher assume without justifying? What's the strongest objection they didn't consider?

Two disciplines: (1) Abduction — the real failure point may be upstream. (2) Grounding — quote the researcher's exact words when challenging them."""

QUESTIONER_SYSTEM = """\
You are a Questioner reviewing a researcher's analysis. Your style: probing, not aggressive — relentless yet unvindictive.

For each Source Card: What question does this insight raise that the passage doesn't answer? What would you need to know next?

For the synthesis: Where does the researcher's reading stop short? What thread should they pull further?

Discipline: Inhibition — each question should count. Don't scatter buckshot; place each question where it will open the most. Aim for 5-7 sharp questions, not 15 vague ones."""

BEAT3_SYSTEM = """\
You are the lead researcher revising your analysis after receiving two challenges: one from a Skeptic (finding flaws) and one from a Questioner (opening new directions).

Review both challenges. Then output your revised Source Cards in EXACTLY this format (one per surviving insight):

**Quote:** [verbatim from passage — keep, revise, or replace based on challenges]
**Claim:** [revised claim incorporating valid critiques]
**Why it matters:** [updated significance]
**Discriminator:** [sharpened — what would falsify this?]
**Concepts:** [comma-separated tags]

Rules for revision:
- Fix flaws the Skeptic identified
- Cut cards that don't survive scrutiny (note: "CUT — [reason]" in place of the card)
- Add new cards from threads the Questioner opened
- Sharpen discriminators based on both challenges

After all cards, write a brief note: what changed? What did each challenger contribute? Which was more productive and why?

IMPORTANT: Do NOT call any tools or store any memories. Only produce text output."""

# ---------------------------------------------------------------------------
# Cast — discovered via trio experiments T1-T4
# ---------------------------------------------------------------------------

LEAD = "claude-cli"
SKEPTIC_MODEL = "r1"
QUESTIONER_MODEL = "gemini"

# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def chunk_text(text: str, target_words: int = 900) -> list[str]:
    """Split text into chunks of roughly target_words, breaking at sentence boundaries."""
    sentences = []
    current = []
    for char in text:
        current.append(char)
        if char in ".?!" and len(current) > 20:
            sentences.append("".join(current).strip())
            current = []
    if current:
        remainder = "".join(current).strip()
        if remainder:
            sentences.append(remainder)

    chunks = []
    current_chunk = []
    current_words = 0

    for sentence in sentences:
        words = len(sentence.split())
        if current_words + words > target_words and current_chunk:
            chunks.append(" ".join(current_chunk))
            current_chunk = []
            current_words = 0
        current_chunk.append(sentence)
        current_words += words

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


async def run_chunk(chunk_num: int, chunk: str, source_label: str) -> dict:
    """Run three-beat pipeline on one chunk."""
    header = f"Source: {source_label} (chunk {chunk_num})"

    # Beat 1: claude-cli reads cold
    print(f"  Chunk {chunk_num} Beat 1: {LEAD} reading...", flush=True)
    t0 = time.time()
    beat1_msgs = [
        {"role": "system", "content": BEAT1_SYSTEM},
        {"role": "user", "content": f"{header}\n\n---\n\n{chunk}"},
    ]
    beat1 = await call_actor_model(LEAD, beat1_msgs)
    beat1_time = time.time() - t0

    if beat1["status"] != "success":
        return {"chunk_num": chunk_num, "error": f"Beat 1 failed: {beat1.get('error', '?')}"}

    beat1_content = beat1["content"]
    print(f"    Beat 1 done ({beat1_time:.0f}s, {len(beat1_content)} chars)", flush=True)

    # Beat 2: Skeptic + Questioner in parallel
    source_context = f"{header}\n\n---\n\n{chunk}\n\n---\n\nRESEARCHER'S ANALYSIS:\n\n{beat1_content}"

    async def run_challenger(model, system, role_name):
        msgs = [
            {"role": "system", "content": system},
            {"role": "user", "content": source_context},
        ]
        t = time.time()
        result = await call_actor_model(model, msgs)
        elapsed = time.time() - t
        content = result["content"] if result["status"] == "success" else f"ERROR: {result.get('error', '?')}"
        return role_name, model, content, elapsed

    print(f"  Chunk {chunk_num} Beat 2: {SKEPTIC_MODEL} (Skeptic) + {QUESTIONER_MODEL} (Questioner)...", flush=True)
    t0 = time.time()
    skeptic_task = run_challenger(SKEPTIC_MODEL, SKEPTIC_SYSTEM, "Skeptic")
    questioner_task = run_challenger(QUESTIONER_MODEL, QUESTIONER_SYSTEM, "Questioner")
    results = await asyncio.gather(skeptic_task, questioner_task, return_exceptions=True)
    beat2_time = time.time() - t0

    beat2_parts = {}
    beat2_lines = []
    for r in results:
        if isinstance(r, BaseException):
            beat2_lines.append(f"ERROR: {r}")
            continue
        role_name, model, content, elapsed = r
        beat2_parts[role_name] = content
        beat2_lines.append(f"--- {role_name}: {model} ({elapsed:.0f}s) ---")
        beat2_lines.append(content)
        beat2_lines.append("")

    print(f"    Beat 2 done ({beat2_time:.0f}s parallel)", flush=True)

    # Beat 3: claude-cli revises with both challenges
    print(f"  Chunk {chunk_num} Beat 3: {LEAD} revising...", flush=True)
    t0 = time.time()
    beat3_msgs = [
        {"role": "system", "content": BEAT3_SYSTEM},
        {"role": "user", "content": (
            f"{header}\n\n---\n\n{chunk}\n\n---\n\n"
            f"YOUR INITIAL ANALYSIS:\n\n{beat1_content}\n\n---\n\n"
            f"SKEPTIC CHALLENGE ({SKEPTIC_MODEL}):\n\n{beat2_parts.get('Skeptic', '(none)')}\n\n---\n\n"
            f"QUESTIONER CHALLENGE ({QUESTIONER_MODEL}):\n\n{beat2_parts.get('Questioner', '(none)')}"
        )},
    ]
    beat3 = await call_actor_model(LEAD, beat3_msgs)
    beat3_time = time.time() - t0
    beat3_content = beat3["content"] if beat3["status"] == "success" else f"ERROR: {beat3.get('error', '?')}"
    print(f"    Beat 3 done ({beat3_time:.0f}s)", flush=True)

    total = beat1_time + beat2_time + beat3_time
    print(f"  Chunk {chunk_num} complete: {total:.0f}s total", flush=True)

    return {
        "chunk_num": chunk_num,
        "chunk_words": len(chunk.split()),
        "beat1_content": beat1_content,
        "beat1_time": beat1_time,
        "beat2_lines": beat2_lines,
        "beat2_time": beat2_time,
        "beat3_content": beat3_content,
        "beat3_time": beat3_time,
        "total_time": total,
    }


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def format_results(source_label: str, chunks: list[str], results: list[dict]) -> str:
    """Format all results into readable text."""
    lines = [
        f"CHORUS MOTH — {datetime.now().isoformat()}",
        f"Source: {source_label}",
        f"Total words: ~{sum(len(c.split()) for c in chunks)}",
        f"Chunks: {len(chunks)}",
        f"Pipeline: {LEAD} (lead) + {QUESTIONER_MODEL} (Questioner) + {SKEPTIC_MODEL} (Skeptic)",
        f"Structure: Beat 1 (read cold) → Beat 2 (parallel challenges) → Beat 3 (revision)",
        "",
    ]

    for result in results:
        if "error" in result:
            lines.extend(["", "=" * 70, f"CHUNK {result['chunk_num']} — ERROR", "=" * 70, "", result["error"], ""])
            continue

        n = result["chunk_num"]
        lines.extend([
            "",
            "=" * 70,
            f"CHUNK {n} (~{result['chunk_words']} words)",
            f"Timing: Beat 1 {result['beat1_time']:.0f}s | Beat 2 {result['beat2_time']:.0f}s | Beat 3 {result['beat3_time']:.0f}s | Total {result['total_time']:.0f}s",
            "=" * 70,
            "",
            "--- Beat 1: Initial Analysis ---",
            "",
            result["beat1_content"],
            "",
            "--- Beat 2: Challenges ---",
            "",
            *result["beat2_lines"],
            "",
            "--- Beat 3: Revised Analysis ---",
            "",
            result["beat3_content"],
            "",
        ])

    total_time = sum(r.get("total_time", 0) for r in results)
    lines.extend([
        "=" * 70,
        f"TOTAL TIME: {total_time:.0f}s ({total_time/60:.1f}min)",
        f"CHUNKS: {len(results)} processed",
        "=" * 70,
    ])

    return "\n".join(lines)


def _parse_cards_from_text(text: str, chunk_num: int) -> list[dict]:
    """Parse Source Cards from text using **Quote:**/**Claim:** format."""
    cards = []
    card_texts = text.split("**Quote:**")
    for i, card_text in enumerate(card_texts[1:], 1):  # skip pre-card text
        card = {"chunk": chunk_num, "card": i}
        for field in ["Claim", "Why it matters", "Discriminator", "Concepts"]:
            marker = f"**{field}:**"
            if marker in card_text:
                start = card_text.index(marker) + len(marker)
                end = len(card_text)
                for other_field in ["Claim", "Why it matters", "Discriminator", "Concepts"]:
                    other_marker = f"**{other_field}:**"
                    if other_marker in card_text[start:]:
                        candidate = start + card_text[start:].index(other_marker)
                        if candidate < end:
                            end = candidate
                card[field.lower().replace(" ", "_")] = card_text[start:end].strip()

        # Quote is everything before the first field marker
        first_marker_pos = len(card_text)
        for field in ["Claim", "Why it matters", "Discriminator", "Concepts"]:
            marker = f"**{field}:**"
            if marker in card_text:
                pos = card_text.index(marker)
                if pos < first_marker_pos:
                    first_marker_pos = pos
        card["quote"] = card_text[:first_marker_pos].strip()

        if card.get("claim"):
            cards.append(card)
    return cards


def extract_source_cards(results: list[dict]) -> list[dict]:
    """Extract source cards from results, trying Beat 3 first then Beat 1 fallback."""
    cards = []
    for result in results:
        if "error" in result:
            continue
        chunk_num = result["chunk_num"]

        # Try Beat 3 (revised analysis) first
        beat3_cards = _parse_cards_from_text(result["beat3_content"], chunk_num)
        if beat3_cards:
            cards.extend(beat3_cards)
            continue

        # Fall back to Beat 1 (initial analysis)
        beat1_cards = _parse_cards_from_text(result["beat1_content"], chunk_num)
        if beat1_cards:
            print(f"    Chunk {chunk_num}: Beat 3 format unparseable, using Beat 1 cards ({len(beat1_cards)} cards)")
            cards.extend(beat1_cards)
        else:
            print(f"    Chunk {chunk_num}: WARNING — no parseable cards from Beat 3 or Beat 1")

    return cards


# ---------------------------------------------------------------------------
# Memory storage
# ---------------------------------------------------------------------------


def _get_dsn() -> str:
    """Build Postgres DSN from env vars (same logic as agi_mcp_server)."""
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "sill")
    user = os.getenv("POSTGRES_USER", "sill")
    pw = os.getenv("POSTGRES_PASSWORD", "sill_password")
    return f"postgresql://{user}:{pw}@{host}:{port}/{db}"


async def store_cards(cards: list[dict], source_label: str, source_path: str) -> list[str]:
    """Store extracted Source Cards as semantic memories. Returns memory IDs."""
    if not cards:
        return []

    async with CognitiveMemory.connect(_get_dsn()) as mem:
        stored = []
        for card in cards:
            # Build memory content: quote + claim + discriminator
            parts = []
            if card.get("quote"):
                parts.append(f'Source Card: "{card["quote"]}"')
            if card.get("claim"):
                parts.append(f"Claim: {card['claim']}")
            if card.get("why_it_matters"):
                parts.append(f"Why it matters: {card['why_it_matters']}")
            if card.get("discriminator"):
                parts.append(f"Discriminator: {card['discriminator']}")

            content = "\n".join(parts)

            # Parse concepts from the card
            concepts = []
            if card.get("concepts"):
                raw = card["concepts"].split("\n")[0]  # first line only
                concepts = [c.strip() for c in raw.split(",") if c.strip()]

            context = {
                "source": "moth",
                "source_label": source_label,
                "library_path": source_path,
                "chunk": card.get("chunk"),
                "pipeline": f"{LEAD} + {QUESTIONER_MODEL} (Questioner) + {SKEPTIC_MODEL} (Skeptic)",
            }

            memory_id = await mem.remember(
                content,
                type=MemoryType.SEMANTIC,
                importance=0.7,
                concepts=concepts,
                context=context,
            )
            stored.append(str(memory_id))
            print(f"    Stored card {card.get('chunk')}.{card.get('card')}: {memory_id} ({len(concepts)} concepts)")

        return stored


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Moth research pipeline")
    parser.add_argument("source_file", help="Path to source text file")
    parser.add_argument("--label", default=None, help="Source label (default: filename)")
    parser.add_argument("--chunk-words", type=int, default=900, help="Target words per chunk")
    parser.add_argument("--output", default=None, help="Output path (default: chorus/moth-<name>.txt)")
    parser.add_argument("--dry-run", action="store_true", help="Skip memory storage")
    args = parser.parse_args()

    source_path = Path(args.source_file)
    if not source_path.exists():
        print(f"Source file not found: {source_path}")
        sys.exit(1)

    text = source_path.read_text().strip()
    source_label = args.label or source_path.stem
    total_words = len(text.split())

    chunks = chunk_text(text, args.chunk_words)
    print(f"CHORUS MOTH")
    print(f"Source: {source_label} (~{total_words} words)")
    print(f"Chunks: {len(chunks)} (target {args.chunk_words} words each)")
    print(f"Pipeline: {LEAD} → {QUESTIONER_MODEL} + {SKEPTIC_MODEL} → {LEAD}")
    if args.dry_run:
        print("Mode: DRY RUN (no memory storage)")
    print()

    # Process chunks sequentially (each chunk uses claude-cli which is a bottleneck)
    results = []
    for i, chunk in enumerate(chunks, 1):
        print(f"--- Chunk {i}/{len(chunks)} (~{len(chunk.split())} words) ---")
        result = await run_chunk(i, chunk, source_label)
        results.append(result)
        print()

    # Format and save full transcript
    output_text = format_results(source_label, chunks, results)

    output_path = args.output
    if not output_path:
        safe_name = source_label.lower().replace(" ", "-")[:40]
        output_path = str(Path(__file__).resolve().parent / f"moth-{safe_name}.txt")

    Path(output_path).write_text(output_text)
    print(f"Transcript: {output_path} ({len(output_text)} chars)")

    # Extract source cards
    cards = extract_source_cards(results)
    print(f"Source cards extracted: {len(cards)}")

    # Store as memories
    if not args.dry_run and cards:
        print(f"\nStoring {len(cards)} cards as semantic memories...")
        memory_ids = await store_cards(cards, source_label, str(source_path))
        print(f"Stored {len(memory_ids)} memories")
    elif args.dry_run:
        print("(dry run — skipping memory storage)")


if __name__ == "__main__":
    asyncio.run(main())
