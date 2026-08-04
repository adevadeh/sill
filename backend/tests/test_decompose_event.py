"""build_sql is pure; assert the bundle invariant and validation."""

import pytest

from scripts import decompose_event as de


SPEC = {"event": "test event", "speaker": "Ada", "acts": [
    {"force": "assertive", "content": "fact one"},
    {"force": "directive", "content": "do the thing", "importance": 0.7},
]}


def test_bundle_wrapped_in_one_transaction():
    sql = de.build_sql(SPEC)
    assert sql.startswith("BEGIN;") and sql.rstrip().endswith("COMMIT;")
    assert sql.count("create_memory") == 2


def test_acts_inherit_spec_speaker_and_patch_force():
    sql = de.build_sql(SPEC)
    assert sql.count("speaker = $sa$Ada$sa$") == 2
    assert "force = 'assertive'" in sql and "force = 'directive'" in sql


def test_dollar_quote_guard_rejects_collision():
    bad = {"event": "e", "speaker": "A",
           "acts": [{"force": "assertive", "content": "evil $sa$ payload"}]}
    with pytest.raises(ValueError):
        de.build_sql(bad)


def test_bad_force_rejected():
    bad = {"event": "e", "speaker": "A",
           "acts": [{"force": "rhetorical", "content": "x"}]}
    with pytest.raises(ValueError):
        de.build_sql(bad)
