"""Two defects the clean-machine acceptance rehearsal found, pinned.

Both were invisible on a development machine and fatal on a fresh one, and
both had the same shape: a step reported success while writing its effect
somewhere nothing reads.

1. **`verify.sh` and `upgrade.sh` ignored `backend/.env`.** Check 1 runs
   `docker compose` from `backend/`, so Compose loads `.env` and check 1 goes
   green; check 3 shells `docker exec "$SILL_DB_CONTAINER"` straight from the
   process environment, which nothing sets. An operator following this repo's
   own advice — "set `SILL_DB_CONTAINER` in `backend/.env` if you need two
   stacks side by side" — got a green check 1 and a check 3 aimed at whatever
   container happened to be named `sill_db`. With two Sills installed, that is
   a *pass* reported off the wrong database.

2. **install.sh wrote the MCP entry to `~/.claude/.mcp.json`.** Claude Code's
   user-scope MCP registry is `~/.claude.json`; `~/.claude/.mcp.json` is not a
   path it reads. Step 7 printed `added mcpServers.sill -> …` and
   `claude mcp list` then said "No MCP servers configured", which is exactly
   the loop the runbook's phase-2 remedy ("re-run ./install.sh and read step
   7's output") cannot break.

Neither test needs docker or a database.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY_SH = REPO_ROOT / "verify.sh"
UPGRADE_SH = REPO_ROOT / "upgrade.sh"
INSTALL_SH = REPO_ROOT / "install.sh"

ENV_LOADING_SCRIPTS = [VERIFY_SH, UPGRADE_SH]


def extract_function(script: Path, name: str) -> str:
    """Pull one shell function's source out of a script.

    Slicing the text is deliberate: neither script is source-guarded, so
    `source verify.sh` would run all six checks against a live stack. This
    exercises the real function body without executing anything else in the
    file.
    """
    text = script.read_text(encoding="utf-8")
    start = text.index(f"{name}() {{")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise AssertionError(f"{script.name}: {name}() has unbalanced braces")


def run_loader(script: Path, env_body: str, tmp_path: Path, preset: dict) -> dict:
    """Run the script's own load_env_file() against a fixture backend/.env."""
    (tmp_path / "backend").mkdir(parents=True, exist_ok=True)
    (tmp_path / "backend" / ".env").write_text(env_body, encoding="utf-8")
    program = "\n".join([
        "set -euo pipefail",
        f'SILL_DIR="{tmp_path}"',
        extract_function(script, "load_env_file"),
        "load_env_file",
        'printf "%s\\n" "SILL_DB_CONTAINER=${SILL_DB_CONTAINER:-<unset>}"',
        'printf "%s\\n" "POSTGRES_USER=${POSTGRES_USER:-<unset>}"',
        'printf "%s\\n" "POSTGRES_DB=${POSTGRES_DB:-<unset>}"',
        'printf "%s\\n" "QUOTED=${QUOTED:-<unset>}"',
    ])
    env = {k: v for k, v in os.environ.items() if k not in
           ("SILL_DB_CONTAINER", "POSTGRES_USER", "POSTGRES_DB", "QUOTED")}
    env.update(preset)
    result = subprocess.run(["bash", "-c", program], capture_output=True,
                            text=True, env=env, timeout=30)
    assert result.returncode == 0, result.stderr
    return dict(line.split("=", 1) for line in result.stdout.strip().splitlines())


FIXTURE_ENV = """\
# a comment line
SILL_DB_CONTAINER=fixture_db

POSTGRES_USER=fixture_user
POSTGRES_DB=fixture_db_name
QUOTED="quoted value"
not a key=value pair
"""


@pytest.mark.parametrize("script", ENV_LOADING_SCRIPTS, ids=lambda p: p.name)
def test_backend_env_supplies_the_db_coordinates(script, tmp_path):
    out = run_loader(script, FIXTURE_ENV, tmp_path, preset={})
    assert out["SILL_DB_CONTAINER"] == "fixture_db"
    assert out["POSTGRES_USER"] == "fixture_user"
    assert out["POSTGRES_DB"] == "fixture_db_name"
    assert out["QUOTED"] == "quoted value", "surrounding quotes should be stripped"


@pytest.mark.parametrize("script", ENV_LOADING_SCRIPTS, ids=lambda p: p.name)
def test_an_exported_variable_beats_the_env_file(script, tmp_path):
    """Compose's precedence, matched: shell environment wins over .env."""
    out = run_loader(script, FIXTURE_ENV, tmp_path,
                     preset={"SILL_DB_CONTAINER": "from_shell"})
    assert out["SILL_DB_CONTAINER"] == "from_shell"
    assert out["POSTGRES_USER"] == "fixture_user", "unset keys still come from .env"


@pytest.mark.parametrize("script", ENV_LOADING_SCRIPTS, ids=lambda p: p.name)
def test_a_missing_env_file_is_not_an_error(script, tmp_path):
    out = run_loader(script, "", tmp_path, preset={})
    (tmp_path / "backend" / ".env").unlink()
    out = run_loader(script, "", tmp_path, preset={})
    assert out["SILL_DB_CONTAINER"] == "<unset>"


@pytest.mark.parametrize("script", ENV_LOADING_SCRIPTS, ids=lambda p: p.name)
def test_the_db_coordinates_are_derived_after_the_env_file_is_loaded(script):
    """Ordering, not just presence: a CONTAINER= line above load_env_file
    would read the pre-.env environment and the fix would be inert."""
    text = script.read_text(encoding="utf-8")
    assert text.index("\nload_env_file\n") < text.index('CONTAINER="${SILL_DB_CONTAINER'), (
        f"{script.name}: CONTAINER is derived before backend/.env is loaded"
    )


# --- install.sh step 7 --------------------------------------------------------


def test_install_never_writes_the_path_claude_code_does_not_read():
    """`~/.claude/.mcp.json` is not Claude Code's user-scope MCP registry.

    Comment lines are exempt on purpose: the step's header explains the old
    path so a reader of a v0.1.0 install can tell what happened to it.
    """
    code = [line for line in INSTALL_SH.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")]
    hits = [line for line in code if ".claude/.mcp.json" in line]
    assert not hits, (
        "install.sh writes ~/.claude/.mcp.json, which Claude Code never reads; "
        f"the user-scope registry is ~/.claude.json — {hits}"
    )


def test_install_prefers_the_claude_cli_for_mcp_registration():
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "claude mcp add --scope user sill -- sill-mcp" in text
    assert 'claude_mcp="$HOME/.claude.json"' in text, (
        "the no-claude-CLI fallback must merge into ~/.claude.json"
    )


def test_dry_run_step_7_reports_the_cli_path_and_writes_nothing(tmp_path):
    """--dry-run must not touch the throwaway HOME, and must name what it
    would do — the step's output is the only thing an operator can check."""
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home)}
    result = subprocess.run(["bash", str(INSTALL_SH), "--dry-run"],
                            capture_output=True, text=True, env=env, timeout=120)
    assert result.returncode == 0, result.stderr
    assert "Step 7/10" in result.stdout
    if "claude mcp add" in result.stdout:
        assert "--scope user sill -- sill-mcp" in result.stdout
    else:  # no claude CLI on this machine: the fallback path is announced
        assert ".claude.json" in result.stdout
    assert not (home / ".claude" / ".mcp.json").exists()
    assert not (home / ".claude.json").exists(), "--dry-run wrote a real file"


def test_the_fallback_merge_produces_a_stdio_entry_claude_code_understands():
    """The shape matters: `claude mcp add` writes type/command/args/env, and a
    hand-merged entry that omits `type` is not equivalent."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    block = text.split("claude CLI not on PATH", 1)[1]
    entry = block.split("servers[\"sill\"] = ", 1)[1].split("\n", 1)[0]
    parsed = json.loads(entry)
    assert parsed == {"type": "stdio", "command": "sill-mcp", "args": [], "env": {}}
