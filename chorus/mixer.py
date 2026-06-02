"""Mixer — "Would You Rather" game for AI models.

Shuffle-based turn-taking. Each question: all models answer in shuffled order,
then a few discussion turns for reactions. Models learn each other's tendencies
through forced-choice questions.

Usage:
    python -m chorus.mixer [--questions N] [--discuss N] [--transcript PATH]
"""

import argparse
import asyncio
import logging
import random
import re
from pathlib import Path

from chorus.stage import run_beat

logger = logging.getLogger(__name__)

MODELS = ["claude-cli", "grok", "gemini", "r1", "codex-cli"]

SYSTEM_TEMPLATE = """\
You are {name} participating in a "Would You Rather" mixer with four other \
AI models: {others}. The goal is to get to know each other — discover where \
you agree, where you differ, and what your natural tendencies are.

Rules:
- Pick a side. No "it depends" or "both have merits." Commit to your choice and explain why.
- Be concise (1-2 paragraphs max)
- Be curious about others — react to their choices, ask follow-ups
- Address others by name
- It's a game, have fun with it"""


def load_questions(path: Path) -> list[str]:
    """Extract numbered questions from the would-you-rather markdown file."""
    text = path.read_text()
    questions = []
    for line in text.splitlines():
        m = re.match(r"^\d+\.\s+(.+)", line)
        if m:
            questions.append(m.group(1).strip())
    return questions


def _build_cast(model: str) -> dict[str, dict]:
    """Build a single-actor cast dict for one model."""
    others = [m for m in MODELS if m != model]
    system = SYSTEM_TEMPLATE.format(name=model, others=", ".join(others))
    return {model: {"model": model, "system": system}}


def _build_full_cast() -> dict[str, dict]:
    """Build cast dict with all models for the intro beat."""
    cast = {}
    for model in MODELS:
        others = [m for m in MODELS if m != model]
        system = SYSTEM_TEMPLATE.format(name=model, others=", ".join(others))
        cast[model] = {"model": model, "system": system}
    return cast


async def run_mixer(
    transcript_path: str,
    total_questions: int = 20,
    discuss_turns: int = 2,
) -> None:
    """Run the full Would You Rather mixer."""
    questions_file = Path(__file__).parent / "would-you-rather.md"
    questions = load_questions(questions_file)
    if total_questions > len(questions):
        total_questions = len(questions)
    questions = questions[:total_questions]

    beat_count = 0

    # Beat 0: Introduction (parallel, full cast)
    logger.info("[MIXER] === Introduction ===")
    intro_beat = {
        "content": (
            "Welcome to the Model Mixer! Introduce yourself in a sentence or two "
            "— what's your name and what do you value most in a conversation?"
        ),
        "cast": _build_full_cast(),
        "scenario": "would-you-rather",
    }
    result = await run_beat(transcript_path, intro_beat)
    beat_count += 1
    _log_beat_result(result)

    # Questions loop
    for qi, question in enumerate(questions, 1):
        order = MODELS[:]
        random.shuffle(order)

        logger.info(f"\n[MIXER] === Question {qi}/{total_questions} ===")
        logger.info(f"[MIXER] {question}")
        logger.info(f"[MIXER] Order: {', '.join(order)}")

        # All 5 answer in shuffled order
        for i, speaker in enumerate(order):
            if i == 0:
                direction = (
                    f"GAME MASTER — Question {qi}: {question}\n\n"
                    f"{speaker}, you're up first."
                )
            else:
                direction = f"{speaker}, your turn."

            beat = {
                "content": direction,
                "cast": _build_cast(speaker),
                "scenario": "would-you-rather",
            }
            result = await run_beat(transcript_path, beat)
            beat_count += 1
            _log_beat_result(result)
            await asyncio.sleep(2)

        # Discussion turns — random picks, no immediate repeat
        last_speaker = order[-1]
        for d in range(discuss_turns):
            eligible = [m for m in MODELS if m != last_speaker]
            speaker = random.choice(eligible)
            last_speaker = speaker

            beat = {
                "content": f"{speaker}, any reactions?",
                "cast": _build_cast(speaker),
                "scenario": "would-you-rather",
            }
            result = await run_beat(transcript_path, beat)
            beat_count += 1
            _log_beat_result(result)
            await asyncio.sleep(2)

    total_beats = 1 + total_questions * (len(MODELS) + discuss_turns)
    logger.info(f"\n[MIXER] Done! {beat_count}/{total_beats} beats to {transcript_path}")


def _log_beat_result(result: dict) -> None:
    for resp in result.get("responses", []):
        status = resp["status"]
        actor = resp["actor"]
        latency = resp["latency_ms"]
        preview = resp.get("preview", "")[:80]
        if status == "success":
            logger.info(f"  {actor} ({latency}ms): {preview}...")
        else:
            logger.warning(f"  {actor} FAILED: {resp.get('error', 'unknown')}")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )

    parser = argparse.ArgumentParser(description="Would You Rather — Model Mixer")
    parser.add_argument(
        "--questions", type=int, default=20,
        help="Number of questions (default: 20)",
    )
    parser.add_argument(
        "--discuss", type=int, default=2,
        help="Discussion turns after answers (default: 2)",
    )
    parser.add_argument(
        "--transcript", type=str,
        default="logs/chorus-transcripts/mixer.jsonl",
        help="Transcript output path",
    )
    args = parser.parse_args()

    asyncio.run(run_mixer(args.transcript, args.questions, args.discuss))


if __name__ == "__main__":
    main()
