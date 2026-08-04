"""Canned-event tests for spontaneous-recall. No DB: docker calls fail fast in
the test env (SILL_DB_CONTAINER points at a nonexistent container), which is a
supported degrade path — header-only output."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / "plugin" / "hooks" / "spontaneous-recall.py"


def run_hook(payload, env_extra=None, cwd=None):
    env = {**os.environ, "SILL_DB_CONTAINER": "sill_test_absent",
           "SILL_LOG_DIR": os.environ.get("PYTEST_SILL_LOG", "/tmp")}
    env.pop("SILL_DETACHED_BEAT", None)
    env.pop("SILL_INTERACTIVE", None)
    env.pop("SILL_HEADLESS_TOOL", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, cwd=cwd, timeout=60)


def test_detached_beat_gate_silences_everything():
    r = run_hook({"prompt": "a long enough genuine question about the system"},
                 {"SILL_DETACHED_BEAT": "1"})
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_headless_tool_wins_over_interactive():
    r = run_hook({"prompt": "another long enough genuine question here"},
                 {"SILL_HEADLESS_TOOL": "1", "SILL_INTERACTIVE": "1"})
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_interactive_overrides_sdk_entrypoint():
    r = run_hook({"prompt": "short"},
                 {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli", "SILL_INTERACTIVE": "1"})
    assert r.returncode == 0 and "[TIME]" in r.stdout


def test_desktop_entrypoint_not_gated():
    r = run_hook({"prompt": "short"}, {"CLAUDE_CODE_ENTRYPOINT": "claude-desktop"})
    assert r.returncode == 0 and "[TIME]" in r.stdout


def test_short_prompt_db_down_emits_clock_only_header():
    r = run_hook({"prompt": "hi there"})
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert re.match(r"^\[TIME\] \w{3} \d{4}-\d{2}-\d{2} \d{2}:\d{2}",
                    out["systemMessage"])
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"


def test_empty_prompt_fully_silent():
    r = run_hook({"prompt": "   "})
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_no_sidecar_written_when_no_memories(tmp_path):
    env = {"PYTEST_SILL_LOG": str(tmp_path), "SILL_LOG_DIR": str(tmp_path)}
    run_hook({"prompt": "hello", "session_id": "tsx1"}, env)
    assert not list(tmp_path.glob("recall-sidecar-*"))
