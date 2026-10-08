"""Populate the OOF store / build router inputs from ``nba.duckdb`` (read-only).

    uv run python -m nba.stack.populate season-avg --stats pts reb ast fg3m
    uv run python -m nba.stack.populate context --out data/stack/context.parquet

Never opens ``nba.duckdb`` for writing; never emits season >= 2025.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from nba.stack import FROZEN_SEASON
from nba.stack.adapters import season_avg_oof
from nba.stack.oof import write_oof

SEASON_AVG_VERSION = "v1-hl10"

_SQL = """
SELECT s.game_id, s.player_id, g.game_date, g.season, s.minutes, s.starter,
       s.pts, s.reb, s.ast, s.fg3m
FROM player_game_stats s JOIN games g USING (game_id)
WHERE g.season <= ?
"""


def load_history(db: str, max_season: int) -> pd.DataFrame:
    con = duckdb.connect(db, read_only=True)
    try:
        df = con.execute(_SQL, [max_season]).df()
    finally:
        con.close()
    df["game_date"] = pd.to_datetime(df["game_date"])
    df["played"] = df["minutes"].fillna(0) > 0
    return df


def basic_context(hist: pd.DataFrame) -> pd.DataFrame:
    """As-of context per player-game (strictly prior games only).

    ``games_played_season``, ``career_played`` (prior), ``min_avg10`` (mean minutes of
    the last 10 prior played games), ``start_rate10`` (share of last 10 prior played
    games started), ``cold_start_bucket`` (career_played < 20 -> 'cold').
    The actual ``starter`` flag is returned too but is a SLICE label only; do not
    feed it to the gate.
    """
    h = hist.sort_values(["player_id", "game_date", "game_id"]).copy()
    pl = h["played"].astype(float)
    h["_p"] = pl
    g = h.groupby("player_id", sort=False)
    h["career_played"] = g["_p"].cumsum() - h["_p"]
    h["games_played_season"] = (
        h.groupby(["player_id", "season"], sort=False)["_p"].cumsum() - h["_p"]
    )
    mins = h["minutes"].where(h["played"])
    starts = h["starter"].astype(float).where(h["played"])
    h["_m"], h["_s"] = mins, starts
    h["min_avg10"] = g["_m"].transform(lambda s: s.shift(1).rolling(10, min_periods=1).mean())
    h["start_rate10"] = g["_s"].transform(lambda s: s.shift(1).rolling(10, min_periods=1).mean())
    h["cold_start_bucket"] = np.where(h["career_played"] < 20, "cold", "established")
    h["starter"] = h["starter"].astype(float)
    return h[
        [
            "game_id",
            "player_id",
            "games_played_season",
            "career_played",
            "min_avg10",
            "start_rate10",
            "cold_start_bucket",
            "starter",
        ]
    ]


def populate_season_avg(db: str, stats: list[str], oof_path: str | None, max_season: int) -> None:
    if max_season >= FROZEN_SEASON:
        raise SystemExit(f"--max-season must be < {FROZEN_SEASON}")
    hist = load_history(db, max_season)
    for stat in stats:
        h = hist.rename(columns={stat: "y"})[
            ["game_id", "player_id", "game_date", "season", "y", "played"]
        ]
        frame = season_avg_oof(h, stat, max_season=max_season)
        n = write_oof(frame, f"season_avg_{stat}", SEASON_AVG_VERSION, oof_path)
        print(f"season_avg_{stat} {SEASON_AVG_VERSION}: wrote {n} rows")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("season-avg")
    a.add_argument("--db", default="nba.duckdb")
    a.add_argument("--stats", nargs="+", default=["pts", "reb", "ast", "fg3m"])
    a.add_argument("--oof", default=None)
    a.add_argument("--max-season", type=int, default=FROZEN_SEASON - 1)
    c = sub.add_parser("context")
    c.add_argument("--db", default="nba.duckdb")
    c.add_argument("--out", default="data/stack/context.parquet")
    c.add_argument("--max-season", type=int, default=FROZEN_SEASON - 1)
    args = ap.parse_args()
    if args.cmd == "season-avg":
        populate_season_avg(args.db, args.stats, args.oof, args.max_season)
    else:
        ctx = basic_context(load_history(args.db, args.max_season))
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        ctx.to_parquet(args.out)
        print(f"wrote {len(ctx)} context rows -> {args.out}")


if __name__ == "__main__":
    main()
