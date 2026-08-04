"""Beat worker: config, state, classification, and the output-verification
guard. No subprocess spawning, no DB, no network."""

import json

import pytest

import beat_worker as bw


VOICES_JSON = json.dumps({
    "voices": [
        {
            "name": "analyst",
            "prompt": "prompts/analyst.md",
            "transcripts": "logs/analyst",
            "output_glob": "notes/analyst-*.md",
            "kickoff": "Begin.",
        },
        {
            "name": "reflector",
            "prompt": "prompts/reflector.md",
            "transcripts": "logs/reflector",
            "output_glob": "journal/reflector-*.md",
            "kickoff": "Begin.",
        },
    ],
})


def test_load_voices_reads_config(tmp_path):
    cfg = tmp_path / "beats.json"
    cfg.write_text(VOICES_JSON)
    voices = bw.load_voices(cfg)
    assert [v.name for v in voices] == ["analyst", "reflector"]
    assert voices[0].prompt.endswith("prompts/analyst.md")
    assert voices[1].output_glob == "journal/reflector-*.md"


def test_load_voices_rejects_empty_config(tmp_path):
    cfg = tmp_path / "beats.json"
    cfg.write_text("{}")
    with pytest.raises(ValueError) as e:
        bw.load_voices(cfg)
    assert "no voices" in str(e.value).lower()


def test_state_roundtrip_and_corruption_tolerance(tmp_path):
    p = tmp_path / "state.json"
    bw.write_state(p, 3)
    assert bw.read_state(p) == 3
    p.write_text("{ not json")
    assert bw.read_state(p) == 0          # corrupt state must not crash the loop
    assert bw.read_state(tmp_path / "absent.json") == 0


def test_state_path_is_never_tmp():
    """The upstream lesson: rotation state in /tmp was wiped by a reboot and
    silenced the beats for five days."""
    assert "/tmp" not in str(bw.default_state_path())


@pytest.mark.parametrize("text,expected_substring", [
    ("API Error: unable to connect to API", "connect"),
    ("ECONNRESET while streaming", "reset"),
    ("Not logged in", "logged in"),
    ("please run /login", "logged in"),
])
def test_classify_skip_recognizes_transient_failures(text, expected_substring):
    reason = bw.classify_skip(text, duration=12.0)
    assert reason and expected_substring in reason.lower()


def test_classify_skip_returns_none_for_real_failures():
    assert bw.classify_skip("Traceback: ValueError in tool", duration=42.0) is None


def test_rotation_advances_only_on_success(tmp_path):
    p = tmp_path / "state.json"
    bw.write_state(p, 0)
    bw.advance_if(p, index=0, n_voices=2, success=False)
    assert bw.read_state(p) == 0
    bw.advance_if(p, index=0, n_voices=2, success=True)
    assert bw.read_state(p) == 1
    bw.advance_if(p, index=1, n_voices=2, success=True)
    assert bw.read_state(p) == 0          # wraps


def test_produced_output_detects_a_new_file(tmp_path):
    (tmp_path / "journal").mkdir()
    before = bw.snapshot_outputs(tmp_path, "journal/r-*.md")
    assert bw.produced_output(tmp_path, "journal/r-*.md", before) is False
    (tmp_path / "journal" / "r-001.md").write_text("a beat")
    assert bw.produced_output(tmp_path, "journal/r-*.md", before) is True


def test_produced_output_is_true_when_voice_declares_no_glob(tmp_path):
    """A voice with no output_glob cannot be verified; do not claim failure."""
    assert bw.produced_output(tmp_path, None, set()) is True
