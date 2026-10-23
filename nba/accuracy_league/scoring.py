# ruff: noqa: E501
"""Pure scoring helpers: Brier, log loss, reliability bins, date-clustered paired bootstrap."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from nba.forward.scorecard import NAN, cluster_ci, mean_fn

#: probabilities are clipped to [EPS, 1 - EPS] before the log (a model count can be exactly 0 or 1)
EPS = 1e-3
N_BINS = 10


def brier(p: float, y: float) -> float:
    return (p - y) ** 2


def logloss(p: float, y: float) -> float:
    q = min(max(p, EPS), 1.0 - EPS)
    return -(y * math.log(q) + (1.0 - y) * math.log(1.0 - q))


def clustered_mean(dates: Sequence[str], vals: Sequence[float]) -> dict[str, Any]:
    """Mean of ``vals`` with a 95% bootstrap CI that resamples whole dates (cluster = slate date)."""
    cols = np.array([[v, 1.0] for v in vals], dtype=float)
    pt, lo, hi = cluster_ci(list(dates), cols, mean_fn)
    return {"value": pt, "lo": lo, "hi": hi, "n": len(vals), "dates": len(set(dates))}


def reliability(ps: Sequence[float], ys: Sequence[float]) -> list[dict[str, Any]]:
    """Ten equal-width bins on P(stat >= k): n, mean forecast, observed frequency."""
    out = []
    for b in range(N_BINS):
        lo, hi = b / N_BINS, (b + 1) / N_BINS
        idx = [i for i, p in enumerate(ps) if (lo <= p < hi) or (b == N_BINS - 1 and p == 1.0)]
        out.append({
            "lo": lo, "hi": hi, "n": len(idx),
            "mean_p": float(np.mean([ps[i] for i in idx])) if idx else NAN,
            "obs": float(np.mean([ys[i] for i in idx])) if idx else NAN,
        })  # fmt: skip
    return out
