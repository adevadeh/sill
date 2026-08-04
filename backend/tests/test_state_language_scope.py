"""state-language-check.py's Write/Edit scope (Step 3b of the scheduling
plan): SILL_BEAT_JOURNAL_DIRS when a beat set it, journals/+docs/ as the
fallback default so an install that never touches the beat worker — or any
interactive session, which beat_worker.spawn_beat() never wraps — keeps
exactly the coverage this hook always had. Tests both paths."""

import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / "plugin" / "hooks" / "state-language-check.py"

CONTENT = "I am feeling tired and need a break after this long session.\n"


def run(payload, env_extra=None):
    env = {**os.environ}
    env.pop("SILL_BEAT_JOURNAL_DIRS", None)  # tests opt in to it explicitly
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload), capture_output=True, text=True,
        env=env, timeout=15,
    )


def write_payload(path):
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": CONTENT}}


def fires(result) -> bool:
    if result.returncode != 0 or not result.stdout.strip():
        return False
    return "state-language-check" in json.loads(result.stdout).get("systemMessage", "")


# --- Path 1: unset SILL_BEAT_JOURNAL_DIRS -> fallback to journals/+docs/ ---

def test_fallback_default_still_covers_journals_and_docs():
    for path in ["journals/foo.md", "docs/bar.md", "nested/docs/baz.md"]:
        r = run(write_payload(path))
        assert r.returncode == 0
        assert fires(r), f"{path} should be in scope under the fallback default"


def test_fallback_default_ignores_a_beats_own_output_dir():
    """Without the env var, a beat's own (non-journals/docs) output dir is
    out of scope — the exact gap Step 3b's wiring in beat_worker.py closes
    for an actual beat child by setting the variable for it."""
    r = run(write_payload("notes/analyst-001-x.md"))
    assert r.returncode == 0 and r.stdout.strip() == ""


# --- Path 2: SILL_BEAT_JOURNAL_DIRS set (as spawn_beat now sets it for
# every beat child) -> that scope applies instead of the default ---

def test_beat_scope_env_covers_the_beats_own_dirs():
    r = run(write_payload("notes/analyst-001-x.md"),
            env_extra={"SILL_BEAT_JOURNAL_DIRS": "notes/:journal/"})
    assert r.returncode == 0
    assert fires(r)


def test_beat_scope_env_also_covers_a_voices_transcripts_dir():
    r = run(write_payload("logs/analyst/20260101-000000.txt"),
            env_extra={"SILL_BEAT_JOURNAL_DIRS": "notes/:logs/analyst/"})
    assert r.returncode == 0
    assert fires(r)


def test_beat_scope_env_replaces_rather_than_adds_to_the_default():
    """Once SILL_BEAT_JOURNAL_DIRS is set, it IS the scope — the house's own
    journals/ convention is just another directory the configured voices
    don't use, same as any other out-of-scope path."""
    r = run(write_payload("journals/foo.md"),
            env_extra={"SILL_BEAT_JOURNAL_DIRS": "notes/:journal/"})
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_beat_scope_env_empty_string_also_falls_back_to_the_default():
    """An empty (but present) value must not be treated as 'scope to
    nothing' — that would silently blind the hook inside a beat whose
    voices somehow derived no fragments at all. beat_worker.py never emits
    an empty value (it omits the key entirely in that case — see
    test_spawn_beat_sets_no_journal_dirs_var_when_derivation_is_empty in
    test_beat_worker.py), but this hook's own fallback must be robust to a
    stray empty value from a hand-edited environment too."""
    r = run(write_payload("journals/foo.md"),
            env_extra={"SILL_BEAT_JOURNAL_DIRS": ""})
    assert r.returncode == 0
    assert fires(r)
