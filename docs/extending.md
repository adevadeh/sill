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

| Var                  | Default        | Used by |
|----------------------|----------------|---------|
| `SILL_DB_CONTAINER`  | `sill_db`      | `recall_lib`, `spontaneous-recall`, `precompact-snapshot`, `goodnight-checkpoint`, `backend/sill.py` (`notice`), `backend/scripts/decompose_event.py`, the install/verify scripts |
| `SILL_DB_USER`       | `sill`         | same set as `SILL_DB_CONTAINER` |
| `SILL_DB_NAME`       | `sill`         | same set as `SILL_DB_CONTAINER` |
| `POSTGRES_HOST`      | `localhost`    | `track-reuse` (direct `psycopg2`, not `docker exec`) |
| `POSTGRES_PORT`      | `5432`         | `track-reuse` |
| `POSTGRES_DB`        | `sill`         | `track-reuse` |
| `POSTGRES_USER`      | `sill`         | `track-reuse` |
| `POSTGRES_PASSWORD`  | `sill_password`| `track-reuse` |

Every one of these already matches what `backend/docker-compose.yml`
and `backend/.env.example` provision by default — there is no
mismatch to work around on a fresh install (earlier drafts of this
doc described one; it was fixed in the hooks' own code before it was
fixed here). Confirm for yourself:

```bash
grep -rn "agi_user\|agi_db\|agi_password" plugin/ backend/*.py backend/scripts/*.py
# -> no output
```

Only override these if you renamed the container, user, or database
away from the shipped defaults (e.g. running a second Sill instance
side by side):

```bash
export SILL_DB_CONTAINER=sill_db_test
export SILL_DB_USER=sill_test
export SILL_DB_NAME=sill_test
export POSTGRES_DB=sill_test
export POSTGRES_USER=sill_test
export POSTGRES_PASSWORD=sill_test_password
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
| `SILL_LOG_DIR`               | `/tmp`                                 | Where every hook's `.log` file lives, plus every sidecar/state file: `recall-sidecar-<session>.jsonl`, `response-patterns-last-<session>.json`, `response-patterns-data.jsonl`, `auto-stored-insights.jsonl`, `verification-state.json`, `cc-session-by-pid/` |

### Headless / interactive gate (spontaneous-recall)

Read fresh on every `UserPromptSubmit`; none of these are set by
`install.sh` — they're for the caller (a scheduler, a wrapping
front-end) to set on the child session it spawns.

| Var                   | Default | Purpose |
|------------------------|---------|---------|
| `SILL_DETACHED_BEAT`   | unset   | `1` = an authoritative "this session is headless" flag a scheduler sets on every child it spawns. Silences recall injection and the `[TIME]` header's clock-advance. |
| `SILL_INTERACTIVE`     | unset   | `1` = override back to interactive even though the entrypoint looks headless (an `sdk*` `CLAUDE_CODE_ENTRYPOINT`) — for a front-end that drives `--print` on behalf of a real person. |
| `SILL_HEADLESS_TOOL`   | unset   | `1` = explicit "be quiet" that always wins, regardless of the other two. |

The gate defaults to interactive and excludes only the known-headless
family (blacklist, not whitelist), so a front-end this project has
never heard of is treated as a human by default. See
`docs/hooks.md`'s spontaneous-recall section for the full precedence
order.

### Mint-path auto-store (response-patterns)

| Var                    | Default                                | Purpose |
|-------------------------|-----------------------------------------|---------|
| `SILL_HOME_PROJECT`    | unset                                   | Names the one project the insight auto-store is meant to run in. **Fail-closed**: unset means every cwd — including a real project — reads as "home", so auto-store stays log-only everywhere until you configure this. |
| `SILL_SPEAKER_SELF`    | `instance`                              | The `--speaker` value stamped on an auto-stored memory. Rename it once you've christened the running instance. |
| `SILL_INSIGHT_DETECT`  | `0` (off)                               | `1` turns on the local-model insight detector. Leave off unless you have a model reachable at `SILL_OLLAMA_URL` — otherwise every Stop event pays a timeout for nothing. |
| `SILL_CLI`             | `sill`                                  | The command the auto-store path shells out to (`$SILL_CLI notice ...`). Point it at a full path if `sill` isn't on the hook's PATH. |
| `SILL_OLLAMA_URL`      | `http://localhost:11434/api/generate`   | Only consulted when `SILL_INSIGHT_DETECT=1`. |
| `SILL_OLLAMA_MODEL`    | `gemma3:12b`                            | Only consulted when `SILL_INSIGHT_DETECT=1`. |

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
shape — `attribution-check`'s F2 patterns hardcode the origin
project's own example names rather than a portable placeholder (see
`docs/hooks.md`'s attribution-check section for how to adapt it),
the F1 path only fires when a `docs/gnomon-sessions/` directory
exists, and the worldview/identity tables that most installs won't
touch. The database-credential mismatch a previous version of this
doc warned about here is gone — every hook has defaulted to `sill` /
`sill` for a while now (see the "Database connection" table above).
None of the above is harmful, but it's worth knowing about when
something doesn't quite line up with what's documented here.

The methodology pack itself, by contrast, is written to be portable.
Twenty-two memories about inquiry, verification, recall patterns,
attribution, and storage hygiene that the upstream project converged
on over six months. You can read the whole thing at
`seed/methodology.jsonl`; it's plain JSONL.
