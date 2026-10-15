# ruff: noqa: E501
# mypy: ignore-errors
"""INJURY_TIMING v2 step 1: the TRUE latest<=T-180 -> latest<=T-60 status change for 2023-24 on the repaired (union) table.

Union = stored table (11:00 / 17:00) + scratch intraday table (hourly 12:00-21:00). Read-only; season <= 2024 in SQL.
Run: PYTHONPATH=<worktree> <main>/.venv/bin/python research/scratch/injury_timing_gap.py
Output reports/injury_timing/gap_2023_24.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from injury_timing_descriptive import (
    MAIN,
    SEED,
    B,
    event_table,
    pct,
    prior_state,
    sn_dict,
    snap,
    stamp_of,
    transitions,
)

from nba.features.game_tipoff import build_game_tipoff

SCRATCH = MAIN / "data/scratch/injury_intraday.duckdb"
OUT = Path(__file__).resolve().parents[2] / "reports/injury_timing/gap_2023_24.json"
SCHEMA = {"game_id": pl.Utf8, "player_id": pl.Int64, "status": pl.Utf8, "as_of": pl.Datetime("us")}


def rows(db: Path, season: int) -> pl.DataFrame:
    con = duckdb.connect(str(db), read_only=True)
    r = con.execute(
        "SELECT a.game_id, a.player_id, lower(a.status), CAST(a.as_of AS TIMESTAMP) FROM player_availability a "
        "JOIN games g USING (game_id) WHERE g.season = ? AND a.player_id IS NOT NULL AND a.source='nba_official_report'",
        [season],
    ).fetchall()
    con.close()
    return pl.DataFrame(r, schema=SCHEMA, orient="row")


def main() -> None:
    assert SCRATCH.exists()
    rng = np.random.default_rng(SEED)
    tips = build_game_tipoff(MAIN / "data/schedule", max_season=2024)
    res = {"scratch_db": str(SCRATCH), "seed": SEED, "B": B}
    for season in (2023, 2024):
        con = duckdb.connect(str(MAIN / "nba.duckdb"), read_only=True)
        games = con.execute(
            "SELECT game_id, game_date, home_team, away_team FROM games WHERE season=?", [season]
        ).fetchall()
        info = {g: (d, int(h), int(a)) for g, d, h, a in games}
        state = prior_state(con)
        pm = {
            (g, p): float(m or 0) > 0
            for g, p, m in con.execute(
                "SELECT s.game_id, s.player_id, s.minutes FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season=?",
                [season],
            ).fetchall()
        }
        con.close()
        df = pl.concat([rows(MAIN / "nba.duckdb", season), rows(SCRATCH, season)]).unique()
        df = df.join(tips.select(["game_id", "tip_et"]), on="game_id", how="inner")
        s180, s60 = snap(df, 180), snap(df, 60)
        t180, t60 = stamp_of(s180), stamp_of(s60)
        tipd = dict(tips.select(["game_id", "tip_et"]).iter_rows())
        lead60 = np.array([(tipd[g] - t).total_seconds() / 60 for g, t in t60.items()])
        gs = [g for g in t60 if g in t180 and t180[g] < t60[g]]
        old = {k: v for k, v in sn_dict(s180).items() if k[0] in set(gs)}
        new = {k: v for k, v in sn_dict(s60).items() if k[0] in set(gs)}
        allc, rotc, mat = transitions(old, new, gs, info, state)
        tab = event_table(allc, rotc, len(gs), rng)
        gap = np.array([(t60[g] - t180[g]).total_seconds() / 3600 for g in gs])
        out = {
            "games_total": len(games),
            "games_with_T60": len(t60),
            "games_with_distinct_T180_T60": len(gs),
            "lead60_p10_p50_p90": [pct(lead60, q) for q in (10, 50, 90)],
            "share_lead60_le90": float((lead60 <= 90).mean()),
            "stamp_gap_hours_p10_p50_p90": [pct(gap, q) for q in (10, 50, 90)],
            "events_per_game": tab,
            "matrix": {f"{a}->{b}": n for (a, b), n in sorted(mat.items())},
        }

        # tag meaning: P(played) by status at T-180 and at T-60
        def rate(keys):
            y = np.array([float(pm.get(k, False)) for k in keys])  # noqa: B023
            g = np.array([k[0] for k in keys])
            ug, inv = np.unique(g, return_inverse=True)
            n = len(ug)
            s = np.bincount(inv, weights=y, minlength=n)
            c = np.bincount(inv, minlength=n).astype(float)
            ii = rng.integers(0, n, size=(B, n))
            m = s[ii].sum(1) / c[ii].sum(1)
            return [
                float(s.sum() / c.sum()),
                float(np.percentile(m, 2.5)),
                float(np.percentile(m, 97.5)),
                int(c.sum()),
            ]

        a180, a60 = sn_dict(s180), sn_dict(s60)
        tag = {}
        for st in ("questionable", "doubtful"):
            tag[f"T180:{st}"] = rate([k for k, v in a180.items() if v == st and k[0] in set(gs)])
            tag[f"T60:{st}"] = rate([k for k, v in a60.items() if v == st and k[0] in set(gs)])
        for t in ("questionable", "out", "available", "absent"):
            ks = [
                k
                for k, v in a180.items()
                if v == "questionable" and a60.get(k, "absent") == t and k[0] in set(gs)
            ]
            if ks:
                tag[f"Q@T180 then {t}@T60"] = rate(ks)
        out["P_played"] = tag
        res[str(season)] = out
    OUT.write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str)[:7000])


if __name__ == "__main__":
    main()
