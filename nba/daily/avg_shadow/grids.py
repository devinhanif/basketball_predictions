# ruff: noqa: E501
"""numpy-only grid arithmetic of the AVG shadow (no torch, no lightgbm; safe in the daily process).

These are line-for-line copies of the offline helpers that built T478's AVG
(``research.eval.ensemble_eval.ext19 / int_pmf / mix_integer_quantiles / grid19``), kept here because
``nba/`` never imports ``research``. ``tests/daily/test_avg_shadow.py`` pins them to the originals.
"""

from __future__ import annotations

import numpy as np

from nba.props.context_residual import CRPS_TAUS, to_integer_support
from nba.props.forward import QUANTILE_TAUS

TAUS19 = np.asarray(QUANTILE_TAUS, dtype=float)
GATE_COLUMNS = ("log_n_prior", "starter10", "n_rep_out", "has_report", "team_game_no")
N_GRID = len(CRPS_TAUS)  # 199


def ext19(q19: np.ndarray) -> np.ndarray:
    """19 quantiles (tau 0.05..0.95) to the 199 grid: linear inside, linear-slope extrapolation outside, clipped at 0, monotone."""
    q19 = np.sort(np.asarray(q19, dtype=float), axis=1)
    t19 = np.arange(1, 20) / 20.0
    t = CRPS_TAUS
    k = np.clip(np.searchsorted(t19, t) - 1, 0, 17)
    w = (t - t19[k]) / 0.05
    q = q19[:, k] * (1 - w) + q19[:, k + 1] * w
    lo, hi = t < 0.05, t > 0.95
    s_lo = np.maximum(q19[:, 1] - q19[:, 0], 0.0) / 0.05
    s_hi = np.maximum(q19[:, 18] - q19[:, 17], 0.0) / 0.05
    q[:, lo] = q19[:, :1] + s_lo[:, None] * (t[lo] - 0.05)[None, :]
    q[:, hi] = q19[:, 18:19] + s_hi[:, None] * (t[hi] - 0.95)[None, :]
    return np.asarray(np.maximum.accumulate(np.clip(q, 0.0, None), axis=1))


def int_pmf(qi: np.ndarray, k: int) -> np.ndarray:
    """Equal-mass integer grids (n, 199) to pmfs over 0..k-1 (values >= k are lumped into k - 1)."""
    v = np.clip(np.asarray(qi).astype(np.int64), 0, k - 1)
    out = np.zeros((v.shape[0], k))
    np.add.at(out, (np.repeat(np.arange(v.shape[0]), v.shape[1]), v.ravel()), 1.0 / v.shape[1])
    return out


def mix_integer_quantiles(qa: np.ndarray, qb: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Integer 199-grid of the pmf mixture ``w * pmf(qa) + (1 - w) * pmf(qb)`` (weights per row)."""
    k = int(max(np.max(qa), np.max(qb))) + 2
    cdf = np.cumsum(w[:, None] * int_pmf(qa, k) + (1.0 - w)[:, None] * int_pmf(qb, k), axis=1)
    out = np.empty((cdf.shape[0], len(CRPS_TAUS)), dtype=np.int16)
    for i in range(0, cdf.shape[0], 4096):
        c = cdf[i : i + 4096]
        out[i : i + 4096] = (c[:, :, None] < (CRPS_TAUS - 1e-12)[None, None, :]).sum(axis=1)
    return out


def mix_half(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return mix_integer_quantiles(a, b, np.full(len(a), 0.5))


def q19_of_grid199(g: np.ndarray) -> np.ndarray:
    """The 19 stored quantiles of an equal-mass 199 grid: the ceil(tau * 199)-th smallest value (smallest v with CDF(v) >= tau).
    Integer in, integer out; the stored AVG grid is reproducible from the stored 199 values with this function (exact)."""
    s = np.sort(np.asarray(g), axis=1)
    pos = np.ceil(TAUS19 * N_GRID - 1e-9).astype(int) - 1
    return np.asarray(s[:, pos])


def softmax_gate(
    x: np.ndarray, med: np.ndarray, mu: np.ndarray, sd: np.ndarray, coef: np.ndarray
) -> np.ndarray:
    """T442's SoftmaxGate weights over [A0, SEQ] from the stored encoder (median impute, standardise) and coefficients (6 x 2)."""
    x = np.where(np.isnan(x), med, x)
    z = (x - mu) / sd
    a = np.column_stack([np.ones(len(z)), z]) @ coef
    a = a - a.max(axis=1, keepdims=True)
    e = np.exp(a)
    return np.asarray(e / e.sum(axis=1, keepdims=True))


def build_arms(
    a0_q19: np.ndarray,
    full_q19: np.ndarray,
    seq_q19: np.ndarray,
    gate_w: np.ndarray,
    seq_ok: np.ndarray,
) -> dict[str, np.ndarray]:
    """Integer 199-grids of SEQc and AVG for rows (n, 19) of A0 / FULL / SEQ stored quantiles.

    SEQc = to_integer(w0 * ext19(A0) + w1 * ext19(SEQ)) on rows where ``seq_ok`` (regular season, n_prior >= 3), else A0 integer
    (T478's route); AVG = 0.5 pmf(SEQc) + 0.5 pmf(FULL integer). Rows without ``seq_ok`` are not served by the shadow at all (the caller
    drops them); their arrays are still defined so the shapes line up.
    """
    a0c = ext19(a0_q19)
    a0i = to_integer_support(a0c).astype(np.int16)
    fulli = to_integer_support(ext19(full_q19)).astype(np.int16)
    pooled = gate_w[:, :1] * a0c + gate_w[:, 1:2] * ext19(seq_q19)
    seqc = np.where(seq_ok[:, None], to_integer_support(pooled).astype(np.int16), a0i)
    return {"A0": a0i, "FULL": fulli, "SEQc": seqc, "AVG": mix_half(seqc, fulli)}
