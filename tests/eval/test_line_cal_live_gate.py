"""LINE_CAL_LIVE gate helpers (label-free, synthetic only; no database)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from research.eval import line_cal_live_gate as g


def test_side_is_the_models_own_probability_side() -> None:
    p = np.array([0.2, 0.49, 0.5, 0.8])
    assert g.side_under(p).tolist() == [True, True, False, False]


def test_minutes_sd_needs_four_games() -> None:
    assert g.minutes_sd([30.0, 31.0, 29.0]) is None
    assert g.minutes_sd([30.0, 30.0, 30.0, 30.0]) == 0.0
    assert g.minutes_sd([20.0, 30.0, 20.0, 30.0]) == pytest.approx(5.7735, abs=1e-4)


def test_clustered_se_matches_iid_when_one_row_per_game() -> None:
    rng = np.random.default_rng(1)
    d = rng.normal(size=400)
    gi = np.arange(400)
    se = g.clustered_se(d, gi, 400)
    assert se == pytest.approx(np.std(d, ddof=0) / np.sqrt(400), rel=1e-9)


def test_power_has_no_gain_when_raw_equals_cal() -> None:
    rng = np.random.default_rng(2)
    p = rng.uniform(0.3, 0.7, 3000)
    games = np.repeat(np.arange(100), 30)
    out = g.synthetic_power(p, p.copy(), games, lambdas=(0.0,), games_grid=(100,), n_synth=5)
    cell = out["lam_0.0"]["all"]
    assert cell["expected"] == 0.0
    assert cell["by_games"]["100"]["p_pass_primary"] < 0.01


def test_power_detects_a_large_planted_gain() -> None:
    rng = np.random.default_rng(3)
    p_cal = rng.uniform(0.4, 0.6, 6000)
    p_raw = np.clip(p_cal - 0.2, 0.01, 0.99)  # badly biased raw
    games = np.repeat(np.arange(200), 30)
    out = g.synthetic_power(p_raw, p_cal, games, lambdas=(0.0,), games_grid=(200,), n_synth=5)
    cell = out["lam_0.0"]["all"]
    assert cell["expected"] < -0.03
    assert cell["by_games"]["200"]["p_pass_primary"] > 0.99


def test_window_refuses_dates_outside_season_2026(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        g.load_legs(tmp_path, date(2025, 10, 21), date(2026, 1, 1), "pinnacle")
