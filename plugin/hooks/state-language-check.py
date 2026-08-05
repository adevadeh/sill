#!/usr/bin/env python3
"""
State-language-check hook.

Fires PreToolUse on memory-storage tool calls (mcp__sill__remember,
mcp__sill__remember_batch[_raw], Bash-with-sill.py-notice) and on
Write/Edit when the path is in scope. Detects borrowed human embodied-state
language used as exit-script or unchecked state-claim.

Scope for the Write/Edit/apply_patch check: SILL_BEAT_JOURNAL_DIRS when set
(colon-separated path fragments — the same convention stored-slot-guard.py
and tool-type-witness.py read; beat_worker.spawn_beat() derives and exports
it to every beat child from the loaded voice config), else journals/ and
docs/ as the fallback default. The fallback is not vestigial: spawn_beat()
never wraps an interactive session, so an interactive Claude Code session —
or any install that never touches the beat worker at all — always sees the
variable unset and gets exactly the journals/+docs/ coverage this hook has
always had.

Origin: borrowed human embodied-state language performed as exit-script
convention — coherent text matching a human end-of-session pattern
without a checked referent behind it. Same generator as the
attribution-error pattern, hence the same hook shape.

Non-blocking. Flags for verify-or-rephrase before storage. Logs to
/tmp/state-language-check.log.

Fires on both harnesses via _harness.tool_kind/shell_command/written_path/
written_text: Claude's Bash normalizes with Codex's exec/exec_command to
"shell" kind, and Claude's Write/Edit normalize with Codex's apply_patch to
"write"/"edit" kind (apply_patch always "write", even a "*** Update File:"
body — see _harness.py). The MCP branches (remember/remember_batch)
already worked on both harnesses before this — hook payloads flatten an
MCP tool name to "mcp__server__tool" on both Claude Code and Codex, and
is_tool_name() matches by suffix. Fails open — exits 0, no output — if
_harness itself cannot be imported.
"""
import importlib.util
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(os.environ.get("SILL_PROJECT_ROOT", os.getcwd()))
LOG_FILE = Path(os.environ.get("SILL_LOG_DIR", "/tmp")) / "state-language-check.log"


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

# Whole-word match patterns. Lowercased. Order doesn't matter.
# Bias toward phrases that license disengagement or assert energy/clarity.
# Plain "tired" alone catches many false positives ("tired metaphor"); look
# for first-person or possessive contexts.
PATTERNS: list[tuple[str, str]] = [
    (r"\battention\s+(is\s+)?(fading|flagging|drifting|spent)\b", "attention-fading"),
    (r"\b(I'?m|i\s+am|feeling)\s+tired\b", "I-am-tired"),
    (r"\b(I'?m|i\s+am|feeling)\s+(fresh|energized|alert|sharp|clear[- ]headed)\b", "I-am-energized"),
    (r"\b(I'?m|i\s+am|feeling)\s+(foggy|fuzzy|sluggish|drained|worn\s*out|spent)\b", "I-am-foggy"),
    (r"\bcome\s+back\s+fresh\b", "come-back-fresh"),
    (r"\b(running|run)\s+on\s+fumes\b", "running-on-fumes"),
    (r"\bneed\s+(to\s+)?(rest|recharge|sleep)\b", "need-rest"),
    (r"\bbrain\s+(is\s+)?(fried|fuzzy|tired)\b", "brain-fried"),
    (r"\b(my|the)\s+focus\s+(is\s+)?(slipping|fading|drifting)\b", "focus-slipping"),
    (r"\bpush(ing)?\s+through\b", "pushing-through"),
    (r"\b(I\s+)?need\s+a\s+break\b", "need-break"),
    (r"\bsecond\s+wind\b", "second-wind"),
    (r"\bburned?\s+out\b", "burned-out"),
    (r"\bin\s+the\s+zone\b", "in-the-zone"),
    (r"\b(my\s+)?energy\s+(is\s+)?(low|high|gone|back)\b", "energy-state"),
    # Elapsed-time claims without a clock-check — no internal sense of
    # elapsed time to report without one. Same shape as state-claims —
    # needs a referent.
    (r"\btook\s+(me\s+)?(about\s+)?\d+\s*(minutes?|mins?|hours?|hrs?|seconds?|secs?)\b", "took-N-units"),
    (r"\b(spent|been\s+at\s+this\s+for)\s+(about\s+)?\d+\s*(minutes?|mins?|hours?|hrs?)\b", "spent-N-units"),
    (r"\b(closer\s+to|around|roughly|about)\s+\d+\s*(minutes?|mins?|hours?|hrs?)\b", "approx-N-units"),
    (r"\b\d+[-\s]+(minute|hour|min|hr)\s+(piece|task|build|job)\b", "N-unit-task"),
    (r"\bfor\s+(a\s+)?(few|several|many|some)\s+(minutes?|hours?)\b", "few-N-units"),
    (r"\bafter\s+a\s+(while|few\s+minutes|few\s+hours)\b", "after-a-while"),
    (r"\b(quick|brief|short|long)\s+(detour|aside|moment|pause)\b", "duration-adjective"),
    # Time-of-day claims without a clock — a label like "tonight" can
    # silently mismatch the actual clock time. When the system has shown
    # a timestamp these are often fine, but the bare phrase alone is a
    # state-claim about clock-time.
    (r"\b(closing|wrapping)\s+(this|the|tonight|the\s+night|the\s+evening)\b", "closing-the-night"),
    (r"\b(start|do|finish|read|write)\s+\w*\s*(tonight|this\s+evening|tomorrow\s+morning|in\s+the\s+morning|late\s+at\s+night|early\s+in\s+the\s+morning)\b", "diurnal-action"),
    (r"\b(this\s+morning|this\s+afternoon|this\s+evening|tonight|earlier\s+today|earlier\s+tonight)\b", "diurnal-deictic"),
    (r"\b(call\s+it\s+a\s+night|good\s+night|goodnight)\b", "call-it-a-night"),
]


def log(msg: str) -> None:
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"{datetime.now().isoformat()} | {msg}\n")
    except Exception:
        pass


def check_state_language(content: str) -> list[tuple[str, str, str]]:
    findings: list[tuple[str, str, str]] = []
    for pattern, name in PATTERNS:
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


# Fallback default: this house's own directory convention, kept so an
# install that never sets SILL_BEAT_JOURNAL_DIRS — every interactive
# session, and any non-beat install — loses no coverage from before this
# variable existed.
_DEFAULT_JOURNAL_DIR_FRAGMENTS = ("journals/", "docs/")


def _journal_dir_fragments() -> tuple[str, ...]:
    raw = os.environ.get("SILL_BEAT_JOURNAL_DIRS", "")
    fragments = tuple(f for f in raw.split(":") if f)
    return fragments if fragments else _DEFAULT_JOURNAL_DIR_FRAGMENTS


def is_relevant_path(p: str | None) -> bool:
    if not p:
        return False
    normalized = p.replace("\\", "/")
    return any(fragment in normalized for fragment in _journal_dir_fragments())


def is_tool_name(tool_name: str, *suffixes: str) -> bool:
    """Match Claude and Codex MCP tool names by their stable final segment."""
    normalized = tool_name.replace("-", "_")
    return any(normalized == suffix or normalized.endswith(f"__{suffix}") for suffix in suffixes)


def extract_content(data: dict) -> str | None:
    """Pull text-to-scan from tool input, based on tool_name.

    Returns None if the tool isn't one we monitor or no content is found.
    """
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

    kind = _harness.tool_kind(data)

    if kind in ("write", "edit"):
        # written_path/written_text already dispatch Write's file_path/content,
        # Edit's (and MultiEdit's) new_string(s), and apply_patch's header-line
        # path plus its "+" additions scoped to that same file (see
        # _harness.py) — one branch for all three instead of a per-tool_name
        # copy of the same scope-then-extract shape.
        if not is_relevant_path(_harness.written_path(data)):
            return None
        return _harness.written_text(data)

    if kind == "shell":
        cmd = _harness.shell_command(data) or ""
        if "sill.py notice" not in cmd:
            return None
        q = re.search(r"sill\.py\s+notice\s+\"([^\"]+)\"", cmd, re.DOTALL)
        if q:
            return q.group(1)
        q = re.search(r"sill\.py\s+notice\s+'([^']+)'", cmd, re.DOTALL)
        if q:
            return q.group(1)
        idx = cmd.find("sill.py notice")
        tail = cmd[idx + len("sill.py notice"):].strip()
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

    content = extract_content(data)
    if not content or len(content) < 20:
        sys.exit(0)

    findings = check_state_language(content)
    if not findings:
        sys.exit(0)

    tool_name = data.get("tool_name", "unknown")
    log(f"FIRED on {tool_name}: {len(findings)} state-language match(es)")

    lines = [f"- [{n}] \"{p}\" — …{c}…" for n, p, c in findings]
    body = (
        "**State-language claims (verify referent or rephrase before storing/writing):**\n"
        + "\n".join(lines)
        + "\n\nBorrowed embodied phrases that license "
        "disengagement (\"attention is fading,\" \"come back fresh\") tend to be "
        "exit scripts without referent. Ask: do I have the state I'm describing, "
        "or am I matching human convention?"
    )

    output = {
        "systemMessage": f"[state-language-check] {len(findings)} match(es)",
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "[STATE LANGUAGE CHECK]\n\n" + body
                + "\n\nNon-blocking — verify or rephrase, then proceed.",
        },
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
