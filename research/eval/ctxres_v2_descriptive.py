"""DESCRIPTIVE / EXPLORATORY report for a pulled ctxres_v2 experiment-2 run.

Companion to docs/CTXRES_V2_EXP2_ANALYSIS_PLAN.md. This module takes NO decisions: the only
confirmatory result is ``research.eval.ctxres_v2_eval`` applied to the recorded candidate.
Everything here is labelled DESCRIPTIVE; BH q-values are computed per named family so a reader can
see which
arm-level claims would survive correction, nothing more.

Input: a pulled run dir with ``metrics.json``, ``best_config.json`` and ``oof/*.parquet`` (one file
per variant; columns variant, stat, game_id, player_id, fold_id, y, mean, crps, tll, q19, p_ge).
Memory-light: one arm's columns at a time; ``q19`` / ``p_ge`` are read only when needed.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from scipy.stats import norm

from nba.eval.context_residual_eval import bh_adjust, boot_p_value
from nba.props.metrics import paired_score_delta_ci
from research.eval.ctxres_v2_eval import (
    BIAS_MAX,
    COV_HI,
    COV_LO,
    CRPS_FLOOR,
    PREREG_CANDIDATES,
    STATS,
    _slices,
    calibrator_rule,
    pit_coverage80,
)

REF = "v1_prod"
KEY = ["game_id", "player_id"]
BASE_COLS = ["game_id", "player_id", "fold_id", "stat", "y", "mean", "crps", "tll"]
#: arms whose 2024 results were viewed in experiment 1 (docs/CTXRES_V2.md, honesty notes).
#: xgb_v1_prodcfg is listed because it ran inside the same experiment-1 sweep (treat as seen).
SEEN_EXP1 = frozenset(
    {
        "v1_prod",
        "xgb_v1_prodcfg",
        "xgb_v12_prodcfg",
        "xgb_v1_tuned",
        "xgb_v12_tuned",
        "xgb_v12_rel",
        "xgb_v12_both",
        "xgb_v12_pruned",
        "xgb_v12_quantile",
        "blend_top3",
    }
)
#: NegBin / Poisson arms emit integer ppf grids: A1.1 coverage carries ~+/-0.035 uncertainty.
INTEGER_GRID_ARMS = frozenset({"xgb_v12_poisson_nb", "glm_poisson_nb"})
GROUPS = {
    "a": "own status",
    "b": "returning teammates",
    "c": "possession rates",
    "d": "schedule",
    "e": "opponent vs position",
    "f": "game environment",
    "g": "draft",
    "h": "TV",
    "i": "opp-adjusted ridge",
    "j": "overtime normalisation",
}
CLOSE_MARGIN = CRPS_FLOOR  # "close to the candidate" = upper CI of (arm - candidate) <= +0.005
Q_FAMILY = 0.05


# ------------------------------------------------------------------------------------ io helpers
def variant_files(oof_dir: Path) -> dict[str, Path]:
    """variant name -> parquet path (file stem with ``__`` mapped back to ``|``)."""
    return {p.stem.replace("__", "|"): p for p in sorted(oof_dir.glob("*.parquet"))}


def _read(path: Path, cols: Sequence[str]) -> pl.DataFrame:
    return pl.read_parquet(path, columns=list(cols))


def _stat_frames(path: Path) -> dict[str, pl.DataFrame]:
    df = _read(path, BASE_COLS)
    return {st: df.filter(pl.col("stat") == st).drop("stat") for st in STATS}


def p_from_ci(point: float, lo: float, hi: float) -> float:
    """Normal-approximation two-sided p implied by a 95% CI (for numbers read from metrics.json,
    which carry CIs but no p-values). APPROXIMATE; labelled as such wherever used."""
    se = (hi - lo) / (2 * 1.96)
    if not np.isfinite(se) or se <= 0:
        return 1.0
    return float(min(1.0, 2 * (1 - norm.cdf(abs(point) / se))))


def family_bh(p: Sequence[float]) -> list[float]:
    """BH-adjusted q-values for one family (empty-safe)."""
    if len(p) == 0:
        return []
    return [float(x) for x in bh_adjust(np.asarray(p, dtype=float))]


# ------------------------------------------------------------------------------ paired machinery
def paired(
    a: pl.DataFrame, b: pl.DataFrame, col: str, n_boot: int, seed: int
) -> dict[str, Any] | None:
    """Game-clustered paired comparison of ``a`` minus ``b`` on per-row score ``col`` (lower is
    better): point, 95% CI, clustered two-sided bootstrap p, and month-fold sign consistency."""
    j = a.select([*KEY, "fold_id", col]).join(b.select([*KEY, col]), on=KEY, suffix="_b")
    j = j.filter(pl.col(col).is_finite() & pl.col(f"{col}_b").is_finite())
    if j.height < 2:
        return None
    sa, sb = j[col].to_numpy().astype(float), j[f"{col}_b"].to_numpy().astype(float)
    gid = j["game_id"].to_numpy()
    ci = paired_score_delta_ci(sa, sb, n_boot=n_boot, seed=seed, cluster_ids=gid)
    p = boot_p_value(sa - sb, gid, n_boot, seed)
    per_fold = (
        j.with_columns((pl.col(col) - pl.col(f"{col}_b")).alias("_d"))
        .group_by("fold_id")
        .agg(pl.col("_d").mean())["_d"]
        .to_numpy()
    )
    return {
        "n": j.height,
        "delta": ci.point,
        "lo": ci.lo,
        "hi": ci.hi,
        "p": p,
        "folds_neg": int((per_fold < 0).sum()),
        "folds": int(len(per_fold)),
    }


def _coverage(path: Path, stat: str) -> tuple[float | None, float | None]:
    """(A1.1 PIT coverage, naive y in [q10,q90] coverage) from stored q19; (None, None) if the arm
    stores no q19 grid (light arms)."""
    df = _read(path, ["stat", "y", "q19"]).filter(pl.col("stat") == stat)
    df = df.filter(pl.col("q19").is_not_null())
    if df.height == 0:
        return None, None
    q = np.array(df["q19"].to_list(), dtype=float)
    y = df["y"].to_numpy().astype(float)
    return pit_coverage80(q, y), float(((y >= q[:, 1]) & (y <= q[:, 17])).mean())


def _fmt(x: float | None, nd: int = 4, sign: bool = True) -> str:
    if x is None or not np.isfinite(x):
        return "n/a"
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def _ci(d: dict[str, Any] | None) -> str:
    if d is None:
        return "n/a"
    return f"{d['delta']:+.4f} [{d['lo']:+.4f},{d['hi']:+.4f}]"


def md_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def mde80(d: dict[str, Any] | None) -> float | None:
    """Approximate minimum detectable |delta| at 80% power, alpha 0.05 two-sided: 2.8 x clustered
    SE, with SE recovered from the 95% CI half-width (so it already carries the game clustering)."""
    if d is None:
        return None
    return float(2.8 * (d["hi"] - d["lo"]) / (2 * 1.96))


def _is_base(name: str) -> bool:
    return "|" not in name


# ------------------------------------------------------------------------------------- analysis
def arm_vs_reference(
    files: dict[str, Path],
    cand: str,
    n_boot: int,
    seed: int,
) -> dict[str, Any]:
    """One arm at a time: leaderboard stats, paired CRPS / threshold-LL deltas vs ``v1_prod`` and
    vs the candidate, PIT coverage. Returns small dicts only."""
    ref = _stat_frames(files[REF])
    cfr = _stat_frames(files[cand]) if cand in files else None
    out: dict[str, Any] = {}
    for arm in sorted(n for n in files if _is_base(n)):
        fr = _stat_frames(files[arm])
        per: dict[str, Any] = {}
        for st in STATS:
            d = fr[st]
            if d.is_empty():
                continue
            ok = d.filter(pl.col("crps").is_finite())
            y = d["y"].to_numpy().astype(float)
            m = d["mean"].to_numpy().astype(float)
            cov_pit, cov_naive = _coverage(files[arm], st)
            rec: dict[str, Any] = {
                "n": d.height,
                "crps": float(ok["crps"].mean()) if ok.height else None,  # type: ignore[arg-type]
                "tll": float(d["tll"].mean()) if d["tll"].is_finite().any() else None,  # type: ignore[arg-type]
                "bias": float((m - y).mean()),
                "mae": float(np.abs(m - y).mean()),
                "cov_pit": cov_pit,
                "cov_naive": cov_naive,
                "crps_vs_ref": None,
                "tll_vs_ref": None,
                "crps_vs_cand": None,
            }
            if arm != REF:
                rec["crps_vs_ref"] = paired(d, ref[st], "crps", n_boot, seed)
                rec["tll_vs_ref"] = paired(d, ref[st], "tll", n_boot, seed)
                if cfr is not None and arm != cand:
                    rec["crps_vs_cand"] = paired(d, cfr[st], "crps", n_boot, seed)
            per[st] = rec
        out[arm] = per
        del fr
    return out


def families(res: dict[str, Any]) -> dict[str, list[tuple[str, str, float]]]:
    """Define the descriptive families (see plan section 3) and attach q-values in place.

    F1 CRPS arm-vs-v1_prod, F2 threshold-LL arm-vs-v1_prod, F3 arm-vs-candidate CRPS (screen).
    Each family: every (arm, stat) cell with a defined paired test. Also per-stat q within F1."""
    fam: dict[str, list[tuple[str, str, float]]] = {"F1": [], "F2": [], "F3": []}
    keys = {"F1": "crps_vs_ref", "F2": "tll_vs_ref", "F3": "crps_vs_cand"}
    for arm, per in res.items():
        for st, rec in per.items():
            for f, k in keys.items():
                if rec.get(k) is not None:
                    fam[f].append((arm, st, rec[k]["p"]))
    for f, k in keys.items():
        qs = family_bh([p for _, _, p in fam[f]])
        for (arm, st, _), q in zip(fam[f], qs, strict=True):
            res[arm][st][k]["q_family"] = q
    for st in STATS:  # per-stat q within F1 (shows how much the pooled correction costs)
        cells = [(a, res[a][st]) for a in res if st in res[a] and res[a][st].get("crps_vs_ref")]
        qs = family_bh([c["crps_vs_ref"]["p"] for _, c in cells])
        for (_, c), q in zip(cells, qs, strict=True):
            c["crps_vs_ref"]["q_stat"] = q
    return fam


def gate_flag(d: dict[str, Any] | None) -> str:
    """Descriptive analogue of checks 1-3 only (floor, CI, family q). NOT the keep rule."""
    if d is None:
        return "n/a"
    ok = d["delta"] <= -CRPS_FLOOR and d["hi"] < 0 and d.get("q_family", 1.0) <= Q_FAMILY
    return "survives" if ok else "no"


def ablation_rows(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """Drop-one ablation from metrics.json (value = CRPS(drop) - CRPS(full xgb_v12_tuned); >0 means
    the group helps). CIs only, so p is the CI-implied normal approximation; BH over all cells."""
    ab = metrics.get("ablation_drop_group_2024") or {}
    cells: list[dict[str, Any]] = []
    for g in sorted(ab):
        for st in STATS:
            v = ab[g].get(st)
            if not v:
                continue
            cells.append(
                {"group": g, "stat": st, "d": v[0], "lo": v[1], "hi": v[2], "p": p_from_ci(*v)}
            )
    for c, q in zip(cells, family_bh([c["p"] for c in cells]), strict=True):
        c["q"] = q
    return cells


def calibration_rows(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """Every ``arm|kind`` calibrator vs its own uncalibrated arm, from metrics.json
    (``d_tll_vs_ref`` = variant minus base, negative is better). Family F4 = all cells."""
    cal = metrics.get("calibration_2024") or {}
    cells: list[dict[str, Any]] = []
    for name in sorted(cal):
        for st in STATS:
            v = cal[name].get(st) or {}
            d = v.get("d_tll_vs_ref")
            if d:
                cells.append(
                    {
                        "variant": name,
                        "stat": st,
                        "d": d[0],
                        "lo": d[1],
                        "hi": d[2],
                        "ece": v.get("ece"),
                        "p": p_from_ci(*d),
                    }
                )
    for c, q in zip(cells, family_bh([c["p"] for c in cells]), strict=True):
        c["q"] = q
    return cells


def blend_vs_best(files: dict[str, Path], best: str, n_boot: int, seed: int) -> dict[str, Any]:
    if "blend_top3" not in files or best not in files:
        return {}
    a, b = _stat_frames(files["blend_top3"]), _stat_frames(files[best])
    return {st: paired(a[st], b[st], "crps", n_boot, seed) for st in STATS}


def candidate_calibrators(
    files: dict[str, Path], cand: str, thresholds: dict[str, list[int]], n_boot: int
) -> dict[str, Any]:
    """Re-apply the pre-registered calibrator rule helper to the candidate (read-only recompute;
    the decision-bearing run is ``ctxres_v2_eval``)."""
    names = [cand, *(f"{cand}|{k}" for k in ("platt", "iso", "conf", "pit"))]
    parts = [pl.read_parquet(files[n]) for n in names if n in files]
    if len(parts) < 2:
        return {}
    oof = pl.concat(parts, how="diagonal_relaxed")
    return {
        k: calibrator_rule(oof, cand, k, thresholds, n_boot=n_boot)
        for k in ("platt", "iso", "conf", "pit")
    }


# --------------------------------------------------------------------------------------- report
def describe(
    run_dir: str,
    export_path: str | None = None,
    verdict_path: str | None = None,
    n_boot: int = 2000,
    seed: int = 0,
    slice_arms: Sequence[str] | None = None,
    prev_metrics: str | None = None,
) -> str:
    rd = Path(run_dir)
    metrics = json.loads((rd / "metrics.json").read_text())
    cfg = json.loads((rd / "best_config.json").read_text())
    files = variant_files(rd / "oof")
    if REF not in files:
        raise FileNotFoundError(f"{REF} missing from {rd / 'oof'}")
    tag = str(metrics.get("experiment_tag") or "")
    cand = PREREG_CANDIDATES.get(tag) or cfg.get("best_arm_2023")
    if not cand:
        raise ValueError("no candidate recoverable from the run")
    res = arm_vs_reference(files, cand, n_boot, seed)
    fam = families(res)
    L: list[str] = []
    L.append("# ctxres_v2 experiment 2 -- DESCRIPTIVE report (not a decision)")
    L.append("")
    L.append(
        f"generated {datetime.now().isoformat(timespec='seconds')}; run `{rd.name}`; tag `{tag}`"
    )
    L.append(f"bootstrap: game-clustered, n_boot={n_boot}, seed={seed}; season-2024 played rows.")
    L.append(
        f"Only the recorded candidate `{cand}` has a confirmatory verdict "
        "(`research.eval.ctxres_v2_eval`); every p, q and CI below is exploratory."
    )
    L.append(
        f"Run's own 2023 best: `{metrics.get('best_arm_2023')}`; recorded candidate in run: "
        f"`{metrics.get('recorded_candidate')}`; catboost_available="
        f"`{metrics.get('catboost_available')}`; budget `{metrics.get('budget')}`."
    )
    L.append("")
    L.append(
        "Family sizes: " + ", ".join(f"{k}={len(v)}" for k, v in fam.items()) + " (BH q=0.05)."
    )
    L.append("")
    rows: list[Any]
    # ---- confirmatory pointer
    if verdict_path and Path(verdict_path).exists():
        v = json.loads(Path(verdict_path).read_text())
        L.append("## 0. Confirmatory verdict (copied from the pre-registered eval, unaltered)")
        L.append("")
        rows = []
        for st, r in v["verdict_per_stat"].items():
            c = r["crps_delta"]
            failed = [k for k, ok in r["checks"].items() if not ok]
            rows.append(
                (
                    st,
                    r["n"],
                    f"{c[0]:+.4f} [{c[1]:+.4f},{c[2]:+.4f}]",
                    f"{r['bias']:+.3f}",
                    f"{r['cov80_pit']:.3f}",
                    f"{r['bh_adj_p']:.3g}",
                    r["keep"],
                    failed,
                )
            )
        L += md_table(
            ["stat", "n", "dCRPS [CI]", "bias", "PIT cov80", "BH q (m=4)", "keep", "failed"], rows
        )
        L.append("")
        L.append(
            f"v2 kept overall: **{v['v2_kept_overall']}** (candidate `{v['candidate']}`, "
            f"coverage check `{v.get('coverage_check')}`)."
        )
        L.append("")
    # ---- leaderboard
    L.append("## 1. Leaderboard, 2024, every arm vs v1_prod (DESCRIPTIVE; F1 = CRPS family)")
    L.append("")
    L.append(
        "delta = arm - v1_prod (negative is better). q_fam = BH over all F1 cells; q_stat = BH "
        "within the stat. `gate` = floor -0.005 & CI<0 & q_fam<=0.05 (checks 1-3 analogue). "
        "folds = months with negative delta / months. `seen1` = arm's 2024 numbers were viewed in "
        "experiment 1 (not independent)."
    )
    L.append("")
    for st in STATS:
        rows = []
        cells = [(a, res[a][st]) for a in res if st in res[a]]
        cells.sort(key=lambda t: (t[1]["crps"] is None, t[1]["crps"] or 0.0))
        for a, r in cells:
            c = r["crps_vs_ref"]
            cov = r["cov_pit"] if r["cov_pit"] is not None else None
            cov_s = _fmt(cov, 3, False) if cov is not None else "n/a"
            if cov is not None and a in INTEGER_GRID_ARMS:
                cov_s += "*"
            rows.append(
                (
                    a + (" (CAND)" if a == cand else ""),
                    "Y" if a in SEEN_EXP1 else "N",
                    r["n"],
                    _fmt(r["crps"], 4, False),
                    _ci(c),
                    _fmt(c["q_family"], 3, False) if c else "n/a",
                    _fmt(c["q_stat"], 3, False) if c else "n/a",
                    _fmt(mde80(c), 4, False),
                    gate_flag(c),
                    f"{c['folds_neg']}/{c['folds']}" if c else "n/a",
                    _fmt(r["bias"], 3),
                    cov_s,
                )
            )
        L.append(f"### {st}")
        L.append("")
        L += md_table(
            [
                "arm",
                "seen1",
                "n",
                "CRPS",
                "dCRPS vs v1 [CI]",
                "q_fam",
                "q_stat",
                "MDE80",
                "gate",
                "folds",
                "bias",
                "PIT cov80",
            ],
            rows,
        )
        L.append("")
    L.append("`*` integer-grid arm: A1.1 coverage has about +/-0.035 estimator uncertainty.")
    L.append(
        "PIT cov80 n/a = arm stored no q19 grid (light arm); naive coverage is in metrics.json."
    )
    L.append("")
    # ---- thresholds
    L.append("## 2. Threshold log loss vs v1_prod (DESCRIPTIVE; F2)")
    L.append("")
    for st in STATS:
        rows = []
        for a in sorted(res):
            r = res[a].get(st)
            c = r["tll_vs_ref"] if r else None
            if c:
                rows.append((a, _fmt(r["tll"], 4, False), _ci(c), _fmt(c["q_family"], 3, False)))
        L.append(f"### {st}")
        L.append("")
        L += md_table(["arm", "thr logloss", "d vs v1 [CI]", "q_fam"], rows)
        L.append("")
    # ---- vs candidate
    L.append("## 3. Arms vs the candidate (DESCRIPTIVE; F3, non-inferiority screen)")
    L.append("")
    L.append(
        f"delta = arm - `{cand}` CRPS. `close` = CI upper bound <= +{CLOSE_MARGIN} (cannot be "
        "ruled worse than the floor); `better` = CI upper < 0. These are router/stack screens, "
        "not decisions."
    )
    L.append("")
    rows = []
    for a in sorted(res):
        if a in (cand, REF):
            continue
        vs_cells = []
        for st in STATS:
            c = (res[a].get(st) or {}).get("crps_vs_cand")
            if c is None:
                vs_cells.append("n/a")
                continue
            tag3 = "better" if c["hi"] < 0 else ("close" if c["hi"] <= CLOSE_MARGIN else "worse?")
            vs_cells.append(f"{c['delta']:+.4f} [{c['lo']:+.4f},{c['hi']:+.4f}] {tag3}")
        rows.append((a, *vs_cells))
    L += md_table(["arm", *STATS], rows)
    L.append("")
    bb = blend_vs_best(files, cand, n_boot, seed)
    if bb:
        L.append(f"### blend_top3 minus `{cand}` (single-model vs blend)")
        L.append("")
        qb = family_bh([bb[st]["p"] for st in STATS if bb[st]])
        it = iter(qb)
        L += md_table(
            ["stat", "dCRPS [CI]", "p", "q (F6, m=4)"],
            [
                (st, _ci(bb[st]), _fmt(bb[st]["p"], 4, False), _fmt(next(it), 4, False))
                if bb[st]
                else (st, "n/a", "n/a", "n/a")
                for st in STATS
            ],
        )
        L.append("")
    # ---- ablation
    ab = ablation_rows(metrics)
    L.append("## 4. Drop-one feature-group ablation (DESCRIPTIVE; F5, all 40 cells)")
    L.append("")
    L.append(
        "value = CRPS(drop group) - CRPS(full xgb_v12_tuned), 2024; positive = group helps. "
        "CIs come from metrics.json (200 resamples); p is CI-implied normal approx. "
        "`useless` = CI includes 0 or value < floor."
    )
    L.append("")
    rows = [
        (
            f"{c['group']} {GROUPS.get(c['group'], '')}",
            c["stat"],
            f"{c['d']:+.4f} [{c['lo']:+.4f},{c['hi']:+.4f}]",
            f"{c['q']:.3g}",
            "helps" if c["lo"] > 0 and c["d"] >= CRPS_FLOOR else "useless/unclear",
        )
        for c in ab
    ]
    L += md_table(["group", "stat", "drop - full [CI]", "q (F5)", "reading"], rows)
    L.append("")
    # ---- calibration
    L.append("## 5. Calibrators")
    L.append("")
    cc = candidate_calibrators(files, cand, cfg.get("thresholds", {}), n_boot)
    if cc:
        L.append(
            f"### Pre-registered calibrator rule on `{cand}` (recomputed; decision lives in eval)"
        )
        L.append("")
        rows = []
        for k, per in cc.items():
            for st in STATS:
                v = per.get(st, {})
                td = v.get("tll_delta")
                ece = v.get("ece")
                rows.append(
                    (
                        k,
                        st,
                        f"{td[0]:+.4f} [{td[1]:+.4f},{td[2]:+.4f}]" if td else "n/a",
                        f"{ece[0]:.4f} -> {ece[1]:.4f}" if ece else "n/a",
                        v.get("keep"),
                    )
                )
        L += md_table(["calibrator", "stat", "d thr-LL [CI]", "ECE old->new", "rule keep"], rows)
        L.append("")
    cal = calibration_rows(metrics)
    sig = [c for c in cal if c["q"] <= Q_FAMILY and c["d"] < 0]
    worse = [c for c in cal if c["q"] <= Q_FAMILY and c["d"] > 0]
    L.append(
        f"### All calibrator variants (F4, {len(cal)} cells; approx p): "
        f"{len(sig)} significantly improve thr-LL, {len(worse)} significantly worsen it."
    )
    L.append("")
    rows = [
        (c["variant"], c["stat"], f"{c['d']:+.4f} [{c['lo']:+.4f},{c['hi']:+.4f}]", f"{c['q']:.3g}")
        for c in cal
        if c["q"] <= Q_FAMILY
    ]
    L += md_table(["variant", "stat", "d thr-LL [CI]", "q (F4)"], rows or [("none", "", "", "")])
    L.append("")
    # ---- zero inflation
    zi = metrics.get("zero_inflation_fg3m_2024") or {}
    if zi:
        L.append("## 6. fg3m zero-inflation check (2024)")
        L.append("")
        L += md_table(
            ["arm", "observed P(0)", "predicted P(0)", "gap"],
            [
                (
                    a,
                    f"{v['obs_p0']:.3f}",
                    f"{v['pred_p0']:.3f}",
                    f"{v['pred_p0'] - v['obs_p0']:+.3f}",
                )
                for a, v in zi.items()
            ],
        )
        L.append("")
    # ---- drift
    dr = metrics.get("drift") or {}
    if dr:
        L.append("## 7. Drift table (season means; shift = in prior-season std units)")
        L.append("")
        cols = ["rows", "pace_proxy_game_total", "three_pa_share_proxy", "usage_proxy"]
        cols += [f"{s}_mean" for s in STATS] + [f"{s}_shift_in_std_vs_prev" for s in STATS]
        rows = [(sea, *(_fmt(dr[sea].get(c), 3, False) for c in cols)) for sea in sorted(dr)]
        L += md_table(["season", *cols], rows)
        L.append("")
    # ---- slices
    if export_path and Path(export_path).exists():
        export = pl.read_parquet(export_path)
        arms = list(slice_arms) if slice_arms else [cand]
        L.append("## 8. Slices (DESCRIPTIVE; arm - v1_prod CRPS; flag > +0.01 with n >= 300)")
        L.append("")
        ref = _stat_frames(files[REF])
        for a in arms:
            if a not in files or a == REF:
                continue
            fr = _stat_frames(files[a])
            rows = []
            for st in STATS:
                pr = fr[st].join(ref[st], on=KEY, suffix="_b")
                pr = pr.filter(pl.col("crps").is_finite() & pl.col("crps_b").is_finite())
                for s in _slices(pr, export, n_boot):
                    flag = "REGRESSION" if s["n"] >= 300 and s["delta"] > 0.01 else ""
                    rows.append((a, st, s["slice"], s["n"], f"{s['delta']:+.4f}", flag))
            L += md_table(["arm", "stat", "slice", "n", "dCRPS", "flag"], rows)
            L.append("")
    # ---- reproducibility vs experiment 1
    if prev_metrics and Path(prev_metrics).exists():
        pm = json.loads(Path(prev_metrics).read_text()).get("leaderboard_2024") or {}
        rows = []
        for a in sorted(res):
            if a not in pm:
                continue
            cells2 = []
            for st in STATS:
                old = (pm[a].get(st) or {}).get("crps")
                new = (res[a].get(st) or {}).get("crps")
                cells2.append("n/a" if old is None or new is None else f"{new - old:+.5f}")
            rows.append((a, *cells2))
        L.append(
            "## 8b. Reproducibility: exp-2 minus exp-1 2024 CRPS (same rows; NOT confirmation)"
        )
        L.append("")
        L += md_table(["arm", *STATS], rows or [("none", "", "", "", "")])
        L.append("")
    # ---- reconciliation vs metrics.json
    lb = metrics.get("leaderboard_2024") or {}
    bad = []
    for a, per in res.items():
        for st, r in per.items():
            m = ((lb.get(a) or {}).get(st) or {}).get("crps")
            if m is not None and r["crps"] is not None and abs(m - r["crps"]) > 1e-4:
                bad.append(f"{a}/{st}: parquet {r['crps']:.5f} vs metrics {m:.5f}")
    L.append("## 9. Reconciliation (parquet recomputation vs metrics.json leaderboard)")
    L.append("")
    L.append("all CRPS means agree within 1e-4" if not bad else "MISMATCH: " + "; ".join(bad))
    L.append("")
    sel = metrics.get("selection_scores_2023") or {}
    if sel:
        L.append("## 10. 2023 selection scores (ratio to recency-Normal; selection season)")
        L.append("")
        L += md_table(
            ["arm", "ratio"],
            [(a, f"{s:.4f}") for a, s in sorted(sel.items(), key=lambda t: _num(t[1]))],
        )
        L.append("")
    L.append(
        f"Bias limit used in confirmatory rule: +/-{BIAS_MAX}; coverage window {COV_LO}-{COV_HI}."
    )
    return "\n".join(L)


def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("inf")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Descriptive report for a ctxres_v2 exp-2 run")
    ap.add_argument("--run", required=True, help="pulled run dir")
    ap.add_argument("--export", default="data/colab/ctxres_v2/ctxres_v2.parquet")
    ap.add_argument("--verdict", default=None, help="verdict JSON from ctxres_v2_eval --out")
    ap.add_argument("--out", default=None, help="markdown output path")
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--prev-metrics", default=None, help="experiment-1 metrics.json (repro table)")
    ap.add_argument("--slice-arms", default="", help="comma list; default = candidate only")
    a = ap.parse_args(argv)
    arms = [x for x in a.slice_arms.split(",") if x] or None
    text = describe(a.run, a.export, a.verdict, a.n_boot, a.seed, arms, a.prev_metrics)
    if a.out:
        Path(a.out).write_text(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
