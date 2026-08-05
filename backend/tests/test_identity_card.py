"""The identity card: `sill identity show|init|set`, backed by
$SILL_STATE_DIR/identity.json (default $XDG_STATE_HOME/sill/identity.json,
else ~/.local/state/sill/identity.json — never /tmp).

Every test isolates the state directory via SILL_STATE_DIR (monkeypatch env,
the same idiom test_track_reuse.py uses for SILL_LOG_DIR) so nothing here
ever reads or writes the real ~/.local/state. The one exception is
test_default_state_dir_is_never_under_tmp, which mirrors
test_beat_worker.py::test_state_path_is_never_tmp exactly: it calls
default_state_dir() with no override and only stringifies the result — no
filesystem access happens, so it's safe to run against whatever the real
environment resolves to.

Central design point under test throughout: `name: null` is a value (not
yet christened), not a gap, and both a missing identity file and a corrupt
one must degrade to a plain, exit-0 report — never a traceback — because
this file exists to be the first thing an instance reads about itself.
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from scripts import identity_card as ic

import sill_cli

BACKEND_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# State path resolution — SILL_STATE_DIR > XDG_STATE_HOME > ~/.local/state,
# same shape as beat_worker.default_state_path() one level up (a directory
# here, a full file path there).
# ---------------------------------------------------------------------------

def test_default_state_dir_is_never_under_tmp(monkeypatch):
    """The upstream lesson: rotation state in /tmp was wiped by a reboot and
    silenced a worker for five days. The identity card guards the same way."""
    monkeypatch.delenv("SILL_STATE_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    assert "/tmp" not in str(ic.default_state_dir())


def test_sill_state_dir_env_overrides_default(tmp_path, monkeypatch):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path / "custom"))
    assert ic.default_state_dir() == tmp_path / "custom"


def test_xdg_state_home_used_when_sill_state_dir_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("SILL_STATE_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert ic.default_state_dir() == tmp_path / "sill"


def test_identity_path_is_state_dir_slash_identity_json(tmp_path, monkeypatch):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path))
    assert ic.identity_path() == tmp_path / "identity.json"


# ---------------------------------------------------------------------------
# init — creates the file with name: null and a born_at timestamp; idempotent
# ---------------------------------------------------------------------------

def test_init_on_fresh_state_dir_creates_file_with_null_name_and_born_at(tmp_path, monkeypatch):
    state_dir = tmp_path / "fresh"
    monkeypatch.setenv("SILL_STATE_DIR", str(state_dir))
    assert not state_dir.exists()

    rc = ic.main(["init"])
    assert rc == 0

    path = state_dir / "identity.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["name"] is None
    assert data["born_at"]
    datetime.fromisoformat(data["born_at"])  # a real timestamp, not a placeholder


def test_init_is_idempotent_and_preserves_born_at(tmp_path, monkeypatch):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path))
    assert ic.main(["init"]) == 0
    first_born_at = json.loads((tmp_path / "identity.json").read_text())["born_at"]

    rc = ic.main(["init"])
    assert rc == 0
    second_born_at = json.loads((tmp_path / "identity.json").read_text())["born_at"]
    assert first_born_at == second_born_at


def test_init_on_corrupt_file_refuses_rather_than_clobbering(tmp_path, monkeypatch):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path))
    path = tmp_path / "identity.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json at all")

    rc = ic.main(["init"])
    assert rc != 0
    assert path.read_text() == "not json at all"  # untouched


# ---------------------------------------------------------------------------
# show — missing file and corrupt file both degrade to a plain report and
# exit 0, never a traceback.
# ---------------------------------------------------------------------------

def test_show_on_missing_file_exits_zero_and_reports_not_yet_initialized(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path / "nonexistent"))
    rc = ic.main(["show"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "not yet initialized" in out.lower()


def test_show_on_corrupt_file_reports_corruption_and_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path))
    path = tmp_path / "identity.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not valid json")

    rc = ic.main(["show"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "corrupt" in out.lower()


def test_show_on_valid_but_non_object_json_is_also_treated_as_corrupt(tmp_path, monkeypatch, capsys):
    """Valid JSON that isn't a JSON object (e.g. a bare list or string) is
    just as unreadable as an identity card as malformed JSON — same
    plain-report-and-exit-0 path, not a KeyError/AttributeError traceback
    from treating a list like a dict."""
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path))
    path = tmp_path / "identity.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]")

    rc = ic.main(["show"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "corrupt" in out.lower()


def test_show_when_identity_path_is_a_directory_exits_zero_not_traceback(tmp_path, monkeypatch, capsys):
    """load() can fail with a bare OSError (IsADirectoryError here) that
    isn't IdentityCorrupt — a path.exists() check alone doesn't rule this
    out, since a directory 'exists' too. The corrupt-file handling in show/
    init/set must catch this class of failure as well, not just malformed
    JSON, to actually deliver on 'never a traceback'."""
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path))
    (tmp_path / "identity.json").mkdir(parents=True)  # a directory, not a file

    rc = ic.main(["show"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "corrupt" in out.lower()


def test_show_on_fresh_identity_reports_name_as_explicit_null(tmp_path, monkeypatch, capsys):
    """The design point: name: null means *not yet christened*, an explicit
    value — and show must say so in words, not just leave the field blank."""
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path))
    ic.main(["init"])
    capsys.readouterr()  # discard init's own output

    rc = ic.main(["show"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "not yet christened" in out.lower()


# ---------------------------------------------------------------------------
# show / init must never traceback — proven at the real process boundary,
# not just as a Python return value, mirroring test_hook_safety.py's
# "actually run it as a subprocess" style for the same class of claim.
# ---------------------------------------------------------------------------

def _run_module(args, env_extra, cwd=BACKEND_ROOT, timeout=15):
    import os
    env = {**os.environ, **env_extra}
    return subprocess.run(
        [sys.executable, "-m", "scripts.identity_card", *args],
        cwd=cwd, capture_output=True, text=True, env=env, timeout=timeout,
    )


def test_show_on_missing_file_exits_zero_as_a_real_subprocess(tmp_path):
    r = _run_module(["show"], {"SILL_STATE_DIR": str(tmp_path / "nope")})
    assert r.returncode == 0, r.stderr
    assert "not yet initialized" in r.stdout.lower()
    assert "Traceback" not in r.stderr


def test_show_on_corrupt_file_exits_zero_as_a_real_subprocess(tmp_path):
    tmp_path.joinpath("identity.json").write_text("{ not json")
    r = _run_module(["show"], {"SILL_STATE_DIR": str(tmp_path)})
    assert r.returncode == 0, r.stderr
    assert "corrupt" in r.stdout.lower()
    assert "Traceback" not in r.stderr


# ---------------------------------------------------------------------------
# set — records name + charter_path + christened_at; refuses to clobber a
# corrupt file; works without a prior init; engine/scope/harnesses persist.
# ---------------------------------------------------------------------------

def test_set_name_and_charter_records_both_plus_christened_at(tmp_path, monkeypatch):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path))
    rc = ic.main(["set", "--name", "Ada", "--charter", "/path/to/charter.md"])
    assert rc == 0

    data = json.loads((tmp_path / "identity.json").read_text())
    assert data["name"] == "Ada"
    assert data["charter_path"] == "/path/to/charter.md"
    assert data["christened_at"]
    datetime.fromisoformat(data["christened_at"])


def test_set_without_prior_init_creates_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path))
    assert not (tmp_path / "identity.json").exists()

    rc = ic.main(["set", "--name", "Ada"])
    assert rc == 0
    data = json.loads((tmp_path / "identity.json").read_text())
    assert data["name"] == "Ada"
    assert data["born_at"]


def test_christened_at_is_set_once_and_does_not_move_on_rename(tmp_path, monkeypatch):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path))
    ic.main(["set", "--name", "Ada"])
    first = json.loads((tmp_path / "identity.json").read_text())["christened_at"]

    ic.main(["set", "--name", "Beatrix"])
    second = json.loads((tmp_path / "identity.json").read_text())["christened_at"]
    assert first == second
    assert json.loads((tmp_path / "identity.json").read_text())["name"] == "Beatrix"


def test_set_on_corrupt_file_refuses_rather_than_silently_overwriting(tmp_path, monkeypatch):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path))
    path = tmp_path / "identity.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json at all")

    rc = ic.main(["set", "--name", "Ada"])
    assert rc != 0
    assert path.read_text() == "not json at all"  # nothing silently clobbered


def test_set_engine_scope_and_harnesses_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path))
    ic.main(["init"])
    rc = ic.main(["set", "--engine", "claude", "--scope", "project",
                  "--harnesses", "claude,codex"])
    assert rc == 0

    data = json.loads((tmp_path / "identity.json").read_text())
    assert data["engine"] == "claude"
    assert data["scope"] == "project"
    assert data["harnesses"] == ["claude", "codex"]
    assert data["name"] is None  # unrelated fields untouched


def test_set_with_no_fields_is_a_usage_error(tmp_path, monkeypatch):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path))
    rc = ic.main(["set"])
    assert rc != 0


# ---------------------------------------------------------------------------
# The file round-trips as valid JSON — not just parseable by our own reader,
# but by the stdlib json module directly, with the full field set present.
# ---------------------------------------------------------------------------

def test_file_round_trips_as_valid_json_with_full_field_set(tmp_path, monkeypatch):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path))
    assert ic.main(["init"]) == 0

    raw = (tmp_path / "identity.json").read_text()
    data = json.loads(raw)  # must not raise
    assert set(ic.FIELDS) <= set(data.keys())


# ---------------------------------------------------------------------------
# CLI wiring — `sill identity ...` reaches this module through sill_cli.py,
# not just a hand-typed stand-in. Mirrors test_adapter_conformance.py's
# "drive sill_cli.main() end to end" style for the notice subcommand.
# ---------------------------------------------------------------------------

def test_sill_cli_wires_identity_show(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path))
    rc = sill_cli.main(["identity", "show"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "not yet initialized" in out.lower()


def test_sill_cli_wires_identity_init_and_set(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SILL_STATE_DIR", str(tmp_path))
    assert sill_cli.main(["identity", "init"]) == 0
    capsys.readouterr()

    assert sill_cli.main(["identity", "set", "--name", "Ada",
                           "--charter", "/x/charter.md"]) == 0
    data = json.loads((tmp_path / "identity.json").read_text())
    assert data["name"] == "Ada"
    assert data["charter_path"] == "/x/charter.md"
