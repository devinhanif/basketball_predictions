"""Shot-chart coordinates mapped to play-by-play: the ``shot_events`` table.

Definition: one row per shot-chart attempt (``shots`` table, nba_api ShotChartDetail), joined to the
play-by-play row that records the same attempt, keyed ``(game_id, action_number)``.

Join rule (the part that matters): ``shots.game_event_id`` equals the PBP ``action_number`` of the
*shot* row. A block is logged as a second PBP row that REUSES the shot's ``action_number`` (team =
the blocker's, player = the blocker, description "<Name> BLOCK"), so a naive join fans out and
attributes the shot to the blocker (the 23 "player mismatches" in the first 5-game check were all
blocks). The PBP side is therefore restricted to ``is_field_goal = 1`` rows, which carry
``action_type`` in (Made Shot, Missed Shot) and are unique per ``(game_id, action_number)``.

Sources: ``shots`` (post-game ShotChartDetail), ``data/pbp/*.parquet`` (post-game), ``games``.
As-of window: POST-GAME. ``known_at`` is naive UTC, the game's date + 1 day 12:00, after the last
tip of any NBA night (the latest finish is about 05:30 UTC the next day) and before the next slate
tips. A feature for game G may use only rows with ``known_at`` before G's tip (prior games).
Imputation: none. Unmatched shots are kept with ``pbp_matched = false`` and NULL PBP fields; they
are counted, never dropped or filled. ``player_match`` is NULL when there is no PBP row.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import duckdb
import polars as pl

PBP_DIR = Path("data/pbp")

_FG_ACTIONS = ("Made Shot", "Missed Shot")

SHOT_EVENT_COLUMNS = [
    "game_id",
    "action_number",
    "season",
    "game_date",
    "team_id",
    "player_id",
    "shot_player_id",
    "pbp_player_id",
    "player_match",
    "pbp_matched",
    "made",
    "pbp_made",
    "made_match",
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
    "pbp_description",
    "known_at",
]


def load_pbp_shot_rows(game_ids: Iterable[str], pbp_dir: Path = PBP_DIR) -> pl.DataFrame:
    """The PBP field-goal rows (made/missed shots only; block rows excluded) for ``game_ids``."""
    frames = []
    for gid in sorted(set(game_ids)):
        f = pbp_dir / f"{gid}.parquet"
        if f.exists():
            frames.append(
                pl.read_parquet(
                    f,
                    columns=[
                        "game_id",
                        "action_number",
                        "team_id",
                        "player_id",
                        "action_type",
                        "description",
                        "is_field_goal",
                    ],
                )
            )
    if not frames:
        return pl.DataFrame(
            schema={
                "game_id": pl.String,
                "action_number": pl.Int64,
                "team_id": pl.Int64,
                "player_id": pl.Int64,
                "action_type": pl.String,
                "description": pl.String,
                "is_field_goal": pl.Int64,
            }
        )
    df = pl.concat(frames, how="vertical_relaxed")
    return df.filter((pl.col("is_field_goal") == 1) & pl.col("action_type").is_in(_FG_ACTIONS))


def build_shot_events(
    shots: pl.DataFrame, pbp_fg: pl.DataFrame, games: pl.DataFrame
) -> pl.DataFrame:
    """Join shots to PBP shot rows. ``pbp_fg`` must already be the field-goal rows only.

    Raises if ``pbp_fg`` has two rows for one ``(game_id, action_number)`` (a fan-out would
    silently double shots) or if ``shots`` repeats a key.
    """
    if shots.select("game_id", "game_event_id").is_duplicated().any():
        raise ValueError("shots has duplicate (game_id, game_event_id)")
    if pbp_fg.select("game_id", "action_number").is_duplicated().any():
        raise ValueError("pbp field-goal rows have duplicate (game_id, action_number)")
    pbp = pbp_fg.select(
        "game_id",
        pl.col("action_number"),
        pl.col("player_id").alias("pbp_player_id"),
        (pl.col("action_type") == "Made Shot").alias("pbp_made"),
        pl.col("description").alias("pbp_description"),
        pl.lit(True).alias("pbp_matched"),
    )
    g = games.select("game_id", "season", "game_date")
    out = (
        shots.rename({"game_event_id": "action_number", "player_id": "shot_player_id"})
        .join(pbp, on=["game_id", "action_number"], how="left")
        .join(g, on="game_id", how="left")
        .with_columns(
            pl.col("pbp_matched").fill_null(False),
            (pl.col("shot_player_id") == pl.col("pbp_player_id")).alias("player_match"),
            (pl.col("made") == pl.col("pbp_made")).alias("made_match"),
            # Resolution rule: the shot chart's player when it agrees (always, once blocks are
            # excluded); otherwise PBP's, which names the shooter in the description. Reviewed
            # against box FGA per player in ``player_fga_agreement``.
            pl.coalesce("pbp_player_id", "shot_player_id").alias("player_id"),
            (pl.col("game_date").cast(pl.Datetime("us")) + pl.duration(days=1, hours=12)).alias(
                "known_at"
            ),
        )
    )
    return out.select(SHOT_EVENT_COLUMNS).sort("game_id", "action_number")


def materialize(con: duckdb.DuckDBPyConnection, pbp_dir: Path = PBP_DIR) -> int:
    """Rebuild table ``shot_events`` from ``shots`` + PBP parquet. Needs a WRITE connection."""
    shots = con.execute("SELECT * FROM shots").pl()
    games = con.execute("SELECT game_id, season, game_date FROM games").pl()
    pbp = load_pbp_shot_rows(shots["game_id"].unique().to_list(), pbp_dir)
    df = build_shot_events(shots, pbp, games)
    con.execute("DROP TABLE IF EXISTS shot_events")
    con.execute(
        """
        CREATE TABLE shot_events (
            game_id VARCHAR, action_number INT, season INT, game_date DATE,
            team_id INT, player_id INT, shot_player_id INT, pbp_player_id INT,
            player_match BOOLEAN, pbp_matched BOOLEAN, made BOOLEAN, pbp_made BOOLEAN,
            made_match BOOLEAN, period INT, clock_seconds INT, loc_x INT, loc_y INT,
            shot_distance INT, shot_zone_basic VARCHAR, shot_zone_area VARCHAR,
            shot_zone_range VARCHAR, action_type VARCHAR, shot_type VARCHAR,
            pbp_description VARCHAR, known_at TIMESTAMP,
            PRIMARY KEY (game_id, action_number)
        )
        """
    )
    con.register("_se", df.to_arrow())
    con.execute("INSERT INTO shot_events SELECT * FROM _se")
    con.unregister("_se")
    return df.height


def join_audit(con: duckdb.DuckDBPyConnection) -> list[dict[str, float]]:
    """Per season: join rate, made agreement, player agreement. Read-only."""
    rows = con.execute(
        """
        SELECT season, count(*) n, avg(pbp_matched::INT) joined,
               avg(made_match::INT) FILTER (WHERE pbp_matched) made_agree,
               avg(player_match::INT) FILTER (WHERE pbp_matched) player_agree,
               sum((NOT player_match)::INT) player_mismatch
        FROM shot_events GROUP BY season ORDER BY season
        """
    ).fetchall()
    return [
        {
            "season": r[0],
            "n": r[1],
            "joined": r[2],
            "made_agree": r[3],
            "player_agree": r[4],
            "player_mismatch": r[5] or 0,
        }
        for r in rows
    ]


def fga_reconciliation_by_season(con: duckdb.DuckDBPyConnection) -> list[dict[str, float]]:
    """Shot-chart attempts vs box-score FGA per (game, team), by season. Read-only."""
    rows = con.execute(
        """
        WITH s AS (SELECT game_id, team_id, count(*) shot_fga FROM shot_events GROUP BY 1, 2),
             b AS (SELECT game_id, team_id, sum(fga) box_fga FROM player_game_stats
                   WHERE fga IS NOT NULL GROUP BY 1, 2)
        SELECT g.season, count(*), avg((shot_fga <> box_fga)::INT), avg(abs(shot_fga - box_fga)),
               avg(shot_fga - box_fga)
        FROM s JOIN b USING (game_id, team_id) JOIN games g USING (game_id)
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    return [
        {"season": r[0], "team_games": r[1], "mismatch_rate": r[2], "mae": r[3], "bias": r[4]}
        for r in rows
    ]


def player_fga_agreement(con: duckdb.DuckDBPyConnection) -> list[dict[str, float]]:
    """Per season, share of (game, player) cells where shot counts equal box FGA, under each
    attribution (shot chart's player_id vs PBP's). Read-only."""
    rows = con.execute(
        """
        WITH keys AS (SELECT game_id, shot_player_id AS player_id FROM shot_events
                      UNION SELECT game_id, pbp_player_id FROM shot_events
                      WHERE pbp_player_id IS NOT NULL),
             a AS (SELECT game_id, shot_player_id AS player_id, count(*) n FROM shot_events
                   GROUP BY 1, 2),
             p AS (SELECT game_id, pbp_player_id AS player_id, count(*) n FROM shot_events
                   WHERE pbp_player_id IS NOT NULL GROUP BY 1, 2),
             b AS (SELECT game_id, player_id, fga FROM player_game_stats WHERE fga IS NOT NULL)
        SELECT g.season, count(*), avg((coalesce(a.n, 0) = coalesce(b.fga, 0))::INT),
               avg((coalesce(p.n, 0) = coalesce(b.fga, 0))::INT)
        FROM keys k LEFT JOIN a USING (game_id, player_id) LEFT JOIN p USING (game_id, player_id)
             LEFT JOIN b USING (game_id, player_id) JOIN games g USING (game_id)
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    return [{"season": r[0], "cells": r[1], "shot_attr": r[2], "pbp_attr": r[3]} for r in rows]


def main(argv: list[str] | None = None) -> int:
    """Load cached shot parquet, rebuild ``shot_events``, print the per-season audits.

    Without ``--apply`` it audits the existing tables read-only. With ``--apply``:
        mkdir data/ops/heavy.lock && uv run python -m nba.data.shot_events --apply
        rmdir data/ops/heavy.lock
    (outside xx:15-25 / xx:45-55; run ``lsof nba.duckdb`` first; back up the DB file first).
    """
    import argparse
    import time
    from datetime import datetime

    from nba.db.connect import DEFAULT_DB_PATH, connect_with_retry
    from nba.ingest.postgame import load_source, shots_fga_reconciliation

    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    if args.apply:
        if not Path("data/ops/heavy.lock").is_dir():
            print("refusing to write: data/ops/heavy.lock is not held (mkdir it first)")
            return 2
        minute = datetime.now().minute
        if 15 <= minute < 25 or 45 <= minute < 55:
            print("refusing to write inside a pretip window (xx:15-25, xx:45-55)")
            return 3
        w = connect_with_retry(args.db)
        try:
            bk = (
                Path("data/backups")
                / f"shots_pre_load_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.parquet"
            )
            bk.parent.mkdir(parents=True, exist_ok=True)
            w.execute(f"COPY (SELECT * FROM shots) TO '{bk}' (FORMAT PARQUET)")
            print(f"backup {bk}")
            print("shots rows loaded", load_source(w, "shots"))
            print("shot_events rows", materialize(w))
        finally:
            w.close()
    r = connect_with_retry(args.db, read_only=True)
    try:
        print("fga_reconciliation", shots_fga_reconciliation(r))
        for row in join_audit(r):
            print("join", row)
        for row in fga_reconciliation_by_season(r):
            print("fga", row)
        for row in player_fga_agreement(r):
            print("player_fga", row)
    finally:
        r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
