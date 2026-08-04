"""Beat worker: config, state, classification, and the output-verification
guard. No real subprocess spawning (subprocess.run is monkeypatched where a
test needs to look at what spawn_beat() would hand the child), no DB, no
network."""

import json
import subprocess

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


@pytest.mark.parametrize("mode", ["malformed-bytes", "missing-file"])
def test_spawn_beat_survives_unreadable_prompt(tmp_path, monkeypatch, mode):
    """spawn_beat() must return rather than raise for both failure classes a
    defensive prompt read has to cover: a missing file (OSError) and a file
    that exists but holds bytes invalid for the codec (UnicodeDecodeError —
    a ValueError, not an OSError subclass, so a bare `except OSError` lets
    it straight through uncaught). Calling spawn_beat directly here still
    respects this file's "no subprocess spawning" design: a prompt-read
    failure returns before the function ever reaches subprocess.run()."""
    monkeypatch.setattr(bw, "PROJECT_ROOT", tmp_path)
    prompt_path = tmp_path / "prompts" / "voice.md"
    prompt_path.parent.mkdir(parents=True)
    if mode == "malformed-bytes":
        prompt_path.write_bytes(b"\xff\xfe not utf8")
    # else "missing-file": leave prompt_path un-created

    voice = bw.Voice(
        name="analyst", prompt="prompts/voice.md",
        transcripts="logs/analyst", output_glob=None, kickoff="Begin.",
    )
    success, duration, transcript_path = bw.spawn_beat(voice, [voice])
    assert success is False
    assert duration == 0.0
    assert transcript_path == ""

    state_path = tmp_path / "state.json"
    bw.write_state(state_path, 0)
    bw.advance_if(state_path, index=0, n_voices=2, success=success)
    assert bw.read_state(state_path) == 0          # rotation must not advance


# ---------------------------------------------------------------------------
# Guard scope derivation (Step 3b — wiring SILL_BEAT_JOURNAL_DIRS so
# stored-slot-guard.py / tool-type-witness.py / state-language-check.py's
# beat-aware fallback stop being opt-in-with-nothing-opting-in).
# ---------------------------------------------------------------------------

TWO_VOICES = [
    bw.Voice(name="analyst", prompt="prompts/analyst.md",
              transcripts="logs/analyst", output_glob="notes/analyst-*.md",
              kickoff="Begin."),
    bw.Voice(name="reflector", prompt="prompts/reflector.md",
              transcripts="logs/reflector", output_glob="journal/reflector-*.md",
              kickoff="Begin."),
]


def test_journal_dirs_for_voices_derives_from_output_glob_and_transcripts():
    """Matches backend/beats.example.json's two voices: each voice
    contributes its output_glob's directory, then its transcripts dir."""
    assert bw.journal_dirs_for_voices(TWO_VOICES) == (
        "notes/:logs/analyst/:journal/:logs/reflector/"
    )


def test_journal_dirs_for_voices_dedupes_shared_fragments():
    voices = [
        bw.Voice(name="a", prompt="p", transcripts="shared/",
                  output_glob="shared/a-*.md", kickoff="Begin."),
        bw.Voice(name="b", prompt="p", transcripts="shared/",
                  output_glob="shared/b-*.md", kickoff="Begin."),
    ]
    assert bw.journal_dirs_for_voices(voices) == "shared/"


def test_journal_dirs_for_voices_skips_a_voice_with_no_output_glob():
    voices = [bw.Voice(name="a", prompt="p", transcripts="logs/a",
                        output_glob=None, kickoff="Begin.")]
    assert bw.journal_dirs_for_voices(voices) == "logs/a/"


def test_journal_dirs_for_voices_empty_for_no_voices():
    """An install with no configured voices exports nothing — the guards
    stay opt-in-and-off, exactly as they were before this wiring."""
    assert bw.journal_dirs_for_voices([]) == ""


def test_spawn_beat_exports_journal_dirs_derived_from_full_voice_config(tmp_path, monkeypatch):
    """The wiring this step exists for: spawn_beat() must export
    SILL_BEAT_JOURNAL_DIRS to the child, derived from every voice in the
    config — not just the one voice running this particular beat — so the
    two opt-in guards and state-language-check.py's beat-aware fallback are
    scoped with zero operator configuration. subprocess.run is monkeypatched
    (never a real CLI spawn); it also completes the run so this exercises
    the full success path, not just an early return."""
    monkeypatch.setattr(bw, "PROJECT_ROOT", tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "analyst.md").write_text("Standing prompt.\n")
    (tmp_path / "notes").mkdir()

    voices = [
        bw.Voice(name="analyst", prompt="prompts/analyst.md",
                  transcripts="logs/analyst", output_glob="notes/analyst-*.md",
                  kickoff="Begin."),
        bw.Voice(name="reflector", prompt="prompts/reflector.md",
                  transcripts="logs/reflector", output_glob="journal/reflector-*.md",
                  kickoff="Begin."),
    ]

    captured_env = {}

    def fake_run(cmd, **kwargs):
        captured_env.update(kwargs.get("env") or {})
        (tmp_path / "notes" / "analyst-001.md").write_text("beat output")
        return subprocess.CompletedProcess(cmd, 0, stdout="did something", stderr="")

    monkeypatch.setattr(bw.subprocess, "run", fake_run)

    success, duration, transcript_path = bw.spawn_beat(voices[0], voices)

    assert success is True, "fixture should exercise the verified-success path"
    assert captured_env.get("SILL_BEAT_JOURNAL_DIRS") == (
        "notes/:logs/analyst/:journal/:logs/reflector/"
    )
    # The other export this same env dict carries must still be present —
    # this test should not be the reason a regression there goes unnoticed.
    assert captured_env.get("SILL_DETACHED_BEAT") == "1"


def test_spawn_beat_sets_no_journal_dirs_var_when_derivation_is_empty(tmp_path, monkeypatch):
    """A voice with neither a usable output_glob directory nor a transcripts
    value (degenerate, but not something spawn_beat should crash over) must
    not export an empty SILL_BEAT_JOURNAL_DIRS — an empty string would still
    be "set" from os.environ.get's point of view and change the guards'
    unset-means-no-scope contract."""
    monkeypatch.setattr(bw, "PROJECT_ROOT", tmp_path)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "voice.md").write_text("Standing prompt.\n")

    voice = bw.Voice(name="bare", prompt="prompts/voice.md",
                      transcripts="", output_glob=None, kickoff="Begin.")

    captured_env = {}

    def fake_run(cmd, **kwargs):
        captured_env.update(kwargs.get("env") or {})
        return subprocess.CompletedProcess(cmd, 0, stdout="did something", stderr="")

    monkeypatch.setattr(bw.subprocess, "run", fake_run)

    success, _duration, _transcript = bw.spawn_beat(voice, [voice])

    assert success is True  # no output_glob declared -> produced_output() defaults True
    assert "SILL_BEAT_JOURNAL_DIRS" not in captured_env
