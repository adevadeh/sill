"""
sill — top-level CLI for the Sill memory system.

Subcommands wired so far:

  sill seed import <path>   Import a JSONL methodology seed into the db
                            (Task 15)
  sill db psql              Drop into a psql shell against the db container
                            (wired in a later task)
  sill verify               Run the standard smoke-check suite
                            (wired in a later task)
  sill notice ...           Store one deliberate memory (the mint path);
                            delegates to sill.py's own parser so this
                            subcommand's argv shape lives in exactly one
                            place. Run 'sill notice --help' for its flags.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence


def _cmd_seed_import(args: argparse.Namespace, _extra: list[str]) -> int:
    # Lazy import so `sill --help` works even if psycopg2 isn't installed yet.
    from scripts.seed_import import main as _seed_import_main

    summary = _seed_import_main(args.path)
    return 0 if summary.errors == 0 else 1


def _cmd_db_psql(_args: argparse.Namespace, _extra: list[str]) -> int:
    raise NotImplementedError(
        "db psql — wired in a later task (drops into psql against $SILL_DB_CONTAINER)"
    )


def _cmd_verify(_args: argparse.Namespace, _extra: list[str]) -> int:
    raise NotImplementedError(
        "verify — wired in a later task (mirrors verify.sh smoke checks)"
    )


def _cmd_notice(args: argparse.Namespace, extra: list[str]) -> int:
    """Delegate to the mint module's own parser so 'sill notice ...' and
    'python sill.py notice ...' accept identical argv."""
    import sill

    return sill.main(["notice", *extra])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sill",
        description="Sill memory system CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    # seed
    seed = sub.add_parser("seed", help="Manage seed memory packs")
    seed_sub = seed.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")
    seed_import = seed_sub.add_parser(
        "import",
        help="Import a JSONL methodology seed into the running db",
    )
    seed_import.add_argument("path", help="Path to a JSONL seed file")
    seed_import.set_defaults(func=_cmd_seed_import)

    # db
    db = sub.add_parser("db", help="Database utilities")
    db_sub = db.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")
    db_psql = db_sub.add_parser("psql", help="Open a psql shell against the db container")
    db_psql.set_defaults(func=_cmd_db_psql)

    # verify
    verify = sub.add_parser("verify", help="Run the smoke-check suite")
    verify.set_defaults(func=_cmd_verify)

    # notice — the mint path. This subparser deliberately declares none of
    # notice's own arguments (content, --speaker, --force, --receipt-to, ...)
    # and disables its own -h/--help, so every flag INCLUDING --help falls
    # through parse_known_args() as "extra" and reaches sill.py's own parser
    # untouched. That keeps the argv shape defined in exactly one place
    # (sill.build_parser) and makes 'sill notice --help' show sill.py's real
    # flags rather than this shell's empty usage line.
    notice = sub.add_parser(
        "notice",
        help="Store one deliberate memory (the mint path). See 'sill notice --help'.",
        add_help=False,
    )
    notice.set_defaults(func=_cmd_notice)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args, extra = parser.parse_known_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 2
    try:
        return int(func(args, extra) or 0)
    except NotImplementedError as exc:
        print(f"sill: {exc}", file=sys.stderr)
        return 64  # EX_USAGE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
