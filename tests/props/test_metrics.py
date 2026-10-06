"""Performance-fix correctness guard for nba/props/metrics.py.

``crps_array`` and the threshold-log-loss helpers now use the vectorized
``batch_ppf``/``batch_p_ge`` helpers (nba.props.distributions) instead of one
scipy call per (row, quantile) or (row, threshold) pair. These tests prove
the vectorized path returns the same numbers as the original per-row
Python-loop implementation (``crps_from_dist`` applied row by row), not just
that it runs faster.
"""

from __future__ import annotations

import numpy as np

from nba.props.distributions import GammaDist, NegBinDist, NormalDist, ZeroInflatedNegBinDist
from nba.props.metrics import (
    avg_threshold_log_loss_per_game,
    crps_array,
    crps_from_dist,
    threshold_log_loss_and_calibration,
)


def test_crps_array_matches_scalar_loop_normal() -> None:
    dists = [NormalDist(mean_=m, std=s) for m, s in [(10.0, 3.0), (22.5, 7.1), (0.5, 0.2)]]
    y = np.array([9.0, 25.0, 1.0])
    vectorized = crps_array(dists, y)
    reference = np.array([crps_from_dist(d, yi) for d, yi in zip(dists, y, strict=True)])
    assert np.allclose(vectorized, reference, atol=1e-6)


def test_crps_array_matches_scalar_loop_gamma() -> None:
    dists = [GammaDist(shape=sh, scale=sc) for sh, sc in [(2.0, 1.5), (9.0, 0.4), (0.3, 3.0)]]
    y = np.array([3.0, 4.0, 0.5])
    vectorized = crps_array(dists, y)
    reference = np.array([crps_from_dist(d, yi) for d, yi in zip(dists, y, strict=True)])
    assert np.allclose(vectorized, reference, atol=1e-6)


def test_crps_array_matches_scalar_loop_negbin_and_zinb() -> None:
    dists = [
        NegBinDist(mu=5.0, alpha=0.3),
        ZeroInflatedNegBinDist(pi=0.2, base=NegBinDist(mu=4.0, alpha=0.4)),
    ]
    y = np.array([4.0, 0.0])
    vectorized = crps_array(dists, y)
    reference = np.array([crps_from_dist(d, yi) for d, yi in zip(dists, y, strict=True)])
    assert np.allclose(vectorized, reference, atol=1e-6)


def test_crps_array_empty_is_empty() -> None:
    assert len(crps_array([], np.array([]))) == 0


def test_threshold_log_loss_and_calibration_matches_scalar_p_ge() -> None:
    dists = [NormalDist(mean_=m, std=s) for m, s in [(10.0, 3.0), (22.5, 7.1), (18.0, 5.0)]]
    y = np.array([9.0, 25.0, 15.0])
    thresholds = [10, 20]
    result = threshold_log_loss_and_calibration(dists, y, thresholds)
    for th_result, th in zip(result, thresholds, strict=True):
        p_expected = np.clip(np.array([d.p_ge(float(th)) for d in dists]), 1e-15, 1 - 1e-15)
        indicator = (y >= th).astype(float)
        ll_expected = float(
            np.mean(-(indicator * np.log(p_expected) + (1 - indicator) * np.log(1 - p_expected)))
        )
        assert abs(th_result.log_loss - ll_expected) < 1e-9


def test_avg_threshold_log_loss_per_game_matches_scalar_p_ge() -> None:
    dists = [NormalDist(mean_=m, std=s) for m, s in [(10.0, 3.0), (22.5, 7.1), (18.0, 5.0)]]
    y = np.array([9.0, 25.0, 15.0])
    thresholds = [10, 20]
    per_game = avg_threshold_log_loss_per_game(dists, y, thresholds)
    eps = 1e-15
    losses = np.zeros(3)
    for th in thresholds:
        p = np.clip(np.array([d.p_ge(float(th)) for d in dists]), eps, 1 - eps)
        indicator = (y >= th).astype(float)
        losses += -(indicator * np.log(p) + (1 - indicator) * np.log(1 - p))
    expected = losses / len(thresholds)
    assert np.allclose(per_game, expected, atol=1e-9)
