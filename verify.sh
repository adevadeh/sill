#!/usr/bin/env bash
# verify.sh — six smoke checks for an installed Sill.
#
# Exits 0 only if all six checks pass. Designed to be safe to re-run.
#
# Checks:
#   1. docker compose ps shows db + embeddings healthy.
#   2. sill-mcp --help exits 0.
#   3. SELECT count(*) FROM memories returns >= 22 (seed loaded).
#   4. plugin/hooks/response-patterns.py exits 0 on a canned Stop event.
#   5. schema_migrations matches backend/migrations/ (schema level current).
#   6. adapter conformance: the four-slot contract (docs/adapters.md) holds
#      on both harnesses' fixture shapes.

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

note() {
  printf '    %s\n' "$*"
}

say() {
  printf '\n=== %s ===\n' "$*"
}

# --- check 1 -------------------------------------------------------------------
say "Check 1/6: docker compose services healthy"
for svc in db embeddings; do
  status="$(cd "$SILL_DIR/backend" && docker compose -f "$COMPOSE_FILE" ps --format json "$svc" 2>/dev/null | python3 -c '
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
say "Check 2/6: sill-mcp --help"
if ! command -v sill-mcp >/dev/null 2>&1; then
  fail "sill-mcp not on PATH (is the backend installed and ~/.local/bin on PATH?)"
fi
if sill-mcp --help >/dev/null 2>&1; then
  pass "sill-mcp --help exits 0"
else
  fail "sill-mcp --help returned non-zero"
fi

# --- check 3 -------------------------------------------------------------------
say "Check 3/6: seed loaded (memories count >= 22)"
count="$(docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
  "SELECT count(*) FROM memories WHERE archived_at IS NULL" 2>/dev/null || echo 0)"
count="$(printf '%s' "$count" | tr -d '[:space:]')"
if [[ -z "$count" || "$count" -lt 22 ]]; then
  fail "memories count is '$count', expected >= 22 (run 'sill seed import seed/methodology.jsonl')"
fi
pass "memories count is $count"

# --- check 4 -------------------------------------------------------------------
say "Check 4/6: response-patterns hook parses a canned Stop event"
# The hook reads last_assistant_message (Codex Stop shape) and exits 0 when
# the text contains no flagged patterns. We expect 0 here.
canned='{"hook_event_name":"Stop","last_assistant_message":"Verify smoke check: a plain sentence with no flagged patterns."}'
if printf '%s' "$canned" | python3 "$SILL_DIR/plugin/hooks/response-patterns.py" >/dev/null 2>&1; then
  pass "response-patterns.py exit 0 on canned Stop event"
else
  fail "response-patterns.py rejected a canned Stop event"
fi

# --- check 5 -------------------------------------------------------------------
say "Check 5/6: schema level current"
want="$(ls "$SILL_DIR"/backend/migrations/[0-9][0-9][0-9]_*.sql 2>/dev/null | wc -l | tr -d '[:space:]' || echo 0)"
have="$(docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
  "SELECT count(*) FROM schema_migrations" 2>/dev/null | tr -d '[:space:]' || echo 0)"
if [[ -z "$have" || "$have" -lt "$want" ]]; then
  fail "schema_migrations has ${have:-0} of $want migrations (run ./upgrade.sh)"
fi
speaker_col="$(docker exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
  "SELECT count(*) FROM information_schema.columns WHERE table_name='memories' AND column_name='speaker'" 2>/dev/null | tr -d '[:space:]' || echo 0)"
if [[ "$speaker_col" != "1" ]]; then
  fail "memories.speaker column missing despite stamp — run ./upgrade.sh and report if it recurs"
fi
pass "schema at level $have/$want; speech-act columns present"

# --- check 6 -------------------------------------------------------------------
say "Check 6/6: adapter conformance (inject/mint/capture/track, both harnesses)"
# The full proof (docs/adapters.md) is the backend test suite's own
# conformance file plus the mint-slot test it deliberately doesn't duplicate
# (see that doc's slot-2 section for why the split). pytest is a dev-only
# extra (backend/pyproject.toml's [dev] group), so it may not be present on
# an end-user install — degrade to a direct, dependency-free check of the
# capture slot (the one part cheap to prove with nothing but the stdlib)
# rather than skipping the check silently.
if python3 -c "import pytest" >/dev/null 2>&1; then
  if (cd "$SILL_DIR/backend" && python3 -m pytest \
        tests/test_adapter_conformance.py \
        "tests/test_notice.py::test_auto_store_argv_parses_for_both_harness_calling_shapes" \
        -q); then
    pass "adapter conformance suite green (pytest)"
  else
    fail "adapter conformance suite failed — see pytest output above, and docs/adapters.md"
  fi
else
  note "pytest not on PATH (dev-only extra: pip install -e backend[dev] for the full four-slot suite) — running a direct capture-slot check instead"
  if python3 - "$SILL_DIR" <<'PY'
import importlib.util
import sys
from pathlib import Path

root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("_harness", root / "plugin" / "hooks" / "_harness.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

for name in ("claude-transcript.jsonl", "codex-rollout.jsonl"):
    path = root / "backend" / "tests" / "fixtures" / name
    records = list(h.iter_transcript_tool_uses(path))
    recall = [r for r in records if r.get("name") == "mcp__sill__recall_batch"]
    assert len(recall) == 1, f"{name}: expected 1 recall_batch record, got {len(recall)}"
    assert recall[0]["input"] == {"ids": ["11111111-1111-1111-1111-111111111111"]}, \
        f"{name}: normalized input did not match"
print("capture-slot check OK on both harness fixtures")
PY
  then
    pass "adapter capture-slot check green (no-pytest fallback — install pytest for the full four-slot suite)"
  else
    fail "adapter capture-slot check failed — see docs/adapters.md"
  fi
fi

printf '\nAll checks passed.\n'
