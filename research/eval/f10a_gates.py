# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-any-return,unused-ignore"
"""F10A_RIVALRY minimum-data gate (docs/prereg/F10A_RIVALRY.md; DRAFT). Counts only: no outcome by flag.

    .venv/bin/python -m research.eval.f10a_gates

Questionable player-games = players whose status in the LATEST official-report snapshot with
``as_of <= real tip - 60 min`` is "questionable" (production rule, ``latest_pretip_flagged``), regular-season
games of seasons 2022-2024. Printed per season: counts by rivalry flag, report coverage by flag (kill criterion 1),
national-TV share by flag (the participation-policy confound), the overall non-play count (power only; never by flag).
Read-only: ``nba.duckdb`` opened ``read_only=True``, ``season <= 2024`` in SQL. Output ``reports/prereg_f10a/gates.json``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from nba.features.game_tipoff import attach_real_tips, build_game_tipoff, tip_coverage
from nba.features.injury_report import (
    ReportTriggerConfig,
    latest_pretip_flagged,
    load_report_rows,
)
from research.features.rivalry import flags

MAX_SEASON = 2024
SEASONS = (2022, 2023, 2024)
OUT = Path("reports/prereg_f10a/gates.json")
FLAGS = ("div_rival", "hist_rival", "any_rival")
MIN_REPORT_SEASON_ROWS = 1000  # draft rule: below this the report season is descriptive


def build(con: duckdb.DuckDBPyConnection) -> tuple[pl.DataFrame, dict[str, Any]]:
    games = con.execute(
        f"""SELECT game_id, game_date, season, home_team, away_team, national_tv IS NOT NULL AND national_tv <> '' AS ntv
            FROM games WHERE season BETWEEN 2022 AND {MAX_SEASON} AND game_id LIKE '002%'"""
    ).pl()
    cfg = ReportTriggerConfig(statuses=("questionable",), tip_source="real")
    rows = load_report_rows(con, cfg, game_ids=games["game_id"].to_list())
    rows = attach_real_tips(rows)
    flagged, used = latest_pretip_flagged(rows, replace(cfg))
    # team of each player in the game: the box row if present, else the latest earlier box row
    pgs = con.execute(
        f"""SELECT p.game_id, p.player_id, p.team_id, p.minutes, g.game_date
            FROM player_game_stats p JOIN games g USING (game_id) WHERE g.season BETWEEN 2022 AND {MAX_SEASON}"""
    ).pl()
    box = {
        (r[0], r[1]): (r[2], r[3])
        for r in pgs.select("game_id", "player_id", "team_id", "minutes").iter_rows()
    }
    last_team: dict[int, list[tuple[Any, int]]] = defaultdict(list)
    for _gid, pid, tid, d in (
        pgs.sort("game_date", "game_id")
        .select("game_id", "player_id", "team_id", "game_date")
        .iter_rows()
    ):
        last_team[pid].append((d, tid))
    ginfo = {r["game_id"]: r for r in games.iter_rows(named=True)}
    out: list[dict[str, Any]] = []
    unresolved = 0
    for gid, pids in flagged.items():
        g = ginfo.get(gid)
        if g is None:
            continue
        for pid in pids:
            team: int | None = None
            played: bool | None = None
            if (gid, pid) in box:
                team = box[(gid, pid)][0]
                m = box[(gid, pid)][1]
                played = bool(m is not None and m > 0)
            else:
                prior = [t for d, t in last_team.get(pid, []) if d < g["game_date"]]
                team = prior[-1] if prior else None
                played = False if team is not None else None
            if team not in (g["home_team"], g["away_team"]):
                unresolved += 1
                continue
            opp = g["away_team"] if team == g["home_team"] else g["home_team"]
            fl = flags(team, opp, g["game_date"])
            out.append(
                {
                    "game_id": gid,
                    "player_id": pid,
                    "season": g["season"],
                    "ntv": g["ntv"],
                    "played": played,
                    **fl,
                }
            )
    df = pl.DataFrame(out)
    cov = tip_coverage(games.select("game_id"), build_game_tipoff())
    meta = {
        "n_games": games.height,
        "games_with_usable_report": len(used),
        "unresolved_team": unresolved,
        "tip_coverage": cov,
    }
    return df, meta


def summarize(df: pl.DataFrame, games: pl.DataFrame, used_games: set[str]) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for s in SEASONS:
        d = df.filter(pl.col("season") == s)
        cell: dict[str, Any] = {
            "questionable_player_games": d.height,
            "games_with_rows": d["game_id"].n_unique(),
        }
        for f in FLAGS:
            on = d.filter(pl.col(f))
            cell[f] = {
                "n_rows": on.height,
                "n_games": on["game_id"].n_unique(),
                "n_players": on["player_id"].n_unique(),
                "share": on.height / d.height if d.height else None,
                "ntv_share": float(on["ntv"].mean()) if on.height else None,
            }
            off = d.filter(~pl.col(f))
            cell[f]["ntv_share_off"] = float(off["ntv"].mean()) if off.height else None
        cell["non_play_events_overall"] = int((~d["played"]).sum())
        cell["players"] = d["player_id"].n_unique()
        res[str(s)] = cell
        # report coverage by game-level rivalry flag (kill criterion 1)
        g = games.filter(pl.col("season") == s)
        cov: dict[str, Any] = {}
        for f in FLAGS:
            gf = [
                flags(r["home_team"], r["away_team"], r["game_date"])[f]
                for r in g.select("home_team", "away_team", "game_date").iter_rows(named=True)
            ]
            gg = g.with_columns(
                pl.Series("f", gf), pl.col("game_id").is_in(list(used_games)).alias("has")
            )
            cov[f] = {
                "rival_games": int(gg["f"].sum()),
                "coverage_rival": float(gg.filter(pl.col("f"))["has"].mean())
                if gg["f"].sum()
                else None,
                "coverage_other": float(gg.filter(~pl.col("f"))["has"].mean()),
            }
        res[str(s)]["report_coverage"] = cov
    return res


def mde_play_rate(d: pl.DataFrame, flag: str, cluster: str) -> float | None:
    """80%-power minimum detectable play-rate difference (percentage points x 100 = fraction) for ``flag``.

    Cluster-robust (CR1) SE of the OLS slope of ``played`` on the flag, clustered by ``cluster``; MDE = 2.8 x SE.
    Only the SE is returned (it depends on the cluster structure and residual variance, never on the flag-level
    difference, so no outcome-by-flag number is exposed)."""
    if d.height < 20 or d[flag].sum() < 5:
        return None
    x = d[flag].cast(pl.Float64).to_numpy()
    y = d["played"].cast(pl.Float64).to_numpy()
    xm, ym = x.mean(), y.mean()
    sxx = float(((x - xm) ** 2).sum())
    b = float(((x - xm) * (y - ym)).sum() / sxx)
    e = y - ym - b * (x - xm)
    u = (x - xm) * e
    codes = d[cluster].to_numpy()
    g = pl.DataFrame({"c": codes, "u": u}).group_by("c").agg(pl.col("u").sum().alias("s"))
    n_c, n = g.height, d.height
    meat = float((g["s"] ** 2).sum())
    se = (meat * (n_c / (n_c - 1)) * ((n - 1) / (n - 2))) ** 0.5 / sxx
    return 2.8 * se


def main() -> int:
    con = duckdb.connect("nba.duckdb", read_only=True)
    try:
        df, meta = build(con)
        games = con.execute(
            f"SELECT game_id, game_date, season, home_team, away_team FROM games WHERE season BETWEEN 2022 AND {MAX_SEASON} AND game_id LIKE '002%'"
        ).pl()
        cfg = ReportTriggerConfig(statuses=("questionable",), tip_source="real")
        rows = attach_real_tips(load_report_rows(con, cfg, game_ids=games["game_id"].to_list()))
        _, used = latest_pretip_flagged(rows, cfg)
    finally:
        con.close()
    res = summarize(df, games, set(used))
    for label, seasons in (
        ("2023", (2023,)),
        ("2024", (2024,)),
        ("2022-2024", SEASONS),
        ("2023-2024", (2023, 2024)),
    ):
        d = df.filter(pl.col("season").is_in(list(seasons)))
        res.setdefault("mde_play_rate_diff_80pct", {})[label] = {
            f"{f}/{c}": mde_play_rate(d, f, c) for f in FLAGS for c in ("game_id", "player_id")
        }
    res["meta"] = meta
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
