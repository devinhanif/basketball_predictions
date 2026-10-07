"""Tests for the as-of game-context wiring into the minutes model
(``MinutesModelConfig.use_game_context``) and the direct minutes-model
evaluation (``nba.props.minutes.MinutesEval``).

Mirrors ``tests/props/test_minutes_no_leakage.py``'s fixture/synthetic-only
discipline -- no real DuckDB file, no full pipeline run.
"""

from __future__ import annotations

import math

import duckdb
import numpy as np
import polars as pl
import pytest

from nba.db.connect import connect
from nba.props.config import MinutesModelConfig
from nba.props.distributions import MinutesHurdleDist
from nba.props.minutes import (
    GAME_CONTEXT_FEATURE_COLUMNS,
    MinutesEval,
    build_minutes_features,
    evaluate_minutes_model,
    fetch_actual_minutes,
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


def _seed_games(con: duckdb.DuckDBPyConnection, n: int = 4) -> None:
    import datetime as dt

    start = dt.date(2023, 10, 24)
    for i in range(n):
        gid = f"g{i}"
        _insert_game(con, gid, str(start + dt.timedelta(days=2 * i)))
        _insert_player_row(con, gid, 101, 20.0 + i)


# --------------------------------------------------------------------------
# 1. Context columns present/absent behind the flag.
# --------------------------------------------------------------------------


def test_context_columns_present_when_flag_on(con: duckdb.DuckDBPyConnection) -> None:
    _seed_games(con)
    feats = build_minutes_features(con, use_game_context=True)
    for col in GAME_CONTEXT_FEATURE_COLUMNS:
        assert col in feats.columns
    # No null leftovers -- every row gets a documented fill-in default.
    for col in GAME_CONTEXT_FEATURE_COLUMNS:
        assert feats.select(col).null_count().item() == 0


def test_context_columns_absent_when_flag_off(con: duckdb.DuckDBPyConnection) -> None:
    _seed_games(con)
    feats = build_minutes_features(con, use_game_context=False)
    for col in GAME_CONTEXT_FEATURE_COLUMNS:
        assert col not in feats.columns
    # Still has the base feature set and no raw minutes column.
    assert "minutes" not in feats.columns
    assert "play_rate_prior" in feats.columns


def test_predict_minutes_runs_with_and_without_context(con: duckdb.DuckDBPyConnection) -> None:
    _seed_games(con)
    feats_on = build_minutes_features(con, use_game_context=True)
    feats_off = build_minutes_features(con, use_game_context=False)
    dists_on = predict_minutes(feats_on, MinutesModelConfig(use_game_context=True))
    dists_off = predict_minutes(feats_off, MinutesModelConfig(use_game_context=False))
    assert len(dists_on) == len(dists_off) == feats_on.height
    for d in dists_on + dists_off:
        assert isinstance(d, MinutesHurdleDist)
        assert 0.0 <= d.p_play <= 1.0


# --------------------------------------------------------------------------
# 2. No-leakage: a planted future game must not change an earlier row's
#    context-aware prediction (same pattern as test_minutes_no_leakage.py).
# --------------------------------------------------------------------------


def test_future_game_context_does_not_change_earlier_prediction(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _insert_game(con, "g1", "2023-10-24")
    _insert_player_row(con, "g1", 101, 20.0)
    _insert_game(con, "g2", "2023-10-28")
    _insert_player_row(con, "g2", 101, 22.0)
    _insert_game(con, "g3", "2023-11-01")
    _insert_player_row(con, "g3", 101, 24.0)

    cfg = MinutesModelConfig(use_game_context=True)
    feats_before = build_minutes_features(con, use_game_context=True).filter(
        pl.col("game_id") == "g3"
    )
    dist_before = predict_minutes(feats_before, cfg)[0]

    # Plant an extreme future game (wild travel/rest-triggering values are
    # not directly injectable via team_context here, but a wildly different
    # *actual minutes* on a strictly later date is the same style of
    # adversarial plant test_minutes_no_leakage.py uses, and it also
    # exercises the game-context join -- a future game changes the global
    # standings/tanking timeline computed by nba.features.game_context, so
    # this additionally proves that timeline's as-of join doesn't leak
    # into g3's row either.
    _insert_game(con, "g4_future", "2023-11-10")
    _insert_player_row(con, "g4_future", 101, 0.0)

    feats_after = build_minutes_features(con, use_game_context=True).filter(
        pl.col("game_id") == "g3"
    )
    dist_after = predict_minutes(feats_after, cfg)[0]

    assert dist_after.params() == dist_before.params()
    assert dist_after.mean() == pytest.approx(dist_before.mean())


def test_context_adjustment_is_pure_per_row_function(con: duckdb.DuckDBPyConnection) -> None:
    """The fixed-effect context adjustment depends only on that row's own
    context columns -- never on any other row -- so reordering/duplicating
    unrelated rows can't change a given row's prediction. This is the
    structural reason the no-leakage test above holds (no cross-row fit)."""
    _seed_games(con, n=3)
    feats = build_minutes_features(con, use_game_context=True)
    cfg = MinutesModelConfig(use_game_context=True)
    dists_full = predict_minutes(feats, cfg)

    one_row = feats.filter(pl.col("game_id") == "g2")
    dist_isolated = predict_minutes(one_row, cfg)[0]
    idx = feats.select("game_id").to_series().to_list().index("g2")
    assert dist_isolated.params() == dists_full[idx].params()


# --------------------------------------------------------------------------
# 3. Minutes-model evaluation: DNP log loss + minutes MAE/bias.
# --------------------------------------------------------------------------


def test_evaluate_minutes_model_scores_dnp_and_played_rows_separately() -> None:
    # Row 0: predicted to always play (p_play=1.0), predicted 30 min; actual
    # played 25 min -> scored on the minutes-MAE head, not DNP.
    # Row 1: predicted never to play (p_play=0.0); actual DNP (0 min) ->
    # perfect DNP prediction, excluded from the minutes-MAE head entirely.
    # Row 2: predicted to play (p_play=0.9) but actually DNP -> contributes
    # to DNP log loss only (non-zero, imperfect), not to minutes-MAE.
    dists = [
        MinutesHurdleDist(p_play=1.0 - 1e-9, mu=30.0, sigma=5.0),
        MinutesHurdleDist(p_play=1e-9, mu=20.0, sigma=5.0),
        MinutesHurdleDist(p_play=0.9, mu=25.0, sigma=5.0),
    ]
    actual = np.array([25.0, 0.0, 0.0])

    ev = evaluate_minutes_model(dists, actual, used_game_context=True)

    assert isinstance(ev, MinutesEval)
    assert ev.n == 3
    assert ev.dnp_n == 3
    assert ev.minutes_n == 1  # only row 0 actually played
    assert math.isfinite(ev.dnp_log_loss)
    assert math.isfinite(ev.minutes_mae)
    assert math.isfinite(ev.minutes_bias)
    # Row 0 is the only one scored on minutes: pred 30, actual 25.
    assert ev.minutes_mae == pytest.approx(5.0)
    assert ev.minutes_bias == pytest.approx(5.0)
    # DNP log loss should be small (two of three predictions near-perfect)
    # but strictly positive (row 2's imperfect 0.9 prediction).
    assert 0.0 < ev.dnp_log_loss < 1.0
    assert ev.used_game_context is True


def test_evaluate_minutes_model_all_dnp_leaves_minutes_metrics_nan() -> None:
    dists = [MinutesHurdleDist(p_play=0.1, mu=10.0, sigma=3.0) for _ in range(3)]
    actual = np.array([0.0, 0.0, 0.0])
    ev = evaluate_minutes_model(dists, actual)
    assert ev.minutes_n == 0
    assert math.isnan(ev.minutes_mae)
    assert math.isnan(ev.minutes_bias)
    assert math.isfinite(ev.dnp_log_loss)


def test_fetch_actual_minutes_coalesces_dnp_nulls_to_zero(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _insert_game(con, "g1", "2023-10-24")
    _insert_player_row(con, "g1", 101, None)  # DNP: NULL minutes
    _insert_game(con, "g2", "2023-10-26")
    _insert_player_row(con, "g2", 101, 18.0)

    actual = fetch_actual_minutes(con).sort("game_id")
    values = actual.select("minutes").to_series().to_list()
    assert values == [0.0, 18.0]
    assert actual.select("minutes").null_count().item() == 0


# --------------------------------------------------------------------------
# 4. Determinism.
# --------------------------------------------------------------------------


def test_minutes_eval_and_predictions_are_deterministic(con: duckdb.DuckDBPyConnection) -> None:
    _seed_games(con, n=5)
    cfg = MinutesModelConfig(use_game_context=True)

    feats_a = build_minutes_features(con, use_game_context=True)
    dists_a = predict_minutes(feats_a, cfg)
    actual_a = fetch_actual_minutes(con)
    arr_a = (
        feats_a.select(["game_id", "player_id"])
        .join(actual_a, on=["game_id", "player_id"], how="left")
        .select("minutes")
        .to_series()
        .fill_null(0.0)
        .to_numpy()
    )
    ev_a = evaluate_minutes_model(dists_a, arr_a)

    feats_b = build_minutes_features(con, use_game_context=True)
    dists_b = predict_minutes(feats_b, cfg)
    actual_b = fetch_actual_minutes(con)
    arr_b = (
        feats_b.select(["game_id", "player_id"])
        .join(actual_b, on=["game_id", "player_id"], how="left")
        .select("minutes")
        .to_series()
        .fill_null(0.0)
        .to_numpy()
    )
    ev_b = evaluate_minutes_model(dists_b, arr_b)

    assert [d.params() for d in dists_a] == [d.params() for d in dists_b]
    assert ev_a.dnp_log_loss == ev_b.dnp_log_loss
    assert ev_a.minutes_mae == ev_b.minutes_mae or (
        math.isnan(ev_a.minutes_mae) and math.isnan(ev_b.minutes_mae)
    )
    assert ev_a.minutes_bias == ev_b.minutes_bias or (
        math.isnan(ev_a.minutes_bias) and math.isnan(ev_b.minutes_bias)
    )
