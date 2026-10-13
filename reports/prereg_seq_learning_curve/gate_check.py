# ruff: noqa: E501
"""Read-only data gate for SEQ_LEARNING_CURVE (docs/prereg/SEQ_LEARNING_CURVE.md). No model, no season > 2024.

    .venv/bin/python -I reports/prereg_seq_learning_curve/gate_check.py
Writes reports/prereg_seq_learning_curve/gate.json and prints a table.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
HIST = ROOT / "data/history/nba_history.duckdb"
CUR = ROOT / "nba.duckdb"
MAXS = 2024  # holdout: season <= 2024 in SQL, always
STATS = ("pts", "reb", "ast", "fg3m", "minutes")


def q(path: Path, sql: str) -> pd.DataFrame:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql).df()
    finally:
        con.close()


def per_db(path: Path, lo: int) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    base = q(
        path,
        f"""
    SELECT g.season,
      count(DISTINCT g.game_id) games,
      count(DISTINCT s.game_id) games_with_box,
      count(*) rows_all,
      count(*) FILTER (WHERE s.minutes IS NULL OR s.minutes = 0) rows_dnp_like,
      count(*) FILTER (WHERE s.minutes > 0) rows_played,
      count(*) FILTER (WHERE s.minutes > 0 AND s.pts IS NULL) pts_null,
      count(*) FILTER (WHERE s.minutes > 0 AND s.reb IS NULL) reb_null,
      count(*) FILTER (WHERE s.minutes > 0 AND s.ast IS NULL) ast_null,
      count(*) FILTER (WHERE s.minutes > 0 AND s.fg3m IS NULL) fg3m_null,
      count(*) FILTER (WHERE s.minutes > 0 AND s.starter IS NULL) starter_null,
      count(*) FILTER (WHERE s.minutes > 0 AND s.fga IS NULL) fga_null,
      count(*) FILTER (WHERE s.minutes > 0 AND s.tov IS NULL) tov_null,
      avg(s.pts) FILTER (WHERE s.minutes > 0) pts_mean,
      avg(s.reb) FILTER (WHERE s.minutes > 0) reb_mean,
      avg(s.ast) FILTER (WHERE s.minutes > 0) ast_mean,
      avg(s.fg3m) FILTER (WHERE s.minutes > 0) fg3m_mean,
      avg(s.minutes) FILTER (WHERE s.minutes > 0) min_mean,
      avg(CASE WHEN s.pts = 0 THEN 1.0 ELSE 0.0 END) FILTER (WHERE s.minutes > 0) pts_zero_share
    FROM games g LEFT JOIN player_game_stats s USING (game_id)
    WHERE g.season >= {lo} AND g.season <= {MAXS} GROUP BY 1 ORDER BY 1""",
    )
    for r in base.to_dict("records"):
        out[int(r["season"])] = {
            k: (float(v) if isinstance(v, float) else int(v)) for k, v in r.items() if k != "season"
        }
    # starters per team-game among played rows (5 expected)
    st = q(
        path,
        f"""SELECT season, count(*) tg, count(*) FILTER (WHERE n = 5) tg_5starters FROM (
        SELECT g.season, s.game_id, s.team_id, count(*) FILTER (WHERE s.starter AND s.minutes > 0) n
        FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season>={lo} AND g.season<={MAXS} GROUP BY 1,2,3) GROUP BY 1""",
    )
    for r in st.to_dict("records"):
        out[int(r["season"])].update(
            team_games=int(r["tg"]), team_games_5_starters=int(r["tg_5starters"])
        )
    av = q(
        path,
        f"""SELECT g.season, count(*) report_rows, count(DISTINCT a.game_id) games_with_report
        FROM player_availability a JOIN games g USING (game_id)
        WHERE a.source='nba_official_report' AND g.season>={lo} AND g.season<={MAXS} GROUP BY 1""",
    )
    for s in out:
        out[s].update(report_rows=0, games_with_report=0)
    for r in av.to_dict("records"):
        out[int(r["season"])].update(
            report_rows=int(r["report_rows"]), games_with_report=int(r["games_with_report"])
        )
    pc = q(
        path,
        f"""SELECT g.season, count(DISTINCT p.game_id) games_with_poss, count(*) poss,
        count(*) FILTER (WHERE p.shooter_id IS NULL AND p.outcome IN ('FGM2','FGM3','FGA_miss')) shot_poss_no_shooter,
        count(*) FILTER (WHERE p.outcome IN ('FGM2','FGM3','FGA_miss') AND p.shot_zone IS NULL) shot_poss_no_zone
        FROM possessions p JOIN games g USING (game_id) WHERE g.season>={lo} AND g.season<={MAXS} GROUP BY 1""",
    )
    for s in out:
        out[s].update(games_with_poss=0, poss=0)
    for r in pc.to_dict("records"):
        out[int(r["season"])].update({k: int(v) for k, v in r.items() if k != "season"})
    fl = q(
        path,
        f"""SELECT f.season, count(*) flag_rows, sum(CAST(covid_bubble AS INT)) bubble, sum(CAST(no_fans AS INT)) nofans,
        sum(CAST(limited_fans AS INT)) limited, sum(CAST(shortened_season AS INT)) shortened
        {", sum(CAST(no_injury_report AS INT)) no_injury_report, sum(CAST(box_derived AS INT)) box_derived" if path == HIST else ""}
        FROM game_era_flags f WHERE f.season>={lo} AND f.season<={MAXS} GROUP BY 1""",
    )
    for r in fl.to_dict("records"):
        out[int(r["season"])].update({k: int(v) for k, v in r.items() if k != "season"})
    return out


def league_rates(path: Path, lo: int) -> dict[int, dict[str, float]]:
    """Full-season league mean per team-game of each target stat (all games in the table), descriptive; the rule's normalizer is the past-only version."""
    d = q(
        path,
        f"""SELECT season, avg(pts) pts, avg(reb) reb, avg(ast) ast, avg(fg3m) fg3m, count(*) team_games FROM (
        SELECT g.season, s.game_id, s.team_id, sum(s.pts) pts, sum(s.reb) reb, sum(s.ast) ast, sum(s.fg3m) fg3m
        FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season>={lo} AND g.season<={MAXS} GROUP BY 1,2,3) GROUP BY 1""",
    )
    return {
        int(r["season"]): {k: float(v) for k, v in r.items() if k != "season"}
        for r in d.to_dict("records")
    }


def main() -> int:
    res: dict[int, dict[str, float]] = {}
    lg = {**league_rates(HIST, 2015), **league_rates(CUR, 2022)}
    ref = lg[2022]
    for r in lg.values():
        r.update({f"c_{k}": ref[k] / r[k] for k in ("pts", "reb", "ast", "fg3m")})
    print(pd.DataFrame(lg).T.round(3).to_string())
    res.update(per_db(HIST, 2015))
    res.update(per_db(CUR, 2022))
    sched = {
        p.stem: p.exists()
        for p in [
            ROOT / f"data/history/schedule/raw_{y}-{str(y + 1)[2:]}.parquet"
            for y in range(2015, 2022)
        ]
    }
    sched.update(
        {
            p.stem: p.exists()
            for p in [
                ROOT / f"data/schedule/raw_{y}-{str(y + 1)[2:]}.parquet" for y in range(2022, 2025)
            ]
        }
    )
    pos = ROOT / "data/players_static"
    out = {
        "seasons": res,
        "league_rates_full_season": lg,
        "schedule_raw_exists": sched,
        "players_static_cache_files": len(list(pos.glob("*.parquet"))) if pos.exists() else 0,
    }
    (ROOT / "reports/prereg_seq_learning_curve/gate.json").write_text(json.dumps(out, indent=1))
    cols = [
        "games",
        "games_with_box",
        "rows_played",
        "rows_dnp_like",
        "pts_null",
        "reb_null",
        "ast_null",
        "fg3m_null",
        "starter_null",
        "team_games_5_starters",
        "team_games",
        "games_with_report",
        "games_with_poss",
        "shot_poss_no_shooter",
        "bubble",
        "nofans",
        "limited",
    ]
    df = pd.DataFrame({s: {c: res[s].get(c) for c in cols} for s in sorted(res)}).T
    print(df.to_string())
    m = pd.DataFrame(
        {
            s: {
                c: round(res[s][c], 3)
                for c in (
                    "pts_mean",
                    "reb_mean",
                    "ast_mean",
                    "fg3m_mean",
                    "min_mean",
                    "pts_zero_share",
                )
            }
            for s in sorted(res)
        }
    ).T
    print(m.to_string())
    print(sched, out["players_static_cache_files"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
