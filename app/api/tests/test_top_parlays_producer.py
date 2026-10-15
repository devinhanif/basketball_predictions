"""The producer's own output must pass the app's contract validator (B1 of the producer review).

Covers the hindsight fixture and a committed small producer run (written by build_report in
tests/parlay/test_top_producer_sample.py). Run in the app's uv environment:
`cd app/api && uv run pytest tests/test_top_parlays_producer.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parlayhitters_api.top_parlays import TopParlays

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
FILES = ["top_parlays_example.json", "top_parlays_producer_sample.json"]


@pytest.mark.parametrize("name", FILES)
def test_producer_output_validates(name: str) -> None:
    raw = json.loads((FIXTURES / name).read_text())
    doc = TopParlays.model_validate(raw)
    assert doc.schema_ == "top_parlays/2" and doc.verdict == "keep_your_money"
    assert not doc.gate.open


def test_hindsight_fixture_is_an_illustration_and_sample_is_not() -> None:
    ex = json.loads((FIXTURES / FILES[0]).read_text())
    sample = json.loads((FIXTURES / FILES[1]).read_text())
    assert ex["illustration"] is True and sample["illustration"] is False
    # a price stamped after the file would be refused
    ex["sizes"]["2"][0]["legs"][0]["price_at"] = "2025-01-27T23:59:30Z"
    with pytest.raises(ValueError, match="after the file was made"):
        TopParlays.model_validate(ex)
