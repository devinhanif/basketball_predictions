"""Impact of the report re-stamp on the T-60 OUT snapshot, seasons <= 2024 only, no outcomes.

Reads two databases read-only (the stored one and its re-stamped copy), builds the training-rule
snapshot per game (``latest_pretip_flagged``, real tips, tip-60) on each, and counts what moves.
The player-game universe is (game_id, player_id) from ``player_game_stats`` -- keys only, no
minutes or stats. Season 2025 is reported as a changed-row COUNT elsewhere
(``restamp_reports.change_table``) and is never loaded here.

    uv run python -m nba.data.restamp_impact --old nba.duckdb --new data/rehearsal/stampfix.duckdb
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from nba.data.restamp_reports import build_plan
from nba.features.game_tipoff import attach_real_tips, build_game_tipoff
from nba.features.injury_report import (
    ReportTriggerConfig,
    latest_pretip_flagged,
    load_report_rows,
)

MAX_SEASON = 2024


def _snapshot(
    con: duckdb.DuckDBPyConnection, tips: pl.DataFrame, cfg: ReportTriggerConfig
) -> tuple[dict[str, set[int]], dict[str, Any]]:
    rows = load_report_rows(con, cfg)
    ids = [
        r[0]
        for r in con.execute("SELECT game_id FROM games WHERE season <= ?", [MAX_SEASON]).fetchall()
    ]
    rows = rows.filter(pl.col("game_id").is_in(ids))
    rows = attach_real_tips(rows, tips=tips)
    flagged, used = latest_pretip_flagged(rows, cfg)
    return flagged, used


def impact(
    old: duckdb.DuckDBPyConnection,
    new: duckdb.DuckDBPyConnection,
    stamp_map: dict[Any, Any],
) -> dict[str, Any]:
    cfg = ReportTriggerConfig(tip_source="real", lead_minutes=60)
    tips = build_game_tipoff(max_season=MAX_SEASON)
    f0, u0 = _snapshot(old, tips, cfg)
    f1, u1 = _snapshot(new, tips, cfg)
    games = pl.DataFrame(
        old.execute("SELECT game_id, season FROM games WHERE season <= ?", [MAX_SEASON]).fetchall(),
        schema={"game_id": pl.Utf8, "season": pl.Int64},
        orient="row",
    )
    univ = pl.DataFrame(
        old.execute(
            "SELECT p.game_id, p.player_id, g.season FROM player_game_stats p "
            "JOIN games g USING (game_id) WHERE g.season <= ?",
            [MAX_SEASON],
        ).fetchall(),
        schema={"game_id": pl.Utf8, "player_id": pl.Int64, "season": pl.Int64},
        orient="row",
    )
    out: dict[str, Any] = {}
    for season in sorted(games["season"].unique().to_list()):
        gids = games.filter(pl.col("season") == season)["game_id"].to_list()
        n_games = len(gids)
        has0 = {g for g in gids if g in u0}
        has1 = {g for g in gids if g in u1}
        both = has0 & has1
        stamp_changed = {g for g in both if u0[g] != u1[g]}
        # the snapshot is a different REPORT (not just the same report re-stamped)
        out_changed = {g for g in both if f0[g] != f1[g]}
        u = univ.filter(pl.col("season") == season)
        pg = u.height

        def flag(f: dict[str, set[int]], g: str, p: int) -> bool:
            return p in f.get(g, set())

        rows_ = u.select("game_id", "player_id").iter_rows()
        n_out_flip = n_out_old = n_out_new = n_rep_flip = 0
        for g, p in rows_:
            a, b = flag(f0, g, p), flag(f1, g, p)
            n_out_old += a
            n_out_new += b
            n_out_flip += a != b
            n_rep_flip += (g in u0) != (g in u1)
        out[str(season)] = {
            "games": n_games,
            "player_games": pg,
            "games_with_report_old": len(has0),
            "games_with_report_new": len(has1),
            "games_report_lost": len(has0 - has1),
            "games_report_gained": len(has1 - has0),
            "games_snapshot_is_a_different_report": len(
                {g for g in both if stamp_map.get(u0[g], u0[g]) != u1[g]}
            ),
            "games_out_set_changed": len(out_changed),
            "games_snapshot_stamp_changed": len(stamp_changed),
            "player_games_out_old": int(n_out_old),
            "player_games_out_new": int(n_out_new),
            "player_games_out_flag_flipped": int(n_out_flip),
            "player_games_has_report_flipped": int(n_rep_flip),
        }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", type=Path, default=Path("nba.duckdb"))
    ap.add_argument("--new", type=Path, required=True)
    ap.add_argument("--raw", type=Path, default=Path("data/availability_official"))
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args(argv)
    o = duckdb.connect(str(a.old), read_only=True)
    n = duckdb.connect(str(a.new), read_only=True)
    try:
        plan = build_plan(a.raw)
        stamp_map = {r[0]: r[1] for r in plan.frame.select("old_stamp", "new_stamp").iter_rows()}
        res = impact(o, n, stamp_map)
    finally:
        o.close()
        n.close()
    print(json.dumps(res, indent=2))
    if a.json:
        a.json.write_text(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
