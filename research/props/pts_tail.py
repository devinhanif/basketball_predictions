"""Candidate upper-tail quantile constructions for the points head (docs/PTS_TAIL.md).

All three candidates keep the production mean head, scale head and conformal calibration
window untouched; they only change how the held-out standardized residuals are turned into
predictive quantiles. Every function is pure: it sees the calibration rows (``y``, centre
``c = m + mu``, scale ``s``) and the rows to predict, never a label of the predicted rows.
Parameters are frozen in the pre-registration; nothing here is tuned.
"""

from __future__ import annotations

import numpy as np
from scipy import stats

PTS_TAIL_KINDS: tuple[str, ...] = ("off", "sqrt", "mondrian_up", "gamma_tail")
#: Fixed constants (pre-registered, not tuned).
TAIL_START = 0.75  # gamma tail fitted to z above this empirical quantile
TAIL_BLEND = 0.5  # weight on the gamma tail quantile for tau > TAIL_START
N_LEVEL_BINS = 3  # mondrian_up: predicted-centre terciles
MIN_CENTRE = 0.5  # floor on the predicted centre before the sqrt transform


def _monotone(q: np.ndarray) -> np.ndarray:
    return np.asarray(np.maximum.accumulate(np.clip(q, 0.0, None), axis=1))


def raw_quantiles(
    c: np.ndarray, s: np.ndarray, z_sorted: np.ndarray, taus: np.ndarray
) -> np.ndarray:
    """Production construction: ``c + s * empirical_quantile(z, tau)``."""
    zq = np.asarray(np.quantile(z_sorted, taus))
    return _monotone(c[:, None] + s[:, None] * zq[None, :])


def sqrt_quantiles(
    y_cal: np.ndarray,
    c_cal: np.ndarray,
    s_cal: np.ndarray,
    c: np.ndarray,
    s: np.ndarray,
    taus: np.ndarray,
) -> np.ndarray:
    """(a) Conformal on the square-root scale (delta-method scale ``s / (2 sqrt(c))``)."""
    rc_cal = np.sqrt(np.maximum(c_cal, MIN_CENTRE))
    w = np.sort((np.sqrt(np.maximum(y_cal, 0.0)) - rc_cal) / (s_cal / (2.0 * rc_cal)))
    wq = np.asarray(np.quantile(w, taus))
    rc = np.sqrt(np.maximum(c, MIN_CENTRE))
    root = rc[:, None] + (s / (2.0 * rc))[:, None] * wq[None, :]
    return _monotone(np.maximum(root, 0.0) ** 2)


def mondrian_up_quantiles(
    z_cal: np.ndarray,
    c_cal: np.ndarray,
    c: np.ndarray,
    s: np.ndarray,
    taus: np.ndarray,
) -> np.ndarray:
    """(b) Separate upper-tail (tau > 0.5) conformal widths per predicted-centre tercile;
    the lower half keeps the pooled quantiles."""
    pooled = np.asarray(np.quantile(z_cal, taus))
    zq = np.tile(pooled, (len(c), 1))
    edges = np.quantile(c_cal, np.linspace(0, 1, N_LEVEL_BINS + 1)[1:-1])
    b_cal = np.searchsorted(edges, c_cal, side="right")
    b = np.searchsorted(edges, c, side="right")
    up = taus > 0.5
    for k in range(N_LEVEL_BINS):
        zk = z_cal[b_cal == k]
        if zk.size < 200:  # too thin a bin: keep the pooled quantiles
            continue
        zk_q = np.quantile(zk, taus[up])
        zq[np.ix_(b == k, up)] = zk_q[None, :]
    return _monotone(c[:, None] + s[:, None] * zq)


def gamma_tail_z(z_cal: np.ndarray, taus: np.ndarray) -> np.ndarray:
    """(c) Standardized-residual quantiles with a Gamma tail above ``TAIL_START``: gamma
    (loc fixed at 0) fitted to the exceedances over the pooled empirical q0.75, blended
    ``TAIL_BLEND`` : ``1 - TAIL_BLEND`` with the empirical quantile for tau > TAIL_START."""
    zq = np.asarray(np.quantile(z_cal, taus), dtype=float)
    u = float(np.quantile(z_cal, TAIL_START))
    ex = z_cal[z_cal > u] - u
    if ex.size < 100 or float(ex.min()) <= 0.0:
        return zq
    shape, _, scale = stats.gamma.fit(ex, floc=0.0)
    hi = taus > TAIL_START
    p = (taus[hi] - TAIL_START) / (1.0 - TAIL_START)
    zg = u + stats.gamma.ppf(p, a=shape, scale=scale)
    out = zq.copy()
    out[hi] = TAIL_BLEND * zg + (1.0 - TAIL_BLEND) * zq[hi]
    return out


def gamma_tail_quantiles(
    z_cal: np.ndarray, c: np.ndarray, s: np.ndarray, taus: np.ndarray
) -> np.ndarray:
    zq = gamma_tail_z(z_cal, taus)
    return _monotone(c[:, None] + s[:, None] * zq[None, :])


def tail_quantiles(
    kind: str,
    *,
    y_cal: np.ndarray,
    c_cal: np.ndarray,
    s_cal: np.ndarray,
    z_cal: np.ndarray,
    c: np.ndarray,
    s: np.ndarray,
    taus: np.ndarray,
) -> np.ndarray:
    """Dispatch on ``kind`` (one of :data:`PTS_TAIL_KINDS`)."""
    if kind == "off":
        return raw_quantiles(c, s, z_cal, taus)
    if kind == "sqrt":
        return sqrt_quantiles(y_cal, c_cal, s_cal, c, s, taus)
    if kind == "mondrian_up":
        return mondrian_up_quantiles(z_cal, c_cal, c, s, taus)
    if kind == "gamma_tail":
        return gamma_tail_quantiles(z_cal, c, s, taus)
    raise ValueError(f"unknown pts_tail kind: {kind!r}")
