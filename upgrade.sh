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
# Flags:
#   --dry-run     List what would be applied; write nothing.
#   --no-verify   Skip step 5.
#   --help        Show this help.
#
# Restore procedure (manual, deliberate):
#   gunzip -c backups/<file>.sql.gz | docker exec -i <container> psql -U <user> -d <db>

set -euo pipefail

SILL_DIR="$(cd "$(dirname "$0")" && pwd)"
MIGRATIONS_DIR="$SILL_DIR/backend/migrations"
BACKUP_DIR="$SILL_DIR/backups"
CONTAINER="${SILL_DB_CONTAINER:-sill_db}"
DB_USER="${POSTGRES_USER:-sill}"
DB_NAME="${POSTGRES_DB:-sill}"
DRY_RUN=0
NO_VERIFY=0

fail() { printf '  FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf '  pass: %s\n' "$*"; }
say()  { printf '\n=== %s ===\n' "$*"; }
usage() { sed -n '2,22p' "$0"; }

while (( $# )); do
  case "$1" in
    --dry-run)   DRY_RUN=1;   shift ;;
    --no-verify) NO_VERIFY=1; shift ;;
    --help)      usage; exit 0 ;;
    *)           fail "unknown flag: $1 (see --help)" ;;
  esac
done

psql_c() { docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc "$1"; }

say "Preflight"
docker exec "$CONTAINER" true 2>/dev/null \
  || fail "container '$CONTAINER' is not running (set SILL_DB_CONTAINER or start the stack)"
[[ "$(psql_c 'SELECT 1' 2>/dev/null)" == "1" ]] \
  || fail "psql not answering in '$CONTAINER' as user '$DB_USER' db '$DB_NAME'"
pass "db reachable in '$CONTAINER'"

say "Pending migrations"
applied="$(psql_c 'SELECT migration_id FROM schema_migrations ORDER BY migration_id' 2>/dev/null || true)"
pending=()
for f in "$MIGRATIONS_DIR"/[0-9][0-9][0-9]_*.sql; do
  [[ -e "$f" ]] || fail "no migrations found under $MIGRATIONS_DIR"
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
  || fail "pg_dump failed — refusing to touch the schema"
bytes="$(wc -c < "$backup" | tr -d '[:space:]')"
(( bytes > 5000 )) || fail "backup is only ${bytes} bytes — looks truncated; refusing to continue"
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
