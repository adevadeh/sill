#!/usr/bin/env bash
# reset.sh — drop the db volume and re-seed.
#
# Flags:
#   --yes / -y    Skip the confirmation prompt.
#   --no-seed     Skip re-import (just down -v and back up).
#   --help / -h   Show this help.

set -euo pipefail

ASSUME_YES=0
NO_SEED=0

usage() { sed -n '2,9p' "$0"; }

while (( $# )); do
  case "$1" in
    --yes|-y) ASSUME_YES=1; shift ;;
    --no-seed) NO_SEED=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "reset.sh: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

SILL_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$SILL_DIR/backend/docker-compose.yml"
SEED_PATH="$SILL_DIR/seed/methodology.jsonl"

if (( ! ASSUME_YES )); then
  printf 'This will DROP the sill postgres volume and re-seed (DATA LOSS).\nContinue? [y/N] '
  read -r reply
  case "$reply" in
    y|Y|yes|YES) ;;
    *) echo "aborted"; exit 1 ;;
  esac
fi

say() { printf '\n=== %s ===\n' "$*"; }
note() { printf '  %s\n' "$*"; }

# --- step 1: down -v ---------------------------------------------------------
say "1/4: docker compose down -v"
# Run from backend/ so the .env there (with any SILL_DB_CONTAINER /
# POSTGRES_PORT overrides) targets the right project/containers.
(cd "$SILL_DIR/backend" && docker compose -f "$COMPOSE_FILE" down -v --remove-orphans)

# --- step 2: up --------------------------------------------------------------
say "2/4: docker compose up -d db embeddings rabbitmq maintenance_worker"
(cd "$SILL_DIR/backend" && docker compose -f "$COMPOSE_FILE" up -d db embeddings rabbitmq maintenance_worker)

# --- step 3: wait for healthy -----------------------------------------------
timeout_s="${SILL_RESET_WAIT_HEALTHY_S:-300}"
say "3/4: wait ${timeout_s}s for db + embeddings to become healthy"
deadline=$(( SECONDS + timeout_s ))
while (( SECONDS < deadline )); do
  all_healthy=1
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
      all_healthy=0
      note "waiting: $svc is '$status'"
    fi
  done
  if (( all_healthy )); then
    note "db + embeddings healthy"
    break
  fi
  sleep 5
done
if (( ! all_healthy )); then
  echo "reset.sh: db/embeddings not healthy after ${timeout_s}s; check 'docker compose -f $COMPOSE_FILE logs'." >&2
  exit 1
fi

# --- step 4: re-seed ---------------------------------------------------------
say "4/4: re-import seed"
if (( NO_SEED )); then
  note "--no-seed: skipping"
else
  if ! command -v sill >/dev/null 2>&1; then
    echo "reset.sh: 'sill' not on PATH; cannot re-import seed. Install the backend first." >&2
    exit 1
  fi
  sill seed import "$SEED_PATH"
fi

printf '\nReset complete.\n'
