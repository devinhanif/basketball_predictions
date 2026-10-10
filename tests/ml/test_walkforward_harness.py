"""Walk-forward harness invariants: no random splits, train < test dates,
and the frozen holdout season is excluded from every fold.
"""

from __future__ import annotations

import polars as pl

from nba.truth.walkforward import (
    filter_by_holdout_mode,
    make_walk_forward_folds,
    split_frozen_holdout,
)


def _toy_matchups() -> pl.DataFrame:
    dates = ["2023-10-24", "2023-10-25", "2023-10-26", "2023-10-27", "2024-01-05"]
    seasons = [2023, 2023, 2023, 2023, 2024]
    return pl.DataFrame(
        {
            "game_id": [f"g{i}" for i in range(len(dates))],
            "game_date": dates,
            "season": seasons,
            "y": [1, 0, 1, 0, 1],
        }
    ).with_columns(pl.col("game_date").str.to_date())


def test_fold_train_set_is_strictly_earlier_than_test_date() -> None:
    df = _toy_matchups().filter(pl.col("season") == 2023)
    folds = make_walk_forward_folds(df, min_train_games=1)
    assert len(folds) > 0
    for fold in folds:
        train_dates = fold.train_df.select("game_date").to_series().to_list()
        test_dates = fold.test_df.select("game_date").to_series().to_list()
        assert all(td < fold.test_date for td in train_dates)
        assert all(td == fold.test_date for td in test_dates)


def test_harness_never_evaluates_on_a_training_row() -> None:
    df = _toy_matchups().filter(pl.col("season") == 2023)
    folds = make_walk_forward_folds(df, min_train_games=1)
    for fold in folds:
        train_ids = set(fold.train_df.select("game_id").to_series().to_list())
        test_ids = set(fold.test_df.select("game_id").to_series().to_list())
        assert train_ids.isdisjoint(test_ids)


def test_min_train_games_skips_folds_with_too_little_history() -> None:
    df = _toy_matchups().filter(pl.col("season") == 2023)
    folds_low = make_walk_forward_folds(df, min_train_games=1)
    folds_high = make_walk_forward_folds(df, min_train_games=100)
    assert len(folds_high) == 0
    assert len(folds_low) > 0


def test_frozen_holdout_season_never_appears_in_walk_forward_folds() -> None:
    df = _toy_matchups()
    split = split_frozen_holdout(df, holdout_season=2024)
    assert split.holdout_df.height == 1
    assert split.tunable_df.filter(pl.col("season") == 2024).height == 0

    folds = make_walk_forward_folds(split.tunable_df, min_train_games=1)
    for fold in folds:
        assert fold.train_df.filter(pl.col("season") == 2024).height == 0
        assert fold.test_df.filter(pl.col("season") == 2024).height == 0


def test_holdout_split_degrades_gracefully_when_season_absent() -> None:
    df = _toy_matchups().filter(pl.col("season") == 2023)
    split = split_frozen_holdout(df, holdout_season=2099)
    assert split.holdout_df.height == 0
    assert split.note != ""


def test_holdout_split_noop_when_not_configured() -> None:
    df = _toy_matchups()
    split = split_frozen_holdout(df, holdout_season=None)
    assert split.holdout_df.height == 0
    assert split.tunable_df.height == df.height


def _toy_season_2025() -> pl.DataFrame:
    """Same shape as ``_toy_matchups`` but with a 2025 (the project's
    canonical frozen holdout, docs/ACCEPTANCE_CRITERIA_2026-10-08.md) row
    mixed in with earlier seasons -- the holdout-filter tests below."""
    return pl.DataFrame(
        {
            "game_id": ["g0", "g1", "g2", "g3"],
            "game_date": ["2023-10-24", "2023-10-25", "2024-01-05", "2025-11-01"],
            "season": [2023, 2023, 2024, 2025],
            "y": [1, 0, 1, 0],
        }
    ).with_columns(pl.col("game_date").str.to_date())


def test_filter_by_holdout_mode_default_all_is_noop() -> None:
    df = _toy_season_2025()
    assert filter_by_holdout_mode(df, holdout_season=2025, mode="all").height == df.height
    # Also a no-op when holdout_season is None, regardless of mode.
    assert filter_by_holdout_mode(df, holdout_season=None, mode="exclude_holdout").height == (
        df.height
    )


def test_filter_by_holdout_mode_exclude_holdout_drops_season_2025() -> None:
    df = _toy_season_2025()
    out = filter_by_holdout_mode(df, holdout_season=2025, mode="exclude_holdout")
    assert out.height == 3
    assert out.filter(pl.col("season") == 2025).height == 0


def test_filter_by_holdout_mode_holdout_only_keeps_only_season_2025() -> None:
    df = _toy_season_2025()
    out = filter_by_holdout_mode(df, holdout_season=2025, mode="holdout_only")
    assert out.height == 1
    assert out["season"].to_list() == [2025]


def test_filter_by_holdout_mode_rejects_unknown_mode() -> None:
    df = _toy_season_2025()
    try:
        filter_by_holdout_mode(df, holdout_season=2025, mode="bogus")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown holdout mode")
