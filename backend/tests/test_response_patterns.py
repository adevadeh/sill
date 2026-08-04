"""Gate logic via subprocess canned Stop events; no DB, no model
(SILL_INSIGHT_DETECT unset ⇒ off; gate tests enable it but die before ollama
via missing transcript / home project)."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / "plugin" / "hooks" / "response-patterns.py"
spec = importlib.util.spec_from_file_location("response_patterns", HOOK)
rp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rp)


def test_source_project_root_cwd_is_unknown():
    assert rp.source_project("/", None) == "unknown"


def test_source_project_home_match(tmp_path, monkeypatch):
    home = tmp_path / "myproj"
    home.mkdir()
    monkeypatch.setenv("SILL_HOME_PROJECT", str(home))
    assert rp.source_project(str(home), None) == rp.HOME_PROJECT_NAME
    assert rp.source_project(str(home / "sub"), None) == rp.HOME_PROJECT_NAME


def test_empty_home_env_treats_everything_as_home(monkeypatch):
    monkeypatch.setenv("SILL_HOME_PROJECT", "")
    assert rp.source_project("/anywhere/else", None) == rp.HOME_PROJECT_NAME


def test_deliberate_mint_detector_sees_bash_notice():
    block = {"type": "tool_use", "name": "Bash",
             "input": {"command": "sill notice 'a fact' --speaker Ada"}}
    assert rp._block_is_deliberate_store(block)
    block2 = {"type": "tool_use", "name": "mcp__sill__remember", "input": {}}
    assert rp._block_is_deliberate_store(block2)
    block3 = {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}
    assert not rp._block_is_deliberate_store(block3)


def _run(payload, env_extra=None):
    env = {**os.environ, "SILL_INSIGHT_DETECT": "0"}
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=60)


def test_canned_stop_clean_sentence_exit0():
    r = _run({"hook_event_name": "Stop",
              "last_assistant_message": "A plain sentence with no flagged patterns."})
    assert r.returncode == 0


def test_missing_transcript_never_reaches_store(tmp_path):
    r = _run({"hook_event_name": "Stop", "cwd": str(tmp_path),
              "last_assistant_message": "x" * 400},
             {"SILL_INSIGHT_DETECT": "1", "SILL_HOME_PROJECT": "/nonexistent-home"})
    assert r.returncode == 0   # payload-integrity guard: no transcript ⇒ no store path


def test_frontmatter_parser_tolerates_comments(tmp_path):
    rule = tmp_path / "r.md"
    rule.write_text("---\nname: t\nseverity: low\npatterns:\n"
                    "  # a comment that must not kill the rule\n"
                    "  - \\bteststring\\b\n---\nbody\n")
    pats, _ = rp.parse_frontmatter(rule.read_text())
    assert any("teststring" in p for p in pats.get("patterns", []))
