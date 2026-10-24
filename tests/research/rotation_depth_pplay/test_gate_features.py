# ruff: noqa: E501
"""As-of and planted-future tests for the ROTATION_DEPTH_PPLAY gate features (docs/prereg/ROTATION_DEPTH_PPLAY.md, DRAFT)."""

from __future__ import annotations

import datetime as dt

import duckdb
import numpy as np
import polars as pl

from research.eval import rotation_depth_pplay_gate as G


def _fixture(n_games: int = 14, later_edit: bool = False) -> duckdb.DuckDBPyConnection:
    """Two teams (1, 2) playing each other n_games times on consecutive days, season 2023; two players per team."""
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INTEGER, home_team INTEGER, away_team INTEGER, home_pts INTEGER, away_pts INTEGER)"
    )
    con.execute(
        "CREATE TABLE player_game_stats (game_id VARCHAR, player_id INTEGER, team_id INTEGER, minutes FLOAT, fga INTEGER, fta INTEGER, oreb INTEGER, tov INTEGER)"
    )
    d0 = dt.date(2023, 11, 1)
    for k in range(n_games):
        gid = f"00223{k:05d}"
        con.execute(
            "INSERT INTO games VALUES (?, ?, 2023, 1, 2, ?, ?)",
            [gid, d0 + dt.timedelta(days=k), 100 + k, 99],
        )
        wild = later_edit and k >= 10
        for team in (1, 2):
            for j in range(2):
                fga = 80 + (5 * k if not wild else 500)
                con.execute(
                    "INSERT INTO player_game_stats VALUES (?, ?, ?, ?, ?, 20, 10, 12)",
                    [gid, team * 10 + j, team, 30.0 if j == 0 else 9.0, fga // 2],
                )
    return con


def test_pace10_uses_only_strictly_earlier_games() -> None:
    con = _fixture()
    tg = G.team_game_table(con).filter(pl.col("team_id") == 1).sort("game_date")
    pace = tg["pace_game"].to_numpy()
    pace10 = tg["pace10"].to_numpy()
    for i in range(len(pace)):
        prior = pace[max(0, i - G.PACE_N) : i]
        if len(prior) < G.MIN_PRIOR_GAMES:
            assert np.isnan(pace10[i]) or pace10[i] is None
        else:
            assert abs(pace10[i] - float(np.mean(prior))) < 1e-9


def test_planted_future_does_not_move_earlier_features() -> None:
    base = G.team_game_table(_fixture()).sort(["team_id", "game_date"])
    edit = G.team_game_table(_fixture(later_edit=True)).sort(["team_id", "game_date"])
    # games 0..10: game 10's own pace may differ (its box is edited) but its as-of features use games 0..9 only
    cols = ["pace10", "win20", "rest_days", "k_game"]
    for team in (1, 2):
        a = base.filter(pl.col("team_id") == team)
        b = edit.filter(pl.col("team_id") == team)
        assert a.head(11).select(cols).equals(b.head(11).select(cols))
        assert a.tail(3)["pace10"].to_list() != b.tail(3)["pace10"].to_list()


def test_depth_outcome_counts_players_at_eight_minutes() -> None:
    tg = G.team_game_table(_fixture())
    assert set(tg["depth8"].to_list()) == {2}
    assert set(tg["n_played"].to_list()) == {2}
