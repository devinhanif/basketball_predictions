# ruff: noqa: E501
"""Rehearsal for docs/prereg/FULL_SHADOW_LIVE_2026_27.md (DESCRIPTIVE, never a verdict; seasons 2023-24 only).

Uses the stored RECENT_ROLE_FULL arms (``data/ops/recent_role_full_cache``: FULL = heads R + pi R) and the stored A0 components, so no
model is fitted; the database is read only for game dates (``season <= 2024``). It runs the live scorer's arithmetic (one-sided
bounds, stale-adjustment placebo, day-shuffle, n_required, weeks) on those rows to size n_min and prove the code path.

    .venv/bin/python -W ignore -m research.eval.full_shadow_live_rehearse
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import duckdb
import numpy as np

from nba.eval import full_shadow_live as L
from nba.eval.lower_tail_eval import load_components
from research.eval import pi_recent_role_eval as PR
from research.eval import recent_role_full_eval as RF

OUT = Path("reports/prereg_full_shadow_live/rehearsal.json")


def cluster_stats(d: np.ndarray, key: np.ndarray) -> dict[str, float]:
    _, inv = np.unique(key, return_inverse=True)
    g = int(inv.max()) + 1
    n, mu = len(d), float(d.mean())
    s = np.bincount(inv, weights=d - mu, minlength=g)
    se = math.sqrt(g / (g - 1) * float((s**2).sum())) / n
    sd = float(d.std(ddof=1))
    return {"sd_row": sd, "deff": (se * math.sqrt(n) / sd) ** 2, "clusters": g}


def main() -> None:
    pic = dict(np.load(RF.LT_DIR / "components_pi.npz", allow_pickle=True))
    pi_r = np.load(RF.pi_path("R"), allow_pickle=True)["pi"]
    con = duckdb.connect("nba.duckdb", read_only=True)
    dates = dict(
        con.execute("SELECT game_id, game_date FROM games WHERE season <= 2024").fetchall()
    )
    out: dict[str, object] = {
        "note": "descriptive 2023-24 rehearsal on stored FULL/A0 arms; 199-point grids, not the live 19-quantile grids"
    }
    sizing: dict[str, object] = {}
    worst = {"sd": 0.0, "deff": 0.0}
    for stat in L.STATS:
        c0 = load_components(stat, RF.LT_DIR)
        y, gid, season, pid = (
            c0["row_y"],
            c0["row_gid"].astype(str),
            c0["row_season"],
            c0["row_pid"].astype(np.int64),
        )
        q0 = RF.arm_qi(stat, c0, pic, pic["row_pi"])
        q1 = RF.arm_qi(stat, RF.load_heads(stat, "R"), pic, pi_r)
        d = PR.crps_int(q1, y) - PR.crps_int(q0, y)
        day = np.array([str(dates[g]) for g in gid])
        per: dict[str, object] = {}
        for s in (2023, 2024):
            m = season == s
            cg, cd = cluster_stats(d[m], gid[m]), cluster_stats(d[m], day[m])
            per[str(s)] = {
                "n": int(m.sum()),
                "delta": float(d[m].mean()),
                "game": cg,
                "date": cd,
                "rows_per_game": m.sum() / cg["clusters"],
            }
            if stat == "pts":
                worst["sd"] = max(worst["sd"], cg["sd_row"])
                worst["deff"] = max(worst["deff"], cg["deff"], cd["deff"])
        sizing[stat] = per
        if stat != "pts":
            continue
        # pts guards on the 2024 report season (stale-adjustment placebo, day-shuffle) with the scorer's own functions
        m = season == 2024
        tip = np.array(
            [np.datetime64(dates[g]).astype("datetime64[s]").astype("int64") for g in gid[m]]
        )
        pl = L.stale_adjust_placebo(pid[m], tip, q1[m], q0[m])
        sh = L.shuffle_grids_within_day(q1[m], np.array([np.datetime64(dates[g]) for g in gid[m]]))
        sc0 = PR.crps_int(q0, y)[m]
        out["pts_2024_guards"] = {
            "full_minus_prod": L.one_sided(d[m], gid[m], L.NULL_SD_PTS),
            "full_minus_stale_placebo": L.one_sided(
                PR.crps_int(q1[m], y[m]) - PR.crps_int(pl, y[m]), gid[m]
            ),
            "stale_placebo_minus_prod": L.one_sided(PR.crps_int(pl, y[m]) - sc0, gid[m]),
            "shuffled_full_minus_prod": L.one_sided(PR.crps_int(sh, y[m]) - sc0, gid[m]),
        }
    out["sizing"] = sizing
    n_min = L.n_required(worst["sd"], worst["deff"])
    out["n_required_one_sided_halfwidth_0.005"] = {
        "worst_sd_row": worst["sd"],
        "worst_deff": worst["deff"],
        "n": n_min,
        "n_min_frozen": L.N_MIN,
        "interim": L.N_INTERIM,
    }
    out["weeks_reached"] = {
        "base_21_rows_per_game_2wk_burn": {
            "final": L.week_reached(L.N_MIN),
            "interim": L.week_reached(L.N_INTERIM),
        },
        "optimistic_25_rows_0_burn": {"final": L.week_reached(L.N_MIN, 25.0, burn_weeks=0.0)},
        "pessimistic_20_rows_3wk_burn": {"final": L.week_reached(L.N_MIN, 20.0, burn_weeks=3.0)},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps(out, indent=1, default=float))


if __name__ == "__main__":
    main()
