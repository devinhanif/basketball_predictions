# ruff: noqa: E501
"""Gaussian-copula joint of integer stat marginals, sampled to an integer pmf per combo prop.

Used by the COMBO_STOCKS shadow (docs/prereg/COMBO_STOCKS_SHADOW.md). Inputs are, per row, the marginal
forecast of each stat as an equal-mass 199-point INTEGER grid (``to_integer_support`` of the 199 predictive
quantiles at ``CRPS_TAUS``): the same object ``nba.props.full_support`` turns into ``p_ge_full`` and the
other shadows store. Everything here is numpy only and deterministic:

* ``int_cdf``: a grid to the marginal CDF over ``0..kmax`` (mass above ``kmax`` is lumped into ``kmax``).
* ``rpit_scores``: randomised PIT of integer outcomes, then the normal score (the usual device for discrete margins).
* ``estimate_latent_corr``: latent Gaussian-copula correlations by INDIRECT INFERENCE. The randomised-PIT
  correlation of discrete data is biased toward 0 (each margin adds independent uniform noise), so for each
  pair the latent rho is the value whose SIMULATED randomised-PIT correlation, under the same marginals,
  equals the observed one. Validated on known-truth simulations in ``tests/props/test_combo_copula.py``.
* ``joint_grids``: fixed common random numbers ``base`` (N x d standard normals, seed frozen in the rule) are
  correlated by ``chol(R)``, pushed through ``Phi``, and inverted through each row's marginal CDF; the
  requested sums (PRA, PR, PA, RA, blk+stl) become per-row 199-point integer grids. ``R = I`` is the
  independent-sum arm, produced by the SAME sampler so both arms carry the same Monte-Carlo noise.
* ``nearest_corr``: eigenvalue clipping so a pairwise-estimated matrix is positive definite.

No file or database is read here.
"""

from __future__ import annotations

import numpy as np
import polars as pl
from scipy.special import ndtr, ndtri

from nba.props.context_residual import CRPS_TAUS

#: Support caps per stat (mass above is lumped into the cap); pts/reb/ast as ``nba.props.full_support.KMAX``.
KMAX: dict[str, int] = {"pts": 80, "reb": 35, "ast": 30, "fg3m": 18, "blk": 12, "stl": 12}
COMBOS: dict[str, tuple[str, ...]] = {
    "pra": ("pts", "reb", "ast"),
    "pr": ("pts", "reb"),
    "pa": ("pts", "ast"),
    "ra": ("reb", "ast"),
    "bs": ("blk", "stl"),
}
STAT_ORDER: tuple[str, ...] = ("pts", "reb", "ast", "fg3m", "blk", "stl")
N_DRAWS = 10_000
BASE_SEED = 20261014
EPS = 1e-6


def base_normals(
    n_draws: int = N_DRAWS, d: int = len(STAT_ORDER), seed: int = BASE_SEED
) -> np.ndarray:
    """The frozen common random numbers: ``n_draws x d`` standard normals generated once from ``seed``.

    Variance reduction, because every row shares these draws (a draw-level error is the same for all rows): half
    the draws are iid N(0, 1), centred and whitened to sample covariance exactly I, and the other half are their
    mirror images, so the sample mean is exactly 0, odd moments vanish, and ``base @ chol(R).T`` has sample
    correlation exactly ``R``."""
    half = n_draws // 2
    e = np.random.default_rng(seed).standard_normal((half, d))
    e = e - e.mean(axis=0)
    cov = e.T @ e / half
    e = e @ np.linalg.inv(np.linalg.cholesky(cov)).T
    return np.vstack([e, -e])


def int_cdf(grid: np.ndarray, kmax: int) -> np.ndarray:
    """CDF over ``0..kmax`` of equal-mass integer grids ``(n, g)``; entry ``[i, k] = P(Y <= k)``."""
    g = np.asarray(grid)
    v = np.clip(g.astype(np.int64), 0, kmax)
    n, m = v.shape
    counts = np.zeros((n, kmax + 1))
    np.add.at(counts, (np.repeat(np.arange(n), m), v.ravel()), 1.0)
    cdf = np.cumsum(counts, axis=1) / m
    cdf[:, -1] = 1.0
    return np.asarray(cdf)


def invert(cdf: np.ndarray, u: np.ndarray) -> np.ndarray:
    """Quantile transform: ``Y = min{k : F(k) >= u}`` row-wise for ``u`` of shape ``(n,)`` or broadcastable ``(n, m)``."""
    if u.ndim == 1:
        return np.asarray((cdf < u[:, None]).sum(axis=1))
    return np.asarray((cdf[:, :, None] < u[:, None, :]).sum(axis=1))


def rpit_scores(cdf: np.ndarray, y: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Randomised PIT ``F(y-1) + v * (F(y) - F(y-1))`` of integer outcomes (``v`` ~ U(0,1) given), as normal scores."""
    n = len(y)
    k = np.clip(y.astype(np.int64), 0, cdf.shape[1] - 1)
    hi = cdf[np.arange(n), k]
    lo = np.where(k > 0, cdf[np.arange(n), np.maximum(k - 1, 0)], 0.0)
    return np.asarray(ndtri(np.clip(lo + v * (hi - lo), EPS, 1.0 - EPS)))


def _pair_corr(
    cdf_a: np.ndarray, cdf_b: np.ndarray, rho: float, rng: np.random.Generator, reps: int = 3
) -> float:
    """Randomised-PIT normal-score correlation of data simulated from the copula with latent ``rho``."""
    n = int(cdf_a.shape[0])
    zs_a, zs_b = [], []
    for _ in range(reps):
        e = rng.standard_normal((n, 2))
        z2 = rho * e[:, 0] + np.sqrt(max(1.0 - rho**2, 0.0)) * e[:, 1]
        ya, yb = invert(cdf_a, ndtr(e[:, 0])), invert(cdf_b, ndtr(z2))
        zs_a.append(rpit_scores(cdf_a, ya, rng.random(n)))
        zs_b.append(rpit_scores(cdf_b, yb, rng.random(n)))
    return float(np.corrcoef(np.concatenate(zs_a), np.concatenate(zs_b))[0, 1])


def nearest_corr(r: np.ndarray, floor: float = 1e-6) -> np.ndarray:
    """Symmetrise, clip eigenvalues at ``floor``, rescale to unit diagonal."""
    s = (r + r.T) / 2.0
    w, vec = np.linalg.eigh(s)
    out = (vec * np.maximum(w, floor)) @ vec.T
    dg = np.sqrt(np.diag(out))
    out = out / dg[:, None] / dg[None, :]
    np.fill_diagonal(out, 1.0)
    return np.asarray((out + out.T) / 2.0)


def row_moments(cdf: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-row mean and variance of the marginal CDF (support ``0..kmax``)."""
    sf = 1.0 - cdf[:, :-1]  # P(Y > k) for k = 0 .. kmax - 1
    k = np.arange(sf.shape[1])
    mean = sf.sum(axis=1)
    ey2 = (sf * (2 * k + 1)).sum(axis=1)
    return mean, np.maximum(ey2 - mean**2, 1e-12)


def _pair_stat(
    cdf_a: np.ndarray,
    cdf_b: np.ndarray,
    rho: float,
    rng: np.random.Generator,
    method: str,
    reps: int = 3,
) -> float:
    """The matching statistic of data simulated from the copula with latent ``rho``: ``"rpit"`` is the correlation of the
    randomised-PIT normal scores, ``"cov"`` the pooled standardised cross-product of the integer residuals."""
    n = int(cdf_a.shape[0])
    ma, va = row_moments(cdf_a)
    mb, vb = row_moments(cdf_b)
    zs_a, zs_b, cross = [], [], 0.0
    for _ in range(reps):
        e = rng.standard_normal((n, 2))
        z2 = rho * e[:, 0] + np.sqrt(max(1.0 - rho**2, 0.0)) * e[:, 1]
        ya, yb = invert(cdf_a, ndtr(e[:, 0])), invert(cdf_b, ndtr(z2))
        if method == "rpit":
            zs_a.append(rpit_scores(cdf_a, ya, rng.random(n)))
            zs_b.append(rpit_scores(cdf_b, yb, rng.random(n)))
        else:
            cross += float(((ya - ma) * (yb - mb)).mean())
    if method == "rpit":
        return float(np.corrcoef(np.concatenate(zs_a), np.concatenate(zs_b))[0, 1])
    return cross / reps / float(np.sqrt(va.mean() * vb.mean()))


RHO_GRID = np.round(np.arange(-0.5, 0.951, 0.05), 2)
METHODS = ("rpit", "cov")


def estimate_latent_corr(
    cdfs: list[np.ndarray],
    ys: list[np.ndarray],
    seed: int = 0,
    rho_grid: np.ndarray = RHO_GRID,
    reps: int = 3,
    method: str = "rpit",
) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    """Latent copula correlation matrix from outcomes ``ys`` under marginal CDFs ``cdfs`` (one pair at a time).

    ``method`` names the statistic that is matched between data and simulation: ``"rpit"`` (correlation of
    randomised-PIT normal scores) or ``"cov"`` (standardised cross-product of the integer residuals, the quantity
    that sets the width of a SUM). Returns ``(R, R_obs, info)``: ``R`` positive definite latent, ``R_obs`` the observed
    statistic matrix, ``info`` the largest eigenvalue shift made by ``nearest_corr`` and the number of rows."""
    if method not in METHODS:
        raise ValueError(f"method {method!r} not in {METHODS}")
    d = len(cdfs)
    rng = np.random.default_rng(seed)
    n = int(cdfs[0].shape[0])
    if method == "rpit":
        zs = [rpit_scores(c, y, rng.random(n)) for c, y in zip(cdfs, ys, strict=True)]
        r_obs = np.corrcoef(np.column_stack(zs), rowvar=False)
    else:
        mom = [row_moments(c) for c in cdfs]
        res = [y - m for (m, _), y in zip(mom, ys, strict=True)]
        r_obs = np.eye(d)
        for i in range(d):
            for j in range(i + 1, d):
                r_obs[i, j] = r_obs[j, i] = float(
                    (res[i] * res[j]).mean() / np.sqrt(mom[i][1].mean() * mom[j][1].mean())
                )
    r_lat = np.eye(d)
    for i in range(d):
        for j in range(i + 1, d):
            g = np.array(
                [_pair_stat(cdfs[i], cdfs[j], float(rho), rng, method, reps) for rho in rho_grid]
            )
            g = np.maximum.accumulate(g)  # the map is monotone; remove simulation noise reversals
            r_lat[i, j] = r_lat[j, i] = float(np.interp(float(r_obs[i, j]), g, rho_grid))
    r_psd = nearest_corr(r_lat)
    return r_psd, r_obs, {"max_abs_psd_shift": float(np.abs(r_psd - r_lat).max()), "n": float(n)}


def joint_grids(
    cdfs: dict[str, np.ndarray],
    r: np.ndarray,
    base: np.ndarray,
    combos: dict[str, tuple[str, ...]] = COMBOS,
    chunk: int = 48,
) -> dict[str, np.ndarray]:
    """Per-row 199-point integer grids of each combo sum under the Gaussian copula ``r`` (``STAT_ORDER`` order).

    ``cdfs[s]`` is ``(n, kmax_s + 1)``. Draw ``m`` of every row uses the same standard normals ``base[m]``
    (common random numbers), so the result does not depend on the batch a row is in."""
    n = next(iter(cdfs.values())).shape[0]
    pos = {s: i for i, s in enumerate(STAT_ORDER)}
    z = base @ np.linalg.cholesky(r).T
    u = ndtr(z)  # (N, d)
    n_draws = base.shape[0]
    out = {c: np.empty((n, len(CRPS_TAUS)), dtype=np.int16) for c in combos}
    tau = CRPS_TAUS - 1e-12
    for lo in range(0, n, chunk):
        hi = min(lo + chunk, n)
        y: dict[str, np.ndarray] = {}
        for s in {s for c in combos.values() for s in c}:
            cd = cdfs[s][lo:hi]
            cd = cd[
                :, : max(int((cd < 1.0).sum(axis=1).max()), 1)
            ]  # columns with F == 1 can never be counted
            y[s] = invert(cd, np.broadcast_to(u[:, pos[s]], (hi - lo, n_draws))).astype(np.int16)
        for name, parts in combos.items():
            tot = np.sum([y[s].astype(np.int32) for s in parts], axis=0)  # (c, N)
            kmax = int(tot.max()) + 1
            idx = (np.arange(hi - lo, dtype=np.int64)[:, None] * kmax + tot).ravel()
            counts = np.bincount(idx, minlength=(hi - lo) * kmax).reshape(hi - lo, kmax)
            cdf = np.cumsum(counts, axis=1) / n_draws
            out[name][lo:hi] = (cdf[:, :, None] < tau[None, None, :]).sum(axis=1)
    return out


def pmf_from_grid(grid: np.ndarray, kmax: int | None = None) -> np.ndarray:
    """Equal-mass integer grids ``(n, g)`` to pmfs ``(n, kmax + 1)`` (mass above ``kmax`` lumped)."""
    top = int(np.max(grid)) if kmax is None else kmax
    c = int_cdf(grid, top)
    return np.asarray(np.diff(np.concatenate([np.zeros((c.shape[0], 1)), c], axis=1), axis=1))


def q19_of_grid199(g: np.ndarray) -> np.ndarray:
    """The 19 stored quantiles (taus 0.05..0.95) of an equal-mass 199 grid: the ceil(tau * 199)-th smallest value."""
    taus19 = np.round(0.05 * np.arange(1, 20), 2)
    s = np.sort(np.asarray(g), axis=1)
    pos = np.ceil(taus19 * len(CRPS_TAUS) - 1e-9).astype(int) - 1
    return np.asarray(s[:, pos])


def convolve_exact(cdfs: dict[str, np.ndarray], parts: tuple[str, ...]) -> np.ndarray:
    """Exact pmf of the INDEPENDENT sum of the named marginals (rows aligned), for checking the sampler."""
    pm = [
        np.diff(np.concatenate([np.zeros((cdfs[s].shape[0], 1)), cdfs[s]], axis=1), axis=1)
        for s in parts
    ]
    acc = pm[0]
    for nxt in pm[1:]:
        n = acc.shape[0]
        res = np.zeros((n, acc.shape[1] + nxt.shape[1] - 1))
        for k in range(nxt.shape[1]):
            res[:, k : k + acc.shape[1]] += acc * nxt[:, k : k + 1]
        acc = res
    return np.asarray(acc)


# --------------------------------------------------------------------------- recent-form empirical baseline
RECENT_WINDOW = 40
RECENT_HALFLIFE = 10.0


def recent_windows(played: pl.DataFrame, col: str, window: int = RECENT_WINDOW) -> np.ndarray:
    """The player's previous ``window`` PLAYED values of ``col`` per row, oldest first, NaN where fewer exist.

    ``played`` must be sorted by (player_id, game_date, game_id) and hold played rows only; row ``i`` sees rows
    of the same player strictly before it, never itself."""
    lags = [
        pl.col(col).cast(pl.Float64).shift(k).over("player_id").alias(f"_l{k}")
        for k in range(window, 0, -1)
    ]
    return played.select(lags).to_numpy()


def empirical_grid(
    hist: np.ndarray, halflife: float = RECENT_HALFLIFE, chunk: int = 512
) -> np.ndarray:
    """199-point integer grid of the recency-weighted empirical distribution of ``hist`` rows (oldest first).

    The most recent value has weight 1, the one before ``0.5 ** (1 / halflife)``, and so on (production's
    recency weights). Rows with no history give an all-NaN result cast to 0; callers restrict to >= 5 prior games."""
    n, w = hist.shape
    wt = 0.5 ** ((w - 1 - np.arange(w)) / halflife)
    out = np.zeros((n, len(CRPS_TAUS)), dtype=np.int16)
    tau = CRPS_TAUS - 1e-12
    for lo in range(0, n, chunk):
        h = hist[lo : lo + chunk]
        ok = ~np.isnan(h)
        key = np.where(ok, h, np.inf)
        order = np.argsort(key, axis=1, kind="stable")
        sv = np.take_along_axis(np.where(ok, h, 0.0), order, axis=1)
        sw = np.take_along_axis(np.where(ok, wt[None, :], 0.0), order, axis=1)
        tot = sw.sum(axis=1, keepdims=True)
        cum = np.cumsum(sw, axis=1) / np.where(tot > 0, tot, 1.0)
        idx = np.minimum((cum[:, :, None] < tau[None, None, :]).sum(axis=1), w - 1)
        out[lo : lo + chunk] = np.take_along_axis(sv, idx, axis=1).astype(np.int16)
    return out


def grid_from_survival(surv: np.ndarray) -> np.ndarray:
    """199-point integer grid of a stored full-support survival ``surv[j] = P(Y >= j + 1)`` (``nba.props.full_support``).

    For production's context rows (``n = 199`` samples) this is exactly ``to_integer_support`` of the 199 predictive
    quantiles, because ``c_k = #(q > k - 0.5)``; for any other ``n`` it is the integer quantile function at ``CRPS_TAUS``."""
    cdf = np.concatenate([1.0 - np.asarray(surv, dtype=float), [1.0]])
    cdf = np.maximum.accumulate(np.clip(cdf, 0.0, 1.0))
    return np.asarray((cdf[:, None] < (CRPS_TAUS - 1e-12)[None, :]).sum(axis=0), dtype=np.int16)
