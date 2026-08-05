#!/usr/bin/env python3
"""
identity_card — the file an instance reads to learn its own name, engine,
and charter, before anyone asks it to think about anything.

The motivating failure this exists to prevent: a persistent agent that did
not know its own model, had no name, and was handed unbounded reflection.
`sill identity show|init|set` reads and writes one JSON file,
$SILL_STATE_DIR/identity.json (default $XDG_STATE_HOME/sill/identity.json,
else ~/.local/state/sill/identity.json — deliberately never /tmp; see
default_state_dir() below and backend/beat_worker.py's
default_state_path(), the same shape one level up: a directory here, a
full rotation-state file path there).

Design point worth stating precisely, because it drives every choice below:
`name: null` means *not yet christened* — an explicit value this file can
hold, not the absence of one. `show` on a missing or corrupt file must
report that state in plain words and exit 0. Never a traceback: this file
is read by an agent trying to orient, and a stack trace there is the
opposite of orientation. See docs/identity.md for the full field reference.

Usage:
    sill identity show                                   # or: python3 -m scripts.identity_card show
    sill identity init
    sill identity set --name Ada --charter path/to/charter.md
    sill identity set --engine claude --scope project --harnesses claude,codex
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Field order here is the file's on-disk key order (new_identity() builds
# the dict in this order, and every mutation below reuses that dict rather
# than rebuilding one, so the order is stable across init -> set -> set).
FIELDS = ("name", "charter_path", "born_at", "engine", "scope", "harnesses", "christened_at")


class IdentityCorrupt(Exception):
    """The identity file exists but cannot be read as a JSON object —
    malformed JSON, or valid JSON that isn't an object (a bare list, a
    string, a number). Raised by load(); every caller in this module
    catches it explicitly and reports plainly rather than letting it
    propagate as a traceback."""


# ---------------------------------------------------------------------------
# State path resolution (never /tmp — see the module docstring)
# ---------------------------------------------------------------------------

def default_state_dir() -> Path:
    """Resolve the Sill state directory: SILL_STATE_DIR if set, else the XDG
    state dir. Deliberately never /tmp: the upstream lesson this guards
    against is a reboot silently clearing /tmp and, in the beat worker's
    rotation state, silencing a running instance for five days with no
    record it happened.
    """
    override = os.environ.get("SILL_STATE_DIR")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "sill"


def identity_path() -> Path:
    return default_state_dir() / "identity.json"


# ---------------------------------------------------------------------------
# Reading and writing the card
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_identity() -> dict[str, Any]:
    """A freshly-born identity record. name is explicitly None: 'not yet
    christened' is a value this file holds, not a key it's missing."""
    return {
        "name": None,
        "charter_path": None,
        "born_at": _now_iso(),
        "engine": None,
        "scope": None,
        "harnesses": [],
        "christened_at": None,
    }


def load(path: Path) -> dict[str, Any]:
    """Read and parse the identity file. Raises IdentityCorrupt if the
    content isn't a valid JSON object, or an OSError subclass (missing,
    permission denied, a directory sitting where the file should be, ...)
    for anything that goes wrong just reading it. Callers that must never
    traceback (every CLI command below) catch `(IdentityCorrupt, OSError)`
    as one pair rather than enumerating every OSError subclass by hand."""
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IdentityCorrupt(f"invalid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise IdentityCorrupt(f"expected a JSON object, found {type(data).__name__}")
    return data


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init(_args: argparse.Namespace) -> int:
    path = identity_path()
    if path.exists():
        try:
            existing = load(path)
        except (IdentityCorrupt, OSError) as exc:
            print(
                f"identity: {path} exists but is corrupt ({exc}) — refusing to "
                f"overwrite it. Fix it by hand, or remove it and run "
                f"'sill identity init' again to start fresh.",
                file=sys.stderr,
            )
            return 1
        print(f"identity: already initialized at {path} (born {existing.get('born_at', 'unknown')})")
        return 0

    identity = new_identity()
    _write(path, identity)
    print(f"identity: initialized at {path} (born {identity['born_at']})")
    return 0


def _display(value: Any) -> str:
    """Render a field for 'show': None as the literal 'null' (matching the
    file's own JSON vocabulary, not Python's capital-N repr), lists/dicts as
    compact JSON, everything else as-is."""
    if value is None:
        return "null"
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)


def cmd_show(_args: argparse.Namespace) -> int:
    path = identity_path()
    if not path.exists():
        print(
            f"identity: not yet initialized (no file at {path}). "
            f"Run 'sill identity init' to create one."
        )
        return 0

    try:
        data = load(path)
    except (IdentityCorrupt, OSError) as exc:
        print(
            f"identity: {path} exists but is corrupt ({exc}) — not safe to read. "
            f"Fix it by hand, or remove it and run 'sill identity init' to start fresh."
        )
        return 0

    name = data.get("name")
    print(f"identity: {path}")
    print(f"  name:          {'null (not yet christened)' if name is None else name}")
    print(f"  charter_path:  {_display(data.get('charter_path'))}")
    print(f"  born_at:       {_display(data.get('born_at'))}")
    print(f"  engine:        {_display(data.get('engine'))}")
    print(f"  scope:         {_display(data.get('scope'))}")
    print(f"  harnesses:     {_display(data.get('harnesses'))}")
    print(f"  christened_at: {_display(data.get('christened_at'))}")
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    path = identity_path()
    if path.exists():
        try:
            data = load(path)
        except (IdentityCorrupt, OSError) as exc:
            print(
                f"identity: {path} exists but is corrupt ({exc}) — refusing to set "
                f"fields onto a corrupt file. Fix it by hand, or remove it and run "
                f"'sill identity init' first.",
                file=sys.stderr,
            )
            return 1
    else:
        data = new_identity()

    changed: list[str] = []
    if args.name is not None:
        data["name"] = args.name
        changed.append("name")
        if not data.get("christened_at"):
            # christened_at marks the first naming, not every rename — a
            # later 'set --name' does not move it. See docs/identity.md.
            data["christened_at"] = _now_iso()
            changed.append("christened_at")
    if args.charter is not None:
        data["charter_path"] = args.charter
        changed.append("charter_path")
    if args.engine is not None:
        data["engine"] = args.engine
        changed.append("engine")
    if args.scope is not None:
        data["scope"] = args.scope
        changed.append("scope")
    if args.harnesses is not None:
        data["harnesses"] = [h.strip() for h in args.harnesses.split(",") if h.strip()]
        changed.append("harnesses")

    if not changed:
        print(
            "identity: 'set' called with nothing to change — pass at least one of "
            "--name/--charter/--engine/--scope/--harnesses",
            file=sys.stderr,
        )
        return 64  # EX_USAGE, matching sill_cli.py's convention

    _write(path, data)
    print(f"identity: updated {', '.join(changed)} at {path}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sill identity",
        description="Read or write this instance's identity card "
                     "(name, charter, engine, scope, harnesses).",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p_show = sub.add_parser("show", help="Print the current identity card")
    p_show.set_defaults(func=cmd_show)

    p_init = sub.add_parser("init", help="Create the identity card if one doesn't exist yet")
    p_init.set_defaults(func=cmd_init)

    p_set = sub.add_parser("set", help="Update one or more identity fields")
    p_set.add_argument("--name", default=None,
                        help="Christen this instance with a name. Setting this for the "
                             "first time also stamps christened_at.")
    p_set.add_argument("--charter", dest="charter", default=None, metavar="PATH",
                        help="Path to this instance's charter document. Not validated for "
                             "existence — the charter may be written before or after this call.")
    p_set.add_argument("--engine", default=None,
                        help="The agent CLI/model this instance runs on, e.g. claude, codex.")
    p_set.add_argument("--scope", default=None,
                        help="Install scope this instance runs at, e.g. project, home "
                             "(see install.sh --scope).")
    p_set.add_argument("--harnesses", default=None, metavar="A,B",
                        help="Comma-separated harnesses wired for this instance, "
                             "e.g. claude,codex.")
    p_set.set_defaults(func=cmd_set)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
