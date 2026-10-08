"""Cutoff-response parsing and live/historical tier selection."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from nba.kalshi.cutoff import parse_cutoff_response, select_tier

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_cutoff_fixture() -> dict:
    return json.loads((FIXTURES_DIR / "cutoff.json").read_text())


def test_parse_cutoff_response_from_fixture() -> None:
    cutoff = parse_cutoff_response(_load_cutoff_fixture())
    assert cutoff.settled_markets_cutoff == datetime(2026, 9, 1)


def test_parse_cutoff_response_accepts_bare_cutoff_key() -> None:
    cutoff = parse_cutoff_response({"cutoff": "2026-01-01T00:00:00"})
    assert cutoff.settled_markets_cutoff == datetime(2026, 1, 1)


def test_parse_cutoff_response_missing_field_raises() -> None:
    with pytest.raises(KeyError):
        parse_cutoff_response({"unrelated": "field"})


def test_select_tier_before_cutoff_is_historical() -> None:
    cutoff = parse_cutoff_response(_load_cutoff_fixture())
    assert select_tier(datetime(2026, 1, 1), cutoff) == "historical"


def test_select_tier_after_cutoff_is_live() -> None:
    cutoff = parse_cutoff_response(_load_cutoff_fixture())
    assert select_tier(datetime(2026, 10, 8), cutoff) == "live"


def test_select_tier_at_cutoff_is_live() -> None:
    """Tie goes to 'live' (documented, conservative choice)."""
    cutoff = parse_cutoff_response(_load_cutoff_fixture())
    assert select_tier(cutoff.settled_markets_cutoff, cutoff) == "live"
