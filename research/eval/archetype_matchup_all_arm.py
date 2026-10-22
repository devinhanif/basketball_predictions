# ruff: noqa: E501
"""ARCHETYPE_MATCHUP_ALL arm M, the shuffled refits, the knockouts and the identity-only arm (rule: docs/prereg/ARCHETYPE_MATCHUP_ALL.md, DRAFT).

Pure array code: it reads no database, no file and no season; outcomes (``y``) and A0's grid arrive as ARGUMENTS and this module is only ever
called on real outcomes by ``archetype_matchup_all_eval.run`` behind the frozen rule and the harness pin. It is exercised here only on
synthetic and planted data.

Arm M, per stat (every choice below is fixed in ``archetype_matchup_all_rule`` and written in the rule, Amendment 2, M3):
  * A0 = the stored production forecast grid ``q0`` (199 quantiles, continuous, from ``preseason_role_prior.quantile_grid``); the score of
    every arm is the integer-support CRPS of ``ceil(q - 0.5)`` (``crps_int_q``), the same function for A0 and every arm.
  * A0's mean = the mean of the 199 continuous grid points, row by row. Rescaling the grid by lambda moves exactly this mean to mean + prediction.
  * Target = actual minus A0's mean. Months are calendar months of ``game_date`` (``YYYY-MM``). A row of month m is predicted by a layer fitted on
    ALL pool rows of strictly earlier months (the 2023 pool is 2023's earlier months; the 2024 pool is 2023 plus 2024's earlier months). The first
    month of the first season has no earlier row: lambda = 1, the row is scored as A0 (never dropped). A month with fewer than MIN_TRAIN_ROWS
    earlier rows is served as A0 too.
  * Layer: LightGBM regression (L2), 4 leaves, 150 trees, learning rate 0.05, min_data_in_leaf 20, seed 0, ``boost_from_average`` True (the
    initial score is the training mean of the target: a global recalibration is part of arm M, which is why the shuffled control and the
    ``all_features`` knockout exist), ``off_arch`` passed as a LightGBM CATEGORICAL feature, every other feature numeric; single thread,
    deterministic, no bagging, no feature sub-sampling. Features that are constant on the training rows are dropped; with none left the
    prediction is the training mean.
  * lambda = (mean + prediction) / mean, clipped to [0.5, 2]; rows with mean < MEAN_FLOOR get lambda = 1.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from research.archive.preseason_role_prior import preseason_role_prior as arch
from research.eval import archetype_matchup_all_rule as rule

LGBM_PARAMS: dict[str, Any] = {
    "objective": "regression",
    "metric": "None",
    "num_leaves": rule.N_LEAVES,
    "learning_rate": rule.LEARNING_RATE,
    "min_data_in_leaf": rule.MIN_DATA_IN_LEAF,
    "seed": rule.LGBM_SEED,
    "boost_from_average": rule.BOOST_FROM_AVERAGE,
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 1,
    "bagging_fraction": 1.0,
    "feature_fraction": 1.0,
    "max_depth": -1,
    "lambda_l2": 0.0,
    "min_gain_to_split": 0.0,
    "min_sum_hessian_in_leaf": 1e-3,
    "max_bin": 255,
    "min_data_in_bin": 3,
    "cat_smooth": 10.0,
    "cat_l2": 10.0,
    "min_data_per_group": 100,
    "max_cat_to_onehot": 4,
    "max_cat_threshold": 32,
    "verbosity": -1,
}


def a0_mean(q0: np.ndarray) -> np.ndarray:
    """A0's mean per row: the mean of the 199 continuous grid points."""
    return np.asarray(q0, float).mean(axis=1)


def month_key(game_date: Any) -> np.ndarray:
    return np.asarray(pd.to_datetime(game_date).strftime("%Y-%m").to_numpy())


def lam_from(mean: np.ndarray, pred: np.ndarray) -> np.ndarray:
    """lambda = (mean + prediction) / mean clipped to LAMBDA_CLIP; exactly 1 where the A0 mean is below MEAN_FLOOR."""
    mean = np.asarray(mean, float)
    ok = mean >= rule.MEAN_FLOOR
    lam = np.ones_like(mean)
    lam[ok] = np.clip((mean[ok] + np.asarray(pred, float)[ok]) / mean[ok], *rule.LAMBDA_CLIP)
    return lam


def fit_predict(
    x_tr: pd.DataFrame, resid_tr: np.ndarray, x_te: pd.DataFrame, cols: Sequence[str]
) -> np.ndarray:
    """The layer. Constant-on-train features are dropped; no feature left -> the training mean (the pure recalibration)."""
    use = [c for c in cols if x_tr[c].nunique() > 1]
    if not use:
        return np.full(len(x_te), float(np.mean(resid_tr)))
    cat = [i for i, c in enumerate(use) if c in rule.CAT_COLS]
    ds = lgb.Dataset(
        x_tr[use].to_numpy(float),
        label=np.asarray(resid_tr, float),
        categorical_feature=cat,
        params={
            "verbosity": -1,
            "max_bin": 255,
            "min_data_in_bin": 3,
            "min_data_in_leaf": rule.MIN_DATA_IN_LEAF,
        },
        free_raw_data=True,
    )
    booster = lgb.train(LGBM_PARAMS, ds, num_boost_round=rule.N_TREES)
    return np.asarray(booster.predict(x_te[use].to_numpy(float)), float)


def walk_forward_lambda(
    x: pd.DataFrame,
    resid: np.ndarray,
    mean: np.ndarray,
    month: np.ndarray,
    is_score: np.ndarray,
    cols: Sequence[str],
    log: Any = None,
) -> np.ndarray:
    """lambda for the ``is_score`` rows (in pool order). Each month is predicted by a layer fitted on pool rows of STRICTLY earlier months."""
    month = np.asarray(month)
    score_idx = np.flatnonzero(is_score)
    lam = np.ones(len(score_idx))
    pos = {int(i): k for k, i in enumerate(score_idx)}
    for m in sorted(set(month[score_idx])):
        tr = np.flatnonzero(month < m)
        te = np.flatnonzero(is_score & (month == m))
        if len(tr) < rule.MIN_TRAIN_ROWS:
            continue  # served as A0
        pred = fit_predict(x.iloc[tr], resid[tr], x.iloc[te], cols)
        lm = lam_from(mean[te], pred)
        for i, v in zip(te, lm, strict=True):
            lam[pos[int(i)]] = v
        if log is not None:
            log(f"  month {m}: trained on {len(tr)} rows, scored {len(te)}")
    return lam


def crps_rows(q: np.ndarray, y: np.ndarray, chunk: int = 4000) -> np.ndarray:
    """Per-row integer-support CRPS (``preseason_role_prior.crps_int_q``), chunked to bound memory; identical values to the unchunked call."""
    out = np.empty(len(q))
    for s in range(0, len(q), chunk):
        out[s : s + chunk] = arch.crps_int_q(q[s : s + chunk], y[s : s + chunk])
    return out


def arm_delta(q0: np.ndarray, y: np.ndarray, lam: np.ndarray, base: np.ndarray) -> np.ndarray:
    """Per-row (arm - A0) integer CRPS. Rows with lambda exactly 1 are the A0 grid itself and score exactly 0 (not recomputed)."""
    d = np.zeros(len(q0))
    sel = np.flatnonzero(lam != 1.0)
    if len(sel):
        d[sel] = crps_rows(q0[sel] * lam[sel, None], y[sel]) - base[sel]
    return d


@dataclass
class StatPool:
    """One stat's pool, stacked in season order. ``q0`` holds ONLY the scored rows (``is_score``), in pool order; everything else is pool-wide."""

    stat: str
    q0: np.ndarray
    y: np.ndarray
    mean: np.ndarray
    month: np.ndarray
    is_score: np.ndarray
    x: pd.DataFrame
    x_shuffled: dict[int, pd.DataFrame]


def arm_columns() -> dict[str, tuple[str, ...]]:
    """Feature columns of every arm refit on the real features (shuffled refits use the same six on the shuffled frames)."""
    cols: dict[str, tuple[str, ...]] = {"M": tuple(rule.FEATURES)}
    for name, drop in rule.KNOCKOUTS.items():
        cols[f"ko_{name}"] = tuple(c for c in rule.FEATURES if c not in drop)
    cols["identity_only"] = tuple(rule.IDENTITY_ONLY_COLS)
    return cols


def run_arms(pool: StatPool, log: Any = None) -> dict[str, np.ndarray]:
    """Per-row (arm - A0) CRPS deltas on the scored rows for M, every knockout, the identity-only arm and the shuffled refits."""
    resid = pool.y - pool.mean
    base = crps_rows(pool.q0, pool.y[pool.is_score])
    y_s = pool.y[pool.is_score]
    out: dict[str, np.ndarray] = {}
    for name, cols in arm_columns().items():
        if log is not None:
            log(f"{pool.stat} arm {name}")
        lam = walk_forward_lambda(pool.x, resid, pool.mean, pool.month, pool.is_score, cols)
        out[name] = arm_delta(pool.q0, y_s, lam, base)
    for s, xs in pool.x_shuffled.items():
        if log is not None:
            log(f"{pool.stat} arm shuffle{s}")
        lam = walk_forward_lambda(
            xs, resid, pool.mean, pool.month, pool.is_score, tuple(rule.FEATURES)
        )
        out[f"shuffle{s}"] = arm_delta(pool.q0, y_s, lam, base)
    return out
