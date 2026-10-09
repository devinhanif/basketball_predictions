"""Apply the PRE-REGISTERED ctxres_v3 (experiment 3) rule to the pulled Colab artifacts, ONCE.

PRE-REGISTERED DECISION RULE (full text: docs/CTXRES_V3.md, written before any season-2025 row was
scored; not changed after seeing results; a negative result is reported as such)
------------------------------------------------------------------------------------------
* Candidate ``hybrid_v3`` = per stat: pts <- ``xgb_v12_quantile``; reb, ast, fg3m <-
  ``xgb_v12_poisson_nb`` (frozen experiment-2 configs, no calibrator). Reference ``v1_prod``
  (production recipe, v1 features) refit identically in the same walk-forward over season 2025.
* The rule is exactly the experiment-2 keep rule (``ctxres_v2_eval.keep_rule``, PIT coverage per
  Amendment A1.1): per stat KEEP iff CRPS delta <= -0.005, game-clustered 95% CI upper bound < 0,
  BH q <= 0.05 across the 4 stats (m = 4), no slice (n >= 300) regressing by more than +0.01, |bias|
  <= 0.5, A1.1 PIT coverage of the 80% interval in 0.75-0.85; and the run's leak audit passed.
  The hybrid is kept overall only if all 4 stats are kept.
* Calibrator (decided before 2025): Platt on pts threshold probabilities only; its exp-2 calibrator
  rule (thr-LL CI < 0, or ECE gain >= 0.005 without worse LL) is reported as
  `calibrator_pts` and does not enter the CRPS keep rule.
* Everything else (vs recency baseline, alternative compositions, threshold log loss/ECE,
  seed replicates, per-block deltas) is DESCRIPTIVE and never decides.
* One application per run id: a second call on the same run refuses unless ``--force`` is given, and
  every ``--force`` use is appended to the ledger.

The 2024 re-statement of the hybrid from the existing experiment-2 OOF (``--restate-2024``) is
descriptive, touches no 2025 data, and does not use the ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.eval.ctxres_v2_eval import COV_HI, COV_LO, STATS, calibrator_rule, keep_rule, load_oof
from nba.props.metrics import paired_score_delta_ci

HYBRID: dict[str, str] = {
    "pts": "xgb_v12_quantile",
    "reb": "xgb_v12_poisson_nb",
    "ast": "xgb_v12_poisson_nb",
    "fg3m": "xgb_v12_poisson_nb",
}
EXPERIMENT_TAG = "ctxres_v3_hybrid"
CANDIDATE = "hybrid_v3"
REFERENCE = "v1_prod"
#: sha256 of the canonical JSON of the frozen config embedded in the job (job.FROZEN); pinned
#: here so a run made from any other config is rejected by the evaluator
FROZEN_CONFIG_SHA256 = "a1c889e34067c9f4c3a72e92d7e4e59e784662a88623cb2ecf44f4a47c829b1e"
#: sha256 of the verbatim experiment-2 engine copy the job runs
ENGINE_SHA256 = "80f67da027347ecf5c57fbbb2b764a7a360945a7c0aba91dddedbf5ad98790f3"
TEST_SEASON = 2025
CALIBRATOR = "platt_pts_only"
THRESHOLDS = {
    "pts": [10, 15, 20, 25, 30],
    "reb": [4, 6, 8, 10],
    "ast": [2, 4, 6, 8],
    "fg3m": [1, 2, 3, 4],
}
DEFAULT_LEDGER = "data/colab/ctxres_v3/eval_ledger.jsonl"
N_BOOT = 2000


class GuardError(RuntimeError):
    """A pre-registration precondition failed (invalid run, wrong config, repeated application)."""


# ----------------------------------------------------------------------------- ledger


def read_ledger(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def append_ledger(path: str | Path, entry: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")


def claim_run(run_id: str, ledger: str | Path, force: bool, argv: list[str]) -> None:
    """Refuse a second application of the rule to ``run_id``; log every ``--force`` use."""
    prior = [e for e in read_ledger(ledger) if e.get("run_id") == run_id]
    if prior and not force:
        raise GuardError(
            f"run {run_id} was already scored ({len(prior)} ledger entries in {ledger}); "
            "the rule is applied once. Use --force only with a written reason."
        )
    append_ledger(
        ledger,
        {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "run_id": run_id,
            "event": "force" if prior else "applied",
            "argv": argv,
        },
    )


# ----------------------------------------------------------------------------- run validity


def validate_run(metrics: dict[str, Any], prereg_id: str | None = None) -> None:
    """Preconditions of the pre-registration; raises ``GuardError`` otherwise."""
    if metrics.get("experiment_tag") != EXPERIMENT_TAG:
        raise GuardError(f"experiment_tag {metrics.get('experiment_tag')!r} != {EXPERIMENT_TAG!r}")
    if not metrics.get("complete"):
        raise GuardError("metrics.json is not complete (the run did not finish)")
    if metrics.get("budget") != "full":
        raise GuardError(f"budget {metrics.get('budget')!r} is not 'full'")
    if metrics.get("frozen_config_sha256") != FROZEN_CONFIG_SHA256:
        raise GuardError("run used a config other than the pre-registered frozen one")
    if metrics.get("engine_sha256") != ENGINE_SHA256:
        raise GuardError("run used an engine other than the pinned experiment-2 copy")
    if metrics.get("hybrid") != HYBRID:
        raise GuardError("hybrid composition in the run differs from the pre-registered one")
    if metrics.get("calibrator") != CALIBRATOR:
        raise GuardError(f"calibrator must be {CALIBRATOR!r} (pre-registered)")
    pid = str(metrics.get("preregistration_id") or "")
    if not pid or pid.upper().startswith("PENDING"):
        raise GuardError("run has no (or a placeholder) preregistration_id")
    if prereg_id is not None and pid != prereg_id:
        raise GuardError(f"run preregistration_id {pid!r} != --prereg-id {prereg_id!r}")
    if not metrics.get("leak_audit"):
        raise GuardError("no leak_audit in the run: invalid under the pre-registration")
    if int(metrics.get("test_season", -1)) != TEST_SEASON:
        raise GuardError("test_season is not 2025")


# ----------------------------------------------------------------------------- assembly


def assemble_hybrid_oof(oof: pl.DataFrame, mapping: dict[str, str] | None = None) -> pl.DataFrame:
    """Hybrid rows picked per stat from the component variants' OOF rows."""
    mapping = mapping or HYBRID
    parts = []
    for st in STATS:
        part = oof.filter((pl.col("variant") == mapping[st]) & (pl.col("stat") == st))
        if part.is_empty():
            raise ValueError(f"component {mapping[st]} has no rows for {st}")
        parts.append(part.with_columns(pl.lit(CANDIDATE).alias("variant")))
    return pl.concat(parts, how="diagonal_relaxed")


def check_hybrid_consistency(oof: pl.DataFrame, mapping: dict[str, str] | None = None) -> None:
    """The stored ``hybrid_v3`` rows must equal the mapped component rows (guards mis-assembly)."""
    mapping = mapping or HYBRID
    for st in STATS:
        h = oof.filter((pl.col("variant") == CANDIDATE) & (pl.col("stat") == st)).sort(
            ["game_id", "player_id"]
        )
        c = oof.filter((pl.col("variant") == mapping[st]) & (pl.col("stat") == st)).sort(
            ["game_id", "player_id"]
        )
        if h.height != c.height or h.is_empty():
            raise GuardError(f"hybrid rows for {st} do not match component {mapping[st]}")
        same = np.array_equal(h["crps"].to_numpy(), c["crps"].to_numpy(), equal_nan=True) and (
            np.array_equal(h["mean"].to_numpy(), c["mean"].to_numpy(), equal_nan=True)
        )
        if not same:
            raise GuardError(f"hybrid {st} differs from component {mapping[st]}")


# ----------------------------------------------------------------------------- descriptive


def _delta(oof: pl.DataFrame, a: str, b: str, st: str, col: str = "crps") -> dict[str, Any]:
    x = oof.filter((pl.col("variant") == a) & (pl.col("stat") == st))
    y = oof.filter((pl.col("variant") == b) & (pl.col("stat") == st))
    j = x.join(y, on=["game_id", "player_id"], suffix="_b")
    if j.is_empty():
        return {"n": 0}
    ci = paired_score_delta_ci(
        j[col].to_numpy(), j[f"{col}_b"].to_numpy(),
        n_boot=N_BOOT, cluster_ids=j["game_id"].to_numpy(),
    )  # fmt: skip
    return {"n": j.height, "delta": [ci.point, ci.lo, ci.hi]}


def descriptive_tables(oof: pl.DataFrame) -> dict[str, Any]:
    """Vs recency baseline, alternative compositions and threshold log loss (no decisions)."""
    out: dict[str, Any] = {"vs_recency": {}, "alt_composition": {}, "threshold_logloss": {}}
    variants = set(oof["variant"].unique().to_list())
    for st in STATS:
        if "recency_normal" in variants:
            out["vs_recency"][st] = {
                "hybrid": _delta(oof, CANDIDATE, "recency_normal", st),
                "v1_prod": _delta(oof, REFERENCE, "recency_normal", st),
            }
        other = "xgb_v12_poisson_nb" if HYBRID[st] == "xgb_v12_quantile" else "xgb_v12_quantile"
        if other in variants:
            out["alt_composition"][st] = {"alt": other, **_delta(oof, other, CANDIDATE, st)}
        out["threshold_logloss"][st] = _delta(oof, CANDIDATE, REFERENCE, st, "tll")
    return out


# ----------------------------------------------------------------------------- run


def run(
    run_dir: str | Path,
    export_path: str | Path,
    out_path: str | Path | None = None,
    ledger: str | Path = DEFAULT_LEDGER,
    force: bool = False,
    prereg_id: str | None = None,
    n_boot: int = N_BOOT,
    argv: list[str] | None = None,
) -> dict[str, Any]:
    d = Path(run_dir)
    metrics = json.loads((d / "metrics.json").read_text())
    validate_run(metrics, prereg_id)
    oof = load_oof(d)
    check_hybrid_consistency(oof)
    export = pl.read_parquet(export_path)
    if TEST_SEASON not in set(export["season"].unique().to_list()):
        raise GuardError("export has no season-2025 rows (use ctxres_v3_with2025.parquet)")
    ids = oof.select("game_id", "player_id").unique()
    exp25 = export.filter(pl.col("season") == TEST_SEASON).select("game_id", "player_id")
    if ids.join(exp25, on=["game_id", "player_id"], how="anti").height:
        raise GuardError("OOF contains rows that are not season-2025 export rows")
    claim_run(
        d.name, ledger, force, argv or sys.argv[1:]
    )  # LAST guard: after this the rule is applied
    verdict = keep_rule(oof, export, CANDIDATE, REFERENCE, n_boot=n_boot, coverage="pit")
    result = {
        "experiment": EXPERIMENT_TAG,
        "run": str(d),
        "run_id": d.name,
        "preregistration_id": metrics["preregistration_id"],
        "candidate": CANDIDATE,
        "reference": REFERENCE,
        "hybrid": HYBRID,
        "verdict_per_stat": verdict,
        "hybrid_kept_overall": all(v["keep"] for v in verdict.values()),
        "leak_audit_present": True,
        "coverage_check": "pit",
        "n_test_rows": metrics.get("n_test_rows"),
        "forced": force,
        "calibrator_pts": calibrator_rule(oof, CANDIDATE, "platt_pts", THRESHOLDS, n_boot=n_boot)[
            "pts"
        ],
        "descriptive": descriptive_tables(oof),
        "seed_replicates": metrics.get("seed_replicates"),
    }
    if out_path:
        Path(out_path).write_text(json.dumps(result, indent=2, default=float))
    return result


# ----------------------------------------------------------------------------- 2024 restatement


def restate_2024(
    exp2_run: str | Path,
    export_v2_path: str | Path,
    n_boot: int = N_BOOT,
) -> dict[str, Any]:
    """DESCRIPTIVE: assemble the hybrid from the experiment-2 2024 OOF and apply the same rule.

    The exp-2 quantile arm stored a light OOF without its 19-quantile grid, so for pts the
    coverage entry is the naive y-in-[q10,q90] value from that run's ``metrics.json`` (PIT coverage
    not computable) and its check is provisional. No season-2025 data is involved. NOT a
    confirmation: the per-stat composition was chosen after viewing these very 2024 results."""
    d = Path(exp2_run)
    oof = load_oof(d)
    m = json.loads((d / "metrics.json").read_text())
    hyb = assemble_hybrid_oof(oof)
    missing = [st for st in STATS if hyb.filter(pl.col("stat") == st)["q19"].is_null().any()]
    zeros = [0.0] * 19
    filled = hyb.with_columns(
        pl.Series("q19", [zeros if x is None else x for x in hyb["q19"].to_list()])
    )
    ref = oof.filter(pl.col("variant") == REFERENCE)
    export = pl.read_parquet(export_v2_path)
    v = keep_rule(pl.concat([filled, ref], how="diagonal_relaxed"), export, CANDIDATE, REFERENCE,
                  n_boot=n_boot, coverage="pit")  # fmt: skip
    for st in missing:
        naive = float(m["leaderboard_2024"][HYBRID[st]][st]["cov80"])
        r = v[st]
        r["cov80"], r["cov80_pit"] = naive, float("nan")
        r["coverage_source"] = "metrics.json naive (PIT not computable: light OOF); provisional"
        r["checks"]["cov80_in_0.75_0.85"] = COV_LO <= naive <= COV_HI
        r["keep"] = all(r["checks"].values())
    return {
        "source_run": str(d),
        "hybrid": HYBRID,
        "verdict_per_stat": v,
        "kept_overall": all(x["keep"] for x in v.values()),
        "provisional_coverage_stats": missing,
        "note": "descriptive; composition chosen after viewing these 2024 results",
    }


# ----------------------------------------------------------------------------- report


def _fmt_verdict(v: dict[str, Any]) -> list[str]:
    lines = ["stat | n | dCRPS [95% CI] | bias | cov80 naive/PIT | BH q | keep | failed checks"]
    for st, r in v.items():
        c = r["crps_delta"]
        failed = [k for k, ok in r["checks"].items() if not ok]
        lines.append(
            f"{st} | {r['n']} | {c[0]:+.4f} [{c[1]:+.4f},{c[2]:+.4f}] | {r['bias']:+.3f} | "
            f"{r['cov80']:.3f}/{r.get('cov80_pit', float('nan')):.3f} | {r['bh_adj_p']:.3g} | "
            f"{r['keep']} | {failed}"
        )
    return lines


def format_report(r: dict[str, Any]) -> str:
    if "reference" in r:
        lines = [f"ctxres_v3 hybrid vs {r['reference']} (season 2025)  run {r.get('run_id', '?')}"]
    else:
        lines = [f"DESCRIPTIVE 2024 re-statement of the hybrid vs {REFERENCE} ({r['source_run']})"]
    lines += _fmt_verdict(r["verdict_per_stat"])
    key = "hybrid_kept_overall" if "hybrid_kept_overall" in r else "kept_overall"
    lines.append(f"hybrid kept overall: {r[key]}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Apply the pre-registered ctxres_v3 rule (ONCE per run)"
    )
    ap.add_argument("--run", help="pulled ctxres_v3_hybrid run dir")
    ap.add_argument("--export", default="data/colab/ctxres_v3/ctxres_v3_with2025.parquet")
    ap.add_argument("--out", default=None)
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--prereg-id", default=None, help="must equal the run's preregistration_id")
    ap.add_argument(
        "--force", action="store_true", help="re-apply to an already scored run (logged)"
    )
    ap.add_argument(
        "--restate-2024", action="store_true", help="descriptive 2024 hybrid from exp-2 OOF"
    )
    ap.add_argument("--exp2-run", default="data/colab/runs/ctxres_v2_sweep/20261008_171446")
    ap.add_argument("--export-v2", default="data/colab/ctxres_v2/ctxres_v2.parquet")
    a = ap.parse_args(argv)
    if a.restate_2024:
        res = restate_2024(a.exp2_run, a.export_v2)
        if a.out:
            Path(a.out).write_text(json.dumps(res, indent=2, default=float))
        print(format_report(res))
        return 0
    if not a.run:
        ap.error("--run is required")
    res = run(
        a.run, a.export, a.out, a.ledger, a.force, a.prereg_id, argv=list(argv or sys.argv[1:])
    )
    print(format_report(res))
    return 0


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
