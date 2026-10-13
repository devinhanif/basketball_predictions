# ruff: noqa: E501
"""Proof P3 (docs/prereg/AVG_SHADOW_LIVE.md): the 19-quantile live arithmetic reproduces the 199-point integer CRPS contrast.

Reads ONLY the stored selection-side artifacts of T478's run #1 (seasons 2023 and 2024; ``oof_WALL_E2.npz`` of data/colab/runs/seq_full_mix_rt_sel,
the stored A0 / FULL caches); the 2025-bearing files are never opened. For each stat, on the 2024 (2024-25) regular-season played rows:

  offline : the T478 arms on the 199-point integer grid (``seq_full_mix_eval.build_arms``);
  live    : the SAME rows rebuilt the way the shadow builds them, from 19 stored quantiles per member (A0 and SEQ interpolated to 19 taus, FULL its
            19 equal-mass integer quantiles), the stored learned-gate weights, ``nba.daily.avg_shadow.grids.build_arms``, AVG stored as 19 integer
            quantiles, every arm scored with the 19-tau pinball (``crps_rows``).

Pass (rule P3): |live - offline| of pts AVG minus FULL <= 0.0005, the same sign on reb / ast / fg3m, and the 19-atom guards G1 and (f) rehearsed
(G1 rehearsal: the randomised PIT of the 19-atom pmf must satisfy the rule's own G1 limits on 2024 pts; ``pass_g1_rehearsal`` is reported separately).

    .venv/bin/python -W ignore research/colab/jobs/avg_shadow_rebuild/p3_equivalence.py --sel-run data/colab/runs/seq_full_mix_rt_sel/20261012_144207
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from nba.daily.avg_shadow import grids
from nba.eval import avg_shadow_live as L
from nba.eval.context_residual_eval import crps_from_quantiles
from nba.eval.full_shadow_live import crps_rows
from research.eval import ensemble_eval as E
from research.eval import seq_full_mix_eval as S
from research.eval import workload_eval as WE

ROOT = Path(__file__).resolve().parents[4]
TOL = 0.0005
SEASON = 2024


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sel-run", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=ROOT / "reports/prereg_avg_shadow_live/p3.json")
    a = ap.parse_args(argv)
    S.configure_rt(a.sel_run, "WALL")
    t0 = time.time()
    res: dict[str, Any] = {"sel_run": str(a.sel_run), "season": SEASON, "stats": {}}
    with WE.heavy():
        w = S.stored_world()
        for st in S.STATS:
            b = w["base"][st]
            assert int(b["season"].max()) <= 2024
            seq199 = S.seq_199(w, st)["E2"]
            pooled, gw = S.learned_blend(
                b["mix"], seq199, b["y"], w["date_days"], b["season"], w["ctx"], st, S.REPO_CAP
            )
            reg = S.regular_flag(b["game_id"]).astype(bool)
            off = S.build_arms(st, b, w["date_days"], w["ctx"], {"E2": seq199})
            m = (b["season"] == SEASON) & reg
            y = b["y"]
            # the live arithmetic from 19 stored quantiles
            a0_q = E.grid19(b["mix"])
            seq_q = E.grid19(seq199)
            full_q = grids.q19_of_grid199(b["full_int"])
            live = grids.build_arms(a0_q, full_q, seq_q, gw, reg)
            q = {k: grids.q19_of_grid199(v) for k, v in live.items()}
            q["A0"] = grids.q19_of_grid199(
                b["a0_int"]
            )  # the stored A0 grid as the live comparator reads it (integer 19 quantiles)
            q["AVG_A0"] = L.mix_atoms(q["SEQc"], q["A0"])
            d_off = crps_from_quantiles(off["AVG"].astype(float), y) - crps_from_quantiles(
                off["FULL"].astype(float), y
            )
            d_live = crps_rows(q["AVG"], y) - crps_rows(q["FULL"], y)
            a0_off = crps_from_quantiles(off["AVG"].astype(float), y) - crps_from_quantiles(
                off["A0"].astype(float), y
            )
            a0_live = crps_rows(q["AVG"], y) - crps_rows(q["A0"], y)
            ctrl_off = crps_from_quantiles(off["AVG"].astype(float), y) - crps_from_quantiles(
                off["AVG_A0"].astype(float), y
            )
            ctrl_live = crps_rows(q["AVG"], y) - crps_rows(q["AVG_A0"], y)
            rec: dict[str, Any] = {
                "n": int(m.sum()),
                "avg_minus_full": {
                    "offline_199": float(d_off[m].mean()),
                    "live_19": float(d_live[m].mean()),
                    "abs_diff": float(abs(d_off[m].mean() - d_live[m].mean())),
                },
                "avg_minus_a0": {
                    "offline_199": float(a0_off[m].mean()),
                    "live_19": float(a0_live[m].mean()),
                },
                "avg_minus_avg_a0_f": {
                    "offline_199": float(ctrl_off[m].mean()),
                    "live_19": float(ctrl_live[m].mean()),
                },
                "same_sign_avg_minus_full": bool(
                    np.sign(d_off[m].mean()) == np.sign(d_live[m].mean())
                ),
                "grid_cell_diff_share": float(
                    (
                        np.abs(
                            q["AVG"][m].astype(int)
                            - grids.q19_of_grid199(off["AVG"])[m].astype(int)
                        )
                        > 1
                    ).mean()
                ),
            }
            if st == "pts":
                rec["G1_calibration_19_atom"] = L.calibration(q["AVG"][m], y[m])
                # the same readings on the 199-point form and for the other arms: is a G1 miss the 19-atom pmf or AVG itself?
                rec["G1_context"] = {
                    k: {
                        "pit_q10_199": E.calibration(off[k][m].astype(float), y[m])["guard"][
                            "cover"
                        ]["0.1"],
                        "pit_q10_19": L.calibration(grids.q19_of_grid199(off[k])[m], y[m])["guard"][
                            "cover"
                        ]["0.1"],
                        "pit_q20_199": E.calibration(off[k][m].astype(float), y[m])["guard"][
                            "cover"
                        ]["0.2"],
                        "pit_q20_19": L.calibration(grids.q19_of_grid199(off[k])[m], y[m])["guard"][
                            "cover"
                        ]["0.2"],
                    }
                    for k in ("AVG", "FULL", "A0")
                }
                rec["pass_crps"] = bool(rec["avg_minus_full"]["abs_diff"] <= TOL)
                rec["pass_g1_rehearsal"] = bool(rec["G1_calibration_19_atom"]["ok"])
                rec["pass"] = bool(rec["pass_crps"] and rec["pass_g1_rehearsal"])
            else:
                rec["pass"] = bool(rec["same_sign_avg_minus_full"])
            res["stats"][st] = rec
            print(
                st,
                json.dumps({k: v for k, v in rec.items() if k != "G1_calibration_19_atom"}),
                flush=True,
            )
            del off, live
    res["pass"] = bool(all(v["pass"] for v in res["stats"].values()))
    res["seconds"] = round(time.time() - t0, 1)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=2, default=float))
    print("P3", "PASS" if res["pass"] else "FAIL", "->", a.out)
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
