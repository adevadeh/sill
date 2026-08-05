# Consent-Scoped Episodic Backfill

`sill backfill plan|run` scans harness transcripts already on disk —
Claude Code and/or Codex session logs — and, only on explicit consent,
copies the ones in scope into a durable, undoable archive. This is
usually a new instance's first acquaintance with the person running it:
their working life, half-finished thoughts, other people's names,
whatever they typed at 2am. That's why this document exists as its own
onboarding phase rather than a line in a bigger runbook.

## Why it's shaped this way

**Consent granularity is the feature, not a formality.** Reading
someone's transcripts is intimate. So the tool is built around one hard
split:

- **`plan`** prints exactly what *would* be read — harnesses, projects,
  date range, file counts, paths — and writes **nothing**. Not a file, not
  a log line, not a database row. It only ever `stat`s candidate files
  (path, modified date, size); it never opens their content.
- **`run`** is the one command that actually opens transcript content,
  and it refuses outright without an explicit `--confirm` flag.
- **The human chooses harnesses, projects, and date range.** A harness or
  project not selected is never touched — not filtered out afterward, but
  never even listed on disk in the first place.

`plan` is meant to always precede `run` as a discipline — read the report,
decide the scope, then confirm it. That ordering is **not** mechanically
enforced, and can't be: `plan` leaving no trace to check against is the
same fact that makes "writes nothing" true in the first place.

## What gets scanned

| Harness | On-disk layout | "Project" |
|---|---|---|
| **Claude Code** | `<home>/.claude/projects/<encoded-cwd>/*.jsonl` — one directory per project | The encoded directory name itself, e.g. `-Users-alex-code-orrery` (the cwd with `/` replaced by `-` — Claude Code's own convention, also documented in `docs/beats.md` and `plugin/hooks/response-patterns.py`). Not decoded back to a real path: a name that itself contains dashes makes that reversal ambiguous, so the raw directory name is shown and used as-is rather than guessed at. |
| **Codex** | `<home>/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-*.jsonl` — sharded by calendar date, not by project | Codex records no per-project directory on disk at all (see `docs/adapters.md`'s divergence table), so every scanned Codex session is grouped under one bucket, `codex-sessions`. Harness- and date-range scoping still apply to it in full; only *per-project* scoping doesn't exist for this harness. |

Both harness roots resolve under a single `--home` (default `$HOME`) —
override it to point at a fixture tree or a non-default install; see
"Verifying this yourself" below for why every example in this document
does exactly that rather than trusting the real home directory.

## Scoping flags

Every flag below works on both `plan` and `run` — pass the same scope to
each so `run` does what `plan` just showed you.

| Flag | Meaning | Default |
|---|---|---|
| `--home PATH` | Home directory harness roots resolve under | `$HOME` |
| `--harnesses A,B` | Comma-separated harnesses to scope to (`claude`, `codex`) | every known harness |
| `--projects A,B` | Comma-separated project names to scope to (get exact names from `plan` first) | every project within the selected harnesses |
| `--since YYYY-MM-DD` | Only files last modified on or after this date | unbounded |
| `--until YYYY-MM-DD` | Only files last modified on or before this date | unbounded |
| `--confirm` | `run` only. Required. Without it, `run` refuses and does nothing. | off |

**A real gotcha, not a hypothetical one:** Claude Code project names
always start with `-` (the encoded-cwd convention above). Passed as
`--projects -Users-alex-code-orrery` with a space, `argparse` reads
`-Users-alex-code-orrery` as another flag and fails with `expected one
argument` — a standard long-option convention across most CLI tools when
a value starts with `-`, not a bug specific to this one. Use the `=` form
instead: `--projects=-Users-alex-code-orrery`. Verify both for yourself:

```bash
cd backend
# space form: fails, as documented above
python3 -m scripts.backfill_scan run --projects -Users-alex-code-orrery --confirm
# -> sill backfill run: error: argument --projects: expected one argument

# '=' form: works
python3 -m scripts.backfill_scan run --home /some/fixture --projects=-Users-alex-code-orrery --confirm
```

## Commands

### `sill backfill plan`

Reports scope; changes nothing. Sample output, against a fixture home
with two Claude projects and one Codex session (dates shown are each
file's own modified date, `--home` and file paths abbreviated for
readability — an unabbreviated run is exact, not approximate, see
"Verifying this yourself"):

```
$ sill backfill plan --home /path/to/home
sill backfill plan — reads no transcript content and writes nothing.
home:      /path/to/home
harnesses: requested=claude,codex  scanned=claude, codex
date range: since=(unbounded) until=(unbounded)

claude: project -Users-alex-code-lighthouse  (1 file)
    - /path/to/home/.claude/projects/-Users-alex-code-lighthouse/session-b.jsonl  (modified 2026-06-01, 187 bytes)
claude: project -Users-alex-code-orrery  (1 file)
    - /path/to/home/.claude/projects/-Users-alex-code-orrery/session-a.jsonl  (modified 2026-07-20, 441 bytes)
codex: project codex-sessions  (1 file)
    note: Codex does not shard sessions by project directory on disk (see docs/adapters.md's divergence table) — every scanned Codex session is grouped under this one bucket. Use --since/--until to narrow it.
    - /path/to/home/.codex/sessions/2026/07/30/rollout-2026-07-30T00-00-00-demo.jsonl  (modified 2026-07-30, 194 bytes)

Total: 3 files across 3 project(s) would be read by 'sill backfill run --confirm' with this same scope.
Nothing has been read, written, stored, or logged by this command.
```

Narrow the scope before ever running anything:

```
$ sill backfill plan --home /path/to/home --harnesses claude --since 2026-07-01
sill backfill plan — reads no transcript content and writes nothing.
home:      /path/to/home
harnesses: requested=claude  scanned=claude
date range: since=2026-07-01 until=(unbounded)

claude: project -Users-alex-code-orrery  (1 file)
    - /path/to/home/.claude/projects/-Users-alex-code-orrery/session-a.jsonl  (modified 2026-07-20, 441 bytes)

Total: 1 file across 1 project(s) would be read by 'sill backfill run --confirm' with this same scope.
Nothing has been read, written, stored, or logged by this command.
```

Note what changed: `codex` drops out of `scanned=` entirely (its root was
never listed — see "an unselected harness is never scanned" in
`backend/tests/test_backfill_scan.py`), and the lighthouse project drops
out because its file's modified date is before `--since`.

### `sill backfill run`

Without `--confirm`, `run` refuses and touches nothing — not the scanned
tree, not the archive, not even the state directory:

```
$ sill backfill run --home /path/to/home
backfill run: refusing to read or archive anything without --confirm. Run 'sill backfill plan' first with the same scope flags to see exactly what this would read, then re-run with --confirm once you have reviewed it.
$ echo $?
64
```

With `--confirm`, `run` re-runs the same scope resolution `plan` used,
then reads and archives each file in scope:

```
$ sill backfill run --home /path/to/home --harnesses claude --projects=-Users-alex-code-orrery --confirm
backfill run: archived 1 file(s) to /path/to/state/backfill/20260805T160351222733Z
backfill run: manifest at /path/to/state/backfill/20260805T160351222733Z/manifest.json
To undo: rm -rf /path/to/state/backfill/20260805T160351222733Z
```

## What it stores

`run` writes to `$SILL_STATE_DIR/backfill/<run_id>/` — the same state-dir
resolution `sill identity` uses (`$SILL_STATE_DIR`, else
`$XDG_STATE_HOME/sill`, else `~/.local/state/sill`; see `docs/identity.md`
— **never `/tmp`**), one new timestamped directory per `run`. That
directory holds:

- **A verbatim copy of every archived file**, at
  `<run_id>/<harness>/<project>/<original-filename>` — the original
  transcript, byte-for-byte, not a summary. Nothing here is lossy-
  compiled: a charter or a memory that gets summarized loses the sentence
  someone will need later, and the same reasoning applies to the raw
  material a later step might read from this archive.
- **`manifest.json`** — one receipt per archived file (`harness`,
  `project`, `source_path`, `archived_path`, `mtime_date`, `size_bytes`,
  and `tool_use_count`, the last one computed by re-using
  `plugin/hooks/_harness.py`'s own `iter_transcript_tool_uses` rather than
  a second, hand-rolled transcript parser — the same function Plan 4's
  adapter-conformance tests exercise), plus the run's own scope (`home`,
  `requested_harnesses`, `scanned_harnesses`, `requested_projects`,
  `since`, `until`) and `errors` for any file that could not be copied.

Sample `manifest.json`, for the single-file `run` above:

```json
{
  "run_id": "20260805T160351222733Z",
  "created_at": "2026-08-05T16:03:51.223590+00:00",
  "home": "/path/to/home",
  "requested_harnesses": ["claude"],
  "scanned_harnesses": ["claude"],
  "requested_projects": ["-Users-alex-code-orrery"],
  "since": null,
  "until": null,
  "files": [
    {
      "harness": "claude",
      "project": "-Users-alex-code-orrery",
      "source_path": "/path/to/home/.claude/projects/-Users-alex-code-orrery/session-a.jsonl",
      "archived_path": "/path/to/state/backfill/20260805T160351222733Z/claude/-Users-alex-code-orrery/session-a.jsonl",
      "mtime_date": "2026-07-20",
      "size_bytes": 441,
      "tool_use_count": 1
    }
  ],
  "errors": []
}
```

**What this does not do.** `backfill_scan.py` has no database dependency
at all — no SQL driver import, no memory-store query call anywhere in it
(checked directly against the module's own source by
`test_module_has_no_database_dependency`). This archive is the consented,
undoable raw material; turning it into queryable episodic memory rows is
a separate concern, deliberately left to a later step. What exists today
is exactly what the brief for this tool asked for: a scan, a consent
gate, and an archive — not a full ingestion pipeline.

## How to undo it

The archive is a plain directory tree. There is no special "undo"
command because none is needed — the receipt `run` prints names the exact
path to remove:

```bash
rm -rf /path/to/state/backfill/20260805T160351222733Z
```

That removes the copies and the manifest. It does **not** touch the
original transcripts under `--home` (`.claude/projects/...` or
`.codex/sessions/...`) — `run` only ever reads and copies from there,
never writes back. Removing an archive directory is independent of any
other run's archive: each `run` gets its own fresh timestamped directory,
so undoing one backfill never disturbs another.

To remove every backfill archive ever produced, remove the parent
directory instead:

```bash
rm -rf "$(cd backend && python3 -c 'from scripts.identity_card import default_state_dir; print(default_state_dir())')/backfill"
```

## Verifying this yourself

Every claim above is checkable without touching a real `~/.claude` or
`~/.codex` — build a throwaway fixture tree and point `--home` at it:

```bash
cd backend
DEMO="$(mktemp -d)"
mkdir -p "$DEMO/home/.claude/projects/-demo-project"
echo '{"type":"user","message":{"role":"user","content":"hi"}}' \
  > "$DEMO/home/.claude/projects/-demo-project/session.jsonl"

SILL_STATE_DIR="$DEMO/state" python3 -m scripts.backfill_scan plan --home "$DEMO/home"
# -> lists -demo-project, 1 file; nothing under $DEMO/state yet — it was
#    never created, not merely left empty (note $DEMO/state is NOT among
#    the directories the mkdir -p above creates):
ls "$DEMO/state" 2>&1   # -> ls: .../state: No such file or directory

SILL_STATE_DIR="$DEMO/state" python3 -m scripts.backfill_scan run --home "$DEMO/home" --confirm
find "$DEMO/state/backfill" -type f   # -> the copy + manifest.json

rm -rf "$DEMO"   # the fixture tree, and everything backfill wrote, gone
```

The full automated version of this same discipline —fixture trees only,
a real `HOME`-and-`SILL_STATE_DIR`-overridden subprocess check, and an
explicit assertion that an unselected harness's discoverer is never even
called — is `backend/tests/test_backfill_scan.py`.
