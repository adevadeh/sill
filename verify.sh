#!/usr/bin/env bash
# verify.sh — six smoke checks for an installed Sill.
#
# Exits 0 only if all six checks pass. Designed to be safe to re-run.
#
# Checks:
#   1. docker compose ps shows db + embeddings healthy.
#   2. sill-mcp answers a real MCP `initialize` handshake over stdio.
#   3. SELECT count(*) FROM memories returns >= 22 (seed loaded).
#   4. plugin/hooks/response-patterns.py exits 0 on a canned Stop event.
#   5. schema_migrations matches backend/migrations/ (schema level current).
#   6. adapter conformance: the four-slot contract (docs/adapters.md) holds
#      on both harnesses' fixture shapes.

set -euo pipefail

SILL_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$SILL_DIR/backend/docker-compose.yml"

# Read backend/.env the way `docker compose` does, so every check in this
# file resolves the same install the compose checks do. Without this, check 1
# (which runs from backend/, so compose loads .env) and check 3 (a bare
# `docker exec "$SILL_DB_CONTAINER"`) read two different configurations: an
# operator who follows this repo's own advice to "set SILL_DB_CONTAINER in
# backend/.env if you need two stacks side by side" gets a green check 1 and
# a check 3 that queries whatever container happens to be named `sill_db` —
# in the worst case, a different Sill's database, reported as a pass.
# Precedence matches compose: an already-exported shell variable wins, .env
# fills in the rest.
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

# Resolve the interpreter that has the backend (and its dev extras) installed.
#
# Check 6 used to test `python3` for pytest and run pytest with `python3` — but
# `install.sh` puts the backend in a pipx venv, or in ~/.local/share/sill-venv,
# never in the system python3. So on a normal install check 6 always found no
# pytest and always took its degraded branch, while printing a remedy naming
# SILL_PYTHON that it then never read. Following that remedy exactly could not
# change the outcome.
#
# Resolution order mirrors install.sh's sill_python() — the same order the
# backend was installed in — with an explicit SILL_PYTHON winning, since that
# is what docs/onboarding/01-install.md and scheduling/README.md already tell
# operators to set.
sill_python() {
  if [[ -n "${SILL_PYTHON:-}" ]]; then
    printf '%s' "$SILL_PYTHON"
    return
  fi
  local pipx_py="${PIPX_HOME:-$HOME/.local/pipx}/venvs/sill-memory/bin/python"
  [[ -x "$pipx_py" ]] && { printf '%s' "$pipx_py"; return; }
  local venv_py="$HOME/.local/share/sill-venv/bin/python"
  [[ -x "$venv_py" ]] && { printf '%s' "$venv_py"; return; }
  printf 'python3'
}

# Speak MCP to the installed server and require a real answer.
#
# Check 2 used to run `sill-mcp --help`, which exits before importing the MCP
# SDK at all. During the v0.2.0 clean-machine rehearsal that check reported
# green on a server that could not serve a single tool: an unpinned
# `mcp>=1.0.0` had resolved to 2.x, which removed the `Server.list_tools()`
# API sill_mcp_server.py registers through, and the server died on its first
# handshake with `'Server' object has no attribute 'list_tools'`. The
# dependency is pinned now (backend/pyproject.toml), but the pin only blocks
# that one resolution — `--help` would exit 0 again the next time the SDK
# moves, and a check that stays green over a dead server is worse than no
# check. So: start it, send `initialize`, require a well-formed result naming
# this server, and kill it.
#
# SILL_MCP_CMD overrides the command (used by the test suite to point at
# stubs that fail in specific ways). SILL_VERIFY_MCP_TIMEOUT_S bounds the
# wait — the server opens a database connection before it will answer, so
# this is not instant.
mcp_handshake() {
  SILL_MCP_CMD="${SILL_MCP_CMD:-sill-mcp}" \
  SILL_VERIFY_MCP_TIMEOUT_S="${SILL_VERIFY_MCP_TIMEOUT_S:-20}" \
  python3 <<'PY'
import json
import os
import shlex
import subprocess
import sys
import threading

cmd = shlex.split(os.environ["SILL_MCP_CMD"])
budget = float(os.environ["SILL_VERIFY_MCP_TIMEOUT_S"])

request = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "verify.sh", "version": "1"},
    },
}


def die(message, proc=None):
    print(f"    {message}", file=sys.stderr)
    if proc is not None:
        try:
            proc.kill()
        except Exception:
            pass
        err = (proc.stderr.read() or "").strip() if proc.stderr else ""
        if err:
            print("    the server said:", file=sys.stderr)
            for line in err.splitlines()[-8:]:
                print(f"      {line}", file=sys.stderr)
    sys.exit(1)


try:
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
except OSError as exc:
    print(f"    could not start {cmd[0]!r}: {exc}", file=sys.stderr)
    sys.exit(1)

# One reply, or nothing. A reader thread is the stdlib's only way to put a
# deadline on a blocking read: a server that accepts the request and then
# hangs must fail this check, not hang verify.sh.
reply = []


def read_reply():
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue          # tolerate stray non-protocol output on stdout
        if isinstance(obj, dict) and obj.get("id") == 1:
            reply.append(obj)
            return


reader = threading.Thread(target=read_reply, daemon=True)
reader.start()

try:
    proc.stdin.write(json.dumps(request) + "\n")
    proc.stdin.flush()
except (BrokenPipeError, OSError):
    die("the server closed its input before the handshake was sent", proc)

reader.join(budget)

if not reply:
    if proc.poll() is not None:
        die(f"the server exited ({proc.returncode}) without answering `initialize`", proc)
    die(f"no answer to `initialize` within {budget:.0f}s", proc)

obj = reply[0]
if "error" in obj:
    die(f"the server refused `initialize`: {obj['error']}", proc)

result = obj.get("result")
if not isinstance(result, dict):
    die(f"malformed handshake reply: {obj}", proc)
info = result.get("serverInfo") or {}
if info.get("name") != "sill":
    die(f"handshake answered by {info.get('name')!r}, expected 'sill'", proc)
if "protocolVersion" not in result:
    die(f"handshake reply names no protocolVersion: {result}", proc)

print(f"{info.get('name')} {info.get('version', '?')} "
      f"(MCP {result['protocolVersion']})")

try:
    proc.stdin.close()
    proc.wait(timeout=5)
except Exception:
    proc.kill()
PY
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
say "Check 2/6: sill-mcp answers an MCP initialize handshake"
if ! command -v "${SILL_MCP_CMD:-sill-mcp}" >/dev/null 2>&1; then
  fail "sill-mcp not on PATH (is the backend installed and ~/.local/bin on PATH?)"
fi
if handshake="$(mcp_handshake)"; then
  pass "MCP handshake answered by $handshake"
else
  fail "sill-mcp did not answer an MCP initialize handshake (see above). Reinstall the backend ('pip install -e backend') and re-run; if it persists, run 'sill-mcp' by hand and read its first line of output."
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
SILL_PY="$(sill_python)"
if "$SILL_PY" -c "import pytest" >/dev/null 2>&1; then
  note "conformance interpreter: $SILL_PY"
  if (cd "$SILL_DIR/backend" && "$SILL_PY" -m pytest \
        tests/test_adapter_conformance.py \
        "tests/test_notice.py::test_auto_store_argv_parses_for_both_harness_calling_shapes" \
        -q); then
    pass "adapter conformance suite green (pytest)"
  else
    fail "adapter conformance suite failed — see pytest output above, and docs/adapters.md"
  fi
else
  note "pytest not importable by $SILL_PY (dev-only extra) — running a direct capture-slot check instead."
  note "For the full four-slot suite, add pytest to that same interpreter — one of:"
  if command -v pipx >/dev/null 2>&1 && [[ "$SILL_PY" == *"/pipx/venvs/sill-memory/"* ]]; then
    note "  pipx inject sill-memory pytest pytest-asyncio"
  fi
  note "  \"$SILL_PY\" -m pip install -e \"$SILL_DIR/backend[dev]\""
  note "Then re-run ./verify.sh. Override the interpreter with SILL_PYTHON=<path> if it guessed wrong."
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
