"""MARKET_SHRINK harness pieces on synthetic data (no files, no network)."""

from __future__ import annotations

import numpy as np

from research.eval import market_shrink_eval as ms


def test_blend_limits() -> None:
    m = np.array([0.4, 0.6])
    pm = np.array([0.7, 0.2])
    assert np.allclose(ms.blend(m, pm, 0.0, 1.0), m)  # w=0 is the market
    assert np.allclose(ms.blend(m, pm, 1.0, 1.0), pm)  # w=1, T=1 is the model


def test_fit_recovers_market_when_model_is_noise() -> None:
    rng = np.random.default_rng(0)
    n = 6000
    m = rng.uniform(0.3, 0.7, n)
    o = (rng.random(n) < m).astype(float)
    pm = rng.uniform(0.05, 0.95, n)
    w, _ = ms.fit_wt(m, pm, o, (1.0,))
    assert w <= 0.1


def test_fit_uses_model_when_it_is_informative() -> None:
    rng = np.random.default_rng(1)
    n = 8000
    truth = rng.uniform(0.2, 0.8, n)
    o = (rng.random(n) < truth).astype(float)
    m = np.clip(0.5 + 0.3 * (truth - 0.5), 0.01, 0.99)  # a flattened market
    w, _ = ms.fit_wt(m, truth, o, (1.0,))
    assert w >= 0.8


def test_holm_monotone_and_walk_forward_never_sees_own_month() -> None:
    h = ms.holm({"a": 0.01, "b": 0.02, "c": 0.5})
    assert h["a"] <= h["b"] <= h["c"] and h["a"] == 0.03
    month = np.array(["2024-01"] * 2500 + ["2024-02"] * 100)
    rng = np.random.default_rng(2)
    m = rng.uniform(0.3, 0.7, month.size)
    scored, arms, log = ms.walk_forward(month, m, m, (rng.random(month.size) < m).astype(float))
    assert not scored[:2500].any() and scored[2500:].all()  # January has no prior fit window
    assert log[0]["skipped"] and not np.isnan(arms["B2"][2500:]).any()
