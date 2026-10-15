# ruff: noqa: E501
"""Live inputs: primary-source parity with the export, pre-tip quotes, drops, holdout refusal."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

import duckdb
import numpy as np
import pytest

from nba.export import best_model
from nba.parlay import line_cal as lc
from nba.parlay import top_source as ts
from nba.props import full_support

SLATE = date(2026, 10, 28)
TIP = datetime(2026, 10, 28, 23, 0)
MADE = TIP - timedelta(minutes=70)
PRICED = TIP - timedelta(minutes=75)
GID = "0022600001"
HOME, AWAY, PID = 1610612738, 1610612752, 201939


def q_grid(mu: float = 22.0) -> list[float]:
    return [round(mu + 4.0 * z, 3) for z in np.linspace(-1.64, 1.64, 19)]


def pred(mu: float = 22.0) -> dict[str, Any]:
    s = np.array(q_grid(mu))
    s199 = np.interp(np.linspace(0, 18, 199), np.arange(19), s)
    return {
        "mean": mu,
        "q_grid": q_grid(mu),
        "p_ge_full": full_support.from_samples(s199, "pts"),
        "p_play": 0.97,
        "proj_minutes": 34.5,
        "mean_recency": 21.4,
        "starter_rate": 0.9,
    }


def build_nba(*, with_avg: bool = False, avg_fresh: bool = True) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT, home_team INT, away_team INT)"
    )
    con.execute("INSERT INTO games VALUES (?, ?, 2026, ?, ?)", [GID, SLATE, HOME, AWAY])
    con.execute("CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT, team_id INT)")
    con.execute(
        "CREATE TABLE player_availability (player_id INT, as_of TIMESTAMP, game_id VARCHAR, status VARCHAR)"
    )
    con.execute(
        "INSERT INTO games VALUES ('0022500999', ?, 2025, ?, ?)", [date(2026, 4, 1), HOME, AWAY]
    )
    con.execute("INSERT INTO player_game_stats VALUES ('0022500999', ?, ?)", [PID, HOME])
    con.execute("INSERT INTO player_game_stats VALUES ('0022500999', 2544, ?)", [HOME])
    con.execute(
        "INSERT INTO player_availability VALUES (2544, ?, ?, 'out')",
        [MADE - timedelta(hours=2), GID],
    )
    con.execute(
        "CREATE TABLE forward_predictions (run_id VARCHAR, made_at TIMESTAMP, game_id VARCHAR, tipoff TIMESTAMP, "
        "model_name VARCHAR, version VARCHAR, target VARCHAR, player_id INT, prediction JSON)"
    )
    con.execute(
        "INSERT INTO forward_predictions VALUES ('r1', ?, ?, ?, ?, 'v', 'pts', ?, ?)",
        [MADE, GID, TIP, ts.PROPS_MODEL, PID, json.dumps(pred())],
    )
    if with_avg:
        con.execute(
            "CREATE TABLE forward_avg_shadow (made_at TIMESTAMP, run_id VARCHAR, game_id VARCHAR, player_id INT, "
            "stat VARCHAR, tipoff_utc TIMESTAMP, fit_through DATE, month_block VARCHAR, installed_at TIMESTAMP, "
            "stale_model BOOLEAN, avg_pmf199 JSON, avg_q19 JSON, a0_q19 JSON, a0_source VARCHAR)"
        )
        pmf = list(np.interp(np.linspace(0, 18, 199), np.arange(19), np.array(q_grid(25.0))))
        con.execute(
            "INSERT INTO forward_avg_shadow VALUES (?, 'r1', ?, ?, 'pts', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                MADE, GID, PID, TIP, date(2026, 9, 30) if avg_fresh else date(2026, 10, 15),
                "2026-10", datetime(2026, 10, 1), False, json.dumps(pmf),
                json.dumps(q_grid(25.0)), json.dumps(q_grid()), ts.PROPS_MODEL,
            ],
        )  # fmt: skip
    return con


def build_odds(rows: list[tuple[Any, ...]]) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE live_book_odds (game_id VARCHAR, player_id INT, market VARCHAR, book VARCHAR, side VARCHAR, "
        "point DOUBLE, price_american INT, received_at TIMESTAMP, player_name VARCHAR, tipoff TIMESTAMP, "
        "commence_time TIMESTAMP, snapshot_kind VARCHAR)"
    )
    for r in rows:
        con.execute(
            "INSERT INTO live_book_odds VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'x')", list(r)
        )
    return con


def quote(
    book: str, side: str, price: int, at: datetime = PRICED, point: float = 22.5
) -> tuple[Any, ...]:
    return (GID, PID, "player_points", book, side, point, price, at, "Jayson Tatum", TIP, TIP)


CALMAP = lc.load_map()


def test_production_row_priced_pre_tip_becomes_over_and_under_legs() -> None:
    odds = build_odds([quote("draftkings", "over", -115), quote("draftkings", "under", -105)])
    out = ts.load_legs(build_nba(), odds, SLATE, now=MADE + timedelta(minutes=5), calmap=CALMAP)
    assert not out.no_slate and len(out.legs) == 2 and out.sources == {"production_only": 1}
    over = next(x for x in out.legs if x.side == "over")
    under = next(x for x in out.legs if x.side == "under")
    assert over.p_raw + under.p_raw == pytest.approx(
        1.0
    ) and over.p_cal + under.p_cal == pytest.approx(1.0)
    assert over.model_source == "production" and over.price_at < over.tipoff
    assert over.team != over.opp and over.drivers["minutes"] == 34.5
    assert over.drivers["p_play"] == pytest.approx(0.97)
    assert 0.0 < over.p_cal < 1.0 and over.p_cal != over.p_raw  # the map moved it


def test_quote_after_tip_or_after_the_forecast_is_not_used() -> None:
    odds = build_odds(
        [
            quote("draftkings", "over", -115, at=TIP),  # at tip: refused in SQL
            quote(
                "draftkings", "under", -105, at=MADE + timedelta(minutes=1)
            ),  # after the forecast
        ]
    )
    out = ts.load_legs(build_nba(), odds, SLATE, now=None, calmap=CALMAP)
    assert out.legs == []


def test_book_without_a_cal_map_and_whole_number_lines_are_dropped_and_counted() -> None:
    odds = build_odds(
        [quote("fanduel", "over", -110), quote("draftkings", "over", -110, point=22.0)]
    )
    out = ts.load_legs(build_nba(), odds, SLATE, now=None, calmap=CALMAP)
    assert out.legs == []
    assert (
        out.dropped["no_cal_map_for_book"] == 1 and out.dropped["whole_number_line_or_no_pmf"] == 1
    )


def test_low_p_play_is_dropped_not_guessed() -> None:
    nba = build_nba()
    nba.execute("DELETE FROM forward_predictions")
    p = pred()
    p["p_play"] = 0.6
    nba.execute(
        "INSERT INTO forward_predictions VALUES ('r1', ?, ?, ?, ?, 'v', 'pts', ?, ?)",
        [MADE, GID, TIP, ts.PROPS_MODEL, PID, json.dumps(p)],
    )
    out = ts.load_legs(
        nba, build_odds([quote("draftkings", "over", -110)]), SLATE, now=None, calmap=CALMAP
    )
    assert out.legs == [] and out.dropped["p_play_below_floor_or_missing"] == 1


def test_teammate_out_is_a_driver() -> None:
    out = ts.load_legs(
        build_nba(), build_odds([quote("draftkings", "over", -110)]), SLATE, now=None, calmap=CALMAP
    )
    assert out.legs[0].drivers["teammates_out_names"]  # 2544 is out and on the same team


def test_fresh_avg_row_is_primary_and_stale_avg_falls_back() -> None:
    odds = build_odds([quote("draftkings", "over", -110)])
    fresh = ts.load_legs(build_nba(with_avg=True), odds, SLATE, now=None, calmap=CALMAP)
    # p_raw / p_cal come from the PRODUCTION row (mean 22); the AVG row (mean 25) is display only
    leg = fresh.legs[0]
    assert fresh.sources == {"avg_shown": 1} and leg.model_source == "production"
    assert leg.drivers["mean"] == pytest.approx(22.0, abs=0.2)
    assert leg.p_avg_raw is not None and leg.p_avg_raw > leg.p_raw  # AVG has the higher mean
    base = ts.load_legs(build_nba(), odds, SLATE, now=None, calmap=CALMAP).legs[0]
    assert (leg.p_raw, leg.p_cal) == (base.p_raw, base.p_cal)  # AVG never moves ranked numbers
    stale = ts.load_legs(
        build_nba(with_avg=True, avg_fresh=False), odds, SLATE, now=None, calmap=CALMAP
    )
    assert stale.sources == {"production_only": 1} and stale.legs[0].p_avg_raw is None


def test_empty_slate_and_holdout_season() -> None:
    nba = build_nba()
    out = ts.load_legs(nba, None, date(2026, 10, 30), now=None, calmap=CALMAP)
    assert out.no_slate and out.legs == []
    with pytest.raises(ts.SourceError, match="holdout"):
        ts.load_legs(nba, None, date(2025, 12, 1), now=None, calmap=CALMAP)
    no_store = ts.load_legs(nba, None, SLATE, now=None, calmap=CALMAP)
    assert not no_store.no_slate and no_store.legs == []


def test_forecast_made_after_the_run_clock_is_not_read() -> None:
    odds = build_odds([quote("draftkings", "over", -110)])
    out = ts.load_legs(build_nba(), odds, SLATE, now=MADE - timedelta(minutes=1), calmap=CALMAP)
    assert out.no_slate


# ------------------------------------------------------ parity with nba.export.best_model


def _row(
    model: str, run: str, made: datetime, fresh: str, comparison: bool, q: list[float], **kw: Any
) -> dict[str, Any]:
    return {
        "game_id": GID, "player_id": PID, "stat": "pts", "model_name": model, "run_id": run,
        "made_at": made, "comparison_only": comparison, "freshness": fresh,
        "freshness_reason": None if fresh == "fresh" else "month_block_mismatch", "q_grid": q,
        "tipoff": TIP, "_a0_source": kw.get("a0_source"), "_a0_q19": kw.get("a0"),
        "a0_source": kw.get("a0_source"), "a0_q19": kw.get("a0"),
    }  # fmt: skip


@pytest.mark.parametrize(
    "case",
    ["avg_fresh", "avg_stale", "avg_missing", "avg_behind", "avg_inputs_changed", "postseason"],
)
def test_choose_primary_matches_the_export(case: str) -> None:
    gid = "0042600001" if case == "postseason" else GID
    prod_q = q_grid()
    t0 = MADE
    prod = _row(
        ts.PROPS_MODEL,
        "p1",
        t0 + timedelta(minutes=10 if case == "avg_behind" else 0),
        "n/a",
        False,
        prod_q,
    )
    avg_q = q_grid(25.0)
    a0 = [v + 1.0 for v in prod_q] if case == "avg_inputs_changed" else prod_q
    avg = _row(
        ts.AVG_MODEL, "a1", t0, "stale" if case == "avg_stale" else "fresh", True, avg_q,
        a0_source=ts.PROPS_MODEL, a0=a0,
    )  # fmt: skip
    rows = [prod] if case == "avg_missing" else [prod, avg]
    for r in rows:
        r["game_id"] = gid
    mine = ts.choose_primary(gid, [dict(r) for r in rows], set())
    theirs = [dict(r) for r in rows]
    best_model.mark_primary(theirs, [], set())
    chosen = [r for r in theirs if r["is_primary"]]
    assert len(chosen) == 1
    assert mine[0] is not None and mine[0]["run_id"] == chosen[0]["run_id"]
    assert mine[1] == chosen[0]["primary_source"]


def test_avg_freshness_matches_the_export() -> None:
    kw: dict[str, Any] = {
        "made_at": MADE, "installed_at": datetime(2026, 10, 1), "fit_through": date(2026, 9, 30),
        "month_block": "2026-10", "stale_flag": False, "game_date": SLATE,
    }  # fmt: skip
    for patch in (
        {},
        {"stale_flag": True},
        {"month_block": "2026-09"},
        {"fit_through": date(2026, 10, 1)},
        {"installed_at": MADE + timedelta(hours=1)},
        {"installed_at": None},
    ):
        k = {**kw, **patch}
        assert ts.avg_freshness(**k) == best_model.avg_freshness(**k)


def test_only_the_newest_snapshot_per_book_and_market_is_selectable() -> None:
    """M2: a line the book moved off stays out, even though its old price favours the model."""
    old = PRICED - timedelta(minutes=20)
    odds = build_odds(
        [
            quote("draftkings", "over", +100, at=old, point=18.5),  # withdrawn line, rich price
            quote("draftkings", "under", -120, at=old, point=18.5),
            quote("draftkings", "over", -115, point=22.5),
            quote("draftkings", "under", -105, point=22.5),
        ]
    )
    out = ts.load_legs(build_nba(), odds, SLATE, now=None, calmap=CALMAP)
    assert {x.line for x in out.legs} == {22.5} and len(out.legs) == 2
