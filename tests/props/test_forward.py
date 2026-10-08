"""Forward routed prop predictions: shape, routing, determinism, no leakage."""

from __future__ import annotations

import json
from datetime import date, timedelta

import duckdb
import polars as pl
import pytest

from nba.props.forward import (
    MODEL_SEASON_AVG,
    MODEL_SIM,
    OUTPUT_COLUMNS,
    ForwardConfig,
    game_seed,
    predict_slate,
    route_model,
)
from tests.daily.conftest import RUN_DATE, STAT_COLS, T1, T2, T3, T4, add_history
from tests.fixtures.loader import build_fixture_db

SLATE = pl.DataFrame(
    {
        "game_id": ["0022600001", "0022600002"],
        "home_team": [T1, T3],
        "away_team": [T2, T4],
    }
)
ALL_SIM = ForwardConfig(sim_buckets=frozenset({"smooth", "lumpy", "erratic", "intermittent",
                                               "insufficient_history"}))  # fmt: skip


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = build_fixture_db(":memory:")
    add_history(c)
    return c


def _plant(con: duckdb.DuckDBPyConnection, game_id: str, d: date, pts: int) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts)"
        " VALUES (?, ?, 2026, ?, ?, 100, 90)",
        [game_id, d, T1, T2],
    )
    for pid, team in ((1000, T1), (1001, T1), (1100, T2), (1101, T2)):
        cols = ",".join(STAT_COLS)
        vals = {c: 0 for c in STAT_COLS}
        vals.update(game_id=game_id, player_id=pid, team_id=team, minutes=40.0, pts=pts,
                    reb=pts, ast=pts, fg3m=pts, starter=True)  # fmt: skip
        con.execute(
            f"INSERT INTO player_game_stats ({cols}) VALUES ({','.join('?' * len(STAT_COLS))})",
            [vals[c] for c in STAT_COLS],
        )


def test_route_model_rule() -> None:
    cfg = ForwardConfig()
    assert route_model("pts", "intermittent", cfg) == MODEL_SEASON_AVG  # pts not sim-routed
    assert route_model("reb", "insufficient_history", cfg) == MODEL_SIM
    assert route_model("ast", "erratic", cfg) == MODEL_SIM
    assert route_model("pts", "smooth", cfg) == MODEL_SEASON_AVG
    assert route_model("ast", "lumpy", cfg) == MODEL_SEASON_AVG
    assert route_model("fg3m", "intermittent", cfg) == MODEL_SEASON_AVG  # no sim head


def test_output_contract_and_distribution_sanity(con: duckdb.DuckDBPyConnection) -> None:
    out = predict_slate(con, RUN_DATE, SLATE, None, n_sims=200, config=ALL_SIM)
    assert out.columns == OUTPUT_COLUMNS and out.height > 0
    assert set(out["stat"].unique()) == {"pts", "reb", "ast", "fg3m"}
    assert set(out["model"].unique()) == {MODEL_SIM, MODEL_SEASON_AVG}
    assert out.filter(pl.col("stat") == "fg3m")["model"].unique().to_list() == [MODEL_SEASON_AVG]
    for r in out.iter_rows(named=True):
        pj = json.loads(r["p_ge"])
        p = [pj[k] for k in sorted(pj, key=int)]
        assert all(0.0 <= x <= 1.0 for x in p)
        assert all(a >= b - 1e-12 for a, b in zip(p, p[1:], strict=False))  # non-increasing in N
        assert r["q10"] <= r["q50"] <= r["q90"]
        assert r["mean"] >= 0.0
    assert 1005 not in out["player_id"].to_list()  # 0-minute bench player in history


def test_default_routing_sends_smooth_regulars_to_season_avg(
    con: duckdb.DuckDBPyConnection,
) -> None:
    out = predict_slate(con, RUN_DATE, SLATE, None, n_sims=100)
    pts = out.filter(pl.col("stat") == "pts")
    assert set(pts["bucket"].unique()) <= {"smooth", "lumpy", "erratic", "intermittent"}
    assert (pts.filter(pl.col("bucket") == "smooth")["model"] == MODEL_SEASON_AVG).all()


def test_deterministic_and_order_independent(con: duckdb.DuckDBPyConnection) -> None:
    a = predict_slate(con, RUN_DATE, SLATE, None, n_sims=150, seed=3, config=ALL_SIM)
    b = predict_slate(con, RUN_DATE, SLATE, None, n_sims=150, seed=3, config=ALL_SIM)
    c = predict_slate(con, RUN_DATE, SLATE.reverse(), None, n_sims=150, seed=3, config=ALL_SIM)
    d = predict_slate(con, RUN_DATE, SLATE, None, n_sims=150, seed=4, config=ALL_SIM)
    assert a.equals(b) and a.equals(c)
    assert not a.equals(d)
    assert game_seed(3, "0022600001") != game_seed(3, "0022600002")


def test_out_players_absent_and_share_redistributed(con: duckdb.DuckDBPyConnection) -> None:
    base = predict_slate(con, RUN_DATE, SLATE, None, n_sims=300, config=ALL_SIM)
    out = predict_slate(con, RUN_DATE, SLATE, {1000, 3000}, n_sims=300, config=ALL_SIM)
    assert {1000, 3000}.isdisjoint(out["player_id"].to_list())
    assert {1000, 3000} <= set(base["player_id"].to_list())
    key = (pl.col("player_id") == 1001) & (pl.col("stat") == "pts")
    m_base = base.filter(key)["mean_sim"][0]
    m_out = out.filter(key)["mean_sim"][0]
    assert m_out > m_base  # teammate inherits the absent player's usage


def test_same_day_and_future_rows_never_change_output(con: duckdb.DuckDBPyConnection) -> None:
    a = predict_slate(con, RUN_DATE, SLATE, None, n_sims=150, config=ALL_SIM)
    _plant(con, "0022600777", RUN_DATE, 99)  # same-day (half-ingested) game
    _plant(con, "0022600778", RUN_DATE + timedelta(days=3), 77)  # future
    b = predict_slate(con, RUN_DATE, SLATE, None, n_sims=150, config=ALL_SIM)
    assert a.equals(b)
    # sanity: a planted row dated BEFORE as_of does move the output (the test is not vacuous)
    _plant(con, "0022600779", RUN_DATE - timedelta(days=1), 99)
    c = predict_slate(con, RUN_DATE, SLATE, None, n_sims=150, config=ALL_SIM)
    assert not a.equals(c)


def test_does_not_write_to_source_connection(con: duckdb.DuckDBPyConnection) -> None:
    tables = ["games", "player_game_stats", "possessions"]
    before = [con.execute(f"SELECT count(*) FROM {t}").fetchone() for t in tables]
    predict_slate(con, RUN_DATE, SLATE, None, n_sims=50)
    assert before == [con.execute(f"SELECT count(*) FROM {t}").fetchone() for t in tables]


def test_empty_and_invalid_inputs(con: duckdb.DuckDBPyConnection) -> None:
    assert predict_slate(con, RUN_DATE, SLATE.head(0), None).height == 0
    dup = pl.DataFrame({"game_id": ["a", "b"], "home_team": [T1, T1], "away_team": [T2, T3]})
    with pytest.raises(ValueError, match="twice"):
        predict_slate(con, RUN_DATE, dup, None)
    # a date before any history projects nobody
    assert predict_slate(con, date(2020, 1, 1), SLATE, None, n_sims=50).height == 0
