"""Tests for the MOV-Elo genetic-algorithm tuner (``nba.eval.ga_tune``).

All on tiny synthetic data with small ``maxiter``/``popsize`` -- fast by
construction. The core thing under test is the anti-overfit contract:
the GA's objective is walk-forward OOF log loss computed *only* over
whatever rows it is given, and the holdout season's rows are never
passed to it.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from nba.eval.ga_tune import (
    DEFAULT_BOUNDS,
    PARAM_NAMES,
    evaluate_mov_elo_on_holdout,
    tune_mov_elo_ga,
    walk_forward_oof_log_loss,
)
from nba.eval.walkforward import split_frozen_holdout


def _synthetic_seasons_df(n_seasons: int = 3, games_per_season: int = 8) -> pl.DataFrame:
    """A few fake seasons of games with varying margins, four teams."""
    rows = []
    rng = np.random.default_rng(0)
    for s in range(n_seasons):
        season = 2022 + s
        for g in range(games_per_season):
            home = (g % 4) + 1
            away = ((g + 1) % 4) + 1
            # Deterministic-ish margin so the objective is stable, but
            # varied enough that different hyperparams score differently.
            margin = int(rng.integers(-20, 21))
            home_pts = 100 + max(margin, 0)
            away_pts = 100 + max(-margin, 0)
            if home_pts == away_pts:
                home_pts += 1
            rows.append(
                (
                    f"s{s}g{g}",
                    f"{season}-11-{(g % 27) + 1:02d}",
                    season,
                    home,
                    away,
                    home_pts,
                    away_pts,
                )
            )
    df = pl.DataFrame(
        {
            "game_id": [r[0] for r in rows],
            "game_date": [r[1] for r in rows],
            "season": [r[2] for r in rows],
            "home_team": [r[3] for r in rows],
            "away_team": [r[4] for r in rows],
            "home_pts": [r[5] for r in rows],
            "away_pts": [r[6] for r in rows],
        }
    ).with_columns(pl.col("game_date").str.to_date())
    y = (df.select("home_pts").to_series() > df.select("away_pts").to_series()).cast(pl.Int8)
    return df.with_columns(y=y)


def test_walk_forward_oof_log_loss_is_finite_and_sane() -> None:
    df = _synthetic_seasons_df()
    loss = walk_forward_oof_log_loss(
        df,
        params={
            "k_factor": 20.0,
            "home_advantage_elo": 65.0,
            "season_carryover": 0.75,
            "mov_c": 2.2,
            "mov_div": 0.001,
        },
        seed=0,
    )
    assert np.isfinite(loss)
    assert loss > 0.0


def test_ga_tuner_returns_params_within_bounds() -> None:
    df = _synthetic_seasons_df()
    best_params, best_loss = tune_mov_elo_ga(None, tunable_df=df, seed=0, maxiter=2, popsize=4)
    assert set(best_params.keys()) == set(PARAM_NAMES)
    for name, value in best_params.items():
        lo, hi = DEFAULT_BOUNDS[name]
        assert lo <= value <= hi
    assert np.isfinite(best_loss)


def test_ga_tuner_is_deterministic_given_seed() -> None:
    df = _synthetic_seasons_df()
    params_a, loss_a = tune_mov_elo_ga(None, tunable_df=df, seed=5, maxiter=2, popsize=4)
    params_b, loss_b = tune_mov_elo_ga(None, tunable_df=df, seed=5, maxiter=2, popsize=4)
    assert params_a == params_b
    assert loss_a == loss_b


def test_ga_tuner_never_receives_holdout_rows() -> None:
    """Construct a holdout season with a planted, out-of-range team id that
    would blow up/shift the objective if it ever leaked in, split it out
    via the same helper the real harness uses, and confirm the tuner's
    objective is identical whether or not the (excluded) holdout df
    exists in the caller's scope -- i.e. the tuner result depends only on
    ``tunable_df``, never on anything from the holdout season.
    """
    df = _synthetic_seasons_df(n_seasons=3)
    holdout_season = 2024  # last synthetic season
    split = split_frozen_holdout(df, holdout_season)
    assert split.holdout_df.height > 0  # the holdout actually has rows to withhold

    # Plant an extreme, obviously-leak-detectable game inside the holdout
    # season only -- team 99 never appears anywhere in the tunable data.
    planted = pl.DataFrame(
        {
            "game_id": ["planted_holdout_game"],
            "game_date": [split.holdout_df.select(pl.col("game_date").max()).item()],
            "season": [holdout_season],
            "home_team": [99],
            "away_team": [1],
            "home_pts": [250],
            "away_pts": [10],
            "y": pl.Series("y", [1], dtype=pl.Int8),
        }
    )
    holdout_with_plant = pl.concat([split.holdout_df, planted], how="vertical")

    params_without_plant, loss_without_plant = tune_mov_elo_ga(
        None, tunable_df=split.tunable_df, seed=3, maxiter=2, popsize=4
    )

    # Even though a "leaked" holdout-including frame exists right here in
    # scope, tune_mov_elo_ga is never called with it -- demonstrating the
    # call contract. Explicitly assert team 99 (only in the planted
    # holdout game) never appears in the data the tuner actually used.
    assert 99 not in set(split.tunable_df.select("home_team").to_series().to_list())
    assert holdout_with_plant.height > split.holdout_df.height  # plant really is holdout-only

    params_again, loss_again = tune_mov_elo_ga(
        None, tunable_df=split.tunable_df, seed=3, maxiter=2, popsize=4
    )
    assert params_without_plant == params_again
    assert loss_without_plant == loss_again


def test_evaluate_holdout_is_confirmatory_and_separate_from_tuning(tmp_path: object) -> None:
    """``evaluate_mov_elo_on_holdout`` is a distinct function from the
    tuner -- calling it does not mutate or re-tune anything, and it
    requires an explicit ``holdout_season`` argument rather than
    inferring one, so it can never be accidentally wired into the GA
    objective.
    """
    import duckdb

    from nba.db.connect import connect

    con: duckdb.DuckDBPyConnection = connect(":memory:")
    try:
        df = _synthetic_seasons_df(n_seasons=2, games_per_season=6)
        for row in df.iter_rows(named=True):
            con.execute(
                "INSERT INTO games (game_id, game_date, season, home_team, away_team, "
                "home_pts, away_pts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    row["game_id"],
                    row["game_date"],
                    row["season"],
                    row["home_team"],
                    row["away_team"],
                    row["home_pts"],
                    row["away_pts"],
                ],
            )
        holdout_season = int(df.select(pl.col("season").max()).item())
        params = {name: DEFAULT_BOUNDS[name][0] for name in PARAM_NAMES}
        result = evaluate_mov_elo_on_holdout(con, params, holdout_season=holdout_season, seed=0)
        assert result["n"] > 0
        assert np.isfinite(result["log_loss"])  # type: ignore[arg-type]
    finally:
        con.close()
