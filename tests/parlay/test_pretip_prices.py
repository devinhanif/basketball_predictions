"""M7: the parlay shadow may only price/log markets whose game has not tipped, on pre-tip prices."""

from __future__ import annotations

from datetime import datetime

import duckdb
import pytest

from nba.parlay.__main__ import load_open_markets, pretip_only
from nba.parlay.kalshi_map import MarketRow
from nba.parlay.legs import Leg
from nba.parlay.slate import SlateInfo

TIP = datetime(2026, 10, 28, 23, 0)


def _market(ticker: str, price_ts: str | None) -> MarketRow:
    return MarketRow(ticker, "S", "E", "t", 1, "pts", 10.0, 0.4, 0.45, price_ts)


def test_pretip_only_drops_tipped_and_late_price_rows() -> None:
    info = SlateInfo(tipoff={"G1": TIP})
    leg = Leg("G1", 1, "pts", 10.0)
    mapped = {
        "ok": (_market("ok", "2026-10-28 21:00:00"), leg),
        "late_price": (_market("late_price", "2026-10-28 23:30:00"), leg),
        "no_tip": (_market("no_tip", "2026-10-28 21:00:00"), Leg("G9", 1, "pts", 10.0)),
    }
    skipped: dict[str, int] = {}
    kept = pretip_only(mapped, info, datetime(2026, 10, 28, 22, 0), skipped)
    assert list(kept) == ["ok"] and skipped == {"price_not_pretip": 1, "tip_unknown": 1}
    # the same markets after tip-off: nothing can be logged
    skipped2: dict[str, int] = {}
    assert not pretip_only(mapped, info, datetime(2026, 10, 28, 23, 31), skipped2)
    assert skipped2["game_tipped"] == 2


def test_load_open_markets_bounds_price_ts() -> None:
    con = duckdb.connect(":memory:")
    con.execute("ATTACH ':memory:' AS kal")
    con.execute(
        "CREATE TABLE kal.kalshi_markets (ticker VARCHAR, series_ticker VARCHAR, "
        "event_ticker VARCHAR, title VARCHAR, player_id INT, stat VARCHAR, threshold FLOAT, "
        "result VARCHAR)"
    )
    con.execute(
        "CREATE TABLE kal.kalshi_prices (ticker VARCHAR, ts TIMESTAMP, yes_bid FLOAT, "
        "yes_ask FLOAT)"
    )
    con.execute("INSERT INTO kal.kalshi_markets VALUES ('T','S','E','t',1,'pts',10,NULL)")
    con.execute(
        "INSERT INTO kal.kalshi_prices VALUES ('T','2026-10-28 20:00:00',0.40,0.45),"
        "('T','2026-10-28 23:40:00',0.95,0.99)"
    )
    unbounded = load_open_markets(con)
    assert unbounded[0].yes_ask == pytest.approx(0.99)
    bounded = load_open_markets(con, before=datetime(2026, 10, 28, 23, 0))
    assert (
        bounded[0].yes_ask == pytest.approx(0.45) and bounded[0].price_ts == "2026-10-28 20:00:00"
    )
