#!/usr/bin/env python3
"""
backfill_scan — `sill backfill plan|run`, the consent-scoped scan that gives
a newly-installed instance its first acquaintance with a person's working
life: the harness transcripts already sitting on disk from Claude Code
and/or Codex sessions.

**Design point worth stating precisely, because it drives every choice
below:** reading someone's transcripts is intimate — their working life,
their half-finished thoughts, other people's names, whatever they typed at
2am. Consent granularity is the feature, not a formality:

  - `plan` prints exactly what *would* be read (harnesses, projects, date
    range, file counts, paths) and writes NOTHING — not a file, not a log
    line, not a database row. It only ever `stat`s candidate files (path,
    mtime, size); it never opens their content. See `render_plan` and
    `cmd_plan` below — neither one calls anything that reads a file's
    bytes.
  - `run` is the one command that actually opens transcript content, and
    it refuses outright without an explicit `--confirm` flag, naming that
    flag in the refusal (see `cmd_run`).
  - The human chooses harnesses, projects, and date range via flags. A
    harness (or project) they did not select is never even `os.listdir`'d
    — `scan()` below only calls a harness's discovery function for
    harnesses actually in the resolved scope; the other harness's root
    directory is never touched at all, not filtered out after the fact.
  - `plan` is meant to always precede `run` as a workflow discipline, but
    that ordering is NOT mechanically enforced here — it cannot be,
    because `plan` leaves no trace to check against. See
    docs/onboarding/02-backfill.md.

**What `run` actually stores, and why this module has no DB dependency:**
`run` copies every file in scope into a timestamped, removable archive
directory under `$SILL_STATE_DIR/backfill/<run_id>/` (mirroring
identity_card.py's state-dir convention — see `default_state_dir` reused
from that module below), alongside a `manifest.json` receipt. That
directory *is* the "episodic backfill" this task builds: a durable,
undoable filesystem copy of exactly what was consented to, with a
harness-normalized per-file receipt (tool-use count, via the reused
`_harness.iter_transcript_tool_uses` — see below). Turning that archive
into queryable episodic memory rows (the `memories` table, embeddings,
etc.) is a separate concern this module deliberately does not implement —
this file has no SQL driver dependency and calls no memory-store query
function at all. The undo path is exactly as simple as that design implies:
`rm -rf` the archive directory the run receipt names. See
docs/onboarding/02-backfill.md for the full explanation and the acceptance
test (`test_module_has_no_database_dependency`) that pins this down.

**Reuse, not re-derivation, of transcript shapes:** this module never
hand-rolls a Claude-vs-Codex JSONL parser. Harness *directory* layout
(where the files live) is genuinely a new concern Plan 4 didn't need to
solve — `plugin/hooks/_harness.py`'s `detect()` classifies hook PAYLOAD
dicts (keyed on a `turn_id` field), which has no bearing on a bare file
path with no payload at all, so it is not called here; what IS reused
directly is `_harness.iter_transcript_tool_uses(path)`, for the one place
this module actually opens file content — building each archived file's
manifest entry during `run` (see `archive()` below). Loaded the same way
`backend/tests/test_harness.py` and `backend/tests/test_notice.py` already
load it (`importlib.util.spec_from_file_location`), because `plugin/hooks`
is not an installed package.

Usage:
    sill backfill plan [--home PATH] [--harnesses claude,codex]
                        [--projects A,B] [--since YYYY-MM-DD] [--until YYYY-MM-DD]
    sill backfill run  ... --confirm
    # or: python3 -m scripts.backfill_scan plan|run ...
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence

from scripts.identity_card import default_state_dir

# ---------------------------------------------------------------------------
# Reuse plugin/hooks/_harness.py rather than re-deriving transcript shapes.
# Not a package (no __init__.py under plugin/hooks), so loaded by path —
# same idiom as backend/tests/test_harness.py and test_notice.py.
# ---------------------------------------------------------------------------

_HOOKS_DIR = Path(__file__).resolve().parents[2] / "plugin" / "hooks"
_harness_spec = importlib.util.spec_from_file_location("_harness", _HOOKS_DIR / "_harness.py")
_harness = importlib.util.module_from_spec(_harness_spec)
_harness_spec.loader.exec_module(_harness)


class BackfillUsageError(Exception):
    """A usage mistake this CLI must report plainly and exit non-zero for
    — never a traceback. Raised by argument resolution (an unknown harness
    name, a malformed --since/--until date) and caught once, at the CLI
    boundary in cmd_plan/cmd_run."""


# ---------------------------------------------------------------------------
# Known harnesses and their on-disk layout.
#
# Claude Code shards session transcripts by project:
#   <home>/.claude/projects/<encoded-cwd>/*.jsonl
# — verified directly (ls of a real ~/.claude/projects), and the same
# encoding docs/beats.md and plugin/hooks/response-patterns.py already
# document elsewhere in this repo.
#
# Codex shards by CALENDAR DATE, not by project:
#   <home>/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-*.jsonl
# — also verified directly. There is no per-project directory to key on,
# so "project" has no on-disk meaning for Codex the way it does for
# Claude. Rather than guess at an unverified in-file cwd/session_meta
# schema (docs/adapters.md flags several Codex record shapes as
# unverified against a real transcript, and this module was written
# without inspecting real transcript CONTENT on purpose — see the module
# docstring), every scanned Codex session is grouped under one honestly-
# labeled bucket, CODEX_PROJECT. Harness- and date-scoping still apply to
# it in full; only per-project scoping does not exist for this harness.
# ---------------------------------------------------------------------------

KNOWN_HARNESSES: tuple[str, ...] = ("claude", "codex")

CODEX_PROJECT = "codex-sessions"
CODEX_PROJECT_NOTE = (
    "Codex does not shard sessions by project directory on disk (see "
    "docs/adapters.md's divergence table) — every scanned Codex session is "
    "grouped under this one bucket. Use --since/--until to narrow it."
)


def harness_root(harness: str, home: Path) -> Path:
    if harness == "claude":
        return home / ".claude" / "projects"
    if harness == "codex":
        return home / ".codex" / "sessions"
    raise BackfillUsageError(
        f"unknown harness {harness!r} — known harnesses: {', '.join(KNOWN_HARNESSES)}"
    )


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScannedFile:
    harness: str
    project: str
    path: Path
    mtime_date: date
    size_bytes: int


@dataclass(frozen=True)
class ScanResult:
    home: Path
    requested_harnesses: tuple[str, ...]
    scanned_harnesses: tuple[str, ...]
    requested_projects: tuple[str, ...] | None
    since: date | None
    until: date | None
    files: tuple[ScannedFile, ...] = field(default_factory=tuple)

    def projects(self) -> dict[tuple[str, str], list[ScannedFile]]:
        """Files grouped by (harness, project), each group sorted by path —
        the grouping render_plan (and the archive step) iterate over."""
        grouped: dict[tuple[str, str], list[ScannedFile]] = {}
        for f in self.files:
            grouped.setdefault((f.harness, f.project), []).append(f)
        for group in grouped.values():
            group.sort(key=lambda f: f.path)
        return grouped


@dataclass(frozen=True)
class ArchiveReceipt:
    archive_dir: Path
    file_count: int
    error_count: int
    manifest_path: Path


# ---------------------------------------------------------------------------
# Discovery — filesystem-stat only. Neither function opens a file's
# content; both are total (a missing/non-directory root yields [], never
# raises) so a fresh install missing one harness is not an error.
# ---------------------------------------------------------------------------

def _file_date(path: Path) -> date:
    """The calendar date (UTC) of a file's mtime — used uniformly for
    date-range filtering across both harnesses' very different on-disk
    layouts. See the module docstring's "why file mtime" note."""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date()


def _discover_claude(root: Path) -> list[ScannedFile]:
    if not root.is_dir():
        return []
    out: list[ScannedFile] = []
    for project_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for f in sorted(project_dir.glob("*.jsonl")):
            if not f.is_file():
                continue
            st = f.stat()
            out.append(ScannedFile("claude", project_dir.name, f,
                                    _file_date(f), st.st_size))
    return out


def _discover_codex(root: Path) -> list[ScannedFile]:
    if not root.is_dir():
        return []
    out: list[ScannedFile] = []
    for f in sorted(root.glob("**/*.jsonl")):
        if not f.is_file():
            continue
        st = f.stat()
        out.append(ScannedFile("codex", CODEX_PROJECT, f, _file_date(f), st.st_size))
    return out


# ---------------------------------------------------------------------------
# Scope resolution — turns CLI-ish inputs (possibly None, meaning "every
# known value") into concrete, validated values. Raises BackfillUsageError
# on anything a human plainly mistyped; never guesses.
# ---------------------------------------------------------------------------

def _resolve_harnesses(requested: Sequence[str] | None) -> tuple[str, ...]:
    if requested is None:
        return KNOWN_HARNESSES
    unknown = [h for h in requested if h not in KNOWN_HARNESSES]
    if unknown:
        raise BackfillUsageError(
            f"unknown harness(es): {', '.join(unknown)} — "
            f"known harnesses: {', '.join(KNOWN_HARNESSES)}"
        )
    seen: list[str] = []
    for h in requested:
        if h not in seen:
            seen.append(h)
    return tuple(seen)


def scan(
    home: Path | str,
    harnesses: Sequence[str] | None = None,
    projects: Sequence[str] | None = None,
    since: date | None = None,
    until: date | None = None,
) -> ScanResult:
    """The one function both `plan` and `run` call to determine scope.
    Stat-only: never opens a candidate file's content. A harness not in
    the resolved scope is never handed to its discovery function at
    all — this loop is the enforcement point for "a harness the human did
    not select is never scanned", not a post-hoc filter."""
    home = Path(home)
    resolved_harnesses = _resolve_harnesses(harnesses)
    project_filter = set(projects) if projects is not None else None

    scanned: list[str] = []
    files: list[ScannedFile] = []
    for h in resolved_harnesses:
        root = harness_root(h, home)
        if not root.is_dir():
            continue
        scanned.append(h)

        if h == "claude":
            found = _discover_claude(root)
        elif h == "codex":
            found = _discover_codex(root)
        else:  # pragma: no cover — unreachable, _resolve_harnesses already validated
            found = []

        for sf in found:
            if project_filter is not None and sf.project not in project_filter:
                continue
            if since is not None and sf.mtime_date < since:
                continue
            if until is not None and sf.mtime_date > until:
                continue
            files.append(sf)

    return ScanResult(
        home=home,
        requested_harnesses=resolved_harnesses,
        scanned_harnesses=tuple(scanned),
        requested_projects=tuple(projects) if projects is not None else None,
        since=since,
        until=until,
        files=tuple(files),
    )


# ---------------------------------------------------------------------------
# plan — pure report, zero side effects.
# ---------------------------------------------------------------------------

def render_plan(result: ScanResult) -> str:
    lines: list[str] = []
    lines.append("sill backfill plan — reads no transcript content and writes nothing.")
    lines.append(f"home:      {result.home}")
    scanned_desc = ", ".join(result.scanned_harnesses) or "(none present on disk)"
    lines.append(f"harnesses: requested={','.join(result.requested_harnesses)}  scanned={scanned_desc}")
    since_s = result.since.isoformat() if result.since else "(unbounded)"
    until_s = result.until.isoformat() if result.until else "(unbounded)"
    lines.append(f"date range: since={since_s} until={until_s}")
    lines.append("")

    by_project = result.projects()
    if not by_project:
        lines.append("Nothing matches this scope — no files would be read by 'run'.")
    else:
        for (harness, project) in sorted(by_project):
            group = by_project[(harness, project)]
            plural = "" if len(group) == 1 else "s"
            lines.append(f"{harness}: project {project}  ({len(group)} file{plural})")
            if harness == "codex" and project == CODEX_PROJECT:
                lines.append(f"    note: {CODEX_PROJECT_NOTE}")
            for sf in group:
                lines.append(f"    - {sf.path}  (modified {sf.mtime_date.isoformat()}, {sf.size_bytes} bytes)")

    lines.append("")
    total = len(result.files)
    plural = "" if total == 1 else "s"
    lines.append(
        f"Total: {total} file{plural} across {len(by_project)} project(s) would be "
        f"read by 'sill backfill run --confirm' with this same scope."
    )
    lines.append("Nothing has been read, written, stored, or logged by this command.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# run — the one command that opens transcript content, and only after
# --confirm. Archives verbatim copies plus a manifest; no DB involved.
# ---------------------------------------------------------------------------

def archive(result: ScanResult, state_dir: Path) -> ArchiveReceipt:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archive_dir = state_dir / "backfill" / run_id
    archive_dir.mkdir(parents=True, exist_ok=False)

    entries: list[dict] = []
    errors: list[dict] = []
    for sf in result.files:
        dest_dir = archive_dir / sf.harness / sf.project
        dest = dest_dir / sf.path.name
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sf.path, dest)
            tool_use_count = sum(1 for _ in _harness.iter_transcript_tool_uses(sf.path))
            entries.append({
                "harness": sf.harness,
                "project": sf.project,
                "source_path": str(sf.path),
                "archived_path": str(dest),
                "mtime_date": sf.mtime_date.isoformat(),
                "size_bytes": sf.size_bytes,
                "tool_use_count": tool_use_count,
            })
        except OSError as exc:
            errors.append({"source_path": str(sf.path), "error": str(exc)})

    manifest = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "home": str(result.home),
        "requested_harnesses": list(result.requested_harnesses),
        "scanned_harnesses": list(result.scanned_harnesses),
        "requested_projects": list(result.requested_projects) if result.requested_projects is not None else None,
        "since": result.since.isoformat() if result.since else None,
        "until": result.until.isoformat() if result.until else None,
        "files": entries,
        "errors": errors,
    }
    manifest_path = archive_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    return ArchiveReceipt(archive_dir=archive_dir, file_count=len(entries),
                           error_count=len(errors), manifest_path=manifest_path)


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------

def _default_home() -> Path:
    """$HOME if set, else the resolved home directory — kept as a small,
    pure, disk-free function so 'overridden HOME' can be tested in
    isolation without ever calling scan()/main() against the real home."""
    override = os.environ.get("HOME")
    return Path(override) if override else Path.home()


def _split_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def _parse_date_arg(value: str | None, flag: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise BackfillUsageError(f"{flag}: invalid date {value!r} — expected YYYY-MM-DD") from exc


def _add_scope_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--home", default=None, metavar="PATH",
                         help="Home directory harness roots are resolved under "
                              "(default: $HOME). Tests point this at a fixture tree; "
                              "never omit it against anything but a real install.")
    parser.add_argument("--harnesses", default=None, metavar="A,B",
                         help="Comma-separated harnesses to scope to, e.g. claude,codex. "
                              "Default: every known harness. A harness not listed here "
                              "is never scanned.")
    parser.add_argument("--projects", default=None, metavar="A,B",
                         help="Comma-separated project names to scope to (get exact names "
                              "from 'plan' first). Default: every project within the "
                              "selected harnesses. Codex has no on-disk project concept — "
                              "see docs/onboarding/02-backfill.md. Claude project names "
                              "always start with '-' (the encoded-cwd convention) — use "
                              "--projects=-Users-... (the '=' form), since a space before a "
                              "value starting with '-' reads as another flag to argparse "
                              "and fails with 'expected one argument'.")
    parser.add_argument("--since", default=None, metavar="YYYY-MM-DD",
                         help="Only include files last modified on or after this date.")
    parser.add_argument("--until", default=None, metavar="YYYY-MM-DD",
                         help="Only include files last modified on or before this date.")


def _scan_from_args(args: argparse.Namespace) -> ScanResult:
    home = Path(args.home) if args.home else _default_home()
    harnesses = _split_csv(args.harnesses)
    projects = _split_csv(args.projects)
    since = _parse_date_arg(args.since, "--since")
    until = _parse_date_arg(args.until, "--until")
    return scan(home, harnesses=harnesses, projects=projects, since=since, until=until)


def cmd_plan(args: argparse.Namespace) -> int:
    try:
        result = _scan_from_args(args)
    except BackfillUsageError as exc:
        print(f"backfill plan: {exc}", file=sys.stderr)
        return 64  # EX_USAGE, matching sill_cli.py's / identity_card.py's convention
    print(render_plan(result))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    if not args.confirm:
        print(
            "backfill run: refusing to read or archive anything without --confirm. "
            "Run 'sill backfill plan' first with the same scope flags to see exactly "
            "what this would read, then re-run with --confirm once you have reviewed it.",
            file=sys.stderr,
        )
        return 64  # EX_USAGE

    try:
        result = _scan_from_args(args)
    except BackfillUsageError as exc:
        print(f"backfill run: {exc}", file=sys.stderr)
        return 64

    receipt = archive(result, state_dir=default_state_dir())
    print(f"backfill run: archived {receipt.file_count} file(s) to {receipt.archive_dir}")
    print(f"backfill run: manifest at {receipt.manifest_path}")
    if receipt.error_count:
        print(f"backfill run: {receipt.error_count} file(s) could not be archived "
              f"— see {receipt.manifest_path}", file=sys.stderr)
    print(f"To undo: rm -rf {receipt.archive_dir}")
    return 0 if receipt.error_count == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sill backfill",
        description="Consent-scoped scan of harness transcripts into a durable, "
                     "undoable archive — 'plan' reports, 'run --confirm' archives.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p_plan = sub.add_parser("plan", help="Print exactly what 'run' would read. Writes nothing.")
    _add_scope_args(p_plan)
    p_plan.set_defaults(func=cmd_plan)

    p_run = sub.add_parser("run", help="Archive the scoped transcripts. Requires --confirm.")
    _add_scope_args(p_run)
    p_run.add_argument("--confirm", action="store_true",
                        help="Required. Without it, run refuses and does nothing.")
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
