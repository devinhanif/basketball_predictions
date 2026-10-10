"""Confirmatory-holdout fix: sequential state must update through the
frozen holdout season, and the holdout evaluation must never peek.

Covers the bug described in the task: a single static
``model.predict(holdout_df)`` call left a sequential model (Elo) stuck on
stale end-of-tunable-season ratings for the whole holdout season. The fix
in ``research.eval.run._evaluate_holdout`` rolls walk-forward through the
holdout, warm-started on the tunable data, hyperparameters fixed.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from nba.features.team_features import MATCHUP_FEATURE_COLUMNS
from nba.models.rung0_baselines import EloBaseline
from research.eval.run import RUNG_SPECS, _evaluate_holdout
from research.models.rung2_gbm import GBM_EXTRA_COLUMNS

_ALL_FEATURE_COLUMNS = list(MATCHUP_FEATURE_COLUMNS) + list(GBM_EXTRA_COLUMNS)


def _matchup_df(rows: list[tuple[str, str, int, int, int, int]]) -> pl.DataFrame:
    """Build a synthetic matchup-feature table.

    ``rows`` is ``(game_id, game_date, season, home_team, away_team, y)``.
    Non-Elo feature columns are deterministic but non-constant filler --
    the rung-1/rung-2 models in ``RUNG_SPECS`` just need to run without
    error; this test's assertions are about Elo's sequential state.
    """
    n = len(rows)
    data: dict[str, object] = {
        "game_id": [r[0] for r in rows],
        "game_date": [r[1] for r in rows],
        "season": [r[2] for r in rows],
        "home_team": [r[3] for r in rows],
        "away_team": [r[4] for r in rows],
        "y": [r[5] for r in rows],
    }
    for j, col in enumerate(_ALL_FEATURE_COLUMNS):
        if col in ("home_b2b", "away_b2b"):
            data[col] = [bool((i + j) % 2) for i in range(n)]
        else:
            data[col] = [float((i * 3 + j * 7) % 11) for i in range(n)]
    return pl.DataFrame(data).with_columns(pl.col("game_date").str.to_date())


def _stale_vs_sequential_scenario() -> tuple[pl.DataFrame, pl.DataFrame]:
    """Tunable season inflates team 2's rating; holdout then deflates it
    (team 2 loses to team 3) *before* the team1-vs-team2 target game.

    A stale, fit-once prediction for the target game sees team 2's
    inflated end-of-tunable rating. A correctly sequential holdout
    evaluation sees team 2's rating after its holdout loss to team 3,
    which must give a different (higher, for team 1) P(home win).
    """
    tunable_rows = [
        (f"t{i}", d, 2023, 2, 1, 1)  # team 2 (away... here home) beats team 1 repeatedly
        for i, d in enumerate(
            ["2023-10-24", "2023-10-26", "2023-10-28", "2023-10-30", "2023-11-01"]
        )
    ]
    tunable_df = _matchup_df(tunable_rows)

    holdout_rows = [
        ("h0", "2024-10-23", 2024, 3, 2, 1),  # team 3 (home) beats team 2 (away)
        ("h_target", "2024-10-25", 2024, 1, 2, 1),  # target: team 1 (home) vs team 2
    ]
    holdout_df = _matchup_df(holdout_rows)
    return tunable_df, holdout_df


def test_holdout_evaluation_updates_sequential_elo_state() -> None:
    tunable_df, holdout_df = _stale_vs_sequential_scenario()
    seed = 0

    # The old, buggy behavior: fit once on tunable data, predict statically.
    stale_model = EloBaseline(seed=seed)
    y_train = tunable_df.select("y").to_series().to_numpy()
    stale_model.fit(tunable_df, y_train)
    target_row = holdout_df.filter(pl.col("game_id") == "h_target")
    stale_p = float(stale_model.predict(target_row)[0])

    holdout_metrics = _evaluate_holdout(holdout_df, tunable_df, seed)
    elo_out = holdout_metrics["rung0_elo"]
    game_ids = elo_out["game_ids"]
    ps = elo_out["p"]
    sequential_p = float(ps[game_ids.index("h_target")])

    # Sequential state (updated by team 2's holdout loss to team 3) must
    # differ from the stale, fit-once prediction.
    assert sequential_p != stale_p

    # And it must match hand-replaying Elo through tunable + h0 before
    # predicting h_target.
    replay = EloBaseline(seed=seed)
    combined_before_target = pl.concat(
        [tunable_df, holdout_df.filter(pl.col("game_id") == "h0")], how="vertical"
    )
    y_replay = combined_before_target.select("y").to_series().to_numpy()
    replay.fit(combined_before_target, y_replay)
    expected_p = float(replay.predict(target_row)[0])
    assert sequential_p == expected_p


def test_holdout_prediction_for_game_g_unchanged_if_g_or_later_outcome_altered() -> None:
    """No peeking: altering G's own outcome, or a later game's outcome,
    must not change the predicted probability for G, for every rung."""
    tunable_df, holdout_df = _stale_vs_sequential_scenario()
    seed = 0

    baseline_metrics = _evaluate_holdout(holdout_df, tunable_df, seed)

    # Alter h_target's own recorded outcome.
    altered_target_outcome = holdout_df.with_columns(
        y=pl.when(pl.col("game_id") == "h_target").then(1 - pl.col("y")).otherwise(pl.col("y"))
    )
    altered_metrics = _evaluate_holdout(altered_target_outcome, tunable_df, seed)

    for name, _rung, _method, _factory in RUNG_SPECS:
        base = baseline_metrics[name]
        alt = altered_metrics[name]
        base_p = base["p"][base["game_ids"].index("h_target")]
        alt_p = alt["p"][alt["game_ids"].index("h_target")]
        assert base_p == alt_p, f"{name}: predicting h_target peeked at its own outcome"

    # Add a game strictly *after* h_target and alter its outcome too.
    later_rows = [("h_after", "2024-10-27", 2024, 1, 4, 0)]
    with_later = pl.concat([holdout_df, _matchup_df(later_rows)], how="vertical")
    metrics_with_later = _evaluate_holdout(with_later, tunable_df, seed)

    later_rows_altered = [("h_after", "2024-10-27", 2024, 1, 4, 1)]
    with_later_altered_outcome = pl.concat(
        [holdout_df, _matchup_df(later_rows_altered)], how="vertical"
    )
    metrics_with_later_altered = _evaluate_holdout(with_later_altered_outcome, tunable_df, seed)

    for name, _rung, _method, _factory in RUNG_SPECS:
        a = metrics_with_later[name]
        b = metrics_with_later_altered[name]
        p_a = a["p"][a["game_ids"].index("h_target")]
        p_b = b["p"][b["game_ids"].index("h_target")]
        assert p_a == p_b, f"{name}: h_target's prediction peeked at a later game's outcome"


def test_season_regression_moves_elo_ratings_toward_the_mean() -> None:
    """Across a season boundary, a rated team's rating must move toward
    ``initial_rating`` by exactly ``1 - season_carryover``, deterministically."""
    carryover = 0.75
    model = EloBaseline(seed=0, season_carryover=carryover)

    season_one_rows = [
        (f"s1_{i}", d, 2023, 2, 1, 1)
        for i, d in enumerate(["2023-10-24", "2023-10-26", "2023-10-28", "2023-10-30"])
    ]
    df_season_one = _matchup_df(season_one_rows)
    y = df_season_one.select("y").to_series().to_numpy()
    model.fit(df_season_one, y)
    rating_team2_end_of_season = model.ratings_[2]
    rating_team1_end_of_season = model.ratings_[1]
    assert rating_team2_end_of_season > model.initial_rating  # team 2 won every game

    season_two_rows = [("s2_0", "2024-10-23", 2024, 3, 4, 1)]  # no team 1/2 involvement
    combined = pl.concat([df_season_one, _matchup_df(season_two_rows)], how="vertical")
    y_combined = combined.select("y").to_series().to_numpy()
    model.fit(combined, y_combined)

    expected_team2 = carryover * rating_team2_end_of_season + (1 - carryover) * model.initial_rating
    expected_team1 = carryover * rating_team1_end_of_season + (1 - carryover) * model.initial_rating
    assert model.ratings_[2] == expected_team2
    assert model.ratings_[1] == expected_team1


def test_season_regression_is_deterministic_given_seed() -> None:
    tunable_df, holdout_df = _stale_vs_sequential_scenario()
    combined = pl.concat([tunable_df, holdout_df], how="vertical")
    y = combined.select("y").to_series().to_numpy()

    model_a = EloBaseline(seed=5)
    model_a.fit(combined, y)
    model_b = EloBaseline(seed=5)
    model_b.fit(combined, y)
    assert model_a.ratings_ == model_b.ratings_


def test_no_season_column_skips_regression_without_crashing() -> None:
    """Small unit-test-style fixtures without a ``season`` column (e.g.
    ``test_models_contract.py``) must keep working unchanged."""
    df = pl.DataFrame(
        {
            "game_id": ["g1", "g2"],
            "game_date": ["2023-10-24", "2023-10-25"],
            "home_team": [1, 2],
            "away_team": [2, 1],
        }
    ).with_columns(pl.col("game_date").str.to_date())
    y = np.array([1, 0])
    model = EloBaseline(seed=0)
    model.fit(df, y)
    assert set(model.ratings_.keys()) == {1, 2}
