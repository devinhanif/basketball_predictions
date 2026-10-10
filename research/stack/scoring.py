"""Per-row scores and pooling for the two target kinds.

Props: grid CRPS = 2 * mean over the 19 levels of the pinball loss (a coarse
quadrature of CRPS; identical for every candidate so comparisons are fair).
Win: log loss.
"""

from __future__ import annotations

import numpy as np

from research.stack.oof import TAUS

_EPS = 1e-6


def is_binary(target: str) -> bool:
    """``win`` and threshold-event targets (``thr_<stat>``) are binary probabilities."""
    return target == "win" or target.startswith("thr_")


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), _EPS, 1 - _EPS)
    return np.asarray(np.log(p / (1 - p)))


def sigmoid(z: np.ndarray) -> np.ndarray:
    return np.asarray(1.0 / (1.0 + np.exp(-np.clip(z, -30, 30))))


def grid_crps(q: np.ndarray, y: np.ndarray) -> np.ndarray:
    """(n, 19) quantile grid, (n,) outcome -> (n,) CRPS approximation."""
    d = y[:, None] - q
    pin = np.where(d >= 0, TAUS[None, :] * d, (TAUS[None, :] - 1.0) * d)
    return np.asarray(2.0 * pin.mean(axis=1))


def row_log_loss(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), _EPS, 1 - _EPS)
    return np.asarray(-(y * np.log(p) + (1 - y) * np.log(1 - p)))


def score_rows(target: str, pred: np.ndarray, y: np.ndarray) -> np.ndarray:
    return row_log_loss(pred, y) if is_binary(target) else grid_crps(pred, y)


def pool(target: str, w: np.ndarray, cand: np.ndarray) -> np.ndarray:
    """Weights (n, k) x candidates -> pooled prediction.

    Props: weighted average of quantile grids (Vincentization; keeps each grid
    monotone). Win: weighted average on the logit scale.
    """
    if is_binary(target):
        return sigmoid((w * logit(cand)).sum(axis=1))
    return np.asarray(np.einsum("nk,nkq->nq", w, cand))
