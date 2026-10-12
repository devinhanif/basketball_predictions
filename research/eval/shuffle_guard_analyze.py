# ruff: noqa: E501
"""Analysis half of the shuffle-guard diagnostic: cache only, no database. See shuffle_guard_diagnostic.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from research.eval import workload_eval as WE

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/ops/shuffle_guard_cache"
OUT_JSON = ROOT / "reports/shuffle_guard_diagnostic_2026-10-12.json"
SEASONS = (2023, 2024)
TOL = -0.001
C, R = "SHUF_CTRL", "SHUF_R"
OBSERVED_2025 = {"delta": -0.0020902912544180415, "ci_lo": -0.003586006622862395, "ci_hi": -0.0006091375177606221, "n": 28173}  # fmt: skip
STAGE1_2024 = -0.0004
REHEARSAL_2024 = 0.0037

# decomposition contrasts: name -> (numerator (heads, pi), baseline (heads, pi))
CONTRASTS: dict[str, tuple[tuple[str, str], tuple[str, str]]] = {
    "both": ((R, R), (C, C)),  # the frozen guard
    "heads_only": ((R, C), (C, C)),
    "pi_only": ((C, R), (C, C)),
    "both_indep_perm": ((R, R + "i"), (C, C + "i")),  # heads seed s, pi seed s+1000
}


def _seeds(season: int) -> list[int]:
    return sorted(int(p.stem.split("seed")[1]) for p in CACHE.glob(f"s{season}_seed*.npz"))


def load(season: int, seed: int) -> dict[str, np.ndarray]:
    z = np.load(CACHE / f"s{season}_seed{seed}.npz")
    return {k: z[k] for k in z.files}


def base(season: int) -> dict[str, np.ndarray]:
    z = np.load(CACHE / f"s{season}_base.npz", allow_pickle=True)
    return {k: z[k] for k in z.files}


def diff(d: dict[str, np.ndarray], con: str) -> np.ndarray:
    (h1, p1), (h0, p0) = CONTRASTS[con]
    out: np.ndarray = d[f"crps__{h1}__{p1}"].astype(float) - d[f"crps__{h0}__{p0}"].astype(float)
    return out


def fires(st: dict[str, float]) -> bool:
    return st["delta"] <= TOL and st["ci_hi"] < 0


def summarize(rows: list[dict[str, float]]) -> dict[str, float]:
    dl = np.array([r["delta"] for r in rows])
    hw = np.array([(r["ci_hi"] - r["ci_lo"]) / 2 for r in rows])
    k = len(dl)
    nf = int(sum(fires(r) for r in rows))
    npos = int(sum(r["delta"] >= -TOL and r["ci_lo"] > 0 for r in rows))
    return {
        "k_seeds": k, "mean": float(dl.mean()), "sd": float(dl.std(ddof=1)), "se_mean": float(dl.std(ddof=1) / np.sqrt(k)),
        "min": float(dl.min()), "max": float(dl.max()), "mean_ci_halfwidth": float(hw.mean()),
        "fire_share": nf / k, "fire_count": nf, "pos_fire_share": npos / k, "share_delta_le_tol": float((dl <= TOL).mean()),
    }  # fmt: skip


def buckets(x: np.ndarray) -> dict[str, np.ndarray]:
    nan = np.isnan(x)
    return {
        "wl_min_last nan": nan,
        "wl_min_last 0-5": ~nan & (x < 5),
        "wl_min_last 5-15": ~nan & (x >= 5) & (x < 15),
        "wl_min_last 15-25": ~nan & (x >= 15) & (x < 25),
        "wl_min_last 25-32": ~nan & (x >= 25) & (x < 32),
        "wl_min_last 32+": ~nan & (x >= 32),
    }


def analyze() -> None:
    res: dict[str, Any] = {"observed_2025": OBSERVED_2025, "tol": TOL, "seasons": {}}
    for season in SEASONS:
        seeds = _seeds(season)
        b = base(season)
        gid = b["gid"].astype(str)
        n = len(gid)
        per: dict[str, list[dict[str, float]]] = {c: [] for c in CONTRASTS}
        acc = {c: np.zeros(n) for c in CONTRASTS}
        stats_pi: list[dict[str, float]] = []
        for s in seeds:
            d = load(season, s)
            for c in CONTRASTS:
                dd = diff(d, c)
                acc[c] += dd
                ci = WE._ci(dd, gid)
                st = {"delta": ci["point"], "ci_lo": ci["lo"], "ci_hi": ci["hi"]}
                per[c].append({k: float(st[k]) for k in ("delta", "ci_lo", "ci_hi")})
            short = b["short"].astype(float)
            dpi = d[f"pi__{R}"] - d[f"pi__{C}"]
            stats_pi.append({
                "mean_pi_C": float(d[f"pi__{C}"].mean()), "mean_pi_R": float(d[f"pi__{R}"].mean()), "obs_short": float(short.mean()),
                "sd_pi_C": float(d[f"pi__{C}"].std()), "sd_pi_R": float(d[f"pi__{R}"].std()), "corr_dpi_short": float(np.corrcoef(dpi, short)[0, 1]),
                "corr_dpi_real_pi": float(np.corrcoef(dpi, b["pic_pi"])[0, 1]),
                "mean_s_C": float(d[f"s__{C}"].mean()), "mean_s_R": float(d[f"s__{R}"].mean()),
                "mean_dc": float((d[f"c__{R}"] - d[f"c__{C}"]).mean()),
                "corr_dc_resid": float(np.corrcoef(d[f"c__{R}"] - d[f"c__{C}"], b["y"] - d[f"c__{C}"])[0, 1]),
            })  # fmt: skip
        summ = {c: summarize(v) for c, v in per.items()}
        # mean over seeds per row, then slices
        mrow = {c: acc[c] / len(seeds) for c in CONTRASTS}
        masks = {
            "all": np.ones(n, bool), "min<5": b["minutes"] < 5, "bench(starter10<.5)": b["starter10"] < 0.5, "starter": b["starter10"] >= 0.5,
            "short flag": b["short"].astype(bool), "not short": ~b["short"].astype(bool), **buckets(b["wl_min_last"].astype(float)),
        }  # fmt: skip
        sl = {}
        for name, mk in masks.items():
            sl[name] = {"n": int(mk.sum()), **{c: float(mrow[c][mk].mean()) for c in CONTRASTS}, "contribution_both": float(mrow["both"][mk].sum() / n)}  # fmt: skip
        ds_seed = np.array([r["delta"] for r in per["both"]])
        order = np.argsort(ds_seed)
        ext = {}
        for tag, idx in (
            ("5_most_negative_seeds", order[:5]),
            ("5_most_positive_seeds", order[-5:]),
        ):
            rowm = np.mean([diff(load(season, seeds[i]), "both") for i in idx], axis=0)
            ext[tag] = {"seeds": [seeds[i] for i in idx], "mean_delta": float(rowm.mean()), "by_slice": {k: float(rowm[m_].mean()) for k, m_ in masks.items() if m_.sum() > 0}}  # fmt: skip
        sp = {k: float(np.mean([r[k] for r in stats_pi])) for k in stats_pi[0]}
        # where does the seed-mean difference come from: min<5 share of total
        nz = sorted(CACHE.glob(f"s{season}_noise_seed*.npz"))
        noise = None
        if nz:
            rows_n = []
            for f in nz:
                sd_ = int(f.stem.split("seed")[1])
                dd = np.load(f)["crps"].astype(float) - load(season, sd_)[f"crps__{C}__{C}"].astype(
                    float
                )
                ci = WE._ci(dd, gid)
                rows_n.append({"delta": ci["point"], "ci_lo": ci["lo"], "ci_hi": ci["hi"]})
            noise = {"note": "(heads, pi) with the five columns replaced by a row-permuted copy, minus (C, C), same label seed", "per_seed": rows_n, **summarize(rows_n)}  # fmt: skip
            noise["same_seeds_real_columns"] = summarize(per["both"][: len(rows_n)])
        res["seasons"][str(season)] = {"noise_column_control": noise,
            "n_rows": n, "n_games": int(len(np.unique(gid))), "seeds": seeds, "summary": summ,
            "per_seed_both": per["both"], "slices_mean_over_seeds": sl, "extreme_seeds": ext, "pi_and_head_diagnostics": sp,
            "z_of_observed_2025_in_season_null": float((OBSERVED_2025["delta"] - summ["both"]["mean"]) / summ["both"]["sd"]),
        }  # fmt: skip
    # pooled fire estimate: both seasons' per-seed deltas, and a shift-free version
    allr = [r for s in SEASONS for r in res["seasons"][str(s)]["per_seed_both"]]
    res["pooled_both"] = summarize(allr)
    # simulate the fire probability at 2025: delta ~ N(mean, sd) from the null distribution, CI halfwidth ~ 2025's own
    hw25 = (OBSERVED_2025["ci_hi"] - OBSERVED_2025["ci_lo"]) / 2
    sim: dict[str, Any] = {}
    rng = np.random.default_rng(0)
    for key, rows in {
        **{str(s): res["seasons"][str(s)]["per_seed_both"] for s in SEASONS},
        "pooled": allr,
    }.items():
        dl = np.array([r["delta"] for r in rows])
        # empirical: P(delta <= tol and delta + hw25 < 0)
        emp = float(np.mean((dl <= TOL) & (dl + hw25 < 0)))
        draws = rng.normal(dl.mean(), dl.std(ddof=1), 200000)
        sim[key] = {"empirical_fire_at_2025_hw": emp, "normal_fire_at_2025_hw": float(np.mean((draws <= TOL) & (draws + hw25 < 0))), "normal_p_delta_le_observed": float(np.mean(draws <= OBSERVED_2025["delta"]))}  # fmt: skip
        # centred (zero-mean) version: what if the construction were unbiased with this sd
        c0 = rng.normal(0.0, dl.std(ddof=1), 200000)
        sim[key]["centred_normal_fire"] = float(np.mean((c0 <= TOL) & (c0 + hw25 < 0)))
    res["fire_estimates_at_2025_ci_halfwidth"] = {"halfwidth": hw25, **sim}
    OUT_JSON.write_text(json.dumps(res, indent=1))
    print(json.dumps({s: res["seasons"][s]["summary"] for s in res["seasons"]}, indent=1))
    print(json.dumps(res["pooled_both"], indent=1))
    print(json.dumps(res["fire_estimates_at_2025_ci_halfwidth"], indent=1))
