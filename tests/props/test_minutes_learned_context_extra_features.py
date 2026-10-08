"""Tests that :func:`fit_learned_game_context` accepts an OPTIONAL
``extra_features`` frame joined on ``(game_id, player_id)``, with the exact
shape ``nba.preprocess.depth_chart.build_depth_chart_features`` is expected
to produce (``[game_id, player_id, depth_chart_rank:int,
minutes_ceiling_hazard:float]``) -- see task conferral note in
``nba.props.minutes`` module docstring. This module does NOT import
``nba.preprocess.depth_chart`` (it may not exist yet); the frame below is
purely synthetic, matching only the documented column names/dtypes.
"""

from __future__ import annotations

import datetime as dt
import math

import duckdb
import numpy as np
import polars as pl
import pytest

from nba.db.connect import connect
from nba.props.config import MinutesModelConfig
from nba.props.minutes import build_minutes_features, fit_learned_game_context


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = connect(":memory:")
    yield c
    c.close()


def _insert_game(con: duckdb.DuckDBPyConnection, game_id: str, game_date: str) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) "  # noqa: E501
        "VALUES (?, ?, 2023, 1, 2, 100, 90)",
        [game_id, game_date],
    )


def _insert_player_row(
    con: duckdb.DuckDBPyConnection, game_id: str, player_id: int, minutes: float | None
) -> None:
    con.execute(
        """
        INSERT INTO player_game_stats
            (game_id, player_id, team_id, minutes, pts, reb, ast, fg3m, stl, blk, tov, starter)
        VALUES (?, ?, 1, ?, 10, 5, 3, 1, 1, 0, 1, true)
        """,
        [game_id, player_id, minutes],
    )


def _actual_minutes_frame(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    con.execute(
        "SELECT game_id, player_id, COALESCE(minutes, 0.0) AS minutes FROM player_game_stats"
    )
    rows = con.fetchall()
    schema = {"game_id": pl.Utf8, "player_id": pl.Int64, "minutes": pl.Float64}
    return pl.DataFrame(rows, schema=schema, orient="row")


def _seed(con: duckdb.DuckDBPyConnection, n: int, rng: np.random.Generator) -> None:
    start = dt.date(2022, 10, 1)
    for i in range(n):
        gid = f"g{i}"
        _insert_game(con, gid, str(start + dt.timedelta(days=i)))
        _insert_player_row(con, gid, 101, max(rng.normal(22, 4), 0.0))


def _synthetic_depth_chart_features(feats: pl.DataFrame) -> pl.DataFrame:
    """Exact documented shape: [game_id, player_id, depth_chart_rank:int,
    minutes_ceiling_hazard:float]."""
    n = feats.height
    return pl.DataFrame(
        {
            "game_id": feats.select("game_id").to_series(),
            "player_id": feats.select("player_id").to_series(),
            "depth_chart_rank": pl.Series([1] * n, dtype=pl.Int64),
            "minutes_ceiling_hazard": pl.Series([0.1] * n, dtype=pl.Float64),
        }
    )


def test_fit_includes_extra_feature_columns_as_candidate_predictors(
    con: duckdb.DuckDBPyConnection,
) -> None:
    rng = np.random.default_rng(0)
    _seed(con, n=300, rng=rng)
    feats = build_minutes_features(con, use_game_context=True)
    actual = (
        feats.select("game_id", "player_id")
        .join(_actual_minutes_frame(con), on=["game_id", "player_id"], how="left")
        .select("minutes")
        .to_series()
        .fill_null(0.0)
        .to_numpy()
    )
    played = (actual > 0.0).astype(float)
    extra = _synthetic_depth_chart_features(feats)

    cfg = MinutesModelConfig(use_learned_game_context=True, learned_context_min_cal_n=50)
    params = fit_learned_game_context(feats, played, actual, cfg, extra_features=extra)

    assert "depth_chart_rank" in params.feature_columns
    assert "minutes_ceiling_hazard" in params.feature_columns
    assert "depth_chart_rank" in params.p_play_coef
    assert "minutes_ceiling_hazard" in params.mu_coef
    assert math.isfinite(params.p_play_coef["depth_chart_rank"])
    assert math.isfinite(params.mu_coef["minutes_ceiling_hazard"])
    # game_id/player_id keys never leak into the predictor set.
    assert "game_id" not in params.feature_columns
    assert "player_id" not in params.feature_columns


def test_fit_without_extra_features_has_only_game_context_columns(
    con: duckdb.DuckDBPyConnection,
) -> None:
    rng = np.random.default_rng(1)
    _seed(con, n=300, rng=rng)
    feats = build_minutes_features(con, use_game_context=True)
    actual = (
        feats.select("game_id", "player_id")
        .join(_actual_minutes_frame(con), on=["game_id", "player_id"], how="left")
        .select("minutes")
        .to_series()
        .fill_null(0.0)
        .to_numpy()
    )
    played = (actual > 0.0).astype(float)
    cfg = MinutesModelConfig(use_learned_game_context=True, learned_context_min_cal_n=50)
    params = fit_learned_game_context(feats, played, actual, cfg)
    assert "depth_chart_rank" not in params.feature_columns
    assert "minutes_ceiling_hazard" not in params.feature_columns
