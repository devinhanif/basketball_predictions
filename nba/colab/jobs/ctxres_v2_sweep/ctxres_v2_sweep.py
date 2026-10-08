# mypy: ignore-errors
# ruff: noqa: E501
# Colab-executed job script (untyped GPU libs on the Colab VM); kept out of strict type checking.
"""ctxres_v2 model-family sweep (self-contained: numpy/pandas/scipy/sklearn/xgboost/torch).

PRE-REGISTERED PROTOCOL (full text: docs/CTXRES_V2.md; rule applied by nba/eval/ctxres_v2_eval.py)
  * Data: ``ctxres_v2.parquet`` + ``feature_spec.json`` (seasons 2022-2024; 2022 = warm-up). Season
    2025 is refused. Walk-forward: every calendar-month block is predicted by models fit on rows
    dated strictly before the block.
  * Selection uses ONLY season-2023 blocks (hyperparameters, best arm, top-3 blend members,
    calibrator settings). Season-2024 blocks are REPORT-ONLY.
  * Selection objective: mean over the 4 stats of CRPS(arm) / CRPS(recency-Normal baseline) on the
    same rows (scale-free so pts does not dominate).
  * Arms (all share the same blocks, features per stat from the spec):
      v1_prod            LightGBM residual + |resid| + split-conformal, production config, v1 features
      xgb_v1_prodcfg     same recipe/config on XGBoost (isolates the engine)
      xgb_v12_prodcfg    ... with v1+v2 features (isolates features at fixed config)
      xgb_v1_tuned       Optuna-tuned XGBoost residual arm, v1 features
      xgb_v12_tuned      Optuna-tuned XGBoost residual arm, v1+v2 features
      xgb_v12_rel/_both  chosen xgb_v12_tuned config; features season-relative / raw+relative
      xgb_v12_quantile   reg:quantileerror multi-quantile direct distribution
      xgb_v12_poisson_nb count:poisson mean + NegBin dispersion (per position group, shrunk)
      glm_poisson_nb     Poisson GLM (standardized) + NegBin dispersion
      mlp_quantile       tabular MLP, monotone 19-quantile head, pinball loss
      logit_thr          L2 logistic per threshold (isotonic across N); threshold probabilities only
      blend_top3         equal-weight blend (quantile + probability average) of the top-3 distribution
                         arms ranked on 2023
  * Calibrators (nested walk-forward: fit on EARLIER blocks' out-of-fold predictions, apply to the next
    block; parameters tuned on 2023 using the 2023-selected best arm, reported on 2024):
      threshold probabilities: platt (pooled, logit p + ln N) | iso (per-threshold, monotone in N after)
      distributions: conf (standardized-score recalibration) | pit (PIT-ECDF recalibration) | none
  * Threshold events: P(stat >= N), N in THR below (the common prop lines).
BUDGET (env NBA_BUDGET): fast (default, ~15-20 min on a T4, estimate) | full | smoke (local CPU).
Seeds are logged in config; GPU XGBoost is not bit-reproducible.
"""

import functools
import json
import math
import os
import subprocess
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

STATS = ("pts", "reb", "ast", "fg3m")
THR = {
    "pts": np.array([10, 15, 20, 25, 30]),
    "reb": np.array([4, 6, 8, 10]),
    "ast": np.array([2, 4, 6, 8]),
    "fg3m": np.array([1, 2, 3, 4]),
}
TAUS199 = (np.arange(1, 200) - 0.5) / 199
TAUS19 = np.arange(1, 20) / 20.0
SEED = 20261008
SELECT_SEASON, REPORT_SEASON, FROZEN_SEASON = 2023, 2024, 2025
PLOGMIN = 1e-4

BUDGETS = {
    "fast": dict(
        trials=12,
        arm_s=900,
        total_s=2100,
        search_s=150,
        search_stride=4,
        search_sub=2,
        rounds=300,
        prod_rounds=200,
        glm_cap=30000,
        logit_cap=30000,
        mlp_epochs=15,
        abl_stride=3,
        min_train=1500,
        min_cal=2000,
        est="~30-35 min on a T4 (MEASURED in run 20261008_131747: arms through xgb_v12_quantile took ~23 min; optional arms are skipped once 80% of 35 min is used and are then missing)",
    ),
    "full": dict(
        trials=40,
        arm_s=900,
        total_s=4000,
        search_s=600,
        search_stride=2,
        search_sub=1,
        rounds=600,
        prod_rounds=200,
        glm_cap=80000,
        logit_cap=80000,
        mlp_epochs=30,
        abl_stride=1,
        min_train=1500,
        min_cal=2000,
        est="~45-60 min on a T4 (estimate, unmeasured)",
    ),
    "smoke": dict(
        trials=2,
        arm_s=120,
        total_s=600,
        search_s=60,
        search_stride=4,
        search_sub=1,
        rounds=40,
        prod_rounds=30,
        glm_cap=3000,
        logit_cap=3000,
        mlp_epochs=2,
        abl_stride=4,
        min_train=250,
        min_cal=40,
        est="~1-2 min on a CPU (2% sample)",
    ),
}
ARM_ESTIMATES = {  # minutes on a T4, fast budget; "M" = measured in run 20261008_131747, "?" = not measured
    "v1_prod (CPU lightgbm) M": 4.3,
    "xgb_v1_prodcfg M": 1.3,
    "xgb_v12_prodcfg M": 2.2,
    "optuna search v1 + v12 M": 2.3,
    "xgb_v1_tuned + xgb_v12_tuned M": 3.1,
    "leak audit + xgb_v12_pruned M": 1.2,
    "xgb_v12_rel + xgb_v12_both M": 4.3,
    "xgb_v12_quantile M (2023 only; both seasons ~8.7)": 8.7,
    "xgb_v12_poisson_nb ?": 1.5,
    "glm_poisson_nb (CPU) ?": 2.0,
    "logit_thr (CPU) ?": 2.0,
    "mlp_quantile ?": 2.0,
    "calibrators (CPU) ?": 1.5,
    "ablation (10 groups) ?": 2.0,
}
COMPLETE_ESTIMATES = {  # NBA_MODE=complete, minutes on a T4 (from the measured per-arm times above)
    "xgb_v12_quantile 2024 blocks": 4.4,
    "xgb_v12_prodcfg + xgb_v12_rel 2024 (blend members)": 2.1,
    "v1_prod 2024 (CPU lightgbm)": 2.2,
    "optional tail: xgb_v12_quantile 2023 (calibrator history) + calibrators": 5.5,
}


# ------------------------------------------------------------------------------ setup


def pip_install(pkg):
    try:
        __import__(pkg)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", pkg], check=True)


class Ctx:
    """Mutable globals for one run."""


C = Ctx()


def log(*a):
    print(f"[{time.monotonic() - C.t0:7.1f}s]", *a, flush=True)


def setup():
    C.t0 = time.monotonic()
    C.budget_name = os.environ.get("NBA_BUDGET", "fast")
    C.smoke = C.budget_name == "smoke"
    C.B = BUDGETS[C.budget_name]
    for p in ("optuna", "xgboost"):
        pip_install(p)
    import torch
    import xgboost  # noqa: F401

    C.torch = torch
    C.device = "cuda" if torch.cuda.is_available() else "cpu"
    if C.device == "cpu":  # avoid OpenMP clashes between torch and lightgbm/xgboost on CPU hosts
        torch.set_num_threads(1)
    C.rng = np.random.default_rng(SEED)
    C.mode = os.environ.get("NBA_MODE", "sweep")
    print(f"BUDGET={C.budget_name} MODE={C.mode} device={C.device}")
    if C.mode == "complete":
        print(
            "expected runtime: core ~9 min, +5.5 min optional calibrator tail (NBA_COMPLETE_CAL=0 skips)"
        )
        for k, v in COMPLETE_ESTIMATES.items():
            print(f"   {k:<70} ~{v:.1f} min")
    else:
        print(f"expected runtime: {C.B['est']}")
        for k, v in ARM_ESTIMATES.items():
            print(f"   {k:<50} ~{v:.1f} min")
    print(
        "   (per-arm wall times are printed as the run proceeds and stored in metrics.json timings_s)"
    )


def load():
    path = Path(os.environ["NBA_PARQUET"])
    spec = json.loads((path.parent / "feature_spec.json").read_text())
    df = pd.read_parquet(path)
    if int(df["season"].max()) >= FROZEN_SEASON:
        raise ValueError("season 2025 present: the frozen holdout must not be loaded")
    df = df.sort_values(["game_date", "game_id", "player_id"], kind="stable")
    if C.smoke:
        games = pd.Series(df["game_id"].unique()).sample(frac=0.02, random_state=SEED)
        df = df[df["game_id"].isin(set(games))]
    df = df.reset_index(drop=True)
    C.df, C.spec = df, spec
    C.dates = df["game_date"].to_numpy().astype("datetime64[D]")
    C.season = df["season"].to_numpy()
    C.gid = df["game_id"].to_numpy()
    base_cols = sorted({c for s in STATS for c in spec["v1_features"][s] + spec["v2_features"][s]})
    rel_cols = relative_columns(base_cols)
    X = df[base_cols].to_numpy(dtype=np.float32)
    names = list(base_cols)
    if rel_cols:
        R = season_relative(df, rel_cols)
        X = np.concatenate([X, R], axis=1)
        names += ["rel__" + c for c in rel_cols]
    C.X, C.names = X, names
    C.ci = {c: i for i, c in enumerate(names)}
    C.rel_cols = rel_cols
    C.pruned = {}
    audit_names(names, "static")
    C.y = {s: df[s].to_numpy(dtype=float) for s in STATS}
    C.m = {s: df[f"base_m_{s}"].to_numpy(dtype=float) for s in STATS}
    C.s = {s: df[f"base_s_{s}"].to_numpy(dtype=float) for s in STATS}
    C.pos = np.nan_to_num(df["pos_code"].to_numpy(dtype=float), nan=-1.0)
    C.dstd = spec["default_std"]
    t0 = int(np.argmax(C.season >= SELECT_SEASON))
    C.t0i, C.nt = t0, len(df) - t0
    fold = df["fold_id"].to_numpy()
    blocks, start = [], t0
    for i in range(t0 + 1, len(df) + 1):
        if i == len(df) or fold[i] != fold[start]:
            blocks.append(
                dict(
                    season=int(C.season[start]),
                    fold=int(fold[start]),
                    a=start,
                    b=i,
                    n=int(np.searchsorted(C.dates, C.dates[start], side="left")),
                )
            )
            start = i
    for k, b in enumerate(blocks):
        b["k"] = k
        b["ok"] = b["n"] >= C.B["min_train"]
    C.blocks = blocks
    C.brank = np.full(C.nt, -1)
    for b in blocks:
        C.brank[b["a"] - t0 : b["b"] - t0] = b["k"]
    C.test_season = C.season[t0:]
    log(f"rows={len(df)} test rows={C.nt} blocks={len(blocks)} features={len(names)}")


def audit_names(names, where):
    """Static leak guard: only allow-listed as-of features, none matching same-game/post-tip names."""
    import re

    allow, deny = set(C.spec["allowlist"]), re.compile(C.spec["leak_deny_regex"])
    bad = []
    for n in names:
        base = n[5:] if n.startswith("rel__") else n
        if base not in allow or deny.search(base):
            bad.append(n)
    if bad:
        raise RuntimeError(
            f"LEAK AUDIT FAILED ({where}): non-allow-listed / same-game features {bad[:10]}"
        )


def leak_audit(p12, rec_imp):
    """Dynamic leak detector for the chosen config: top-10 by gain (all blocks) and by mean |SHAP|
    (xgboost pred_contribs, one fit on the last 2023 block's training rows) must be allow-listed,
    must not match the deny patterns and must not track the same-game label (|rank corr| < 0.95)."""
    import xgboost as xgb
    from scipy.stats import spearmanr

    out = {}
    blk = [b for b in C.blocks if b["season"] == SELECT_SEASON and b["ok"]][-1]
    for st in STATS:
        agg = {}
        for _season, d in rec_imp[st]:
            for k, v in d.items():
                agg[k] = agg.get(k, 0.0) + v
        top_gain = [k for k, _ in sorted(agg.items(), key=lambda kv: -kv[1])[:10]]
        ci = cols_for(st, "v12")
        n = blk["n"]
        r = C.y[st][:n] - C.m[st][:n]
        h = fit_head(make_head("xgb", p12, C.B["rounds"], True), C.X[:n][:, ci], r, "xgb", True)
        Xs = C.X[np.ix_(np.arange(max(0, n - 4000), n), ci)]
        contrib = h.get_booster().predict(xgb.DMatrix(Xs), pred_contribs=True)[:, :-1]
        mshap = np.abs(contrib).mean(axis=0)
        names = [C.names[i] for i in ci]
        top_shap = [names[i] for i in np.argsort(-mshap)[:10]]
        top = sorted(set(top_gain) | set(top_shap))
        audit_names(top, f"dynamic top-10 {st}")
        ysame = C.y[st][max(0, n - 4000) : n]
        corrs = {}
        for nm in top:
            v = Xs[:, names.index(nm)]
            ok = ~np.isnan(v)
            c = spearmanr(v[ok], ysame[ok]).statistic if ok.sum() > 50 else 0.0
            corrs[nm] = float(c)
            if abs(c) >= 0.95:
                raise RuntimeError(f"LEAK AUDIT FAILED: {nm} rank-corr {c:.3f} with the {st} label")
        out[st] = dict(top_gain=top_gain, top_shap=top_shap, label_rank_corr=corrs)
    log("leak audit passed (allow-list + top-10 gain/SHAP + label-correlation check)")
    return out


def relative_columns(cols):
    pre = ("m5_", "m10_", "m40_", "m100_", "r20_", "opp_r20_", "opp_allow_", "implied_")
    one = (
        "min5",
        "min10",
        "min40",
        "pr_usage",
        "pr_fta_rate",
        "pr_oreb36",
        "pr_dreb36",
        "game_total_feat",
    )
    return [c for c in cols if c.startswith(pre) or c in one]


def season_relative(df, cols, min_prior=300):
    """x - as-of league mean: mean of the SAME season over rows dated strictly before the row's
    date; before ``min_prior`` such rows exist, the previous season's full mean (NaN for the
    first season's opening days)."""
    out = np.full((len(df), len(cols)), np.nan, dtype=np.float32)
    d = df["game_date"].to_numpy().astype("datetime64[D]")
    seas = df["season"].to_numpy()
    prev_mean = None
    for sea in np.unique(seas):
        idx = np.where(seas == sea)[0]
        x = df.iloc[idx][cols].to_numpy(dtype=float)
        ds = d[idx]
        ud, inv = np.unique(ds, return_inverse=True)
        valid = ~np.isnan(x)
        sums = np.zeros((len(ud), len(cols)))
        cnts = np.zeros((len(ud), len(cols)))
        np.add.at(sums, inv, np.where(valid, x, 0.0))
        np.add.at(cnts, inv, valid.astype(float))
        cs = np.cumsum(sums, axis=0) - sums  # strictly earlier dates
        cc = np.cumsum(cnts, axis=0) - cnts
        with np.errstate(invalid="ignore", divide="ignore"):
            mean_d = cs / cc
        fallback = prev_mean if prev_mean is not None else np.full(len(cols), np.nan)
        mean_d = np.where(cc >= min_prior, mean_d, fallback[None, :])
        out[idx] = (x - mean_d[inv]).astype(np.float32)
        with np.errstate(invalid="ignore"):
            prev_mean = np.nanmean(x, axis=0)
    return out


def cols_for(stat, kind, drop_group=None):
    sp = C.spec
    v1, v2 = list(sp["v1_features"][stat]), list(sp["v2_features"][stat])
    if drop_group:
        gone = set(sp["v2_groups"][drop_group][stat])
        v2 = [c for c in v2 if c not in gone]
    base = v1 if kind == "v1" else v1 + [c for c in v2 if c not in v1]
    if kind == "pruned":
        base = [c for c in base if c in set(C.pruned[stat])]
    if kind in ("rel", "both"):
        rel = {c for c in C.rel_cols}
        raw = [c for c in base if c not in rel]
        relc = ["rel__" + c for c in base if c in rel]
        base = (raw + relc) if kind == "rel" else (raw + [c for c in base if c in rel] + relc)
    return np.array([C.ci[c] for c in base])


# ------------------------------------------------------------------------------ scoring


def crps_rows(q, y):
    d = y[:, None] - q
    return 2.0 * np.where(d >= 0, TAUS199 * d, (TAUS199 - 1.0) * d).mean(axis=1)


def cdf_grid(q, t):
    """F(t) by linear interpolation on the 199-quantile grid (ties tolerated)."""
    n = q.shape[0]
    c = (q < t[:, None]).sum(axis=1)
    ar = np.arange(n)
    ql, qh = q[ar, np.clip(c - 1, 0, 198)], q[ar, np.clip(c, 0, 198)]
    frac = np.clip((t - ql) / np.maximum(qh - ql, 1e-9), 0.0, 1.0)
    F = np.where(c == 0, 0.5 / 199, np.where(c >= 199, 1 - 0.5 / 199, (c - 0.5 + frac) / 199))
    return np.clip(F, 0.5 / 199, 1 - 0.5 / 199)


def p_from_grid(q, thr):
    p = np.stack([1.0 - cdf_grid(q, np.full(len(q), n - 0.5)) for n in thr], axis=1)
    return np.clip(p, PLOGMIN, 1 - PLOGMIN)


def tll_rows(p, y, thr):
    ev = y[:, None] >= thr[None, :]
    pc = np.clip(p, PLOGMIN, 1 - PLOGMIN)
    return -(np.where(ev, np.log(pc), np.log(1 - pc))).mean(axis=1)


def ece_eq_mass(p, ev, bins=10):
    o = np.argsort(p.ravel())
    pp, ee = p.ravel()[o], ev.ravel()[o].astype(float)
    return float(
        sum(
            len(g) * abs(pp[g].mean() - ee[g].mean())
            for g in np.array_split(np.arange(len(pp)), bins)
        )
        / len(pp)
    )


def normal_q(m, s):
    from scipy.stats import norm

    return m[:, None] + s[:, None] * norm.ppf(TAUS199)[None, :]


def clean_q(q):
    return np.maximum.accumulate(np.clip(q, 0.0, None), axis=1)


def boot_ci(delta, gid, n_boot=400, seed=SEED):
    """Game-clustered bootstrap CI for mean(delta)."""
    codes, _ = pd.factorize(gid)
    nc = codes.max() + 1
    s = np.bincount(codes, weights=delta, minlength=nc)
    c = np.bincount(codes, minlength=nc).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, nc, size=(n_boot, nc))
    means = s[idx].sum(axis=1) / c[idx].sum(axis=1)
    return [float(delta.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))]


# ------------------------------------------------------------------------------ result store


def new_res():
    return {
        s: dict(
            q=np.full((C.nt, 199), np.nan, np.float32),
            mean=np.full(C.nt, np.nan, np.float32),
            p=np.full((C.nt, len(THR[s])), np.nan, np.float32),
        )
        for s in STATS
    }


def store(res, blk, stat, pred):
    a, b = blk["a"] - C.t0i, blk["b"] - C.t0i
    if pred.get("q") is not None:
        res[stat]["q"][a:b] = pred["q"]
        res[stat]["mean"][a:b] = pred.get("mean", pred["q"].mean(axis=1))
        p = pred.get("p")
        res[stat]["p"][a:b] = p if p is not None else p_from_grid(pred["q"], THR[stat])
    else:
        res[stat]["p"][a:b] = pred["p"]


def ok_mask(res, stat):
    return ~np.isnan(res[stat]["p"][:, 0])


def base_crps(stat):
    sl = slice(C.t0i, None)
    q = normal_q(C.m[stat][sl], C.s[stat][sl])
    return crps_rows(q, C.y[stat][sl])


def selection_score(res, mask_fn=None):
    """Mean over stats of CRPS/CRPS(recency Normal) on 2023 rows (selection data only)."""
    r = []
    for s in STATS:
        sel = (C.test_season == SELECT_SEASON) & ok_mask(res, s)
        if sel.sum() == 0 or np.isnan(res[s]["q"][sel][:, 0]).any():
            return float("inf")
        c = crps_rows(res[s]["q"][sel].astype(float), C.y[s][C.t0i :][sel])
        r.append(c.mean() / base_crps(s)[sel].mean())
    return float(np.mean(r))


# ------------------------------------------------------------------------------ engines


def split_fit_cal(n, cal_frac=0.15):
    cut = int(n * (1.0 - cal_frac))
    cut = min(cut, max(n - C.B["min_cal"], 1))
    return int(np.searchsorted(C.dates[:n], C.dates[cut], side="left"))


def make_head(engine, params, rounds, es, objective="reg:squarederror", extra=None):
    if engine == "lgb":
        import lightgbm as lgb

        return lgb.LGBMRegressor(
            n_estimators=C.B["prod_rounds"],
            learning_rate=0.04,
            num_leaves=15,
            min_child_samples=100,
            colsample_bytree=0.8,
            subsample=0.8,
            subsample_freq=1,
            random_state=0,
            n_jobs=1 if C.smoke else -1,
            verbose=-1,
            deterministic=True,
            force_row_wise=True,
        )
    import xgboost as xgb

    kw = dict(
        n_estimators=rounds,
        tree_method="hist",
        device=C.device,
        objective=objective,
        random_state=SEED,
        verbosity=0,
        **params,
    )
    if es:
        kw["early_stopping_rounds"] = 20
    kw.update(extra or {})
    return xgb.XGBRegressor(**kw)


def fit_head(h, X, y, engine, es, w=None):
    if engine == "xgb" and es:
        nv = max(int(len(y) * 0.12), 40)
        h.fit(X[:-nv], y[:-nv], eval_set=[(X[-nv:], y[-nv:])], verbose=False)
    else:
        h.fit(X, y)
    return h


PROD_XGB = dict(
    learning_rate=0.04,
    max_depth=4,
    subsample=0.8,
    colsample_bytree=0.8,
    min_child_weight=100,
    reg_alpha=0.0,
    reg_lambda=1.0,
)


def resid_block(blk, kind, engine, params, rounds, es, record=None, stride=1, drop=None):
    out = {}
    n = blk["n"]
    for st in STATS:
        ci = cols_for(st, kind, drop)
        sel = np.arange(0, n, stride)
        d = C.dates[sel]
        n_fit = int(
            np.searchsorted(
                d, d[min(int(len(d) * 0.85), max(len(d) - C.B["min_cal"], 1))], side="left"
            )
        )
        X = C.X[np.ix_(sel, ci)]
        m, y = C.m[st][sel], C.y[st][sel]
        r = y - m
        mh = fit_head(make_head(engine, params, rounds, es), X[:n_fit], r[:n_fit], engine, es)
        sh = fit_head(
            make_head(engine, params, rounds, es), X[:n_fit], np.abs(r[:n_fit]), engine, es
        )
        floor = 0.3 * C.dstd[st] / 2.5
        mu_c = mh.predict(X[n_fit:])
        z = (r[n_fit:] - mu_c) / np.maximum(sh.predict(X[n_fit:]), floor)
        zq, zmean = np.quantile(z, TAUS199), float(z.mean())
        ia, ib = blk["a"], blk["b"]
        Xt = C.X[ia:ib][:, ci]
        mu, sc = mh.predict(Xt), np.maximum(sh.predict(Xt), floor)
        mt = C.m[st][ia:ib]
        q = clean_q(mt[:, None] + mu[:, None] + sc[:, None] * zq[None, :])
        out[st] = dict(q=q, mean=np.clip(mt + mu + sc * zmean, 0, None))
        if record is not None and engine == "xgb":
            imp = np.asarray(mh.feature_importances_, dtype=float)
            record.setdefault(st, []).append(
                (blk["season"], dict(zip([C.names[i] for i in ci], imp, strict=True)))
            )
    return out


def ext_tails(q19):
    """19 quantiles (tau 0.05..0.95) -> 199-grid with linear tail extrapolation."""
    q19 = np.sort(q19, axis=1)
    t = TAUS199
    k = np.clip(np.searchsorted(TAUS19, t) - 1, 0, 17)
    w = (t - TAUS19[k]) / 0.05
    q = q19[:, k] * (1 - w) + q19[:, k + 1] * w
    lo, hi = t < 0.05, t > 0.95
    s_lo = np.maximum(q19[:, 1] - q19[:, 0], 0.0) / 0.05
    s_hi = np.maximum(q19[:, 18] - q19[:, 17], 0.0) / 0.05
    q[:, lo] = q19[:, :1] + s_lo[:, None] * (t[lo] - 0.05)[None, :]
    q[:, hi] = q19[:, 18:19] + s_hi[:, None] * (t[hi] - 0.95)[None, :]
    return clean_q(q)


def quantile_block(blk, params, rounds):
    out = {}
    n = blk["n"]
    for st in STATS:
        ci = cols_for(st, "v12")
        r = C.y[st][:n] - C.m[st][:n]
        h = make_head("xgb", params, rounds, True, "reg:quantileerror", {"quantile_alpha": TAUS19})
        fit_head(h, C.X[:n][:, ci], r, "xgb", True)
        ia, ib = blk["a"], blk["b"]
        q19 = h.predict(C.X[ia:ib][:, ci]).reshape(ib - ia, 19) + C.m[st][ia:ib][:, None]
        out[st] = dict(q=ext_tails(q19))
    return out


def nb_pred(mu, alpha):
    from scipy.stats import nbinom

    mu = np.maximum(mu, 1e-3)
    nn = 1.0 / alpha
    pp = nn / (nn + mu)
    q = nbinom.ppf(TAUS199[None, :], nn[:, None], pp[:, None]).astype(np.float32)
    return q, nn, pp


def disp_alpha(y, mu, grp_fit, grp_te, k=300.0):
    a_all = max(float(np.sum((y - mu) ** 2 - mu) / max(np.sum(mu**2), 1e-9)), 1e-3)
    out = np.full(len(grp_te), a_all)
    for g in np.unique(grp_te):
        mk = grp_fit == g
        if mk.sum() >= 30:
            a_g = max(
                float(np.sum((y[mk] - mu[mk]) ** 2 - mu[mk]) / max(np.sum(mu[mk] ** 2), 1e-9)), 1e-3
            )
            out[grp_te == g] = (mk.sum() * a_g + k * a_all) / (mk.sum() + k)
    return out


def count_block(blk, engine, params, rounds):
    """Poisson mean (GLM or XGB count:poisson) + NegBin dispersion on the held-out cal window."""
    from scipy.stats import nbinom

    out = {}
    n = blk["n"]
    for st in STATS:
        ci = cols_for(st, "v12")
        n_fit = split_fit_cal(n)
        X, y = C.X[:n][:, ci], C.y[st][:n]
        Xt = C.X[blk["a"] : blk["b"]][:, ci]
        if engine == "glm":
            from sklearn.linear_model import PoissonRegressor

            lo = max(0, n_fit - C.B["glm_cap"])
            med = np.nanmedian(X[lo:n_fit], axis=0)
            med = np.where(np.isnan(med), 0.0, med)
            mu_, sd_ = np.nanmean(X[lo:n_fit], axis=0), np.nanstd(X[lo:n_fit], axis=0)
            mu_, sd_ = np.nan_to_num(mu_), np.where(np.nan_to_num(sd_) < 1e-6, 1.0, sd_)

            def prep(A, med=med, mu_=mu_, sd_=sd_):
                A = np.where(np.isnan(A), med[None, :], A)
                return np.clip((A - mu_) / sd_, -5, 5)

            h = PoissonRegressor(alpha=1e-2, max_iter=80).fit(prep(X[lo:n_fit]), y[lo:n_fit])
            mu_c, mu_t = h.predict(prep(X[n_fit:])), h.predict(prep(Xt))
        else:
            h = make_head("xgb", params, rounds, True, "count:poisson")
            fit_head(h, X[:n_fit], y[:n_fit], "xgb", True)
            mu_c, mu_t = h.predict(X[n_fit:]), h.predict(Xt)
        al = disp_alpha(y[n_fit:], mu_c, C.pos[n_fit:n], C.pos[blk["a"] : blk["b"]])
        q, nn, pp = nb_pred(mu_t, al)
        p = np.stack([nbinom.sf(N - 1, nn, pp) for N in THR[st]], axis=1)
        out[st] = dict(q=q, mean=mu_t, p=np.clip(p, PLOGMIN, 1 - PLOGMIN))
    return out


def logit_block(blk):
    from sklearn.linear_model import LogisticRegression

    out = {}
    n = blk["n"]
    for st in STATS:
        ci = cols_for(st, "v12")
        lo = max(0, n - C.B["logit_cap"])
        X = C.X[lo:n][:, ci]
        med = np.where(np.isnan(np.nanmedian(X, axis=0)), 0.0, np.nanmedian(X, axis=0))
        mu_, sd_ = np.nan_to_num(np.nanmean(X, axis=0)), np.nan_to_num(np.nanstd(X, axis=0))
        sd_ = np.where(sd_ < 1e-6, 1.0, sd_)

        def prep(A, med=med, mu_=mu_, sd_=sd_):
            return np.clip((np.where(np.isnan(A), med[None, :], A) - mu_) / sd_, -5, 5)

        Xs, Xt = prep(X), prep(C.X[blk["a"] : blk["b"]][:, ci])
        P = np.zeros((blk["b"] - blk["a"], len(THR[st])))
        for j, N in enumerate(THR[st]):
            e = (C.y[st][lo:n] >= N).astype(int)
            if e.sum() < 30 or (1 - e).sum() < 30:
                P[:, j] = e.mean()
                continue
            P[:, j] = LogisticRegression(C=0.05, max_iter=60).fit(Xs, e).predict_proba(Xt)[:, 1]
        P = np.minimum.accumulate(np.clip(P, PLOGMIN, 1 - PLOGMIN), axis=1)  # monotone in N
        out[st] = dict(p=P)
    return out


def mlp_block(blk, state):
    torch = C.torch
    nn_ = torch.nn
    dev = C.device
    n = blk["n"]
    cidx = np.unique(np.concatenate([cols_for(st, "v12") for st in STATS]))
    X = C.X[:n][:, cidx]
    med = np.where(np.isnan(np.nanmedian(X, axis=0)), 0.0, np.nanmedian(X, axis=0))
    nanshare = np.isnan(X).mean(axis=0)
    flag = np.where(nanshare > 0.02)[0]
    mu_, sd_ = np.nan_to_num(np.nanmean(X, axis=0)), np.nan_to_num(np.nanstd(X, axis=0))
    sd_ = np.where(sd_ < 1e-6, 1.0, sd_)

    def prep(A):
        Z = np.clip((np.where(np.isnan(A), med[None, :], A) - mu_) / sd_, -5, 5)
        return np.concatenate([Z, np.isnan(A[:, flag]).astype(np.float32)], axis=1).astype(
            np.float32
        )

    Xa = prep(X)
    ia, ib = blk["a"], blk["b"]
    Xt = prep(C.X[ia:ib][:, cidx])
    Y = np.stack([(C.y[s][:n] - C.m[s][:n]) / np.maximum(C.s[s][:n], 0.3) for s in STATS], axis=1)
    Y = np.clip(Y, -8, 8).astype(np.float32)
    nv = max(int(n * 0.12), 40)
    torch.manual_seed(SEED + blk["k"])
    net = nn_.Sequential(
        nn_.Linear(Xa.shape[1], 256),
        nn_.SiLU(),
        nn_.Dropout(0.1),
        nn_.Linear(256, 128),
        nn_.SiLU(),
        nn_.Linear(128, 4 * 19),
    ).to(dev)
    taus = torch.tensor(TAUS19, dtype=torch.float32, device=dev)

    def head(o):
        o = o.float().view(-1, 4, 19)
        return torch.cumsum(
            torch.cat([o[..., :1], torch.nn.functional.softplus(o[..., 1:]) * 0.3], -1), -1
        )

    def loss_fn(o, y):
        d = y[:, :, None] - head(o)
        return torch.maximum(taus * d, (taus - 1) * d).mean()

    xt_, yt_ = (torch.tensor(a, device=dev) for a in (Xa[:-nv], Y[:-nv]))
    xv_, yv_ = (torch.tensor(a, device=dev) for a in (Xa[-nv:], Y[-nv:]))
    opt = torch.optim.AdamW(net.parameters(), lr=2e-3, weight_decay=1e-4)
    use_amp = dev == "cuda"
    scaler = torch.amp.GradScaler("cuda", enabled=use_amp)
    best, best_state, bad = float("inf"), None, 0
    for _ep in range(C.B["mlp_epochs"]):
        net.train()
        perm = torch.randperm(len(xt_), device=dev)
        for i in range(0, len(perm), 2048):
            bi = perm[i : i + 2048]
            with torch.autocast(device_type="cuda", enabled=use_amp):
                loss = loss_fn(net(xt_[bi]), yt_[bi])
            opt.zero_grad()
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
        net.eval()
        with torch.no_grad():
            vl = float(loss_fn(net(xv_), yv_))
        if vl < best - 1e-5:
            best, bad = vl, 0
            best_state = {k: v.detach().clone() for k, v in net.state_dict().items()}
        else:
            bad += 1
            if bad >= 3:
                break
    net.load_state_dict(best_state)
    net.eval()
    with torch.no_grad():
        qz = head(net(torch.tensor(Xt, device=dev))).cpu().numpy()
    out = {}
    for j, st in enumerate(STATS):
        q19 = C.m[st][ia:ib][:, None] + np.maximum(C.s[st][ia:ib], 0.3)[:, None] * qz[:, j, :]
        out[st] = dict(q=ext_tails(q19))
    return out


# ------------------------------------------------------------------------------ arm runner


def over_budget(frac=0.8):
    return time.monotonic() - C.t0 > frac * C.B["total_s"]


def run_arm(
    name, block_fn, results, timings, budget_s=None, optional=False, seasons=None, into=None
):
    """Walk-forward one arm. An arm that cannot finish all requested blocks inside the hard cap is
    DROPPED (never stored), so a partially-computed arm can never win the 2023 selection."""
    if optional and over_budget():
        log(f"arm {name}: SKIPPED (budget); reported as missing")
        return None
    budget_s = budget_s or C.B["arm_s"]
    t = time.monotonic()
    res = into if into is not None else new_res()
    todo = [b for b in C.blocks if b["ok"] and (seasons is None or b["season"] in seasons)]
    done = 0
    for blk in todo:
        if time.monotonic() - t > budget_s:
            log(f"  {name}: hard cap hit after {done}/{len(todo)} blocks: arm DROPPED (incomplete)")
            timings[name + "_dropped_incomplete"] = time.monotonic() - t
            return None
        for st, pred in block_fn(blk).items():
            store(res, blk, st, pred)
        done += 1
    results[name] = res
    timings[name] = timings.get(name, 0.0) + time.monotonic() - t
    sc = selection_score(res) if (name != "logit_thr" and seasons is None) else float("nan")
    log(f"arm {name}: {done} blocks in {time.monotonic() - t:.0f}s | 2023 sel ratio {sc:.4f}")
    return res


# ------------------------------------------------------------------------------ optuna search


def search(label, kind, timings):
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    t = time.monotonic()
    sblocks = [b for b in C.blocks if b["season"] == SELECT_SEASON and b["ok"]][
        :: C.B["search_stride"]
    ]

    def sample(trial):
        return dict(
            learning_rate=trial.suggest_float("learning_rate", 0.05, 0.2, log=True),
            max_depth=trial.suggest_int("max_depth", 3, 5),
            subsample=trial.suggest_float("subsample", 0.6, 0.8),
            colsample_bytree=trial.suggest_float("colsample_bytree", 0.6, 0.8),
            min_child_weight=trial.suggest_float("min_child_weight", 20, 300, log=True),
            reg_alpha=trial.suggest_float("reg_alpha", 1e-2, 10, log=True),
            reg_lambda=trial.suggest_float("reg_lambda", 1, 50, log=True),
        )

    def objective(trial):
        params, ratios = sample(trial), []
        for bi, blk in enumerate(sblocks):
            pred = resid_block(
                blk, kind, "xgb", params, C.B["rounds"], True, stride=C.B["search_sub"]
            )
            for st in STATS:
                a, b = blk["a"], blk["b"]
                ratios.append(
                    crps_rows(pred[st]["q"], C.y[st][a:b]).mean()
                    / crps_rows(normal_q(C.m[st][a:b], C.s[st][a:b]), C.y[st][a:b]).mean()
                )
            trial.report(float(np.mean(ratios)), bi)
            if trial.should_prune():
                raise optuna.TrialPruned()
        return float(np.mean(ratios))

    study = optuna.create_study(
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=SEED),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=4),
    )
    study.optimize(objective, n_trials=C.B["trials"], timeout=C.B["search_s"])
    timings["search_" + label] = time.monotonic() - t
    log(f"search {label}: {len(study.trials)} trials, best selection ratio {study.best_value:.4f}")
    return dict(
        params=study.best_params,
        value=float(study.best_value),
        n_trials=len(study.trials),
        n_complete=sum(t.state.name == "COMPLETE" for t in study.trials),
        blocks=[b["fold"] for b in sblocks],
    )


# ------------------------------------------------------------------------------ calibrators


def wquantile(v, w, taus):
    o = np.argsort(v)
    v, w = v[o], w[o]
    cw = (np.cumsum(w) - 0.5 * w) / w.sum()
    return np.interp(taus, cw, v)


def hist_weights(k, hist_mask, W, hl):
    ranks = C.brank[hist_mask]
    age = k - ranks
    return np.where(age <= W, 0.5 ** (age / hl) if hl else 1.0, 0.0)


def pit_and_z(q, y, rng):
    u = cdf_grid(q, y + rng.uniform(-0.5, 0.5, len(y)))
    sc = np.maximum((q[:, 148] - q[:, 49]) / 1.349, 0.25)
    return u, (y - q[:, 99]) / sc, sc


def thr_calibrate(p, y_all, thr, ok, kind, prm):
    """Nested walk-forward threshold calibration. Returns calibrated p (NaN where unavailable)."""
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression

    out = p.copy()
    ev = (y_all[:, None] >= thr[None, :]).astype(float)
    for blk in C.blocks:
        k = blk["k"]
        cur = (C.brank == k) & ok
        if cur.sum() == 0:
            continue
        hist = (C.brank >= 0) & (C.brank < k) & (C.brank >= k - prm["W"]) & ok
        if hist.sum() < 500:
            continue
        w = hist_weights(k, hist, prm["W"], prm["hl"]) + 1e-12
        if kind == "platt":
            lp = np.log(
                np.clip(p[hist], PLOGMIN, 1 - PLOGMIN)
                / (1 - np.clip(p[hist], PLOGMIN, 1 - PLOGMIN))
            )
            ln = np.broadcast_to(np.log(thr)[None, :], lp.shape)
            Xh = np.stack([lp.ravel(), ln.ravel()], axis=1)
            lr = LogisticRegression(C=prm["C"], max_iter=200).fit(
                Xh, ev[hist].ravel().astype(int), sample_weight=np.repeat(w, len(thr))
            )
            lc = np.log(
                np.clip(p[cur], PLOGMIN, 1 - PLOGMIN) / (1 - np.clip(p[cur], PLOGMIN, 1 - PLOGMIN))
            )
            lnc = np.broadcast_to(np.log(thr)[None, :], lc.shape)
            out[cur] = lr.predict_proba(np.stack([lc.ravel(), lnc.ravel()], axis=1))[:, 1].reshape(
                lc.shape
            )
        else:
            for j in range(len(thr)):
                ph, eh = p[hist, j], ev[hist, j]
                o = np.argsort(ph)
                ph, eh, wh = ph[o], eh[o], w[o]
                grp = np.array_split(np.arange(len(ph)), max(1, len(ph) // prm["minbin"]))
                px = np.array([np.average(ph[g], weights=wh[g]) for g in grp])
                ey = np.array([np.average(eh[g], weights=wh[g]) for g in grp])
                wy = np.array([wh[g].sum() for g in grp])
                ir = IsotonicRegression(out_of_bounds="clip").fit(px, ey, sample_weight=wy)
                out[cur, j] = ir.predict(p[cur, j])
        out[cur] = np.minimum.accumulate(np.clip(out[cur], PLOGMIN, 1 - PLOGMIN), axis=1)
    return out


def dist_calibrate(q, p, y_all, ok, kind, prm, seed=SEED):
    rng = np.random.default_rng(seed)
    qq, pp = q.copy(), p.copy()
    mean = q.mean(axis=1)
    yv = y_all
    u = np.full(len(q), np.nan)
    z = np.full(len(q), np.nan)
    sc = np.full(len(q), np.nan)
    u[ok], z[ok], sc[ok] = pit_and_z(q[ok].astype(float), yv[ok], rng)
    for blk in C.blocks:
        k = blk["k"]
        cur = (C.brank == k) & ok
        if cur.sum() == 0:
            continue
        hist = (C.brank >= 0) & (C.brank < k) & (C.brank >= k - prm["W"]) & ok
        if hist.sum() < prm["minrows"]:
            continue
        w = hist_weights(k, hist, prm["W"], prm["hl"]) + 1e-12
        if kind == "conf":
            zq = wquantile(z[hist], w, TAUS199)
            nq = clean_q(q[cur, 99:100] + sc[cur, None] * zq[None, :])
            qq[cur] = nq
        else:
            tp = np.clip(wquantile(u[hist], w, TAUS199), 0, 1)
            pos = np.clip(tp * 199 - 0.5, 0, 198)
            lo = np.floor(pos).astype(int)
            hi = np.minimum(lo + 1, 198)
            fr = pos - lo
            qq[cur] = clean_q(q[cur][:, lo] * (1 - fr) + q[cur][:, hi] * fr)
        mean[cur] = qq[cur].mean(axis=1)
        if kind == "pit":
            o = np.argsort(u[hist])
            cw = (np.cumsum(w[o]) - 0.5 * w[o]) / w[o].sum()
            Fraw = 1.0 - p[cur]
            pp[cur] = np.clip(1.0 - np.interp(Fraw, u[hist][o], cw), PLOGMIN, 1 - PLOGMIN)
        else:
            pp[cur] = p_from_grid(qq[cur].astype(float), C.thr_cur)
    return qq, pp, mean


def make_variant(base, kind, prm):
    """Apply a calibrator to every stat of a result set."""
    out = new_res()
    for st in STATS:
        r, o = base[st], out[st]
        ok = ok_mask(base, st)
        y = C.y[st][C.t0i :]
        C.thr_cur = THR[st]
        if kind in ("platt", "iso"):
            o["q"], o["mean"] = r["q"], r["mean"]
            o["p"] = thr_calibrate(r["p"].astype(float), y, THR[st], ok, kind, prm).astype(
                np.float32
            )
        else:
            if np.isnan(r["q"][ok][:, 0]).any():
                return None
            q, p, m = dist_calibrate(r["q"].astype(float), r["p"].astype(float), y, ok, kind, prm)
            o["q"], o["p"], o["mean"] = (
                q.astype(np.float32),
                p.astype(np.float32),
                m.astype(np.float32),
            )
    return out


CAL_GRID = {
    "platt": [
        dict(W=w, hl=h, C=c) for w in (4, 9, 99) for h in (None, 4.0) for c in (0.1, 1.0, 10.0)
    ],
    "iso": [dict(W=w, hl=h, minbin=m) for w in (4, 9, 99) for h in (None, 4.0) for m in (50, 200)],
    "conf": [
        dict(W=w, hl=h, minrows=m) for w in (4, 9, 99) for h in (None, 4.0) for m in (200, 1000)
    ],
    "pit": [
        dict(W=w, hl=h, minrows=m) for w in (4, 9, 99) for h in (None, 4.0) for m in (200, 1000)
    ],
}


def tune_calibrators(best_res):
    """Tune each calibrator's settings on 2023 rows of the 2023-selected best arm."""
    chosen, trace = {}, {}
    sel = C.test_season == SELECT_SEASON
    for kind, grid in CAL_GRID.items():
        scores = []
        for prm in grid:
            v = make_variant(best_res, kind, prm)
            if v is None:
                scores.append(float("inf"))
                continue
            vals = []
            for st in STATS:
                m = sel & ok_mask(v, st)
                if m.sum() == 0:
                    continue
                y = C.y[st][C.t0i :][m]
                if kind in ("platt", "iso"):
                    vals.append(tll_rows(v[st]["p"][m].astype(float), y, THR[st]).mean())
                else:
                    vals.append(
                        crps_rows(v[st]["q"][m].astype(float), y).mean() / base_crps(st)[m].mean()
                    )
            scores.append(float(np.mean(vals)) if vals else float("inf"))
        j = int(np.argmin(scores))
        chosen[kind] = grid[j]
        trace[kind] = [dict(prm=g, score=s) for g, s in zip(grid, scores, strict=True)]
        log(f"calibrator {kind}: best {grid[j]} 2023 score {scores[j]:.4f}")
    return chosen, trace


# ------------------------------------------------------------------------------ reporting


def summarize(res, st, season, ref=None):
    """Per-arm/stat metrics on one season's rows (+ paired clustered deltas vs ``ref``)."""
    ok = ok_mask(res, st) & (C.test_season == season)
    if ref is not None:
        ok &= ok_mask(ref, st)
    y = C.y[st][C.t0i :][ok]
    gid = C.gid[C.t0i :][ok]
    out = dict(n=int(ok.sum()))
    if ok.sum() == 0:
        return out
    p = res[st]["p"][ok].astype(float)
    tl = tll_rows(p, y, THR[st])
    ev = y[:, None] >= THR[st][None, :]
    out.update(tll=float(tl.mean()), brier=float(((p - ev) ** 2).mean()), ece=ece_eq_mass(p, ev))
    has_q = not np.isnan(res[st]["q"][ok][:, 0]).any()
    if has_q:
        q = res[st]["q"][ok].astype(float)
        cr = crps_rows(q, y)
        cb = base_crps(st)[ok]
        mean = res[st]["mean"][ok].astype(float)
        u = cdf_grid(q, y + np.random.default_rng(SEED).uniform(-0.5, 0.5, len(y)))
        us = np.sort(u)
        ks = float(np.max(np.abs(us - (np.arange(1, len(us) + 1) - 0.5) / len(us))))
        out.update(
            crps=float(cr.mean()),
            crps_ratio_baseline=float(cr.mean() / cb.mean()),
            mae=float(np.abs(mean - y).mean()),
            bias=float((mean - y).mean()),
            cov80=float(((y >= q[:, 19]) & (y <= q[:, 179])).mean()),
            pit_ks=ks,
        )
        if ref is not None and not np.isnan(ref[st]["q"][ok][:, 0]).any():
            rq = ref[st]["q"][ok].astype(float)
            out["d_crps_vs_ref"] = boot_ci(cr - crps_rows(rq, y), gid)
    if ref is not None:
        out["d_tll_vs_ref"] = boot_ci(
            tl - tll_rows(ref[st]["p"][ok].astype(float), y, THR[st]), gid
        )
    return out


def per_block_crps(res):
    out = {}
    for st in STATS:
        ok = ok_mask(res, st) & ~np.isnan(res[st]["q"][:, 0])
        d = {}
        for blk in C.blocks:
            m = ok & (C.brank == blk["k"])
            if m.sum():
                d[str(blk["fold"])] = float(
                    crps_rows(res[st]["q"][m].astype(float), C.y[st][C.t0i :][m]).mean()
                )
        out[st] = d
    return out


def jsonable(o):
    """Recursively convert numpy scalars/arrays; non-finite floats become None."""
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    if isinstance(o, (np.floating, float)):
        return float(o) if math.isfinite(float(o)) else None
    if isinstance(o, (np.integer,)):
        return int(o)
    return o


def drift_table():
    df = C.df
    rows = {}
    prev = None
    for sea in sorted(df["season"].unique()):
        d = df[df["season"] == sea]
        r = dict(
            rows=int(len(d)),
            pace_proxy_game_total=float(d["game_total_feat"].mean()),
            three_pa_share_proxy=float((d["pr_zone_c3"] + d["pr_zone_a3"]).mean()),
            fta_per100_proxy=float(d["pr_fta_rate"].mean()),
            usage_proxy=float(d["pr_usage"].mean()),
        )
        for s in STATS:
            r[f"{s}_mean"], r[f"{s}_std"] = float(d[s].mean()), float(d[s].std())
            if prev is not None:
                from scipy.stats import ks_2samp

                r[f"{s}_shift_in_std_vs_prev"] = float(
                    (d[s].mean() - prev[s].mean()) / max(prev[s].std(), 1e-9)
                )
                r[f"{s}_ks_vs_prev"] = float(ks_2samp(d[s], prev[s]).statistic)
        rows[str(int(sea))] = r
        prev = d
    return rows


def oof_frame(variants, full_variants):
    df = C.df
    sel = C.test_season == REPORT_SEASON
    recs = []
    for name, res in variants.items():
        for st in STATS:
            m = sel & ok_mask(res, st)
            if m.sum() == 0:
                continue
            y = C.y[st][C.t0i :][m]
            has_q = not np.isnan(res[st]["q"][m][:, 0]).any()
            d = pd.DataFrame(
                {
                    "variant": name,
                    "stat": st,
                    "game_id": C.gid[C.t0i :][m],
                    "player_id": df["player_id"].to_numpy()[C.t0i :][m],
                    "fold_id": df["fold_id"].to_numpy()[C.t0i :][m],
                    "y": y.astype(np.float32),
                    "mean": res[st]["mean"][m].astype(np.float32),
                    "crps": (
                        crps_rows(res[st]["q"][m].astype(float), y).astype(np.float32)
                        if has_q
                        else np.float32("nan")
                    ),
                    "tll": tll_rows(res[st]["p"][m].astype(float), y, THR[st]).astype(np.float32),
                }
            )
            if name in full_variants:
                q = res[st]["q"][m].astype(float)
                if has_q:
                    d["q19"] = list(
                        q[:, [int(round(t * 199 - 0.5)) for t in TAUS19]].astype(np.float32)
                    )
                d["p_ge"] = list(res[st]["p"][m].astype(np.float32))
            recs.append(d)
    return pd.concat(recs, ignore_index=True)


# ------------------------------------------------------------------------------ main


def main():
    setup()
    load()
    results, timings, rec_imp = {}, {}, {}
    B = C.B
    run_arm(
        "v1_prod",
        lambda b: resid_block(b, "v1", "lgb", {}, B["prod_rounds"], False),
        results,
        timings,
    )
    for nm, kind in (("xgb_v1_prodcfg", "v1"), ("xgb_v12_prodcfg", "v12")):
        run_arm(
            nm,
            lambda b, kind=kind: resid_block(b, kind, "xgb", PROD_XGB, B["prod_rounds"], False),
            results,
            timings,
        )
    s1 = search("v1", "v1", timings)
    s12 = search("v12", "v12", timings)
    p1, p12 = s1["params"], s12["params"]
    run_arm(
        "xgb_v1_tuned",
        lambda b: resid_block(b, "v1", "xgb", p1, B["rounds"], True),
        results,
        timings,
    )
    run_arm(
        "xgb_v12_tuned",
        lambda b: resid_block(b, "v12", "xgb", p12, B["rounds"], True, rec_imp),
        results,
        timings,
    )
    imp23 = {}
    for st, lst in rec_imp.items():
        agg = {}
        for season, d in lst:
            if season == SELECT_SEASON:  # selection data only
                for k, v in d.items():
                    agg[k] = agg.get(k, 0.0) + v
        imp23[st] = [k for k, _ in sorted(agg.items(), key=lambda kv: -kv[1])]
    k_keep = 30 if not C.smoke else 15
    C.pruned = {st: imp23[st][:k_keep] for st in STATS}
    leak = leak_audit(p12, rec_imp)
    run_opt = functools.partial(run_arm, optional=True)
    run_opt(
        "xgb_v12_pruned",
        lambda b: resid_block(b, "pruned", "xgb", p12, B["rounds"], True),
        results,
        timings,
    )
    for nm, kind in (("xgb_v12_rel", "rel"), ("xgb_v12_both", "both")):
        run_opt(
            nm,
            lambda b, kind=kind: resid_block(b, kind, "xgb", p12, B["rounds"], True),
            results,
            timings,
        )
    run_opt("xgb_v12_quantile", lambda b: quantile_block(b, p12, B["rounds"]), results, timings)
    run_opt(
        "xgb_v12_poisson_nb", lambda b: count_block(b, "xgb", p12, B["rounds"]), results, timings
    )
    run_opt("glm_poisson_nb", lambda b: count_block(b, "glm", None, 0), results, timings)
    run_opt("logit_thr", logit_block, results, timings)
    run_opt("mlp_quantile", lambda b: mlp_block(b, None), results, timings)

    # ---- selection on 2023 ONLY
    dist_arms = [
        a for a in results if a not in ("logit_thr", "v1_prod")
    ]  # includes pruned/rel/both
    scores = {a: selection_score(results[a]) for a in results if a != "logit_thr"}
    ranked = sorted(dist_arms, key=lambda a: scores[a])
    best, top3 = ranked[0], ranked[:3]
    log(f"2023 selection: best={best} top3={top3}")
    blend = new_res()
    for st in STATS:
        for key in ("q", "mean", "p"):
            blend[st][key] = np.mean([results[a][st][key] for a in top3], axis=0)
    results["blend_top3"] = blend
    cal_params, cal_trace = tune_calibrators(results[best])

    # ---- variants (calibrators) for every arm; full detail only for the key ones
    variants = dict(results)
    for a in list(results):
        for kind in ("platt", "iso", "conf", "pit"):
            if kind in ("conf", "pit") and a == "logit_thr":
                continue
            v = make_variant(results[a], kind, cal_params[kind])
            if v is not None:
                variants[f"{a}|{kind}"] = v
    log("calibrator variants built")

    # ---- ablation on the report season blocks (descriptive; no decisions)
    ablation = {}
    ab_blocks = [b for b in C.blocks if b["season"] == REPORT_SEASON and b["ok"]][
        :: B["abl_stride"]
    ]
    for g in ("a", "b", "c", "d", "e", "f", "g", "h", "i", "j"):
        t = time.monotonic()
        res = new_res()
        for blk in ab_blocks:
            for st, pred in resid_block(blk, "v12", "xgb", p12, B["rounds"], True, drop=g).items():
                store(res, blk, st, pred)
        d = {}
        for st in STATS:
            m = ok_mask(res, st) & (C.test_season == REPORT_SEASON)
            full = results["xgb_v12_tuned"]
            y = C.y[st][C.t0i :][m]
            c_drop = crps_rows(res[st]["q"][m].astype(float), y)
            c_full = crps_rows(full[st]["q"][m].astype(float), y)
            d[st] = boot_ci(c_drop - c_full, C.gid[C.t0i :][m], 200)  # >0: group helps
        ablation[g] = d
        log(f"ablation drop {g}: {time.monotonic() - t:.0f}s")

    # ---- metrics
    ref = results["v1_prod"]
    metrics = dict(
        budget=C.budget_name,
        seeds=dict(seed=SEED, optuna=SEED, torch=SEED),
        device=C.device,
        select_season=SELECT_SEASON,
        report_season=REPORT_SEASON,
        selection_scores_2023=scores,
        best_arm_2023=best,
        top3_2023=top3,
        timings_s=timings,
        n_test_rows=int(C.nt),
    )
    metrics["leaderboard_2024"] = {
        a: {st: summarize(r, st, REPORT_SEASON, ref if a != "v1_prod" else None) for st in STATS}
        for a, r in results.items()
    }
    metrics["selection_2023"] = {
        a: {st: summarize(r, st, SELECT_SEASON) for st in STATS} for a, r in results.items()
    }
    metrics["calibration_2024"] = {
        v: {st: summarize(r, st, REPORT_SEASON, results[v.split("|")[0]]) for st in STATS}
        for v, r in variants.items()
        if "|" in v
    }
    metrics["blocks_crps"] = {a: per_block_crps(r) for a, r in results.items() if a != "logit_thr"}
    metrics["ablation_drop_group_2024"] = ablation
    metrics["blend_vs_best_single_2024"] = {
        st: {
            k: v
            for k, v in summarize(results["blend_top3"], st, REPORT_SEASON, results[best]).items()
            if k.startswith("d_")
        }
        for st in STATS
    }
    metrics["zero_inflation_fg3m_2024"] = {
        a: dict(
            obs_p0=float(1 - (C.y["fg3m"][C.t0i :][C.test_season == REPORT_SEASON] >= 1).mean()),
            pred_p0=float(
                1 - np.nanmean(results[a]["fg3m"]["p"][C.test_season == REPORT_SEASON][:, 0])
            ),
        )
        for a in ("xgb_v12_poisson_nb", "glm_poisson_nb", "xgb_v12_tuned")
        if a in results
    }
    metrics["normalization_2024"] = {
        a: {st: metrics["leaderboard_2024"][a][st].get("crps") for st in STATS}
        for a in ("xgb_v12_tuned", "xgb_v12_rel", "xgb_v12_both", "xgb_v12_pruned")
        if a in results
    }
    metrics["drift"] = drift_table()
    metrics["leak_audit"] = leak
    metrics["pruned_features"] = C.pruned
    out_root = Path(os.environ.get("NBA_ARTIFACT_ROOT", "artifacts"))
    out = out_root / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    full_v = {
        "v1_prod",
        best,
        "blend_top3",
        *[f"{best}|{k}" for k in ("platt", "iso", "conf", "pit")],
        *[f"v1_prod|{k}" for k in ("platt", "iso", "conf", "pit")],
    }
    keep_v = {v for v in variants if "|" not in v or v.split("|")[0] in ("v1_prod", best)}
    oof_frame({v: variants[v] for v in keep_v}, full_v).to_parquet(
        out / "oof_2024.parquet", index=False
    )
    imp = {}
    for st, lst in rec_imp.items():
        keys = lst[0][1].keys()
        imp[st] = dict(
            sorted(
                {k: float(np.mean([d[k] for _, d in lst])) for k in keys}.items(),
                key=lambda kv: -kv[1],
            )[:40]
        )
    (out / "feature_importance.json").write_text(json.dumps(imp, indent=1))
    (out / "best_config.json").write_text(
        json.dumps(
            dict(
                search_v1=s1,
                search_v12=s12,
                best_arm_2023=best,
                top3_2023=top3,
                calibrators=cal_params,
                calibrator_trace=cal_trace,
                budget=C.budget_name,
                budget_params=B,
                seed=SEED,
                thresholds={k: v.tolist() for k, v in THR.items()},
            ),
            indent=1,
            default=float,
        )
    )
    (out / "metrics.json").write_text(json.dumps(jsonable(metrics), indent=1))
    log(f"ARTIFACTS WRITTEN TO {out}")
    return metrics


def arm_block_fn(name, p1, p12):
    """Block function for an arm by name, using already-chosen configs (completion mode)."""
    B = C.B
    table = {
        "v1_prod": lambda b: resid_block(b, "v1", "lgb", {}, B["prod_rounds"], False),
        "xgb_v1_prodcfg": lambda b: resid_block(b, "v1", "xgb", PROD_XGB, B["prod_rounds"], False),
        "xgb_v12_prodcfg": lambda b: resid_block(
            b, "v12", "xgb", PROD_XGB, B["prod_rounds"], False
        ),
        "xgb_v1_tuned": lambda b: resid_block(b, "v1", "xgb", p1, B["rounds"], True),
        "xgb_v12_tuned": lambda b: resid_block(b, "v12", "xgb", p12, B["rounds"], True),
        "xgb_v12_rel": lambda b: resid_block(b, "rel", "xgb", p12, B["rounds"], True),
        "xgb_v12_both": lambda b: resid_block(b, "both", "xgb", p12, B["rounds"], True),
        "xgb_v12_quantile": lambda b: quantile_block(b, p12, B["rounds"]),
        "xgb_v12_poisson_nb": lambda b: count_block(b, "xgb", p12, B["rounds"]),
        "glm_poisson_nb": lambda b: count_block(b, "glm", None, 0),
        "logit_thr": logit_block,
        "mlp_quantile": lambda b: mlp_block(b, None),
    }
    if name not in table:
        raise ValueError(
            f"completion mode cannot refit arm {name!r} (needs state from the first run)"
        )
    return table[name]


def main_complete(plan):
    """NBA_MODE=complete: NO search and NO selection. Refit only the listed arms with the configs the
    first run chose on 2023, on the 2024 walk-forward blocks (plus 2023 blocks of the candidate as
    nested-calibrator history), then calibrators with the first run's settings and the fixed
    top-3 blend. Same artifact formats as the sweep."""
    setup()
    load()
    cfg = plan["best_config"]
    p1, p12 = cfg["search_v1"]["params"], cfg["search_v12"]["params"]
    cand, top3, cal_params = cfg["best_arm_2023"], cfg["top3_2023"], cfg["calibrators"]
    arms = list(plan["arms"])
    want = [cand]
    if "blend_top3" in arms:
        want += [a for a in top3 if a not in want]
    want += [a for a in arms if a != "blend_top3" and a not in want]
    if "v1_prod" not in want:
        want.append("v1_prod")  # the pre-registered reference must share rows
    log(
        f"COMPLETE mode: candidate={cand} (chosen on 2023 in {plan['prev_run']}), fixed top3={top3}"
    )
    results, timings = {}, {}
    for a in want:
        run_arm(a, arm_block_fn(a, p1, p12), results, timings, seasons={REPORT_SEASON})
    missing = [a for a in want if a not in results]
    if missing:
        raise RuntimeError(f"completion arms did not finish: {missing}")
    if "blend_top3" in arms:
        blend = new_res()
        for st in STATS:
            for key in ("q", "mean", "p"):
                blend[st][key] = np.mean([results[a][st][key] for a in top3], axis=0)
        results["blend_top3"] = blend
    variants = dict(results)
    cal_done = False
    if os.environ.get("NBA_COMPLETE_CAL", "1") == "1" and cand in results:
        run_arm(
            cand,
            arm_block_fn(cand, p1, p12),
            {},
            timings,
            seasons={SELECT_SEASON},
            into=results[cand],
        )  # fills the 2023 rows (nested-calibrator history only)
        for kind in ("platt", "iso", "conf", "pit"):
            v = make_variant(results[cand], kind, cal_params[kind])
            if v is not None:
                variants[f"{cand}|{kind}"] = v
        cal_done = True
        log("calibrator variants built on the candidate (settings from the first run)")
    ref = results["v1_prod"]
    metrics = dict(
        mode="complete", budget=C.budget_name, seeds=dict(seed=SEED), device=C.device,
        select_season=SELECT_SEASON, report_season=REPORT_SEASON, best_arm_2023=cand,
        top3_2023=top3, selection_scores_2023=plan.get("selection_scores_2023"),
        leak_audit=plan.get("leak_audit"), timings_s=timings, n_test_rows=int(C.nt),
        completion=dict(prev_run=plan["prev_run"], arms=arms, calibrators_run=cal_done,
                        note="no search/selection in this run; configs and arm choice fixed on 2023"),
    )  # fmt: skip
    metrics["leaderboard_2024"] = {
        a: {st: summarize(r, st, REPORT_SEASON, ref if a != "v1_prod" else None) for st in STATS}
        for a, r in results.items()
    }
    metrics["calibration_2024"] = {
        v: {st: summarize(r, st, REPORT_SEASON, results[v.split("|")[0]]) for st in STATS}
        for v, r in variants.items() if "|" in v
    }  # fmt: skip
    if "blend_top3" in results:
        metrics["blend_vs_best_single_2024"] = {
            st: {k: v for k, v in summarize(results["blend_top3"], st, REPORT_SEASON, results[cand]).items()
                 if k.startswith("d_")}
            for st in STATS
        }  # fmt: skip
    out = Path(os.environ.get("NBA_ARTIFACT_ROOT", "artifacts")) / datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )
    out.mkdir(parents=True, exist_ok=True)
    full_v = {
        "v1_prod",
        cand,
        "blend_top3",
        *[f"{cand}|{k}" for k in ("platt", "iso", "conf", "pit")],
    }
    oof_frame(variants, full_v).to_parquet(out / "oof_2024.parquet", index=False)
    (out / "best_config.json").write_text(
        json.dumps(jsonable({**cfg, "completion_of": plan["prev_run"]}), indent=1)
    )
    (out / "feature_importance.json").write_text("{}")
    (out / "metrics.json").write_text(json.dumps(jsonable(metrics), indent=1))
    log(f"ARTIFACTS WRITTEN TO {out}")
    return metrics


def entry():
    """Run the sweep, or the completion step if ``completion_prev.json`` was staged / NBA_MODE=complete."""
    plan_path = Path(os.environ["NBA_PARQUET"]).parent / "completion_prev.json"
    plan = json.loads(plan_path.read_text()) if plan_path.exists() else {}
    if plan.get("mode") == "complete" or os.environ.get("NBA_MODE") == "complete":
        if not plan:
            raise FileNotFoundError("NBA_MODE=complete needs the staged completion_prev.json")
        os.environ["NBA_MODE"] = "complete"
        return main_complete(plan)
    return main()


if __name__ == "__main__":
    entry()
