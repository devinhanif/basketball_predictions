"""Tests for the explicit blowout/garbage-time branch
(``MinutesModelConfig.use_garbage_time`` / :func:`fit_garbage_time_params`,
``docs/NEXT_OPTIONS.md`` §1 Option B).

``projected_margin`` is always an injected, synthetic input here -- this
module never computes it (see ``nba.props.minutes`` module docstring);
mirrors ``tests/props/test_minutes_game_context.py``'s
fixture/synthetic-only discipline.
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
    GarbageTimeParams,
    build_minutes_features,
    fit_garbage_time_params,
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


def _actual_minutes_frame(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    con.execute(
        "SELECT game_id, player_id, COALESCE(minutes, 0.0) AS minutes FROM player_game_stats"
    )
    rows = con.fetchall()
    schema = {"game_id": pl.Utf8, "player_id": pl.Int64, "minutes": pl.Float64}
    return pl.DataFrame(rows, schema=schema, orient="row")


def _seed_blowouts_and_close_games(
    con: duckdb.DuckDBPyConnection, n: int, rng: np.random.Generator
) -> np.ndarray:
    """``n`` games alternating "close" (small margin, normal minutes ~32)
    and "blowout" (large margin, reduced garbage-time minutes ~12).
    Returns the synthetic ``projected_margin`` array, same order as
    ``build_minutes_features``'s own ``(game_date, game_id)`` sort."""
    start = dt.date(2022, 10, 1)
    margins = []
    for i in range(n):
        gid = f"g{i}"
        _insert_game(con, gid, str(start + dt.timedelta(days=i)))
        is_blowout = i % 3 == 0
        margin = rng.uniform(25, 35) if is_blowout else rng.uniform(1, 8)
        minutes = max(rng.normal(12, 2), 0.0) if is_blowout else max(rng.normal(32, 2), 0.0)
        _insert_player_row(con, gid, 101, minutes)
        margins.append(margin)
    return np.array(margins)


# --------------------------------------------------------------------------
# 1. Honesty guard: too few blowout calibration rows -> no-op (default_mu).
# --------------------------------------------------------------------------


def test_fit_is_noop_below_min_cal_n(con: duckdb.DuckDBPyConnection) -> None:
    rng = np.random.default_rng(0)
    margins = _seed_blowouts_and_close_games(con, n=10, rng=rng)
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
    cfg = MinutesModelConfig(use_garbage_time=True, garbage_time_min_cal_n=100)
    params = fit_garbage_time_params(feats, played, actual, margins, cfg)
    assert isinstance(params, GarbageTimeParams)
    assert params.garbage_time_mu == cfg.default_mu
    assert "no-op" in params.note


# --------------------------------------------------------------------------
# 2. Fit on enough blowout rows learns a mean below default_mu/close-game
#    minutes (since blowout minutes were synthesized to be lower).
# --------------------------------------------------------------------------


def test_fit_learns_reduced_mean_from_blowout_rows(con: duckdb.DuckDBPyConnection) -> None:
    rng = np.random.default_rng(1)
    margins = _seed_blowouts_and_close_games(con, n=600, rng=rng)
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
    cfg = MinutesModelConfig(use_garbage_time=True, garbage_time_min_cal_n=50)
    params = fit_garbage_time_params(feats, played, actual, margins, cfg)
    assert params.n_cal >= 50
    assert math.isfinite(params.garbage_time_mu)
    # Synthesized blowout minutes (~12) are far below the close-game mean
    # (~32) and the league default_mu (24.0); the shrunk fit should land
    # clearly below default_mu, not just noise around it.
    assert params.garbage_time_mu < cfg.default_mu - 3.0


# --------------------------------------------------------------------------
# 3. predict_minutes: a large |projected_margin| shrinks mu toward
#    garbage_time_mu and widens sigma; a small margin leaves mu/sigma
#    essentially at the baseline (w ~ 0).
# --------------------------------------------------------------------------


def test_predict_minutes_shrinks_mu_and_widens_sigma_for_blowouts(
    con: duckdb.DuckDBPyConnection,
) -> None:
    rng = np.random.default_rng(2)
    _seed_blowouts_and_close_games(con, n=10, rng=rng)
    feats = build_minutes_features(con, use_game_context=True)
    cfg = MinutesModelConfig(
        use_garbage_time=True,
        garbage_time_margin_threshold=20.0,
        garbage_time_margin_scale=10.0,
        garbage_time_sigma_inflate=2.0,
    )
    gt_params = GarbageTimeParams(garbage_time_mu=10.0, n_cal=200)

    close_margin = np.full(feats.height, 2.0)
    blowout_margin = np.full(feats.height, 35.0)  # >= threshold + scale -> w = 1.0

    baseline = predict_minutes(feats, MinutesModelConfig(use_garbage_time=False))
    dists_close = predict_minutes(
        feats, cfg, projected_margin=close_margin, garbage_time=gt_params
    )
    dists_blowout = predict_minutes(
        feats, cfg, projected_margin=blowout_margin, garbage_time=gt_params
    )

    for b, c, bl in zip(baseline, dists_close, dists_blowout, strict=True):
        assert c.mu == pytest.approx(b.mu, abs=1e-6)
        assert c.sigma == pytest.approx(b.sigma, abs=1e-6)
        assert bl.mu == pytest.approx(10.0, abs=1e-6)  # w=1.0 -> fully at garbage_time_mu
        assert bl.sigma == pytest.approx(b.sigma * 2.0, abs=1e-6)  # sigma_inflate=2.0 at w=1.0


def test_predict_minutes_noop_when_flag_on_but_missing_inputs(
    con: duckdb.DuckDBPyConnection,
) -> None:
    rng = np.random.default_rng(3)
    _seed_blowouts_and_close_games(con, n=10, rng=rng)
    feats = build_minutes_features(con, use_game_context=True)
    cfg = MinutesModelConfig(use_garbage_time=True)
    baseline = predict_minutes(feats, MinutesModelConfig(use_garbage_time=False))
    dists_no_margin = predict_minutes(feats, cfg, garbage_time=GarbageTimeParams(10.0, 200))
    dists_no_params = predict_minutes(feats, cfg, projected_margin=np.full(feats.height, 35.0))
    assert [d.params() for d in dists_no_margin] == [d.params() for d in baseline]
    assert [d.params() for d in dists_no_params] == [d.params() for d in baseline]


# --------------------------------------------------------------------------
# 4. No-leakage: garbage-time application is a pure per-row function of
#    that row's own projected_margin -- a planted future (wild) row's
#    margin/outcome must not change an earlier row's prediction.
# --------------------------------------------------------------------------


def test_garbage_time_is_pure_per_row_function(con: duckdb.DuckDBPyConnection) -> None:
    rng = np.random.default_rng(4)
    _seed_blowouts_and_close_games(con, n=6, rng=rng)
    feats = build_minutes_features(con, use_game_context=True)
    cfg = MinutesModelConfig(use_garbage_time=True)
    gt_params = GarbageTimeParams(garbage_time_mu=10.0, n_cal=200)
    margins = np.array([2.0, 35.0, 5.0, 40.0, 1.0, 22.0][: feats.height])

    dists_full = predict_minutes(feats, cfg, projected_margin=margins, garbage_time=gt_params)

    one_row = feats.filter(pl.col("game_id") == "g2")
    one_margin = margins[2:3]
    dist_isolated = predict_minutes(
        one_row, cfg, projected_margin=one_margin, garbage_time=gt_params
    )[0]
    idx = feats.select("game_id").to_series().to_list().index("g2")
    assert dist_isolated.params() == dists_full[idx].params()
