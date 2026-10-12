# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,call-overload,attr-defined,misc,no-any-return,type-var"
"""DESCRIPTIVE slices of the SEQ learned blend without the ROUTER_G2 gate, and its overlap with RECENT_ROLE_FULL (Devin, DECISIONS e24b56c, item A).

Seasons 2023 / 2024 only, from stored arrays only; the only fit is the existing walk-forward learned blend
(`near_miss_bundle_eval.learned_blend`) reused on stored members. No rule is tested here; nothing is a claim and nothing is frozen.

Arms (integer 199-grids, scored on the integer support):
  A0      production + stored lower-tail mixture
  SEQc    T442's learned blend of [A0 mixture, SEQ-E2], used on regular-season rows (game_id prefix 002), postseason / play-in -> A0
  E2p     the same blend used only where ROUTER_G2's cached g_eff >= 0.90, else A0 (the gated arm, T464)
  FULL    RECENT_ROLE_FULL: heads + pi refit with the recent-role columns (stored caches), scored through the stored mixture
  AVG     50/50 pmf mixture of SEQc and FULL
  FSc     learned blend of [FULL, SEQ-E2] (the existing blend code, first stage FULL), regular rows, postseason -> FULL

    .venv/bin/python -W ignore -m research.eval.seq_blend_slices [--stats pts reb ast fg3m]
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

import numpy as np

from nba.eval.context_residual_eval import crps_from_quantiles
from nba.eval.lower_tail_eval import arm_grid, load_components
from nba.props.context_residual import to_integer_support
from research.eval import ensemble_eval as E
from research.eval import near_miss_bundle_eval as N
from research.eval import recent_role_full_eval as F
from research.eval import router_g2_eval as G
from research.eval import workload_eval as WE

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports/seq_blend_slices_2026-10-12/results.json"
SEASONS = (2023, 2024)
TAU = 0.90
N_BOOT_CORR = 1000


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def corr_ci(
    x: np.ndarray, y: np.ndarray, gid: np.ndarray, n_boot: int = N_BOOT_CORR
) -> dict[str, float]:
    """Pearson correlation with a game-clustered bootstrap CI (games resampled, per-game sufficient statistics)."""
    u, inv = np.unique(gid, return_inverse=True)
    k = len(u)
    s = np.stack(
        [np.bincount(inv, w, k) for w in (np.ones(len(x)), x, y, x * x, y * y, x * y)], axis=1
    )

    def r(t: np.ndarray) -> float:
        n, sx, sy, sxx, syy, sxy = t
        cov = sxy / n - sx * sy / n**2
        vx, vy = sxx / n - (sx / n) ** 2, syy / n - (sy / n) ** 2
        return float(cov / np.sqrt(vx * vy)) if vx > 0 and vy > 0 else float("nan")

    rng = np.random.default_rng(0)
    draws = []
    for _ in range(n_boot):
        c = np.bincount(rng.integers(0, k, k), minlength=k)
        draws.append(r(c @ s))
    lo, hi = np.nanpercentile(draws, [2.5, 97.5])
    return {"r": r(s.sum(axis=0)), "lo": float(lo), "hi": float(hi)}


def arm_report(stat: str, b: dict[str, Any], q: np.ndarray, ci_slices: bool) -> dict[str, Any]:
    rows = np.arange(len(b["y"]))
    ev = E.evaluate_arm(stat, b, rows, q, ci_slices=ci_slices, dk=stat == "pts")
    out: dict[str, Any] = {}
    for s in SEASONS:
        r = ev[str(s)]
        g = E.guards_for(
            {k: v for k, v in r.items() if not k.startswith("_")}, stat, e1_like=stat == "pts"
        )
        sl = r["slices"]["minutes<5"]
        g["g2b_minutes<5"] = bool(sl["n"] < E.MIN_SLICE_N or sl.get("point", -1.0) <= E.SLICE_WORSE)
        out[str(s)] = {
            "n": r["n"], "n_games": r["n_games"], "crps_arm": r["crps_arm"], "crps_a0": r["crps_a0"],
            "delta": r["delta"], "calibration": r["calibration"], "slices": r["slices"],
            "team_share": r["team_share"], "tll_dk": r.get("tll_dk"), "tll_fixed": r.get("tll_fixed"),
            "guards": g, "guards_all_pass": bool(all(g.values())),
        }  # fmt: skip
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", nargs="+", default=list(E.STATS))
    args = ap.parse_args(argv)
    with WE.heavy():
        _log("loading world (stored arrays only, seasons <= 2024)")
        w = N.load_world()
        z = np.load(G.CACHE / "gates.npz", allow_pickle=True)
        b0 = w["base"]["pts"]
        if not (
            np.array_equal(z["gid"].astype(str), b0["game_id"])
            and np.array_equal(z["pid"], b0["player_id"])
        ):
            raise RuntimeError("cached gates are not aligned with the A0 rows")
        geff = z["geff"]
        pic = dict(np.load(F.LT_DIR / "components_pi.npz", allow_pickle=True))
        pi_r = np.load(F.pi_path("R"))["pi"]
        res: dict[str, Any] = {
            "note": "DESCRIPTIVE; stored arrays; seasons 2023/2024; no 2025",
            "tau": TAU,
            "stats": {},
        }
        for st in args.stats:
            t0 = time.time()
            b = w["base"][st]
            y, gid, season = b["y"], b["game_id"], b["season"]
            reg = np.array([str(x)[:3] == "002" for x in gid])
            a0 = b["a0_int"]
            _log(f"{st}: SEQ blend (first stage A0 mixture)")
            pooled, wts = N.learned_blend(
                b["mix"], w["seq"][st], y, w["date_days"], season, w["ctx"], st
            )
            e2 = to_integer_support(pooled).astype(np.int16)
            seqc = G.route(a0, e2, reg.astype(float), tau=0.5)
            e2p = G.route(a0, e2, geff, TAU)
            _log(f"{st}: FULL from stored caches")
            comp = load_components(st, F.LT_DIR)
            comp_full = F.load_heads(st, "R")
            if not F.same_rows(comp_full, comp) or not (
                np.array_equal(comp["row_gid"].astype(str), gid)
                and np.array_equal(comp["row_pid"].astype(np.int64), b["player_id"])
            ):
                raise RuntimeError(f"{st}: FULL rows are not the A0 rows")
            full = F.arm_qi(st, comp_full, pic, pi_r)
            a0_chk = WE.mixture_qi(comp, pic, st)
            if not np.array_equal(a0_chk, a0):
                raise RuntimeError(f"{st}: A0 from the FULL side is not the ensemble A0")
            avg = E.mix_integer_quantiles(seqc, full, np.full(len(y), 0.5))
            _log(f"{st}: learned blend of [FULL, SEQ]")
            full_cont = arm_grid(
                "mixture", comp_full, F.PR.candidate_pic(pic, pi_r), st
            )  # continuous grid, T442's first-stage support
            pooled_f, _ = N.learned_blend(
                full_cont, w["seq"][st], y, w["date_days"], season, w["ctx"], st
            )
            fsc = G.route(
                full, to_integer_support(pooled_f).astype(np.int16), reg.astype(float), tau=0.5
            )
            half = np.full(len(y), 0.5)
            comp_pl = F.load_heads(st, "PL")
            pl_full = F.arm_qi(st, comp_pl, pic, np.load(F.pi_path("PL"))["pi"])
            arms = {
                "A0": a0,
                "SEQc": seqc,
                "E2p": e2p,
                "FULL": full,
                "AVG": avg,
                "FSc": fsc,
                "AVG_gated": E.mix_integer_quantiles(e2p, full, half),  # E2p (gated) with FULL
                "AVG_SEQc_A0": E.mix_integer_quantiles(
                    seqc, a0, half
                ),  # SEQc with an uninformative partner (the mixture effect alone)
                "AVG_SEQc_PLFULL": E.mix_integer_quantiles(
                    seqc, pl_full, half
                ),  # SEQc with FULL's past-only placebo
                "PL_FULL": pl_full,
            }
            crps = {k: crps_from_quantiles(v.astype(float), y) for k, v in arms.items()}
            rec: dict[str, Any] = {"arms": {}, "contrasts": {}, "corr": {}, "rows_routed_share": {}}
            for k in ("SEQc", "E2p", "FULL", "AVG", "FSc", "AVG_gated"):
                rec["arms"][k] = arm_report(st, b, arms[k], ci_slices=st == "pts")
                _log(f"{st}: {k} guards done")
            for a, c in (
                ("SEQc", "A0"),
                ("E2p", "A0"),
                ("FULL", "A0"),
                ("AVG", "A0"),
                ("FSc", "A0"),
                ("AVG", "FULL"),
                ("AVG", "SEQc"),
                ("SEQc", "FULL"),
                ("FSc", "FULL"),
                ("FSc", "SEQc"),
                ("E2p", "SEQc"),
                ("AVG_gated", "A0"),
                ("AVG_gated", "FULL"),
                ("AVG_gated", "E2p"),
                ("AVG_SEQc_A0", "A0"),
                ("AVG_SEQc_PLFULL", "A0"),
                ("PL_FULL", "A0"),
                ("AVG", "AVG_SEQc_A0"),
                ("AVG", "AVG_SEQc_PLFULL"),
            ):
                d = crps[a] - crps[c]
                rec["contrasts"][f"{a}_minus_{c}"] = {
                    str(s): {
                        "n": int((season == s).sum()),
                        **E.clustered(d[season == s], gid[season == s]),
                    }
                    for s in SEASONS
                }
            mins = b["minutes"]
            for s in SEASONS:
                sm = season == s
                masks = {
                    "all": sm,
                    "regular": sm & reg,
                    "minutes>=5": sm & (mins >= 5),
                    "minutes<5": sm & (mins < 5),
                }
                gs, gf, ge = (
                    crps["A0"] - crps["SEQc"],
                    crps["A0"] - crps["FULL"],
                    crps["A0"] - crps["E2p"],
                )
                rec["corr"][str(s)] = {
                    k: {
                        "n": int(m.sum()),
                        "seqc_vs_full": corr_ci(gs[m], gf[m], gid[m]),
                        "e2p_vs_full": corr_ci(ge[m], gf[m], gid[m]),
                        "mean_gain_seqc": float(gs[m].mean()),
                        "mean_gain_full": float(gf[m].mean()),
                    }
                    for k, m in masks.items()
                }
                rec["rows_routed_share"][str(s)] = {
                    "regular": float(reg[sm].mean()),
                    "geff>=tau": float((geff[sm] >= TAU).mean()),
                }
                rec.setdefault("crps", {})[str(s)] = {
                    k: float(v[sm].mean()) for k, v in crps.items()
                }
            res["stats"][st] = rec
            WE.write_json(OUT, res)
            _log(f"{st}: done in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
