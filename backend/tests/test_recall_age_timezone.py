"""A memory stored seconds ago must not be labelled "-1d ago".

The bug: `spontaneous-recall.py` formatted each recalled memory's age with

    created_date = dt.fromisoformat(created.replace('+00:00','+00:00').split('+')[0])
    days = (dt.now() - created_date).days

`created` arrives from Postgres as UTC with an offset. `.split('+')[0]` threw
the offset away and left a **naive datetime still holding the UTC wall clock**,
which was then subtracted from a naive **local** `dt.now()`. Anywhere west of
UTC the difference is negative for anything recent — 18:25Z minus 11:25 PDT is
-7h, and `timedelta(hours=-7).days` is `-1`, which fell through the `days < 30`
branch and printed `"-1d ago"`.

Observed on a fresh install in PDT: a memory minted ninety seconds earlier was
presented to the model as `-1d ago`. Every user west of UTC saw it on every
recent memory; users east of UTC saw ages silently inflated instead. This is
the header the model reads on *every prompt*, so it is wrong data injected into
context — the specific failure this repo is careful about everywhere else.

(The `.replace('+00:00','+00:00')` in the original was a no-op — a vestige.)

Testing note: `spontaneous-recall.py` has no `if __name__ == "__main__"` guard;
importing it runs the whole hook and calls `sys.exit(0)`. The repo's existing
`test_spontaneous_recall.py` therefore drives it by subprocess, but the age
path needs a populated database, which this suite deliberately does not have.
So these tests lift the two helper functions out of the **real shipped file**
by AST and exercise them directly — no reimplementation, no copy to drift.

Needs no docker, database, or network.
"""

import ast
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "plugin" / "hooks" / "spontaneous-recall.py"
WANTED = {"parse_db_timestamp", "memory_age_days"}


def load_helpers():
    """Exec just the two helpers out of the real hook source."""
    tree = ast.parse(HOOK.read_text(encoding="utf-8"))
    picked = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in WANTED]
    missing = WANTED - {n.name for n in picked}
    assert not missing, f"hook no longer defines {missing}"
    ns = {"re": re}
    exec(compile(ast.Module(body=picked, type_ignores=[]), str(HOOK), "exec"), ns)
    return ns


HELPERS = load_helpers()
parse_db_timestamp = HELPERS["parse_db_timestamp"]
memory_age_days = HELPERS["memory_age_days"]


@pytest.mark.parametrize("raw", [
    "2026-08-06 18:25:21.164779+00",   # what psql actually prints
    "2026-08-06 18:25:21+00",
    "2026-08-06 18:25:21.164779+00:00",
    "2026-08-06T18:25:21.164779Z",
    "2026-08-06 18:25:21.164779",      # offset-less: read as UTC
])
def test_every_shape_the_store_emits_parses_to_an_aware_utc_instant(raw):
    parsed = parse_db_timestamp(raw)
    assert parsed.tzinfo is not None, "naive datetimes reintroduce the bug"
    assert parsed.utcoffset() == timedelta(0)
    assert (parsed.year, parsed.month, parsed.day, parsed.hour) == (2026, 8, 6, 18)


def test_the_two_digit_offset_postgres_emits_is_handled():
    """`datetime.fromisoformat` did not accept a two-digit offset until 3.11,
    and this project's floor is 3.10 — the reason the helper normalizes the
    offset itself instead of trusting the interpreter."""
    assert parse_db_timestamp("2026-08-06 18:25:21+00").utcoffset() == timedelta(0)


def test_a_non_utc_offset_is_respected_not_assumed_away():
    """Session TimeZone is not guaranteed to be UTC; an offset that is present
    must be believed rather than overwritten."""
    parsed = parse_db_timestamp("2026-08-06 20:25:21+02:00")
    assert parsed.utcoffset() == timedelta(hours=2)
    assert parsed.astimezone(timezone.utc).hour == 18


def test_a_memory_stored_moments_ago_is_zero_days_old():
    """The regression itself. Before the fix this returned -1 west of UTC."""
    just_now = datetime.now(timezone.utc) - timedelta(seconds=90)
    assert memory_age_days(just_now) == 0


def test_age_is_never_negative_from_any_offset():
    """The old code's failure was a function of the *observer's* offset, so
    sweep the range rather than trusting the one zone this machine runs in."""
    now = datetime.now(timezone.utc)
    for hours in range(-12, 15):
        stamped = (now - timedelta(minutes=5)).astimezone(timezone(timedelta(hours=hours)))
        assert memory_age_days(stamped) == 0, f"negative/inflated age at UTC{hours:+d}"


def test_a_future_timestamp_reads_as_today_rather_than_negative():
    """Clock skew between the container and the host should not resurrect a
    negative age by a different route."""
    assert memory_age_days(datetime.now(timezone.utc) + timedelta(hours=6)) == 0


@pytest.mark.parametrize("days_ago", [1, 2, 29, 45, 400])
def test_real_ages_still_come_through(days_ago):
    """The fix must not flatten genuine age — clamping is only for <= 0."""
    stamped = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=1)
    assert memory_age_days(stamped) == days_ago


def test_the_old_arithmetic_really_did_go_negative():
    """Characterization: reproduce the replaced expression deterministically,
    so the defect is demonstrated rather than asserted.

    Removing this file's other tests would only prove the helpers exist; this
    one shows *why* they had to. It hardcodes a UTC-7 observer instead of
    reading the machine's zone, so it means the same thing in CI at UTC.
    """
    utc_now = datetime(2026, 8, 6, 18, 25, 21, tzinfo=timezone.utc)
    created_raw = "2026-08-06 18:25:21.164779+00"      # stored this instant

    # --- exactly what the hook used to do ---
    naive_created = datetime.fromisoformat(created_raw.split("+")[0])
    naive_local_now = utc_now.astimezone(timezone(timedelta(hours=-7))).replace(tzinfo=None)
    old_days = (naive_local_now - naive_created).days
    assert old_days == -1, "expected the historical off-by-a-day; got %r" % old_days

    # --- what the replacement does with the same inputs ---
    assert memory_age_days(parse_db_timestamp(created_raw)) >= 0


def test_the_hook_no_longer_splits_the_offset_off():
    """Pin the specific construct that caused it, so it can't come back via a
    refactor that keeps the helper names."""
    src = HOOK.read_text(encoding="utf-8")
    assert "split('+')[0]" not in src, "the offset is being discarded again"
    assert "dt.now() - created_date" not in src, "naive local vs UTC subtraction is back"
