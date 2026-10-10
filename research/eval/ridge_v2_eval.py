"""Apply the PRE-REGISTERED ridge_v2 (experiment 4) keep rule to pulled Colab artifacts.

PRE-REGISTERED DECISION RULE (written 2026-10-08 BEFORE any ridge_v2 run; full text in
docs/RIDGE_V2.md; not changed after seeing results; a negative result is reported as such)
------------------------------------------------------------------------------------------
* Arms differ only in the ridge columns (v2 group i) fed to the FIXED experiment-2 per-stat models
  (pts: multi-quantile XGBoost; reb/ast/fg3m: Poisson + NegBin XGBoost).
* Candidate, per stat = the NEW arm (any arm except the references ``exp2_ref``, ``ref_fixed``,
  ``no_ridge``) with the lowest SEASON-2023 CRPS for that stat. Chosen from ``metrics.json``
  ``selection_2023`` only; season 2024 never influences the choice.
* Reference = ``ref_fixed``: the experiment-2 ridge estimator predicted for EVERY row. The
  experiment-2 export was NULL for players with < 5 minutes tonight, a leak (docs/RIDGE_V2.md);
  ``exp2_ref`` keeps that leaky column for comparison and is reported, not used for the decision.
* KEEP a stat iff ALL hold on season-2024 rows (candidate - ref_fixed):
    1. mean CRPS delta <= -0.005 (floor);
    2. game-clustered 95% bootstrap CI of the delta excludes 0 (upper bound < 0);
    3. the clustered two-sided p-value survives Benjamini-Hochberg across the 4 stats (m = 4),
       q = 0.05;
    4. no slice (teammate-out report status, minutes-change bucket, first-15 vs rest team games,
       starter vs bench; n >= 300) has a CRPS delta > +0.01;
    5. mean bias of the candidate within +/-0.5;
    6. 80% interval coverage within 0.75-0.85 by the Amendment A1.1 PIT construction (integer
       grids from Poisson/NegBin arms carry about +/-0.035 estimator uncertainty).
  and the run's leak audit is present and the run is complete.
* Passing on 2024 makes an arm a CONFIRMATORY CANDIDATE for one later 2025 touch (separate
  pre-registration); it is not a production change.
* Everything else (full leaderboard vs the reference, ladders, drop-one ablation, comparison with
  ``exp2_ref`` and ``no_ridge``) is DESCRIPTIVE.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.eval.context_residual_eval import (
    SLICE_MAX_REGRESSION,
    SLICE_MIN_N,
    bh_adjust,
    boot_p_value,
)
from nba.props.metrics import paired_score_delta_ci
from research.eval.ctxres_v2_eval import (
    BIAS_MAX,
    COV_HI,
    COV_LO,
    CRPS_FLOOR,
    N_BOOT,
    _mat,
    _pair,
    _slices,
    load_oof,
    merge_oof,
    pit_coverage80,
)

STATS = ("pts", "reb", "ast", "fg3m")
REFERENCE_ARMS = ("exp2_ref", "ref_fixed", "no_ridge")
PRIMARY_REF = "ref_fixed"
LADDER = ("ref_fixed", "L1", "L2", "L3", "L4", "ALL")
COMPONENT_ARMS = ("r1", "r6", "r5", "r5arch", "r2", "r2arch", "r3a", "r3b", "r4")
DROP_ARMS = ("drop_r1", "drop_r6", "drop_r5", "drop_r2", "drop_r3a", "drop_r3b", "drop_r4")


def merge_selection(metrics: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """arm -> stat -> season-2023 mean CRPS (the last run wins for an arm present twice)."""
    out: dict[str, dict[str, float]] = {}
    for m in metrics:
        for arm, per in m["selection_2023"].items():
            out[arm] = {
                st: float(per[st]["crps"]) for st in STATS if per[st].get("crps") is not None
            }
    return out


def choose_candidates(sel: dict[str, dict[str, float]]) -> dict[str, str]:
    """Per stat, the non-reference arm with the lowest 2023 CRPS (ties -> arm name order)."""
    cand: dict[str, str] = {}
    for st in STATS:
        pool = sorted(a for a in sel if a not in REFERENCE_ARMS and st in sel[a])
        if not pool:
            raise ValueError(f"no non-reference arm with a 2023 score for {st}")
        cand[st] = min(pool, key=lambda a: (sel[a][st], a))
    return cand


def _delta(oof: pl.DataFrame, a: str, b: str, stat: str, n_boot: int) -> dict[str, Any] | None:
    pr = _pair(oof, a, b, stat)
    if pr.is_empty():
        return None
    ci = paired_score_delta_ci(
        pr["crps"].to_numpy(),
        pr["crps_b"].to_numpy(),
        n_boot=n_boot,
        cluster_ids=pr["game_id"].to_numpy(),
    )
    d = pr["crps"].to_numpy() - pr["crps_b"].to_numpy()
    p = boot_p_value(d, pr["game_id"].to_numpy(), n_boot)
    return {
        "n": pr.height,
        "crps": float(pr["crps"].mean()),  # type: ignore[arg-type]
        "delta": [ci.point, ci.lo, ci.hi],
        "p": p,
    }


def keep_rule(
    oof: pl.DataFrame,
    export: pl.DataFrame,
    candidates: dict[str, str],
    ref: str = PRIMARY_REF,
    n_boot: int = N_BOOT,
) -> dict[str, dict[str, Any]]:
    """Per-stat verdict of ``candidates[stat]`` vs ``ref`` (BH over the 4 stats)."""
    res: dict[str, dict[str, Any]] = {}
    pvals: list[float] = []
    for st in STATS:
        cand = candidates[st]
        pr = _pair(oof, cand, ref, st)
        if pr.is_empty():
            raise ValueError(f"no paired 2024 rows for {cand} vs {ref} ({st})")
        gid = pr["game_id"].to_numpy()
        d = pr["crps"].to_numpy() - pr["crps_b"].to_numpy()
        ci = paired_score_delta_ci(
            pr["crps"].to_numpy(), pr["crps_b"].to_numpy(), n_boot=n_boot, cluster_ids=gid
        )
        y = pr["y"].to_numpy()
        q19 = _mat(pr["q19"])
        sl = _slices(pr, export, n_boot)
        res[st] = {
            "candidate": cand,
            "n": pr.height,
            "crps_delta": [ci.point, ci.lo, ci.hi],
            "bias": float((pr["mean"].to_numpy() - y).mean()),
            "cov80_pit": pit_coverage80(q19, y),
            "cov80_naive": float(((y >= q19[:, 1]) & (y <= q19[:, 17])).mean()),
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
        r["checks"] = {
            "floor": bool(r["crps_delta"][0] <= -CRPS_FLOOR),
            "ci_excludes_0": bool(r["crps_delta"][2] < 0),
            "bh_q": bool(q <= 0.05),
            "no_slice_regression": not r["slice_regressions"],
            "bias_within_0.5": bool(abs(r["bias"]) <= BIAS_MAX),
            "cov80_in_0.75_0.85": bool(COV_LO <= r["cov80_pit"] <= COV_HI),
        }
        r["bh_adj_p"] = float(q)
        r["keep"] = all(r["checks"].values())
    return res


def descriptive_tables(
    oof: pl.DataFrame, arms: list[str], n_boot: int, aliases: dict[str, str] | None = None
) -> dict[str, Any]:
    """Every arm vs the primary reference, plus the leak decomposition and the drop-one table."""
    present = set(oof["variant"].unique().to_list())
    out: dict[str, Any] = {"leaderboard": {}, "leak": {}, "ablation": {}}
    for st in STATS:
        rows: dict[str, Any] = {}
        for a in arms:
            if a in present and a != PRIMARY_REF:
                d = _delta(oof, a, PRIMARY_REF, st, n_boot)
                if d is not None:
                    rows[a] = d
        out["leaderboard"][st] = rows
        leak: dict[str, Any] = {}
        for name, (a, b) in {
            "exp2_ridge_gain (no_ridge - exp2_ref)": ("no_ridge", "exp2_ref"),
            "honest_ridge_gain (no_ridge - ref_fixed)": ("no_ridge", "ref_fixed"),
            "leak_effect (ref_fixed - exp2_ref)": ("ref_fixed", "exp2_ref"),
        }.items():
            if a in present and b in present:
                d2 = _delta(oof, a, b, st, n_boot)
                if d2 is not None:
                    leak[name] = d2
        out["leak"][st] = leak
        abl: dict[str, Any] = {}
        for a in DROP_ARMS:
            a_run = (aliases or {}).get(a, a)  # drop_r4 == L4 when r4 adds nothing identical
            if a_run in present and "ALL" in present:
                d3 = _delta(oof, a_run, "ALL", st, n_boot)  # >0: the component helps
                if d3 is not None:
                    abl[a] = d3
        out["ablation"][st] = abl
    return out


def run(
    run_dirs: str | list[str],
    export_path: str,
    out_path: str | None = None,
    n_boot: int = N_BOOT,
) -> dict[str, Any]:
    """Apply the rule to one or several (sharded) run dirs, merged by arm."""
    dirs = [Path(x) for x in ([run_dirs] if isinstance(run_dirs, str) else run_dirs)]
    metrics = [json.loads((d / "metrics.json").read_text()) for d in dirs]
    for d, m in zip(dirs, metrics, strict=True):
        if not m.get("complete"):
            raise ValueError(f"{d} is incomplete (metrics.json complete != true)")
        if not m.get("leak_audit"):
            raise ValueError(f"{d} has no leak_audit: invalid under the pre-registration")
        if m.get("experiment_tag") != "ridge_v2_exp4":
            raise ValueError(f"{d} is not a ridge_v2_exp4 run")
    oof = merge_oof([load_oof(d) for d in dirs])
    export = pl.read_parquet(export_path)
    if int(export["season"].max()) > 2024:  # type: ignore[arg-type]
        raise ValueError("export contains season > 2024")
    sel = merge_selection(metrics)
    for must in (PRIMARY_REF,):
        if must not in sel:
            raise ValueError(f"reference arm {must} missing from the pulled runs")
    aliases: dict[str, str] = {}
    for m in metrics:
        aliases.update(m.get("aliases") or {})
    cand = choose_candidates(sel)
    verdict = keep_rule(oof, export, cand, n_boot=n_boot)
    for st in STATS:
        verdict[st]["candidate_2023_crps"] = sel[cand[st]][st]
        verdict[st]["reference_2023_crps"] = sel[PRIMARY_REF][st]
        verdict[st]["candidate_beats_ref_on_2023"] = bool(sel[cand[st]][st] < sel[PRIMARY_REF][st])
    arms = sorted(sel)
    result = {
        "primary_reference": PRIMARY_REF,
        "candidates_2023": cand,
        "selection_2023": sel,
        "verdict_per_stat": verdict,
        "kept_stats": [st for st in STATS if verdict[st]["keep"]],
        "kept_overall": all(verdict[st]["keep"] for st in STATS),
        "aliases": aliases,
        "descriptive": descriptive_tables(oof, arms, min(n_boot, 500), aliases),
        "runs": [str(d) for d in dirs],
        "arms": arms,
    }
    if out_path:
        Path(out_path).write_text(json.dumps(result, indent=2, default=float))
    return result


def _fmt(ci: list[float]) -> str:
    return f"{ci[0]:+.4f} [{ci[1]:+.4f},{ci[2]:+.4f}]"


def format_report(r: dict[str, Any]) -> str:
    lines = ["# ridge_v2 (experiment 4) verdict", ""]
    lines += [
        f"primary reference `{r['primary_reference']}`; kept stats: {r['kept_stats']}; "
        f"kept overall: **{r['kept_overall']}**",
        "",
        "| stat | candidate (2023) | n | dCRPS vs ref [CI] | bias | PIT cov80 | BH q | keep "
        "| failed |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for st, v in r["verdict_per_stat"].items():
        failed = [k for k, ok in v["checks"].items() if not ok]
        lines.append(
            f"| {st} | {v['candidate']} | {v['n']} | {_fmt(v['crps_delta'])} | {v['bias']:+.3f} | "
            f"{v['cov80_pit']:.3f} | {v['bh_adj_p']:.4f} | {v['keep']} | {failed} |"
        )
    lines += ["", "## Leak decomposition (DESCRIPTIVE; positive = first arm worse)", ""]
    for st, d in r["descriptive"]["leak"].items():
        for name, v in d.items():
            lines.append(f"* {st} {name}: {_fmt(v['delta'])}")
    lines += ["", "## Leaderboard vs ref_fixed, 2024 (DESCRIPTIVE)", ""]
    for st, rows in r["descriptive"]["leaderboard"].items():
        lines += [f"### {st}", "", "| arm | CRPS | dCRPS [CI] |", "|---|---|---|"]
        for a, v in sorted(rows.items(), key=lambda kv: kv[1]["delta"][0]):
            lines.append(f"| {a} | {v['crps']:.4f} | {_fmt(v['delta'])} |")
        lines.append("")
    lines += ["## Drop-one ablation from ALL (DESCRIPTIVE; positive = component helps)", ""]
    for st, rows in r["descriptive"]["ablation"].items():
        for a, v in rows.items():
            lines.append(f"* {st} {a} - ALL: {_fmt(v['delta'])}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Apply the ridge_v2 pre-registered rule")
    ap.add_argument("--run", nargs="+", required=True, help="pulled run dir(s); merged by arm")
    ap.add_argument("--export", default="data/colab/ctxres_v2/ctxres_v2.parquet")
    ap.add_argument("--out", default="reports/ridge_v2_verdict.json")
    ap.add_argument("--md", default=None, help="also write a markdown report")
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    a = ap.parse_args(argv)
    r = run(a.run, a.export, a.out, a.n_boot)
    text = format_report(r)
    if a.md:
        Path(a.md).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
