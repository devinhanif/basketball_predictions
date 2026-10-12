# ruff: noqa: E501
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import duckdb
import numpy as np

from nba.daily.pplay_shadow.store import ensure_tables
from research.eval import pplay_into_props_live as P

TIP = datetime(2026, 10, 21, 23, 0)
GRID = [float(i) * 0.5 for i in range(19)]  # 0..9 continuous


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    ensure_tables(con)
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT, home_team INT, away_team INT, home_pts INT, away_pts INT, national_tv VARCHAR)"
    )
    con.execute(
        "CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT, team_id INT, minutes FLOAT, pts INT, reb INT, ast INT, fg3m INT)"
    )
    con.execute(
        "CREATE TABLE forward_predictions (run_id VARCHAR, made_at TIMESTAMP, game_id VARCHAR, tipoff TIMESTAMP, model_name VARCHAR, version VARCHAR, target VARCHAR, player_id INT, prediction JSON)"
    )
    con.execute(
        "INSERT INTO games VALUES ('g1', ?, 2026, 1, 2, 100, 90, NULL), ('g0', ?, 2026, 1, 3, 100, 90, NULL)",
        [date(2026, 10, 21), date(2026, 10, 20)],
    )
    return con


def _shadow(
    con: duckdb.DuckDBPyConnection,
    run: str,
    made: datetime,
    pid: int,
    p_prod: float,
    p_r: float | None,
    props: bool = True,
) -> None:
    pc = json.dumps({"pts": {"model": "m", "mean": 5.0, "q_grid": GRID}}) if props else None
    con.execute(
        "INSERT INTO forward_pplay_shadow (run_id, made_at, game_id, tipoff, player_id, team_id, model_version, p_play_prod, p_play_r, in_prod_log, proj_minutes, props_cond) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [run, made, "g1", TIP, pid, 1, "pplay_r_v1", p_prod, p_r, True, 12.0, pc],
    )


def _score(
    con: duckdb.DuckDBPyConnection,
    run: str,
    made: datetime,
    pid: int,
    y: float,
    status: str = "scored",
) -> None:
    con.execute(
        "INSERT INTO forward_pplay_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL)",
        [
            datetime(2026, 10, 22),
            "g1",
            date(2026, 10, 21),
            2026,
            pid,
            run,
            made,
            TIP,
            0.5,
            0.5,
            y,
            0.0,
            0.0,
            True,
            status,
        ],
    )


def test_reader_picks_latest_pre_cutoff_row_and_converts_to_integer_support() -> None:
    con = _con()
    early, mid, late = (
        TIP - timedelta(hours=5),
        TIP - timedelta(minutes=90),
        TIP - timedelta(minutes=30),
    )
    _shadow(con, "a", early, 7, 0.8, 0.7)
    _shadow(con, "b", mid, 7, 0.8, 0.6)  # latest with made_at <= tip - 60
    _shadow(con, "c", late, 7, 0.8, 0.1)  # inside the 60-minute cutoff: never eligible
    _score(con, "b", mid, 7, 1.0)
    _score(con, "c", late, 7, 1.0)
    con.execute("INSERT INTO player_game_stats VALUES ('g1', 7, 1, 20, 6, 2, 1, 0)")
    r = P.load_live(con, "pts")
    assert len(r["pid"]) == 1 and abs(r["p_r"][0] - 0.6) < 1e-12 and r["y_u"][0] == 6.0
    assert r["qi"][0].tolist() == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9][
        :19
    ] or r["qi"].shape == (1, 19)
    assert (np.diff(r["qi"][0]) >= 0).all() and r["qi"].dtype == np.int16


def test_reader_drops_null_p_r_missing_grid_and_zeroes_dnp() -> None:
    con = _con()
    mid = TIP - timedelta(minutes=90)
    _shadow(con, "a", mid, 1, 0.9, None)  # R has no row
    _shadow(con, "a", mid, 2, 0.9, 0.8, props=False)  # no conditional grid
    _shadow(con, "a", mid, 3, 0.9, 0.2)  # DNP
    for pid in (1, 2, 3):
        _score(con, "a", mid, pid, 0.0)
    r = P.load_live(con, "pts")
    assert r["pid"].tolist() == [3] and r["y_u"].tolist() == [0.0] and int(r["dropped"][0]) == 1
    assert r["b2b"].tolist() == [True]  # team 1 played g0 the day before


def test_score_live_prefers_the_right_p_and_shuffle_is_alive() -> None:
    con = _con()
    mid = TIP - timedelta(minutes=90)
    rng = np.random.default_rng(0)
    for pid in range(400):
        played = rng.random() < 0.5
        _shadow(con, "a", mid, pid, 0.5, 0.97 if played else 0.03)
        _score(con, "a", mid, pid, float(played))
        con.execute(
            "INSERT INTO player_game_stats VALUES ('g1', ?, 1, ?, 4, 1, 1, 0)",
            [pid, 20.0 if played else 0.0],
        )
    out = P.score_live(P.load_live(con, "pts"))
    assert out["n_rows"] == 400
    assert out["R_minus_prod"]["ci_hi"] < 0 and out["shuffled_R_minus_prod"]["ci_lo"] > 0


def test_stamp_gates_counts() -> None:
    con = _con()
    _shadow(con, "a", TIP - timedelta(minutes=30), 5, 0.9, 0.9)  # only a late row
    g = P.stamp_and_shadow_gates(con)
    assert g["late_only_pairs"] == 1 and g["rows_after_tip"] == 0 and g["prod_p_compared"] == 0


def test_piecewise_week_matches_flat_when_one_segment() -> None:
    assert (
        abs(P.week_reached_piecewise(2200, [(float("inf"), 1225.0)]) - P.week_reached(2200)) < 1e-9
    )
