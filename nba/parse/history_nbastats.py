"""Build history tables for a season from downloaded stats.nba.com-format play-by-play.

Pure CPU and file I/O: nothing here opens a DuckDB file. ``build_season`` turns one season's
``nbastats`` (+ ``nbastats_po``) and ``shotdetail`` CSVs into staging parquet (games, derived player
box, stints, possessions with lineups, shots) and writes each game's play-by-play to
``data/history/pbp/<game_id>.parquet`` in the V3-normalised schema (never overwriting an existing
file).
``load_stage`` (``nba.parse.history_nbastats_load``) writes the staging files to the history DB.

The parsers are the existing ones (``parse_possessions``, ``track_lineups_with_report``,
``attach_lineups_to_possessions``); only the input mapping and the box derivation are new
(``nba.ingest.nbastats_pbp``, ``nba.ingest.nbastats_box``).

    uv run python -m nba.parse.history_nbastats --extracted <dir> --season 2018 --stage <dir>

Resumable per season (a finished season's ``summary.json`` is kept; ``--force`` rebuilds staging).
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

import polars as pl

from nba.ingest.nbastats_box import derive_box, derive_starters
from nba.ingest.nbastats_pbp import (
    duplicate_keys,
    infer_home_team,
    load_subtype_map,
    read_nbastats,
    to_v3,
)
from nba.parse.lineups import (
    attach_lineups_to_possessions,
    stint_minutes,
    track_lineups_with_report,
)
from nba.parse.possessions import parse_possessions

SHOT_COLUMNS = [
    "game_id",
    "game_event_id",
    "player_id",
    "team_id",
    "period",
    "clock_seconds",
    "loc_x",
    "loc_y",
    "shot_distance",
    "shot_zone_basic",
    "shot_zone_area",
    "shot_zone_range",
    "action_type",
    "shot_type",
    "made",
]


def read_shots(paths: list[Path]) -> pl.DataFrame:
    raw = pl.concat([pl.read_csv(p, infer_schema_length=0, null_values=["", "NaN"]) for p in paths])
    return raw.unique(maintain_order=True)


def shots_table(raw: pl.DataFrame) -> pl.DataFrame:
    df = raw.select(
        pl.col("GAME_ID").cast(pl.Int64).map_elements(lambda x: f"{x:010d}", return_dtype=pl.Utf8),
        pl.col("GAME_EVENT_ID").cast(pl.Int64).alias("game_event_id"),
        pl.col("PLAYER_ID").cast(pl.Int64).alias("player_id"),
        pl.col("TEAM_ID").cast(pl.Int64).alias("team_id"),
        pl.col("PERIOD").cast(pl.Int64).alias("period"),
        (
            pl.col("MINUTES_REMAINING").cast(pl.Int64) * 60
            + pl.col("SECONDS_REMAINING").cast(pl.Int64)
        ).alias("clock_seconds"),
        pl.col("LOC_X").cast(pl.Int64).alias("loc_x"),
        pl.col("LOC_Y").cast(pl.Int64).alias("loc_y"),
        pl.col("SHOT_DISTANCE").cast(pl.Int64).alias("shot_distance"),
        pl.col("SHOT_ZONE_BASIC").alias("shot_zone_basic"),
        pl.col("SHOT_ZONE_AREA").alias("shot_zone_area"),
        pl.col("SHOT_ZONE_RANGE").alias("shot_zone_range"),
        pl.col("ACTION_TYPE").alias("action_type"),
        pl.col("SHOT_TYPE").alias("shot_type"),
        (pl.col("SHOT_MADE_FLAG").cast(pl.Int64) == 1).alias("made"),
    ).rename({"GAME_ID": "game_id"})
    return df.select(SHOT_COLUMNS)


def build_season(
    extracted: Path,
    season: int,
    stage: Path,
    history_dir: Path,
    *,
    write_pbp: bool = True,
    max_games: int | None = None,
    progress_every: int = 200,
) -> dict[str, Any]:
    names = [f"nbastats_{season}", f"nbastats_po_{season}"]
    shot_names = [f"shotdetail_{season}", f"shotdetail_po_{season}"]
    pbp_raw = read_nbastats([extracted / n / f"{n}.csv" for n in names])
    shots_raw = read_shots([extracted / n / f"{n}.csv" for n in shot_names])
    subtypes = load_subtype_map()
    dates = (
        shots_raw.group_by("GAME_ID")
        .agg(pl.col("GAME_DATE").mode().first().alias("d"))
        .with_columns(
            pl.col("GAME_ID")
            .cast(pl.Int64)
            .map_elements(lambda x: f"{x:010d}", return_dtype=pl.Utf8)
        )
    )
    date_of = dict(zip(dates["GAME_ID"], dates["d"], strict=True))
    dist_of: dict[int, dict[int, int]] = {}
    for gid_s, ev_s, dist_s in shots_raw.select(
        "GAME_ID", "GAME_EVENT_ID", "SHOT_DISTANCE"
    ).iter_rows():
        if gid_s is not None and ev_s is not None and dist_s is not None:
            dist_of.setdefault(int(gid_s), {})[int(ev_s)] = int(dist_s)
    parts = pbp_raw.partition_by("GAME_ID", as_dict=True)
    keys = sorted(parts, key=lambda k: int(k[0]))
    if max_games:
        keys = keys[:max_games]

    games: list[dict[str, Any]] = []
    boxes: list[pl.DataFrame] = []
    stints_all: list[pl.DataFrame] = []
    poss_all: list[pl.DataFrame] = []
    cnt: Counter[str] = Counter()
    unmapped: dict[str, int] = {}
    problems: list[str] = []
    t0 = time.monotonic()
    pbp_dir = history_dir / "pbp"
    pbp_dir.mkdir(parents=True, exist_ok=True)
    for i, k in enumerate(keys):
        g = parts[k]
        rows = g.to_dicts()
        v3 = to_v3(g, subtypes, unmapped, dist_of.get(int(k[0])))
        gid = v3["game_id"][0]
        if write_pbp:
            path = pbp_dir / f"{gid}.parquet"
            if path.exists():
                cnt["pbp_existing_kept"] += 1
            else:
                v3.write_parquet(path)
        home = infer_home_team(rows)
        teams = {int(r["PLAYER1_TEAM_ID"]) for r in rows if r["PLAYER1_TEAM_ID"] not in (None, "0")}
        away = next(iter(teams - {home}), 0)
        scored = v3.filter(pl.col("score_home") != "")
        last = scored.tail(1)
        hp = int(last["score_home"][0]) if last.height else None
        ap = int(last["score_away"][0]) if last.height else None
        d = date_of.get(gid)
        games.append(
            {
                "game_id": gid,
                "game_date": f"{d[:4]}-{d[4:6]}-{d[6:]}" if d else None,
                "season": season,
                "home_team": home,
                "away_team": away,
                "home_pts": hp,
                "away_pts": ap,
            }
        )
        if not home or not away or hp is None:
            problems.append(f"{gid}: home/away/score not resolved")
        poss = parse_possessions(v3)
        starters = derive_starters(rows, subtypes)
        minutes: dict[int, float] = {}
        if starters is None:
            cnt["no_starters"] += 1
            problems.append(f"{gid}: no starters")
            poss = attach_lineups_to_possessions(poss, pl.DataFrame())
        else:
            roster: dict[int, list[int]] = {}
            for r in rows:
                for pk, tk in (
                    ("PLAYER1_ID", "PLAYER1_TEAM_ID"),
                    ("PLAYER2_ID", "PLAYER2_TEAM_ID"),
                ):
                    if r[pk] and r[tk] and r[tk] != "0":
                        roster.setdefault(int(r[tk]), [])
                        if int(r[pk]) not in roster[int(r[tk])]:
                            roster[int(r[tk])].append(int(r[pk]))
            stints, rep = track_lineups_with_report(v3, starters, roster)
            cnt["unresolved_subs"] += len(rep.unresolved)
            cnt["desync_subs"] += len(rep.desync)
            cnt["open_filled"] += len(rep.open_filled)
            cnt["q1_mismatch"] += len(rep.q1_mismatch)
            minutes = {
                int(p): float(m)
                for p, m in zip(
                    stint_minutes(stints)["player_id"],
                    stint_minutes(stints)["minutes"],
                    strict=True,
                )
            }
            poss = attach_lineups_to_possessions(poss, stints)
            stints_all.append(stints)
        try:
            boxes.append(derive_box(rows, subtypes, minutes, starters))
        except KeyError as exc:
            problems.append(f"{gid}: box {exc}")
            boxes.append(derive_box(rows, subtypes, {}, starters))
        poss_all.append(poss)
        cnt["games"] += 1
        if progress_every and (i + 1) % progress_every == 0:
            print(
                f"PROGRESS build {season} {i + 1}/{len(keys)} "
                f"{(i + 1) / (time.monotonic() - t0):.1f} games/s",
                flush=True,
            )

    stage.mkdir(parents=True, exist_ok=True)
    game_df = pl.DataFrame(games).with_columns(
        pl.col("game_date").str.to_date("%Y-%m-%d", strict=False)
    )
    game_df.write_parquet(stage / "games.parquet")
    pl.concat(boxes).write_parquet(stage / "box.parquet")
    if stints_all:
        pl.concat(stints_all).write_parquet(stage / "stints.parquet")
    pl.concat(poss_all).write_parquet(stage / "possessions.parquet")
    shots = shots_table(shots_raw)
    shot_dups = shots.height - shots.unique(subset=["game_id", "game_event_id"]).height
    shots = shots.unique(subset=["game_id", "game_event_id"], keep="first", maintain_order=True)
    shots = shots.filter(pl.col("game_id").is_in(game_df["game_id"].implode()))
    shots.write_parquet(stage / "shots.parquet")
    summary = {
        "season": season,
        "games": game_df.height,
        "games_regular": int(game_df.filter(pl.col("game_id").str.slice(2, 1) == "2").height),
        "games_playoff": int(game_df.filter(pl.col("game_id").str.slice(2, 1) == "4").height),
        "games_other": int(
            game_df.filter(~pl.col("game_id").str.slice(2, 1).is_in(["2", "4"])).height
        ),
        "games_without_date": int(game_df["game_date"].null_count()),
        "pbp_rows": pbp_raw.height,
        "raw_duplicate_keys_after_dedupe": duplicate_keys(pbp_raw),
        "shots_rows": shots.height,
        "shot_duplicate_keys_dropped": shot_dups,
        "box_rows": sum(b.height for b in boxes),
        "stint_rows": sum(s.height for s in stints_all),
        "possession_rows": sum(p.height for p in poss_all),
        "adapter_unmapped": unmapped,
        "counters": dict(cnt),
        "problems": problems[:50],
        "n_problems": len(problems),
        "seconds": round(time.monotonic() - t0, 1),
    }
    (stage / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.parse.history_nbastats")
    ap.add_argument("--extracted", type=Path, required=True)
    ap.add_argument("--season", type=int, action="append", required=True)
    ap.add_argument("--stage", type=Path, required=True, help="staging dir (one subdir per season)")
    ap.add_argument("--history", type=Path, default=Path("data/history"))
    ap.add_argument("--no-pbp", action="store_true", help="do not write per-game pbp parquet")
    ap.add_argument("--max-games", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    for s in args.season:
        out = args.stage / str(s)
        if (out / "summary.json").exists() and not args.force:
            print(f"season {s}: staged, skipping (use --force)")
            continue
        summ = build_season(
            args.extracted,
            s,
            out,
            args.history,
            write_pbp=not args.no_pbp,
            max_games=args.max_games,
        )
        print(json.dumps({k: v for k, v in summ.items() if k != "problems"}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
