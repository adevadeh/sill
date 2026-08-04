"""The worker's mode surface. No DB, no docker — argparse only."""

import re
from pathlib import Path

import pytest

WORKER = Path(__file__).resolve().parents[1] / "worker.py"
COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def test_heartbeat_mode_is_gone_from_choices():
    src = WORKER.read_text()
    m = re.search(r'"--mode".*?choices=\[(.*?)\]', src, re.S)
    assert m, "could not find the --mode choices list"
    choices = m.group(1)
    assert "heartbeat" not in choices, "the dreaming heartbeat mode still ships"
    assert '"both"' not in choices, "the 'both' mode implies heartbeat"
    assert "maintenance" in choices


def test_compose_has_no_heartbeat_service():
    text = COMPOSE.read_text()
    assert "heartbeat_worker" not in text
    assert "profiles:" not in text or "heartbeat" not in text


@pytest.mark.parametrize("doc", ["README.md", "docs/concepts.md"])
def test_docs_do_not_advertise_heartbeat(doc):
    p = Path(__file__).resolve().parents[2] / doc
    text = p.read_text().lower()
    assert "heartbeat_worker" not in text
    assert "heartbeat profile" not in text
