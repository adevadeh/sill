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
#   7.  Idempotently register the 'sill' MCP server with Claude Code
#       (`claude mcp add --scope user`, falling back to a direct merge into
#       ~/.claude.json) and with ~/.codex/config.toml if Codex is installed.
#   8.  Hook wiring, per --scope (default project):
#         project — --hooks-for <path> writes idempotent hook configs into
#                   that project's .claude/settings.local.json and
#                   .codex/hooks.json. Nothing happens without --hooks-for.
#         home    — registers hooks user-scope (~/.claude/settings.json,
#                   ~/.codex/hooks.json) and installs an ambient
#                   instructions file (~/.claude/CLAUDE.md) so every
#                   session in every directory carries the Sill
#                   background. --hooks-for still works alongside it,
#                   additively, for a project that also wants its own
#                   project-scoped entries.
#   9.  Print a "run ./verify.sh" hint.
#  10.  Final next-steps banner.
#
# Flags:
#   --scope home|project   Where hooks are wired (default: project). This is
#                          a real tradeoff, not a style choice — see step 8
#                          above and docs/extending.md:
#                            project — narrower blast radius, no
#                              cross-project mixing, but only the project(s)
#                              you --hooks-for get recall/guards.
#                            home    — every prompt in every project pays
#                              the recall hook's latency, and any project's
#                              work can reach the one store; in exchange you
#                              never re-wire hooks per project again.
#   --no-seed              Skip step 5.
#   --hooks-for <project>  Write per-project hook configs into <project>/.claude
#                          and <project>/.codex. Applies with --scope project
#                          (the default); additive, not exclusive, with
#                          --scope home.
#   --dry-run              Print each step without doing it.
#   --help                 Show this help.

set -euo pipefail

# --- argparse -----------------------------------------------------------------
NO_SEED=0
DRY_RUN=0
HOOKS_FOR=""
SCOPE="project"

usage() {
  sed -n '2,44p' "$0"
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
    --scope)
      [[ $# -ge 2 ]] || { echo "install.sh: --scope requires a value (home or project)" >&2; exit 2; }
      SCOPE="$2"
      case "$SCOPE" in
        home|project) ;;
        *)
          echo "install.sh: invalid --scope '$SCOPE' — valid values: home, project" >&2
          exit 2
          ;;
      esac
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
# BASH_SOURCE, not $0: this resolves correctly whether the script is run
# directly (the normal case) or sourced (backend/tests/test_install_scope.py
# sources it to unit-test individual step functions without running the
# full 10-step pipeline — see that file's docstring and the main guard at
# the bottom of this file).
SILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
  # docker compose loads `.env` from cwd, not from the directory containing
  # the compose file — so run from backend/ so the .env there (with any
  # SILL_DB_CONTAINER / POSTGRES_PORT overrides) is honored.
  run "(cd \"$SILL_DIR/backend\" && docker compose -f \"$COMPOSE_FILE\" build)"
  run "(cd \"$SILL_DIR/backend\" && docker compose -f \"$COMPOSE_FILE\" up -d db embeddings rabbitmq maintenance_worker)"
}

# --- step 4: wait for healthchecks -------------------------------------------
step_wait_healthy() {
  # 600s timeout because first boot downloads ~300MB safetensors when
  # the upstream HF repo has no ONNX files (Candle backend fallback).
  # Subsequent boots reuse the embeddings_cache volume and are quick.
  local timeout_s="${SILL_INSTALL_WAIT_HEALTHY_S:-600}"
  say "Step 4/10: wait for db + embeddings healthchecks (${timeout_s}s)"
  if (( DRY_RUN )); then
    note "DRY: would poll 'docker compose ps' for 'healthy' on db and embeddings"
    return 0
  fi
  local deadline=$(( SECONDS + timeout_s ))
  local services="db embeddings"
  while (( SECONDS < deadline )); do
    local all_healthy=1
    for svc in $services; do
      local status
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
      note "db and embeddings both healthy"
      return 0
    fi
    sleep 5
  done
  echo "install.sh: db/embeddings not healthy after ${timeout_s}s. Check 'docker compose -f $COMPOSE_FILE logs'." >&2
  exit 1
}

# --- step 5: import seed ------------------------------------------------------
step_import_seed() {
  say "Step 5/10: import methodology seed"
  if (( NO_SEED )); then
    note "--no-seed: skipping"
    return 0
  fi
  # Source backend/.env so any POSTGRES_PORT / POSTGRES_USER overrides
  # reach `sill seed import`. The CLI does not auto-load .env (yet),
  # so the install script does it for the one-shot import.
  if [[ -f "$SILL_DIR/backend/.env" ]]; then
    note "sourcing $SILL_DIR/backend/.env for db creds"
    # shellcheck disable=SC1091
    run "set -a && . \"$SILL_DIR/backend/.env\" && set +a && sill seed import \"$SILL_DIR/seed/methodology.jsonl\""
  else
    run "sill seed import \"$SILL_DIR/seed/methodology.jsonl\""
  fi
}

# --- step 6: plugin symlink ---------------------------------------------------
step_plugin_symlink() {
  say "Step 6/10: symlink plugin into ~/.claude/plugins/local"
  run "mkdir -p \"$HOME/.claude/plugins/local\""
  run "ln -sfn \"$SILL_DIR/plugin\" \"$HOME/.claude/plugins/local/sill-plugin\""
}

# --- step 7: MCP wiring -------------------------------------------------------
# Claude Code's user-scope MCP registry is ~/.claude.json, NOT ~/.claude/.mcp.json.
# Through v0.1.0 this step wrote the latter, which Claude Code never reads: the
# installer reported success, and `claude mcp list` then said "No MCP servers
# configured" with no way to tell from either output that the entry had gone
# somewhere inert. Found by the v0.2.0 clean-machine acceptance rehearsal
# (docs/RELEASE-REHEARSAL.md). Preferred path is now the `claude` CLI itself, so
# the file format stays Claude Code's business rather than this script's; the
# direct merge below is the fallback for a machine where `claude` isn't on PATH.
step_mcp_wiring() {
  say "Step 7/10: idempotently wire 'sill' MCP server"
  local claude_mcp="$HOME/.claude.json"
  if (( DRY_RUN )); then
    if command -v claude >/dev/null 2>&1; then
      note "DRY: would run 'claude mcp add --scope user sill -- sill-mcp'"
    else
      note "DRY: would merge mcpServers.sill = {command: 'sill-mcp'} into $claude_mcp"
    fi
  elif command -v claude >/dev/null 2>&1; then
    # `claude mcp add` is idempotent by its own report ("already exists"), so a
    # re-run is safe; it writes wherever this Claude Code version keeps user
    # scope, which is the point of delegating to it.
    if out="$(claude mcp add --scope user sill -- sill-mcp 2>&1)"; then
      printf '  %s\n' "$out"
    else
      printf '  %s\n' "$out"
      note "  'claude mcp add' failed; add it by hand: claude mcp add --scope user sill -- sill-mcp"
    fi
  else
    note "claude CLI not on PATH; merging directly into $claude_mcp"
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
        print('  "sill": {"type": "stdio", "command": "sill-mcp", "args": [], "env": {}}')
        sys.exit(0)
servers = data.setdefault("mcpServers", {})
if "sill" in servers:
    print(f"  mcpServers.sill already present in {path}; leaving as-is")
else:
    servers["sill"] = {"type": "stdio", "command": "sill-mcp", "args": [], "env": {}}
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

# Resolve the interpreter that has the backend deps (psycopg2, asyncpg, …)
# installed, so hook commands run under it instead of a bare system python3 that
# can't import them. Mirrors how step 2 installs the backend: pipx venv first,
# then the ~/.local/share/sill-venv fallback, then a last-resort bare python3.
sill_python() {
  if command -v pipx >/dev/null 2>&1; then
    local cand="${PIPX_HOME:-$HOME/.local/pipx}/venvs/sill-memory/bin/python"
    [[ -x "$cand" ]] && { printf '%s' "$cand"; return; }
  fi
  local venv_py="$HOME/.local/share/sill-venv/bin/python"
  [[ -x "$venv_py" ]] && { printf '%s' "$venv_py"; return; }
  printf 'python3'
}

# Render Codex hooks.json at $2 from template $1, skipping (with a note) if
# the destination already exists — re-rendering an *existing* install is
# ./upgrade.sh's job (it shows a diff and supports --force-hooks; see
# upgrade.sh's own header), not this first-wiring step's. Shared by both
# --scope project (per-project .codex/hooks.json) and --scope home
# (~/.codex/hooks.json).
_render_codex_hooks() {
  local template="$1" dst="$2" plugin_dir="$3" sill_py="$4"
  if (( DRY_RUN )); then
    note "DRY: would render $template -> $dst with SILL_PLUGIN_DIR=$plugin_dir SILL_PYTHON=$sill_py"
    return 0
  fi
  mkdir -p "$(dirname "$dst")"
  if [[ -f "$dst" ]]; then
    note "  $dst already exists; leaving as-is (see ./upgrade.sh --hooks-for to refresh)"
    return 0
  fi
  python3 - "$template" "$dst" "$plugin_dir" "$sill_py" <<'PY'
import pathlib, sys
src, dst, plugin, py = sys.argv[1:5]
text = (pathlib.Path(src).read_text()
        .replace("{{SILL_PLUGIN_DIR}}", plugin)
        .replace("{{SILL_PYTHON}}", py))
pathlib.Path(dst).write_text(text)
print(f"  wrote {dst}")
PY
}

# Idempotently merge the template's hooks block into a Claude Code hooks
# JSON file. Both destinations this installer writes share the same
# top-level {"hooks": {...}} shape: <project>/.claude/settings.local.json
# (project scope) and ~/.claude/settings.json (home scope — Claude Code's
# own "globally" location; see docs/extending.md's "Adding your own hooks"
# section, and this repo's own live ~/.claude/settings.json, which already
# carries unrelated hooks under the same key).
_merge_claude_hooks() {
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
}

# Idempotently append plugin/claude.home.md.template's ambient-instructions
# block to ~/.claude/CLAUDE.md. Never touches anything already in that
# file — it's the operator's own global instructions file, which may
# already carry substantial unrelated content — keyed on a marker comment
# so a second run is a no-op instead of a duplicate append.
_install_home_ambient_file() {
  local plugin_dir="$1"
  local template="$plugin_dir/claude.home.md.template"
  local dst="$HOME/.claude/CLAUDE.md"
  if (( DRY_RUN )); then
    note "DRY: would idempotently append $template -> $dst"
    return 0
  fi
  mkdir -p "$(dirname "$dst")"
  python3 - "$template" "$dst" <<'PY'
import pathlib, sys
template_path, dst_path = sys.argv[1:3]
block = pathlib.Path(template_path).read_text().rstrip("\n") + "\n"
marker = "<!-- sill:home-scope-ambient -->"
dst = pathlib.Path(dst_path)
existing = dst.read_text() if dst.exists() else ""
if marker in existing:
    print(f"  {dst} already has the Sill ambient block; nothing to do")
else:
    prefix = existing
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    if prefix and not prefix.endswith("\n\n"):
        prefix += "\n"
    dst.write_text(prefix + block)
    print(f"  appended Sill ambient block -> {dst}")
PY
}

# --- step 8: hook wiring, per --scope ------------------------------------------
step_hook_wiring() {
  say "Step 8/10: hook wiring (scope: $SCOPE)"
  local plugin_dir="$SILL_DIR/plugin"
  local template="$SILL_DIR/plugin/codex.hooks.json.template"
  local sill_py
  sill_py="$(sill_python)"

  if [[ "$SCOPE" == "home" ]]; then
    note "home scope: every prompt in every project will pay the recall hook's latency, and any project's work can reach this one store."
    _render_codex_hooks "$template" "$HOME/.codex/hooks.json" "$plugin_dir" "$sill_py"
    _merge_claude_hooks "$template" "$HOME/.claude/settings.json" "$plugin_dir" "$sill_py"
    _install_home_ambient_file "$plugin_dir"
  fi

  if [[ -z "$HOOKS_FOR" ]]; then
    if [[ "$SCOPE" == "project" ]]; then
      note "--hooks-for not provided; skipping"
    fi
    return 0
  fi

  local target
  target="$(cd "$HOOKS_FOR" && pwd)"
  note "project scope target: $target (narrower blast radius, no cross-project mixing)"
  _render_codex_hooks "$template" "$target/.codex/hooks.json" "$plugin_dir" "$sill_py"
  _merge_claude_hooks "$template" "$target/.claude/settings.local.json" "$plugin_dir" "$sill_py"
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

    Next: turn this working install into somebody's Sill.
      Read docs/onboarding/README.md — a phased runbook, walked with the
      person present, ending at a christening: a charter in their own
      words, a name, a first deliberate memory, a chosen cadence.

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
# Guarded so backend/tests/test_install_scope.py can `source` this file to
# unit-test individual step/helper functions (e.g. _install_home_ambient_file)
# against a tmp HOME without running the full 10-step pipeline. True whenever
# this file is executed directly (./install.sh or bash install.sh, the only
# ways a real operator runs it) and false when sourced.
if [[ "${BASH_SOURCE[0]:-$0}" == "$0" ]]; then
  step_preflight
  step_install_backend
  step_docker_up
  step_wait_healthy
  step_import_seed
  step_plugin_symlink
  step_mcp_wiring
  step_hook_wiring
  step_verify_hint
  step_banner
fi
