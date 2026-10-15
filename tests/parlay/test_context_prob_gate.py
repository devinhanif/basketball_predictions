from __future__ import annotations

import numpy as np
import polars as pl
import pytest
from scipy.stats import multivariate_normal, norm

from nba.parlay import context_prob_gate as G


def test_orthant_matches_bivariate_normal() -> None:
    rng = np.random.default_rng(0)
    p1 = rng.uniform(0.15, 0.85, 8)
    p2 = rng.uniform(0.15, 0.85, 8)
    rho = rng.uniform(-0.5, 0.5, 8)
    got = G.orthant_prob(p1, p2, rho)
    for i in range(8):
        a, b = norm.ppf(1 - p1[i]), norm.ppf(1 - p2[i])
        cov = [[1.0, rho[i]], [rho[i], 1.0]]
        # P(Z1 > a, Z2 > b) = P(-Z1 < -a, -Z2 < -b) (same correlation)
        want = multivariate_normal(mean=[0, 0], cov=cov).cdf([-a, -b])
        assert got[i] == pytest.approx(want, abs=1e-6)


def test_orthant_independence_and_bounds() -> None:
    p1 = np.array([0.3, 0.5])
    p2 = np.array([0.4, 0.6])
    assert G.orthant_prob(p1, p2, 0.0) == pytest.approx(p1 * p2)
    hi = G.orthant_prob(p1, p2, 0.6)
    assert (hi > p1 * p2).all() and (hi <= np.minimum(p1, p2)).all()


def test_delta_moments_sign_and_zero() -> None:
    pb = np.array([0.2, 0.3])
    m0, v0 = G.paired_delta_moments(pb, pb)
    assert np.allclose(m0, 0.0) and np.allclose(v0, 0.0)
    pc = np.array([0.25, 0.27])
    m, v = G.paired_delta_moments(pb, pc)
    assert (m < 0).all() and (
        v > 0
    ).all()  # truth is pc: the context model is better in expectation


def test_delta_moments_match_simulation() -> None:
    pb, pc = np.array([0.2]), np.array([0.26])
    m, v = G.paired_delta_moments(pb, pc)
    rng = np.random.default_rng(1)
    y = rng.random(400_000) < pc[0]
    d = -(y * np.log(pc[0] / pb[0]) + (~y) * np.log((1 - pc[0]) / (1 - pb[0])))
    assert d.mean() == pytest.approx(m[0], abs=1e-3)
    assert d.var() == pytest.approx(v[0], rel=0.03)


def test_power_bounds_ordering() -> None:
    lo, up = G.power_bounds(mean_sum=-0.5, var_sum=50.0, n_units=10_000, n_games=500, alpha=0.05)
    assert 0.0 <= lo <= up <= 1.0
    lo2, up2 = G.power_bounds(-2.0, 200.0, 40_000, 500, 0.05)  # 4x units, same per-unit moments
    assert up2 > up and lo2 == pytest.approx(lo)
    assert G.power_bounds(0.0, 0.0, 0, 0, 0.05) == (0.0, 0.0)


def test_seasons_guard_refuses_holdout() -> None:
    G.assert_seasons([2022, 2023, 2024])
    with pytest.raises(ValueError):
        G.assert_seasons([2024, 2025])


def test_labels() -> None:
    vac = np.array([10.0, 30.0, 60.0, 50.0])
    has = np.array([True, True, True, False])
    t24, t48 = G.tv_labels(vac, has)
    assert t24.tolist() == [0, 1, 1, -1]
    assert t48.tolist() == [0, -1, 1, -1]
    a = np.array([1, 0, -1, 0], dtype=np.int8)
    b = np.array([0, 0, 0, -1], dtype=np.int8)
    assert G.lab_lead(a, b).tolist() == [1, 0, -1, -1]
    assert G.lab_vac(
        np.array([1, 0], dtype=np.int8), np.array([0, 0], dtype=np.int8), "opponent"
    ).tolist() == [1, 0]
    assert G.lab_vac(
        np.array([1, 0], dtype=np.int8), np.array([0, 0], dtype=np.int8), "teammate"
    ).tolist() == [-1, 0]


def test_cell_row_counts_and_power_on_synthetic_units() -> None:
    n = 4000
    rng = np.random.default_rng(2)
    units = pl.DataFrame(
        {
            "season": [2024] * n,
            "game_id": [f"g{i // 8}" for i in range(n)],
            "p_a": rng.uniform(0.3, 0.6, n),
            "p_b": rng.uniform(0.3, 0.6, n),
            "lead_out": rng.integers(0, 2, n).astype(np.int8),
        }
    )
    inv = {"all": 0.3, "cells": {"lead_out": (0.4, 0.28, 0.12, True)}}
    row = G.cell_row("same_player:pts-ast", "lead_out", units, inv, (2024,))
    assert row["n_ctx"] + row["n_ref"] == n
    assert row["games_ctx"] <= 500
    assert row["mean_d_ctx_units_scale1"] < 0
    assert row["power_lo_scale1_a05"] <= row["power_up_scale1_a05"]
    assert row["power_up_scale0.5_a05"] <= row["power_up_scale1_a05"]
