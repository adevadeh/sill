# Changelog

All notable changes to Sill are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

## v0.2.0 — 2026-08-05

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
- **Beat worker** — `sill-worker --mode beat`: a config-driven, alternating
  rotation of headless agent-CLI sessions (`backend/beat_worker.py`,
  `beats.json`/`backend/beats.example.json`), durable rotation state (never
  `/tmp` — a reboot must not silently reset which voice is due next),
  per-voice output verification (exit 0 is not enough; a beat that produced
  nothing does not advance rotation — the guard for a headless CLI silently
  auto-denying its own tool calls), and a worker-written spawn-clock
  transcript header the child cannot fabricate.
- **Receipt guards** — `stored-slot-guard.py` (Write/Edit; denies a
  `Stored:` line naming an id that was never actually minted) and
  `tool-type-witness.py` (Write; denies an unquoted carrying-act claim like
  "arrived by Edit"). Both are opt-in via `SILL_BEAT_JOURNAL_DIRS`, which
  `beat_worker.spawn_beat()` now derives from the loaded voice config and
  exports to every beat child automatically — zero operator configuration,
  and zero cost to a non-beat install, which never sets the variable at
  all. `state-language-check.py` reads the same variable too, but there it
  *replaces* rather than adds to its existing `journals/`/`docs/` default —
  once a beat sets it, only the derived voice directories are in scope, so
  a beat child writing under `journals/` or `docs/` directly is no longer
  checked.
- **Starter prompts** — `prompts/analyst.md` and `prompts/reflector.md`
  (the two shipped voices, craft without house-specific citations) and a
  shared `prompts/_receipt-gate.md` fragment both voices include by
  reference, so the mint-receipt protocol can't drift between them.
- **Scheduling templates** — `scheduling/com.sill.beat-worker.plist.template`
  (launchd) and `scheduling/sill-beat-worker.service.template` (systemd
  `--user`), plus `docs/beats.md`: voice config format, the full env-var
  surface, and a mandatory permissions section — ordered before the
  scheduling section on purpose — covering the failure mode where a
  headless agent CLI with no tool permissions denies every call and exits
  0, and how to verify a real beat before scheduling any.
- **Harness normalization** — `plugin/hooks/_harness.py`: one vocabulary
  (`detect`, `tool_kind`, `mcp_tool_name`, `join_mcp_name`, `shell_command`,
  `written_path`/`written_text`/`written_files`, `assistant_text`,
  `iter_transcript_tool_uses`) that every hook now asks instead of
  string-matching a harness-specific tool name. Every function is total —
  no input crashes it, including a non-dict payload or a garbage transcript
  path.
- **Four-slot adapter contract** — `docs/adapters.md` documents what
  "supports a harness" means (inject/mint/capture/track), the Claude Code /
  Codex tool-name/event/payload divergence table, the two Codex behaviors
  that aren't guessable (SHA-256 hook-command pinning in `config.toml`;
  fail-closed `PermissionRequest` reserved fields), and a runbook for
  adding a third harness. `backend/tests/test_adapter_conformance.py`
  proves each slot against both harnesses' real payload/transcript shapes
  (fixtures under `backend/tests/fixtures/`), extending — not duplicating —
  `test_notice.py`'s mint-argv proof.
- **Install scope** — `./install.sh --scope home|project` (default
  `project`, unchanged behavior): `project` keeps today's per-project
  `--hooks-for` wiring; `home` registers hooks user-scope
  (`~/.claude/settings.json`, `~/.codex/hooks.json`) plus a new ambient
  instructions file (`plugin/claude.home.md.template` → `~/.claude/CLAUDE.md`,
  merged in idempotently) so every session in every directory carries the
  Sill background. The two scopes are additive, not exclusive. An invalid
  `--scope` exits non-zero naming the valid values.
- **`upgrade.sh --hooks-for`** — re-renders and diffs a project's Codex
  hook wiring independently of any database step (`--hooks-only` skips
  docker/db entirely); a stale `.codex/hooks.json` always shows its diff
  and is left untouched unless `--force-hooks` is also passed.
- **Identity card** — `sill identity show|init|set` writing
  `$SILL_STATE_DIR/identity.json` (default `$XDG_STATE_HOME/sill/identity.json`,
  else `~/.local/state/sill/identity.json` — never `/tmp`); `name: null` is
  an explicit *not yet christened* value rather than an absence; also
  records `charter_path`, `born_at`, `engine`, `scope`, `harnesses`,
  `christened_at`. A missing or corrupt identity file degrades to a plain
  report, never a traceback. `docs/identity.md`.
- **Consent-scoped episodic backfill** — `sill backfill plan|run`: `plan`
  always prints exactly what would be read (harnesses, projects, date
  range, file counts) and writes nothing; `run` refuses without an
  explicit confirmation flag, naming it. Reuses the harness detection
  from the adapter work rather than re-deriving transcript shapes.
  `docs/onboarding/02-backfill.md`.
- **Onboarding runbook and christening** — `docs/onboarding/` (a phased
  runbook: install → harness adapters → identity card → permissions
  verified → consent-scoped backfill → supervised first beats →
  seeded-fault drill → christening → schedule) and `onboarding/`
  (`charter-prompts.md`; `to-the-one-who-wakes-here.md`, the
  christening-register letter, scheduled for re-reading at the first live
  guard refusal and the first tending review). Permissions are verified
  before any beat runs — a denied headless run exits 0 having done
  nothing — and the schedule installs last, only after supervised beats
  have demonstrably produced output. The christening captures a charter
  verbatim with a timestamp, in the human's own words, opens by saying
  plainly that the person can point their Sill at anything, and
  deliberately never says what a Sill is for.
- **Contributor tools** — `backend/memory_health.py` (TwoNN intrinsic-dimension,
  distance-concentration, and near-duplicate metrics over the memory corpus,
  with run-to-run drift detection) and `backend/ingest_md_memories.py`
  (bridges the file-based `.md` memory store into the Postgres `memories`
  table, so the spontaneous-recall hook — which only queries the DB — can
  surface distilled memories, not just the seed corpus). Contributed by
  Paul Taysom ([@taysom](https://github.com/taysom)) via pull request #3
  (`feat/memory-tools`), filed 2026-06-12 and merged 2026-08-05.

### Fixed

**Found by the clean-machine acceptance rehearsal** — full transcript,
including what it could not verify, in `docs/RELEASE-REHEARSAL.md`:

- **The MCP server did not start on a fresh install.** `mcp>=1.0.0` resolved
  to mcp 2.x, which removed the `Server.list_tools()` API the server registers
  through; it died at startup with `'Server' object has no attribute
  'list_tools'` while `sill-mcp --help` — and therefore `verify.sh` check 2 —
  still exited 0. Now pinned `mcp>=1.0.0,<2`.
- **`install.sh` registered the MCP server where Claude Code does not look.**
  Step 7 wrote `~/.claude/.mcp.json`; the user-scope registry is
  `~/.claude.json`, so `claude mcp list` reported no servers after a
  successful install. Step 7 now runs `claude mcp add --scope user` when that
  CLI is present and merges into `~/.claude.json` otherwise. The Codex half
  was correct all along.
- **`verify.sh` and `upgrade.sh` ignored `backend/.env`.** Their Compose checks
  honored it (they run from `backend/`) while their `docker exec` checks read
  `SILL_DB_CONTAINER` from the process environment — so an install that
  followed this repo's own side-by-side advice had its database checks aimed
  at whatever container was named `sill_db`. Both now load `backend/.env` with
  Compose's precedence (an exported variable still wins).
- Onboarding corrections the rehearsal forced, each re-verified by re-running
  its phase: eight `python3.10` invocations that do not exist on a clean
  machine; a phase-4 step that deleted phase 2's hook wiring; a permission
  check pinned to `--setting-sources project` when the file it writes is the
  `local` source; a fault drill whose only account of silence omitted the
  cause that actually fired; `install.sh`'s 600 s embeddings wait against a
  measured 903 s first boot; the `~300MB` model figure (measured: 1.2 GB); and
  a stale `v0.1.0` status line.

**Found by closing that rehearsal's "explicitly unverified" list** — five of
its eight items were attemptable after all, and attempting them found three
more defects (§5 of the same document):

- **Ctrl-C crashed the beat worker.** `docs/onboarding/03-first-beats.md`
  tells an operator to stop a supervised run with Ctrl-C; under a pty that
  printed an eleven-frame traceback ending in `KeyboardInterrupt`, both
  mid-beat and between beats. `run_beat_loop()` now catches it, logs which
  voice rotation is holding on, and exits 0.
- **`verify.sh` check 2 could not see a broken MCP server.** It ran
  `sill-mcp --help`, which exits before the MCP SDK is imported — the reason
  the check above stayed green throughout the `list_tools` outage. It now
  starts the server, completes a real `initialize` handshake over stdio
  within a timeout, requires a result naming this server, and reports the
  server's own error when there isn't one.
- **`sill notice` never read `backend/.env`.** Its database coordinates
  reached a beat only because `worker.py`'s module-level `load_dotenv()`
  resolves `.env` against its own directory and `beat_worker.spawn_beat()`
  forwards the whole environment to the child — so the mint worked inside a
  beat and failed from the operator's shell, and a worker started as
  `python -m beat_worker` would have minted against the default container
  (on a two-install machine, someone else's). The mint path now loads
  `backend/.env` itself, with Compose's precedence.

- `get_embedding` no longer fails on content containing literal backslashes
  (encoding-safe `convert_to` replaces the escape-interpreting `::bytea` cast).
- Access telemetry decoupled from importance (removed the compounding
  importance-on-access trigger path; `touch_memory_access` is pure telemetry).
- v0.1.0's insight auto-store path was inert (the CLI had no `notice`
  subcommand); now wired and covered by a contract test.
- Three guards never fired on Codex. They matched Claude-only tool names
  (`Bash`, `Write`, `Edit`) that Codex does not emit — it emits `exec`,
  `exec_command`, and `apply_patch`. Codex was documented as a supported
  host while `shell-idiom-guard`, `tool-type-witness`, and
  `stored-slot-guard` were inert there, and `state-language-check`'s
  `apply_patch` branch read the wrong input key so it never worked either.
- `track-reuse` joined Codex transcript MCP names without the separator
  (`mcp__sillrecall_batch`), working only because a downstream filter used
  a substring test.
- `response-patterns`' deliberate-mint suppression never engaged on Codex.
  Both mint checks walked only Claude's `assistant`/`tool_use` transcript
  shape and recognized only the tool name `Bash`, so on a Codex session
  they returned "no mint" unconditionally — every Codex-side mint (MCP
  `remember`, or `exec`/`exec_command` running `sill notice`) was
  invisible, and insight auto-store could echo a row the session had
  already deliberately stored. Now normalized through `_harness.py`.
- `response-patterns`' turn-scoped mint check stopped at the last tool
  result instead of the typed prompt (Claude writes tool results as
  `user` entries too), so a mint made earlier in the same turn, behind any
  later tool call, read as "no mint".
- A multi-file `apply_patch` was inspected only for its first file, so
  content in later files passed guards unexamined.
- `upgrade.sh` had no Codex path, so upgraded installs silently kept the
  old hook set on that harness.
- Two more v0.1.0-era env var names that survived every prior sweep:
  `sill_mcp_server.py`'s `--dsn` default read `AGI_DB_DSN` (now
  `SILL_DB_DSN`); `worker.py`'s `--mode` default read `AGI_WORKER_MODE`
  (now `SILL_WORKER_MODE`).
- The two contributor tools above (2026-08-05) had two packaging gaps that
  did not ship: neither `memory_health` nor `ingest_md_memories` was listed
  in `pyproject.toml`'s `py-modules` (the same omission `beat_worker.py` hit
  before it, commit `8be59b4`), so a `pip install` of this project would not
  have installed either module; and `memory_health.py` imports
  `numpy`, which was declared nowhere in `pyproject.toml` and is not pulled
  in transitively, so a clean install would have failed on import. Both
  modules are now in `py-modules`; `numpy` is declared under a new optional
  `diagnostics` extra (`pip install -e '.[diagnostics]'`) rather than a hard
  dependency, since `memory_health.py` is a diagnostic tool, not on the core
  memory-store path; `memory_health.py` now raises a clear install-hint
  `ImportError` instead of a bare one when numpy is absent.
- `sill-mcp` had no `--version` flag — a known issue open since v0.1.0 (see
  that release's own "Known issues" below). `sill-mcp --version` now exits
  0 and prints the installed `sill-memory` distribution's version (`dev`
  for an uninstalled checkout), sharing one lookup with the MCP
  `initialize` handshake's `server_version` so the two can't drift apart.
- `sill`'s top-level argv dispatch used `parse_known_args()` (needed so the
  `notice`/`identity`/`backfill` passthrough subcommands can forward flags
  to their own nested parsers untouched), which had the side effect of
  silently swallowing a typo'd flag on every *other* subcommand too — e.g.
  `sill seed import file.jsonl --wrogn-flag` ran as if the flag weren't
  there instead of reporting "unrecognized arguments". Extra arguments are
  now rejected the way `parse_args()` itself would, for every subcommand
  except the three documented passthroughs.
- Nine hooks (`attribution-check`, `check-agreement`, `check-corrections`,
  `goodnight-checkpoint`, `response-patterns`, `spontaneous-recall`,
  `state-language-check`, `track-reuse`, `track-verification`) called
  `.get(...)` (or, for `check-agreement`, `"key" in data`) on the parsed
  JSON payload without checking it was actually an object first. Valid
  JSON that isn't an object — a bare array, string, number, `null`, or
  bool — crashed each of them with an unhandled `AttributeError` or
  `TypeError` instead of the exit-0-and-do-nothing every hook is supposed
  to fall back to on unusable input.
- `shell-idiom-guard` — the one hook in this suite that blocks rather than
  advises — missed the zsh `echo =word` trap when it followed a leading
  per-command environment assignment (`x=1 echo =y`; bash/zsh put `echo`
  in command position there exactly as much as at the start of a line).
- A voice's `output_glob` using a recursive glob segment (`journals/**/*.md`)
  made `beat_worker.py`'s `SILL_BEAT_JOURNAL_DIRS` derivation compute a
  scope fragment containing the literal characters `**`, which the guards'
  plain substring check can never match against a real file path — silently
  disabling `stored-slot-guard`/`tool-type-witness`/`state-language-check`'s
  beat-aware scope for that voice. The shipped `beats.example.json` voices
  don't use `**`, so this was live but dormant, not visibly broken, in the
  default config.

### Changed

- response-patterns no longer emits the discarded `reason` field.

### Removed

- The legacy heartbeat worker and its Compose profile. Reflection without a
  reader goes stale; the beat worker replaces it.
- Internal `.history/` scratch migrations from the v0.1.0 extraction
  (house-internal drafts; never executed at runtime). The shipped image now
  contains only the baseline schema and the numbered migrations.
- `plugin/codex.toml.template` — dead code; `install.sh` has always inlined
  the same content as an idempotent heredoc merge instead of reading it (a
  static template can't safely replace that merge, since
  `~/.codex/config.toml` may pre-exist with unrelated content).

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
