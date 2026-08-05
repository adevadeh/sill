"""Receipt guards: placeholder single-sourcing, case handling, scope, and
the never-block-on-a-down-store contract."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_lowercase_receipt_with_trailing_backtick_reaches_the_store_check(tmp_path):
    """Regression for the case-sensitive head-split bug the lowercase fast
    path exposed: `head = line.split("Stored", 1)[0]` is case-SENSITIVE, so
    on a lowercase 'stored:' line it never finds a split point and returns
    the WHOLE LINE as head. The mention exemption ("`" in head) then fires
    on any backtick anywhere on the line — including a harmless trailing
    parenthetical — falsely exempting a genuine lowercase receipt.

    Same non-distinguishing-outcome problem this file's docstrings already
    name: a store failing open produces the same 'exit 0, no deny' as the
    exemption bug firing, so — like the test above — this does not assert
    on stdout. It proves the guard reached its per-id store-check
    subprocess call via the docker-invocation marker.
    """
    marker = tmp_path / "docker-invoked.marker"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(f"#!/bin/sh\necho invoked >> {marker}\nexit 1\n")
    fake_docker.chmod(0o755)

    r = run("stored-slot-guard.py", {
        "tool_name": "Write", "tool_input": {
            "file_path": "journal/r-001.md",
            "content": "stored: deadbeef-1111-2222-3333-444455556666 (see `note`)\n"}},
        env_extra={"PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}"})

    # Same outward contract as test_store_unreachable_never_blocks: a down
    # store must never block the write.
    assert r.returncode == 0
    assert "deny" not in r.stdout
    # The property this test actually exists to check: the trailing
    # backtick — which is not part of "Stored" at all — must not exempt a
    # genuine lowercase receipt line from the store check.
    assert marker.exists(), (
        "guard never invoked docker — the case-sensitive head split let a "
        "trailing backtick on this lowercase receipt line exempt it before "
        "reaching the per-id store-check code path"
    )


def test_guard_denies_when_the_store_genuinely_lacks_the_id(tmp_path):
    """The guard's actual point, never exercised by any test above: every
    one of them runs against an absent/unreachable container, which is the
    fail-OPEN path (test_store_unreachable_never_blocks) — a down store
    must never block a write, so an unreachable lookup is skipped, not
    treated as missing. This test is the other branch: the store answers
    successfully (returncode 0) and truthfully reports zero matching rows,
    so the guard must deny. Uses this file's own fake-docker technique (see
    test_lowercase_receipt_reaches_the_store_check above), but the fake
    behaves like a reachable store this time (`echo 0`, exit 0) instead of
    an unreachable one (`exit 1`). This is docs/hooks.md's own canned test
    for this hook (its stored-slot-guard section, 'Canned test')."""
    fake_docker = tmp_path / "docker"
    fake_docker.write_text("#!/bin/sh\necho 0\n")
    fake_docker.chmod(0o755)

    r = run("stored-slot-guard.py", {
        "tool_name": "Write", "tool_input": {
            "file_path": "journal/r-001.md",
            "content": "Stored: deadbeef-1111-2222-3333-444455556666\n"}},
        env_extra={"PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}"})

    assert r.returncode == 0
    out = json.loads(r.stdout)
    hook_out = out["hookSpecificOutput"]
    assert hook_out["permissionDecision"] == "deny"
    assert "deadbeef" in hook_out["permissionDecisionReason"]


@pytest.mark.parametrize("hook", ["stored-slot-guard.py", "tool-type-witness.py"])
@pytest.mark.parametrize("payload", ["[]", "null", '"just a string"', "42"])
def test_guards_exit_zero_on_a_non_object_json_payload(hook, payload):
    """Both guards call data.get(...) straight after json.load(sys.stdin)
    without checking the payload is actually a JSON object first. A
    non-object top-level payload — a bare list, null, string, or number —
    is valid JSON that parses without error, so the bare `except Exception`
    around json.load() does not catch it; unguarded, .get() on a list/None/
    str/int raises AttributeError uncaught, exit 1. Every hook in this
    suite is required to exit 0 on every path (see test_hook_safety.py's
    module docstring for the class of bug this is)."""
    r = subprocess.run([sys.executable, str(HOOKS / hook)],
                       input=payload, capture_output=True, text=True,
                       env={**os.environ, "SILL_BEAT_JOURNAL_DIRS": "journal/"},
                       timeout=15)
    assert r.returncode == 0, f"{hook} on payload {payload!r}: {r.stderr}"


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
