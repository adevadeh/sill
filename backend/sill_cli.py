"""
sill — top-level CLI for the Sill memory system.

Subcommands are wired progressively across the extraction tasks:

  sill seed import <path>   Import a JSONL methodology seed into the db
                            (wired in Task 15)
  sill db psql              Drop into a psql shell against the db container
                            (wired in a later task)
  sill verify               Run the standard smoke-check suite
                            (wired in a later task)

For now this is a stub: each subcommand parses cleanly and prints usage,
but the underlying implementation raises NotImplementedError noting which
task will wire it.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence


def _cmd_seed_import(args: argparse.Namespace) -> int:
    raise NotImplementedError(
        f"seed import {args.path!r} — wired in Task 15 (seed_import.py + roundtrip test)"
    )


def _cmd_db_psql(_args: argparse.Namespace) -> int:
    raise NotImplementedError(
        "db psql — wired in a later task (drops into psql against $SILL_DB_CONTAINER)"
    )


def _cmd_verify(_args: argparse.Namespace) -> int:
    raise NotImplementedError(
        "verify — wired in a later task (mirrors verify.sh smoke checks)"
    )


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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 2
    try:
        return int(func(args) or 0)
    except NotImplementedError as exc:
        print(f"sill: {exc}", file=sys.stderr)
        return 64  # EX_USAGE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
