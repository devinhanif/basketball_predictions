# mypy: ignore-errors
# ruff: noqa: E501
"""ctxres_v3 LEAK-FREE per-stat hybrid: local CPU replication run (experiment 3, leak-fixed).

PRE-REGISTRATION: docs/CTXRES_V3_LEAKFREE.md (text up to its PREREG-END line; its sha256 is stored in
``run_header.json``). Seasons 2023 and 2024 only: 2024 was ALREADY SEEN for this recipe (docs/RIDGE_V2.md
sec. 9), so this is a replication / sanity run, never a confirmation. Season 2025 is refused.

Engine: the ridge_v2 sweep engine (``research/colab/jobs/ridge_v2_sweep/ridge_v2_sweep.py``, sha256 pinned)
is imported as a module and driven on CPU. Its ``ref_fixed`` arm IS the hybrid: v1 + v2 groups a-h,j +
the leak-free ``rg_f3b8321a_*`` ridge columns, pts <- 19-quantile XGBoost, reb/ast/fg3m <- Poisson/NB.
The staged ``ridge_v2_spec.json`` is a FILTERED copy (only ``ref_fixed``; the leaky ``exp2_columns``
emptied) so no ``ridge_*`` column can even be loaded.

Variants (priority order; later ones are cut first): ``v1_prod`` (LightGBM production recipe refit in the
same blocks), ``hybrid_lf``, ``recency_normal``, ``alt_lf`` (other family per stat; descriptive).
Crash safety: every (variant, stat, block) prediction is a small npz checkpoint; OOF parquet is streamed
per variant and season; a re-run resumes. A heavy-job lock (``data/ops/heavy.lock``) is held while fitting.

    uv run --with xgboost python research/colab/jobs/ctxres_v3_leakfree/ctxres_v3_leakfree.py
"""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[4]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research.eval.ctxres_v3_leakfree import (  # noqa: E402
    STATS,
    LeakStop,
    assert_leakfree_columns,
    heavy_lock,
    missingness_audit,
    prereg_sha256,
)

ENGINE_PATH = REPO / "research/colab/jobs/ridge_v2_sweep/ridge_v2_sweep.py"
ENGINE_SHA256 = "4952cc9c46f1b180d530a903426322ee4f1cee73ab5822ffac5a89450e5b8ca6"
PREREG_DOC = REPO / "docs/CTXRES_V3_LEAKFREE.md"
SRC = {
    "ctxres_v2.parquet": REPO / "data/colab/ctxres_v2/ctxres_v2.parquet",
    "feature_spec.json": REPO / "data/colab/ctxres_v2/feature_spec.json",
    "ridge_v2.parquet": REPO / "data/colab/ridge_v2/ridge_v2.parquet",
    "base_config.json": REPO / "data/colab/ridge_v2/base_config.json",
}
RIDGE_SPEC = REPO / "data/colab/ridge_v2/ridge_v2_spec.json"
DEFAULT_ROOT = REPO / "data/colab/ctxres_v3_leakfree"
ALT = {"quantile": "poisson_nb", "poisson_nb": "quantile"}
VARIANTS = ("v1_prod", "hybrid_lf", "recency_normal", "alt_lf")
CODE_VERSION = "v3lf-ckpt-1"

rs = None  # the ridge_v2 engine module
C = None


def load_engine():
    got = hashlib.sha256(ENGINE_PATH.read_bytes()).hexdigest()
    if got != ENGINE_SHA256:
        raise RuntimeError(f"ridge_v2 engine drifted: sha256 {got} != pinned {ENGINE_SHA256}")
    spec = importlib.util.spec_from_file_location("ridge_v2_engine", ENGINE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def stage(root, spec_path=RIDGE_SPEC, src=SRC):
    """Symlink the inputs and write the FILTERED ridge spec (ref_fixed only, no leaky columns)."""
    d = Path(root) / "stage"
    d.mkdir(parents=True, exist_ok=True)
    for name, p in src.items():
        link = d / name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(Path(p).resolve())
    rspec = json.loads(Path(spec_path).read_text())
    keep = {"ref_fixed": rspec["arms"]["ref_fixed"]}
    filt = dict(rspec)
    filt["arms"] = keep
    filt["exp2_columns"] = {s: [] for s in STATS}
    (d / "ridge_v2_spec.json").write_text(json.dumps(filt))
    return d / "ctxres_v2.parquet"


def setup(budget):
    global rs, C
    rs = load_engine()
    C = rs.C
    C.t0 = time.monotonic()
    rs.BUDGETS["test"] = dict(arm_s=300, rounds=30, min_train=250, min_cal=40, est_arm_min=0.2)
    C.budget_name = budget
    C.smoke = False  # never the engine's 2% game sampling
    C.B = dict(rs.BUDGETS[budget])
    C.B["prod_rounds"] = 200 if budget == "full" else 20
    C.device = "cpu"
    C.torch = None
    C.rng = np.random.default_rng(rs.SEED)
    C.njobs = int(os.environ.get("NBA_NJOBS", "4"))


# ------------------------------------------------------------------------------ models


def v1_stat(blk, st):
    """Production recipe (port of the exp-2 sweep ``resid_block`` kind=v1, engine=lgb) for one stat."""
    import lightgbm as lgb

    def head():
        return lgb.LGBMRegressor(
            n_estimators=C.B["prod_rounds"],
            learning_rate=0.04,
            num_leaves=15,
            min_child_samples=100,
            colsample_bytree=0.8,
            subsample=0.8,
            subsample_freq=1,
            random_state=0,
            n_jobs=C.njobs,
            verbose=-1,
            deterministic=True,
            force_row_wise=True,
        )

    n = blk["n"]
    ci = np.array([C.ci[c] for c in C.spec["v1_features"][st]])
    d = C.dates[:n]
    n_fit = int(
        np.searchsorted(d, d[min(int(len(d) * 0.85), max(len(d) - C.B["min_cal"], 1))], side="left")
    )
    X = C.X[:n][:, ci]
    m, y = C.m[st][:n], C.y[st][:n]
    r = y - m
    mh = head().fit(X[:n_fit], r[:n_fit])
    sh = head().fit(X[:n_fit], np.abs(r[:n_fit]))
    floor = 0.3 * C.dstd[st] / 2.5
    mu_c = mh.predict(X[n_fit:])
    z = (r[n_fit:] - mu_c) / np.maximum(sh.predict(X[n_fit:]), floor)
    zq, zmean = np.quantile(z, rs.TAUS199), float(z.mean())
    ia, ib = blk["a"], blk["b"]
    Xt = C.X[ia:ib][:, ci]
    mu, sc = mh.predict(Xt), np.maximum(sh.predict(Xt), floor)
    mt = C.m[st][ia:ib]
    q = rs.clean_q(mt[:, None] + mu[:, None] + sc[:, None] * zq[None, :])
    return dict(q=q, mean=np.clip(mt + mu + sc * zmean, 0, None))


def unit_pred(variant, st, blk, params):
    rounds = C.B["rounds"]
    if variant == "v1_prod":
        return v1_stat(blk, st)
    if variant == "hybrid_lf":
        return rs.stat_pred(blk, "ref_fixed", st, params, rounds)
    if variant == "recency_normal":
        ia, ib = blk["a"], blk["b"]
        return dict(q=rs.clean_q(rs.normal_q(C.m[st][ia:ib], C.s[st][ia:ib])), mean=C.m[st][ia:ib])
    if variant == "alt_lf":
        fam = dict(rs.FAMILY)
        rs.FAMILY = {**fam, st: ALT[fam[st]]}
        try:
            return rs.stat_pred(blk, "ref_fixed", st, params, rounds)
        finally:
            rs.FAMILY = fam
    raise ValueError(variant)


# ------------------------------------------------------------------------------ checkpoints / OOF


def unit_file(ck, variant, st, blk):
    return Path(ck) / "units" / variant / st / f"{blk['fold']}.npz"


def save_unit(path, pred):
    path.parent.mkdir(parents=True, exist_ok=True)
    flat = {
        "q": np.asarray(pred["q"], np.float32),
        "mean": np.asarray(pred.get("mean", pred["q"].mean(axis=1)), np.float32),
    }
    if pred.get("p") is not None:
        flat["p"] = np.asarray(pred["p"], np.float32)
    tmp = path.with_suffix(".tmp.npz")
    np.savez(tmp, **flat)
    os.replace(tmp, path)


def load_unit(path):
    with np.load(path) as z:
        return {k: z[k] for k in z.files}


def write_oof(out_dir, name, res, season):
    """Stream one variant's rows of one season to its own parquet (list<float32> q19 / p_ge)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    df = C.df
    sel = C.test_season == season
    idx19 = [int(round(t * 199 - 0.5)) for t in rs.TAUS19]
    tabs = []
    for st in STATS:
        m = sel & rs.ok_mask(res, st)
        n = int(m.sum())
        if n == 0:
            continue
        y = C.y[st][C.t0i :][m]
        q = res[st]["q"][m]
        cols = {
            "variant": pa.array(np.full(n, name)),
            "stat": pa.array(np.full(n, st)),
            "game_id": pa.array(C.gid[C.t0i :][m]),
            "player_id": pa.array(df["player_id"].to_numpy()[C.t0i :][m]),
            "fold_id": pa.array(df["fold_id"].to_numpy()[C.t0i :][m]),
            "y": pa.array(y.astype(np.float32)),
            "mean": pa.array(res[st]["mean"][m].astype(np.float32)),
            "crps": pa.array(rs.crps_rows(q.astype(float), y).astype(np.float32)),
            "tll": pa.array(
                rs.tll_rows(res[st]["p"][m].astype(float), y, rs.THR[st]).astype(np.float32)
            ),
        }
        flat = np.ascontiguousarray(q[:, idx19], dtype=np.float32).ravel()
        cols["q19"] = pa.ListArray.from_arrays(
            pa.array(np.arange(0, n * 19 + 1, 19, dtype=np.int32)), pa.array(flat)
        )
        k = len(rs.THR[st])
        flatp = np.ascontiguousarray(res[st]["p"][m], dtype=np.float32).ravel()
        cols["p_ge"] = pa.ListArray.from_arrays(
            pa.array(np.arange(0, n * k + 1, k, dtype=np.int32)), pa.array(flatp)
        )
        tabs.append(pa.table(cols))
    pq.write_table(pa.concat_tables(tabs), Path(out_dir) / f"{name}.parquet", compression="zstd")


def run_variant(variant, ck, run_dir, params):
    done = [(Path(run_dir) / f"oof_{s}" / f"{variant}.parquet").exists() for s in (2023, 2024)]
    if all(done):
        rs.log(f"variant {variant}: RESUMED (OOF already written)")
        return
    t = time.monotonic()
    todo = [b for b in C.blocks if b["ok"]]
    res = rs.new_res()
    for st in STATS:
        for i, blk in enumerate(todo):
            f = unit_file(ck, variant, st, blk)
            if not f.exists():
                t1 = time.monotonic()
                save_unit(f, unit_pred(variant, st, blk, params))
                rs.log(
                    f"  {variant}/{st} block {blk['fold']} ({i + 1}/{len(todo)}) {time.monotonic() - t1:.1f}s"
                )
            rs.store_pred(res, blk, st, load_unit(f))
    for season in (2023, 2024):
        write_oof(Path(run_dir) / f"oof_{season}", variant, res, season)
    rs.log(f"variant {variant}: done in {time.monotonic() - t:.0f}s")
    del res
    import gc

    gc.collect()


# ------------------------------------------------------------------------------ main


def git_state():
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--", "nba", "docs/CTXRES_V3_LEAKFREE.md"],
                capture_output=True,
                text=True,
                cwd=REPO,
            ).stdout.strip()
        )
        return sha, dirty
    except OSError:
        return "unknown", True


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--run-id", default="run_20261009")
    ap.add_argument(
        "--budget", default=os.environ.get("NBA_BUDGET", "full"), choices=["full", "test"]
    )
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--db", default=str(REPO / "nba.duckdb"))
    ap.add_argument("--no-lock", action="store_true")
    ap.add_argument("--skip-db-audit", action="store_true", help="tests only")
    a = ap.parse_args(argv)
    want = [v for v in VARIANTS if v in a.variants.split(",")]
    run_dir = Path(a.root) / a.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    sha = prereg_sha256(PREREG_DOC)
    prev = run_dir / "run_header.json"
    if prev.exists() and json.loads(prev.read_text())["prereg_sha256"] != sha:
        raise RuntimeError("pre-registration text changed since this run directory was started")
    staged = stage(a.root)
    os.environ["NBA_PARQUET"] = str(staged)
    setup(a.budget)
    ctx = heavy_lock() if not a.no_lock else __import__("contextlib").nullcontext()
    with ctx:
        rs.load()
        rs.audit_names(C.names, "static")
        assert_leakfree_columns({s: C.base_cols[s] + C.arms["ref_fixed"][s] for s in STATS})
        assert_leakfree_columns({"all": C.names})
        if int(C.season.max()) >= 2025:
            raise LeakStop("season >= 2025 present")
        frame = run_dir / "final_feature_frame.parquet"
        C.df[["game_id", "player_id", "season"] + list(C.names)].to_parquet(frame)
        audit = {"skipped": True}
        if not a.skip_db_audit:
            audit = missingness_audit(frame, a.db, C.names)
            rs.log(
                f"missingness audit passed: max asymmetry {audit['max_asymmetry']} over {audit['n_columns']} columns"
            )
        label_audit = rs.null_label_audit()
        git_sha, dirty = git_state()
        import lightgbm
        import xgboost

        header = dict(
            prereg_sha256=sha,
            code_version=CODE_VERSION,
            engine_sha256=ENGINE_SHA256,
            budget=a.budget,
            seed=rs.SEED,
            xgboost=xgboost.__version__,
            lightgbm=lightgbm.__version__,
            device="cpu",
            n_jobs=C.njobs,
            git_sha=git_sha,
            git_dirty_nba_or_prereg=dirty,
            started=datetime.now().isoformat(timespec="seconds"),
            n_rows=len(C.df),
            n_test_rows=int(C.nt),
            blocks=len(C.blocks),
            features={s: C.base_cols[s] + C.arms["ref_fixed"][s] for s in STATS},
            missingness_audit=audit,
            null_label_audit_max_gap=max(
                (abs(v["std_label_gap"]) for v in label_audit.values()), default=0.0
            ),
        )
        prev.write_text(json.dumps(header, indent=1, default=float))
        params = dict(C.base_cfg["params"], n_jobs=C.njobs)
        ck = run_dir / "checkpoints"
        for v in want:
            run_variant(v, ck, run_dir, params)
    header["finished"] = datetime.now().isoformat(timespec="seconds")
    header["variants_done"] = want
    prev.write_text(json.dumps(header, indent=1, default=float))
    rs.log(f"RUN DIR {run_dir}")
    return run_dir


if __name__ == "__main__":
    main()
