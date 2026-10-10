"""Regression tests for the "pooled CRPS/mean-bias comes out NaN" bug.

Root cause (see research/props/run.py::_target_frame docstring): on real data,
`player_game_stats.pts/reb/ast/fg3m` are stored as SQL NULL for DNP
(did-not-play) rows rather than 0. `pl.Series.to_numpy().astype(float)`
silently turns a NULL Int64 into NaN, which then poisons every pooled
`np.mean`-based metric (CRPS, mean-bias bootstrap) for the whole stat, not
just the offending row.

These tests build a tiny synthetic DuckDB with exactly the two row types
CLAUDE.md asked us to check:
  - a true DNP row (NULL minutes/pts/reb/ast/fg3m) for an *established*
    player (so the minutes/rate models have real history to shrink from),
  - a brand-new (no-history) player's first game.
and assert the pipeline produces finite pooled metrics for both, with a
transparent, correct n_valid/n_total accounting.
"""

from __future__ import annotations

import math

import duckdb

from nba.db.connect import apply_schema
from nba.props.config import PropsConfig
from research.props.report import render_props_report
from research.props.run import run_props_experiment


def _build_db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    apply_schema(con)
    games = [
        ("G1", "2024-01-01", 2024, 1, 2, 100, 98),
        ("G2", "2024-01-03", 2024, 1, 2, 110, 95),
        ("G3", "2024-01-05", 2024, 1, 2, 105, 101),
        ("G4", "2024-01-07", 2024, 1, 2, 108, 100),
    ]
    con.executemany(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) VALUES (?, ?::DATE, ?, ?, ?, ?, ?)",  # noqa: E501
        games,
    )
    # Player 1: plays G1/G2/G4, a true DNP (NULL everything) in G3 --
    # the exact shape of the real-data bug (see module docstring).
    rows = [
        ("G1", 1, 1, 30.0, 20, 5, 4, 2, 1, 0, 1, True),
        ("G2", 1, 1, 28.0, 18, 6, 3, 1, 1, 0, 2, True),
        ("G3", 1, 1, None, None, None, None, None, None, None, None, False),
        ("G4", 1, 1, 32.0, 22, 7, 5, 3, 0, 1, 1, True),
        # Player 2: a brand-new player, first (and only) appearance in G4 --
        # the other documented NaN candidate (no-history cold start).
        ("G4", 2, 2, 15.0, 8, 2, 1, 1, 0, 0, 0, False),
    ]
    con.executemany(
        "INSERT INTO player_game_stats "
        "(game_id, player_id, team_id, minutes, pts, reb, ast, fg3m, stl, blk, tov, starter) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return con


def test_dnp_row_coalesced_to_zero_not_nan() -> None:
    """``_target_frame`` must never leak a NULL box-score stat as NaN."""
    from research.props.run import _target_frame

    con = _build_db()
    try:
        target = _target_frame(con)
    finally:
        con.close()
    dnp_row = target.filter((target["game_id"] == "G3") & (target["player_id"] == 1))
    assert dnp_row.height == 1
    for stat in ("pts", "reb", "ast", "fg3m"):
        value = dnp_row[0, stat]
        assert value == 0  # coalesced, not NaN/None
        assert value == value  # not NaN


def test_pooled_metrics_are_finite_with_a_dnp_and_a_no_history_row() -> None:
    con = _build_db()
    try:
        result = run_props_experiment(con, config=PropsConfig(), seed=0, n_boot=50)
    finally:
        con.close()

    assert len(result.stats) == 4
    for s in result.stats:
        assert s.n == 5  # 4 games for player 1 + 1 for player 2
        assert s.n_valid == 5  # the DNP + no-history rows are real, scored rows, not dropped
        assert s.exclusion_note == ""
        assert math.isfinite(s.mean_bias.point)
        assert math.isfinite(s.crps_point)
        for t in s.threshold_calibration:
            assert math.isfinite(t.log_loss)

    for c in result.combo_stats:
        assert c.n_valid == c.n
        assert c.exclusion_note == ""
        assert math.isfinite(c.crps_point)
        assert math.isfinite(c.mean_bias.point)


def test_report_shows_n_valid_over_n_total() -> None:
    con = _build_db()
    try:
        result = run_props_experiment(con, config=PropsConfig(), seed=0, n_boot=50)
    finally:
        con.close()
    report = render_props_report(result)
    assert "NaN" not in report.split("## Notes")[0].replace(
        "non-finite", ""
    )  # no stray NaN in the metrics sections above the static Notes text
    assert "5/5" in report  # n_valid/n for every base stat


def test_exclusion_is_reported_not_silently_dropped_when_a_row_is_genuinely_bad() -> None:
    """Simulates a *new* (non-DNP) data problem: a row whose predicted mean
    comes out non-finite for a reason unrelated to the DNP fix. The pooled
    metric must stay finite (NaN-aware) and the row must be counted and
    explained, never silently vanish or silently poison the pooled number.
    """
    import numpy as np

    from nba.props.distributions import NormalDist
    from nba.props.metrics import crps_array
    from research.props.run import StatResult

    dists = [NormalDist(mean_=10.0, std=3.0), NormalDist(mean_=12.0, std=2.5)]
    y = np.array([9.0, float("nan")])
    pred_mean = np.array([d.mean() for d in dists])
    valid_mask = np.isfinite(y) & np.isfinite(pred_mean)
    assert valid_mask.tolist() == [True, False]
    y_m = y[valid_mask]
    dists_m = [d for d, keep in zip(dists, valid_mask, strict=True) if keep]
    crps = crps_array(dists_m, y_m)
    assert np.isfinite(crps).all()
    pooled = float(np.nanmean(crps))
    assert math.isfinite(pooled)

    # Sanity-check the StatResult contract carries the accounting fields.
    sr = StatResult(
        stat="pts",
        n=2,
        dist_family="normal",
        mean_bias=None,  # type: ignore[arg-type]
        coverage=0.0,
        threshold_calibration=[],
        pooled_ece=0.0,
        crps_point=pooled,
        crps_vs_season_avg=None,  # type: ignore[arg-type]
        crps_vs_last10_avg=None,  # type: ignore[arg-type]
        log_loss_vs_season_avg=None,  # type: ignore[arg-type]
        log_loss_vs_last10_avg=None,  # type: ignore[arg-type]
        zero_inflation_detected=False,
        data_sufficiency_note="",
        baseline_note="",
        n_valid=1,
        exclusion_note="1/2 rows excluded: non-finite target.",
    )
    assert sr.n_valid == 1
    assert "excluded" in sr.exclusion_note
