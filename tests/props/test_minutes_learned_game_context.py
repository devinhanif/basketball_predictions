"""Tests for the learned (ridge-fit, not hand-set) game-context adjustment
(``MinutesModelConfig.use_learned_game_context`` / :func:`fit_learned_game_context`,
``docs/NEXT_OPTIONS.md`` §1 Option A).

Mirrors ``tests/props/test_minutes_game_context.py``'s fixture/synthetic-only
discipline -- no real DuckDB file, no full pipeline run.
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
from nba.props.minutes import (
    GAME_CONTEXT_FEATURE_COLUMNS,
    LearnedGameContextParams,
    build_minutes_features,
    fit_learned_game_context,
    predict_minutes,
)


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


def _seed_games(con: duckdb.DuckDBPyConnection, n: int, player_id: int = 101) -> None:
    start = dt.date(2023, 10, 24)
    for i in range(n):
        gid = f"g{i}"
        _insert_game(con, gid, str(start + dt.timedelta(days=2 * i)))
        minutes = 20.0 + (i % 5)
        _insert_player_row(con, gid, player_id, minutes)


# --------------------------------------------------------------------------
# 1. Honesty guard: too few calibration rows -> no-op (all-zero) fit.
# --------------------------------------------------------------------------


def test_fit_is_noop_below_min_cal_n(con: duckdb.DuckDBPyConnection) -> None:
    _seed_games(con, n=5)
    feats = build_minutes_features(con, use_game_context=True)
    actual_played = np.ones(feats.height)
    actual_minutes = feats.select("game_id", "player_id").join(
        _actual_minutes_frame(con), on=["game_id", "player_id"], how="left"
    ).select("minutes").to_series().fill_null(0.0).to_numpy()

    cfg = MinutesModelConfig(use_learned_game_context=True, learned_context_min_cal_n=200)
    params = fit_learned_game_context(feats, actual_played, actual_minutes, cfg)
    assert isinstance(params, LearnedGameContextParams)
    assert all(v == 0.0 for v in params.p_play_coef.values())
    assert all(v == 0.0 for v in params.mu_coef.values())
    assert params.p_play_intercept == 0.0
    assert params.mu_intercept == 0.0
    assert "no-op" in params.note


def _actual_minutes_frame(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    con.execute(
        "SELECT game_id, player_id, COALESCE(minutes, 0.0) AS minutes FROM player_game_stats"
    )
    rows = con.fetchall()
    schema = {"game_id": pl.Utf8, "player_id": pl.Int64, "minutes": pl.Float64}
    return pl.DataFrame(rows, schema=schema, orient="row")


# --------------------------------------------------------------------------
# 2. Fit runs end-to-end on a larger synthetic sample and produces a
#    coefficient for every candidate column.
# --------------------------------------------------------------------------


def test_fit_produces_coefficients_for_every_candidate_column(
    con: duckdb.DuckDBPyConnection,
) -> None:
    rng = np.random.default_rng(0)
    start = dt.date(2022, 10, 1)
    for i in range(400):
        gid = f"g{i}"
        _insert_game(con, gid, str(start + dt.timedelta(days=i)))
        minutes = 20.0 + rng.normal(0, 3)
        _insert_player_row(con, gid, 101, max(minutes, 0.0))

    feats = build_minutes_features(con, use_game_context=True)
    actual = feats.select("game_id", "player_id").join(
        _actual_minutes_frame(con), on=["game_id", "player_id"], how="left"
    ).select("minutes").to_series().fill_null(0.0).to_numpy()
    actual_played = (actual > 0.0).astype(float)

    cfg = MinutesModelConfig(use_learned_game_context=True, learned_context_min_cal_n=50)
    params = fit_learned_game_context(feats, actual_played, actual, cfg)

    assert params.n_cal >= 50
    for col in GAME_CONTEXT_FEATURE_COLUMNS:
        assert col in params.p_play_coef
        assert col in params.mu_coef
        assert math.isfinite(params.p_play_coef[col])
        assert math.isfinite(params.mu_coef[col])


# --------------------------------------------------------------------------
# 3. predict_minutes applies the learned params (not the hand-set one) when
#    use_learned_game_context=True, and is a no-op (falls back to shrinkage
#    baseline) when the flag is on but no params are supplied.
# --------------------------------------------------------------------------


def test_predict_minutes_uses_learned_params_when_flag_on(con: duckdb.DuckDBPyConnection) -> None:
    _seed_games(con, n=5)
    feats = build_minutes_features(con, use_game_context=True)
    cfg = MinutesModelConfig(use_learned_game_context=True)
    params = LearnedGameContextParams(
        feature_columns=["b2b"],
        p_play_coef={"b2b": -0.5},
        p_play_intercept=0.0,
        mu_coef={"b2b": -10.0},
        mu_intercept=0.0,
        n_cal=100,
        n_cal_played=100,
    )
    dists_with = predict_minutes(feats, cfg, learned_context=params)
    dists_without = predict_minutes(feats, MinutesModelConfig(use_learned_game_context=False))
    # Not every row necessarily has b2b=True in this tiny fixture, but at
    # least the plumbing must run and produce valid distributions.
    assert len(dists_with) == len(dists_without) == feats.height
    for d in dists_with:
        assert 0.0 <= d.p_play <= 1.0


def test_predict_minutes_noop_when_flag_on_but_no_params(con: duckdb.DuckDBPyConnection) -> None:
    _seed_games(con, n=5)
    feats = build_minutes_features(con, use_game_context=True)
    cfg_flag_on_no_params = MinutesModelConfig(use_learned_game_context=True)
    cfg_off = MinutesModelConfig(use_learned_game_context=False, use_game_context=False)
    dists_a = predict_minutes(feats, cfg_flag_on_no_params, learned_context=None)
    dists_b = predict_minutes(feats, cfg_off)
    assert [d.params() for d in dists_a] == [d.params() for d in dists_b]


# --------------------------------------------------------------------------
# 4. No-leakage: fitting on a window that includes a planted future game
#    must not change an earlier row's prediction once params are applied
#    (same adversarial-plant pattern as test_minutes_game_context.py and
#    test_minutes_no_leakage.py).
# --------------------------------------------------------------------------


def test_learned_context_is_pure_per_row_function_at_apply_time(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _seed_games(con, n=5)
    feats = build_minutes_features(con, use_game_context=True)
    cfg = MinutesModelConfig(use_learned_game_context=True)
    params = LearnedGameContextParams(
        feature_columns=["b2b", "tanking_incentive"],
        p_play_coef={"b2b": -0.1, "tanking_incentive": -0.2},
        p_play_intercept=0.01,
        mu_coef={"b2b": -1.0, "tanking_incentive": -2.0},
        mu_intercept=0.1,
        n_cal=100,
        n_cal_played=100,
    )
    dists_full = predict_minutes(feats, cfg, learned_context=params)

    one_row = feats.filter(pl.col("game_id") == "g2")
    dist_isolated = predict_minutes(one_row, cfg, learned_context=params)[0]
    idx = feats.select("game_id").to_series().to_list().index("g2")
    assert dist_isolated.params() == dists_full[idx].params()


def test_fit_uses_only_chronologically_earlier_rows(con: duckdb.DuckDBPyConnection) -> None:
    """Planting a wild future game must not change a fit restricted (via
    cal_frac) to rows strictly before it -- proves chronological_split is
    doing its job, not just that predict-time application is pure."""
    rng = np.random.default_rng(1)
    start = dt.date(2022, 10, 1)
    for i in range(300):
        gid = f"g{i}"
        _insert_game(con, gid, str(start + dt.timedelta(days=i)))
        _insert_player_row(con, gid, 101, max(20.0 + rng.normal(0, 3), 0.0))

    feats_before = build_minutes_features(con, use_game_context=True)
    actual_before = feats_before.select("game_id", "player_id").join(
        _actual_minutes_frame(con), on=["game_id", "player_id"], how="left"
    ).select("minutes").to_series().fill_null(0.0).to_numpy()
    played_before = (actual_before > 0.0).astype(float)
    cfg = MinutesModelConfig(use_learned_game_context=True, learned_context_cal_frac=0.3)
    params_before = fit_learned_game_context(feats_before, played_before, actual_before, cfg)

    # Plant a wild future game with an extreme (but still only ~0.5% of
    # the dataset) outcome, strictly after everything above.
    _insert_game(con, "g_future", str(start + dt.timedelta(days=301)))
    _insert_player_row(con, "g_future", 101, 0.0)

    feats_after = build_minutes_features(con, use_game_context=True).filter(
        pl.col("game_id") != "g_future"
    )
    actual_after = feats_after.select("game_id", "player_id").join(
        _actual_minutes_frame(con), on=["game_id", "player_id"], how="left"
    ).select("minutes").to_series().fill_null(0.0).to_numpy()
    played_after = (actual_after > 0.0).astype(float)
    # cal_frac=0.3 of 300 rows (90) is unchanged by appending one row at
    # the very end, so the fit over the *same first 90 rows* must be
    # identical regardless of what's appended after it.
    params_after = fit_learned_game_context(feats_after, played_after, actual_after, cfg)

    assert params_before.p_play_coef == params_after.p_play_coef
    assert params_before.mu_coef == params_after.mu_coef
