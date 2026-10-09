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

import logging
from datetime import UTC
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

log = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")

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


def build_game_tipoff(schedule_dir: str | Path | None = None) -> pl.DataFrame:
    """One row per game_id from every ``raw_*.parquet`` in ``schedule_dir``.

    Rows with an unparseable/absent time are dropped (the caller falls back to
    the proxy and counts them). Duplicated game_ids keep the last file's row.
    """
    schedule_dir = SCHEDULE_DIR if schedule_dir is None else schedule_dir
    frames = []
    files = sorted(Path(schedule_dir).glob("raw_*.parquet")) + sorted(
        Path(schedule_dir).glob("live_tips_*.parquet")
    )
    for f in files:
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
    out = rows.join(tips.select(["game_id", "tip_et"]), on="game_id", how="left")
    n_miss = out.filter(pl.col("tip_et").is_null()).select("game_id").unique().height
    if n_miss:
        log.warning("real tip missing for %d game(s); falling back to the 19:00 ET proxy", n_miss)
    return out


def tip_coverage(games: pl.DataFrame, tips: pl.DataFrame | None = None) -> dict[str, int]:
    """Coverage of real tips over ``games`` (game_id): n, with_real, proxy_fallback."""
    tips = build_game_tipoff() if tips is None else tips
    n = games.select("game_id").unique().height
    have = games.select("game_id").unique().join(tips, on="game_id").height
    return {"n_games": n, "with_real_tip": have, "proxy_fallback": n - have}


def persist_live_tips(
    games: list[tuple[str, object]], season: str, schedule_dir: str | Path | None = None
) -> Path:
    """Cache forward-schedule tips as ``live_tips_<season>.parquet`` (same columns
    :func:`build_game_tipoff` reads), so games of a season with no ``raw_*`` file
    still gate training on their real tip once completed. ``games`` =
    ``[(game_id, tipoff_naive_utc)]``. Overwritten each call (the live schedule is
    authoritative for postponements); a schedule published before the game, so
    storing it is as-of safe."""
    import datetime as dt

    out = Path(SCHEDULE_DIR if schedule_dir is None else schedule_dir)
    out.mkdir(parents=True, exist_ok=True)
    ids, utc, est = [], [], []
    for gid, t in games:
        assert isinstance(t, dt.datetime)
        u = t.replace(tzinfo=UTC)
        ids.append(str(gid))
        utc.append(u.strftime("%Y-%m-%dT%H:%M:%SZ"))
        est.append(u.astimezone(_ET).strftime("%Y-%m-%dT%H:%M:%SZ"))
    p = out / f"live_tips_{season}.parquet"
    pl.DataFrame(
        {"gameId": ids, "gameDateTimeUTC": utc, "gameDateTimeEst": est},
        schema={"gameId": pl.Utf8, "gameDateTimeUTC": pl.Utf8, "gameDateTimeEst": pl.Utf8},
    ).write_parquet(p)
    return p
