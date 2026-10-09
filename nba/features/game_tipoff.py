"""Real scheduled tip-off times per game, for as-of gating of pre-tip information.

The ``games`` table carries a DATE only, so backtests historically gated
official injury-report snapshots on a PROXY tip (``game_date`` at 19:00 ET).
For a matinee that proxy admits snapshots published after the real tip. This
module builds ``game_tipoff(game_id, tipoff_utc, tip_et)`` from the cached
schedule parquet (``data/schedule/raw_*.parquet``):

* ``tipoff_utc``: ``gameDateTimeUTC`` (naive UTC).
* ``tip_et``: ``gameDateTimeEst`` (naive US-Eastern wall clock; the schedule
  labels it with a trailing ``Z`` but it is the ET clock). This is the same
  naive-ET clock as the report ``as_of`` stamps, so the two compare directly.

As-of discipline: a scheduled tip time is published before the game and is
used here only as a cutoff (``as_of <= tip_et - lead``); it never feeds a
feature value. Games whose real time is missing fall back to the proxy,
and :func:`attach_real_tips` reports how many.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

SCHEDULE_DIR = Path("data/schedule")
TIP_SOURCES = ("proxy19", "real")
_SCHEMA = pl.Schema(
    {  # type: ignore[arg-type]
        "game_id": pl.Utf8,
        "tipoff_utc": pl.Datetime("us"),
        "tip_et": pl.Datetime("us"),
    }
)


def _parse(col: str) -> pl.Expr:
    return pl.col(col).str.replace("Z$", "").str.to_datetime("%Y-%m-%dT%H:%M:%S", strict=False)


def build_game_tipoff(schedule_dir: str | Path = SCHEDULE_DIR) -> pl.DataFrame:
    """One row per game_id from every ``raw_*.parquet`` in ``schedule_dir``.

    Rows with an unparseable/absent time are dropped (the caller falls back to
    the proxy and counts them). Duplicated game_ids keep the last file's row.
    """
    frames = []
    for f in sorted(Path(schedule_dir).glob("raw_*.parquet")):
        d = pl.read_parquet(f, columns=["gameId", "gameDateTimeUTC", "gameDateTimeEst"])
        frames.append(
            d.select(
                pl.col("gameId").cast(pl.Utf8).alias("game_id"),
                _parse("gameDateTimeUTC").alias("tipoff_utc"),
                _parse("gameDateTimeEst").alias("tip_et"),
            )
        )
    if not frames:
        return pl.DataFrame(schema=_SCHEMA)
    out = pl.concat(frames).filter(pl.col("tip_et").is_not_null())
    return out.unique("game_id", keep="last", maintain_order=True).cast(_SCHEMA)


def attach_real_tips(rows: pl.DataFrame, tips: pl.DataFrame | None = None) -> pl.DataFrame:
    """Add a nullable ``tip_et`` column to report rows (null = real time missing)."""
    if "tip_et" in rows.columns:
        rows = rows.drop("tip_et")
    tips = build_game_tipoff() if tips is None else tips
    return rows.join(tips.select(["game_id", "tip_et"]), on="game_id", how="left")


def tip_coverage(games: pl.DataFrame, tips: pl.DataFrame | None = None) -> dict[str, int]:
    """Coverage of real tips over ``games`` (game_id): n, with_real, proxy_fallback."""
    tips = build_game_tipoff() if tips is None else tips
    n = games.select("game_id").unique().height
    have = games.select("game_id").unique().join(tips, on="game_id").height
    return {"n_games": n, "with_real_tip": have, "proxy_fallback": n - have}
