"""QC and load of staged nbastats-derived seasons into the history DuckDB.

``qc_stage`` is pure (parquet in, dict out). ``load_stage`` is the only writer and is meant to run
once,
by the maintainer, holding ``data/ops/heavy.lock``, after a file-level backup of the history DB:

    mkdir data/ops/heavy.lock          # retry for 120 s if it exists; rmdir it afterwards
    uv run python -m nba.parse.history_nbastats_load --stage data/history/nbastats_stage \\
        --season 2015 --season 2016 --season 2017 --season 2018 --qc-out
          reports/history_2015_2019_qc.json

It never touches ``nba.duckdb``. All inserts for one run happen in one transaction; game ids that
already exist in ``games`` abort the run (nothing is overwritten). ``ingest_log`` gets one row per
game for ``pbp`` and ``boxscore_derived``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from nba.ingest.era_flags import add_history_flags, compute_era_flags, ensure_era_flag_columns
from nba.parse.lineups import clean_lineup_rate
from nba.parse.reconcile import (
    bias_outside_band,
    mean_abs_error,
    points_match_rate,
    reconcile_points,
    reconcile_possessions,
    signed_bias,
    team_box_from_player_game_stats,
)

LOADED_TABLES = ["games", "player_game_stats", "possessions", "stints", "shots", "game_era_flags"]


def _f(v: Any) -> float:
    return float(v) if v is not None else float("nan")


def qc_stage(stage_season: Path) -> dict[str, Any]:
    games = pl.read_parquet(stage_season / "games.parquet")
    box = pl.read_parquet(stage_season / "box.parquet")
    poss = pl.read_parquet(stage_season / "possessions.parquet")
    stints_path = stage_season / "stints.parquet"
    stints = pl.read_parquet(stints_path) if stints_path.exists() else pl.DataFrame()
    shots = pl.read_parquet(stage_season / "shots.parquet")

    rec_pts = reconcile_points(poss, box)
    rec_poss = reconcile_possessions(poss, team_box_from_player_game_stats(box))
    long = pl.concat(
        [
            games.select(
                "game_id", pl.col("home_team").alias("team_id"), pl.col("home_pts").alias("t")
            ),
            games.select(
                "game_id", pl.col("away_team").alias("team_id"), pl.col("away_pts").alias("t")
            ),
        ]
    )
    box_vs_final = (
        box.group_by(["game_id", "team_id"])
        .agg(pl.col("pts").sum().alias("b"))
        .join(long, on=["game_id", "team_id"], how="left")
    )
    # stint minutes per team-game should be 5 x game length (48 + 5 per OT) -> multiples of 240 +
    # 25n
    if stints.is_empty():
        tm = pl.DataFrame()
        tm_ok = float("nan")
    else:
        tm = stints.with_columns(
            ((pl.col("start_clock") - pl.col("end_clock")) / 60.0 * 5).alias("pm")
        )
        tm = tm.group_by(["game_id", "team_id"]).agg(pl.col("pm").sum())
        tm_ok = float(((tm["pm"] - 240.0).abs() % 25.0).le(1.0).mean() or 0.0)  # type: ignore[arg-type]
    shot_vs_box = (
        shots.group_by(["game_id", "team_id"])
        .agg(pl.len().alias("shot_fga"))
        .join(
            box.group_by(["game_id", "team_id"]).agg(pl.col("fga").sum().alias("box_fga")),
            on=["game_id", "team_id"],
            how="inner",
        )
    )
    return {
        "games": games.height,
        "games_missing_final_score": int(games["home_pts"].is_null().sum()),
        "games_without_date": int(games["game_date"].is_null().sum()),
        "team_games": rec_pts.height,
        "possession_points_equal_box_points": points_match_rate(rec_pts),
        "possession_points_diff_team_games": int((rec_pts["diff"] != 0).sum()),
        "box_points_equal_final_score": float((box_vs_final["b"] == box_vs_final["t"]).mean()),  # type: ignore[arg-type]
        "box_points_ne_final_score_team_games": int((box_vs_final["b"] != box_vs_final["t"]).sum()),
        "box_points_ne_final_score_games": sorted(
            box_vs_final.filter(pl.col("b") != pl.col("t"))["game_id"].unique().to_list()
        ),
        "shot_rows_ne_box_fga_games": sorted(
            shot_vs_box.filter(pl.col("shot_fga") != pl.col("box_fga"))["game_id"]
            .unique()
            .to_list()
        ),
        "possession_count_mean_abs_error": mean_abs_error(rec_poss),
        "possession_count_signed_bias": signed_bias(rec_poss),
        "possession_count_bias_outside_band": bias_outside_band(rec_poss),
        "clean_5v5_possession_rate": clean_lineup_rate(poss),
        "stint_team_minutes_consistent_share": tm_ok,
        "shot_rows_equal_box_fga_team_games": _f(
            (shot_vs_box["shot_fga"] == shot_vs_box["box_fga"]).mean()
        ),
        "shot_vs_box_fga_team_games": shot_vs_box.height,
        "games_with_no_stints": int(
            games.height - (stints["game_id"].n_unique() if not stints.is_empty() else 0)
        ),
        "box_rows": box.height,
        "box_rows_minutes_zero": int((box["minutes"] == 0).sum()),
        "box_rows_minutes_zero_with_stats": int(
            box.filter(
                (pl.col("minutes") == 0)
                & ((pl.col("fga") + pl.col("fta") + pl.col("tov") + pl.col("pf")) > 0)
            ).height
        ),
    }


def _existing_ids(con: duckdb.DuckDBPyConnection, ids: list[str]) -> list[str]:
    rows = con.execute(
        "SELECT game_id FROM games WHERE game_id IN (SELECT unnest(?))", [ids]
    ).fetchall()
    return [r[0] for r in rows]


def load_stage(
    db_path: Path,
    stage_dir: Path,
    seasons: list[int],
    history_dir: Path,
    backup_dir: Path,
    *,
    tag: str = "2015_19_ingest",
) -> dict[str, Any]:
    from nba.db.connect import connect_with_retry

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"nba_history_pre_{tag}_{ts}.duckdb"
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_path, backup)
    if backup.stat().st_size != db_path.stat().st_size:
        raise RuntimeError("backup size mismatch; aborting before any write")
    con = connect_with_retry(db_path)
    out: dict[str, Any] = {"backup": str(backup), "before": {}, "after": {}, "seasons": {}}
    try:
        ensure_era_flag_columns(con)
        out["before"] = _counts(con)
        con.execute("BEGIN")
        try:
            for season in seasons:
                d = stage_dir / str(season)
                games = pl.read_parquet(d / "games.parquet")
                clash = _existing_ids(con, games["game_id"].to_list())
                if clash:
                    raise RuntimeError(f"{len(clash)} game ids already in games, e.g. {clash[:3]}")
                for table, cols, casts in _PARQUET_TABLES:
                    f = d / f"{table_file(table)}.parquet"
                    if f.exists():
                        _insert_parquet(con, table, cols, f, casts)
                gm = games.select("game_id", "game_date", "season", "home_team").with_columns(
                    pl.col("home_team").cast(pl.Int64)
                )
                flags = add_history_flags(compute_era_flags(gm), gm)
                con.register("_flags", flags.to_arrow())
                try:
                    names = ", ".join(flags.columns)
                    con.execute(f"INSERT INTO game_era_flags ({names}) SELECT {names} FROM _flags")
                finally:
                    con.unregister("_flags")
                now = datetime.now(UTC).replace(tzinfo=None)
                rows = []
                for gid in games["game_id"].to_list():
                    rows.append(
                        ("pbp", gid, "done", str(history_dir / "pbp" / f"{gid}.parquet"), now)
                    )
                    rows.append(("boxscore_derived", gid, "done", str(d / "box.parquet"), now))
                con.executemany(
                    "INSERT INTO ingest_log (source, key, status, cache_path, fetched_at) "
                    "VALUES (?,?,?,?,?)",
                    rows,
                )
                out["seasons"][season] = games.height
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        out["after"] = _counts(con)
    finally:
        con.close()
    return out


_POSS_COLS = [
    "game_id", "poss_idx", "period", "clock_start", "clock_end", "off_team", "def_team",
    "off_players", "def_players", "score_diff", "outcome", "shooter_id", "shot_zone",
    "assister_id", "oreb", "fta", "pts", "n_oreb", "n_dreb",
]  # fmt: skip
_BOX_COLS = [
    "game_id", "player_id", "team_id", "minutes", "pts", "reb", "ast", "fg3m", "stl", "blk", "tov",
    "starter", "fgm", "fga", "fg3a", "ftm", "fta", "oreb", "dreb", "pf",
]  # fmt: skip
_GAME_COLS = ["game_id", "game_date", "season", "home_team", "away_team", "home_pts", "away_pts"]
_STINT_COLS = ["game_id", "team_id", "period", "start_clock", "end_clock", "players", "stint_idx"]
_FIVE = {"off_players": "INTEGER[5]", "def_players": "INTEGER[5]", "players": "INTEGER[5]"}
#: (table, columns, per-column casts); the parquet file is ``<stem>.parquet`` in the stage dir.
_PARQUET_TABLES: list[tuple[str, list[str], dict[str, str]]] = [
    ("games", _GAME_COLS, {}),
    ("player_game_stats", _BOX_COLS, {}),
    ("possessions", _POSS_COLS, _FIVE),
    ("stints", _STINT_COLS, _FIVE),
    ("shots", [], {}),
]
_STEM = {"player_game_stats": "box"}


def table_file(table: str) -> str:
    return _STEM.get(table, table)


def _insert_parquet(
    con: duckdb.DuckDBPyConnection, table: str, cols: list[str], path: Path, casts: dict[str, str]
) -> None:
    cols = cols or _SHOT_COLS
    sel = ", ".join(f"CAST({c} AS {casts[c]})" if c in casts else c for c in cols)
    con.execute(f"INSERT INTO {table} ({', '.join(cols)}) SELECT {sel} FROM read_parquet('{path}')")


def _counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in LOADED_TABLES:
        row = con.execute(f"SELECT count(*) FROM {t}").fetchone()
        out[t] = int(row[0]) if row else -1
    return out


_SHOT_COLS = [
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.parse.history_nbastats_load")
    ap.add_argument("--stage", type=Path, required=True)
    ap.add_argument("--season", type=int, action="append", required=True)
    ap.add_argument("--db-path", type=Path, default=Path("data/history/nba_history.duckdb"))
    ap.add_argument("--history", type=Path, default=Path("data/history"))
    ap.add_argument("--backup-dir", type=Path, default=Path("data/backups"))
    ap.add_argument("--qc-out", type=Path, default=None)
    ap.add_argument("--qc-only", action="store_true", help="QC the staged parquet; no DB access")
    args = ap.parse_args(argv)
    t0 = time.monotonic()
    qc = {str(s): qc_stage(args.stage / str(s)) for s in args.season}
    if args.qc_out:
        args.qc_out.parent.mkdir(parents=True, exist_ok=True)
        args.qc_out.write_text(json.dumps(qc, indent=1))
    print(json.dumps(qc, indent=1))
    if args.qc_only:
        return 0
    res = load_stage(args.db_path, args.stage, args.season, args.history, args.backup_dir)
    print(json.dumps(res, indent=1, default=str), f"seconds={time.monotonic() - t0:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
