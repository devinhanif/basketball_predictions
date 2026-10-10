# mypy: ignore-errors
# ruff: noqa: E501, F821, E402, F811, I001
"""ctxres_v3 hybrid: ONE confirmatory walk-forward over season 2025 with FROZEN configs.

PRE-REGISTRATION: docs/CTXRES_V3.md (written before any 2025 row was scored). The rule is applied
once by research/eval/ctxres_v3_eval.py. NOTHING is searched or selected here.

  hybrid_v3 = per stat   pts  <- xgb_v12_quantile   (19-quantile XGBoost, direct distribution)
                         reb, ast, fg3m <- xgb_v12_poisson_nb (count:poisson mean + NegBin dispersion)
  comparator  v1_prod         production LightGBM residual + |resid| + split-conformal, v1 features,
                              refit identically inside the same walk-forward
  descriptive recency_normal  as-of recency-Normal baseline
  calibrator  Platt (exp-2 tuned W=4, hl=4, C=10) on the hybrid's pts threshold probabilities ONLY,
              nested walk-forward inside the 2025 run (docs/CTXRES_V3.md); no calibrator elsewhere

The model engine (arms, features, scoring) is the frozen, verbatim copy ``ctxres_v2_engine.py`` of
the experiment-2 sweep (sha256 pinned below); only the season roles are re-pointed: the walk-forward
test season is 2025 (``SELECT_SEASON = REPORT_SEASON = 2025``), every month block is fit on ALL
played rows strictly before it (2022-2024 and earlier 2025 months). The input is the SEPARATE
``ctxres_v3_with2025.parquet`` (``--include-holdout-2025`` export).

Guard: the run REFUSES to start unless env NBA_PREREGISTERED_ID equals the ``preregistration_id``
staged in ``job_meta.json`` (from job.yaml, set by the maintainer after the HOLDOUT_ACCESS_LOG row is
written) and that id is not a PENDING placeholder.

Crash safety (exp-2 pattern): every finished arm is checkpointed (``ArmStore``); small artifacts
(``frozen_config.json``, ``run_header.json``, leak audit) are written first; ``metrics.json`` has
``complete: false`` until the end and the ``oof/`` folder appears only when everything is written.
Budget: ``full`` only (``smoke`` is the CPU test budget). GPU XGBoost is not bit-reproducible;
seeds are logged and ``NBA_V3_REPLICATES`` (default 1) reruns the two component arms with seed+r as
a DESCRIPTIVE seed-sensitivity check that never enters the keep rule.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

#: Name the engine is staged under in the notebook / run dir. Until 2026-10-10 a byte-identical copy
#: of ``ctxres_v2_sweep/ctxres_v2_sweep.py`` sat next to this file under that name; the copy was the
#: restructure's one DELETE (DESIGN_RESTRUCTURE Appendix A) and the sweep script is read directly.
ENGINE_FILE = "ctxres_v2_engine.py"
ENGINE_SRC = Path(__file__).resolve().parent.parent / "ctxres_v2_sweep" / "ctxres_v2_sweep.py"
#: sha256 of research/colab/jobs/ctxres_v2_sweep/ctxres_v2_sweep.py (the verbatim engine for v3)
ENGINE_SHA256 = "80f67da027347ecf5c57fbbb2b764a7a360945a7c0aba91dddedbf5ad98790f3"

if (
    "ArmStore" not in globals()
):  # standalone import (tests / local); the notebook runs the engine cell first
    _ep = ENGINE_SRC
    _src = _ep.read_text().replace('if __name__ == "__main__":\n    entry()\n', "")
    exec(compile(_src, str(_ep), "exec"), globals())  # noqa: S102

EXPERIMENT_TAG_V3 = "ctxres_v3_hybrid"
CODE_VERSION_V3 = "v3-ckpt-1"
TEST_SEASON_V3 = 2025
HYBRID = {
    "pts": "xgb_v12_quantile",
    "reb": "xgb_v12_poisson_nb",
    "ast": "xgb_v12_poisson_nb",
    "fg3m": "xgb_v12_poisson_nb",
}
#: frozen from data/colab/runs/ctxres_v2_sweep/20261008_171446 (exp-2 best_config.json: search_v12 params,
#: budget_params rounds/prod_rounds/min_train/min_cal, seed). No search is run in v3.
FROZEN = {
    "source_run": "ctxres_v2_sweep/20261008_171446",
    "source_best_config_sha256": "4ae729a111618993ba700a9d79ce8879c515df51aca2d71229eef4174a58dac8",
    "hybrid": HYBRID,
    "p12": {
        "learning_rate": 0.0558198665255319,
        "max_depth": 3,
        "subsample": 0.7168179865008671,
        "colsample_bytree": 0.7144428763045745,
        "min_child_weight": 116.4036974504763,
        "reg_alpha": 0.17402858001416036,
        "reg_lambda": 4.138732643418021,
    },
    "rounds": 600,
    "prod_rounds": 200,
    "min_train": 1500,
    "min_cal": 2000,
    "seed": 20261008,
    "calibrator": "platt_pts_only",
    "calibrator_params": {"W": 4, "hl": 4.0, "C": 10.0},
    "comparator": "v1_prod",
    "test_season": 2025,
}
PENDING_PREFIX = "PENDING"


def frozen_sha256(frozen=None):
    return hashlib.sha256(
        json.dumps(frozen or FROZEN, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


# ------------------------------------------------------------------------------ guard + assembly


class PreregistrationError(RuntimeError):
    """The run was started without a matching, non-placeholder pre-registration id."""


def check_prereg(env, meta_path):
    """Return the validated pre-registration id, or raise ``PreregistrationError``.

    ``meta_path`` is the ``job_meta.json`` staged next to the parquet (carries the job.yaml id).
    The env id must be set by hand and equal it; PENDING placeholders are always refused."""
    want = str(env.get("NBA_PREREGISTERED_ID") or "").strip()
    if not want:
        raise PreregistrationError(
            "env NBA_PREREGISTERED_ID is not set; refusing to touch season 2025"
        )
    mp = Path(meta_path)
    if not mp.exists():
        raise PreregistrationError(f"{mp} not found: run through `python -m research.colab push`")
    meta = json.loads(mp.read_text())
    got = str(meta.get("preregistration_id") or "").strip()
    if not got or got.upper().startswith(PENDING_PREFIX):
        raise PreregistrationError(
            f"job.yaml preregistration_id is a placeholder ({got!r}); log the access row first"
        )
    if want != got:
        raise PreregistrationError(
            f"NBA_PREREGISTERED_ID {want!r} != staged preregistration_id {got!r}"
        )
    if not meta.get("touches_holdout"):
        raise PreregistrationError("staged job does not declare touches_holdout")
    if str(env.get("NBA_SCORE_HOLDOUT", "")) != "True":
        raise PreregistrationError(
            "NBA_SCORE_HOLDOUT is not True (stage the job with `python -m research.colab push`)"
        )
    return got


def assemble_hybrid(arms, mapping=None):
    """Per-stat pick of the frozen component arms. ``arms``: name -> {stat: {q, mean, p}}."""
    mapping = mapping or HYBRID
    return {
        st: {k: np.array(arms[mapping[st]][st][k], copy=True) for k in ("q", "mean", "p")}
        for st in STATS
    }


def platt_pts_only(hybrid):
    """Frozen calibrator choice: Platt on pts threshold probabilities, raw copies elsewhere."""
    cal = make_variant(hybrid, "platt", FROZEN["calibrator_params"])
    out = {st: {k: np.array(hybrid[st][k], copy=True) for k in ("q", "mean", "p")} for st in STATS}
    out["pts"]["p"] = cal["pts"]["p"]
    return out


def recency_res():
    """Recency-Normal baseline on the test rows, in the engine's result layout."""
    res = new_res()
    sl = slice(C.t0i, None)
    for st in STATS:
        m, s = C.m[st][sl], C.s[st][sl]
        s = np.where(np.isfinite(s) & (s > 0), s, C.dstd[st])
        q = clean_q(normal_q(m, s))
        res[st]["q"][:] = q
        res[st]["mean"][:] = m
        res[st]["p"][:] = p_from_grid(q, THR[st])
    return res


# ------------------------------------------------------------------------------ setup / load


def setup_v3():
    """Lean version of the engine ``setup``: no Optuna, no CatBoost, no MLP; prints the GPU."""
    global EXPERIMENT_TAG, CODE_VERSION, SELECT_SEASON, REPORT_SEASON, FROZEN_SEASON
    C.t0 = time.monotonic()
    C.budget_name = os.environ.get("NBA_BUDGET", "full")
    if C.budget_name not in ("full", "smoke"):
        raise ValueError("ctxres_v3_hybrid runs the FULL budget only (smoke = CPU test)")
    C.smoke = C.budget_name == "smoke"
    C.store = None
    C.strict = True  # every arm must finish; nothing is skipped or dropped
    C.B = dict(BUDGETS[C.budget_name])
    if not C.smoke:  # the frozen exp-2 values must be the ones in force
        for k in ("min_train", "min_cal", "rounds", "prod_rounds"):
            assert C.B[k] == FROZEN[k], (k, C.B[k], FROZEN[k])
    pip_install("xgboost")
    import torch

    C.torch = torch
    C.device = "cuda" if torch.cuda.is_available() else "cpu"
    if C.device == "cpu":
        torch.set_num_threads(1)
    C.has_cat = False
    C.rng = np.random.default_rng(SEED)
    C.mode = "v3"
    EXPERIMENT_TAG, CODE_VERSION = EXPERIMENT_TAG_V3, CODE_VERSION_V3
    SELECT_SEASON = REPORT_SEASON = TEST_SEASON_V3  # the single walk-forward test season
    FROZEN_SEASON = (
        TEST_SEASON_V3 + 1
    )  # the engine loader's refusal threshold (2025 is allowed here)
    gpu = "none"
    if C.device == "cuda":
        gpu = torch.cuda.get_device_name(0)
    print(f"BUDGET={C.budget_name} MODE=v3 device={C.device} GPU={gpu}")
    try:
        smi = subprocess.run(
            ["nvidia-smi", "-L"], capture_output=True, text=True, timeout=20
        ).stdout.strip()
        print(smi or "nvidia-smi: no output")
    except (OSError, subprocess.SubprocessError):
        print("nvidia-smi unavailable")
    C.gpu_name = gpu
    print(
        "expected runtime on a T4: v1_prod ~2-3 min (CPU), xgb_v12_poisson_nb ~3 min, "
        "xgb_v12_quantile ~10-12 min, leak audit ~1 min, replicate seed ~15 min each; "
        "total ~35 min with NBA_V3_REPLICATES=1"
    )


def load_v3():
    """Engine ``load`` re-pointed at season 2025; refuses a parquet that is not the 2022-2025 export."""
    load()
    seasons = sorted({int(s) for s in C.df["season"].unique()})
    if TEST_SEASON_V3 not in seasons or seasons[0] != 2022:
        raise ValueError(f"expected the 2022-2025 export, got seasons {seasons}")
    if int(C.test_season.min()) != TEST_SEASON_V3 or int(C.test_season.max()) != TEST_SEASON_V3:
        raise AssertionError("test rows must be season 2025 only")
    if not C.blocks or not all(b["season"] == TEST_SEASON_V3 for b in C.blocks):
        raise AssertionError("blocks must all be season-2025 months")
    log(
        f"v3 test blocks: {[b['fold'] for b in C.blocks]} (train rows before each: {[b['n'] for b in C.blocks]})"
    )


# ------------------------------------------------------------------------------ leak audit


def audit_v3(p12):
    """Static allow-list check ran in ``load``; here the engine's dynamic detector (top-10 gain and
    |SHAP| allow-listed, deny-regex clean, |rank corr| < 0.95 with the same-game label) on one fit
    of the frozen config using rows strictly before the last 2025 block."""
    blk = [b for b in C.blocks if b["ok"]][-1]
    n = blk["n"]
    rec = {}
    for st in STATS:
        ci = cols_for(st, "v12")
        r = C.y[st][:n] - C.m[st][:n]
        h = fit_head(make_head("xgb", p12, C.B["rounds"], True), C.X[:n][:, ci], r, "xgb", True)
        imp = np.asarray(h.feature_importances_, dtype=float)
        rec[st] = [(blk["season"], dict(zip([C.names[i] for i in ci], imp, strict=True)))]
    return leak_audit(p12, rec)


# ------------------------------------------------------------------------------ main


def block_fns(p12):
    B = C.B
    return {
        "v1_prod": lambda b: resid_block(b, "v1", "lgb", {}, B["prod_rounds"], False),
        "xgb_v12_poisson_nb": lambda b: count_block(b, "xgb", p12, B["rounds"]),
        "xgb_v12_quantile": lambda b: quantile_block(b, p12, B["rounds"]),
    }


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main_v3():
    import gc
    import shutil

    setup_v3()
    parquet = Path(os.environ["NBA_PARQUET"])
    prereg_id = check_prereg(
        os.environ, parquet.parent / "job_meta.json"
    )  # BEFORE any 2025 row is read
    log(f"pre-registration id OK: {prereg_id}")
    load_v3()
    p12 = FROZEN["p12"]
    run_root = Path(os.environ.get("NBA_ARTIFACT_ROOT", "artifacts"))
    ck = run_root.parent / "checkpoints" / data_hash()[:12]
    C.store = ArmStore(ck)
    out_ptr = ck / "out_dir.txt"  # resume writes into the same artifact folder
    if out_ptr.exists():
        out = Path(out_ptr.read_text().strip())
    else:
        out = run_root / datetime.now().strftime("%Y%m%d_%H%M%S")
        out_ptr.write_text(str(out))
    out.mkdir(parents=True, exist_ok=True)
    # ---- small artifacts first
    (out / "frozen_config.json").write_text(
        json.dumps(jsonable({**FROZEN, "frozen_sha256": frozen_sha256()}), indent=1)
    )
    header = dict(
        experiment_tag=EXPERIMENT_TAG,
        preregistration_id=prereg_id,
        engine_sha256=ENGINE_SHA256,
        frozen_config_sha256=frozen_sha256(),
        parquet_sha256=sha256_file(parquet),
        n_rows=int(len(C.df)),
        n_test_rows=int(C.nt),
        blocks=[dict(fold=b["fold"], n_train=b["n"], n_test=b["b"] - b["a"]) for b in C.blocks],
        device=C.device,
        gpu=C.gpu_name,
        seeds=dict(seed=SEED),
        python=sys.version.split()[0],
        started=datetime.now().isoformat(timespec="seconds"),
        budget=C.budget_name,
    )
    (out / "run_header.json").write_text(json.dumps(jsonable(header), indent=1))
    audit = cached_json("leak_audit", lambda: audit_v3(p12))
    (out / "leak_audit.json").write_text(json.dumps(jsonable(audit), indent=1))
    # ---- arms (each checkpointed; a finished arm is RESUMED, never recomputed)
    results, timings = {}, {}
    fns = block_fns(p12)
    for name in ("xgb_v12_poisson_nb", "v1_prod", "xgb_v12_quantile"):
        run_arm(name, fns[name], results, timings)
        log_mem(name)
    arms = {a: C.store.get(a) for a in ("v1_prod", "xgb_v12_quantile", "xgb_v12_poisson_nb")}
    variants = {
        **arms,
        "hybrid_v3": assemble_hybrid(arms),
        "recency_normal": recency_res(),
    }
    variants["hybrid_v3|platt_pts"] = platt_pts_only(variants["hybrid_v3"])
    ref = arms["v1_prod"]
    metrics = dict(
        experiment_tag=EXPERIMENT_TAG,
        complete=False,
        preregistration_id=prereg_id,
        frozen_config_sha256=frozen_sha256(),
        engine_sha256=ENGINE_SHA256,
        budget=C.budget_name,
        device=C.device,
        gpu=C.gpu_name,
        seeds=dict(seed=SEED),
        test_season=TEST_SEASON_V3,
        n_test_rows=int(C.nt),
        timings_s=timings,
        leak_audit=audit,
        hybrid=HYBRID,
        calibrator="platt_pts_only",
        leaderboard_2025={
            v: {
                st: summarize(r, st, TEST_SEASON_V3, ref if v != "v1_prod" else None)
                for st in STATS
            }
            for v, r in variants.items()
        },
        blocks_crps={v: per_block_crps(r) for v, r in variants.items()},
    )
    (out / "metrics_partial.json").write_text(json.dumps(jsonable(metrics), indent=1))
    # ---- descriptive seed sensitivity (never enters the keep rule)
    reps = int(os.environ.get("NBA_V3_REPLICATES", "1"))
    metrics["seed_replicates"] = replicate_summaries(reps, p12, variants["hybrid_v3"], ref)
    # ---- OOF per variant, then publish
    parts = ck / "oof_parts"
    parts.mkdir(exist_ok=True)
    for v, r in variants.items():
        write_oof_variant(parts, v, r, True)
    gc.collect()
    shutil.copytree(
        parts, out / "oof", dirs_exist_ok=True
    )  # appears only when everything is written
    metrics["complete"] = True
    (out / "metrics.json").write_text(json.dumps(jsonable(metrics), indent=1))
    (out / "metrics_partial.json").unlink(missing_ok=True)
    log_mem("done")
    log(f"ARTIFACTS WRITTEN TO {out}")
    return metrics


def replicate_summaries(reps, p12, primary_hybrid, ref):
    """Rerun the two component arms with seed + r (r = 1..reps); report the hybrid's mean CRPS per
    stat and the difference from the primary run. Descriptive only."""
    global SEED
    base_seed = SEED
    out = {}
    for r in range(1, reps + 1):
        res, tm = {}, {}
        SEED = base_seed + r  # read by make_head at call time (xgboost random_state)
        try:
            for name in ("xgb_v12_poisson_nb", "xgb_v12_quantile"):
                run_arm(f"rep{r}_{name}", block_fns(p12)[name], res, tm)
        finally:
            SEED = base_seed
        arms = {a: C.store.get(f"rep{r}_{a}") for a in ("xgb_v12_poisson_nb", "xgb_v12_quantile")}
        h = assemble_hybrid(arms)
        out[f"seed+{r}"] = {
            st: dict(
                crps=summarize(h, st, TEST_SEASON_V3).get("crps"),
                d_crps_vs_v1=summarize(h, st, TEST_SEASON_V3, ref).get("d_crps_vs_ref"),
                d_crps_vs_primary=summarize(h, st, TEST_SEASON_V3, primary_hybrid).get(
                    "d_crps_vs_ref"
                ),
            )
            for st in STATS
        }
    return out


def entry_v3():
    return main_v3()


if __name__ == "__main__":
    entry_v3()
