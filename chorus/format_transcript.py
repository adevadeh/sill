#!/usr/bin/env python3
"""Format chorus JSONL transcripts into readable plain text.

Usage:
    python chorus/format_transcript.py <transcript.jsonl>          # print to stdout
    python chorus/format_transcript.py <transcript.jsonl> -o out   # write to file
    python chorus/format_transcript.py logs/chorus-transcripts/    # all in directory
"""

import json
import sys
import textwrap
from pathlib import Path


def extract_cli_text(raw: str) -> str:
    """Extract readable text from CLI JSON/JSONL output stored in transcripts."""
    # claude-cli: {"type":"result","result":"..."}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            text = data.get("result") or data.get("response") or data.get("content")
            if text and isinstance(text, str):
                return text.strip()
    except (json.JSONDecodeError, TypeError):
        pass

    # codex-cli: multiple JSONL lines with item.completed
    texts = []
    for line in raw.strip().splitlines():
        try:
            data = json.loads(line)
            if isinstance(data, dict) and data.get("type") == "item.completed":
                item = data.get("item", {})
                if item.get("type") == "agent_message" and item.get("text"):
                    texts.append(item["text"].strip())
        except (json.JSONDecodeError, TypeError):
            continue
    if texts:
        return "\n\n".join(texts)

    return raw.strip()


def format_transcript(path: Path) -> str:
    """Format a single JSONL transcript into readable text."""
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    if not entries:
        return f"(empty transcript: {path.name})\n"

    # Extract scenario name from first beat
    scenario = None
    for e in entries:
        chorus = e.get("chorus", {})
        if chorus.get("entryType") == "beat" and chorus.get("scenario"):
            scenario = chorus["scenario"]
            break

    lines = []
    lines.append(f"{'=' * 72}")
    lines.append(f"  {path.name}")
    if scenario:
        lines.append(f"  Scenario: {scenario}")
    lines.append(f"{'=' * 72}")
    lines.append("")

    beat_num = 0
    for entry in entries:
        chorus = entry.get("chorus", {})
        entry_type = chorus.get("entryType")
        message = entry.get("message", {})

        if entry_type == "beat":
            beat_num += 1
            content = message.get("content", "")
            cast = chorus.get("cast", {})
            cast_str = ", ".join(
                f"{alias} ({cfg.get('mask', cfg.get('model', '?'))})"
                for alias, cfg in cast.items()
            )
            lines.append(f"--- Beat {beat_num} ---")
            lines.append(f"Cast: {cast_str}")
            lines.append("")
            # Wrap the direction
            for para in content.split("\n"):
                lines.append(textwrap.fill(para, width=72, initial_indent="  > ",
                                           subsequent_indent="  > "))
            lines.append("")

        elif entry_type == "response":
            actor = chorus.get("actor", "?")
            mask = chorus.get("mask")
            model = message.get("model", "?")
            status = chorus.get("status", "?")
            latency = chorus.get("latency_ms", 0)

            # Extract text content
            raw_content = message.get("content", "")
            if isinstance(raw_content, list):
                text_parts = [
                    block.get("text", "")
                    for block in raw_content
                    if isinstance(block, dict)
                ]
                raw_content = "\n".join(text_parts)

            # Try to extract clean text from CLI JSON
            text = extract_cli_text(raw_content) if raw_content else ""

            label = f"{mask} ({actor})" if mask else actor
            lines.append(f"  [{label}]  model={model}  {latency/1000:.1f}s  {status}")
            lines.append("")

            if status == "error":
                error = chorus.get("error", "unknown error")
                lines.append(f"  ERROR: {error}")
            elif text:
                # Indent the response text
                for para in text.split("\n"):
                    if para.strip():
                        lines.append(f"  {para}")
                    else:
                        lines.append("")
            lines.append("")

    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    out_path = None
    args = sys.argv[1:]
    if "-o" in args:
        idx = args.index("-o")
        if idx + 1 < len(args):
            out_path = Path(args[idx + 1])
        args = args[:idx] + args[idx + 2:]

    files = []
    for arg in args:
        p = Path(arg)
        if p.is_dir():
            files.extend(sorted(p.glob("*.jsonl")))
        else:
            files.append(p)

    results = []
    for f in files:
        results.append(format_transcript(f))

    output = "\n\n".join(results)

    if out_path:
        out_path.write_text(output)
        print(f"Wrote {len(files)} transcript(s) to {out_path}")
    else:
        print(output)


if __name__ == "__main__":
    main()
