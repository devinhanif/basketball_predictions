from __future__ import annotations

import numpy as np

from nba.ride import winprob as wp


def test_clock_and_seconds_left() -> None:
    assert wp.parse_clock("PT05M12.50S") == 312.5
    assert wp.parse_clock("5:51") == 351.0
    assert wp.parse_clock("22.2") == 22.2
    assert wp.seconds_left(4, 30.0) == 30.0
    assert wp.seconds_left(1, 720.0) == 2880.0
    assert wp.seconds_left(5, 200.0) == 200.0  # overtime: that period only


def test_fit_recovers_signal_and_is_monotone() -> None:
    rng = np.random.default_rng(1)
    m = rng.integers(-30, 31, 20000).astype(float)
    t = rng.uniform(1, 2880, 20000)
    true = wp.sigmoid(0.9 * m / np.sqrt(t / 60 + 1))
    y = (rng.uniform(size=20000) < true).astype(float)
    model = wp.fit_logistic(wp.features(m, t), y)
    p_small, p_big = model.p_home(3, 600), model.p_home(15, 600)
    assert 0.5 < p_small < p_big
    assert model.p_home(10, 60) > model.p_home(10, 1200)  # same lead, less time: safer
    assert abs(model.p_home(-12, 300) + model.p_home(12, 300) - 1) < 0.1


def test_shipped_model_late_blowout_and_close_game() -> None:
    model = wp.WinProbModel.load()
    assert model.p_home(25, 300) > 0.98
    assert 0.4 < model.p_home(0, 1440) < 0.7
    assert model.p_home(-25, 300) < 0.02


def test_leader_and_wilson() -> None:
    assert wp.leader_prob(0.3) == ("away", 0.7)
    lo, hi = wp.wilson(3, 100)
    assert 0.0 < lo < 0.03 < hi < 0.1
