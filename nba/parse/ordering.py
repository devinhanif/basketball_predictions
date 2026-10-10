"""Game-order sort for raw play-by-play, shared by the lineup tracker and the possession parser.

``action_number`` is not game order. The feed appends post-hoc corrections (a missed
substitution, a rebound, a shot) after the end of the game with the right period and
clock but a late number: 4,512 rows in 1,651 games, 2022-25 (ADR 0002 item 5). Any
consumer that sorts by ``(period, action_number)`` puts those at the end of their
period.

Tie rule (events stamped with the same period and clock):

  1. substitutions first, so the player coming in is on the floor for an action stamped
     at the same instant (the lineup tracker needs this);
  2. every other event keeps ``action_number`` order, so a miss, its rebound and the
     putback (all stamped alike) stay in the feed's own sequence, as do a foul and its
     free throws. The possession parser skips substitutions, so for it the rule reduces
     to "clock, then action_number".

A row whose clock does not parse sorts as 0:00 (end of its period), the same value the
possession parser stamps on it.
"""

from __future__ import annotations

import re

import polars as pl

CLOCK_RE = re.compile(r"PT(\d+)M([\d.]+)S")


def in_game_order(pbp: pl.DataFrame) -> pl.DataFrame:
    """Order events as they happened: period, clock descending, substitutions first at a tie.

    Needs columns ``period``, ``clock`` (ISO ``PT11M38.00S``), ``action_type`` and
    ``action_number``; returns the same columns, sorted.
    """
    clock_s = pl.col("clock").str.extract(CLOCK_RE.pattern, 1).cast(pl.Float64) * 60.0 + pl.col(
        "clock"
    ).str.extract(CLOCK_RE.pattern, 2).cast(pl.Float64)
    return (
        pbp.with_columns(
            clock_s.fill_null(0.0).alias("_clock_s"),
            (pl.col("action_type") != "Substitution").alias("_not_sub"),
        )
        .sort(
            ["period", "_clock_s", "_not_sub", "action_number"],
            descending=[False, True, False, False],
        )
        .drop("_clock_s", "_not_sub")
    )
