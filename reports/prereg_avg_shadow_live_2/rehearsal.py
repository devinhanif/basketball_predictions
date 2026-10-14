# ruff: noqa: E501
"""AVG_SHADOW_LIVE_2 rehearsal (DESCRIPTIVE, not evidence; season 2024 regular season only; reads the same stored selection-side artifacts as P3).

Question: G1 (and G2-G5, (c), (f), (g)) on three forms of the same 2024 rows:
  "19"      the stored 19 quantiles read as equal-mass atoms (the frozen AVG_SHADOW_LIVE form; P3 failed G1 here),
  "199live" the 199-point integer pmf the shadow stores in forward_avg_shadow.avg_pmf199: built from the 19 stored quantiles of each member
            (grids.build_arms), i.e. exactly the live object,
  "199off"  the offline T478 199-point grid (context only; the live path cannot see it).
G1 is the scorer's own ``nba.eval.avg_shadow_live.calibration`` (randomised PIT, seed 0) on every form.

    .venv/bin/python -W ignore reports/prereg_avg_shadow_live_2/rehearsal.py     (about 40 s; read-only; writes rehearsal.json beside it)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from nba.daily.avg_shadow import grids  # noqa: E402
from nba.eval import avg_shadow_live as L  # noqa: E402
from nba.eval.context_residual_eval import crps_from_quantiles  # noqa: E402
from nba.eval.full_shadow_live import crps_rows  # noqa: E402
from research.eval import ensemble_eval as E  # noqa: E402
from research.eval import seq_full_mix_eval as S  # noqa: E402
from research.eval import workload_eval as WE  # noqa: E402

SEL = ROOT / "data/colab/runs/seq_full_mix_rt_sel/20261012_144207"
SEASON = 2024
OUT = Path(__file__).resolve().parent / "rehearsal.json"


def main() -> None:
    S.configure_rt(SEL, "WALL")
    t0 = time.time()
    out: dict[str, Any] = {"sel_run": str(SEL.relative_to(ROOT)), "season": SEASON, "stats": {}}
    with WE.heavy():
        w = S.stored_world()
        for st in S.STATS:
            b = w["base"][st]
            assert int(b["season"].max()) <= SEASON
            seq199 = S.seq_199(w, st)["E2"]
            _, gw = S.learned_blend(
                b["mix"], seq199, b["y"], w["date_days"], b["season"], w["ctx"], st, S.REPO_CAP
            )
            reg = S.regular_flag(b["game_id"]).astype(bool)
            off = S.build_arms(st, b, w["date_days"], w["ctx"], {"E2": seq199})
            y, gid = b["y"], b["game_id"]
            m = (b["season"] == SEASON) & reg
            rows = np.flatnonzero(m)
            live = grids.build_arms(
                E.grid19(b["mix"]), grids.q19_of_grid199(b["full_int"]), E.grid19(seq199), gw, reg
            )
            q19 = {k: grids.q19_of_grid199(v) for k, v in live.items()}
            q19["A0"] = grids.q19_of_grid199(b["a0_int"])
            q19["AVG_A0"] = L.mix_atoms(q19["SEQc"], q19["A0"])
            forms: dict[str, tuple[dict[str, np.ndarray], Any, float]] = {
                "19": ({k: q19[k] for k in ("AVG", "FULL", "A0", "AVG_A0")}, crps_rows, 0.5 / 19),
                "199live": (
                    {k: live[k].astype(float) for k in ("AVG", "FULL", "A0")},
                    crps_from_quantiles,
                    0.5 / 199,
                ),
                "199off": (
                    {k: off[k].astype(float) for k in ("AVG", "FULL", "A0", "AVG_A0")},
                    crps_from_quantiles,
                    0.5 / 199,
                ),
            }
            sm = E.slice_masks(b, rows)
            yy = y[m]
            day = w["date_days"][m]
            rec: dict[str, Any] = {"n": int(m.sum()), "games": int(len(np.unique(gid[m])))}
            for tag, (f, crf, eps) in forms.items():
                cr = {k: crf(v, y) for k, v in f.items()}
                d_full = (cr["AVG"] - cr["FULL"])[m]
                d_a0 = (cr["AVG"] - cr["A0"])[m]
                cal = L.calibration(f["AVG"][m], yy)
                r: dict[str, Any] = {
                    "avg_minus_full": float(d_full.mean()),
                    "avg_minus_a0": float(d_a0.mean()),
                    "G1": {
                        **{k: cal[k] for k in ("bias", "cov80", "ok")},
                        "cover": cal["guard"]["cover"],
                    },
                    "G1_FULL_A0_same_form": {
                        k: {
                            "cover": (c := L.calibration(f[k][m], yy))["guard"]["cover"],
                            "ok": c["ok"],
                        }
                        for k in ("FULL", "A0")
                    },
                    "later_half_avg_minus_full": float(d_full[day > np.median(day)].mean()),
                }
                if "AVG_A0" in f:
                    r["avg_minus_avg_a0"] = float(((cr["AVG"] - cr["AVG_A0"])[m]).mean())
                if (
                    st == "pts"
                ):  # G2-G5 are pts guards; "199live" keeps the scored contrasts on the frozen 19 form, shown here for completeness
                    r["G2"] = {
                        k: {
                            "n": int(sm[k].sum()),
                            "delta": float(d_a0[sm[k]].mean()) if sm[k].sum() else None,
                        }
                        for k in E.GUARD_SLICES
                    }
                    r["G2_ok"] = all(
                        v["delta"] <= 0.01
                        for v in r["G2"].values()
                        if v["n"] >= 300 and v["delta"] is not None
                    )
                    ts = E.team_share(d_a0, b["team_id"][rows])
                    keep = b["team_id"][rows] != ts["top_team"]
                    r["G3"] = {
                        "top_share": ts["top_share"],
                        "delta_without_top": float(d_a0[keep].mean()),
                        "ok": bool(ts["top_share"] <= 0.25 and d_a0[keep].mean() <= -0.0025),
                    }

                    def tl(
                        q: np.ndarray, n: int, eps: float = eps, yy: np.ndarray = yy
                    ) -> np.ndarray:
                        p = np.clip((q >= n).mean(axis=1), eps, 1 - eps)
                        return np.asarray(-np.where(yy >= n, np.log(p), np.log(1 - p)))

                    tll = {
                        str(n): float((tl(f["AVG"][m], n) - tl(f["A0"][m], n)).mean())
                        for n in (20, 25, 30)
                    }
                    r["G4"] = {"delta": tll, "ok": all(v <= 0.005 for v in tll.values())}
                    late = sm["tip>=19ET(proxy-safe)"]
                    r["G5"] = {
                        "n": int(late.sum()),
                        "delta": float(d_a0[late].mean()),
                        "ok": bool(late.sum() < 300 or d_a0[late].mean() <= -0.005),
                    }
                rec[tag] = r
            out["stats"][st] = rec
            print(st, "G1 ok by form:", {t: rec[t]["G1"]["ok"] for t in forms}, flush=True)
            del off, live
    out["seconds"] = round(time.time() - t0, 1)
    OUT.write_text(json.dumps(out, indent=1, default=float))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
