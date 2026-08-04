"""Receipt guards: placeholder single-sourcing, case handling, scope, and
the never-block-on-a-down-store contract."""

import json
import os
import subprocess
import sys
from pathlib import Path

import sill

HOOKS = Path(__file__).resolve().parents[2] / "plugin" / "hooks"


def run(hook, payload, env_extra=None):
    env = {**os.environ, "SILL_BEAT_JOURNAL_DIRS": "journal/",
           "SILL_DB_CONTAINER": "sill_absent_container"}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(HOOKS / hook)],
                          input=json.dumps(payload), capture_output=True,
                          text=True, env=env, timeout=30)


def test_guard_does_not_retype_the_placeholder():
    """The literal is a wire contract; the guard must source it, not copy it.

    Brief's original expression for this test reduces algebraically to the
    direct check below: `src.split("RECEIPT_PLACEHOLDER")[-1][:0]` is always
    `""` (a zero-length slice), so the assertion is exactly `"MINT-PENDING"
    not in src` regardless of whether "RECEIPT_PLACEHOLDER" appears. Using
    the brief's own permitted substitute — a direct check that the literal
    appears zero times in the guard source, while the guard still resolves
    it at runtime (test_placeholder_line_passes exercises that path).
    """
    src = (HOOKS / "stored-slot-guard.py").read_text()
    assert "MINT-PENDING" not in src, "guard hand-types the placeholder literal"


def test_placeholder_line_passes(tmp_path):
    r = run("stored-slot-guard.py", {
        "tool_name": "Write", "tool_input": {
            "file_path": "journal/r-001.md",
            "content": f"# beat\n\n{sill.RECEIPT_PLACEHOLDER}\n"}})
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_store_unreachable_never_blocks(tmp_path):
    """A wedged store must not stop a journal from being written."""
    r = run("stored-slot-guard.py", {
        "tool_name": "Write", "tool_input": {
            "file_path": "journal/r-001.md",
            "content": "Stored: deadbeef-1111-2222-3333-444455556666\n"}})
    assert r.returncode == 0
    assert "deny" not in r.stdout


def test_out_of_scope_paths_are_ignored():
    r = run("stored-slot-guard.py", {
        "tool_name": "Write", "tool_input": {
            "file_path": "src/unrelated.py",
            "content": "Stored: deadbeef-1111-2222-3333-444455556666\n"}})
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_lowercase_stored_is_not_a_bypass():
    """Upstream's case-sensitive fast-path let 'stored:' skip the check."""
    src = (HOOKS / "stored-slot-guard.py").read_text()
    assert '"Stored" not in text' not in src, "case-sensitive fast-path reintroduced"


def test_witness_denies_unquoted_carrying_act_claim():
    r = run("tool-type-witness.py", {
        "tool_name": "Write", "tool_input": {
            "file_path": "journal/r-001.md",
            "content": "The receipt arrived by Edit after the mint.\n"}})
    assert r.returncode == 0
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_witness_exempts_quoted_and_blockquoted():
    for content in ["He wrote `arrived by Edit` in the log.\n",
                    "> arrived by Edit\n",
                    'The phrase "arrived by Edit" is a claim.\n']:
        r = run("tool-type-witness.py", {
            "tool_name": "Write",
            "tool_input": {"file_path": "journal/r-001.md", "content": content}})
        assert r.returncode == 0 and r.stdout.strip() == "", content


def test_witness_ignores_edit_tool():
    r = run("tool-type-witness.py", {
        "tool_name": "Edit", "tool_input": {
            "file_path": "journal/r-001.md",
            "new_string": "arrived by Edit"}})
    assert r.returncode == 0 and r.stdout.strip() == ""
