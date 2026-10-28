"""RAPM_V2 gate math (label-free power and movement ceiling)."""

from __future__ import annotations

import numpy as np
import pytest

from nba.eval.rapm_v2_gate import kl_ceiling, power_measured_sd, power_true_floor, swap_ceiling


def test_self_consistent_power_matches_t518() -> None:
    # T518 G5: 0.208 at -0.002, n 1,318; pooled 2019-24 about 0.79
    assert power_true_floor(1318, 0.002)["power"] == pytest.approx(0.208, abs=0.002)
    assert power_true_floor(7595, 0.002)["power"] == pytest.approx(0.787, abs=0.005)


def test_measured_sd_power_matches_rapm_retest() -> None:
    # RAPM_RETEST table: pooled 2022-24, SD 0.111, P(detect -0.002) 0.20, -0.004 0.62
    assert power_measured_sd(3951, 0.002, 0.1110)["power"] == pytest.approx(0.20, abs=0.01)
    assert power_measured_sd(3951, 0.004, 0.1110)["power"] == pytest.approx(0.62, abs=0.01)


def test_kl_zero_when_identical_and_ceiling_scales() -> None:
    p = np.array([0.3, 0.5, 0.7])
    c = kl_ceiling(p, p)
    assert c["K"] == pytest.approx(0.0) and c["rms_logit_shift"] == pytest.approx(0.0)
    assert swap_ceiling(0.01, 1.0) == 0.0
    assert swap_ceiling(0.01, 0.5) == pytest.approx(0.01)


def test_kl_small_shift_approximation() -> None:
    rng = np.random.default_rng(0)
    pa = rng.uniform(0.2, 0.8, 5000)
    d = 0.1
    pb = 1 / (1 + np.exp(-(np.log(pa / (1 - pa)) + d)))
    k = kl_ceiling(pb, pa)["K"]
    approx = float(np.mean(pb * (1 - pb)) * d**2 / 2)
    assert k == pytest.approx(approx, rel=0.05)
