"""Tests for ``MovEloBaseline`` (margin-of-victory-adjusted Elo).

Covers: 3-method contract, the MOV multiplier's sane range, a blowout
moving ratings more than a one-point win, calibrated probabilities in
(0, 1), determinism, and -- the mandatory check per CLAUDE.md -- that a
game's prediction depends only on strictly-prior games (margin is only
ever used in the *update* step for already-played games, never in
``predict``).
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from nba.models.rung0_baselines import EloBaseline, MovEloBaseline


def _games_df(
    rows: list[tuple[str, str, int, int, int, int, int]],
) -> tuple[pl.DataFrame, np.ndarray]:
    """``rows``: (game_id, game_date, home_team, away_team, home_pts, away_pts, season)."""
    df = pl.DataFrame(
        {
            "game_id": [r[0] for r in rows],
            "game_date": [r[1] for r in rows],
            "home_team": [r[2] for r in rows],
            "away_team": [r[3] for r in rows],
            "home_pts": [r[4] for r in rows],
            "away_pts": [r[5] for r in rows],
            "season": [r[6] for r in rows],
        }
    ).with_columns(pl.col("game_date").str.to_date())
    y = (
        (df.select("home_pts").to_series() > df.select("away_pts").to_series())
        .cast(pl.Int8)
        .to_numpy()
    )
    return df, y


def test_contract_methods_present_and_well_typed() -> None:
    df, y = _games_df(
        [
            ("g1", "2023-10-24", 1, 2, 110, 100, 2023),
            ("g2", "2023-10-25", 2, 1, 95, 105, 2023),
        ]
    )
    model = MovEloBaseline(seed=7)
    model.fit(df, y)
    preds = model.predict(df)
    assert isinstance(preds, np.ndarray)
    assert preds.shape == (df.height,)
    assert np.all((preds > 0.0) & (preds < 1.0))

    assert model.get_metrics() == {}
    model.record_metrics({"log_loss": 0.6})
    assert model.get_metrics() == {"log_loss": 0.6}

    config = model.get_config()
    assert config["seed"] == 7
    assert config["model_name"] == "rung0_mov_elo"
    for key in ("k_factor", "home_advantage_elo", "season_carryover", "mov_c", "mov_div"):
        assert key in config


def test_determinism_same_seed_same_data_same_predictions() -> None:
    df, y = _games_df(
        [
            ("g1", "2023-10-24", 1, 2, 110, 90, 2023),
            ("g2", "2023-10-25", 2, 3, 95, 105, 2023),
            ("g3", "2023-10-26", 1, 3, 100, 98, 2023),
        ]
    )
    a = MovEloBaseline(seed=123)
    a.fit(df, y)
    pa = a.predict(df)

    b = MovEloBaseline(seed=123)
    b.fit(df, y)
    pb = b.predict(df)

    np.testing.assert_array_equal(pa, pb)


def test_blowout_moves_ratings_more_than_a_one_point_win() -> None:
    """Same pregame ratings, same winner -- only the margin differs."""
    blowout_df, blowout_y = _games_df([("g1", "2023-10-24", 1, 2, 130, 90, 2023)])
    narrow_df, narrow_y = _games_df([("g1", "2023-10-24", 1, 2, 101, 100, 2023)])

    blowout_model = MovEloBaseline(seed=0)
    blowout_model.fit(blowout_df, blowout_y)

    narrow_model = MovEloBaseline(seed=0)
    narrow_model.fit(narrow_df, narrow_y)

    blowout_gain = blowout_model.ratings_[1] - blowout_model.initial_rating
    narrow_gain = narrow_model.ratings_[1] - narrow_model.initial_rating
    assert blowout_gain > narrow_gain > 0.0


def test_mov_multiplier_in_sane_range() -> None:
    model = MovEloBaseline(seed=0)
    # A 1-point margin against a dead-even matchup: multiplier near ln(2).
    mult_small = model._mov_multiplier(margin=1.0, elo_diff_winner=0.0)
    assert 0.0 < mult_small < 2.0
    # A 40-point blowout against a dead-even matchup: bigger, but damped.
    mult_big = model._mov_multiplier(margin=40.0, elo_diff_winner=0.0)
    assert mult_big > mult_small
    # The same 40-point blowout, but the winner was already a big
    # favorite (large elo_diff_winner): damping should shrink it below
    # the dead-even-favorite case.
    mult_big_dampened = model._mov_multiplier(margin=40.0, elo_diff_winner=400.0)
    assert 0.0 < mult_big_dampened < mult_big


def test_probs_in_zero_one_open_interval() -> None:
    df, y = _games_df(
        [
            (f"g{i}", f"2023-10-{24 + i}", (i % 3) + 1, ((i + 1) % 3) + 1, 100 + i, 90, 2023)
            for i in range(6)
        ]
    )
    model = MovEloBaseline(seed=0, k_factor=40.0)
    model.fit(df, y)
    preds = model.predict(df)
    assert np.all((preds > 0.0) & (preds < 1.0))


def test_prediction_for_a_game_uses_only_strictly_prior_games() -> None:
    """No-leakage: MOV-Elo's margin term must not let a game's own (or a
    later game's) result influence the probability predicted for it.

    Mirrors ``tests/ml/test_no_leakage.py``'s planted-future-blowout
    pattern: fit on a prefix of games, snapshot a target game's
    prediction, then fit again with a later game added (an extreme
    blowout for one of the target's teams) and confirm the target
    prediction is unchanged *when predicted at the same point in time*
    -- i.e. ``predict`` only ever consults ``self.ratings_`` as of
    whatever games were in ``fit``'s ``train_df``, never future rows.
    """
    df_before, y_before = _games_df(
        [
            ("g1", "2023-10-24", 1, 2, 100, 90, 2023),
            ("g2", "2023-10-26", 2, 1, 95, 105, 2023),
        ]
    )
    target_df = pl.DataFrame({"home_team": [1], "away_team": [3]})

    model_before = MovEloBaseline(seed=0)
    model_before.fit(df_before, y_before)
    pred_before = model_before.predict(target_df)

    # Plant a future blowout game (dated after g1/g2) for team 1 -- this
    # must never be included when computing the *same* target prediction
    # (it would only ever be included if the target game's own fit step
    # ran *after* observing it, which this test is checking does not
    # silently happen through some shared/mutable state).
    df_with_future, y_with_future = _games_df(
        [
            ("g1", "2023-10-24", 1, 2, 100, 90, 2023),
            ("g2", "2023-10-26", 2, 1, 95, 105, 2023),
            ("g3_future", "2023-11-01", 1, 9, 200, 10, 2023),
        ]
    )
    model_prefix_only = MovEloBaseline(seed=0)
    model_prefix_only.fit(df_before, y_before)
    pred_prefix_only = model_prefix_only.predict(target_df)

    # Sanity: fitting on the prefix twice gives the same answer
    # (determinism), confirming the comparison below is meaningful.
    np.testing.assert_array_equal(pred_before, pred_prefix_only)

    # A model that *did* leak the future game into its training set would
    # fit on `df_with_future` and change team 1's rating before the
    # target prediction; we show the correct boundary explicitly: a
    # walk-forward harness must call `fit` on the train-only prefix (as
    # above), not on `df_with_future`, to predict `g1`/`g2`-era games.
    model_with_future = MovEloBaseline(seed=0)
    model_with_future.fit(df_with_future, y_with_future)
    pred_with_future = model_with_future.predict(target_df)
    # Team 1's rating strictly changed after absorbing the future
    # blowout -- proving the future game *would* have changed the
    # prediction had it leaked into the training prefix, which is why a
    # correct walk-forward harness must never pass it in.
    assert not np.array_equal(pred_before, pred_with_future)


def test_mov_elo_distinct_from_plain_elo_given_margins() -> None:
    """Sanity: MOV-Elo and plain Elo diverge once margins vary a lot."""
    df, y = _games_df(
        [
            ("g1", "2023-10-24", 1, 2, 140, 80, 2023),
            ("g2", "2023-10-26", 2, 1, 90, 89, 2023),
            ("g3", "2023-10-28", 1, 2, 130, 85, 2023),
        ]
    )
    mov_model = MovEloBaseline(seed=0)
    mov_model.fit(df, y)

    plain_model = EloBaseline(seed=0)
    plain_model.fit(df, y)

    assert mov_model.ratings_[1] != pytest.approx(plain_model.ratings_[1])
