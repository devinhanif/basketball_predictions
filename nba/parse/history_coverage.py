"""Coverage of staged (or loaded) seasons against the structure of the NBA schedule.

There is no stored schedule for 2015-16 .. 2018-19, so the "schedule" here is its rules: a 30-team
82-game regular season (1,230 games, game ids ``0021YYnnnnn`` numbered 1..1230, 41 home and 41 away
per team, no duplicate pairings beyond the schedule) and best-of-7 playoff series (game id
``004YY0RSSG``: round R, series SS... game G; four rounds of 8, 4, 2 and 1 series; a series ends at
four wins). Anything the rules say should exist and does not is listed, not repaired.

    uv run python -m nba.parse.history_coverage --stage data/history/nbastats_stage \\
        --season 2015 --out reports/history_2015_2019_coverage.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import polars as pl


def regular_season(games: pl.DataFrame, season: int) -> dict[str, Any]:
    reg = games.filter(pl.col("game_id").str.slice(2, 1) == "2")
    nums = sorted(int(g[5:]) for g in reg["game_id"].to_list())
    missing = [n for n in range(1, 1231) if n not in set(nums)]
    long = pl.concat(
        [
            reg.select("game_id", pl.col("home_team").alias("team"), pl.lit(1).alias("home")),
            reg.select("game_id", pl.col("away_team").alias("team"), pl.lit(0).alias("home")),
        ]
    )
    per_team = long.group_by("team").agg(pl.len().alias("n"), pl.col("home").sum().alias("home"))
    off = per_team.filter((pl.col("n") != 82) | (pl.col("home") != 41))
    return {
        "games": reg.height,
        "expected": 1230,
        "missing_game_numbers": missing,
        "teams": per_team.height,
        "teams_not_82_games_or_41_home": off.sort("team").to_dicts(),
        "first_date": str(reg["game_date"].min()),
        "last_date": str(reg["game_date"].max()),
        "season": season,
    }


def playoffs(games: pl.DataFrame) -> dict[str, Any]:
    po = games.filter(pl.col("game_id").str.slice(2, 1) == "4").with_columns(
        pl.col("game_id").str.slice(7, 1).cast(pl.Int64).alias("rnd"),
        pl.col("game_id").str.slice(8, 1).cast(pl.Int64).alias("ser"),
        pl.col("game_id").str.slice(9, 1).cast(pl.Int64).alias("g"),
        (pl.col("home_pts") > pl.col("away_pts")).alias("home_win"),
    )
    series = po.group_by("rnd", "ser").agg(
        pl.len().alias("games"),
        pl.col("g").min().alias("gmin"),
        pl.col("g").max().alias("gmax"),
        pl.col("home_team").first().alias("t_a"),
        pl.col("away_team").first().alias("t_b"),
    )
    problems: list[str] = []
    per_round = {
        int(r["rnd"]): r["n"]
        for r in po.group_by("rnd").agg(pl.col("ser").n_unique().alias("n")).to_dicts()
    }
    for rnd, want in ((1, 8), (2, 4), (3, 2), (4, 1)):
        if per_round.get(rnd) != want:
            problems.append(f"round {rnd}: {per_round.get(rnd)} series, expected {want}")
    for r in series.to_dicts():
        if r["gmin"] != 1 or r["gmax"] != r["games"]:
            problems.append(f"series {r['rnd']}{r['ser']}: game numbers not 1..{r['games']}")
        if not 4 <= r["games"] <= 7:
            problems.append(f"series {r['rnd']}{r['ser']}: {r['games']} games")
    # each series must end at exactly four wins for one team and fewer for the other
    wins: dict[tuple[int, int, int], int] = {}
    for g in po.iter_rows(named=True):
        w = g["home_team"] if g["home_win"] else g["away_team"]
        k = (g["rnd"], g["ser"], w)
        wins[k] = wins.get(k, 0) + 1
    by_series: dict[tuple[int, int], list[int]] = {}
    for (rnd, ser, _), n in wins.items():
        by_series.setdefault((rnd, ser), []).append(n)
    for (rnd, ser), ns in sorted(by_series.items()):
        if max(ns) != 4 or (len(ns) == 2 and min(ns) > 3):
            problems.append(f"series {rnd}{ser}: win counts {sorted(ns)} (final scores)")
    return {
        "games": po.height,
        "series": series.height,
        "series_per_round": per_round,
        "games_per_series": sorted(series["games"].to_list()),
        "structure_problems": problems,
    }


def per_game_sanity(stage: Path) -> dict[str, Any]:
    games = pl.read_parquet(stage / "games.parquet")
    box = pl.read_parquet(stage / "box.parquet")
    shots = pl.read_parquet(stage / "shots.parquet")
    poss = pl.read_parquet(stage / "possessions.parquet")
    periods = poss.group_by("game_id").agg(pl.col("period").max().alias("p"))
    short = periods.filter(pl.col("p") < 4)["game_id"].to_list()
    totals = games.with_columns((pl.col("home_pts") + pl.col("away_pts")).alias("t"))
    odd = totals.filter((pl.col("t") < 130) | (pl.col("t") > 330))["game_id"].to_list()
    players = (
        box.filter(pl.col("minutes") > 0).group_by("game_id", "team_id").agg(pl.len().alias("n"))
    )
    thin = players.filter(pl.col("n") < 5)["game_id"].unique().to_list()
    no_shots = sorted(set(games["game_id"]) - set(shots["game_id"].unique()))
    return {
        "games_fewer_than_4_periods": short,
        "games_total_points_outside_130_330": odd,
        "games_team_with_fewer_than_5_players_with_minutes": thin,
        "games_without_any_shot_row": no_shots,
        "box_rows_per_game_mean": box.height / games.height,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.parse.history_coverage")
    ap.add_argument("--stage", type=Path, required=True)
    ap.add_argument("--season", type=int, action="append", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    out: dict[str, Any] = {}
    for s in args.season:
        games = pl.read_parquet(args.stage / str(s) / "games.parquet")
        out[str(s)] = {
            "regular": regular_season(games, s),
            "playoffs": playoffs(games),
            "sanity": per_game_sanity(args.stage / str(s)),
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
