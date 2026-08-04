"""notice() SQL shape + parser contract, DB faked via _query_db patching."""

from unittest import mock

import pytest

import sill


def _fake_query(sql, timeout=60):
    if "create_memory" in sql:
        return ["11111111-2222-3333-4444-555555555555"]
    return ["ok"]


def test_notice_emits_force_speaker_patch():
    with mock.patch.object(sill, "_query_db", side_effect=_fake_query) as q:
        sill.notice("a fact", "semantic", speaker="Ada", force="directive")
    sqls = [c.args[0] for c in q.call_args_list]
    patch = next(s for s in sqls if "UPDATE memories SET" in s)
    assert "force = 'directive'" in patch and "speaker = 'Ada'" in patch


def test_notice_escapes_apostrophes_in_speaker():
    with mock.patch.object(sill, "_query_db", side_effect=_fake_query) as q:
        sill.notice("a fact", "semantic", speaker="O'Brien")
    patch = next(s for s in (c.args[0] for c in q.call_args_list)
                 if "UPDATE memories SET" in s)
    assert "O''Brien" in patch


def test_notice_rejects_bad_force_before_db():
    with mock.patch.object(sill, "_query_db", side_effect=AssertionError("no DB")):
        with pytest.raises(ValueError):
            sill.notice("x", "semantic", speaker="Ada", force="rhetorical")


def test_parser_concepts_append_and_comma_split():
    p = sill.build_parser()
    a = p.parse_args(["notice", "c", "--speaker", "Ada",
                      "--concepts", "x,y", "--concepts", "z"])
    assert sill.flatten_concepts(a.concepts) == ["x", "y", "z"]


def test_parser_requires_speaker():
    p = sill.build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["notice", "c"])


def test_hook_call_shape_parses():
    """The auto-store hook's constructed argv must parse — this contract broke
    silently in v0.1.0 (the CLI had no notice subcommand at all)."""
    p = sill.build_parser()
    argv = ["notice", "content here", "--importance", "0.6",
            "--force", "assertive", "--speaker", "instance",
            "--concepts", "a,b"]
    a = p.parse_args(argv)
    assert a.speaker == "instance" and a.importance == 0.6
