"""Strict as-of market state, implied-ladder math and forward capture (synthetic, no network)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from hypothesis import given
from hypothesis import strategies as st

from nba.markets.asof import (
    asof_state,
    event_tag,
    game_market_state,
    latest_quotes,
    prop_market_state,
)
from nba.markets.capture import backfill_games, capture_forward, ensure_table
from nba.markets.implied import devig_two_way, ladder_median, pav_non_increasing
from nba.parlay.kalshi_map import load_team_abbr

ABBR = load_team_abbr()
AWAY, HOME = ABBR["OKC"], ABBR["SAS"]
D = date(2026, 10, 20)
TAG = event_tag(D, "OKC", "SAS")
T0 = datetime(2026, 10, 20, 20, 0, 0)  # made_at, naive UTC
TIP = datetime(2026, 10, 20, 23, 0, 0)


def _kal() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute(
        "CREATE TABLE kalshi_markets (ticker VARCHAR, series_ticker VARCHAR, event_ticker VARCHAR,"
        " title VARCHAR, player_id INT, stat VARCHAR, threshold FLOAT)"
    )
    c.execute(
        "CREATE TABLE kalshi_prices (ticker VARCHAR, ts TIMESTAMP, yes_bid FLOAT, yes_ask FLOAT,"
        " last FLOAT, volume INT, open_interest INT, source VARCHAR)"
    )
    return c


def _mk(c: duckdb.DuckDBPyConnection, ticker: str, series: str, th: float | None,
        pid: int | None = None, stat: str | None = None) -> None:  # fmt: skip
    ev = "-".join(ticker.split("-")[:2])
    c.execute(
        "INSERT INTO kalshi_markets VALUES (?,?,?,?,?,?,?)",
        [ticker, series, ev, "t", pid, stat, th],
    )


def _px(c: duckdb.DuckDBPyConnection, ticker: str, ts: datetime, bid: float, ask: float,
        source: str = "live") -> None:  # fmt: skip
    c.execute(
        "INSERT INTO kalshi_prices VALUES (?,?,?,?,?,?,?,?)",
        [ticker, ts, bid, ask, bid, 5, 9, source],
    )


def _game_db() -> duckdb.DuckDBPyConnection:
    c = _kal()
    # moneyline: home 0.60 / away 0.42 mids  (vig 0.02) -> de-vigged 0.5882
    for t, b, a in ((f"KXNBAGAME-{TAG}-SAS", 0.59, 0.61), (f"KXNBAGAME-{TAG}-OKC", 0.41, 0.43)):
        _mk(c, t, "KXNBAGAME", None)
        _px(c, t, T0 - timedelta(minutes=10), b, a)
    # spread ladder around home margin ~ +3: P(home margin > t)
    for t, th, mid in (("SAS1", 1.5, 0.56), ("SAS3", 3.5, 0.48), ("SAS5", 5.5, 0.40)):
        tk = f"KXNBASPREAD-{TAG}-{t}"
        _mk(c, tk, "KXNBASPREAD", th)
        _px(c, tk, T0 - timedelta(minutes=5), mid - 0.01, mid + 0.01)
    # total ladder: median between 218.5 (0.55) and 220.5 (0.45) -> 219.5
    for th, mid in ((216.5, 0.65), (218.5, 0.55), (220.5, 0.45), (222.5, 0.35)):
        tk = f"KXNBATOTAL-{TAG}-{int(th + 0.5)}"
        _mk(c, tk, "KXNBATOTAL", th)
        _px(c, tk, T0 - timedelta(minutes=5), mid - 0.01, mid + 0.01)
    return c


def test_quote_is_strictly_before_as_of_and_planted_future_ignored() -> None:
    c = _kal()
    tk = "KXNBAGAME-X-SAS"
    _px(c, tk, T0 - timedelta(seconds=1), 0.50, 0.52)
    _px(c, tk, T0, 0.90, 0.92)  # stamped exactly at as_of: must NOT be used
    _px(c, tk, T0 + timedelta(minutes=1), 0.99, 1.0)  # future
    q = latest_quotes(c, [tk], T0)[tk]
    assert (q.yes_bid, q.yes_ask) == (pytest.approx(0.5), pytest.approx(0.52))
    assert q.age_s == pytest.approx(1.0)
    assert latest_quotes(c, [tk], T0 - timedelta(seconds=1)) == {}
    # aware datetimes are converted to naive UTC
    assert latest_quotes(c, [tk], T0.replace(tzinfo=UTC))[tk].yes_bid == pytest.approx(0.5)


def test_game_state_derivations_and_future_never_used() -> None:
    c = _game_db()
    # plant a wildly different future book on every ticker
    for (tk,) in c.execute("SELECT ticker FROM kalshi_markets").fetchall():
        _px(c, tk, T0 + timedelta(minutes=1), 0.01, 0.03)
    g = game_market_state(c, D, "OKC", "SAS", T0)
    assert g.p_home_win == pytest.approx(0.60 / (0.60 + 0.42))
    assert g.p_home_win_lo == pytest.approx(max(0.59, 1 - 0.43))
    assert g.p_home_win_hi == pytest.approx(min(0.61, 1 - 0.41))
    # margin ladder points (0, .588) (1.5,.56) (3.5,.48) (5.5,.40): median between 3.5 and 5.5? no:
    # G(3.5)=.48 < .5 and G(1.5)=.56 >= .5 -> interpolate 1.5 + (.56-.5)/(.56-.48)*2 = 3.0
    assert g.implied_margin == pytest.approx(3.0)
    assert g.implied_total == pytest.approx(219.5)
    assert g.implied_home_total == pytest.approx((219.5 + 3.0) / 2)
    assert g.implied_away_total == pytest.approx((219.5 - 3.0) / 2)
    assert g.n_spread_rungs == 3 and g.n_total_rungs == 4
    # at the planted time the future book would dominate
    g2 = game_market_state(c, D, "OKC", "SAS", T0 + timedelta(minutes=2))
    assert g2.ml_home is not None and g2.ml_home.mid == pytest.approx(0.02)


def test_away_spread_contract_maps_to_negative_margin() -> None:
    c = _kal()
    for t, th, mid in (("OKC1", 1.5, 0.55), ("OKC3", 3.5, 0.45)):  # away wins by > th
        tk = f"KXNBASPREAD-{TAG}-{t}"
        _mk(c, tk, "KXNBASPREAD", th)
        _px(c, tk, T0 - timedelta(minutes=1), mid, mid)
    g = game_market_state(c, D, "OKC", "SAS", T0)
    # P(M > -3.5) = .55 and P(M > -1.5) = .45: the median sits at -2.5
    assert g.implied_margin == pytest.approx(-2.5)


def test_wide_and_stale_quotes_excluded_from_derivation_but_kept_raw() -> None:
    c = _kal()
    tk = f"KXNBAGAME-{TAG}-SAS"
    _mk(c, tk, "KXNBAGAME", None)
    _px(c, tk, T0 - timedelta(minutes=1), 0.15, 0.79)  # spread 0.64
    g = game_market_state(c, D, "OKC", "SAS", T0)
    assert g.p_home_win is None and g.ml_home is not None and g.ml_home.mid == pytest.approx(0.47)
    c2 = _kal()
    _mk(c2, tk, "KXNBAGAME", None)
    _px(c2, tk, T0 - timedelta(hours=5), 0.59, 0.61)
    assert game_market_state(c2, D, "OKC", "SAS", T0).p_home_win is None


def test_prop_ladder_implied_pge_monotone_and_median() -> None:
    c = _kal()
    pid = 1629029
    # raw mids deliberately non-monotone at 25+
    for n, mid in ((15, 0.90), (20, 0.65), (25, 0.70), (30, 0.30), (35, 0.10)):
        tk = f"KXNBAPTS-{TAG}-P{pid}-{n}"
        _mk(c, tk, "KXNBAPTS", float(n), pid, "pts")
        _px(c, tk, T0 - timedelta(minutes=3), mid - 0.01, mid + 0.01)
        _px(c, tk, T0 + timedelta(minutes=3), 0.5, 0.5)  # future, ignored
    s = prop_market_state(c, D, "OKC", "SAS", pid, "pts", T0)
    assert [r.n for r in s.rungs] == [15, 20, 25, 30, 35]
    mono = [r.p_mono for r in s.rungs]
    assert all(
        a is not None and b is not None and a >= b for a, b in zip(mono, mono[1:], strict=False)
    )
    assert s.implied_pge(20) == pytest.approx(0.675) and s.implied_pge(25) == pytest.approx(0.675)
    assert s.implied_pge(99) is None
    assert s.implied_median is not None and 25.0 < s.implied_median < 30.0
    assert prop_market_state(c, D, "OKC", "SAS", 999, "pts", T0).rungs == []


@given(st.lists(st.floats(0, 1), min_size=1, max_size=30))
def test_pav_is_non_increasing_and_preserves_mean(xs: list[float]) -> None:
    y = pav_non_increasing(xs)
    assert len(y) == len(xs)
    assert all(a >= b - 1e-12 for a, b in zip(y, y[1:], strict=False))
    assert sum(y) == pytest.approx(sum(xs), abs=1e-9)


def test_ladder_median_unbracketed_and_devig() -> None:
    assert ladder_median([(1.0, 0.9), (2.0, 0.8)]) is None  # never crosses 0.5
    assert ladder_median([(1.0, 0.4), (2.0, 0.3)]) is None  # already below at the first rung
    assert ladder_median([(1.0, 0.6), (3.0, 0.4)]) == pytest.approx(2.0)
    assert devig_two_way(0.6, 0.5) == pytest.approx(0.6 / 1.1)
    assert devig_two_way(None, 0.3) == pytest.approx(0.7) and devig_two_way(None, None) is None


# ----- forward capture -----
def _nba(tmp: Path | None = None) -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute(
        "CREATE TABLE forward_predictions (run_id VARCHAR, made_at TIMESTAMP, game_id VARCHAR,"
        " tipoff TIMESTAMP, model_name VARCHAR, version VARCHAR, target VARCHAR, player_id INT,"
        " prediction JSON)"
    )
    c.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT, home_team INT,"
        " away_team INT, home_pts INT, away_pts INT, national_tv VARCHAR)"
    )
    teams = json.dumps({"home_team": HOME, "away_team": AWAY, "p_home": 0.55})
    rows = [
        ("r1", T0, "G1", TIP, "rung0_injury_elo", "v1", "win_prob_home", -1, teams),
        ("r1", T0, "G1", TIP, "props_context_residual", "v1", "pts", 1629029, "{}"),
        ("r1", T0, "G1", TIP, "props_context_residual", "v1", "stl", 5, "{}"),  # not a mapped prop
        ("r0", T0 - timedelta(hours=1), "G9", TIP + timedelta(days=1), "m", "v",
         "win_prob_home", -1, json.dumps({"home_team": 999, "away_team": 998})),
    ]  # fmt: skip
    c.executemany("INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)", rows)
    return c


def _kal_with_prop() -> duckdb.DuckDBPyConnection:
    c = _game_db()
    for n, mid in ((20, 0.65), (25, 0.45)):
        tk = f"KXNBAPTS-{TAG}-P-{n}"
        _mk(c, tk, "KXNBAPTS", n - 0.5 if n == 25 else float(n), 1629029, "pts")
        _px(c, tk, T0 - timedelta(minutes=2), mid - 0.01, mid + 0.01)
        _px(c, tk, T0 + timedelta(minutes=2), 0.99, 1.0)
    return c


def test_capture_forward_idempotent_strict_and_complete() -> None:
    nba, kal, out = _nba(), _kal_with_prop(), duckdb.connect(":memory:")
    s1 = capture_forward(nba, kal, out, D, now=datetime(2026, 10, 21))
    assert (s1.candidates, s1.written, s1.already_captured) == (3, 3, 0)
    assert s1.with_quote == 3 and s1.unresolved == 0
    s2 = capture_forward(nba, kal, out, D, now=datetime(2026, 10, 22))
    assert (s2.candidates, s2.written, s2.already_captured) == (3, 0, 3)
    assert out.execute("SELECT count(*) FROM market_at_prediction").fetchone() == (3,)
    row = out.execute(
        "SELECT p_home_win, implied_total, implied_margin, prop_n_rungs, prop_pge_json, reason"
        " FROM market_at_prediction WHERE target='pts'"
    ).fetchone()
    assert row is not None
    assert row[0] == pytest.approx(0.60 / 1.02) and row[1] == pytest.approx(219.5)
    assert row[3] == 2 and json.loads(row[4]) == {
        "20": pytest.approx(0.65),
        "25": pytest.approx(0.45),
    }
    assert row[5] == "ok"
    # the unmapped stat still gets game state, no prop fields
    r2 = out.execute(
        "SELECT prop_n_rungs, p_home_win FROM market_at_prediction WHERE target='stl'"
    ).fetchone()
    assert r2 is not None and r2[0] is None and r2[1] is not None
    # the other-day game is not captured for this date and is unresolved when asked for all
    s3 = capture_forward(nba, kal, out, None, now=datetime(2026, 10, 22))
    assert s3.written == 1 and s3.unresolved == 1


def test_capture_missing_table_is_clean_noop() -> None:
    out = duckdb.connect(":memory:")
    s = capture_forward(duckdb.connect(":memory:"), _kal(), out, D)
    assert s.written == 0


def test_capture_records_no_quote_reason_when_book_is_only_in_the_future() -> None:
    kal = _kal()
    tk = f"KXNBAGAME-{TAG}-SAS"
    _mk(kal, tk, "KXNBAGAME", None)
    _px(kal, tk, T0 + timedelta(minutes=1), 0.5, 0.52)
    out = duckdb.connect(":memory:")
    capture_forward(_nba(), kal, out, D)
    reasons = {r[0] for r in out.execute("SELECT reason FROM market_at_prediction").fetchall()}
    assert reasons == {"no_quote_before_as_of"}


def test_asof_state_resolves_game_from_forward_rows_and_games_table() -> None:
    nba, kal = _nba(), _kal_with_prop()
    r = asof_state(kal, nba, "G1", T0, 1629029, "pts")
    assert r is not None and r[1] is not None and len(r[1].rungs) == 2
    assert r[0].implied_total == pytest.approx(219.5)
    assert asof_state(kal, nba, "NOPE", T0) is None
    nba.execute("INSERT INTO games VALUES ('G2', ?, 2026, ?, ?, NULL, NULL, NULL)", [D, HOME, AWAY])
    r2 = asof_state(kal, nba, "G2", T0)
    assert r2 is not None and r2[0].p_home_win is not None


def test_backfill_runs_clean_idempotent_and_empty() -> None:
    nba, kal, out = _nba(), _game_db(), duckdb.connect(":memory:")
    ensure_table(out)
    assert backfill_games(nba, kal, out, date(2026, 10, 8), date(2026, 11, 1)).written == 0
    nba.execute("INSERT INTO games VALUES ('G2', ?, 2026, ?, ?, 100, 99, NULL)", [D, HOME, AWAY])
    # proxy tip is 19:00 ET = 23:00Z, after the planted books (20:00Z): quotes visible
    s = backfill_games(nba, kal, out, date(2026, 10, 8), date(2026, 11, 1))
    assert (s.written, s.with_quote) == (1, 1)
    s = backfill_games(nba, kal, out, date(2026, 10, 8), date(2026, 11, 1))
    assert (s.written, s.already_captured) == (0, 1)
    assert out.execute("SELECT capture_kind, model_name FROM market_at_prediction").fetchall() == [
        ("backfill_proxy", "__backfill__")
    ]
