"""Walk-forward backtest harness (CLAUDE.md "Evaluation").

Train on strictly earlier dates, predict the next date block, roll
forward -- never a random split. A frozen final-season holdout is carved
out up front and is never touched by fold generation, so no amount of
"tuning on the backtest" can leak into it.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

#: Below this many distinct training dates, walk-forward is still run (the
#: fixture has exactly 2) but the report must say so instead of implying a
#: real multi-season backtest happened.
MIN_RELIABLE_DATES = 30


@dataclass
class Fold:
    fold_index: int
    train_df: pl.DataFrame
    test_df: pl.DataFrame
    train_end_date: object
    test_date: object


@dataclass
class HoldoutSplit:
    """Frozen final holdout season + everything else ("tunable")."""

    tunable_df: pl.DataFrame
    holdout_df: pl.DataFrame
    holdout_season: int | None
    note: str


def split_frozen_holdout(df: pl.DataFrame, holdout_season: int | None) -> HoldoutSplit:
    """Carve out ``holdout_season`` as a frozen final holdout.

    If ``holdout_season`` is ``None`` or isn't present in ``df`` (e.g. the
    fixture only has one season), the holdout is empty and the note says
    so explicitly -- rungs are then only compared on the walk-forward
    folds over the tunable data, with a clear caveat in the report rather
    than a silently-empty holdout table.
    """
    if holdout_season is None:
        return HoldoutSplit(df, df.clear(), None, "no holdout_season configured")
    holdout_df = df.filter(pl.col("season") == holdout_season)
    tunable_df = df.filter(pl.col("season") != holdout_season)
    if holdout_df.height == 0:
        return HoldoutSplit(
            df,
            df.clear(),
            holdout_season,
            f"holdout_season={holdout_season} has no games in this dataset "
            "(expected on the fixture / single-season data); holdout is empty",
        )
    note = ""
    return HoldoutSplit(tunable_df, holdout_df, holdout_season, note)


#: Valid ``mode`` values for :func:`filter_by_holdout_mode`.
HOLDOUT_MODES = ("all", "exclude_holdout", "holdout_only")


def filter_by_holdout_mode(
    df: pl.DataFrame, holdout_season: int | None, mode: str = "all"
) -> pl.DataFrame:
    """Optional season-based filter shared by every props/routing eval entry
    point that wants an opt-in frozen-holdout split, without reimplementing
    ad hoc season filtering per module.

    Thin wrapper around :func:`split_frozen_holdout` so callers get the
    exact same season-column semantics the Elo rungs already use
    (``nba/eval/ga_tune.py --holdout-season``, default ``2025`` per
    ``docs/ACCEPTANCE_CRITERIA_2026-10-08.md``'s "canonical frozen holdout
    for props/routing").

    ``mode``:
      - ``"all"`` (default): no filtering -- identical to every caller's
        pre-existing behavior. Also the result whenever ``holdout_season``
        is ``None``, regardless of ``mode``.
      - ``"exclude_holdout"``: tunable/training pool only (``season !=
        holdout_season``) -- what tuning/backtesting should use.
      - ``"holdout_only"``: the frozen holdout season only (``season ==
        holdout_season``) -- confirmatory evaluation only, at most once,
        logged per the acceptance-criteria doc's ledger discipline.
    """
    if holdout_season is None or mode == "all":
        return df
    split = split_frozen_holdout(df, holdout_season)
    if mode == "exclude_holdout":
        return split.tunable_df
    if mode == "holdout_only":
        return split.holdout_df
    raise ValueError(f"unknown holdout mode {mode!r}; expected one of {HOLDOUT_MODES}")


def make_walk_forward_folds(df: pl.DataFrame, min_train_games: int = 1) -> list[Fold]:
    """One fold per distinct test date: train on strictly earlier dates.

    ``df`` must already exclude the frozen holdout (see
    :func:`split_frozen_holdout`). The first ``k`` dates are skipped as
    training-only until the cumulative training-row count reaches
    ``min_train_games`` (so the very first date, with zero prior games,
    is never itself a test fold).
    """
    if df.height == 0:
        return []
    ordered = df.sort(["game_date", "game_id"])
    dates = ordered.select("game_date").unique().sort("game_date").to_series().to_list()

    folds: list[Fold] = []
    for fold_index, test_date in enumerate(dates):
        train_df = ordered.filter(pl.col("game_date") < test_date)
        if train_df.height < min_train_games:
            continue
        test_df = ordered.filter(pl.col("game_date") == test_date)
        folds.append(
            Fold(
                fold_index=fold_index,
                train_df=train_df,
                test_df=test_df,
                train_end_date=train_df.select(pl.col("game_date").max()).item(),
                test_date=test_date,
            )
        )
    return folds


def walk_forward_data_sufficiency_note(dates_seen: int) -> str:
    if dates_seen >= MIN_RELIABLE_DATES:
        return ""
    return (
        f"only {dates_seen} distinct training dates available; this is a smoke "
        "run on fixture-scale data, not a statistically powered multi-season "
        "backtest (CLAUDE.md requires >=3 full seasons for real conclusions)"
    )
