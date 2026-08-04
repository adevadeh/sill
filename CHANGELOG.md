# Changelog

All notable changes to Sill are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

## v0.2.0-dev — unreleased

### Added

- **Schema migration lane** — versioned migrations under `backend/migrations/`
  (ported from the upstream agi-memory research project, 020–026), a
  `schema_migrations` tracking table stamped at initdb for fresh installs,
  `./upgrade.sh` (backup-first, `--dry-run`), verify.sh check 5, and a CI job
  asserting fresh init == baseline + upgrade (pg_dump diff).
- **Speech-act columns** — nullable `force` and `speaker` on `memories`;
  reuse-event provenance table snapshotting both at detection time.
- **session_activity** — per-project activity clock for the spontaneous-recall
  time header (hook wiring follows in a later 0.2.0 change).
- **Guards** — archive-status invariant trigger (`archived_at` authoritative);
  connect phantom-node guard (`discover_relationship` refuses ids with no
  memories row).
- **Mint path** — `sill notice` (force/speaker-tagged memory writes, required
  `--speaker`), store-written receipts (`--receipt-to` splices a mint's
  receipt into a waiting journal placeholder instead of the caller pasting
  it back by hand), and `backend/scripts/decompose_event.py` (bundles every
  speech act from one event into a single transaction so they share one
  `created_at` as the bundle key).
- **spontaneous-recall hook wiring** — the `[TIME]` header (built on
  `session_activity` above) reporting wall-clock plus within-/across-session
  gaps; a headless/detached-beat gate (`SILL_DETACHED_BEAT`,
  `SILL_INTERACTIVE`, `SILL_HEADLESS_TOOL`) that silences recall for
  non-interactive `--print` invocations; a recall sidecar so non-MCP recall
  paths stay visible to reuse tracking; access telemetry; `src=` provenance
  refs pointing at a memory's origin address in recall output.
- **track-reuse provenance detector** — evidence-based reuse detection
  sampled from a memory's body rather than its head (the head is what a
  citation reproduces), three false-positive guards (body-only sampling,
  reject a phrase shared by ≥2 recalled memories as a title not evidence,
  zero a burst of more than 3 detections in one Stop event as a citation
  sweep), and a sidecar reader so recall paths that bypass MCP tool_results
  are still tracked.
- **response-patterns fog closure** — a fail-closed `SILL_HOME_PROJECT` gate
  (unset ⇒ every project reads as home ⇒ auto-store stays log-only);
  deliberate-mint suppression so a session that already minted a row (MCP
  `remember` or the CLI's `notice`/`decompose_event`) doesn't get an
  unhedged auto-store echo of itself; a carry-forward sidecar that delivers
  a Stop hook's warnings at the start of the next prompt; tagged,
  force/speaker-stamped auto-store; comment-tolerant frontmatter parsing;
  the `authorship-attribution` response-pattern rule.
- **New hooks** — `shell-idiom-guard` (PreToolUse/Bash; denies the zsh
  `echo =word` trap — the one hook in the suite that blocks rather than
  advises); `clear-handoff` (SessionStart; re-injects the prior session's
  final assistant message after `/clear`).

### Fixed

- `get_embedding` no longer fails on content containing literal backslashes
  (encoding-safe `convert_to` replaces the escape-interpreting `::bytea` cast).
- Access telemetry decoupled from importance (removed the compounding
  importance-on-access trigger path; `touch_memory_access` is pure telemetry).
- v0.1.0's insight auto-store path was inert (the CLI had no `notice`
  subcommand); now wired and covered by a contract test.

### Changed

- response-patterns no longer emits the discarded `reason` field.

### Removed

- Internal `.history/` scratch migrations from the v0.1.0 extraction
  (house-internal drafts; never executed at runtime). The shipped image now
  contains only the baseline schema and the numbered migrations.

## v0.1.0 — 2026-06-03

Initial extraction from the agi-memory research project.

### Added

- **Backend** — Postgres 16 + pgvector + Apache AGE behind a single
  squashed `schema.sql` (43 tables, no migration history). Built into
  a Docker image that bakes the schema in at
  `/docker-entrypoint-initdb.d/`.
- **Embeddings** — HuggingFace Text Embeddings Inference container
  serving `unsloth/embeddinggemma-300m` by default. 768-dim vectors;
  dimension locked at install via `app.embedding_dimension` GUC.
- **RabbitMQ** — for inter-process messaging used by the maintenance
  worker.
- **Workers** — maintenance worker default-on; heartbeat worker
  opt-in via the `heartbeat` Compose profile (requires an LLM
  provider).
- **MCP server** — `sill-mcp` console script exposes recall,
  recall_preview, recall_batch, remember, hydrate, get_goals,
  get_drives, get_identity, get_worldview, and the chorus stage
  endpoint.
- **Console scripts** — `sill`, `sill-mcp`, `sill-worker` via
  pyproject entry points; pipx-installable.
- **Chorus library** — `chorus.stage` for multi-actor model
  orchestration via litellm (gemini, deepseek, anthropic, openai,
  ollama, …). MCP-registered as `mcp__sill__stage`.
- **Claude Code / Codex plugin** with 10 hooks:
  - `spontaneous-recall` — UserPromptSubmit; injects relevant memories
    plus optional episodic-memory excerpts.
  - `track-reuse` — Stop; updates last_reused / reuse_count when
    recalled memories appear in responses.
  - `track-verification` — PostToolUse; supports check-agreement.
  - `attribution-check` — PreToolUse on memory writes; flags
    speaker-boundary drift.
  - `check-agreement` — Stop; flags "you're right" without
    verification.
  - `check-corrections` — UserPromptSubmit; primes verify-before-agree.
  - `response-patterns` — Stop; configurable rule-file detection.
  - `state-language-check` — PreToolUse + Write/Edit; flags borrowed
    embodied-state language in persisted text.
  - `precompact-snapshot` — PreCompact; materializes orientation
    before context compaction.
  - `goodnight-checkpoint` — UserPromptSubmit; writes daily log on
    sleep-related messages. Trigger phrases configurable.
- **Response-pattern rule files** — 7 generic detection patterns
  (agreement, block-hedge, hedging, meta-deflection,
  noted-without-noting, state-language, storage-deference).
- **Seed methodology pack** — 22 probe-validated procedural/semantic
  memories across six categories (inquiry method, epistemic
  discipline, memory hygiene, recall patterns, tool selection,
  identity-shape). Auto-imported on first install via
  `sill seed import`.
- **Install / verify / reset / uninstall scripts** — `./install.sh`
  with `--no-seed`, `--hooks-for <project>`, `--dry-run` flags;
  smoke tests via `./verify.sh`; rollback via `./reset.sh` and
  `./uninstall.sh --keep-data`.
- **Documentation** — `README.md`, `docs/concepts.md`,
  `docs/hooks.md`, `docs/extending.md`.

### Environment

All hooks read `SILL_*` env vars with sensible defaults:
`SILL_DB_CONTAINER`, `SILL_DB_USER`, `SILL_DB_NAME`,
`SILL_PROJECT_ROOT`, `SILL_PLUGIN_DIR`, `SILL_EPISODIC_MEMORY_PATH`,
`SILL_GOODNIGHT_FOCUS_DRIVE`, `SILL_FAKE_EMBEDDINGS` (test only).

### Soft dependencies

- [episodic-memory](https://github.com/obra/superpowers-plugins) —
  conversation-transcript archive; `spontaneous-recall` falls back
  to sill-only if absent.

### Known issues

See `extraction/known-issues.md` in the upstream agi-memory worktree
for the v0.1.0 punch list. Summary:

- `sill` CLI does not auto-load `backend/.env` (workaround: run
  from `backend/` or source `.env` first).
- `sill-mcp` has no `--version` flag.
- Embeddings cold boot logs "404 Not Found" lines from the Candle
  backend — alarming but harmless.

### Credit

Sill v0.1.0 is extracted from the agi-memory research project
(William Taysom + Sili). Schema and recall design have been in
continuous use since late 2025; this release packages a sanitized
core suitable for fresh installs.
