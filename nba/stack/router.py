"""Learned router: per-row candidate weights from context, walk-forward.

Two gates map context features to a weight vector over ``k`` candidates:

* :class:`SoftmaxGate` -- linear logits, softmax weights, trained directly to
  minimise the pooled score (grid CRPS for props, log loss on the logit pool
  for win) with analytic gradients and L-BFGS.
* :class:`LGBMGate` -- LightGBM multiclass classifier of the argmin-score
  candidate; class probabilities (floored toward uniform) are the weights.

Walk-forward (:func:`walk_forward`): expanding window over date blocks. A gate
scoring block ``B`` is fit ONLY on rows with ``game_date < start(B)``; for
``win`` an optional Platt/isotonic calibrator is fit on the most recent
``calib_days`` of that training window, with the gate fit on the earlier part.
Season >= 2025 is rejected.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from nba.stack import FROZEN_SEASON
from nba.stack.data import StackData
from nba.stack.oof import TAUS
from nba.stack.scoring import logit, pool, sigmoid


class ContextEncoder:
    """Numeric passthrough (median impute, standardise) + one-hot for categoricals."""

    def __init__(self) -> None:
        self.num: list[str] = []
        self.cat: dict[str, list[str]] = {}
        self.med: np.ndarray = np.zeros(0)
        self.mu: np.ndarray = np.zeros(0)
        self.sd: np.ndarray = np.ones(0)

    def _raw(self, df: pd.DataFrame) -> np.ndarray:
        cols: list[np.ndarray] = [df[c].to_numpy(dtype=float) for c in self.num]
        for c, cats in self.cat.items():
            s = df[c].astype(str)
            cols.extend((s == v).to_numpy(dtype=float) for v in cats)
        return np.column_stack(cols) if cols else np.zeros((len(df), 0))

    def fit(self, df: pd.DataFrame) -> ContextEncoder:
        for c in df.columns:
            if pd.api.types.is_bool_dtype(df[c]) or pd.api.types.is_numeric_dtype(df[c]):
                self.num.append(c)
            else:
                self.cat[c] = sorted(df[c].astype(str).unique().tolist())
        # medians for the numeric block only (one-hot never NaN)
        x = self._raw(df)
        with np.errstate(all="ignore"):
            med = np.nanmedian(x, axis=0) if x.size else np.zeros(x.shape[1])
        self.med = np.where(np.isfinite(med), med, 0.0)
        xi = np.where(np.isnan(x), self.med, x)
        self.mu = xi.mean(axis=0) if xi.size else np.zeros(xi.shape[1])
        sd = xi.std(axis=0) if xi.size else np.ones(xi.shape[1])
        self.sd = np.where(sd > 1e-9, sd, 1.0)
        return self

    def transform(self, df: pd.DataFrame) -> np.ndarray:
        x = self._raw(df)
        x = np.where(np.isnan(x), self.med, x)
        return np.asarray((x - self.mu) / self.sd)


class Gate(Protocol):
    def fit(self, x: np.ndarray, data: StackData) -> None: ...
    def weights(self, x: np.ndarray) -> np.ndarray: ...


def _softmax(a: np.ndarray) -> np.ndarray:
    a = a - a.max(axis=1, keepdims=True)
    e = np.exp(a)
    return np.asarray(e / e.sum(axis=1, keepdims=True))


@dataclass
class SoftmaxGate:
    l2: float = 1e-2
    maxiter: int = 150
    coef: np.ndarray | None = field(default=None, repr=False)

    def _loss_grad(
        self, theta: np.ndarray, xa: np.ndarray, data: StackData, k: int
    ) -> tuple[float, np.ndarray]:
        n = xa.shape[0]
        wmat = theta.reshape(xa.shape[1], k)
        w = _softmax(xa @ wmat)
        if data.target == "win":
            lg = logit(data.cand)  # (n, k)
            z = (w * lg).sum(axis=1)
            p = np.clip(sigmoid(z), 1e-6, 1 - 1e-6)
            loss = float(-(data.y * np.log(p) + (1 - data.y) * np.log(1 - p)).mean())
            dw = ((p - data.y) / n)[:, None] * lg
        else:
            qp = np.einsum("nk,nkq->nq", w, data.cand)
            d = data.y[:, None] - qp
            pin = np.where(d >= 0, TAUS[None, :] * d, (TAUS[None, :] - 1.0) * d)
            loss = float(2.0 * pin.mean(axis=1).mean())
            gq = (d < 0).astype(float) - TAUS[None, :]  # d pinball / d q
            dw = (2.0 / (len(TAUS) * n)) * np.einsum("nq,nkq->nk", gq, data.cand)
        dlogit = w * (dw - (w * dw).sum(axis=1, keepdims=True))
        grad = xa.T @ dlogit
        pen = self.l2 * float((wmat[1:] ** 2).sum())
        grad[1:] += 2.0 * self.l2 * wmat[1:]
        return loss + pen, grad.ravel()

    def fit(self, x: np.ndarray, data: StackData) -> None:
        k = len(data.candidates)
        xa = np.column_stack([np.ones(len(x)), x])
        theta0 = np.zeros(xa.shape[1] * k)
        res = minimize(
            self._loss_grad,
            theta0,
            args=(xa, data, k),
            jac=True,
            method="L-BFGS-B",
            options={"maxiter": self.maxiter},
        )
        self.coef = res.x.reshape(xa.shape[1], k)

    def weights(self, x: np.ndarray) -> np.ndarray:
        if self.coef is None:
            raise RuntimeError("gate not fit")
        return _softmax(np.column_stack([np.ones(len(x)), x]) @ self.coef)


@dataclass
class LGBMGate:
    n_estimators: int = 80
    learning_rate: float = 0.05
    num_leaves: int = 8
    min_child_samples: int = 100
    floor: float = 0.05  # blend toward uniform: w = (1-floor)*proba + floor/k
    seed: int = 0
    _model: Any = field(default=None, repr=False)
    _classes: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int), repr=False)
    _k: int = 0

    def fit(self, x: np.ndarray, data: StackData) -> None:
        import lightgbm as lgb

        self._k = len(data.candidates)
        best = data.cand_scores().argmin(axis=1)
        self._classes = np.unique(best)
        if len(self._classes) < 2:
            self._model = None
            return
        self._model = lgb.LGBMClassifier(
            n_estimators=self.n_estimators,
            learning_rate=self.learning_rate,
            num_leaves=self.num_leaves,
            min_child_samples=self.min_child_samples,
            random_state=self.seed,
            n_jobs=1,
            verbose=-1,
        )
        self._model.fit(x, best)

    def weights(self, x: np.ndarray) -> np.ndarray:
        w = np.zeros((len(x), self._k))
        if self._model is None:
            w[:, self._classes[0] if len(self._classes) else 0] = 1.0
        else:
            w[:, self._classes] = self._model.predict_proba(x)
        return np.asarray((1.0 - self.floor) * w + self.floor / self._k)


@dataclass(frozen=True)
class WalkForwardConfig:
    block_days: int = 30
    min_train_rows: int = 2000
    calibration: str = "none"  # 'none' | 'platt' | 'isotonic'  (win only)
    calib_days: int = 60
    min_calib_rows: int = 300
    max_season: int = FROZEN_SEASON - 1


@dataclass
class RouterResult:
    name: str
    scored: np.ndarray  # (n,) bool
    weights: np.ndarray  # (n, k), NaN where not scored
    pooled: np.ndarray  # (n,) or (n, 19), NaN where not scored
    blocks: list[dict[str, Any]]


def _guard(data: StackData, cfg: WalkForwardConfig) -> None:
    if cfg.max_season >= FROZEN_SEASON:
        raise ValueError(f"max_season must be < {FROZEN_SEASON} (frozen holdout)")
    if (data.keys["season"] > cfg.max_season).any():
        raise ValueError(f"data contains season > {cfg.max_season}; frozen season is never used")


def iter_blocks(
    dates: np.ndarray, cfg: WalkForwardConfig
) -> Iterator[tuple[np.ndarray, np.ndarray, np.datetime64]]:
    """Yield (block_mask, train_mask, block_start): train = strictly earlier dates."""
    d0 = dates.min()
    step = np.timedelta64(cfg.block_days, "D")
    start = d0
    last = dates.max()
    while start <= last:
        end = start + step
        block = (dates >= start) & (dates < end)
        train = dates < start
        if block.any() and train.sum() >= cfg.min_train_rows:
            yield block, train, start
        start = end


def _empty(data: StackData) -> tuple[np.ndarray, np.ndarray]:
    k = len(data.candidates)
    shape = (data.n,) if data.target == "win" else (data.n, data.cand.shape[2])
    return np.full((data.n, k), np.nan), np.full(shape, np.nan)


def walk_forward(
    data: StackData,
    gate_factory: Callable[[], Gate],
    cfg: WalkForwardConfig | None = None,
    name: str = "router",
) -> RouterResult:
    cfg = cfg or WalkForwardConfig()
    _guard(data, cfg)
    dates = data.dates
    weights, pooled = _empty(data)
    scored = np.zeros(data.n, dtype=bool)
    blocks: list[dict[str, Any]] = []
    for block, train, start in iter_blocks(dates, cfg):
        fit_m = train
        cal_m: np.ndarray | None = None
        if cfg.calibration != "none" and data.target == "win":
            cut = start - np.timedelta64(cfg.calib_days, "D")
            f, c = train & (dates < cut), train & (dates >= cut)
            if f.sum() >= cfg.min_train_rows and c.sum() >= cfg.min_calib_rows:
                fit_m, cal_m = f, c
        if dates[fit_m].max() >= start:
            raise AssertionError("leakage: training rows not strictly before block start")
        enc = ContextEncoder().fit(data.context[fit_m])
        gate = gate_factory()
        gate.fit(enc.transform(data.context[fit_m]), data.subset(fit_m))
        w = gate.weights(enc.transform(data.context[block]))
        p = pool(data.target, w, data.cand[block])
        if cal_m is not None:
            wc = gate.weights(enc.transform(data.context[cal_m]))
            pc = pool(data.target, wc, data.cand[cal_m])
            p = _calibrate(cfg.calibration, pc, data.y[cal_m], p)
        weights[block], pooled[block], scored[block] = w, p, True
        blocks.append(
            {
                "start": str(start),
                "n_block": int(block.sum()),
                "n_fit": int(fit_m.sum()),
                "train_max_date": str(dates[fit_m].max()),
                "calibrated": cal_m is not None,
            }
        )
    if not scored.any():
        raise ValueError("no block had enough training rows; lower min_train_rows")
    return RouterResult(name, scored, weights, pooled, blocks)


def _calibrate(kind: str, p_cal: np.ndarray, y_cal: np.ndarray, p_new: np.ndarray) -> np.ndarray:
    if kind == "platt":
        lr = LogisticRegression(C=1e6, max_iter=200)
        lr.fit(logit(p_cal).reshape(-1, 1), y_cal.astype(int))
        return np.asarray(lr.predict_proba(logit(p_new).reshape(-1, 1))[:, 1])
    if kind == "isotonic":
        iso = IsotonicRegression(y_min=1e-4, y_max=1 - 1e-4, out_of_bounds="clip")
        iso.fit(p_cal, y_cal)
        return np.asarray(iso.predict(p_new))
    raise ValueError(f"unknown calibration {kind!r}")


def hard_bucket_walk_forward(
    data: StackData,
    bucket: np.ndarray,
    cfg: WalkForwardConfig | None = None,
    min_bucket_n: int = 30,
    name: str = "hard_route",
) -> RouterResult:
    """Existing-style hard router: per bucket, the candidate with the lowest
    mean TRAIN score (earlier blocks only) gets weight 1."""
    cfg = cfg or WalkForwardConfig()
    _guard(data, cfg)
    bucket = np.asarray(bucket).astype(str)
    sc = data.cand_scores()
    k = len(data.candidates)
    weights, pooled = _empty(data)
    scored = np.zeros(data.n, dtype=bool)
    blocks: list[dict[str, Any]] = []
    for block, train, start in iter_blocks(data.dates, cfg):
        overall = int(sc[train].mean(axis=0).argmin())
        pick = {}
        for b in np.unique(bucket[train]):
            m = train & (bucket == b)
            pick[b] = int(sc[m].mean(axis=0).argmin()) if m.sum() >= min_bucket_n else overall
        w = np.zeros((int(block.sum()), k))
        for i, b in enumerate(bucket[block]):
            w[i, pick.get(b, overall)] = 1.0
        weights[block] = w
        pooled[block] = pool(data.target, w, data.cand[block])
        scored[block] = True
        blocks.append(
            {
                "start": str(start),
                "n_block": int(block.sum()),
                "n_fit": int(train.sum()),
                "train_max_date": str(data.dates[train].max()),
                "calibrated": False,
            }
        )
    return RouterResult(name, scored, weights, pooled, blocks)


def equal_pool(data: StackData) -> np.ndarray:
    k = len(data.candidates)
    return pool(data.target, np.full((data.n, k), 1.0 / k), data.cand)
