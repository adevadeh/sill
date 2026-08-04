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
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(os.environ.get("SILL_PROJECT_ROOT", os.getcwd()))
LOG_FILE = Path(os.environ.get("SILL_LOG_DIR", "/tmp")) / "state-language-check.log"

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


def extract_relevant_patch_additions(patch: str) -> str | None:
    """Return added text from docs/ or journals/ files in an apply_patch payload."""
    additions: list[str] = []
    relevant = False

    for line in patch.splitlines():
        if line.startswith("*** Add File: ") or line.startswith("*** Update File: "):
            relevant = is_relevant_path(line.split(": ", 1)[1].strip())
            continue
        if line.startswith("*** Delete File: ") or line.startswith("*** End of File"):
            relevant = False
            continue
        if relevant and line.startswith("+"):
            additions.append(line[1:])

    return "\n".join(additions) if additions else None


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

    if tool_name == "Write":
        if not is_relevant_path(tool_input.get("file_path")):
            return None
        c = tool_input.get("content")
        return c if isinstance(c, str) else None

    if tool_name == "Edit":
        if not is_relevant_path(tool_input.get("file_path")):
            return None
        # Only scan the new_string — it's what's being introduced
        c = tool_input.get("new_string")
        return c if isinstance(c, str) else None

    if tool_name == "apply_patch":
        c = tool_input.get("command")
        if isinstance(c, str):
            return extract_relevant_patch_additions(c)
        return None

    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        if not isinstance(cmd, str) or "sill.py notice" not in cmd:
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
