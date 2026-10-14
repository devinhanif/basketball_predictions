# ruff: noqa: E501
"""COMBO_STOCKS_SHADOW rehearsal, stage C: rule 6(d) G1 (fixed row order), 6(e) slice guard, 6(f) later half, all seven targets, 2024.

Descriptive dry run of the rule as written (pre-freeze review B2, B3, 2026-10-14). Reads the stage-A arrays and the stage-B select/report
outputs; the database only for team schedules and positions (seasons <= 2024). Uses ``nba.props.combo_g1`` (the rule's G1, not the
AVG reader), ``R_chosen.npz`` as committed, and the per-row grids stage B saved (``grids_2024.npz``).

    python -W ignore reports/prereg_combo_stocks_shadow/stage_c_guards.py --a ... --a2 ... --a3 ... --a4 ... --db ... --out <stage_b dir>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import stage_b as B  # noqa: E402

from nba.props import combo_g1 as G  # noqa: E402

FLOOR_STOCK = {"blk": 0.0050, "stl": 0.0050}


def _main() -> None:
    ap = argparse.ArgumentParser()
    for k in ("--a", "--db", "--out"):
        ap.add_argument(k, required=True)
    for k in ("--a2", "--a3", "--a4", "--a5"):
        ap.add_argument(k, default="none")
    args = ap.parse_args()
    out = Path(args.out)
    w = B.World(args)
    z = np.load(out / "R_chosen.npz")
    w.set_head({"blk": str(z["head"][0]), "stl": str(z["head"][1])})
    w.set_choice({s: str(v) for s, v in zip(B.STATS, z["choice"], strict=True)})
    use_copula = {c: bool(v) for c, v in zip(B.COMBO_NAMES, z["use_copula"], strict=True)}
    sel = json.loads((out / "select.json").read_text())
    floors = {t: v["floor"] for t, v in sel["floors_and_n_required"].items()}
    g = np.load(out / "grids_2024.npz")
    rep = g["rep"]
    m = np.zeros(w.keys.height, dtype=bool)
    m[rep] = True
    assert np.array_equal(np.flatnonzero(m), rep)
    k = w.keys
    pid = k["player_id"].to_numpy()
    # second night of a back-to-back: the team played on the previous calendar day
    con = duckdb.connect(args.db, read_only=True)
    try:
        tg = con.execute(
            "SELECT game_date, home_team AS t FROM games WHERE season <= 2024 UNION ALL SELECT game_date, away_team FROM games WHERE season <= 2024"
        ).pl()
    finally:
        con.close()
    played = {
        (int(t), int(d))
        for d, t in zip(tg["game_date"].cast(pl.Int32).to_numpy(), tg["t"].to_numpy(), strict=True)
    }
    team = k["team_id"].to_numpy()
    second_night = np.array(
        [(int(t), int(d) - 1) in played for t, d in zip(team, w.day, strict=True)]
    )
    mins = k["minutes"].to_numpy().astype(float)
    masks_all = {
        "starter": w.starter == 1,
        "bench": w.starter == 0,
        "min<10": mins < 10,
        "min10-20": (mins >= 10) & (mins <= 20),
        "min>20": mins > 20,
        "teammate_out": (k["has_report"].to_numpy() == 1)
        & (k["n_out_rot"].fill_null(0).to_numpy() > 0),
        "second_night": second_night,
        "cold_start": k["n_prior"].to_numpy() < 10,
        "first15": k["team_game_no"].to_numpy() < 15,
    }
    res: dict[str, Any] = {"season": 2024, "n": int(len(rep)), "targets": {}}
    for t in ("blk", "stl", *B.COMBO_NAMES):
        yy = w.y[t][rep]
        if t in ("blk", "stl"):
            sg = w.q[t][rep]
        else:
            sg = g[f"cop_{t}"] if use_copula[t] else g[f"ind_{t}"]
        d = B.crps(sg, yy) - B.crps(w.rec[t][rep], yy)
        masks = {nm: mk[rep] for nm, mk in masks_all.items()}
        sg_res = G.slice_guard(d, masks, floors[t])
        half = w.day[rep] > np.median(w.day[rep])
        later = float(d[half].mean())
        gg = G.g1(sg, yy, w.day[rep], w.gid[rep], pid[rep])
        res["targets"][t] = {
            "floor": floors[t],
            "delta_all": float(d.mean()),
            "g1": gg,
            "slice_guard_ok": sg_res["ok"],
            "slices": {
                nm: [r["n"], None if r["delta"] is None else round(r["delta"], 4)]
                for nm, r in sg_res["slices"].items()
            },
            "violations": [nm for nm, r in sg_res["slices"].items() if r["violates"]],
            "later_half_delta": later,
            "later_half_ok": bool(later <= -floors[t]),
            "a_ok": bool(d.mean() <= -floors[t]),
        }
        r = res["targets"][t]
        print(
            t,
            "g1",
            r["g1"]["ok"],
            {a: round(b, 4) for a, b in r["g1"]["cover"].items()},
            "slice_ok",
            r["slice_guard_ok"],
            r["violations"],
            "later",
            round(later, 4),
            flush=True,
        )
    (HERE / "guards_2024.json").write_text(json.dumps(res, indent=1, default=float))
    print("done")


LOCK = Path("/Users/devin/Downloads/nba-prediction/data/ops/heavy.lock")


def main() -> None:
    from research.eval import workload_eval as WE

    with WE.WorkloadLock(LOCK):  # the cooperative lock: take it or wait, never skip it
        _main()


if __name__ == "__main__":
    main()
