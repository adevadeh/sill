"""sill_cli.py's top-level argv dispatch.

build_parser()'s subparsers are split into two shapes:

  - "declaring" subcommands (seed import, db psql, verify) parse their own
    arguments with argparse's normal add_argument() calls.
  - "passthrough" subcommands (notice, identity, backfill) deliberately
    declare none of their own arguments and disable their own -h/--help, so
    every flag falls through to a nested module's own parser untouched (see
    build_parser()'s inline comments for why: one argv shape defined in one
    place, not re-derived here).

main() parses with parser.parse_known_args(argv), not parse_args(), because
the passthrough subcommands need that leniency — argparse has no per-
subparser way to ask for it selectively. The cost is that a mistyped flag on
a *declaring* subcommand (e.g. "sill seed import x.jsonl --wrogn-flag") used
to fall into the same "extra" bucket and get silently ignored instead of
raising the usual "unrecognized arguments" error, because nothing after
parse_known_args() ever inspected `extra` for those subcommands. This file
pins the fix: extra flags are rejected for every subcommand except the
three that are documented to need them.
"""
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import sill_cli  # noqa: E402


def test_typo_flag_on_seed_import_is_rejected(capsys):
    """The specimen from this task's brief: a typo'd flag on a declaring
    subcommand used to reach _cmd_seed_import's file-not-found error
    instead of argparse's own 'unrecognized arguments' — i.e. the CLI
    complained about the wrong thing, or (with a real file) not at all.

    parser.error() raises SystemExit(2) rather than returning — the same
    way parse_args() itself already behaves for every other argparse error
    in this file (an unknown subcommand, a missing required argument), so
    this matches existing behavior rather than inventing a new shape."""
    with pytest.raises(SystemExit) as exc_info:
        sill_cli.main(["seed", "import", "somefile.jsonl", "--wrogn-flag"])
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "unrecognized arguments" in err
    assert "--wrogn-flag" in err


def test_typo_flag_on_verify_is_rejected(capsys):
    with pytest.raises(SystemExit) as exc_info:
        sill_cli.main(["verify", "--nonexistent"])
    assert exc_info.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_typo_flag_on_db_psql_is_rejected(capsys):
    with pytest.raises(SystemExit) as exc_info:
        sill_cli.main(["db", "psql", "--nonexistent"])
    assert exc_info.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_seed_import_with_no_extra_flags_still_dispatches(tmp_path):
    """The fix must not reject a clean invocation — only genuine extras. An
    empty seed file is the cleanest DB-free fixture: seed_import.py reports
    'no rows' and returns success without ever needing a connection, so a
    real rc of 0 here (not argparse's 2) proves clean args reach the
    handler untouched."""
    seed_file = tmp_path / "seed.jsonl"
    seed_file.write_text("")
    rc = sill_cli.main(["seed", "import", str(seed_file)])
    assert rc == 0, "a clean invocation must not be treated as having extra arguments"


@pytest.mark.parametrize("passthrough_cmd", ["notice", "identity", "backfill"])
def test_passthrough_subcommands_still_forward_unknown_flags(passthrough_cmd, monkeypatch):
    """The leniency notice/identity/backfill actually need must survive:
    their own nested parsers see 'extra' untouched, not a top-level
    'unrecognized arguments' error."""
    seen = {}

    def fake_notice(argv):
        seen["argv"] = argv
        return 0

    def fake_identity(argv):
        seen["argv"] = argv
        return 0

    def fake_backfill(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(sill_cli, "_cmd_notice",
                         lambda args, extra: fake_notice(extra))
    monkeypatch.setattr(sill_cli, "_cmd_identity",
                         lambda args, extra: fake_identity(extra))
    monkeypatch.setattr(sill_cli, "_cmd_backfill",
                         lambda args, extra: fake_backfill(extra))

    rc = sill_cli.main([passthrough_cmd, "--some-nested-flag", "value"])
    assert rc == 0
    assert seen["argv"] == ["--some-nested-flag", "value"]
