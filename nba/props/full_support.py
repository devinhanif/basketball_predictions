"""Compact full-support survival function ``P(Y >= k)``, k = 1..K, for integer-support scoring.

Forward prop rows store only a handful of thresholds (``nba.props.config.THRESHOLDS``) and a
19-point quantile grid, which cannot reproduce the integer-support CRPS (RPS) pre-registered in
docs/FORWARD_PREREG_2026_27.md. This module adds one ADDITIVE JSON field, ``p_ge_full``::

    {"n": <denominator>, "c": [c_1, ..., c_K]}      # P(Y >= k) = c_k / n

* sample-based rows (context-residual quantile samples): ``n`` = number of quantile samples
  (199) and ``c_k = #(q > k - 0.5)`` exactly, the same convention as the stored ``p_ge``;
* parametric rows (recency distributions): ``n = 10000``, ``c_k = round(P(Y >= k) * n)``.

The list is cut after the first ``k`` with ``P(Y >= k) <= TAIL_EPS`` (99.9% of the mass is covered
and the final entry proves it), capped at ``KMAX[stat]``. ``P(Y >= 0) = 1`` is implicit and
``P(Y >= k) = 0`` beyond ``K``. Nothing here touches any existing field.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import numpy as np

#: Largest threshold stored per stat (suggested by the pre-registration, section 8.1).
KMAX: dict[str, int] = {"pts": 80, "reb": 35, "ast": 30, "fg3m": 18}
#: Stop storing once P(Y >= k) is at most this (99.9% of the mass is inside 0..k-1).
TAIL_EPS = 1e-3
#: Denominator for parametric (non-sample) rows.
PARAM_DENOM = 10_000
FIELD = "p_ge_full"


def _cut(c: np.ndarray, n: int) -> list[int]:
    """Keep entries up to and including the first with ``c / n <= TAIL_EPS``."""
    done = np.flatnonzero(c / n <= TAIL_EPS)
    end = int(done[0]) + 1 if done.size else int(c.size)
    return [int(v) for v in c[:end]]


def from_samples(q: np.ndarray, stat: str) -> dict[str, Any]:
    """Exact ``P(Y >= k) = mean(q > k - 0.5)`` from equally weighted quantile samples."""
    s = np.sort(np.asarray(q, dtype=float))
    n = int(s.size)
    ks = np.arange(1, KMAX[stat] + 1)
    c = n - np.searchsorted(s, ks - 0.5, side="right")
    return {"n": n, "c": _cut(c, n)}


def from_dist(dist: Any, stat: str) -> dict[str, Any]:
    """``P(Y >= k)`` from any object with ``p_ge(float)`` (``nba.props.distributions``)."""
    c: list[int] = []
    for k in range(1, KMAX[stat] + 1):
        p = float(dist.p_ge(float(k)))
        c.append(int(round(min(1.0, max(0.0, p)) * PARAM_DENOM)))
        if p <= TAIL_EPS:
            break
    return {"n": PARAM_DENOM, "c": c}


def dumps(fs: Mapping[str, Any]) -> str:
    return json.dumps({"c": fs["c"], "n": fs["n"]}, sort_keys=True, separators=(",", ":"))


def survival(fs: Mapping[str, Any] | None) -> np.ndarray | None:
    """Array ``[P(Y>=1), ..., P(Y>=K)]`` or ``None`` when the row has no full support."""
    if not fs or not fs.get("c") or not fs.get("n"):
        return None
    return np.asarray(fs["c"], dtype=float) / float(fs["n"])


def is_complete(surv: np.ndarray | None) -> bool:
    """True when the stored tail is at most ``TAIL_EPS`` (the row can be scored exactly)."""
    return surv is not None and surv.size > 0 and float(surv[-1]) <= TAIL_EPS


def crps_int_surv(surv: np.ndarray, y: int) -> float:
    """Integer-support CRPS (RPS) ``sum_k (F(k) - 1[y <= k])^2`` from ``surv[j] = P(Y >= j+1)``.

    ``F(k) = 1 - P(Y >= k + 1)``. Terms with ``k >= max(K, y)`` are exactly zero, so the sum
    over k = 0..max(K, y) - 1 equals the infinite sum."""
    big_k = int(surv.size)
    m = max(big_k, int(y))
    p = np.zeros(m)
    p[:big_k] = surv
    f = 1.0 - p  # F(k) for k = 0..m-1
    obs = (np.arange(m) >= int(y)).astype(float)
    return float(np.sum((f - obs) ** 2))


def crps_int(p_ge: Mapping[str, float], y: int, kmax: int | None = None) -> float:
    """Integer-support CRPS from a ``{"N": P(Y >= N)}`` dict covering N = 1..Nmax.

    ``kmax`` (optional) only extends the summation range; extra terms are exactly zero once
    ``P(Y >= k) = 0`` and ``k >= y``. Raises if the thresholds are not contiguous from 1."""
    keys = sorted(int(k) for k in p_ge)
    if keys != list(range(1, len(keys) + 1)):
        raise ValueError("p_ge must cover N = 1..Nmax contiguously")
    surv = np.asarray([float(p_ge[str(k)]) if str(k) in p_ge else 0.0 for k in keys])
    if kmax is not None and kmax > surv.size:
        surv = np.concatenate([surv, np.zeros(kmax - surv.size)])
    return crps_int_surv(surv, y)


def pit_bounds(surv: np.ndarray, y: int) -> tuple[float, float]:
    """``(F(y - 1), F(y))``: the continuity-corrected PIT of integer ``y`` is uniform on it."""
    big_k = int(surv.size)

    def cdf(k: int) -> float:
        if k < 0:
            return 0.0
        return 1.0 - (float(surv[k]) if k < big_k else 0.0)

    return cdf(int(y) - 1), cdf(int(y))
