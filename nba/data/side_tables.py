"""Joins against side tables whose row EXISTENCE depends on minutes played (audit F9).

``player_game_hustle`` has a row for 14% of players under 1 minute, 32% at 1-2, 65% at 2-5, 88% at
5-10 and 98%+ at 10+ (2022-24). A plain LEFT JOIN from the box score turns "no row" into NULLs that
are NULL iff tonight's minutes are low: the leak shape that contaminated three experiments. Use
``join_side_table`` so absence is an explicit flag and the values stay NULL (never 0), and never
join the CURRENT game's row into a pre-game feature (only strictly prior games are knowable).
"""

from __future__ import annotations

import polars as pl


def join_side_table(
    base: pl.DataFrame,
    side: pl.DataFrame,
    on: list[str],
    *,
    flag: str,
) -> pl.DataFrame:
    """LEFT JOIN ``side`` onto ``base`` and add boolean ``flag`` = "a side row exists".

    Side values for absent rows are NULL, not zero: "no row" means unknown. The flag is an
    outcome-dependent column when ``side`` is post-game; consumers must not use it (or the
    values) for the game being predicted. Duplicate keys in ``side`` raise.
    """
    if side.select(on).is_duplicated().any():
        raise ValueError(f"side table has duplicate keys on {on}")
    marker = f"__{flag}_present"
    joined = base.join(side.with_columns(pl.lit(True).alias(marker)), on=on, how="left")
    return joined.with_columns(pl.col(marker).fill_null(False).alias(flag)).drop(marker)
