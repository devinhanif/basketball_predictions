"""3-method contract (``predict``/``get_metrics``/``get_config``) tests for
every rung, plus determinism given a fixed seed.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from nba.models.rung0_baselines import ClosingLineStub, EloBaseline, HomeCourtBaseline
from nba.models.rung1_logistic import LogisticRung
from nba.models.rung2_gbm import LightGBMRung


def _toy_train() -> tuple[pl.DataFrame, np.ndarray]:
    df = pl.DataFrame(
        {
            "game_id": ["g1", "g2", "g3", "g4"],
            "game_date": ["2023-10-24", "2023-10-25", "2023-10-26", "2023-10-27"],
            "home_team": [1, 2, 1, 3],
            "away_team": [2, 1, 3, 1],
            "win_pct_diff": [0.1, -0.2, 0.3, -0.1],
            "pts_for_diff": [2.0, -3.0, 5.0, -1.0],
            "pts_allowed_diff": [-1.0, 2.0, -2.0, 1.0],
            "tov_diff": [0.5, -0.5, 1.0, -1.0],
            "pace_diff": [1.0, -1.0, 0.5, -0.5],
            "rest_diff": [0.0, 1.0, -1.0, 0.0],
            "travel_diff": [100.0, -50.0, 0.0, 200.0],
            "home_b2b": [False, True, False, False],
            "away_b2b": [False, False, True, False],
            "is_national_tv": [True, False, False, True],
            "conf_rank_diff": [1.0, -2.0, 0.0, 3.0],
            "win_pct_asof_diff": [0.1, -0.2, 0.3, -0.1],
            "home_in_playoff_pos": [True, False, False, True],
            "away_in_playoff_pos": [False, True, True, False],
            "home_in_playin": [False, False, True, False],
            "away_in_playin": [False, True, False, False],
            "tanking_incentive_diff": [0.0, 0.0, -0.3, 0.2],
            "home_avg_pts_for_prior": [110.0, 108.0, 112.0, 109.0],
            "home_avg_pts_allowed_prior": [108.0, 110.0, 107.0, 111.0],
            "home_pace_proxy_prior": [220.0, 218.0, 222.0, 219.0],
            "away_avg_pts_for_prior": [109.0, 110.0, 108.0, 112.0],
            "away_avg_pts_allowed_prior": [110.0, 108.0, 111.0, 107.0],
            "away_pace_proxy_prior": [219.0, 220.0, 218.0, 222.0],
            "home_games_played_prior": [10, 10, 11, 5],
            "away_games_played_prior": [10, 10, 5, 11],
        }
    ).with_columns(pl.col("game_date").str.to_date())
    y = np.array([1, 0, 1, 0])
    return df, y


@pytest.mark.parametrize(
    "factory",
    [
        lambda: HomeCourtBaseline(seed=7),
        lambda: EloBaseline(seed=7),
        lambda: LogisticRung(seed=7),
        lambda: LightGBMRung(seed=7),
    ],
)
def test_contract_methods_present_and_well_typed(factory) -> None:  # type: ignore[no-untyped-def]
    train_df, y = _toy_train()
    model = factory()
    model.fit(train_df, y)

    preds = model.predict(train_df)
    assert isinstance(preds, np.ndarray)
    assert preds.shape == (train_df.height,)
    assert np.all((preds >= 0.0) & (preds <= 1.0))

    metrics_before = model.get_metrics()
    assert metrics_before == {}
    model.record_metrics({"log_loss": 0.6})
    assert model.get_metrics() == {"log_loss": 0.6}

    config = model.get_config()
    assert isinstance(config, dict)
    assert "seed" in config
    assert config["seed"] == 7


@pytest.mark.parametrize(
    "factory",
    [
        lambda: HomeCourtBaseline(seed=123),
        lambda: EloBaseline(seed=123),
        lambda: LogisticRung(seed=123),
        lambda: LightGBMRung(seed=123),
    ],
)
def test_determinism_same_seed_same_data_same_predictions(factory) -> None:  # type: ignore[no-untyped-def]
    train_df, y = _toy_train()

    model_a = factory()
    model_a.fit(train_df, y)
    preds_a = model_a.predict(train_df)

    model_b = factory()
    model_b.fit(train_df, y)
    preds_b = model_b.predict(train_df)

    np.testing.assert_array_equal(preds_a, preds_b)


def test_closing_line_stub_does_not_fabricate_a_line() -> None:
    stub = ClosingLineStub(seed=0)
    train_df, y = _toy_train()
    stub.fit(train_df, y)
    with pytest.raises(NotImplementedError):
        stub.predict(train_df)
    assert stub.get_config()["status"] == "stub_not_implemented"
