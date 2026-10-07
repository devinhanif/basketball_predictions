"""3-method contract + determinism tests for ``nba.models.rung3_sim.PossessionSimRung``."""

from __future__ import annotations

import math

import numpy as np
import polars as pl

from nba.models.rung3_sim import PossessionSimRung

LEAGUE_AVG = 1.146


def _toy_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": ["g1", "g2", "g3"],
            "home_off_rtg_prior": [1.20, 1.10, 1.10],
            "home_def_rtg_prior": [1.10, 1.10, 1.10],
            "home_pace_prior": [100.0, 100.0, 100.0],
            "away_off_rtg_prior": [1.10, 1.10, 1.20],
            "away_def_rtg_prior": [1.10, 1.10, 1.10],
            "away_pace_prior": [100.0, 100.0, 100.0],
            "league_avg_ppp_asof": [LEAGUE_AVG, LEAGUE_AVG, LEAGUE_AVG],
        }
    )


def test_contract_methods_present_and_well_typed() -> None:
    df = _toy_df()
    y = np.array([1, 0, 1])
    model = PossessionSimRung(seed=7, n_sims=300)
    model.fit(df, y)

    preds = model.predict(df)
    assert isinstance(preds, np.ndarray)
    assert preds.shape == (df.height,)
    assert np.all((preds >= 0.0) & (preds <= 1.0))

    metrics_before = model.get_metrics()
    assert metrics_before == {}
    model.record_metrics({"log_loss": 0.6})
    assert model.get_metrics() == {"log_loss": 0.6}

    config = model.get_config()
    assert isinstance(config, dict)
    assert config["seed"] == 7
    assert config["rung"] == 3


def test_determinism_same_seed_same_data_same_predictions() -> None:
    df = _toy_df()
    y = np.array([1, 0, 1])

    model_a = PossessionSimRung(seed=123, n_sims=300)
    model_a.fit(df, y)
    preds_a = model_a.predict(df)

    model_b = PossessionSimRung(seed=123, n_sims=300)
    model_b.fit(df, y)
    preds_b = model_b.predict(df)

    np.testing.assert_array_equal(preds_a, preds_b)


def test_win_prob_monotonic_in_rating_gap_through_the_rung() -> None:
    df = pl.DataFrame(
        {
            "game_id": ["weak", "average", "strong"],
            "home_off_rtg_prior": [1.00, 1.10, 1.25],
            "home_def_rtg_prior": [1.10, 1.10, 1.10],
            "home_pace_prior": [100.0, 100.0, 100.0],
            "away_off_rtg_prior": [1.10, 1.10, 1.10],
            "away_def_rtg_prior": [1.10, 1.10, 1.10],
            "away_pace_prior": [100.0, 100.0, 100.0],
            "league_avg_ppp_asof": [LEAGUE_AVG] * 3,
        }
    )
    model = PossessionSimRung(seed=7, n_sims=3000)
    model.fit(df, np.array([0, 0, 1]))
    preds = model.predict(df)
    assert preds[0] < preds[1] < preds[2]


def test_simulate_full_exposes_margin_and_total() -> None:
    df = _toy_df()
    model = PossessionSimRung(seed=1, n_sims=300)
    model.fit(df, np.array([1, 0, 1]))
    results = model.simulate_full(df)
    assert len(results) == 3
    for r in results:
        assert math.isfinite(r.margin_mean)
        assert math.isfinite(r.margin_sd)
        assert math.isfinite(r.total_mean)
        assert r.total_sd >= 0.0


def test_predict_handles_missing_possession_columns_gracefully() -> None:
    """A bare matchup DataFrame (no possession-feature join) must not crash."""
    df = pl.DataFrame({"game_id": ["g1", "g2"]})
    model = PossessionSimRung(seed=0, n_sims=100)
    model.fit(df, np.array([1, 0]))
    preds = model.predict(df)
    assert preds.shape == (2,)
    assert np.all((preds >= 0.0) & (preds <= 1.0))
