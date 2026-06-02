# Extending Sill

How to write good memories, add your own hooks, customize the shipped
rules, and tune the env-var knobs.

---

## Writing good memories

The single best mental model for working with Sill:

> **Memory retrieval is in-context programming.** Each recalled
> memory opens certain pathways and closes others in the frozen
> weights. Dense semantic content (verbatim quotes, concrete
> examples, named entities) makes rich pathway nodes. Abstract
> pointers ("discusses X", "argues that Y") are dead ends — they
> tell the model a topic exists without giving it material to think
> with. Memory quality matters more than memory quantity.

Concretely, this means:

- **Quote, don't summarize.** A memory that contains
  `"X is wrong because Y" — Alice, 2025-12-01` steers thinking. A
  memory that says `Alice argued against X` doesn't.
- **Name entities.** Use actual project / file / function names.
  Embeddings cluster real names; "the recall module" embeds like
  noise.
- **Include the falsifier.** If you can write `would be revised
  if Z`, write it. It makes the memory testable instead of
  load-bearing-by-vibe.
- **Resist restating-the-obvious.** If the memory is `we should
  verify before agreeing`, you've just restated CLAUDE.md. Skip
  it; rely on the methodology pack.

### The quality gate

Seed memory #7 of `seed/methodology.jsonl`:

> Before storing a memory, check the draft has at least one of:
> 1. a verbatim quote with source,
> 2. a falsifier ("this would be wrong if…"),
> 3. a concrete example.
>
> Stable preferences/decisions are OK without these but should
> name what they're a preference *over*. If the draft is just a
> restated observation with none of the above, ask before storing.

Run this gate in your head before every `remember` call. If you
notice yourself drafting `I should store this insight that we
agreed to verify` — don't. Either find the quote behind the
insight, or skip the store.

### Storage triggers

Seed memory #6:

> When the user states a generalizable principle, distinction, or
> critique of your behavior that you'd want a future instance to
> act on — and a quick `recall_preview` suggests it isn't already
> in the corpus — store it before continuing. Skip preferences,
> examples, and one-off corrections. Bar: "a future instance
> making the same mistake would be prevented by this".

The most common storage failure mode isn't over-storing or
under-storing — it's *talking about* storing without storing. The
`noted-without-noting.md` response-pattern rule catches that.

---

## Adding your own hooks alongside Sill's

Claude Code's hook system is additive within an event. If you have
your own `Stop` hook and Sill has two, all three run (in array
order). The composition rules:

1. Hooks in the **same event** run independently — none can block
   another.
2. Sill's hooks emit `additionalContext` / `systemMessage` and
   exit 0 — they never abort.
3. If you write a blocking hook (exit non-zero with
   `permissionDecision: "deny"`), you'll abort the tool call but
   not Sill's hooks for that event.

**Where to put them.** Two options:

- **Per-project**, in `<project>/.claude/settings.local.json` or
  `<project>/.codex/hooks.json`. Sill's installer merges into
  these files idempotently; adding your own entries alongside is
  safe.
- **Globally**, in `~/.claude/settings.json`. Sill doesn't touch
  this file.

**Avoiding matcher conflicts.** Sill's `PreToolUse` matchers
target memory-storage tools and (for state-language-check)
`Bash|apply_patch|Edit|Write`. If your hook uses a narrower or
unrelated matcher, there's no conflict. If you want to gate one
of Sill's hooks off for a specific tool, the cleanest path is
to copy the relevant entry, edit the matcher, and disable the
shipped one — don't try to "subtract" matchers.

**Example layout** for a project with your own hook and Sill's:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {"type": "command", "command": "python3 /path/to/my-hook.py"}
        ]
      },
      {
        "hooks": [
          {"type": "command",
           "command": "python3 /Users/you/.claude/plugins/local/sill-plugin/hooks/response-patterns.py"},
          {"type": "command",
           "command": "python3 /Users/you/.claude/plugins/local/sill-plugin/hooks/track-reuse.py"}
        ]
      }
    ]
  }
}
```

The second entry is what `./install.sh --hooks-for` writes. The
first is yours. Both run on every Stop.

---

## Adding your own response-pattern rules

The `response-patterns` hook reads every `*.md` file in the rules
directory and treats each as an independent check.

### Option A: drop into the shipped directory

Add `plugin/response-patterns/my-rule.md`:

```yaml
---
name: my-rule
enabled: true
patterns:
  - "regex one"
  - "regex two"
---

Warning message body. `{matched}` interpolates the matched text.

**Corrective action:** what the agent should do about it.
```

It'll be picked up on the next Stop event. The shipped files are a
fair set of examples to copy from (`agreement.md` for a simple
keyword list; `block-hedge.md` for regex with backreferences).

Note: this directory is inside the symlinked plugin, so files you
add here will travel with the install. If that's a problem, use
option B.

### Option B: point the hook at your own directory

```bash
export SILL_RESPONSE_PATTERNS_DIR=/path/to/my/rules
```

The hook reads only that directory. You can copy the shipped rules
across selectively, or start from scratch.

### Disabling a shipped rule

Edit its frontmatter:

```yaml
---
name: agreement
enabled: false
patterns: …
---
```

The hook checks `enabled` before evaluating patterns; the rule is
skipped without being deleted.

---

## Customizing goodnight-checkpoint triggers

`plugin/config/goodnight-triggers.txt`:

```
# Lines starting with # are comments.
goodnight
good night
going to sleep
going to bed
off to bed
calling it a night
signing off
see you tomorrow
```

One trigger per line. Matched as substrings, case-insensitive,
word-bounded.

To add a trigger: append a line. To remove: comment it out or delete
it. The hook re-reads the file every fire, so no restart needed.

To use a different file, point `SILL_PLUGIN_DIR` at a directory
that has a `config/goodnight-triggers.txt`.

To suppress the focus-update side effect, leave
`SILL_GOODNIGHT_FOCUS_DRIVE` unset.

---

## Env-var cheat sheet

### Database connection

| Var                  | Default       | Used by |
|----------------------|---------------|---------|
| `SILL_DB_CONTAINER`  | `sill_db`     | `recall_lib`, `spontaneous-recall`, `precompact-snapshot`, `goodnight-checkpoint`, the install/verify scripts |
| `SILL_DB_USER`       | varies        | `recall_lib` (`sill`), `goodnight-checkpoint` (`sill`), `spontaneous-recall` (`agi_user`), `precompact-snapshot` (`agi_user`) |
| `SILL_DB_NAME`       | varies        | same split as above |
| `POSTGRES_HOST`      | `localhost`   | `track-reuse` (direct psycopg2) |
| `POSTGRES_PORT`      | `5432`        | `track-reuse` |
| `POSTGRES_DB`        | `agi_db`      | `track-reuse` |
| `POSTGRES_USER`      | `agi_user`    | `track-reuse` |
| `POSTGRES_PASSWORD`  | `agi_password`| `track-reuse` |

**The mismatch.** `install.sh` creates the db as `sill` / `sill`,
but `spontaneous-recall`, `precompact-snapshot`, and `track-reuse`
default to the upstream `agi_*` names. Until the defaults are
unified, set this in your shell:

```bash
export SILL_DB_USER=sill
export SILL_DB_NAME=sill
export POSTGRES_DB=sill
export POSTGRES_USER=sill
export POSTGRES_PASSWORD=sill_password
```

### Paths

| Var                          | Default                                | Purpose |
|------------------------------|----------------------------------------|---------|
| `SILL_PROJECT_ROOT`          | `cwd`                                  | Where attribution-check looks for sessions, where goodnight-checkpoint writes daily logs, where precompact-snapshot writes orientation |
| `SILL_PLUGIN_DIR`            | parent of hooks dir                    | Where response-patterns and goodnight-triggers config files live |
| `SILL_RULES_DIR`             | `<root>/.claude/rules`                 | Where precompact-snapshot writes |
| `SILL_ORIENTATION_OUTPUT`    | `<rules>/sill-orientation.generated.md`| Override the orientation file path entirely |
| `SILL_ORIENTATION_TITLE`     | `Sill Orientation`                     | H1 title in the orientation file |
| `SILL_RESPONSE_PATTERNS_DIR` | `<plugin>/response-patterns`           | Where the response-patterns hook reads rule files from |
| `SILL_BEAT_SESSIONS_DIR`     | `<root>/docs/gnomon-sessions`          | Where attribution-check looks for `beat-NNN-*.md` files |
| `SILL_RESEARCH_MANIFEST`     | `<root>/docs/research-manifest.json`   | Optional manifest read by precompact-snapshot |
| `SILL_LOG_DIR`               | `/tmp`                                 | Where every hook writes its `.log` file |

### Integrations

| Var                          | Default | Purpose |
|------------------------------|---------|---------|
| `SILL_EPISODIC_MEMORY_PATH`  | unset   | Path to the episodic-memory CLI binary. Unset = spontaneous-recall skips episodic. |
| `PERSONAL_SUPERPOWERS_DIR`   | `~/.config/superpowers` | Where archived conversations live, for episodic recall. |

### Hook-specific

| Var                            | Purpose |
|--------------------------------|---------|
| `SILL_GOODNIGHT_FOCUS_DRIVE`   | Name of a `drives` row to update with a one-line summary on goodnight. Unset = skip. |
| `SILL_FAKE_EMBEDDINGS`         | Test-only. `1` / `true` / `yes` makes `sill seed import` use deterministic fake embeddings instead of calling the embeddings service. Useful for CI / fast roundtrip tests. |

### Container names (Docker Compose overrides)

| Var                                | Default                    |
|------------------------------------|----------------------------|
| `SILL_EMBEDDINGS_CONTAINER`        | `sill_embeddings`          |
| `SILL_RABBITMQ_CONTAINER`          | `sill_rabbitmq`            |
| `SILL_MAINTENANCE_WORKER_CONTAINER`| `sill_maintenance_worker`  |
| `SILL_HEARTBEAT_WORKER_CONTAINER`  | `sill_heartbeat_worker`    |

Useful when running multiple Sill stacks on the same host.

---

## A note on the upstream

Sill was extracted from agi-memory and still carries some upstream
shape — the `agi_user` / `agi_db` defaults in three hooks, the
attribution-check F1 path that only fires in the original session
log directory, and the worldview/identity tables that most installs
won't touch. None of this is harmful, but it's worth knowing about
when something doesn't quite line up with what's documented here.

The methodology pack itself, by contrast, is written to be portable.
Twenty-two memories about inquiry, verification, recall patterns,
attribution, and storage hygiene that the upstream project converged
on over six months. You can read the whole thing at
`seed/methodology.jsonl`; it's plain JSONL.
