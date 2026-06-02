#!/usr/bin/env bash
# install.sh — one-shot Sill installer.
#
# Steps:
#   1.  Preflight: docker / python3 / pipx (or pip+venv) on PATH; docker daemon up.
#   2.  Install the backend package and console scripts (sill, sill-mcp, sill-worker).
#   3.  Build Docker images, bring up db + embeddings + rabbitmq + maintenance_worker.
#   4.  Wait for db + embeddings to become healthy (120s timeout).
#   5.  Import the methodology seed (unless --no-seed).
#   6.  Symlink the plugin into ~/.claude/plugins/local/sill-plugin.
#   7.  Idempotently merge the MCP server entry into ~/.claude/.mcp.json
#       (and ~/.codex/config.toml if Codex is installed).
#   8.  --hooks-for <project>: write idempotent hook configs into a target project.
#   9.  Print a "run ./verify.sh" hint.
#  10.  Final next-steps banner.
#
# Flags:
#   --no-seed              Skip step 5.
#   --hooks-for <project>  Write per-project hook configs into <project>/.claude
#                          and <project>/.codex.
#   --dry-run              Print each step without doing it.
#   --help                 Show this help.

set -euo pipefail

# --- argparse -----------------------------------------------------------------
NO_SEED=0
DRY_RUN=0
HOOKS_FOR=""

usage() {
  sed -n '2,25p' "$0"
}

while (( $# )); do
  case "$1" in
    --no-seed)
      NO_SEED=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --hooks-for)
      [[ $# -ge 2 ]] || { echo "install.sh: --hooks-for requires a path" >&2; exit 2; }
      HOOKS_FOR="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "install.sh: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

# --- helpers ------------------------------------------------------------------
SILL_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$SILL_DIR/backend/docker-compose.yml"

say() { printf '\n=== %s ===\n' "$*"; }
note() { printf '    %s\n' "$*"; }

run() {
  if (( DRY_RUN )); then
    printf 'DRY: %s\n' "$*"
  else
    eval "$@"
  fi
}

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "install.sh: missing prerequisite: $1" >&2
    return 1
  fi
}

# --- step 1: preflight --------------------------------------------------------
step_preflight() {
  say "Step 1/10: preflight"
  local missing=0
  need_cmd docker || missing=1
  need_cmd python3 || missing=1

  if command -v pipx >/dev/null 2>&1; then
    note "pipx available (will use pipx install -e)"
  else
    note "pipx not on PATH; will fall back to a venv at ~/.local/share/sill-venv"
  fi

  if (( missing )); then
    echo "install.sh: install the missing prerequisites above and re-run." >&2
    exit 1
  fi

  if (( DRY_RUN )); then
    note "DRY: would run 'docker info' to confirm the daemon is up"
  else
    if ! docker info >/dev/null 2>&1; then
      echo "install.sh: docker is installed but the daemon isn't responding. Start Docker Desktop / OrbStack and re-run." >&2
      exit 1
    fi
    note "docker daemon responding"
  fi
}

# --- step 2: install backend --------------------------------------------------
step_install_backend() {
  say "Step 2/10: install backend (sill / sill-mcp / sill-worker)"
  if command -v pipx >/dev/null 2>&1; then
    run "pipx install --force -e \"$SILL_DIR/backend\""
  else
    local venv="$HOME/.local/share/sill-venv"
    local localbin="$HOME/.local/bin"
    run "python3 -m venv \"$venv\""
    run "\"$venv/bin/pip\" install --upgrade pip"
    run "\"$venv/bin/pip\" install -e \"$SILL_DIR/backend\""
    run "mkdir -p \"$localbin\""
    for bin in sill sill-mcp sill-worker; do
      run "ln -sfn \"$venv/bin/$bin\" \"$localbin/$bin\""
    done
    note "Symlinked sill/sill-mcp/sill-worker into $localbin (ensure it's on your PATH)"
  fi
}

# --- step 3: bring up docker stack --------------------------------------------
step_docker_up() {
  say "Step 3/10: build images + start db, embeddings, rabbitmq, maintenance_worker"
  run "docker compose -f \"$COMPOSE_FILE\" build"
  run "docker compose -f \"$COMPOSE_FILE\" up -d db embeddings rabbitmq maintenance_worker"
}

# --- step 4: wait for healthchecks -------------------------------------------
step_wait_healthy() {
  say "Step 4/10: wait for db + embeddings healthchecks (120s)"
  if (( DRY_RUN )); then
    note "DRY: would poll 'docker compose ps' for 'healthy' on db and embeddings"
    return 0
  fi
  local deadline=$(( SECONDS + 120 ))
  local services="db embeddings"
  while (( SECONDS < deadline )); do
    local all_healthy=1
    for svc in $services; do
      local status
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
        all_healthy=0
        note "waiting: $svc is '$status'"
      fi
    done
    if (( all_healthy )); then
      note "db and embeddings both healthy"
      return 0
    fi
    sleep 5
  done
  echo "install.sh: db/embeddings not healthy after 120s. Check 'docker compose -f $COMPOSE_FILE logs'." >&2
  exit 1
}

# --- step 5: import seed ------------------------------------------------------
step_import_seed() {
  say "Step 5/10: import methodology seed"
  if (( NO_SEED )); then
    note "--no-seed: skipping"
    return 0
  fi
  run "sill seed import \"$SILL_DIR/seed/methodology.jsonl\""
}

# --- step 6: plugin symlink ---------------------------------------------------
step_plugin_symlink() {
  say "Step 6/10: symlink plugin into ~/.claude/plugins/local"
  run "mkdir -p \"$HOME/.claude/plugins/local\""
  run "ln -sfn \"$SILL_DIR/plugin\" \"$HOME/.claude/plugins/local/sill-plugin\""
}

# --- step 7: MCP wiring -------------------------------------------------------
step_mcp_wiring() {
  say "Step 7/10: idempotently wire 'sill' MCP server"
  local claude_mcp="$HOME/.claude/.mcp.json"
  if (( DRY_RUN )); then
    note "DRY: would merge mcpServers.sill = {command: 'sill-mcp'} into $claude_mcp"
  else
    mkdir -p "$HOME/.claude"
    python3 - "$claude_mcp" <<'PY'
import json, sys, pathlib
path = pathlib.Path(sys.argv[1])
data = {}
if path.exists():
    try:
        data = json.loads(path.read_text() or "{}")
    except json.JSONDecodeError:
        # Don't clobber a hand-edited file we can't parse.
        print(f"install.sh: {path} isn't valid JSON; leave it alone. Manually add:")
        print('  "sill": {"command": "sill-mcp"}')
        sys.exit(0)
servers = data.setdefault("mcpServers", {})
if "sill" in servers:
    print(f"  mcpServers.sill already present in {path}; leaving as-is")
else:
    servers["sill"] = {"command": "sill-mcp"}
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"  added mcpServers.sill -> {path}")
PY
  fi

  if command -v codex >/dev/null 2>&1; then
    local codex_cfg="$HOME/.codex/config.toml"
    if (( DRY_RUN )); then
      note "DRY: would add [mcp_servers.sill] + [features].hooks=true to $codex_cfg"
    else
      mkdir -p "$HOME/.codex"
      python3 - "$codex_cfg" <<'PY'
import pathlib, sys, re
path = pathlib.Path(sys.argv[1])
text = path.read_text() if path.exists() else ""

def has_section(body: str, header: str) -> bool:
    pat = rf"(?ms)^\s*\[{re.escape(header)}\]\s*$"
    return re.search(pat, body) is not None

def has_key_in_section(body: str, header: str, key: str) -> bool:
    pat = rf"(?ms)^\s*\[{re.escape(header)}\]\s*$(?P<body>.*?)(?=^\s*\[|\Z)"
    m = re.search(pat, body)
    if not m:
        return False
    return re.search(rf"(?m)^\s*{re.escape(key)}\s*=", m.group("body")) is not None

additions: list[str] = []
if not has_section(text, "mcp_servers.sill"):
    additions.append('[mcp_servers.sill]\ncommand = "sill-mcp"\n')
if has_section(text, "features"):
    if not has_key_in_section(text, "features", "hooks"):
        # Append to existing section by adding a new fully-qualified key.
        additions.append("[features]\nhooks = true\n")
else:
    additions.append("[features]\nhooks = true\n")

if additions:
    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n" + "\n".join(additions)
    path.write_text(text)
    print(f"  updated {path}")
else:
    print(f"  {path} already has sill MCP + features.hooks")
PY
    fi
  else
    note "codex CLI not detected; skipping ~/.codex/config.toml wiring"
  fi
}

# --- step 8: optional --hooks-for project wiring -----------------------------
step_hooks_for_project() {
  say "Step 8/10: per-project hook config"
  if [[ -z "$HOOKS_FOR" ]]; then
    note "--hooks-for not provided; skipping"
    return 0
  fi
  local target
  target="$(cd "$HOOKS_FOR" && pwd)"
  local plugin_dir="$SILL_DIR/plugin"
  local template="$SILL_DIR/plugin/codex.hooks.json.template"

  # Codex per-project hooks.
  local codex_dst="$target/.codex/hooks.json"
  if (( DRY_RUN )); then
    note "DRY: would render $template -> $codex_dst with SILL_PLUGIN_DIR=$plugin_dir"
  else
    mkdir -p "$(dirname "$codex_dst")"
    if [[ -f "$codex_dst" ]]; then
      note "  $codex_dst already exists; leaving as-is (delete to re-render)"
    else
      python3 - "$template" "$codex_dst" "$plugin_dir" <<'PY'
import pathlib, sys
src, dst, plugin = sys.argv[1:4]
text = pathlib.Path(src).read_text().replace("{{SILL_PLUGIN_DIR}}", plugin)
pathlib.Path(dst).write_text(text)
print(f"  wrote {dst}")
PY
    fi
  fi

  # Claude Code per-project hooks (same hook commands, different schema location).
  local claude_dst="$target/.claude/settings.local.json"
  if (( DRY_RUN )); then
    note "DRY: would idempotently merge hooks block into $claude_dst"
  else
    mkdir -p "$(dirname "$claude_dst")"
    python3 - "$template" "$claude_dst" "$plugin_dir" <<'PY'
import json, pathlib, sys
template_path, dst_path, plugin = sys.argv[1:4]
template = json.loads(pathlib.Path(template_path).read_text().replace("{{SILL_PLUGIN_DIR}}", plugin))
dst = pathlib.Path(dst_path)
data = {}
if dst.exists():
    try:
        data = json.loads(dst.read_text() or "{}")
    except json.JSONDecodeError:
        print(f"install.sh: {dst} isn't valid JSON; leaving it alone.")
        sys.exit(0)
hooks = data.setdefault("hooks", {})
changed = False
for event, entries in template.get("hooks", {}).items():
    existing = hooks.setdefault(event, [])
    # naive dedup: compare exact JSON repr
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
  fi
}

# --- step 9: verify hint ------------------------------------------------------
step_verify_hint() {
  say "Step 9/10: smoke check"
  note "Run ./verify.sh to confirm the install."
}

# --- step 10: banner ----------------------------------------------------------
step_banner() {
  say "Step 10/10: next steps"
  cat <<'EOF'
    Sill is installed.

    Optional follow-ups:
      * Install the episodic-memory marketplace plugin in Claude Code for richer recall.
      * Restart Claude Code (and/or codex) so the new MCP server is picked up.
      * Configure SILL_EPISODIC_MEMORY_PATH in your shell if you want the
        spontaneous-recall hook to inject conversation snippets.

    To remove everything: ./uninstall.sh
    To wipe the db and re-seed: ./reset.sh
EOF
}

# --- main ---------------------------------------------------------------------
step_preflight
step_install_backend
step_docker_up
step_wait_healthy
step_import_seed
step_plugin_symlink
step_mcp_wiring
step_hooks_for_project
step_verify_hint
step_banner
