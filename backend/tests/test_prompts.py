"""Starter prompts must be craft, not corpus."""

import re
from pathlib import Path

import pytest

import sill

PROMPTS = Path(__file__).resolve().parents[2] / "prompts"
FILES = ["analyst.md", "reflector.md", "_receipt-gate.md"]

HOUSE = re.compile(r"William|Sili\b|Gnomon|Scryolum|beat[- ]\d+|sili-\d+|/Users/", re.I)


@pytest.mark.parametrize("name", FILES)
def test_prompt_exists_and_is_house_free(name):
    text = (PROMPTS / name).read_text()
    hits = HOUSE.findall(text)
    assert not hits, f"{name} carries house references: {set(hits)}"


def test_receipt_gate_quotes_the_real_placeholder():
    text = (PROMPTS / "_receipt-gate.md").read_text()
    assert sill.RECEIPT_PLACEHOLDER in text


@pytest.mark.parametrize("name", ["analyst.md", "reflector.md"])
def test_both_voices_include_the_shared_receipt_gate(name):
    text = (PROMPTS / name).read_text()
    assert "_receipt-gate.md" in text, "voice must include the shared gate, not restate it"


@pytest.mark.parametrize("name", ["analyst.md", "reflector.md"])
def test_voices_carry_the_store_discipline(name):
    text = (PROMPTS / name).read_text().lower()
    assert "--speaker" in text and "--concepts" in text
