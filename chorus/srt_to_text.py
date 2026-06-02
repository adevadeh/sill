#!/usr/bin/env python3
"""Convert SRT subtitle file to clean text, deduplicating overlapping lines."""

import re
import sys
from pathlib import Path


def srt_to_text(srt_path: str) -> str:
    """Strip SRT timing/numbering, deduplicate overlapping subtitle fragments."""
    text = Path(srt_path).read_text()
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Skip sequence numbers
        if re.match(r"^\d+$", stripped):
            continue
        # Skip timing lines
        if re.match(r"\d{2}:\d{2}:\d{2}", stripped):
            continue
        lines.append(stripped)

    # Deduplicate overlapping subtitle fragments
    # SRT subtitles often overlap: line N ends with words that line N+1 starts with
    clean = []
    for line in lines:
        if not clean:
            clean.append(line)
            continue
        # Check if current line overlaps with end of previous
        prev = clean[-1]
        # Try progressively longer overlaps
        merged = False
        for overlap_len in range(min(len(line), len(prev)), 5, -1):
            if prev.endswith(line[:overlap_len]):
                clean[-1] = prev + line[overlap_len:]
                merged = True
                break
        if not merged:
            clean.append(line)

    return " ".join(clean)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python srt_to_text.py <input.srt> [output.txt]")
        sys.exit(1)

    srt_path = sys.argv[1]
    text = srt_to_text(srt_path)

    if len(sys.argv) >= 3:
        Path(sys.argv[2]).write_text(text)
        print(f"Wrote {len(text.split())} words to {sys.argv[2]}")
    else:
        print(text)
