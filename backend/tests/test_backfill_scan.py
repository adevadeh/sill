"""`sill backfill plan|run` — consent-scoped episodic backfill.

**The design point under test throughout:** `plan` is read-only at the
filesystem level — it stats candidate files (path, mtime, size) to report
what *would* be read, but never opens their content and never writes
anything, not even a log line. `run` is the one command that actually
opens transcript content, and only after an explicit `--confirm` flag
(mirroring identity_card's "corrupt file" refusals: name the fix, never
traceback, never proceed silently). A harness or project the human did not
select is never even ``os.listdir``'d, let alone read.

**Hard constraint for every test in this file:** never read or write the
real ``~/.claude`` or ``~/.codex`` — this machine has live installs of
both. Every test builds its own fixture tree under ``tmp_path`` and passes
it explicitly via ``--home``/``home=`` (never relying on the default,
which resolves against the real ``$HOME``). ``SILL_STATE_DIR`` is likewise
always overridden before any ``run --confirm`` call, the same idiom
``test_identity_card.py`` uses for the archive/state path.

**Why file mtime, not a per-line JSONL timestamp, drives date filtering:**
both harnesses' transcript records carry their own timestamps (verified in
``backend/tests/fixtures/{claude-transcript,codex-rollout}.jsonl``), but
reading them to determine a file's date would mean opening file content
during ``plan`` — exactly the read ``plan`` must never perform. mtime is
harness-uniform, requires no content parse, and is the same signal
``docs/beats.md`` already uses ("match by mtime") for this class of
question.

**Why no DB-call assertion looks like the rest of this repo's DB tests**
(e.g. ``test_notice.py``'s ``mock.patch.object(sill, "_query_db", ...)``):
``backfill_scan.py`` has no DB dependency to mock in the first place. This
task builds the scan-and-archive tool — a durable, undoable *filesystem*
archive (raw transcript copies + a manifest) under
``$SILL_STATE_DIR/backfill/<run_id>/``, matching the brief's own language
("the archive path and how to remove it"). Turning that archive into
queryable episodic memory rows is a separate concern this module
deliberately does not do; see ``test_module_has_no_database_dependency``
below and ``docs/onboarding/02-backfill.md``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from scripts import backfill_scan as bs

import sill_cli

BACKEND_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Fixture-tree builders — every test tree lives under tmp_path, never the
# real home directory.
# ---------------------------------------------------------------------------

def _claude_line(tool_uses: int = 0, cwd: str = "/tmp/fixture-project") -> str:
    """One Claude-shaped transcript line (the verified envelope from
    backend/tests/fixtures/claude-transcript.jsonl): type=="assistant",
    message.content[] blocks with type=="tool_use" when tool_uses > 0,
    else a plain user line. Real enough for _harness.iter_transcript_tool_uses
    to parse — used to prove `run` actually reuses that function rather
    than re-deriving the schema."""
    if tool_uses:
        blocks = [
            {"type": "tool_use", "id": f"toolu_{i}", "name": "mcp__sill__recall",
             "input": {}}
            for i in range(tool_uses)
        ]
        record = {"type": "assistant", "timestamp": "2026-01-01T00:00:00Z",
                  "sessionId": "fixture", "cwd": cwd,
                  "message": {"role": "assistant", "content": blocks}}
    else:
        record = {"type": "user", "timestamp": "2026-01-01T00:00:00Z",
                  "sessionId": "fixture", "cwd": cwd,
                  "message": {"role": "user", "content": "hi"}}
    return json.dumps(record) + "\n"


def _write_claude_session(home: Path, project: str, filename: str = "session-0.jsonl",
                           *, tool_uses: int = 0, mtime: str | None = None) -> Path:
    proj_dir = home / ".claude" / "projects" / project
    proj_dir.mkdir(parents=True, exist_ok=True)
    f = proj_dir / filename
    f.write_text(_claude_line(tool_uses=tool_uses), encoding="utf-8")
    if mtime is not None:
        _set_mtime(f, mtime)
    return f


def _write_codex_session(home: Path, *, year="2026", month="01", day="01",
                          filename: str = "rollout-fixture.jsonl",
                          mtime: str | None = None) -> Path:
    day_dir = home / ".codex" / "sessions" / year / month / day
    day_dir.mkdir(parents=True, exist_ok=True)
    f = day_dir / filename
    record = {"timestamp": f"{year}-{month}-{day}T00:00:00Z", "type": "response_item",
              "payload": {"type": "message", "role": "user",
                          "content": [{"type": "input_text", "text": "hi"}]}}
    f.write_text(json.dumps(record) + "\n", encoding="utf-8")
    if mtime is not None:
        _set_mtime(f, mtime)
    return f


def _set_mtime(path: Path, iso_date: str) -> None:
    ts = datetime.fromisoformat(iso_date).replace(tzinfo=timezone.utc).timestamp()
    os.utime(path, (ts, ts))


def _snapshot(root: Path) -> frozenset:
    """Every path under root plus its mtime and size — used to prove a
    command touched nothing at all, not just "created no new top-level
    file"."""
    if not root.exists():
        return frozenset()
    return frozenset(
        (str(p.relative_to(root)), p.stat().st_mtime, p.stat().st_size, p.is_dir())
        for p in root.rglob("*")
    )


# ---------------------------------------------------------------------------
# plan writes nothing — the central claim. Checked three ways: the scanned
# tree is untouched, the state dir gains nothing, and transcript content is
# never even opened.
# ---------------------------------------------------------------------------

def test_plan_creates_no_files_in_the_scanned_tree(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path / "state"))
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a")
    _write_codex_session(home)

    before = _snapshot(home)
    rc = bs.main(["plan", "--home", str(home)])
    capsys.readouterr()
    after = _snapshot(home)

    assert rc == 0
    assert before == after


def test_plan_writes_nothing_to_the_state_dir_either(tmp_path, monkeypatch, capsys):
    """Not a log line, not a temp file, nothing — plan's side effects are
    zero even in Sill's own state directory, not just in the scanned
    harness tree."""
    state_dir = tmp_path / "state"
    monkeypatch.setenv("SILL_STATE_DIR", str(state_dir))
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a")

    rc = bs.main(["plan", "--home", str(home)])
    capsys.readouterr()

    assert rc == 0
    assert not state_dir.exists()


def test_plan_never_opens_transcript_content(tmp_path, monkeypatch, capsys):
    """A stronger claim than 'no files created': plan must not even READ
    the content of a candidate file. Proven by making the one function
    that reads transcript content raise if called."""
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path / "state"))
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a")
    _write_codex_session(home)

    def _boom(_path):
        raise AssertionError("plan must never read transcript content")

    monkeypatch.setattr(bs._harness, "iter_transcript_tool_uses", _boom)
    rc = bs.main(["plan", "--home", str(home)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "proj-a" in out


def test_module_has_no_database_dependency(tmp_path):
    """backfill_scan is a filesystem-only tool: turning the archive it
    writes into queryable episodic memory rows is a separate concern this
    module deliberately does not implement (see module docstring above and
    docs/onboarding/02-backfill.md). There is no db-calling function here
    to mock the way test_notice.py mocks sill._query_db — the absence
    itself is the contract, so it's checked directly against the source."""
    source = Path(bs.__file__).read_text(encoding="utf-8")
    for needle in ("psycopg2", "asyncpg", "_query_db", "import sill\n", "SILL_DB_"):
        assert needle not in source, f"unexpected DB dependency marker: {needle!r}"


# ---------------------------------------------------------------------------
# plan names every project it would read
# ---------------------------------------------------------------------------

def test_plan_output_names_every_claude_project_it_would_read(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path / "state"))
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-alpha")
    _write_claude_session(home, "proj-beta")
    _write_claude_session(home, "proj-gamma")

    rc = bs.main(["plan", "--home", str(home)])
    out = capsys.readouterr().out

    assert rc == 0
    for name in ("proj-alpha", "proj-beta", "proj-gamma"):
        assert name in out


def test_plan_output_names_the_codex_bucket_when_codex_is_scanned(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path / "state"))
    home = tmp_path / "fixture-home"
    _write_codex_session(home)

    rc = bs.main(["plan", "--home", str(home)])
    out = capsys.readouterr().out

    assert rc == 0
    assert bs.CODEX_PROJECT in out


def test_plan_reports_file_counts_and_paths(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path / "state"))
    home = tmp_path / "fixture-home"
    f1 = _write_claude_session(home, "proj-a", "session-0.jsonl")
    f2 = _write_claude_session(home, "proj-a", "session-1.jsonl")

    rc = bs.main(["plan", "--home", str(home)])
    out = capsys.readouterr().out

    assert rc == 0
    assert str(f1) in out
    assert str(f2) in out
    assert "2 file" in out  # exact wording covered by render_plan tests below


def test_plan_on_empty_home_reports_nothing_found_and_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path / "state"))
    home = tmp_path / "fixture-home"
    home.mkdir()

    rc = bs.main(["plan", "--home", str(home)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "0 file" in out or "Nothing" in out


# ---------------------------------------------------------------------------
# run without --confirm refuses, naming the flag, and touches nothing
# ---------------------------------------------------------------------------

def test_run_without_confirm_exits_nonzero_and_names_the_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path / "state"))
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a")

    rc = bs.main(["run", "--home", str(home)])
    err = capsys.readouterr().err

    assert rc != 0
    assert "--confirm" in err


def test_run_without_confirm_creates_no_archive(tmp_path, monkeypatch, capsys):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("SILL_STATE_DIR", str(state_dir))
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a")

    bs.main(["run", "--home", str(home)])
    capsys.readouterr()

    assert not (state_dir / "backfill").exists()


def test_run_without_confirm_never_reads_transcript_content(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path / "state"))
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a")

    def _boom(_path):
        raise AssertionError("refused run must never read transcript content")

    monkeypatch.setattr(bs._harness, "iter_transcript_tool_uses", _boom)
    rc = bs.main(["run", "--home", str(home)])
    capsys.readouterr()

    assert rc != 0


# ---------------------------------------------------------------------------
# date-range filtering excludes what it should — direct scan() calls for
# precision, plus one CLI-level check that the flags actually wire through.
# ---------------------------------------------------------------------------

def test_since_until_filters_by_file_mtime(tmp_path):
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a", "old.jsonl", mtime="2026-01-01")
    _write_claude_session(home, "proj-a", "mid.jsonl", mtime="2026-03-01")
    _write_claude_session(home, "proj-a", "new.jsonl", mtime="2026-06-01")

    result = bs.scan(home, since=date(2026, 2, 1), until=date(2026, 4, 1))

    names = {f.path.name for f in result.files}
    assert names == {"mid.jsonl"}


def test_since_only_excludes_files_before_it(tmp_path):
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a", "old.jsonl", mtime="2026-01-01")
    _write_claude_session(home, "proj-a", "new.jsonl", mtime="2026-06-01")

    result = bs.scan(home, since=date(2026, 3, 1))

    names = {f.path.name for f in result.files}
    assert names == {"new.jsonl"}


def test_until_only_excludes_files_after_it(tmp_path):
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a", "old.jsonl", mtime="2026-01-01")
    _write_claude_session(home, "proj-a", "new.jsonl", mtime="2026-06-01")

    result = bs.scan(home, until=date(2026, 3, 1))

    names = {f.path.name for f in result.files}
    assert names == {"old.jsonl"}


def test_since_and_until_are_inclusive_on_both_ends(tmp_path):
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a", "boundary-since.jsonl", mtime="2026-02-01")
    _write_claude_session(home, "proj-a", "boundary-until.jsonl", mtime="2026-04-01")

    result = bs.scan(home, since=date(2026, 2, 1), until=date(2026, 4, 1))

    names = {f.path.name for f in result.files}
    assert names == {"boundary-since.jsonl", "boundary-until.jsonl"}


def test_cli_since_until_flags_filter_plan_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path / "state"))
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a", "old.jsonl", mtime="2026-01-01")
    _write_claude_session(home, "proj-a", "new.jsonl", mtime="2026-06-01")

    rc = bs.main(["plan", "--home", str(home), "--since", "2026-05-01"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "new.jsonl" in out
    assert "old.jsonl" not in out


def test_malformed_date_flag_is_a_clean_usage_error_not_a_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path / "state"))
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a")

    rc = bs.main(["plan", "--home", str(home), "--since", "not-a-date"])
    err = capsys.readouterr().err

    assert rc != 0
    assert "--since" in err


# ---------------------------------------------------------------------------
# an unselected harness is never scanned — black-box (no codex files in the
# result) and white-box (the codex discoverer is never even called).
# ---------------------------------------------------------------------------

def test_unselected_harness_produces_no_files_in_the_result(tmp_path):
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a")
    _write_codex_session(home)

    result = bs.scan(home, harnesses=["claude"])

    assert all(f.harness == "claude" for f in result.files)
    assert result.scanned_harnesses == ("claude",)


def test_unselected_harness_discoverer_is_never_called(tmp_path, monkeypatch):
    """Stronger than the black-box check above: proves codex is never even
    traversed, not just filtered out afterward — same idiom test_notice.py
    uses for 'no DB' (mock.patch a function to raise, show it's never hit)."""
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a")
    _write_codex_session(home)

    def _boom(_root):
        raise AssertionError("codex must not be scanned when not selected")

    monkeypatch.setattr(bs, "_discover_codex", _boom)
    result = bs.scan(home, harnesses=["claude"])

    assert len(result.files) == 1


def test_cli_plan_with_harnesses_claude_only_omits_codex_bucket(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path / "state"))
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a")
    _write_codex_session(home)

    rc = bs.main(["plan", "--home", str(home), "--harnesses", "claude"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "proj-a" in out
    assert bs.CODEX_PROJECT not in out


def test_unknown_harness_name_is_a_clean_usage_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path / "state"))
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a")

    rc = bs.main(["plan", "--home", str(home), "--harnesses", "cursor"])
    err = capsys.readouterr().err

    assert rc != 0
    assert "cursor" in err


# ---------------------------------------------------------------------------
# --projects scopes within a harness too (not required by the brief's own
# five cases, but the same consent-granularity claim applied one level
# deeper, and cheap to pin down).
# ---------------------------------------------------------------------------

def test_projects_filter_scopes_to_selected_projects_only(tmp_path):
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a")
    _write_claude_session(home, "proj-b")

    result = bs.scan(home, projects=["proj-a"])

    assert {f.project for f in result.files} == {"proj-a"}


def test_cli_plan_with_projects_flag_omits_unselected_project(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path / "state"))
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a")
    _write_claude_session(home, "proj-b")

    rc = bs.main(["plan", "--home", str(home), "--projects", "proj-a"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "proj-a" in out
    assert "proj-b" not in out


def test_projects_flag_handles_real_claude_dash_prefixed_project_names(tmp_path, monkeypatch, capsys):
    """Real Claude Code project directory names always start with '-' (the
    encoded-cwd convention — see harness_root's docstring and any real
    ~/.claude/projects listing). Passed as '--projects <value>' with a
    space, argparse reads a dash-leading value as another flag and fails
    with 'expected one argument' — a standard, well-known argparse/getopt
    convention, not a bug in this module, but one that WILL bite an
    operator following this doc if undocumented. The '--flag=value' form
    sidesteps it; --projects's own help text says so (see
    docs/onboarding/02-backfill.md's callout, verified against this exact
    command)."""
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path / "state"))
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "-Users-alex-code-orrery")
    _write_claude_session(home, "-Users-alex-code-lighthouse")

    rc = bs.main(["plan", "--home", str(home), "--projects=-Users-alex-code-orrery"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "-Users-alex-code-orrery" in out
    assert "-Users-alex-code-lighthouse" not in out


# ---------------------------------------------------------------------------
# run --confirm actually archives the right files, reuses
# _harness.iter_transcript_tool_uses (rather than re-deriving transcript
# shapes) for the manifest's per-file receipt, and stays scoped.
# ---------------------------------------------------------------------------

def test_run_with_confirm_archives_selected_files_and_writes_manifest(tmp_path, monkeypatch, capsys):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("SILL_STATE_DIR", str(state_dir))
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a", "session-0.jsonl")

    rc = bs.main(["run", "--home", str(home), "--confirm"])
    out = capsys.readouterr().out

    assert rc == 0
    archive_dirs = list((state_dir / "backfill").iterdir())
    assert len(archive_dirs) == 1
    archive_dir = archive_dirs[0]

    manifest = json.loads((archive_dir / "manifest.json").read_text())
    assert len(manifest["files"]) == 1
    entry = manifest["files"][0]
    assert entry["harness"] == "claude"
    assert entry["project"] == "proj-a"

    archived_copy = archive_dir / "claude" / "proj-a" / "session-0.jsonl"
    assert archived_copy.exists()
    assert archived_copy.read_text() == (home / ".claude" / "projects" / "proj-a" / "session-0.jsonl").read_text()

    assert str(archive_dir) in out


def test_run_with_confirm_counts_tool_uses_via_the_harness_module(tmp_path, monkeypatch, capsys):
    """Positive control for test_plan_never_opens_transcript_content: run
    DOES read content, via the reused _harness.iter_transcript_tool_uses —
    proven by a fixture file with a known tool-use count landing correctly
    in the manifest."""
    state_dir = tmp_path / "state"
    monkeypatch.setenv("SILL_STATE_DIR", str(state_dir))
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a", "session-0.jsonl", tool_uses=3)

    rc = bs.main(["run", "--home", str(home), "--confirm"])
    capsys.readouterr()

    assert rc == 0
    archive_dir = next((state_dir / "backfill").iterdir())
    manifest = json.loads((archive_dir / "manifest.json").read_text())
    assert manifest["files"][0]["tool_use_count"] == 3


def test_run_with_confirm_respects_harness_scope(tmp_path, monkeypatch, capsys):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("SILL_STATE_DIR", str(state_dir))
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a")
    _write_codex_session(home)

    rc = bs.main(["run", "--home", str(home), "--harnesses", "claude", "--confirm"])
    capsys.readouterr()

    assert rc == 0
    archive_dir = next((state_dir / "backfill").iterdir())
    assert not (archive_dir / "codex").exists()
    manifest = json.loads((archive_dir / "manifest.json").read_text())
    assert all(e["harness"] == "claude" for e in manifest["files"])


def test_run_with_confirm_respects_date_scope(tmp_path, monkeypatch, capsys):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("SILL_STATE_DIR", str(state_dir))
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a", "old.jsonl", mtime="2026-01-01")
    _write_claude_session(home, "proj-a", "new.jsonl", mtime="2026-06-01")

    rc = bs.main(["run", "--home", str(home), "--since", "2026-05-01", "--confirm"])
    capsys.readouterr()

    assert rc == 0
    archive_dir = next((state_dir / "backfill").iterdir())
    archived_names = {p.name for p in (archive_dir / "claude" / "proj-a").iterdir()}
    assert archived_names == {"new.jsonl"}


# ---------------------------------------------------------------------------
# the undo path: rm -rf the archive dir the receipt names, and it's gone.
# No special "undo" code exists — the archive being a plain directory *is*
# the undo mechanism; this test pins that down as a behavioral contract.
# ---------------------------------------------------------------------------

def test_archive_dir_is_undoable_by_plain_removal(tmp_path, monkeypatch, capsys):
    import shutil

    state_dir = tmp_path / "state"
    monkeypatch.setenv("SILL_STATE_DIR", str(state_dir))
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a")

    bs.main(["run", "--home", str(home), "--confirm"])
    capsys.readouterr()
    archive_dir = next((state_dir / "backfill").iterdir())
    assert archive_dir.exists()

    shutil.rmtree(archive_dir)
    assert not archive_dir.exists()
    # and the original, source transcript is untouched by either the run or the undo
    assert (home / ".claude" / "projects" / "proj-a" / "session-0.jsonl").exists()


# ---------------------------------------------------------------------------
# --home / $HOME resolution — never touches the real home directory by
# default in these tests; the default-resolution helper is checked in
# isolation (pure env-var logic, no disk access) rather than by ever
# calling scan()/main() without an explicit --home.
# ---------------------------------------------------------------------------

def test_default_home_reads_the_home_env_var(monkeypatch):
    monkeypatch.setenv("HOME", "/nonexistent/fixture/home")
    assert bs._default_home() == Path("/nonexistent/fixture/home")


# ---------------------------------------------------------------------------
# CLI wiring — `sill backfill plan|run` reaches this module through
# sill_cli.py, mirroring test_identity_card.py's
# test_sill_cli_wires_identity_show/init_and_set.
# ---------------------------------------------------------------------------

def test_sill_cli_wires_backfill_plan(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path / "state"))
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a")

    rc = sill_cli.main(["backfill", "plan", "--home", str(home)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "proj-a" in out


def test_sill_cli_wires_backfill_run_without_confirm(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path / "state"))
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a")

    rc = sill_cli.main(["backfill", "run", "--home", str(home)])
    err = capsys.readouterr().err

    assert rc != 0
    assert "--confirm" in err


# ---------------------------------------------------------------------------
# Real process boundary — mirrors test_identity_card.py's
# test_show_on_missing_file_exits_zero_as_a_real_subprocess: proves plan
# and run behave correctly as an actual OS process, with HOME and
# SILL_STATE_DIR both pointed at fixture paths, so nothing about this
# guarantee depends on being inside the same Python interpreter as the
# test.
# ---------------------------------------------------------------------------

def _run_module(args, env_extra, cwd=BACKEND_ROOT, timeout=15):
    env = {**os.environ, **env_extra}
    return subprocess.run(
        [sys.executable, "-m", "scripts.backfill_scan", *args],
        cwd=cwd, capture_output=True, text=True, env=env, timeout=timeout,
    )


def test_plan_as_a_real_subprocess_with_fixture_home(tmp_path):
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a")
    fake_real_home = tmp_path / "not-the-real-home"
    fake_real_home.mkdir()

    r = _run_module(["plan", "--home", str(home)],
                     {"HOME": str(fake_real_home), "SILL_STATE_DIR": str(tmp_path / "state")})
    assert r.returncode == 0, r.stderr
    assert "proj-a" in r.stdout
    assert "Traceback" not in r.stderr


def test_run_confirm_as_a_real_subprocess_with_fixture_home(tmp_path):
    home = tmp_path / "fixture-home"
    _write_claude_session(home, "proj-a")
    fake_real_home = tmp_path / "not-the-real-home"
    fake_real_home.mkdir()
    state_dir = tmp_path / "state"

    r = _run_module(["run", "--home", str(home), "--confirm"],
                     {"HOME": str(fake_real_home), "SILL_STATE_DIR": str(state_dir)})
    assert r.returncode == 0, r.stderr
    assert "Traceback" not in r.stderr
    assert (state_dir / "backfill").exists()
    # the process-level fixture home for "the real HOME" is never touched
    assert list(fake_real_home.iterdir()) == []
