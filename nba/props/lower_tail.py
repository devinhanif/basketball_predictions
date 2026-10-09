"""Lower-tail predictive-distribution candidates for the props heads (docs/LOWER_TAIL.md).

All candidates keep production's mean head, scale head and conformal calibration window; they
only change how the held-out standardized residuals are turned into predictive quantiles.
Every function is pure: it sees calibration rows and training-row summaries (never a label of
the rows being predicted). Constants are frozen in the pre-registration and are not tuned.

A "short" game is one with realised minutes < ``SHORT_RATIO`` x projected minutes (the as-of
``min10``). The ``mixture`` candidate models P(short | plays) pre-tip and gives short games
their own per-minute-scaled component; the two ``mondrian_*`` candidates only re-estimate
the lower half (tau < 0.5) of the conformal quantiles within a row bucket.
"""

from __future__ import annotations

from typing import Any

import lightgbm as lgb
import numpy as np

LOWER_TAIL_KINDS: tuple[str, ...] = ("off", "mixture", "mondrian_centre", "mondrian_minutes")
SHORT_RATIO = 0.5
PI_CLIP: tuple[float, float] = (0.005, 0.5)
MIN_NORMAL_CAL = 500  # mixture: need this many normal-minutes calibration rows
MIN_SHORT_TRAIN = 100  # mixture: need this many short training rows, else pi = 0
MIN_BIN = 200  # mondrian: bins with fewer calibration rows fall back to pooled
N_CENTRE_BINS = 3
MINUTES_EDGES: tuple[float, float] = (15.0, 25.0)
MIN_CENTRE = 0.5  # floor on the recency mean in the short-minutes ratio


def _monotone(q: np.ndarray) -> np.ndarray:
    return np.asarray(np.maximum.accumulate(np.clip(q, 0.0, None), axis=1))


def short_flag(minutes: np.ndarray, min10: np.ndarray) -> np.ndarray:
    """1 where realised minutes < SHORT_RATIO x projected minutes."""
    return np.asarray(minutes < SHORT_RATIO * min10)


# --------------------------------------------------------------------------- mixture


def fit_short_state(
    x: np.ndarray,
    short: np.ndarray,
    y: np.ndarray,
    m: np.ndarray,
    taus: np.ndarray,
    params: dict[str, Any],
    clf: Any | None = None,
) -> dict[str, Any]:
    """Fit the pre-tip short-minutes classifier on training rows (``x`` = COMMON_FEATURES) and
    the empirical quantiles of the short-minutes per-recency-mean ratio ``y / max(m, 0.5)``.
    Returns ``{"pi_model": classifier | None, "w_q": quantiles | None}``; ``pi_model`` is
    None (=> pi = 0) when there are too few short rows or only one class. A fitted ``clf`` may
    be passed in to share one classifier across stats (the target does not depend on the stat)."""
    n_short = int(short.sum())
    if n_short < MIN_SHORT_TRAIN or n_short == len(short):
        return {"pi_model": None, "w_q": None}
    if clf is None:
        clf = lgb.LGBMClassifier(**{**params, "objective": "binary"}).fit(x, short.astype(int))
    w = y[short] / np.maximum(m[short], MIN_CENTRE)
    return {"pi_model": clf, "w_q": np.asarray(np.quantile(w, taus))}


def predict_pi(state: dict[str, Any], x: np.ndarray) -> np.ndarray:
    """Pre-tip P(short | plays), clipped to PI_CLIP; zeros when the classifier is absent."""
    clf = state["pi_model"]
    if clf is None:
        return np.zeros(len(x))
    return np.asarray(np.clip(clf.predict_proba(x)[:, 1], PI_CLIP[0], PI_CLIP[1]))


def mixture_quantiles(
    c: np.ndarray,
    s: np.ndarray,
    m_row: np.ndarray,
    pi: np.ndarray,
    z_norm: np.ndarray,
    w_q: np.ndarray | None,
    taus: np.ndarray,
) -> np.ndarray:
    """Quantiles of ``(1 - pi) * Normal(c, s, z_norm) + pi * Short(m_row * w_q)``. The two
    components are equal-mass point sets on the tau grid; the mixture quantiles are read off the
    pooled, weighted, sorted points (midpoint cumulative weights, linear interpolation)."""
    zq = np.asarray(np.quantile(z_norm, taus))
    q_norm = np.clip(c[:, None] + s[:, None] * zq[None, :], 0.0, None)
    if w_q is None:
        return _monotone(q_norm)
    k = len(taus)
    q_short = np.clip(np.maximum(m_row, MIN_CENTRE)[:, None] * w_q[None, :], 0.0, None)
    vals = np.concatenate([q_norm, q_short], axis=1)
    wts = np.concatenate(
        [np.repeat(((1.0 - pi) / k)[:, None], k, axis=1), np.repeat((pi / k)[:, None], k, axis=1)],
        axis=1,
    )
    order = np.argsort(vals, axis=1, kind="stable")
    vals = np.take_along_axis(vals, order, axis=1)
    wts = np.take_along_axis(wts, order, axis=1)
    cum = np.cumsum(wts, axis=1) - 0.5 * wts
    out = np.empty((len(c), len(taus)))
    for i in range(len(c)):
        out[i] = np.interp(taus, cum[i], vals[i])
    return _monotone(out)


# --------------------------------------------------------------------------- mondrian lower


def mondrian_lower_z(
    z_cal: np.ndarray, bin_cal: np.ndarray, bin_row: np.ndarray, taus: np.ndarray
) -> np.ndarray:
    """Standardized quantiles ``[n_rows, len(taus)]``: pooled everywhere except tau < 0.5, which
    comes from the calibration rows of the row's own bin (bins < MIN_BIN keep pooled)."""
    pooled = np.asarray(np.quantile(z_cal, taus))
    zq = np.tile(pooled, (len(bin_row), 1))
    low = taus < 0.5
    for k in np.unique(bin_row):
        zk = z_cal[bin_cal == k]
        if zk.size < MIN_BIN:
            continue
        zq[np.ix_(bin_row == k, low)] = np.quantile(zk, taus[low])[None, :]
    return zq


def centre_bins(c_cal: np.ndarray, c: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    edges = np.quantile(c_cal, np.linspace(0, 1, N_CENTRE_BINS + 1)[1:-1])
    return np.searchsorted(edges, c_cal, side="right"), np.searchsorted(edges, c, side="right")


def minutes_bins(min10_cal: np.ndarray, min10: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    edges = np.asarray(MINUTES_EDGES)
    return np.searchsorted(edges, min10_cal, side="right"), np.searchsorted(
        edges, min10, side="right"
    )


# --------------------------------------------------------------------------- dispatch


def lower_quantiles(
    kind: str,
    *,
    z_cal: np.ndarray,
    c_cal: np.ndarray,
    min10_cal: np.ndarray,
    short_cal: np.ndarray,
    c: np.ndarray,
    s: np.ndarray,
    m_row: np.ndarray,
    min10_row: np.ndarray,
    pi: np.ndarray | None,
    w_q: np.ndarray | None,
    taus: np.ndarray,
) -> np.ndarray:
    """Dispatch on ``kind`` (one of :data:`LOWER_TAIL_KINDS`). ``off`` is production."""
    if kind == "off":
        zq = np.asarray(np.quantile(z_cal, taus))
        return _monotone(c[:, None] + s[:, None] * zq[None, :])
    if kind == "mixture":
        z_norm = z_cal[~short_cal]
        if len(z_norm) < MIN_NORMAL_CAL or pi is None:
            zq = np.asarray(np.quantile(z_cal, taus))
            return _monotone(c[:, None] + s[:, None] * zq[None, :])
        return mixture_quantiles(c, s, m_row, pi, z_norm, w_q, taus)
    if kind == "mondrian_centre":
        bc, br = centre_bins(c_cal, c)
        return _monotone(c[:, None] + s[:, None] * mondrian_lower_z(z_cal, bc, br, taus))
    if kind == "mondrian_minutes":
        bc, br = minutes_bins(min10_cal, min10_row)
        return _monotone(c[:, None] + s[:, None] * mondrian_lower_z(z_cal, bc, br, taus))
    raise ValueError(f"unknown lower_tail kind: {kind!r}")
