"""Raw Kalshi JSON -> kalshi_markets / kalshi_prices row parsing."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from nba.kalshi.aliases import UnmatchedKalshiNameError
from nba.kalshi.parse import (
    SkippedMarket,
    parse_candlesticks_frame,
    parse_market,
    parse_markets_frame,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_markets_fixture() -> list[dict]:
    raw = json.loads((FIXTURES_DIR / "markets_page.json").read_text())
    markets = raw["markets"]
    assert isinstance(markets, list)
    return markets


def _load_candles_fixture() -> list[dict]:
    raw = json.loads((FIXTURES_DIR / "candlesticks.json").read_text())
    candles = raw["candlesticks"]
    assert isinstance(candles, list)
    return candles


def test_parse_market_extracts_threshold_and_player_id() -> None:
    markets = _load_markets_fixture()
    row = parse_market(markets[0])  # LeBron James Points 25+
    assert row["player_id"] == 2544
    assert row["stat"] == "pts"
    assert row["threshold"] == 25.0
    assert row["ticker"] == markets[0]["ticker"]


def test_parse_market_skips_non_prop_market() -> None:
    markets = _load_markets_fixture()
    game_market = next(m for m in markets if m["title"] == "Lakers to win")
    with pytest.raises(SkippedMarket):
        parse_market(game_market)


def test_parse_market_unmatched_name_raises_loudly() -> None:
    markets = _load_markets_fixture()
    bad = next(m for m in markets if "Not A Real Player" in m["title"])
    with pytest.raises(UnmatchedKalshiNameError):
        parse_market(bad)


def test_parse_markets_frame_includes_only_resolvable_props() -> None:
    """Game markets are silently skipped; the unmatched-name market is NOT
    present in this fixture-driven frame call because the caller is
    expected to catch/handle it upstream -- here we only exercise the
    two clean prop rows plus the one game row (skipped)."""
    clean_markets = [m for m in _load_markets_fixture() if "Not A Real Player" not in m["title"]]
    df = parse_markets_frame(clean_markets)
    assert len(df) == 2
    assert set(df["stat"].to_list()) == {"pts", "fg3m"}
    assert set(df["player_id"].to_list()) == {2544, 201939}


def test_parse_markets_frame_propagates_unmatched_name() -> None:
    with pytest.raises(UnmatchedKalshiNameError):
        parse_markets_frame(_load_markets_fixture())


def test_parse_candlesticks_frame_handles_flat_and_nested_and_null() -> None:
    df = parse_candlesticks_frame(
        _load_candles_fixture(), ticker="KXNBAPTS-26OCT08LALBOS-LEBJAM-25", source="live"
    )
    assert len(df) == 3
    rows = df.sort("ts").to_dicts()
    assert rows[0]["yes_bid"] == pytest.approx(0.52)
    assert rows[1]["yes_bid"] == pytest.approx(0.58)  # nested OHLC 'close'
    assert rows[2]["yes_bid"] is None  # legitimately missing quote, not coerced to 0
    assert all(r["source"] == "live" for r in rows)
    assert isinstance(rows[0]["ts"], datetime)


def test_parse_candlesticks_frame_rejects_bad_source() -> None:
    with pytest.raises(ValueError):
        parse_candlesticks_frame(_load_candles_fixture(), ticker="X", source="paper")


def test_empty_markets_frame_has_expected_schema() -> None:
    df = parse_markets_frame([])
    assert df.is_empty()
    assert set(df.columns) == {
        "ticker",
        "series_ticker",
        "event_ticker",
        "title",
        "player_id",
        "stat",
        "threshold",
        "open_time",
        "close_time",
        "settled_ts",
        "result",
    }
