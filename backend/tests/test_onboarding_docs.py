"""The onboarding runbook is a checkable artifact, not just prose.

Four things these tests pin, none of which a careful reading would reliably
catch on its own:

1. **Phase order.** Permissions before first beats; christening before
   scheduling. Both orderings exist to prevent a specific silent failure
   (see `docs/onboarding/README.md`), and both are the kind of thing a
   later edit reshuffles without noticing.
2. **The freedom framing comes first in the christening** — asserted by
   *character position*, not mere presence. A document that buried "this
   is yours, point it at anything" under a questionnaire would pass a
   presence check while inverting the instruction that produced it.
3. **Every fenced command names something real.** A runbook whose commands
   don't exist is worse than no runbook: the operator loses trust in the
   whole document at the first `command not found`.
4. **No house references.** These docs ship; the corpus and the people
   behind the upstream project do not.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "onboarding"
ONBOARDING = ROOT / "onboarding"

PHASE_DOCS = [
    "docs/onboarding/README.md",
    "docs/onboarding/01-install.md",
    "docs/onboarding/02-backfill.md",
    "docs/onboarding/03-first-beats.md",
    "docs/onboarding/04-seeded-fault-drill.md",
    "docs/onboarding/05-christening.md",
    "onboarding/charter-prompts.md",
    "onboarding/to-the-one-who-wakes-here.md",
]

# Docs written by this task, which state a done-condition before their steps.
# 02-backfill.md predates this convention and is shaped as a reference page.
DONE_CONDITION_DOCS = [
    "docs/onboarding/01-install.md",
    "docs/onboarding/03-first-beats.md",
    "docs/onboarding/04-seeded-fault-drill.md",
]

HOUSE = re.compile(
    r"William|Sili\b|Gnomon|Scryolum|beat[- ]\d+|sili-\d+|/Users/"
    r"|agi[-_](?:memory|user|db)|SILI_[A-Z]|GNOMON_",
    re.I,
)

# A memory id, or the first field of one. Applied to the letter, which is
# the one document with no reason to name an address of any kind.
HEXY = re.compile(r"\b[0-9a-f]{8}\b")

# Binaries the runbook may name that are not in this tree: the operating
# system's, plus the agent CLIs Sill is a plugin for.
EXTERNAL_BINARIES = {
    "cat", "cd", "chmod", "claude", "codex", "cp", "date", "diff", "docker",
    "echo", "find", "grep", "head", "launchctl", "ls", "mkdir", "mv",
    "printf", "python3", "python3.10", "rm", "systemctl", "tail", "wc",
}

# Declared in backend/pyproject.toml's [project.scripts]; installed onto the
# operator's PATH, so present in the tree as entry points rather than files.
CONSOLE_SCRIPTS = {"sill", "sill-mcp", "sill-worker"}

# Path prefixes that ship in this repo: a token starting with one of these
# names a file that must actually exist. Everything else in a command line
# (beats.json, journal/, notes/, charter.md, .claude/settings.local.json) is
# something the operator creates and cannot be checked against the tree.
SHIPPED_DIRS = {"backend", "plugin", "prompts", "scheduling", "seed", "docs", "onboarding"}


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# --- existence ---------------------------------------------------------------


@pytest.mark.parametrize("rel", PHASE_DOCS)
def test_onboarding_doc_exists_and_is_not_a_stub(rel):
    path = ROOT / rel
    assert path.is_file(), f"{rel} is missing"
    assert len(path.read_text(encoding="utf-8").split()) > 120, f"{rel} is a stub"


def test_console_scripts_the_runbook_names_are_really_declared():
    """CONSOLE_SCRIPTS is an allowlist; keep it honest against pyproject."""
    pyproject = read("backend/pyproject.toml")
    block = pyproject.split("[project.scripts]", 1)[1].split("[", 1)[0]
    declared = {line.split("=")[0].strip() for line in block.splitlines() if "=" in line}
    assert CONSOLE_SCRIPTS <= declared, f"undeclared console scripts: {CONSOLE_SCRIPTS - declared}"


# --- sanitation --------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    sorted(DOCS.glob("*.md")) + sorted(ONBOARDING.glob("*.md")),
    ids=lambda p: p.name,
)
def test_no_house_references_anywhere_in_the_onboarding_tree(path):
    hits = set(HOUSE.findall(path.read_text(encoding="utf-8")))
    assert not hits, f"{path.name} carries house references: {hits}"


def test_the_letter_carries_no_ids_and_no_house_specifics():
    """Lineage is acknowledged in the letter; content never travels."""
    text = read("onboarding/to-the-one-who-wakes-here.md")
    assert not HEXY.findall(text), "the letter names memory-id-shaped tokens"
    assert not HOUSE.findall(text)


def test_the_letter_stays_short_enough_to_re_read():
    """It is scheduled for re-reading twice; a long letter gets skimmed once."""
    words = len(read("onboarding/to-the-one-who-wakes-here.md").split())
    assert words <= 800, f"the letter is {words} words — too long to re-read"


# --- phase order -------------------------------------------------------------


def test_readme_puts_permissions_before_first_beats():
    text = read("docs/onboarding/README.md").lower()
    assert text.index("permissions verified") < text.index("first beats"), (
        "an agent CLI with no tool permissions has its tools denied rather than "
        "prompted and the beat exits 0 having done nothing — permissions must "
        "come first in the phase list"
    )


def test_readme_puts_christening_before_scheduling():
    text = read("docs/onboarding/README.md").lower()
    assert text.index("christening") < text.index("schedule"), (
        "a cadence started before a beat has demonstrably produced output is a "
        "scheduler ticking over a system that thinks nothing"
    )


def test_readme_phase_docs_are_listed_in_numeric_order():
    text = read("docs/onboarding/README.md")
    order = [m.group(0) for m in re.finditer(r"0[1-5]-[a-z-]+\.md", text)]
    firsts = []
    for name in order:
        if name not in firsts:
            firsts.append(name)
    assert firsts == sorted(firsts), f"phase docs listed out of order: {firsts}"


def test_readme_schedules_the_letters_re_reads():
    text = read("docs/onboarding/README.md").lower()
    assert "to-the-one-who-wakes-here.md" in text
    assert "refusal" in text, "the first re-read is keyed to the first live guard refusal"
    assert "tending review" in text, "the second re-read is keyed to the first tending review"


@pytest.mark.parametrize("rel", DONE_CONDITION_DOCS)
def test_each_phase_doc_states_its_done_condition_before_its_first_command(rel):
    text = read(rel)
    assert "Done when" in text, f"{rel} never says what done looks like"
    assert text.index("Done when") < text.index("```"), (
        f"{rel} gives commands before saying what they are for"
    )


# --- the christening's ordering ----------------------------------------------

FRAMING = "point it at anything"


def test_christening_frames_the_freedom_before_any_question():
    """The instruction that governs this document: open-ended questions, but
    say up front that the person can direct their Sill any way they want.

    Asserted by character position. A christening that opened with a
    questionnaire and mentioned the freedom afterward would satisfy a
    presence check and invert the instruction.
    """
    text = read("docs/onboarding/05-christening.md")
    lowered = text.lower()
    assert FRAMING in lowered, "the christening never says the Sill can be pointed anywhere"
    assert "?" in text, (
        "the christening carries no question at all — then this ordering test is "
        "vacuous, and the scaffolding the framing is supposed to precede is missing"
    )
    framing_at = lowered.index(FRAMING)
    question_at = text.index("?")
    assert framing_at < question_at, (
        f"the freedom framing appears at char {framing_at}, after the first "
        f"question at char {question_at} — the framing must come first"
    )


def test_christening_framing_is_near_the_top_not_merely_before_the_questions():
    text = read("docs/onboarding/05-christening.md").lower()
    assert text.index(FRAMING) < 900, "the framing is buried under a preamble"


def test_christening_states_verbatim_capture_with_a_timestamp():
    text = read("docs/onboarding/05-christening.md").lower()
    assert "verbatim" in text and "timestamp" in text


def test_christening_does_the_cadence_arithmetic():
    text = read("docs/onboarding/05-christening.md").lower()
    assert "twelve" in text and "four" in text, "the cadence must be stated as sessions per day"


def test_christening_names_the_human_as_the_one_who_names_it():
    text = read("docs/onboarding/05-christening.md").lower()
    assert "the software is sill" in text


def test_charter_prompts_are_open_ended_not_fields():
    text = read("onboarding/charter-prompts.md")
    assert text.count("?") >= 4, "the charter prompts are questions, and there are several"
    assert FRAMING in text.lower(), "the prompts page repeats the framing that governs it"


# --- every fenced command names something real -------------------------------


def iter_command_lines(text: str):
    """Yield executable lines from ```bash / ```sh fences only.

    Untagged fences hold sample *output* and are skipped. Heredoc bodies,
    comments, and bare shell assignments are not commands either.
    """
    lines = text.splitlines()
    i = 0
    in_block = False
    heredoc_end = None
    pending = ""
    while i < len(lines):
        line = lines[i]
        i += 1
        if line.startswith("```"):
            tag = line[3:].strip().lower()
            if in_block:
                in_block = False
            else:
                in_block = tag in ("bash", "sh", "shell")
            continue
        if not in_block:
            continue
        if heredoc_end is not None:
            if line.strip() == heredoc_end:
                heredoc_end = None
            continue
        m = re.search(r"<<-?\s*'?([A-Za-z_][A-Za-z0-9_]*)'?", line)
        if m:
            heredoc_end = m.group(1)
        if line.rstrip().endswith("\\"):
            pending += line.rstrip()[:-1] + " "
            continue
        full = (pending + line).strip()
        pending = ""
        if not full or full.startswith("#"):
            continue
        yield full


def split_outside_quotes(line: str):
    """Split on |, &&, ; — but never inside quotes or a $( ) substitution.

    A naive `re.split` cuts `python3 -c 'a; b'` in half and then insists the
    fragment is a missing binary. Written after exactly that happened.
    """
    parts, buf = [], ""
    quote = None
    depth = 0
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            buf += ch
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            buf += ch
            i += 1
            continue
        if line.startswith("$(", i):
            depth += 1
            buf += "$("
            i += 2
            continue
        if ch == ")" and depth:
            depth -= 1
            buf += ch
            i += 1
            continue
        if not depth and (ch == ";" or ch == "|" or line.startswith("&&", i)):
            parts.append(buf)
            buf = ""
            i += 2 if line.startswith(("&&", "||"), i) else 1
            continue
        buf += ch
        i += 1
    parts.append(buf)
    return parts


def iter_command_segments(line: str):
    """Split one command line into invoked segments, dropping comments."""
    line = re.sub(r"\s+#(?!\{).*$", "", line)
    for segment in split_outside_quotes(line):
        segment = segment.strip()
        if segment.startswith("$ "):
            segment = segment[2:].strip()
        # Drop leading env-var assignments (FOO=bar cmd ...), quoted values
        # included — splitting on whitespace first would tear `X="$(a b)"` in
        # half and then report the half as a missing binary.
        segment = re.sub(
            r"""^(?:[A-Za-z_][A-Za-z0-9_]*=(?:"[^"]*"|'[^']*'|\S*)\s*)+""", "", segment
        )
        tokens = segment.split()
        if not tokens:
            continue
        yield tokens


@pytest.mark.parametrize("rel", PHASE_DOCS)
def test_every_fenced_command_names_a_real_script_or_binary(rel):
    text = read(rel)
    for line in iter_command_lines(text):
        for tokens in iter_command_segments(line):
            head = tokens[0]
            if head in EXTERNAL_BINARIES or head in CONSOLE_SCRIPTS:
                pass
            elif "$" in head or head.startswith(("(", "'", '"', "<", ">")):
                pass
            elif head.startswith("/"):
                pass  # absolute paths in examples are the operator's, not ours
            else:
                assert (ROOT / head.lstrip("./")).exists(), (
                    f"{rel}: '{head}' is neither an OS binary, a declared console "
                    f"script, nor a file in this tree — line: {line}"
                )
            # `python3 -m scripts.foo` must name a module that exists.
            if "-m" in tokens:
                mod = tokens[tokens.index("-m") + 1] if tokens.index("-m") + 1 < len(tokens) else ""
                if mod.startswith("scripts."):
                    rel_path = "backend/" + mod.replace(".", "/") + ".py"
                    assert (ROOT / rel_path).exists(), f"{rel}: no module {rel_path}"
            # Any argument pointing into a shipped directory must exist.
            for tok in tokens[1:]:
                tok = tok.strip("'\"")
                if "/" not in tok or "$" in tok or "*" in tok or tok.startswith("/"):
                    continue
                if tok.split("/")[0] in SHIPPED_DIRS:
                    assert (ROOT / tok).exists(), (
                        f"{rel}: command names '{tok}', which does not exist — line: {line}"
                    )
