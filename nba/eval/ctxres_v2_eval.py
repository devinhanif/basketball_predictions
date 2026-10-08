"""Apply the PRE-REGISTERED ctxres_v2 keep rule to the pulled Colab artifacts.

PRE-REGISTERED DECISION RULE (written 2026-10-08, BEFORE any Colab run; full text in
docs/CTXRES_V2.md; not changed after seeing results; a negative result is reported as such)
------------------------------------------------------------------------------------------
* Candidate = the arm chosen by SEASON-2023 selection only (``best_arm_2023`` in
  ``best_config.json``: lowest mean over the 4 stats of CRPS / CRPS of the recency-Normal
  baseline on 2023 blocks). It is compared with PRODUCTION v1 (``v1_prod``: the production
  LightGBM residual config on v1 features, re-run inside the same walk-forward) on SEASON-2024
  rows (report-only; never used for any choice). Season 2025 is never touched; one clean
  confirmatory 2025 touch is allowed later under a separate pre-registration.
* KEEP v2 for a stat iff ALL hold on 2024 rows:
    1. mean CRPS delta (candidate - v1) <= -0.005 (floor);
    2. game-clustered 95% bootstrap CI of the delta excludes 0 (upper < 0);
    3. the clustered two-sided p-value survives Benjamini-Hochberg across the 4 stats, q = 0.05;
    4. no slice (teammate-out report status, minutes-change bucket, first-15 vs rest team games,
       starter vs bench; n >= 300) has a CRPS delta > +0.01;
    5. mean bias within +/-0.5 of the actual stat;
    6. 80% interval coverage within 0.75-0.85 (experiment 1: y in [q10, q90]; experiment 2+:
       PIT coverage, Amendment A1.1 in docs/CTXRES_V2.md);
  and the run's leak audit passed (``metrics.json`` carries ``leak_audit``).
* v2 is KEPT overall only if every one of the 4 stats is kept; otherwise report per stat.
* CALIBRATOR rule (applied to the candidate arm, per calibrator, per stat, on 2024 threshold
  events): kept only if the threshold log loss improves with the clustered CI excluding 0, OR the
  equal-mass-bin ECE improves by >= 0.005 without worsening log loss (CI upper <= +0.001).
* Diagnostics only: MAE, per-block CRPS, leaderboard of all arms, ablation by feature group,
  season-relative and pruned-feature arms, blend vs best single arm, drift table.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.eval.context_residual_eval import (
    SLICE_COLS,
    SLICE_MAX_REGRESSION,
    SLICE_MIN_N,
    add_slice_columns,
    bh_adjust,
    boot_p_value,
)
from nba.props.metrics import paired_score_delta_ci

STATS = ("pts", "reb", "ast", "fg3m")
CRPS_FLOOR = 0.005
BIAS_MAX = 0.5
COV_LO, COV_HI = 0.75, 0.85
ECE_GAIN = 0.005
TLL_NO_WORSE = 0.001
N_BOOT = 2000


def _mat(col: pl.Series) -> np.ndarray:
    return np.array(col.to_list(), dtype=float)


GRID19 = np.arange(1, 20) / 20.0


def cdf_from_q19(q19: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Interpolated predictive CDF from the 19-quantile grid (levels 0.05..0.95).

    Interior: linear interpolation between knots (tied knots resolve to the largest level).
    Tails: linear extrapolation from the two outer knots (slope 0.05 / knot gap; flat when the
    two outer knots tie), clipped to [0, 1]. F(x) = 0 for x < 0 (support floor)."""
    q = np.asarray(q19, dtype=float)
    x = np.asarray(x, dtype=float)
    ar = np.arange(len(x))
    c = (q <= x[:, None]).sum(axis=1)
    lo, hi = np.clip(c - 1, 0, 18), np.clip(c, 0, 18)
    ql, qh = q[ar, lo], q[ar, hi]
    frac = np.where(qh > ql, (x - ql) / np.where(qh > ql, qh - ql, 1.0), 0.0)
    f_int = GRID19[lo] + np.clip(frac, 0.0, 1.0) * (GRID19[hi] - GRID19[lo])
    g_lo, g_hi = q[:, 1] - q[:, 0], q[:, 18] - q[:, 17]
    s_lo = np.where(g_lo > 0, 0.05 / np.where(g_lo > 0, g_lo, 1.0), 0.0)
    s_hi = np.where(g_hi > 0, 0.05 / np.where(g_hi > 0, g_hi, 1.0), 0.0)
    f_lo = np.clip(GRID19[0] - s_lo * (q[:, 0] - x), 0.0, 1.0)
    f_hi = np.clip(GRID19[18] + s_hi * (x - q[:, 18]), 0.0, 1.0)
    f_hi = np.where((x > q[:, 18]) & (s_hi == 0), 1.0, f_hi)
    f = np.where(c == 0, f_lo, np.where(c >= 19, f_hi, f_int))
    return np.where(x < 0, 0.0, f)


def pit_coverage80(q19: np.ndarray, y: np.ndarray) -> float:
    """Central-80% PIT coverage for integer outcomes (Amendment A1.1).

    Continuity-corrected CDF F_cc(y) = F(y + 0.5), F_cc(y - 1) = F(y - 0.5) with the interpolated
    ``cdf_from_q19``; coverage = mean over rows of the Czado-Gneiting-Held NON-randomized PIT mass
    in [0.1, 0.9] (deterministic, no seed): E[1(0.1 <= u <= 0.9)], u ~ U(F_cc(y-1), F_cc(y))."""
    y = np.asarray(y, dtype=float)
    hi = cdf_from_q19(q19, y + 0.5)
    lo = cdf_from_q19(q19, y - 0.5)
    w = np.maximum(hi - lo, 1e-12)

    def fbar(u: float) -> np.ndarray:
        return np.asarray(np.clip((u - lo) / w, 0.0, 1.0))

    return float((fbar(0.9) - fbar(0.1)).mean())


def _pair(oof: pl.DataFrame, a: str, b: str, stat: str) -> pl.DataFrame:
    x = oof.filter((pl.col("variant") == a) & (pl.col("stat") == stat))
    y = oof.filter((pl.col("variant") == b) & (pl.col("stat") == stat))
    return x.join(y, on=["game_id", "player_id"], suffix="_b")


def ece_equal_mass(p: np.ndarray, ev: np.ndarray, bins: int = 10) -> float:
    o = np.argsort(p.ravel())
    pp, ee = p.ravel()[o], ev.ravel()[o].astype(float)
    parts = np.array_split(np.arange(len(pp)), bins)
    return float(sum(len(g) * abs(pp[g].mean() - ee[g].mean()) for g in parts) / len(pp))


def _slices(pair: pl.DataFrame, export: pl.DataFrame, n_boot: int) -> list[dict[str, Any]]:
    cols = [
        "game_id",
        "player_id",
        "has_report",
        "n_out_rot",
        "min_gap",
        "team_game_no",
        "starter10",
    ]
    j = add_slice_columns(pair.join(export.select(cols), on=["game_id", "player_id"], how="left"))
    out: list[dict[str, Any]] = []
    for col in SLICE_COLS:
        for val in sorted(j[col].drop_nulls().unique().to_list()):
            sub = j.filter(pl.col(col) == val)
            if sub.height < 50:
                continue
            ci = paired_score_delta_ci(
                sub["crps"].to_numpy(),
                sub["crps_b"].to_numpy(),
                n_boot=min(n_boot, 500),
                cluster_ids=sub["game_id"].to_numpy(),
            )
            out.append({"slice": f"{col}={val}", "n": sub.height, "delta": ci.point})
    return out


def keep_rule(
    oof: pl.DataFrame,
    export: pl.DataFrame,
    candidate: str,
    ref: str = "v1_prod",
    n_boot: int = N_BOOT,
    coverage: str = "naive",
) -> dict[str, Any]:
    """Per-stat verdict for ``candidate`` vs ``ref`` (see module docstring).

    ``coverage``: ``naive`` (y in [q10, q90]; experiment 1) or ``pit`` (Amendment A1.1,
    experiments 2+). Both numbers are always reported; only the named one is checked."""
    res: dict[str, dict[str, Any]] = {}
    pvals: list[float] = []
    for st in STATS:
        pr = _pair(oof, candidate, ref, st)
        if pr.is_empty():
            raise ValueError(f"no paired 2024 rows for {candidate} vs {ref} ({st})")
        d = pr["crps"].to_numpy() - pr["crps_b"].to_numpy()
        gid = pr["game_id"].to_numpy()
        ci = paired_score_delta_ci(
            pr["crps"].to_numpy(), pr["crps_b"].to_numpy(), n_boot=n_boot, cluster_ids=gid
        )
        y = pr["y"].to_numpy()
        bias = float((pr["mean"].to_numpy() - y).mean())
        q19 = _mat(pr["q19"])
        cov = float(((y >= q19[:, 1]) & (y <= q19[:, 17])).mean())
        cov_pit = pit_coverage80(q19, y)
        sl = _slices(pr, export, n_boot)
        res[st] = {
            "n": pr.height,
            "crps_delta": [ci.point, ci.lo, ci.hi],
            "bias": bias,
            "cov80": cov,
            "cov80_pit": cov_pit,
            "coverage_check": coverage,
            "slices": sl,
            "slice_regressions": [
                s["slice"]
                for s in sl
                if s["n"] >= SLICE_MIN_N and s["delta"] > SLICE_MAX_REGRESSION
            ],
        }
        pvals.append(boot_p_value(d, gid, n_boot))
    adj = bh_adjust(np.array(pvals))
    for st, q in zip(STATS, adj, strict=True):
        r = res[st]
        checks = {
            "floor": r["crps_delta"][0] <= -CRPS_FLOOR,
            "ci_excludes_0": r["crps_delta"][2] < 0,
            "bh_q": bool(q <= 0.05),
            "no_slice_regression": not r["slice_regressions"],
            "bias_within_0.5": abs(r["bias"]) <= BIAS_MAX,
            "cov80_in_0.75_0.85": COV_LO
            <= (r["cov80_pit"] if coverage == "pit" else r["cov80"])
            <= COV_HI,
        }
        r.update(bh_adj_p=float(q), checks=checks, keep=all(checks.values()))
    return res


def calibrator_rule(
    oof: pl.DataFrame,
    candidate: str,
    kind: str,
    thresholds: dict[str, list[int]],
    n_boot: int = N_BOOT,
) -> dict[str, Any]:
    """Keep rule for one calibrator ``kind`` (platt|iso|conf|pit) on the candidate arm."""
    out: dict[str, Any] = {}
    for st in STATS:
        pr = _pair(oof, f"{candidate}|{kind}", candidate, st)
        if pr.is_empty() or "p_ge" not in pr.columns:
            out[st] = {"keep": False, "reason": "no paired rows"}
            continue
        ci = paired_score_delta_ci(
            pr["tll"].to_numpy(), pr["tll_b"].to_numpy(), n_boot=n_boot,
            cluster_ids=pr["game_id"].to_numpy(),
        )  # fmt: skip
        thr = np.array(thresholds[st])
        ev = pr["y"].to_numpy()[:, None] >= thr[None, :]
        e_new = ece_equal_mass(_mat(pr["p_ge"]), ev)
        e_old = ece_equal_mass(_mat(pr["p_ge_b"]), ev)
        better_ll = ci.hi < 0
        better_ece = (e_old - e_new) >= ECE_GAIN and ci.hi <= TLL_NO_WORSE
        out[st] = {
            "tll_delta": [ci.point, ci.lo, ci.hi],
            "ece": [e_old, e_new],
            "keep": bool(better_ll or better_ece),
        }
    return out


def merge_oof(frames: list[pl.DataFrame]) -> pl.DataFrame:
    """Merge OOF frames by ``variant``; a variant present in several runs is taken from the LAST
    run (the completion run supersedes the first run's lighter copy)."""
    by_variant: dict[str, pl.DataFrame] = {}
    for f in frames:
        for v in f["variant"].unique().to_list():
            by_variant[v] = f.filter(pl.col("variant") == v)
    return pl.concat(list(by_variant.values()), how="diagonal_relaxed")


def experiment_number(metrics: dict[str, Any]) -> int:
    """2 for experiment-2+ runs (``experiment_tag`` like ``..._exp2_...`` or ``experiment`` = 2)."""
    tag = str(metrics.get("experiment_tag") or "")
    if metrics.get("experiment") == 2 or "exp2" in tag:
        return 2
    return 1


def run(
    run_dirs: str | list[str],
    export_path: str,
    out_path: str | None = None,
    coverage: str = "auto",
) -> dict[str, Any]:
    """Apply the rule to one run dir, or to several (first run + completion run) merged by variant.

    The candidate / top-3 / thresholds come from the FIRST dir's ``best_config.json`` (selection
    was done there, on 2023); the leak audit may come from any dir."""
    dirs = [Path(x) for x in ([run_dirs] if isinstance(run_dirs, str) else run_dirs)]
    metrics = [json.loads((d / "metrics.json").read_text()) for d in dirs]
    cfg = json.loads((dirs[0] / "best_config.json").read_text())
    if not any(m.get("leak_audit") for m in metrics):
        raise ValueError("no run has a leak_audit: run is invalid under the pre-registration")
    oof = merge_oof([pl.read_parquet(d / "oof_2024.parquet") for d in dirs])
    export = pl.read_parquet(export_path)
    if int(export["season"].max()) > 2024:  # type: ignore[arg-type]
        raise ValueError("export contains season > 2024")
    cand = cfg["best_arm_2023"]
    if coverage == "auto":
        coverage = "pit" if experiment_number(metrics[0]) >= 2 else "naive"
    verdict = keep_rule(oof, export, cand, coverage=coverage)
    cal = {
        k: calibrator_rule(oof, cand, k, cfg["thresholds"]) for k in ("platt", "iso", "conf", "pit")
    }
    result = {
        "candidate": cand,
        "verdict_per_stat": verdict,
        "v2_kept_overall": all(v["keep"] for v in verdict.values()),
        "calibrators": cal,
        "top3_2023": cfg["top3_2023"],
        "budget": cfg["budget"],
        "runs": [str(d) for d in dirs],
        "coverage_check": coverage,
    }
    if out_path:
        Path(out_path).write_text(json.dumps(result, indent=2, default=float))
    return result


def build_completion_plan(run_dir: str, arms: list[str]) -> dict[str, Any]:
    """Plan file for the notebook's COMPLETION mode, built from a finished first run."""
    d = Path(run_dir)
    cfg = json.loads((d / "best_config.json").read_text())
    m = json.loads((d / "metrics.json").read_text())
    return {
        "mode": "complete",
        "arms": arms,
        "prev_run": d.name,
        "best_config": cfg,
        "selection_scores_2023": m.get("selection_scores_2023"),
        "leak_audit": m.get("leak_audit"),
    }


DEFAULT_COMPLETE_ARMS = "xgb_v12_quantile,blend_top3,v1_prod"


def format_report(r: dict[str, Any]) -> str:
    lines = [f"candidate (2023-selected): {r['candidate']}   top3: {r['top3_2023']}"]
    lines.append(f"coverage check: {r.get('coverage_check', 'naive')}")
    lines.append("stat | n | dCRPS [95% CI] | bias | cov80 naive/PIT | BH q | keep | failed checks")
    for st, v in r["verdict_per_stat"].items():
        c = v["crps_delta"]
        failed = [k for k, ok in v["checks"].items() if not ok]
        lines.append(
            f"{st} | {v['n']} | {c[0]:+.4f} [{c[1]:+.4f},{c[2]:+.4f}] | {v['bias']:+.3f} | "
            f"{v['cov80']:.3f}/{v.get('cov80_pit', float('nan')):.3f} | "
            f"{v['bh_adj_p']:.3g} | {v['keep']} | {failed}"
        )
    lines.append(f"v2 kept overall: {r['v2_kept_overall']}")
    for k, per in r["calibrators"].items():
        lines.append(
            f"calibrator {k}: " + ", ".join(f"{s}={v.get('keep')}" for s, v in per.items())
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import os

    ap = argparse.ArgumentParser(description="Apply the pre-registered ctxres_v2 keep rule")
    ap.add_argument("--run", nargs="+", help="pulled run dir(s): first run, then completion run")
    ap.add_argument("--export", default="data/colab/ctxres_v2/ctxres_v2.parquet")
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--coverage",
        choices=["auto", "naive", "pit"],
        default="auto",
        help="coverage check of the keep rule; auto = pit (Amendment A1.1) for experiment 2+ runs",
    )
    ap.add_argument(
        "--stage-completion",
        nargs="?",
        const="",
        default=None,
        metavar="FIRST_RUN_DIR",
        help="write data/colab/ctxres_v2/completion_prev.json from a finished run (or env "
        "CTXRES_COMPLETE_FROM); with no source writes an empty plan if none exists",
    )
    ap.add_argument("--arms", default=os.environ.get("NBA_COMPLETE_ARMS", DEFAULT_COMPLETE_ARMS))
    ap.add_argument("--plan-dir", default="data/colab/ctxres_v2")
    ap.add_argument(
        "--clear", action="store_true", help="reset the staged plan to {} (normal sweep)"
    )
    a = ap.parse_args(argv)
    if a.stage_completion is not None or a.clear:
        plan_path = Path(a.plan_dir) / "completion_prev.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        src = a.stage_completion or os.environ.get("CTXRES_COMPLETE_FROM", "")
        if a.clear:
            plan_path.write_text("{}")
        elif src:
            plan = build_completion_plan(src, [x for x in a.arms.split(",") if x])
            plan_path.write_text(json.dumps(plan))
            print(f"staged completion plan from {src}: arms={plan['arms']}")
        elif not plan_path.exists():
            plan_path.write_text("{}")
        return 0
    if not a.run:
        ap.error("--run is required")
    print(format_report(run(a.run, a.export, a.out, a.coverage)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
