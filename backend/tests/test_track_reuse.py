"""Guard logic is pure; import the hook as a module via importlib."""

import importlib.util
import json
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / "plugin" / "hooks" / "track-reuse.py"
spec = importlib.util.spec_from_file_location("track_reuse", HOOK)
tr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tr)


def _mem(mid, content):
    return {"id": mid, "content": content}


def test_phrase_sampling_skips_the_head():
    head = "Alpha beta gamma delta epsilon zeta"          # 6 head words
    body = "the distinctive body phrase continues here uniquely"
    m = _mem("m1", head + " " + body)
    resp = "Unrelated text ... the distinctive body phrase continues ... more"
    ev = tr.find_reuse_phrase(m, resp.lower())
    assert ev and ev != "__ID__"
    resp_head_only = "quoting: Alpha beta gamma delta epsilon zeta"
    assert tr.find_reuse_phrase(m, resp_head_only.lower()) is None


def test_shared_phrase_rejected_as_title_not_evidence():
    shared = "the certification queue rule holds"
    m1 = _mem("m1", "x " * 6 + shared + " tail one")
    m2 = _mem("m2", "y " * 6 + shared + " tail two")
    reused = tr.detect_reuse([m1, m2], ("prefix " + shared + " suffix").lower())
    assert reused == []


def test_burst_limit_zeroes_citation_sweeps():
    mems, resp = [], ""
    for i in range(5):
        body = f"unique body phrase number {i} continues distinctly onward here"
        mems.append(_mem(f"m{i}", "h " * 6 + body))
        resp += " ... " + body
    assert tr.detect_reuse(mems, resp.lower()) == []


def test_sidecar_reader_time_gates_recent_only(tmp_path, monkeypatch):
    monkeypatch.setenv("SILL_LOG_DIR", str(tmp_path))
    (tmp_path / "recall-sidecar-recent.jsonl").write_text(json.dumps(
        {"ts": "2000-01-01T00:00:00", "source": "spontaneous-recall",
         "memories": [{"id": "00000000-0000-0000-0000-000000000000",
                       "content": "stale entry"}]}) + "\n")
    assert tr.read_recall_sidecars(None) == []
