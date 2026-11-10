"""BEST_PRICE_FORWARD gate: pure helpers and the known-at refusals (planted future)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import duckdb
import pytest

from research.eval import best_price_forward_gate as g


def test_price_conversions() -> None:
    assert g.american_to_decimal(150) == pytest.approx(2.5)
    assert g.american_to_decimal(-120) == pytest.approx(1 + 100 / 120)
    assert g.american_to_prob(-110) == pytest.approx(110 / 210)
    assert g.devig_over(0.55, 0.55) == pytest.approx(0.5)


def test_kalshi_cost_and_fee() -> None:
    # over buys YES at the ask; under buys NO at 1 - bid; fee 0.07 P(1-P), unrounded
    assert g.kalshi_fee(0.5) == pytest.approx(0.0175)
    assert g.kalshi_cost("over", 0.40, 0.42) == pytest.approx(0.42 + 0.07 * 0.42 * 0.58)
    assert g.kalshi_cost("under", 0.40, 0.42) == pytest.approx(0.60 + 0.07 * 0.60 * 0.40)
    assert g.kalshi_decimal("over", 0.40, 0.42) == pytest.approx(1 / (0.42 + 0.07 * 0.42 * 0.58))
    assert g.kalshi_mid_over(0.40, 0.42) == pytest.approx(0.41)


def test_kalshi_quotable() -> None:
    assert g.kalshi_quotable(0.01, 0.03)
    assert not g.kalshi_quotable(0.0, 0.03)
    assert not g.kalshi_quotable(0.40, 0.40)
    assert not g.kalshi_quotable(None, 0.5)


def test_maker_price() -> None:
    assert g.maker_price("over", 0.40, 0.43) == pytest.approx(0.41)
    assert g.maker_price("over", 0.40, 0.41) is None  # would cross
    # under: NO bid = 1 - yes_ask = 0.57, improved by 1c to 0.58 < NO ask 0.60
    assert g.maker_price("under", 0.40, 0.43) == pytest.approx(0.58)


def test_reference_point_majority_then_closeness() -> None:
    pairs = {
        "draftkings": {24.5: (0.52, 0.52), 25.5: (0.48, 0.56)},
        "fanduel": {24.5: (0.53, 0.51)},
        "betmgm": {25.5: (0.52, 0.52)},
    }
    assert g.reference_point(pairs) == 24.5
    # one main each at two points: the closer-to-even consensus wins
    pairs2 = {"a": {6.5: (0.52, 0.52)}, "b": {7.5: (0.45, 0.60)}}
    assert g.reference_point(pairs2) == 6.5
    assert g.reference_point({}) is None


def test_stale_filter() -> None:
    assert g.stale_filter({"a": 0.50, "b": 0.52, "c": 0.62}) == {"a": 0.50, "b": 0.52}
    assert g.stale_filter({"a": 0.50, "b": 0.60}) == {}
    assert g.stale_filter({"a": 0.50, "b": 0.55}) == {"a": 0.50, "b": 0.55}


def test_pick_side_threshold_and_one_side() -> None:
    assert g.pick_side(0.56, 0.52) == "over"
    assert g.pick_side(0.48, 0.52) == "under"
    assert g.pick_side(0.54, 0.52) is None
    assert g.pick_side(0.55, 0.52) == "over"  # >= delta


def test_best_venue_tie_prefers_book() -> None:
    assert g.best_venue({"kalshi": 1.95, "fanduel": 1.95}) == ("fanduel", 1.95)
    assert g.best_venue({"kalshi": 1.96, "fanduel": 1.95})[0] == "kalshi"
    assert g.best_venue({"draftkings": 1.9, "betmgm": 1.9})[0] == "betmgm"


def _kalshi_db(e: datetime) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE kalshi_markets (ticker VARCHAR, series_ticker VARCHAR, event_ticker VARCHAR,"
        " player_id INT, threshold FLOAT, result VARCHAR)"
    )
    con.execute(
        "CREATE TABLE kalshi_prices (ticker VARCHAR, ts TIMESTAMP, yes_bid FLOAT, yes_ask FLOAT,"
        " source VARCHAR)"
    )
    con.execute(
        "INSERT INTO kalshi_markets VALUES"
        " ('T1', 'KXNBAPTS', 'KXNBAPTS-26NOV01AAABBB', 7, 25, 'yes')"
    )
    rows = [
        (e - timedelta(minutes=25), 0.10, 0.12),  # too old (> 20 min)
        (e - timedelta(minutes=5), 0.40, 0.42),  # the entry quote
        (e + timedelta(minutes=1), 0.90, 0.92),  # planted future: after E, must be refused
    ]
    for ts_, b, a in rows:
        con.execute("INSERT INTO kalshi_prices VALUES ('T1', ?, ?, ?, 'live')", [ts_, b, a])
    return con


def test_kalshi_entry_refuses_post_entry_and_stale_quotes() -> None:
    e = datetime(2026, 11, 1, 23, 0)
    con = _kalshi_db(e)
    out = g.load_kalshi(con, {(7, date(2026, 11, 1)): ("G1", e)})
    assert set(out) == {("G1", 7, "pts")}
    yb, ya, age, wb, wa, tk = out[("G1", 7, "pts")][24.5]
    assert (round(wb, 2), round(wa, 2), tk) == (0.40, 0.42, "T1")
    assert (round(yb, 2), round(ya, 2)) == (0.40, 0.42)
    assert age == pytest.approx(5.0)


def test_predictions_refuse_rows_made_after_entry() -> None:
    e = datetime(2026, 11, 1, 23, 0)
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT)")
    con.execute("INSERT INTO games VALUES ('G1', DATE '2026-11-01', 2026)")
    con.execute(
        "CREATE TABLE forward_predictions (game_id VARCHAR, model_name VARCHAR, target VARCHAR,"
        " player_id INT, made_at TIMESTAMP, prediction JSON)"
    )
    for made, tag in ((e - timedelta(minutes=40), "early"), (e + timedelta(minutes=5), "future")):
        con.execute(
            "INSERT INTO forward_predictions VALUES ('G1', ?, 'pts', 7, ?, ?)",
            [g.PROD, made, json.dumps({"tag": tag})],
        )
    rows, gdate, n = g.load_predictions(
        con, {"G1": (e, e + timedelta(hours=1))}, date(2026, 10, 20), date(2026, 11, 8)
    )
    assert n == 1 and gdate == {"G1": date(2026, 11, 1)}
    assert len(rows) == 1 and json.loads(rows[0][4])["tag"] == "early"


def test_window_refuses_other_seasons(tmp_path: object) -> None:
    with pytest.raises(ValueError):
        g.run(g.Path("/nonexistent"), date(2025, 10, 1), date(2026, 11, 8), date(2026, 11, 11))


def _book_db(e: datetime) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE live_book_odds (game_id VARCHAR, slate_date DATE, snapshot_kind VARCHAR,"
        " received_at TIMESTAMP, book VARCHAR, market VARCHAR, player_id INT, side VARCHAR,"
        " point DOUBLE, price_american INT, last_update TIMESTAMP)"
    )
    rows = [
        (e, "over", -110),
        (e, "under", -110),
        (e + timedelta(minutes=1), "over", 250),  # planted: labelled t60, received after E
    ]
    for rec, side, a in rows:
        con.execute(
            "INSERT INTO live_book_odds VALUES ('G1', DATE '2026-11-01', 't60', ?, 'draftkings',"
            " 'player_points', 7, ?, 24.5, ?, ?)",
            [rec, side, a, rec],
        )
    return con


def test_book_quotes_refuse_post_entry_t60_row() -> None:
    e = datetime(2026, 11, 1, 23, 0)
    con = _book_db(e)
    grp, refused = g.load_book_quotes(
        con, date(2026, 10, 20), date(2026, 11, 8), {"G1": (e, e + timedelta(hours=1))}
    )
    assert refused == 1
    chosen = g.choose_quotes(grp[("G1", 7, "pts")])
    assert int(chosen["draftkings"][("over", 24.5)][6]) == -110  # price unchanged by the plant


def test_choose_quotes_deterministic_order() -> None:
    t = datetime(2026, 11, 1, 22, 0)
    lu1, lu2 = t - timedelta(minutes=2), t - timedelta(minutes=1)
    a = ("G1", 7, "player_points", "fanduel", "over", 24.5, -105, t, lu1)
    b = ("G1", 7, "player_points", "fanduel", "over", 24.5, -120, t, lu2)  # later last_update
    c = ("G1", 7, "player_points", "fanduel", "over", 24.5, -115, t, lu2)  # tie: worse price wins
    for rows in ([a, b, c], [c, b, a], [b, a, c]):
        got = g.choose_quotes(rows)["fanduel"][("over", 24.5)]
        assert int(got[6]) == -120


def test_sort_picks_and_power_reproduce() -> None:
    def pk(gid: str, d: int, pid: int, side: str) -> g.Pick:
        return g.Pick(
            gid, date(2026, 11, d), "pts", pid, 24.5, side, 0.5, 0.56, 0.5,
            {"draftkings": 1.91, "fanduel": 1.95},
        )  # fmt: skip

    picks = [pk(f"G{i}", 1 + i % 3, 100 + i % 4, "over" if i % 2 else "under") for i in range(12)]
    a = g.synthetic_power(g.sort_picks(picks), games_grid=(6,), n_synth=20)
    b = g.synthetic_power(g.sort_picks(list(reversed(picks))), games_grid=(6,), n_synth=20)
    assert a == b


def test_kalshi_tie_on_latest_ts_uses_worse_price() -> None:
    """S13: two rows share the latest ts; the price is the worse for the bettor on either side."""
    e = datetime(2026, 11, 1, 23, 0)
    con = _kalshi_db(e)
    con.execute(
        "INSERT INTO kalshi_prices VALUES ('T1', ?, 0.38, 0.44, 'live')", [e - timedelta(minutes=5)]
    )
    out = g.load_kalshi(con, {(7, date(2026, 11, 1)): ("G1", e)})
    yb, ya, _age, wb, wa, _tk = out[("G1", 7, "pts")][24.5]
    assert (round(yb, 2), round(ya, 2)) == (0.38, 0.44)  # lowest row for consensus and maker
    assert (round(wb, 2), round(wa, 2)) == (0.38, 0.44)
    con.execute(
        "INSERT INTO kalshi_prices VALUES ('T1', ?, 0.41, 0.45, 'live')", [e - timedelta(minutes=5)]
    )
    out = g.load_kalshi(con, {(7, date(2026, 11, 1)): ("G1", e)})
    _yb, _ya, _age, wb, wa, _tk = out[("G1", 7, "pts")][24.5]
    assert (round(wb, 2), round(wa, 2)) == (0.38, 0.45)  # min bid (under pays more), max ask
