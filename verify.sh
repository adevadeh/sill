#!/usr/bin/env bash
# verify.sh — four smoke checks for an installed Sill.
#
# Exits 0 only if all four checks pass. Designed to be safe to re-run.
#
# Checks:
#   1. docker compose ps shows db + embeddings healthy.
#   2. sill-mcp --help exits 0.
#   3. SELECT count(*) FROM memories returns >= 22 (seed loaded).
#   4. plugin/hooks/response-patterns.py exits 0 on a canned Stop event.

set -euo pipefail

SILL_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$SILL_DIR/backend/docker-compose.yml"
CONTAINER="${SILL_DB_CONTAINER:-sill_db}"
DB_USER="${POSTGRES_USER:-sill}"
DB_NAME="${POSTGRES_DB:-sill}"

fail() {
  printf '  FAIL: %s\n' "$*" >&2
  exit 1
}

pass() {
  printf '  pass: %s\n' "$*"
}

say() {
  printf '\n=== %s ===\n' "$*"
}

# --- check 1 -------------------------------------------------------------------
say "Check 1/4: docker compose services healthy"
for svc in db embeddings; do
  status="$(docker compose -f "$COMPOSE_FILE" ps --format json "$svc" 2>/dev/null | python3 -c '
import json, sys
raw = sys.stdin.read().strip()
if not raw:
    print("missing"); sys.exit()
for line in raw.splitlines():
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        continue
    print(obj.get("Health") or obj.get("State") or "unknown")
    break
' 2>/dev/null || true)"
  if [[ "$status" != "healthy" ]]; then
    fail "$svc is '$status', expected 'healthy'"
  fi
  pass "$svc is healthy"
done

# --- check 2 -------------------------------------------------------------------
say "Check 2/4: sill-mcp --help"
if ! command -v sill-mcp >/dev/null 2>&1; then
  fail "sill-mcp not on PATH (is the backend installed and ~/.local/bin on PATH?)"
fi
if sill-mcp --help >/dev/null 2>&1; then
  pass "sill-mcp --help exits 0"
else
  fail "sill-mcp --help returned non-zero"
fi

# --- check 3 -------------------------------------------------------------------
say "Check 3/4: seed loaded (memories count >= 22)"
count="$(docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
  "SELECT count(*) FROM memories WHERE archived_at IS NULL" 2>/dev/null || echo 0)"
count="$(printf '%s' "$count" | tr -d '[:space:]')"
if [[ -z "$count" || "$count" -lt 22 ]]; then
  fail "memories count is '$count', expected >= 22 (run 'sill seed import seed/methodology.jsonl')"
fi
pass "memories count is $count"

# --- check 4 -------------------------------------------------------------------
say "Check 4/4: response-patterns hook parses a canned Stop event"
# The hook reads last_assistant_message (Codex Stop shape) and exits 0 when
# the text contains no flagged patterns. We expect 0 here.
canned='{"hook_event_name":"Stop","last_assistant_message":"Verify smoke check: a plain sentence with no flagged patterns."}'
if printf '%s' "$canned" | python3 "$SILL_DIR/plugin/hooks/response-patterns.py" >/dev/null 2>&1; then
  pass "response-patterns.py exit 0 on canned Stop event"
else
  fail "response-patterns.py rejected a canned Stop event"
fi

printf '\nAll checks passed.\n'
