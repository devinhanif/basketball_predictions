"""Game-level COVID / era tags (``game_era_flags``), rule-based and conservative.

Rules (documented in docs/NEW_DATA_SOURCES.md, "COVID era tags"; season int = start year):

* 2019 (2019-20): ``shortened_season`` for every game (suspended 2020-03-11, 63-75 games per
  team). ``covid_bubble`` + ``no_fans`` for games on/after 2020-07-30 (restart in Orlando, no
  home court; neutral site).
* 2020 (2020-21): ``shortened_season`` (72 games, started 2020-12-22) and ``limited_fans`` for
  EVERY game (regular season + playoffs): attendance ranged from empty to partial capacity by
  arena and date, and no per-arena calendar is encoded, so the whole season is one "reduced
  attendance" regime. ``no_fans`` is NOT asserted (unknown per arena). Toronto home games are
  noted (played in Tampa all season).
* every other season: all flags False.

Known unflagged edges: the last pre-suspension dates (2020-03-10/11, some games without fans),
2021-22 January capacity caps (Omicron), and COVID-protocol postponements/rescheduled games.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import duckdb
import polars as pl

BUBBLE_START = date(2020, 7, 30)
TORONTO = 1610612761
COLUMNS = [
    "game_id",
    "season",
    "covid_bubble",
    "no_fans",
    "limited_fans",
    "shortened_season",
    "notes",
]


def compute_era_flags(games: pl.DataFrame) -> pl.DataFrame:
    """``games`` needs game_id, game_date (Date), season (int), home_team (int)."""
    rows: list[dict[str, object]] = []
    for g in games.iter_rows(named=True):
        season = int(g["season"])
        d = g["game_date"]
        bubble = season == 2019 and d >= BUBBLE_START
        notes: list[str] = []
        if bubble:
            notes.append("orlando bubble: neutral site, no fans")
        if season == 2020:
            notes.append("reduced attendance by arena/date; blanket limited_fans")
            if int(g["home_team"]) == TORONTO:
                notes.append("TOR home games played in Tampa")
        rows.append(
            {
                "game_id": str(g["game_id"]),
                "season": season,
                "covid_bubble": bubble,
                "no_fans": bubble,
                "limited_fans": season == 2020,
                "shortened_season": season in (2019, 2020),
                "notes": "; ".join(notes) or None,
            }
        )
    schema = {
        "game_id": pl.Utf8,
        "season": pl.Int64,
        "covid_bubble": pl.Boolean,
        "no_fans": pl.Boolean,
        "limited_fans": pl.Boolean,
        "shortened_season": pl.Boolean,
        "notes": pl.Utf8,
    }
    return pl.DataFrame(rows, schema=schema)


def read_games(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    return con.execute("SELECT game_id, game_date, season, home_team FROM games").pl()


def write_era_flags(con: duckdb.DuckDBPyConnection, flags: pl.DataFrame) -> int:
    """Idempotent delete-then-insert of the given games' flags into ``game_era_flags``."""
    con.execute(
        "CREATE TABLE IF NOT EXISTS game_era_flags (game_id VARCHAR PRIMARY KEY, season INT, "
        "covid_bubble BOOLEAN, no_fans BOOLEAN, limited_fans BOOLEAN, "
        "shortened_season BOOLEAN, notes VARCHAR)"
    )
    if flags.is_empty():
        return 0
    ids = flags["game_id"].to_list()
    con.execute("DELETE FROM game_era_flags WHERE game_id IN (SELECT unnest(?))", [ids])
    con.register("_era_df", flags.select(COLUMNS))
    try:
        con.execute("INSERT INTO game_era_flags SELECT * FROM _era_df")
    finally:
        con.unregister("_era_df")
    return flags.height


def main(argv: Sequence[str] | None = None) -> int:
    """Write flags to the HISTORY DB (+ a parquet covering history and current seasons).

    ``nba.duckdb`` is only ever opened read-only here.
    """
    from nba.db.connect import DEFAULT_DB_PATH, connect

    ap = argparse.ArgumentParser(prog="python -m nba.ingest.era_flags")
    ap.add_argument("--history-db", type=Path, default=Path("data/history/nba_history.duckdb"))
    ap.add_argument("--parquet", type=Path, default=Path("data/history/game_era_flags.parquet"))
    args = ap.parse_args(argv)
    frames: list[pl.DataFrame] = []
    cur = connect(DEFAULT_DB_PATH, read_only=True)
    try:
        frames.append(compute_era_flags(read_games(cur)))
    finally:
        cur.close()
    hcon = connect(args.history_db)
    try:
        hist = compute_era_flags(read_games(hcon))
        n = write_era_flags(hcon, hist)
    finally:
        hcon.close()
    frames.append(hist)
    allf = pl.concat(frames).unique(subset=["game_id"], keep="last")
    args.parquet.parent.mkdir(parents=True, exist_ok=True)
    allf.write_parquet(args.parquet)
    print(f"era flags: history_db={n} parquet={allf.height} -> {args.parquet}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
