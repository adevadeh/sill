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


def test_lowercase_receipt_reaches_the_store_check(tmp_path):
    """End-to-end regression for the lowercase fast-path fix (the source-level
    check above only proves the old literal string is absent).

    'Exit 0, no deny' is NOT proof the fast path works: a fail-open store
    (test_store_unreachable_never_blocks) produces the exact same external
    outcome as the old bypass firing again — a lowercase 'stored:' line that
    used to skip the whole check also exited 0 with no deny. So this test
    does not assert on stdout at all. It proves the fast path did NOT
    short-circuit by observing that the guard actually reached its per-id
    store-check subprocess call: a fake `docker` placed first on PATH
    records whether it was invoked. Under the old `"Stored" not in text`
    fast path, a lowercase receipt line would return at that first check,
    and the marker would never be written.
    """
    marker = tmp_path / "docker-invoked.marker"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(f"#!/bin/sh\necho invoked >> {marker}\nexit 1\n")
    fake_docker.chmod(0o755)

    r = run("stored-slot-guard.py", {
        "tool_name": "Write", "tool_input": {
            "file_path": "journal/r-001.md",
            "content": "stored: deadbeef-1111-2222-3333-444455556666\n"}},
        env_extra={"PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}"})

    # Same outward contract as test_store_unreachable_never_blocks: a down
    # store must never block the write.
    assert r.returncode == 0
    assert "deny" not in r.stdout
    # The property this test actually exists to check: the fast path let
    # this lowercase line through to the code that shells out to the store.
    assert marker.exists(), (
        "guard never invoked docker — the lowercase fast path short-circuited "
        "before reaching the per-id store-check code path"
    )


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
