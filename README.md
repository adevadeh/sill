# Sill

Persistent memory for AI agents. Postgres + pgvector + Apache AGE behind an
MCP server, with a Claude Code / Codex plugin that wires spontaneous recall,
attribution checks, response-pattern detection, and a small set of other
discipline hooks.

Built for the case where you run Claude Code (or Codex) locally and want
the model to remember decisions, methodology, and prior conversations
across sessions — without paying a SaaS for it.

> Status: v0.1.0 — initial extraction from the agi-memory research project.

---

## Quick start

```bash
git clone <this-repo> sill
cd sill
./install.sh
```

`./install.sh` runs ten preflighted steps. On a clean machine you should
see, in order:

1. preflight (docker / python3 / pipx on PATH; docker daemon up)
2. install backend (`sill`, `sill-mcp`, `sill-worker` console scripts)
3. `docker compose build`; start db + embeddings + rabbitmq + maintenance_worker
4. wait up to 120s for db + embeddings healthchecks
5. import the methodology seed (22 procedural/semantic memories)
6. symlink the plugin into `~/.claude/plugins/local/sill-plugin`
7. idempotently merge the `sill` MCP server entry into `~/.claude/.mcp.json`
   (and `~/.codex/config.toml` if Codex is installed)
8. optional: wire per-project hook configs (`--hooks-for /path/to/project`)
9. print a `./verify.sh` hint
10. final next-steps banner

Then:

```bash
./verify.sh
```

Six smoke checks: db/embeddings healthy, `sill-mcp --help` runs, seed
loaded (>= 22 memories), one hook parses a canned event, schema level
current, adapter conformance (the four-slot contract — see
`docs/adapters.md`). Exits 0 on green.

Restart Claude Code (and/or Codex) so the new MCP server entry is picked
up.

## Upgrading

```bash
git pull
./upgrade.sh
```

`upgrade.sh` backs up the database first (to `backups/<db>-preupgrade-<UTC>.sql.gz`),
applies any pending `backend/migrations/NNN_*.sql` in order (tracked in the
`schema_migrations` table), then runs `./verify.sh`. Preview with
`./upgrade.sh --dry-run`. Fresh installs never need it — first boot already
initializes at the current level (check with `./verify.sh`, check 5).

Restore, if you ever need it — the dump replays into an **empty** database, so
drop and recreate first, then verify (graph-extension data can be finicky
across dump/restore, so treat `./verify.sh` as part of the restore):

```bash
docker exec sill_db psql -U sill -d postgres \
  -c "DROP DATABASE IF EXISTS sill WITH (FORCE);" \
  -c "CREATE DATABASE sill OWNER sill;"
gunzip -c backups/<file>.sql.gz | docker exec -i sill_db psql -U sill -d sill
./verify.sh
```

### Hook upgrades and `--hooks-for`

`./upgrade.sh` only migrates the database — it never touches a
project's hook wiring. Picking up hooks added after your project was
first wired means re-running `./install.sh --hooks-for /path/to/project`,
and the two files that command writes behave differently on a re-run:

- **`.claude/settings.local.json`** always merges idempotently — new
  hook entries are appended, existing ones untouched. Re-running
  `--hooks-for` is enough; nothing else to do.
- **`.codex/hooks.json`** is skipped entirely if the file already
  exists (`install.sh`'s own note: `"already exists; leaving as-is
  (delete to re-render)"`) — a project wired before a hook was added
  keeps missing it on the Codex side until you delete the file and
  re-render it.

Check whether a given project's Codex wiring is stale, and fix it if so:

```bash
grep -q "clear-handoff" /path/to/project/.codex/hooks.json \
  && echo "up to date" || echo "STALE — delete and re-render"
# if stale:
rm /path/to/project/.codex/hooks.json
./install.sh --hooks-for /path/to/project
```

---

## Prerequisites

| Tool        | Why                                            | Notes |
|-------------|------------------------------------------------|-------|
| Docker      | Postgres + embeddings + RabbitMQ + workers     | OrbStack works on macOS; native Docker on Linux. The daemon must be running. |
| Python 3.10+ | Backend package, hooks                        | macOS system Python works; Linux distro Python works. |
| pipx (or pip) | Installs the `sill`/`sill-mcp`/`sill-worker` console scripts | If pipx isn't on PATH, install falls back to a venv at `~/.local/share/sill-venv` and symlinks into `~/.local/bin`. Make sure `~/.local/bin` is on your `PATH`. |
| Claude Code | The plugin host                                | Get it from Anthropic. The MCP server and hooks plug in automatically. |
| Codex CLI   | *(optional)* Second supported host             | Detected automatically; if installed, `~/.codex/config.toml` is updated. |

The embeddings container downloads the default model
(`unsloth/embeddinggemma-300m`, ~300MB) on first start. Plan for a slow
first `docker compose up`.

---

## What gets installed

**Docker stack** (`backend/docker-compose.yml`):

- `db` — Postgres 16 with `pgvector` and Apache AGE. Volume:
  `postgres_data`. Default container name: `sill_db`.
- `embeddings` — HuggingFace Text Embeddings Inference (CPU image),
  serving `unsloth/embeddinggemma-300m`. Volume: `embeddings_cache`.
- `rabbitmq` — Used by the maintenance worker for inter-process events.
- `maintenance_worker` — Default-on. Importance decay, drift tracking,
  housekeeping.

Reflective processing ships as the beat worker (see `docs/beats.md`), which is off
until you turn it on.

**Console scripts** (installed into pipx-managed bin, or
`~/.local/bin` if pipx isn't available):

- `sill` — top-level CLI (`sill seed import`, `sill notice` — the mint
  path, see below — `sill db psql`, `sill verify`; not all
  subcommands are wired yet).
- `sill-mcp` — the MCP server. Claude Code spawns this from
  `~/.claude/.mcp.json`.
- `sill-worker` — the worker entry point used inside the Docker containers.

**Plugin** (`plugin/`):

- 12 hooks (recall, attribution checks, response patterns, a mint-path
  auto-store, a shell-idiom guard, `/clear` handoff, etc. — see
  `docs/hooks.md`)
- 8 generic response-pattern rule files
- `goodnight-triggers.txt` config
- Templates for Claude Code and Codex hook wiring

Symlinked to `~/.claude/plugins/local/sill-plugin` so Claude Code picks
it up.

---

## Configuration

Copy `backend/.env.example` to `backend/.env` and edit if you need
non-defaults:

```bash
cp backend/.env.example backend/.env
```

The defaults work for a single-host single-user install. Common reasons
to edit:

- **Port collision**: bump `POSTGRES_PORT` if 5432 is already taken.
- **Run multiple Sill instances**: change `SILL_DB_CONTAINER` so they
  don't share a Postgres container name.
- **Episodic-memory integration**: set `SILL_EPISODIC_MEMORY_PATH`
  to the path of an installed
  [episodic-memory](https://github.com/obra/superpowers-plugins) CLI.

Embedding model and dimension are **locked at install time** — changing
them later requires a schema rebuild. See `docs/concepts.md`.

For the full list of `SILL_*` env vars (hook log dir, plugin dir,
project root, beat sessions dir, fake-embeddings test mode, …) see
`docs/extending.md`.

---

## The mint path

Recall is the read side; `sill notice` is the write side. It stores
one memory as a speech act — what it says, and who said it with what
illocutionary force (`assertive`/`directive`/`commissive`/
`expressive`/`declaration`; only `assertive` is truth-scored, the
rest succeed by being complied-with, kept, sincere, or felicitous).
`--speaker` is required — an unattributed mint is the store's main
hygiene hole. `--receipt-to <file>` has the store itself splice the
mint's receipt into a waiting placeholder line in a journal file,
instead of you pasting the id back in by hand. For bundling several
acts from one event into a single transaction, so they share one
`created_at` as the bundle key, see
`backend/scripts/decompose_event.py`. Full flag reference:

```bash
sill notice --help
```

This same path is what `response-patterns`' local-model insight
detector calls to auto-store a novel insight (off by default — see
`SILL_INSIGHT_DETECT` in `docs/extending.md`), which is also why
v0.1.0's auto-store never actually stored anything: the CLI it shelled
out to had no `notice` subcommand until this path was wired.

---

## Reflective beats

A config-driven rotation of headless agent-CLI sessions — each running
its own standing prompt through orient → decide → act → store → log, each
producing its own transcript — that fires on a timer instead of only when
you're at the keyboard. **Off by default, opt-in**: nothing runs until you
write a `beats.json` and start `sill-worker --mode beat` yourself, by hand
or under a schedule.

**Read `docs/beats.md` before turning this on** — specifically its
Permissions section, before its Scheduling section. A non-interactive
agent CLI with no tool permissions configured for the directory it runs in
gets every tool call denied rather than prompted and exits 0 having done
nothing; a beat worker with that problem ticks on a schedule forever while
accomplishing nothing, and nothing about the schedule itself will tell you
that's what's happening. `docs/beats.md` covers the voice config format, a
worked example, the full env-var surface, how to run one beat by hand and
confirm it actually did something, and the scheduling templates under
`scheduling/` for running it unattended once you have.

---

## Adding hooks to a project

If you want Sill's hooks to run in a specific project (recall on prompt
submit, attribution checks on `remember` calls, response-pattern checks
on Stop, etc.), point the installer at that project:

```bash
./install.sh --hooks-for /path/to/your/project
```

This writes two files:

- `/path/to/your/project/.codex/hooks.json` — rendered from
  `plugin/codex.hooks.json.template`, but only if that file doesn't
  already exist.
- `/path/to/your/project/.claude/settings.local.json` — same hooks
  merged into the existing `hooks` block (created if absent).

Re-running is safe on both sides, but only the Claude Code side is
idempotent in the useful sense of also picking up new hooks — existing
entries are preserved and Sill's are deduplicated, and a later Sill
version's new hooks get merged in on the next run. The Codex side is
all-or-nothing: once `.codex/hooks.json` exists, re-running
`--hooks-for` leaves it exactly as it was. See "Hook upgrades and
`--hooks-for`" under Upgrading above for the delete-and-re-render fix.

---

## Verifying, resetting, uninstalling

```bash
./verify.sh        # six smoke checks; exits non-zero on failure
./reset.sh         # drops the postgres volume and re-seeds (asks first)
./uninstall.sh     # removes containers + volumes + backend + plugin symlink
./uninstall.sh --keep-data   # same, but keep the volumes
```

`uninstall.sh` deliberately does **not** edit `~/.claude/.mcp.json` or
`~/.codex/config.toml` — those may contain other servers it shouldn't
disturb. It prints instructions for the hand-edit.

For background on what's in the database, what shape memories should
take, and how recall is meant to flow, see `docs/concepts.md`.

---

## Soft dependency: episodic-memory

The `spontaneous-recall` hook can pull conversation snippets from
[episodic-memory](https://github.com/obra/superpowers-plugins) (a
separate Claude Code plugin) when it's installed. Without it, the hook
still works — it just falls back to sill-only recall.

To enable: install the episodic-memory plugin, then export
`SILL_EPISODIC_MEMORY_PATH` pointing at its CLI binary. The hook
detects it on each call.

---

## Documentation

- `docs/concepts.md` — what's in the database, memory shape rules,
  embedding dimension, recall patterns, workers, the AGE graph layer.
- `docs/hooks.md` — one section per shipped hook (event, env vars,
  how to disable, a canned test command).
- `docs/extending.md` — writing good memories, the quality gate,
  adding your own hooks, customizing rule files and triggers,
  env-var cheat sheet.
- `docs/beats.md` — the reflective beat worker: voice config, the
  env-var surface, permissions (read this before scheduling anything),
  running one beat by hand, reading a transcript.

---

## Credit

Sill was extracted from the agi-memory research project (William
Taysom + Sili). The underlying schema and recall design have been
in continuous use since late 2025; this v0.1.0 release packages a
sanitized core for reuse.

License: MIT. See `LICENSE`.
