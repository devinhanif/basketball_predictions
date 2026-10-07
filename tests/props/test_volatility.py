"""Tests for the volatility-bucket slice (CLAUDE.md Phase 2 ask: "the
model's edge concentrates in high-volatility players").

Mirrors ``tests/props/test_minutes_no_leakage.py``'s fixture style: an
in-memory DuckDB built from the real schema via ``nba.db.connect.connect``,
never the live ``nba.duckdb`` at repo root.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pytest

from nba.db.connect import connect
from nba.props.config import PropsConfig, VolatilityBucketSpec, VolatilityConfig
from nba.props.report import render_props_report
from nba.props.run import run_props_experiment
from nba.props.volatility import (
    INSUFFICIENT_HISTORY_BUCKET,
    assign_buckets,
    build_volatility_features,
    evaluate_volatility_buckets,
    quantile_buckets,
)
from tests.fixtures.loader import build_fixture_db


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = connect(":memory:")
    yield c
    c.close()


def _insert_game(con: duckdb.DuckDBPyConnection, game_id: str, game_date: str) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) VALUES (?, ?, 2023, 1, 2, 100, 90)",
        [game_id, game_date],
    )


def _insert_player_row(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    player_id: int,
    pts: float,
    minutes: float = 30.0,
) -> None:
    con.execute(
        """
        INSERT INTO player_game_stats
            (game_id, player_id, team_id, minutes, pts, reb, ast, fg3m, stl, blk, tov, starter)
        VALUES (?, ?, 1, ?, ?, 5, 3, 1, 1, 0, 1, true)
        """,
        [game_id, player_id, minutes, pts],
    )


# ---------------------------------------------------------------------------
# No-leakage: planted future game
# ---------------------------------------------------------------------------


def test_volatility_unchanged_by_a_planted_future_game(con: duckdb.DuckDBPyConnection) -> None:
    """The CV for an earlier target game must not change when a wildly
    different future game is appended for the same player (same leakage
    discipline as the minutes model's no-leakage test)."""
    _insert_game(con, "g1", "2023-10-24")
    _insert_player_row(con, "g1", 101, pts=10.0)
    _insert_game(con, "g2", "2023-10-26")
    _insert_player_row(con, "g2", 101, pts=12.0)
    _insert_game(con, "g3", "2023-10-28")
    _insert_player_row(con, "g3", 101, pts=11.0)

    feats_before = build_volatility_features(con, "pts", lookback_games=10, min_games_for_cv=2)
    row_before = feats_before.filter(
        (feats_before["game_id"] == "g3") & (feats_before["player_id"] == 101)
    )
    cv_before = row_before["cv"][0]
    n_before = row_before["n_prior"][0]
    assert n_before == 2

    # Plant a wild future game (after g3) for the same player.
    _insert_game(con, "g4_future", "2023-11-05")
    _insert_player_row(con, "g4_future", 101, pts=999.0)

    feats_after = build_volatility_features(con, "pts", lookback_games=10, min_games_for_cv=2)
    row_after = feats_after.filter(
        (feats_after["game_id"] == "g3") & (feats_after["player_id"] == 101)
    )
    cv_after = row_after["cv"][0]
    n_after = row_after["n_prior"][0]

    assert n_after == n_before
    assert cv_after == pytest.approx(cv_before)


def test_volatility_minutes_variant_unchanged_by_planted_future_game(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _insert_game(con, "g1", "2023-10-24")
    _insert_player_row(con, "g1", 101, pts=10.0, minutes=20.0)
    _insert_game(con, "g2", "2023-10-26")
    _insert_player_row(con, "g2", 101, pts=12.0, minutes=25.0)
    _insert_game(con, "g3", "2023-10-28")
    _insert_player_row(con, "g3", 101, pts=11.0, minutes=22.0)

    feats_before = build_volatility_features(con, "minutes", lookback_games=10, min_games_for_cv=2)
    cv_before = feats_before.filter(feats_before["game_id"] == "g3")["cv"][0]

    _insert_game(con, "g4_future", "2023-11-05")
    _insert_player_row(con, "g4_future", 101, pts=0.0, minutes=0.0)

    feats_after = build_volatility_features(con, "minutes", lookback_games=10, min_games_for_cv=2)
    cv_after = feats_after.filter(feats_after["game_id"] == "g3")["cv"][0]

    assert cv_after == pytest.approx(cv_before)


# ---------------------------------------------------------------------------
# Bucketing correctness
# ---------------------------------------------------------------------------


def test_divide_by_zero_and_tiny_sample_are_guarded(con: duckdb.DuckDBPyConnection) -> None:
    """A constant-zero history (mean ~= 0) and a too-short history both
    yield an undefined (NaN) CV rather than +/-inf or a crash."""
    _insert_game(con, "g1", "2023-10-24")
    _insert_player_row(con, "g1", 201, pts=0.0)
    _insert_game(con, "g2", "2023-10-26")
    _insert_player_row(con, "g2", 201, pts=0.0)
    _insert_game(con, "g3", "2023-10-28")
    _insert_player_row(con, "g3", 201, pts=0.0)

    feats = build_volatility_features(con, "pts", lookback_games=10, min_games_for_cv=2)
    row = feats.filter(feats["game_id"] == "g3")
    assert row["n_prior"][0] == 2
    assert row["cv"][0] != row["cv"][0]  # NaN: mean ~= 0

    # Too few prior games (min_games_for_cv=5, only 0 available on g1).
    row_g1 = feats.filter(feats["game_id"] == "g1")
    assert row_g1["n_prior"][0] == 0
    assert row_g1["cv"][0] != row_g1["cv"][0]


def test_high_cv_player_lands_in_high_bucket_steady_player_in_low() -> None:
    buckets = [
        VolatilityBucketSpec(name="low", lo=0.0, hi=0.35),
        VolatilityBucketSpec(name="high", lo=0.35, hi=float("inf")),
    ]
    # Steady player: pts hover around 20 with tiny variation -> low CV.
    steady_cv = np.array([0.05])
    # Erratic player: pts swing wildly -> high CV.
    erratic_cv = np.array([1.2])
    unknown_cv = np.array([float("nan")])

    labels = assign_buckets(np.concatenate([steady_cv, erratic_cv, unknown_cv]), buckets)
    assert labels[0] == "low"
    assert labels[1] == "high"
    assert labels[2] == INSUFFICIENT_HISTORY_BUCKET


def test_quantile_buckets_median_split() -> None:
    cv = np.array([0.1, 0.2, 0.3, 0.8, 0.9, float("nan")])
    buckets = quantile_buckets(cv)
    labels = assign_buckets(cv, buckets)
    # Below-median values -> low, at/above-median -> high, NaN -> insufficient.
    assert labels[0] == "low"
    assert labels[-2] == "high"
    assert labels[-1] == INSUFFICIENT_HISTORY_BUCKET


def test_quantile_buckets_falls_back_with_too_few_finite_values() -> None:
    cv = np.array([float("nan"), float("nan"), 0.4])
    buckets = quantile_buckets(cv)
    # Falls back to the documented fixed-cutoff defaults.
    assert [b.name for b in buckets] == ["low", "high"]


def test_insufficient_bucket_n_produces_explicit_note_not_a_point_estimate() -> None:
    n = 5
    bucket_labels = np.array(["low"] * n)
    zeros = np.zeros(n)
    result = evaluate_volatility_buckets(
        stat="pts",
        cv_variant="stat",
        bucket_labels=bucket_labels,
        pred_mean=zeros,
        q10=zeros,
        q90=zeros,
        y=zeros,
        crps_model=zeros,
        crps_season=zeros,
        crps_last10=zeros,
        ll_model=zeros,
        ll_season=zeros,
        ll_last10=zeros,
        buckets=[VolatilityBucketSpec(name="low", lo=0.0, hi=0.35)],
        min_bucket_n=100,
        n_boot=50,
        seed=0,
    )
    low = next(r for r in result if r.bucket == "low")
    assert low.sufficient is False
    assert "insufficient data" in low.note
    assert low.mean_bias.point != low.mean_bias.point  # NaN point, not a misleading number


# ---------------------------------------------------------------------------
# End-to-end on the fixture
# ---------------------------------------------------------------------------


def test_volatility_slice_runs_end_to_end_on_fixture_and_is_graceful() -> None:
    con_fixture = build_fixture_db(":memory:")
    try:
        result = run_props_experiment(con_fixture, config=PropsConfig(), seed=3, n_boot=50)
    finally:
        con_fixture.close()

    assert len(result.stats) == 4
    for s in result.stats:
        assert s.volatility_buckets  # non-empty: slicing ran
        for b in s.volatility_buckets:
            # Fixture has exactly 1 game per player -> every row falls in
            # "insufficient_history" and every bucket is far below
            # min_bucket_n -> explicit note, no crash, no stray NaN leaking
            # into a point estimate that looks trustworthy.
            if not b.sufficient:
                assert "insufficient data" in b.note
            else:
                assert np.isfinite(b.mean_bias.point)

    report = render_props_report(result)
    assert "Volatility buckets" in report
    assert "insufficient data for a trustworthy CI" in report


def test_volatility_slice_disabled_via_config() -> None:
    con_fixture = build_fixture_db(":memory:")
    try:
        cfg = PropsConfig(volatility=VolatilityConfig(enabled=False))
        result = run_props_experiment(con_fixture, config=cfg, seed=0, n_boot=50)
    finally:
        con_fixture.close()

    for s in result.stats:
        assert s.volatility_buckets == []
    report = render_props_report(result)
    assert "disabled or no data" in report


def test_minutes_cv_variant_runs_end_to_end() -> None:
    con_fixture = build_fixture_db(":memory:")
    try:
        cfg = PropsConfig(volatility=VolatilityConfig(cv_variant="minutes"))
        result = run_props_experiment(con_fixture, config=cfg, seed=0, n_boot=50)
    finally:
        con_fixture.close()

    for s in result.stats:
        for b in s.volatility_buckets:
            assert b.cv_variant == "minutes"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_volatility_buckets_are_deterministic_given_seed() -> None:
    def _run() -> object:
        con_fixture = build_fixture_db(":memory:")
        try:
            return run_props_experiment(con_fixture, config=PropsConfig(), seed=11, n_boot=80)
        finally:
            con_fixture.close()

    result_a = _run()
    result_b = _run()
    for sa, sb in zip(result_a.stats, result_b.stats, strict=True):
        assert len(sa.volatility_buckets) == len(sb.volatility_buckets)
        for ba, bb in zip(sa.volatility_buckets, sb.volatility_buckets, strict=True):
            assert ba.n == bb.n
            assert ba.mean_bias.point == bb.mean_bias.point or (
                ba.mean_bias.point != ba.mean_bias.point
                and bb.mean_bias.point != bb.mean_bias.point
            )
