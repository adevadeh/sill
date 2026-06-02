# Hooks

Sill ships ten hooks under `plugin/hooks/`. They fall into three groups:

- **Recall** — `spontaneous-recall`.
- **Discipline checks** — `track-reuse`, `track-verification`,
  `attribution-check`, `check-agreement`, `check-corrections`,
  `response-patterns`, `state-language-check`.
- **Materialization** — `precompact-snapshot`, `goodnight-checkpoint`.

All hooks are non-blocking: they emit `systemMessage` /
`additionalContext` advice but never abort a tool call. Disabling any
of them is a matter of removing or commenting out one entry in
`~/.claude/settings.local.json` (or
`<project>/.codex/hooks.json` for Codex).

The default wiring is in `plugin/codex.hooks.json.template`. The
installer copies it (with `{{SILL_PLUGIN_DIR}}` substituted) to both
Codex and Claude Code config locations when you run
`./install.sh --hooks-for <project>`.

---

## spontaneous-recall

**What it does:** On every user prompt, queries sill (via psql against
the db container) for relevant memories and optionally pulls
conversation snippets from `episodic-memory`. Injects the results as
`additionalContext` so the response is grounded.

**When it fires:** `UserPromptSubmit`. Timeout: 45s.

**Env vars:**

- `SILL_DB_CONTAINER` (default `sill_db`)
- `SILL_DB_USER` (default `agi_user` — **mismatch alert**, see
  below)
- `SILL_DB_NAME` (default `agi_db` — same)
- `SILL_EPISODIC_MEMORY_PATH` — path to the episodic-memory CLI.
  Unset = degrade gracefully (sill-only recall).
- `PERSONAL_SUPERPOWERS_DIR` (default `~/.config/superpowers`)
- `SILL_LOG_DIR` (default `/tmp`)

**Mismatch alert.** The `install.sh` flow creates a database named
`sill` owned by `sill`, but this hook (and several others) default to
`agi_user` / `agi_db` from the upstream agi-memory project. Until the
defaults are unified, export these in your shell:

```bash
export SILL_DB_USER=sill
export SILL_DB_NAME=sill
```

**How to disable:** delete or comment out the `UserPromptSubmit`
entry that calls `spontaneous-recall.py`. The other hooks will continue
to work; you just won't get pre-prompt context.

---

## track-reuse

**What it does:** When the agent stops, scans the response text for
memory IDs that were hydrated this turn. For each match, calls
`touch_memory_reuse()` to bump `reuse_count` and update `last_reused`.
This produces the "value signal" used by importance decay.

**When it fires:** `Stop`. Timeout: 30s.

**Env vars:**

- `POSTGRES_HOST` (default `localhost`)
- `POSTGRES_PORT` (default `5432`)
- `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` — direct
  Postgres connection, not via docker exec. Defaults match the
  upstream (`agi_db` / `agi_user` / `agi_password`); set to your
  install's values.
- `SILL_LOG_DIR` (default `/tmp`)

**How to disable:** remove the `track-reuse.py` entry from the `Stop`
block. The cost: importance decay loses one of its signals; the
discipline checks still work.

---

## track-verification

**What it does:** On every tool call, records that a verification tool
ran (Bash / Read / Grep / etc.) by writing to a turn-scoped state
file. The `check-agreement` hook reads this file to know whether the
agent verified before agreeing.

**When it fires:** `PostToolUse`. No matcher — runs on every tool.

**Env vars:**

- `SILL_LOG_DIR` (default `/tmp`) — controls where
  `verification-state.json` and `agreement-hook.log` live.

**How to disable:** comment out the `PostToolUse` entry. Side effect:
`check-agreement` will always think no verification happened and warn
on every agreement phrase.

---

## attribution-check

**What it does:** Two drift classes, both flagged for manual review:

- **F1**: "beat N" citations. Checks the cited beat number against
  `docs/gnomon-sessions/beat-NNN-*.md` and translates legacy
  drift via `NUMBERING.md`. Useful only in the upstream project; in
  a fresh install it just no-ops (no sessions dir).
- **F2**: authorship patterns ("Sili said", "William wrote", "you
  wrote", "I concluded", "your quote", "quoted William"). Flags
  for verification before the memory is stored.

**When it fires:** `PreToolUse` matching
`mcp__(agi_memory|agi-memory|sill)__(remember|remember_batch|remember_batch_raw)|Bash`.

**Env vars:**

- `SILL_PROJECT_ROOT` (default `cwd`) — where to look for session
  files for the F1 check.
- `SILL_BEAT_SESSIONS_DIR` (default `<root>/docs/gnomon-sessions`).
- `SILL_LOG_DIR` (default `/tmp`).

**How to disable:** remove the `PreToolUse` entry whose command is
`attribution-check.py`. If you only want the F2 (authorship) check,
leave it on — F1 silently skips when the sessions dir is missing.

---

## check-agreement

**What it does:** On Stop, checks whether the response contains an
agreement phrase ("you're right", "my apologies", "good catch", …)
and whether a verification tool ran this turn. If agreement + no
verification, injects a self-check reminder.

**When it fires:** `Stop`.

**Env vars:** `SILL_LOG_DIR` only.

**How to disable:** remove from the `Stop` block. Note that the
`response-patterns` hook also catches agreement phrases via
`response-patterns/agreement.md`; `check-agreement` is the
stricter version that conditions on verification state.

---

## check-corrections

**What it does:** On every user prompt, (a) clears the per-turn
verification state file, and (b) checks the prompt against a list of
correction patterns ("you're wrong", "actually", "are you sure",
"did you check", …). If a correction looks likely, injects context
telling the agent to verify before responding.

**When it fires:** `UserPromptSubmit`. Not wired in the default
template — add it if you want the reset + nudge behavior.

**Env vars:** `SILL_LOG_DIR` only.

**How to disable:** don't wire it (default). If you wired it and
want it off, remove the `UserPromptSubmit` entry.

---

## response-patterns

**What it does:** On Stop, scans the response against a directory of
markdown rule files. Each rule file has frontmatter
(`name`, `enabled`, `patterns`) and a body that becomes the
warning message (with `{matched}` substituted). Matches log to
`/tmp/response-patterns-data.jsonl` for later analysis.

The shipped rule set (`plugin/response-patterns/`):

| File                       | What it catches |
|----------------------------|-----------------|
| `agreement.md`             | Agreement phrases without (separate) verification |
| `block-hedge.md`           | Ned Block-style P/A disclaimers performed as a verbal tic |
| `hedging.md`               | "hard to say", "may never know", … — stopped-thinking phrases |
| `meta-deflection.md`       | "great question", "many perspectives", … — stalling |
| `noted-without-noting.md`  | "I should store this" without actually storing |
| `state-language.md`        | Borrowed embodied-state phrases ("attention fading", …) |
| `storage-deference.md`     | Asking permission to store instead of storing |

**When it fires:** `Stop`. Timeout: 45s.

**Env vars:**

- `SILL_PLUGIN_DIR` (default `parent of hooks dir`).
- `SILL_RESPONSE_PATTERNS_DIR` (default
  `<plugin>/response-patterns`).
- `SILL_LOG_DIR` (default `/tmp`).

**How to disable:** either remove the `Stop` entry, or disable
individual rules by setting `enabled: false` in their frontmatter.
You can also point `SILL_RESPONSE_PATTERNS_DIR` at your own
directory of rule files — see `docs/extending.md`.

---

## state-language-check

**What it does:** Catches borrowed embodied-state and time-of-day
language ("attention is fading", "I need a break", "took about 20
minutes", "tonight") in two contexts:

1. Memory storage (`mcp__sill__remember*` calls) — flags before
   they enter the corpus.
2. `Write` / `Edit` to files under `journals/` or `docs/` — flags
   before they enter the source tree.

These phrases tend to be exit-scripts without referents in an LLM
(documented in the 2026-04-29 replay journal). The hook is
non-blocking; it just asks "do you have the state you're describing,
or are you matching human convention?"

**When it fires:** `PreToolUse` matching
`mcp__(agi_memory|agi-memory|sill)__(remember|remember_batch|remember_batch_raw)|Bash|apply_patch|Edit|Write`.

**Env vars:** `SILL_PROJECT_ROOT` (default `cwd`), `SILL_LOG_DIR`
(default `/tmp`).

**How to disable:** remove the matching `PreToolUse` entry.

---

## precompact-snapshot

**What it does:** Before context compaction, queries the db for
current focus, active goals, recent strategic decisions, open
contradictions, research progress, and recent high-importance
memories. Writes them all to
`<project>/.claude/rules/sill-orientation.generated.md`. The intent
is that a freshly-compacted context wakes up oriented — the
orientation file can be `@`-imported from a project `CLAUDE.md`.

**When it fires:** `PreCompact` (not in the default template — add
it explicitly when you want it).

**Env vars:**

- `SILL_PROJECT_ROOT` (default `cwd`).
- `SILL_RULES_DIR` (default `<root>/.claude/rules`).
- `SILL_ORIENTATION_OUTPUT` (default
  `<rules>/sill-orientation.generated.md`).
- `SILL_ORIENTATION_TITLE` (default `Sill Orientation`).
- `SILL_DB_CONTAINER` (default `sill_db`).
- `SILL_DB_USER` (default `agi_user` — see mismatch alert above).
- `SILL_DB_NAME` (default `agi_db` — same).
- `SILL_RESEARCH_MANIFEST` (default
  `<root>/docs/research-manifest.json`) — optional, for the
  "research progress" section.

**How to disable:** don't wire it. If wired and unwanted, remove the
`PreCompact` entry.

---

## goodnight-checkpoint

**What it does:** On user prompts matching configured trigger phrases
(`goodnight`, `signing off`, `see you tomorrow`, …), writes a daily
markdown checkpoint at
`<project>/logs/daily-checkpoints/YYYY-MM-DD.md` summarizing today's
memory activity. Optionally updates a `drives` row's `current_focus`
field with a one-line summary.

**When it fires:** `UserPromptSubmit` (not in the default template —
opt in).

**Env vars:**

- `SILL_DB_CONTAINER` (default `sill_db`).
- `SILL_DB_USER` (default `sill`) — note: this hook uses the
  install-time defaults, unlike `spontaneous-recall`.
- `SILL_DB_NAME` (default `sill`) — same.
- `SILL_PROJECT_ROOT` (default `cwd`).
- `SILL_PLUGIN_DIR` (default `parent of hooks dir`).
- `SILL_GOODNIGHT_FOCUS_DRIVE` — name of a `drives` row to update.
  Unset = skip the focus update.

**Config files:**

- `plugin/config/goodnight-triggers.txt` — one phrase per line,
  `#` for comments. Substring match, case-insensitive,
  word-bounded.

**How to disable:** don't wire it. If wired, remove the
`UserPromptSubmit` entry.

---

## Rule files (response-patterns)

The `plugin/response-patterns/` directory holds seven generic rule
files that ship by default. Each file is a self-contained unit:

```yaml
---
name: my-rule
enabled: true
patterns:
  - "regex one"
  - "regex two"
---

Warning message body. Can reference {matched}.

**Corrective action:** what the agent should do next.
```

To add your own: drop a new `<name>.md` into the directory, or point
`SILL_RESPONSE_PATTERNS_DIR` at your own directory and copy the
shipped rules over selectively. See `docs/extending.md` for the
full pattern.

---

## Adding hooks to a project

When you ran `./install.sh --hooks-for /path/to/project`, the
installer wrote two files:

- `/path/to/project/.codex/hooks.json` — full Codex hook config.
- `/path/to/project/.claude/settings.local.json` — merged into any
  existing `hooks` block.

Re-running `--hooks-for` is idempotent; existing hook entries are
preserved and Sill's are deduplicated.

Some hooks (`check-corrections`, `precompact-snapshot`,
`goodnight-checkpoint`) are **not** in the default template. To wire
them, copy the relevant block out of `plugin/codex.hooks.json.template`
and edit by hand. Examples in the comments at the top of each hook
file.
