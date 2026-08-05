#!/usr/bin/env bash
# upgrade.sh — bring a data-bearing Sill install up to the current schema level.
#
# Steps:
#   1. Preflight: db container running, psql answering.
#   2. Pending: diff backend/migrations/NNN_*.sql against schema_migrations.
#   3. Backup: pg_dump | gzip to backups/<db>-preupgrade-<UTC>.sql.gz.
#      Aborts if the dump fails or looks truncated. NEVER applies without one.
#   4. Apply each pending migration in numeric order (psql ON_ERROR_STOP),
#      stamping schema_migrations after each success.
#   5. Run ./verify.sh (skip with --no-verify; CI does, verify needs compose).
#
# Every migration is idempotent (IF NOT EXISTS / OR REPLACE / IF EXISTS): a
# failed run is fixed by addressing the cause and re-running this script.
#
# Hook refresh — --hooks-for <path>, optional, independent of the DB steps
# above, and run BEFORE them: re-renders that project's hook wiring so an
# upgrade doesn't silently leave one harness running the old hook set.
#   - <path>/.claude/settings.local.json merges idempotently, same as
#     install.sh: new entries are appended, nothing existing is touched.
#   - <path>/.codex/hooks.json is the fix for a real gap: install.sh only
#     ever writes this file once and silently skips it if it already
#     exists, so a stale Codex hook set otherwise survives every upgrade
#     forever (the Claude side re-merges; the Codex side never did). This
#     diffs the existing file against a fresh render and, if they differ,
#     PRINTS THE DIFF and leaves the file untouched unless you also pass
#     --force-hooks — never a silent overwrite of a file you may have
#     hand-edited. Note also: Codex SHA-256-pins each hook command, so
#     overwriting this file invalidates that trust and Codex will re-prompt
#     for approval on its next run (see docs/adapters.md).
#   - --hooks-only runs just this refresh and skips every DB step,
#     preflight included (no docker/db required) — for refreshing hooks
#     right after `git pull`, independently of when you next run a schema
#     upgrade.
#
# Flags:
#   --hooks-for <path>   Refresh <path>'s hook wiring (see above).
#   --force-hooks        With --hooks-for: overwrite a stale .codex/hooks.json
#                        instead of only showing the diff. Requires --hooks-for.
#   --hooks-only         With --hooks-for: do only the hook refresh; skip
#                        every DB step (no docker/db required). Requires
#                        --hooks-for.
#   --dry-run     List what would be applied; write nothing.
#   --no-verify   Skip step 5.
#   --help        Show this help.
#
# Restore procedure (manual, deliberate; the dump replays into an EMPTY database):
#   docker exec <container> psql -U <user> -d postgres \
#     -c 'DROP DATABASE IF EXISTS <db> WITH (FORCE);' -c 'CREATE DATABASE <db> OWNER <user>;'
#   gunzip -c backups/<file>.sql.gz | docker exec -i <container> psql -U <user> -d <db>
#   Then run ./verify.sh — graph-extension data can be finicky across dump/restore.

set -euo pipefail

SILL_DIR="$(cd "$(dirname "$0")" && pwd)"
MIGRATIONS_DIR="$SILL_DIR/backend/migrations"
BACKUP_DIR="$SILL_DIR/backups"

# Same reasoning as verify.sh's copy: an operator who set SILL_DB_CONTAINER /
# POSTGRES_USER / POSTGRES_DB in backend/.env (the documented way to run two
# stacks side by side) must not have this script back up and migrate a
# different install's database. Compose precedence: exported shell variable
# wins, .env fills in the rest.
load_env_file() {
  local f="$SILL_DIR/backend/.env" line key val
  [[ -f "$f" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"
    val="${line#*=}"
    key="${key//[[:space:]]/}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    [[ -n "${!key+x}" ]] && continue
    val="${val#\"}"; val="${val%\"}"
    val="${val#\'}"; val="${val%\'}"
    export "$key=$val"
  done < "$f"
}
load_env_file

CONTAINER="${SILL_DB_CONTAINER:-sill_db}"
DB_USER="${POSTGRES_USER:-sill}"
DB_NAME="${POSTGRES_DB:-sill}"
DRY_RUN=0
NO_VERIFY=0
HOOKS_FOR=""
FORCE_HOOKS=0
HOOKS_ONLY=0

fail() { printf '  FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf '  pass: %s\n' "$*"; }
say()  { printf '\n=== %s ===\n' "$*"; }
note() { printf '    %s\n' "$*"; }
usage() { sed -n '2,51p' "$0"; }

while (( $# )); do
  case "$1" in
    --dry-run)     DRY_RUN=1;     shift ;;
    --no-verify)   NO_VERIFY=1;   shift ;;
    --hooks-for)
      [[ $# -ge 2 ]] || fail "--hooks-for requires a path"
      HOOKS_FOR="$2"
      shift 2
      ;;
    --force-hooks) FORCE_HOOKS=1; shift ;;
    --hooks-only)  HOOKS_ONLY=1;  shift ;;
    --help)        usage; exit 0 ;;
    *)             fail "unknown flag: $1 (see --help)" ;;
  esac
done

if (( FORCE_HOOKS )) && [[ -z "$HOOKS_FOR" ]]; then
  fail "--force-hooks requires --hooks-for <path>"
fi
if (( HOOKS_ONLY )) && [[ -z "$HOOKS_FOR" ]]; then
  fail "--hooks-only requires --hooks-for <path>"
fi

psql_c() { docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc "$1"; }

# Resolve the interpreter that has the backend deps installed — same
# resolution order as install.sh's sill_python(): pipx venv, then the
# ~/.local/share/sill-venv fallback, then a last-resort bare python3.
sill_python() {
  if command -v pipx >/dev/null 2>&1; then
    local cand="${PIPX_HOME:-$HOME/.local/pipx}/venvs/sill-memory/bin/python"
    [[ -x "$cand" ]] && { printf '%s' "$cand"; return; }
  fi
  local venv_py="$HOME/.local/share/sill-venv/bin/python"
  [[ -x "$venv_py" ]] && { printf '%s' "$venv_py"; return; }
  printf 'python3'
}

# Idempotently merge the template's hooks block into <path>/.claude/settings.local.json.
# Never removes or overwrites an existing entry — only appends missing
# ones — so, unlike the Codex side below, this never needs a diff/confirm
# gate. Same logic as install.sh's _merge_claude_hooks (duplicated rather
# than sourced: these are two independent, standalone operator scripts by
# this repo's convention — see install.sh).
_merge_claude_settings_local() {
  local template="$1" dst="$2" plugin_dir="$3" sill_py="$4"
  if (( DRY_RUN )); then
    note "DRY: would idempotently merge hooks block into $dst"
    return 0
  fi
  mkdir -p "$(dirname "$dst")"
  python3 - "$template" "$dst" "$plugin_dir" "$sill_py" <<'PY'
import json, pathlib, sys
template_path, dst_path, plugin, py = sys.argv[1:5]
template = json.loads(pathlib.Path(template_path).read_text()
                      .replace("{{SILL_PLUGIN_DIR}}", plugin)
                      .replace("{{SILL_PYTHON}}", py))
dst = pathlib.Path(dst_path)
data = {}
if dst.exists():
    try:
        data = json.loads(dst.read_text() or "{}")
    except json.JSONDecodeError:
        print(f"upgrade.sh: {dst} isn't valid JSON; leaving it alone.")
        sys.exit(0)
hooks = data.setdefault("hooks", {})
changed = False
for event, entries in template.get("hooks", {}).items():
    existing = hooks.setdefault(event, [])
    existing_repr = {json.dumps(e, sort_keys=True) for e in existing}
    for entry in entries:
        if json.dumps(entry, sort_keys=True) not in existing_repr:
            existing.append(entry)
            changed = True
if changed:
    dst.write_text(json.dumps(data, indent=2) + "\n")
    print(f"  merged sill hooks into {dst}")
else:
    print(f"  {dst} already has sill hooks; nothing to do")
PY
}

# The fix for the missing Codex path (see the header comment above): render
# the template in memory and compare it against whatever's already at
# <path>/.codex/hooks.json.
#   - missing entirely -> write it (nothing to lose, matches install.sh's
#     first-wiring behavior).
#   - identical -> say so, touch nothing.
#   - different -> print a unified diff; overwrite only if FORCE_HOOKS=1;
#     otherwise leave it untouched and say exactly how to proceed. This is
#     the "informed, not guessing" default the task calls for: the diff is
#     always shown when something's stale, whether or not it's applied.
_refresh_codex_hooks() {
  local template="$1" dst="$2" plugin_dir="$3" sill_py="$4"
  python3 - "$template" "$dst" "$plugin_dir" "$sill_py" "$DRY_RUN" "$FORCE_HOOKS" <<'PY'
import difflib, pathlib, sys

src, dst_path, plugin, py, dry_run, force = sys.argv[1:7]
dry_run = dry_run == "1"
force = force == "1"

rendered = (pathlib.Path(src).read_text()
            .replace("{{SILL_PLUGIN_DIR}}", plugin)
            .replace("{{SILL_PYTHON}}", py))
dst = pathlib.Path(dst_path)

if not dst.exists():
    if dry_run:
        print(f"  DRY: would write fresh {dst}")
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(rendered)
        print(f"  wrote fresh {dst}")
    sys.exit(0)

current = dst.read_text()
if current == rendered:
    print(f"  {dst} already up to date; nothing to do")
    sys.exit(0)

diff_text = "".join(difflib.unified_diff(
    current.splitlines(keepends=True),
    rendered.splitlines(keepends=True),
    fromfile=str(dst),
    tofile=f"{dst} (rendered from current template)"))
print(f"  STALE: {dst} differs from the current template")
print(diff_text)

if dry_run:
    if force:
        print(f"  DRY: would overwrite {dst} (--force-hooks)")
    else:
        print(f"  DRY: would leave {dst} untouched (pass --force-hooks to overwrite)")
    sys.exit(0)

if not force:
    print(f"  leaving {dst} untouched -- it may carry a hand edit.")
    print("  re-run with --force-hooks to overwrite, or apply the diff above by hand.")
    sys.exit(0)

dst.write_text(rendered)
print(f"  overwrote {dst} (--force-hooks)")
print("  NOTE: Codex SHA-256-pins each hook command in ~/.codex/config.toml;")
print("  overwriting this file invalidates that trust. Codex will re-prompt")
print("  for approval on its next run. See docs/adapters.md.")
PY
}

step_hooks_refresh() {
  say "Hook refresh (--hooks-for $HOOKS_FOR)"
  local target
  target="$(cd "$HOOKS_FOR" && pwd)"
  local plugin_dir="$SILL_DIR/plugin"
  local template="$SILL_DIR/plugin/codex.hooks.json.template"
  local sill_py
  sill_py="$(sill_python)"

  _merge_claude_settings_local "$template" "$target/.claude/settings.local.json" "$plugin_dir" "$sill_py"
  _refresh_codex_hooks "$template" "$target/.codex/hooks.json" "$plugin_dir" "$sill_py"
}

# Runs (if requested) before any DB step, deliberately: --hooks-only must be
# able to complete with zero docker/db calls, and a combined run should
# refresh hooks even if the DB step that follows happens to fail.
if [[ -n "$HOOKS_FOR" ]]; then
  step_hooks_refresh
  if (( HOOKS_ONLY )); then
    printf '\nHooks-only run complete for %s.\n' "$HOOKS_FOR"
    exit 0
  fi
fi

say "Preflight"
docker exec "$CONTAINER" true 2>/dev/null \
  || fail "container '$CONTAINER' is not running (set SILL_DB_CONTAINER or start the stack)"
[[ "$(psql_c 'SELECT 1' 2>/dev/null)" == "1" ]] \
  || fail "psql not answering in '$CONTAINER' as user '$DB_USER' db '$DB_NAME' — start the stack (cd backend && docker compose up -d) or set SILL_DB_CONTAINER/POSTGRES_USER/POSTGRES_DB to match your install"
pass "db reachable in '$CONTAINER'"

say "Pending migrations"
applied="$(psql_c 'SELECT migration_id FROM schema_migrations ORDER BY migration_id' 2>/dev/null || true)"
pending=()
for f in "$MIGRATIONS_DIR"/[0-9][0-9][0-9]_*.sql; do
  [[ -e "$f" ]] || fail "no migrations found under $MIGRATIONS_DIR — run this from a full checkout of the sill repo (backend/migrations/ is a repo asset)"
  id="$(basename "$f" | cut -c1-3)"
  grep -qx "$id" <<<"$applied" || pending+=("$f")
done
if (( ${#pending[@]} == 0 )); then
  pass "schema already current (nothing pending)"
  exit 0
fi
for f in "${pending[@]}"; do printf '  pending: %s\n' "$(basename "$f")"; done
if (( DRY_RUN )); then pass "dry run — nothing written"; exit 0; fi

say "Backup (never upgrade without one)"
mkdir -p "$BACKUP_DIR"
backup="$BACKUP_DIR/${DB_NAME}-preupgrade-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" | gzip > "$backup" \
  || fail "pg_dump failed — nothing was applied; check 'docker logs $CONTAINER' and free disk space, then re-run"
bytes="$(wc -c < "$backup" | tr -d '[:space:]')"
(( bytes > 5000 )) || fail "backup is only ${bytes} bytes — looks truncated; nothing was applied. Check free disk space, inspect $backup, then re-run"
pass "backup at $backup (${bytes} bytes)"

say "Apply"
psql_c "CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id text PRIMARY KEY,
    applied_at   timestamptz NOT NULL DEFAULT now(),
    source       text NOT NULL DEFAULT 'upgrade.sh')" >/dev/null
psql_c "COMMENT ON TABLE schema_migrations IS 'Applied schema migrations. Fresh installs are stamped by initdb; upgrades are stamped by upgrade.sh. verify.sh check 5 compares this against backend/migrations/.'" >/dev/null
for f in "${pending[@]}"; do
  id="$(basename "$f" | cut -c1-3)"
  printf '  applying %s\n' "$(basename "$f")"
  docker exec -i "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 < "$f" >/dev/null \
    || fail "migration $id failed. Nothing after it was applied. The pre-upgrade backup is at $backup"
  psql_c "INSERT INTO schema_migrations (migration_id) VALUES ('$id') ON CONFLICT DO NOTHING" >/dev/null
  pass "applied $id"
done

if (( ! NO_VERIFY )); then
  say "Verify"
  "$SILL_DIR/verify.sh"
fi

printf '\nUpgrade complete: %s migrations applied. Backup: %s\n' "${#pending[@]}" "$backup"
