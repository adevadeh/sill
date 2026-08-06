#!/usr/bin/env python3
"""
Attribution-check hook.

Fires PreToolUse on memory-storage tool calls (mcp__sill__remember,
mcp__sill__remember_batch, Bash-with-sill.py-notice). Detects two drift
classes:

  F1. Beat-number citations. Checks that "beat N" references match an
      existing session-log file, and translates numbers in the legacy
      drift range via docs/gnomon-sessions/NUMBERING.md.

  F2. Authorship / attribution patterns (you wrote, Sili said, William
      said, your quote, I concluded, etc.). Non-verifying — flags for
      manual check before storage.

Non-blocking. Emits systemMessage for the operator's TUI and logs every fire
to /tmp/attribution-check.log.

The Bash/sill.py-notice branch of extract_content() fires on both harnesses
via _harness.tool_kind/shell_command: Claude's Bash and Codex's exec/
exec_command/shell/shell_command all normalize to "shell" kind, and each
tool's own command key is read there (see _harness.py). The MCP
branches (remember/remember_batch) already worked on both harnesses before
this — hook payloads flatten an MCP tool name to "mcp__server__tool" on
both Claude Code and Codex, and is_tool_name() matches by suffix. Fails
open — exits 0, no output — if _harness itself cannot be imported.
"""
import importlib.util
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# SILL_PROJECT_ROOT = where the agent's session logs / docs live (caller project).
# SILL_BEAT_SESSIONS_DIR = optional override for the session log directory used by F1.
# When neither is set or the directory is missing, F1 still catches citations
# but cannot verify against on-disk files.
REPO = Path(os.environ.get("SILL_PROJECT_ROOT", os.getcwd()))
SESSIONS_DIR = Path(
    os.environ.get("SILL_BEAT_SESSIONS_DIR", str(REPO / "docs" / "gnomon-sessions"))
)
NUMBERING_MD = SESSIONS_DIR / "NUMBERING.md"
LOG_FILE = Path(os.environ.get("SILL_LOG_DIR", "/tmp")) / "attribution-check.log"


def _load_harness():
    try:
        spec = importlib.util.spec_from_file_location(
            "_sill_harness", Path(__file__).resolve().parent / "_harness.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


_harness = _load_harness()

BEAT_RE = re.compile(r"\bbeat[s]?\s*(\d{1,3})\b", re.IGNORECASE)

F2_PATTERNS = [
    (r"\bSili\s+(wrote|said|claimed|concluded|authored|stated|observed)\b", "Sili-attribution"),
    (r"\bWilliam\s+(wrote|said|claimed|concluded|authored|stated|mentioned|noted|asked)\b", "William-attribution"),
    (r"\byou\s+wrote\b", "you-wrote"),
    (r"\byou\s+said\b", "you-said"),
    (r"\byour\s+quote\b", "your-quote"),
    (r"\bI\s+concluded\b", "I-concluded"),
    (r"\bI\s+wrote\b", "I-wrote"),
    (r"\bquoted\s+William\b", "quoted-William"),
]


def log(msg: str) -> None:
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"{datetime.now().isoformat()} | {msg}\n")
    except Exception:
        pass


def load_drift_map() -> dict[str, str]:
    """Return {content-claimed-N-as-str: filename-NNN-as-str} for legacy drift range."""
    drift: dict[str, str] = {}
    if not NUMBERING_MD.exists():
        return drift
    try:
        text = NUMBERING_MD.read_text()
    except Exception:
        return drift
    for m in re.finditer(r"\|\s*beat-(\d{3})\s*\|\s*beat\s*(\d{1,3})", text):
        filename_n = m.group(1)
        content_n = m.group(2)
        if filename_n != content_n.zfill(3):
            drift[content_n] = filename_n
    return drift


def check_beat_references(content: str, drift_map: dict) -> list[str]:
    warnings: list[str] = []
    seen: set[str] = set()
    for m in BEAT_RE.finditer(content):
        n = m.group(1)
        if n in seen:
            continue
        seen.add(n)
        padded = n.zfill(3)
        matches = list(SESSIONS_DIR.glob(f"beat-{padded}-*.md"))
        if n in drift_map:
            warnings.append(
                f"'beat {n}' is in legacy drift range — resolves to filename "
                f"beat-{drift_map[n]}-*.md (per NUMBERING.md). "
                f"Filename beat-{padded}-*.md has different content."
            )
        elif not matches:
            warnings.append(
                f"'beat {n}' cited but no file beat-{padded}-*.md exists. "
                f"Verify before claiming this beat produced something."
            )
    return warnings


def check_authorship(content: str) -> list[tuple[str, str, str]]:
    findings: list[tuple[str, str, str]] = []
    for pattern, name in F2_PATTERNS:
        for m in re.finditer(pattern, content, re.IGNORECASE):
            start = max(0, m.start() - 40)
            end = min(len(content), m.end() + 40)
            context = content[start:end].replace("\n", " ")
            if start > 0:
                context = "…" + context
            if end < len(content):
                context = context + "…"
            findings.append((name, m.group(0), context))
    return findings


def is_tool_name(tool_name: str, *suffixes: str) -> bool:
    """Match Claude and Codex MCP tool names by their stable final segment."""
    normalized = tool_name.replace("-", "_")
    return any(normalized == suffix or normalized.endswith(f"__{suffix}") for suffix in suffixes)


def extract_content(data: dict) -> str | None:
    """Pull the memory-content string from tool input, based on tool_name."""
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    if is_tool_name(tool_name, "remember"):
        c = tool_input.get("content")
        return c if isinstance(c, str) else None

    if is_tool_name(tool_name, "remember_batch", "remember_batch_raw"):
        mems = tool_input.get("memories", [])
        if isinstance(mems, list):
            parts = [m.get("content", "") for m in mems if isinstance(m, dict)]
            return "\n\n---\n\n".join(p for p in parts if p)
        return None

    if _harness.tool_kind(data) == "shell":
        cmd = _harness.shell_command(data) or ""
        if "sill.py notice" not in cmd:
            return None
        # Try double-quoted first, then single-quoted, then heredoc-ish fallback
        q = re.search(r"sill\.py\s+notice\s+\"([^\"]+)\"", cmd, re.DOTALL)
        if q:
            return q.group(1)
        q = re.search(r"sill\.py\s+notice\s+'([^']+)'", cmd, re.DOTALL)
        if q:
            return q.group(1)
        idx = cmd.find("sill.py notice")
        tail = cmd[idx + len("sill.py notice"):].strip()
        # Cut at common option markers to avoid pulling in --concepts or --importance
        for opt in ("--concepts", "--importance", "--type"):
            i = tail.find(opt)
            if i >= 0:
                tail = tail[:i].strip()
        return tail or None

    return None


def main() -> None:
    if _harness is None:
        sys.exit(0)
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)
    if not isinstance(data, dict):
        # Valid JSON that isn't an object (e.g. a bare array or string) is
        # exactly as unusable as invalid JSON — extract_content()'s first
        # call is data.get(...), which raises AttributeError on a list.
        sys.exit(0)

    content = extract_content(data)
    if not content or len(content) < 20:
        sys.exit(0)

    drift_map = load_drift_map()
    beat_warnings = check_beat_references(content, drift_map)
    authorship = check_authorship(content)

    if not beat_warnings and not authorship:
        sys.exit(0)

    tool_name = data.get("tool_name", "unknown")
    log(f"FIRED on {tool_name}: {len(beat_warnings)} beat, {len(authorship)} attribution")

    parts: list[str] = []
    if beat_warnings:
        parts.append("**Beat-reference warnings:**\n" + "\n".join(f"- {w}" for w in beat_warnings))
    if authorship:
        lines = [f"- [{n}] \"{p}\" — …{c}…" for n, p, c in authorship]
        parts.append(
            "**Attribution claims (verify against the record before storing):**\n"
            + "\n".join(lines)
        )

    tui_bits: list[str] = []
    if beat_warnings:
        tui_bits.append(f"{len(beat_warnings)} beat-ref warning(s)")
    if authorship:
        tui_bits.append(f"{len(authorship)} attribution claim(s)")

    output = {
        "systemMessage": "[attribution-check] " + " | ".join(tui_bits),
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "[ATTRIBUTION CHECK]\n\n" + "\n\n".join(parts)
                + "\n\nNon-blocking — verify as needed, then proceed with the store.",
        },
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
