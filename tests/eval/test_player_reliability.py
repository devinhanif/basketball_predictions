"""Unit tests for the descriptive player-reliability table (synthetic data only)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from nba.eval import player_reliability as pr


def _played(vals: list[int], pid: int = 1) -> pl.DataFrame:
    d0 = dt.date(2024, 1, 1)
    return pl.DataFrame(
        {
            "player_id": [pid] * len(vals),
            "game_id": [f"00224{i:05d}" for i in range(len(vals))],
            "game_date": [d0 + dt.timedelta(days=2 * i) for i in range(len(vals))],
            "pts": vals,
        }
    )


def test_weighted_pmf_quantiles_basic() -> None:
    vals = np.array([[0.0, 10.0, 20.0, 0.0]])
    w = np.array([[1.0, 1.0, 1.0, 0.0]])
    q = pr.weighted_pmf_quantiles(vals, w, np.array([0.1, 0.5, 0.9]))
    assert q.tolist() == [[0.0, 10.0, 20.0]]
    assert np.isnan(pr.weighted_pmf_quantiles(vals, w * 0)).all()


def test_baseline_is_asof_planted_future() -> None:
    hist = _played([5, 6, 7, 8, 9, 10, 11, 12])
    rows = hist.tail(1)
    q1, n1 = pr.baseline_quantiles(hist, rows, "pts")
    assert n1[0] == 7
    # the row's own value is not in its baseline
    own = rows["game_id"][0]
    changed = hist.with_columns(
        pl.when(pl.col("game_id") == own).then(99).otherwise(pl.col("pts")).alias("pts")
    )
    q2, _ = pr.baseline_quantiles(changed, rows, "pts")
    assert np.array_equal(q1, q2)
    # a later (future) game is ignored
    later = _played([500]).with_columns(
        pl.lit(dt.date(2025, 1, 1)).alias("game_date"), pl.lit("0022499999").alias("game_id")
    )
    q3, _ = pr.baseline_quantiles(pl.concat([hist, later]), rows, "pts")
    assert np.array_equal(q1, q3)


def test_interval_coverage_naive() -> None:
    y = np.array([0.0, 5.0, 5.0, 9.0])
    lo = np.array([0.0, 5.0, 5.5, 6.0])
    hi = np.array([3.4, 5.2, 7.0, 9.0])
    assert pr.interval_coverage(y, lo, hi).tolist() == [1.0, 1.0, 0.0, 1.0]


def test_eb_shrink_pulls_noisy_cells_to_mean() -> None:
    rng = np.random.default_rng(0)
    var = np.where(np.arange(200) < 100, 0.0004, 0.04)
    true = rng.normal(0.1, 0.02, 200)
    est = true + rng.normal(0, np.sqrt(var))
    shr, mu, tau2 = pr.eb_shrink(est, var, np.ones(200, dtype=bool))
    assert tau2 > 0
    assert np.abs(shr[100:] - mu).mean() < np.abs(est[100:] - mu).mean()
    assert np.abs(shr[:100] - est[:100]).mean() < np.abs(shr[100:] - est[100:]).mean()


def test_tier_rule() -> None:
    n = np.array([20, 40, 60, 60, 60, 60])
    skill = np.array([0.5, 0.5, 0.5, 0.5, 0.0, 0.5])
    cov = np.array([0.8, 0.8, 0.8, 0.88, 0.8, 0.65])
    t = pr.assign_tier(n, skill, cov, 0.1, 0.01)
    assert t.tolist() == ["low", "medium", "high", "medium", "medium", "low"]
    # no between-player spread -> nobody can be high
    assert "high" not in pr.assign_tier(n, skill, cov, 0.1, 0.0)


def test_kappa_perfect() -> None:
    a = np.array(["low", "medium", "high", "low"])
    assert pr.kappa(a, a) == 1.0
