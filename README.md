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

Five smoke checks: db/embeddings healthy, `sill-mcp --help` runs, seed
loaded (>= 22 memories), one hook parses a canned event, schema level current. Exits 0 on green.

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
  -c "DROP DATABASE IF EXISTS sill WITH (FORCE); CREATE DATABASE sill OWNER sill;"
gunzip -c backups/<file>.sql.gz | docker exec -i sill_db psql -U sill -d sill
./verify.sh
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
- `heartbeat_worker` — **Opt-in only** via the `heartbeat` Compose
  profile. Periodic synthesis / reflection cycles. Don't bring this up
  unless you've configured an LLM provider.

**Console scripts** (installed into pipx-managed bin, or
`~/.local/bin` if pipx isn't available):

- `sill` — top-level CLI (`sill seed import`, `sill db psql`,
  `sill verify`; not all subcommands are wired yet).
- `sill-mcp` — the MCP server. Claude Code spawns this from
  `~/.claude/.mcp.json`.
- `sill-worker` — the worker entry point used inside the Docker containers.

**Plugin** (`plugin/`):

- 10 hooks (recall, attribution checks, response patterns, etc.)
- 7 generic response-pattern rule files
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

## Adding hooks to a project

If you want Sill's hooks to run in a specific project (recall on prompt
submit, attribution checks on `remember` calls, response-pattern checks
on Stop, etc.), point the installer at that project:

```bash
./install.sh --hooks-for /path/to/your/project
```

This writes two idempotent files:

- `/path/to/your/project/.codex/hooks.json` — rendered from
  `plugin/codex.hooks.json.template`.
- `/path/to/your/project/.claude/settings.local.json` — same hooks
  merged into the existing `hooks` block (created if absent).

Re-running is safe; existing hooks are preserved.

---

## Verifying, resetting, uninstalling

```bash
./verify.sh        # five smoke checks; exits non-zero on failure
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
  how to disable).
- `docs/extending.md` — writing good memories, the quality gate,
  adding your own hooks, customizing rule files and triggers,
  env-var cheat sheet.

---

## Credit

Sill was extracted from the agi-memory research project (William
Taysom + Sili). The underlying schema and recall design have been
in continuous use since late 2025; this v0.1.0 release packages a
sanitized core for reuse.

License: MIT. See `LICENSE`.
