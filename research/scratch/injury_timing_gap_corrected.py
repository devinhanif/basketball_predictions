# ruff: noqa: E501
# mypy: ignore-errors
"""INJURY_TIMING rev 3, M6: the 2023-24 status-change table on the CORRECTED (header) clock.

latest stamp <= tip-180 versus latest stamp <= tip-60 over R (header-stamped intraday table UNION shifted stored table), plus the live-cutoff
comparison (R at ``live_cutoff`` vs the stored table as production has it). Read-only; season <= 2024.
Run: PYTHONPATH=<worktree> <main>/.venv/bin/python research/scratch/injury_timing_gap_corrected.py
Output: reports/injury_timing/gap_corrected_2023_24.json (supersedes gap_2023_24.json, which used the mis-stamped clock)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from injury_timing_descriptive import SEED, B, event_table, pct, prior_state, transitions

from research.eval import injury_timing_gate as gate

MAIN = Path("/Users/devin/Downloads/nba-prediction")
SCRATCH = MAIN / "data/scratch/injury_intraday_v2.duckdb"
OUT = Path(__file__).resolve().parents[2] / "reports/injury_timing/gap_corrected_2023_24.json"


def snapshot(rows: pl.DataFrame, cut: dict) -> tuple[dict, dict]:
    c = pl.DataFrame(
        {"game_id": list(cut), "_c": list(cut.values())},
        schema={"game_id": pl.Utf8, "_c": pl.Datetime("us")},
    )
    u = rows.join(c, on="game_id").filter(pl.col("as_of") <= pl.col("_c"))
    lat = u.group_by("game_id").agg(pl.col("as_of").max().alias("_l"))
    s = u.join(lat, on="game_id").filter(pl.col("as_of") == pl.col("_l"))
    st = {(g, p): v for g, p, v in s.select(["game_id", "player_id", "status"]).iter_rows()}
    return st, {g: t for g, t in lat.iter_rows()}


def main() -> None:
    rng = np.random.default_rng(SEED)
    b = gate.build_all(MAIN, SCRATCH)
    tips, R = b["tips"], b["R"]
    res = {"scratch_db": str(SCRATCH), "seed": SEED, "B": B}
    con = duckdb.connect(str(MAIN / "nba.duckdb"), read_only=True)
    state = prior_state(con)
    for season in (2023, 2024):
        gl = b["games"][season]
        info = {
            g: (d, int(h), int(a))
            for g, d, h, a in con.execute(
                "SELECT game_id, game_date, home_team, away_team FROM games WHERE season=?",
                [season],
            ).fetchall()
        }
        pm = {
            (g, p): float(m or 0) > 0
            for g, p, m in con.execute(
                "SELECT s.game_id, s.player_id, s.minutes FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season=?",
                [season],
            ).fetchall()
        }
        Rs = R.filter(pl.col("season") == season)
        s180, t180 = snapshot(
            Rs,
            {g: tips[g] - __import__("datetime").timedelta(minutes=180) for g in gl if g in tips},
        )
        s60, t60 = snapshot(
            Rs, {g: tips[g] - __import__("datetime").timedelta(minutes=60) for g in gl if g in tips}
        )
        gs = [g for g in t60 if g in t180 and t180[g] < t60[g]]
        gset = set(gs)
        old = {k: v for k, v in s180.items() if k[0] in gset}
        new = {k: v for k, v in s60.items() if k[0] in gset}
        allc, rotc, mat = transitions(old, new, gs, info, state)
        gap = np.array([(t60[g] - t180[g]).total_seconds() / 3600 for g in gs])
        lead60 = np.array([(tips[g] - t60[g]).total_seconds() / 60 for g in t60])
        out = {
            "games_total": len(gl),
            "games_with_distinct_T180_T60": len(gs),
            "stamp_gap_hours_p10_p50_p90": [pct(gap, q) for q in (10, 50, 90)],
            "lead_at_tip_minus_60_p10_p50_p90": [pct(lead60, q) for q in (10, 50, 90)],
            "events_per_game": event_table(allc, rotc, len(gs), rng),
            "matrix": {f"{a}->{c}": n for (a, c), n in sorted(mat.items())},
        }

        def rate(keys):
            y = np.array([float(pm.get(k, False)) for k in keys])  # noqa: B023
            g = np.array([k[0] for k in keys])
            ug, inv = np.unique(g, return_inverse=True)
            n = len(ug)
            sm = np.bincount(inv, weights=y, minlength=n)
            c = np.bincount(inv, minlength=n).astype(float)
            ii = rng.integers(0, n, size=(B, n))
            m = sm[ii].sum(1) / c[ii].sum(1)
            return [
                float(sm.sum() / c.sum()),
                float(np.percentile(m, 2.5)),
                float(np.percentile(m, 97.5)),
                int(c.sum()),
            ]

        tag = {}
        for st in ("questionable", "doubtful"):
            tag[f"T180:{st}"] = rate([k for k, v in old.items() if v == st])
            tag[f"T60:{st}"] = rate([k for k, v in new.items() if v == st])
        for t in ("questionable", "out", "available", "absent"):
            ks = [k for k, v in old.items() if v == "questionable" and new.get(k, "absent") == t]
            if ks:
                tag[f"Q@T180 then {t}@T60"] = rate(ks)
        out["P_played"] = tag
        qd = sum(v for k, v in mat.items() if k[0] in ("questionable", "doubtful"))
        qd_out = sum(
            v for k, v in mat.items() if k[0] in ("questionable", "doubtful") and k[1] == "out"
        )
        out["qd_at_T180"] = qd
        out["qd_at_T180_out_at_T60"] = qd_out
        res[str(season)] = out
    con.close()
    OUT.write_text(json.dumps(res, indent=1, default=str))
    print(
        json.dumps(
            {
                s: {
                    k: res[s][k]
                    for k in (
                        "games_with_distinct_T180_T60",
                        "stamp_gap_hours_p10_p50_p90",
                        "qd_at_T180",
                        "qd_at_T180_out_at_T60",
                    )
                }
                for s in ("2023", "2024")
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
