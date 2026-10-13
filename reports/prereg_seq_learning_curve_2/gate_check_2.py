# ruff: noqa: E501
"""Read-only data gate D1-D4, D6, D7 for SEQ_LEARNING_CURVE_2 (docs/prereg/SEQ_LEARNING_CURVE_2.md), re-measured on the current history data_version.
No model, season <= 2024 in SQL, read-only connections, nothing written but the json beside this file.

    .venv/bin/python -I reports/prereg_seq_learning_curve_2/gate_check_2.py

Thresholds are the rule's (set before this script ran); the script only applies them. D3 is read from the reconcile json files (re-run offline by
`python -m nba.parse.history_reconcile reconcile ...`, no network); D5 (tests) and D8 (Colab rate) are not data checks and are reported by the caller.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
HIST = ROOT / "data/history/nba_history.duckdb"
CUR = ROOT / "nba.duckdb"
CACHE = ROOT / "data/players_static"
OUT = Path(__file__).resolve().parent / "gate_check_2.json"
MANIFEST = ROOT / "docs/data_manifests/history_latest.json"
RAW_DIR = ROOT / "data/raw/nba_data_shufinskiy_2026-10-13_b"
D3_FILES = (
    ROOT / "reports/history_2013_2014_boxsample_reconcile.json",
    ROOT / "reports/prereg_seq_learning_curve_2/d3_2015_2018_reconcile_post_ftfix.json",
)
MAXS = 2024  # holdout: season <= 2024 in SQL, always
NEW = (2013, 2014)
HELD = tuple(range(2015, 2025))
REF = {
    "pts": 114.330,
    "reb": 43.422,
    "ast": 25.211,
    "fg3m": 12.315,
}  # L_ref (2022-23), as the parent
PARENT_SEASON_MEAN = {  # the parent's frozen constants, for the drift comparison only
    2015: (102.489, 43.707, 22.132, 8.558),
    2016: (105.621, 43.409, 22.598, 9.705),
    2017: (106.215, 43.474, 23.147, 10.492),
    2018: (110.989, 45.155, 24.488, 11.363),
}


def q(path: Path, sql: str) -> pd.DataFrame:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql).df()
    finally:
        con.close()


def box_gate(path: Path, lo: int, hi: int, tables_flags: bool) -> dict[int, dict[str, Any]]:
    base = q(
        path,
        f"""SELECT g.season, count(DISTINCT g.game_id) games, count(DISTINCT s.game_id) games_with_box,
        count(*) FILTER (WHERE s.minutes > 0) played,
        count(*) FILTER (WHERE s.minutes > 0 AND (s.pts IS NULL OR s.reb IS NULL OR s.ast IS NULL OR s.fg3m IS NULL OR s.starter IS NULL OR s.fga IS NULL OR s.tov IS NULL)) null_rows
        FROM games g LEFT JOIN player_game_stats s USING (game_id) WHERE g.season >= {lo} AND g.season <= {hi} GROUP BY 1""",
    )
    tg = q(
        path,
        f"""SELECT season, count(*) tg, count(*) FILTER (WHERE n = 5) tg5, 2.0 * avg(m) / 240.0 min_ratio FROM (
        SELECT g.season, s.game_id, s.team_id, count(*) FILTER (WHERE s.starter AND s.minutes > 0) n, sum(s.minutes) m
        FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season >= {lo} AND g.season <= {hi} GROUP BY 1, 2, 3) GROUP BY 1""",
    )
    pos = q(
        path,
        f"""SELECT g.season, count(DISTINCT p.game_id) games_poss FROM possessions p JOIN games g USING (game_id)
        WHERE g.season >= {lo} AND g.season <= {hi} GROUP BY 1""",
    )
    fl = q(
        path,
        f"""SELECT season, count(*) flag_rows, count(DISTINCT game_id) flag_games FROM game_era_flags WHERE season >= {lo} AND season <= {hi} GROUP BY 1""",
    )
    out: dict[int, dict[str, Any]] = {}
    for r in base.to_dict("records"):
        s = int(r["season"])
        t = tg[tg.season == s].iloc[0]
        p = pos[pos.season == s]
        f = fl[fl.season == s]
        games = int(r["games"])
        checks = {
            "box_rows_for_every_game": int(r["games_with_box"]) == games,
            "played_rows_ge_24000": int(r["played"]) >= 24000,
            "null_share_le_0.1pct": int(r["null_rows"]) <= 0.001 * int(r["played"]),
            "five_starters_ge_99.5pct": int(t.tg5) >= 0.995 * int(t.tg),
            "minutes_ratio_2.00_2.03": 2.00 <= float(t.min_ratio) <= 2.03,
            "possessions_every_game": (int(p.games_poss.iloc[0]) if len(p) else 0) == games,
            "era_flag_row_per_game": (int(f.flag_games.iloc[0]) if len(f) else 0) == games,
        }
        out[s] = {
            "games": games,
            "played_rows": int(r["played"]),
            "null_rows": int(r["null_rows"]),
            "team_games": int(t.tg),
            "team_games_5_starters": int(t.tg5),
            "minutes_ratio": float(t.min_ratio),
            "checks": checks,
            "pass": all(checks.values()),
        }
    return out


def league_means(path: Path, lo: int, hi: int) -> dict[int, tuple[float, float, float, float]]:
    d = q(
        path,
        f"""SELECT season, avg(pts) pts, avg(reb) reb, avg(ast) ast, avg(fg3m) fg3m FROM (
        SELECT g.season, s.game_id, s.team_id, sum(s.pts) pts, sum(s.reb) reb, sum(s.ast) ast, sum(s.fg3m) fg3m
        FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season >= {lo} AND g.season <= {hi} GROUP BY 1, 2, 3) GROUP BY 1""",
    )
    return {
        int(r["season"]): (
            round(r["pts"], 3),
            round(r["reb"], 3),
            round(r["ast"], 3),
            round(r["fg3m"], 3),
        )
        for r in d.to_dict("records")
    }


def static_ids(path: Path) -> set[int]:
    d = q(
        path, "SELECT player_id FROM players_static WHERE position IS NOT NULL AND position <> ''"
    )
    return set(d.player_id.astype(int))


def d4() -> dict[str, object]:
    known = static_ids(CUR) | static_ids(HIST)
    for f in CACHE.glob("*.parquet"):
        x = pd.read_parquet(f)
        if "position" in x.columns and x["position"].notna().any():
            known.add(int(f.stem))
    df = q(
        HIST,
        "SELECT g.season, s.player_id, s.minutes FROM games g JOIN player_game_stats s USING (game_id) WHERE g.season <= 2021 AND s.minutes > 0",
    )
    df["known"] = df.player_id.astype(int).isin(known)
    per: dict[str, object] = {}
    for s, g in df.groupby("season"):
        unk, kn = g[~g.known], g[g.known]
        share = float((~g.known).mean())
        gap = float(kn.minutes.mean() - unk.minutes.mean()) if len(unk) else 0.0
        per[str(int(s))] = {
            "played_rows": len(g),
            "unknown_rows": len(unk),
            "unknown_share": share,
            "unknown_ids": sorted(int(i) for i in unk.player_id.unique()),
            "mean_min_unknown": float(unk.minutes.mean()) if len(unk) else None,
            "mean_min_known": float(kn.minutes.mean()),
            "minutes_gap": gap,
            "share_le_1pct": share <= 0.01,
            "gap_le_3": gap <= 3.0,
            "pass": share <= 0.01 and gap <= 3.0,
        }
    return {"per_season": per, "pooled_unknown_share": float((~df.known).mean())}


def d3() -> dict[str, object]:
    """D3 per season (the rule's 1% limit applies PER SEASON): missing/extra played rows <= 0.1% of player-games; any stat off in <= 1% of player-games; minutes within tolerance >= 91%."""
    per: dict[str, object] = {}
    for f in D3_FILES:
        for s, x in json.loads(f.read_text()).items():
            if s in ("all", "total"):
                continue
            n = int(x["official_played_rows"])
            miss, extra = int(x["missing_in_derived_played"]), int(x["extra_in_derived"])
            worst = max(int(v["mismatch"]) for v in x["per_stat"].values())
            within = float(x["minutes"]["share_within_tol"])
            per[s] = {
                "file": str(f.relative_to(ROOT)),
                "games": int(x["games"]),
                "official_played_rows": n,
                "missing": miss,
                "extra": extra,
                "worst_stat_mismatch_rows": worst,
                "minutes_within_tol": within,
                "pass": (miss + extra) <= 0.001 * n and worst <= 0.01 * n and within >= 0.91,
            }
    return per


def d1() -> dict[str, object]:
    sums = {}
    for line in (RAW_DIR / "SHA256SUMS").read_text().splitlines():
        h, _, name = line.partition("  ")
        sums[name] = h
    need = (
        "nbastats_2013.tar.xz",
        "nbastats_2014.tar.xz",
        "nbastats_po_2013.tar.xz",
        "nbastats_po_2014.tar.xz",
    )
    ok = {
        n: (RAW_DIR / n).exists()
        and hashlib.sha256((RAW_DIR / n).read_bytes()).hexdigest() == sums.get(n)
        for n in need
    }
    return {"files": ok, "pass": all(ok.values())}


def main() -> int:
    man = json.loads(MANIFEST.read_text())
    dv = man["data_version"]
    box = {**box_gate(HIST, 2013, 2021, True), **box_gate(CUR, 2022, MAXS, False)}
    lm = {**league_means(HIST, 2013, 2021), **league_means(CUR, 2022, MAXS)}
    lprev = {2013: tuple(REF.values()), 2014: lm[2013]}
    drift = {
        s: [round(a - b, 3) for a, b in zip(lm[s], PARENT_SEASON_MEAN[s], strict=True)]
        for s in PARENT_SEASON_MEAN
    }
    res: dict[str, Any] = {
        "data_version": dv,
        "D1": d1(),
        "D2": {str(s): box[s] for s in sorted(box) if s <= 2018 or s in box},
        "D3": d3(),
        "D4": d4(),
        "D6_carried_gates_2015_2024": {str(s): box[s]["pass"] for s in HELD},
        "D7": {
            "season_mean_pts_reb_ast_fg3m": {str(s): lm[s] for s in sorted(lm)},
            "Lprev": {str(s): v for s, v in lprev.items()},
            "parent_constant_drift_2015_2018": drift,
        },
    }
    OUT.write_text(json.dumps(res, indent=1, default=str))
    print("data_version", dv)
    for s in sorted(box):
        print("D2/D6", s, box[s]["pass"], {k: v for k, v in box[s]["checks"].items() if not v})
    for s, x in res["D3"].items():
        print("D3", s, x["pass"], x["worst_stat_mismatch_rows"], round(x["minutes_within_tol"], 4))
    for s, x in res["D4"]["per_season"].items():
        print(
            "D4",
            s,
            x["pass"],
            round(x["unknown_share"] * 100, 3),
            "% gap",
            round(x["minutes_gap"], 1),
            len(x["unknown_ids"]),
            "ids",
        )
    print("D1", res["D1"]["pass"], "D7", res["D7"]["Lprev"], "drift", drift)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
