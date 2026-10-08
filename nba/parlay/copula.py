"""Correlation estimation, PSD repair, and seeded Gaussian / t copula sampling.

A leg hits ('yes') iff its latent uniform ``u > 1 - p_yes``; ('no') iff ``u <= 1 - p_yes``.
Only the marginal ``p_yes`` per leg is needed, which sidesteps discrete-CDF details.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy import stats

from nba.parlay.legs import Leg

FloatArr = NDArray[np.float64]


def nearest_correlation(a: FloatArr, n_iter: int = 200, tol: float = 1e-10) -> FloatArr:
    """Higham (2002) nearest correlation matrix, then a strict PD guarantee."""
    a = (np.asarray(a, dtype=float) + np.asarray(a, dtype=float).T) / 2.0
    y = a.copy()
    ds = np.zeros_like(a)
    for _ in range(n_iter):
        r = y - ds
        w, v = np.linalg.eigh(r)
        x = (v * np.clip(w, 0.0, None)) @ v.T
        ds = x - r
        y_new = x.copy()
        np.fill_diagonal(y_new, 1.0)
        if np.linalg.norm(y_new - y, "fro") < tol:
            y = y_new
            break
        y = y_new
    w, v = np.linalg.eigh((y + y.T) / 2.0)
    y = (v * np.clip(w, 1e-8, None)) @ v.T
    d = 1.0 / np.sqrt(np.diag(y))
    y = y * d[:, None] * d[None, :]
    y = (y + y.T) / 2.0
    np.fill_diagonal(y, 1.0)
    return y


@dataclass(frozen=True)
class ResidualRow:
    """Historical normal-score residual z = Phi^-1(randomized PIT) of one player-stat-game."""

    game_id: str
    player_id: int
    team_id: int
    stat: str
    z: float


def randomized_pit_z(cdf_lo: float, cdf_hi: float, u: float) -> float:
    """Normal score of a randomized PIT; ``cdf_lo=F(x-1)``, ``cdf_hi=F(x)``, ``u`` in (0,1)."""
    v = cdf_lo + u * (cdf_hi - cdf_lo)
    return float(stats.norm.ppf(min(max(v, 1e-9), 1 - 1e-9)))


def _relation(player_a: int, team_a: int | None, player_b: int, team_b: int | None) -> str:
    if player_a == player_b:
        return "same_player"
    if team_a is not None and team_b is not None:
        return "teammate" if team_a == team_b else "opponent"
    return "unknown"


@dataclass(frozen=True)
class PairCorr:
    rho: dict[tuple[str, str, str], float]
    n: dict[tuple[str, str, str], int]

    def get(self, relation: str, stat_a: str, stat_b: str) -> float:
        key = (relation, *sorted((stat_a, stat_b)))
        return self.rho.get(key, 0.0)  # type: ignore[arg-type]


def estimate_pair_corr(rows: Sequence[ResidualRow], shrink_k: float) -> PairCorr:
    """Pearson correlation of same-game residuals by (relation, stat pair), shrunk toward 0."""
    by_game: dict[str, list[ResidualRow]] = defaultdict(list)
    for r in rows:
        by_game[r.game_id].append(r)
    xs: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    ys: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for grp in by_game.values():
        for i, a in enumerate(grp):
            for b in grp[i + 1 :]:
                rel = _relation(a.player_id, a.team_id, b.player_id, b.team_id)
                if a.stat <= b.stat:
                    first, second = a, b
                else:
                    first, second = b, a
                key = (rel, first.stat, second.stat)
                xs[key].append(first.z)
                ys[key].append(second.z)
    rho: dict[tuple[str, str, str], float] = {}
    n: dict[tuple[str, str, str], int] = {}
    for key, x in xs.items():
        cnt = len(x)
        n[key] = cnt
        if cnt < 3:
            continue
        xa, ya = np.asarray(x), np.asarray(ys[key])
        if xa.std() < 1e-12 or ya.std() < 1e-12:
            continue
        rho[key] = float(np.corrcoef(xa, ya)[0, 1]) * cnt / (cnt + shrink_k)
    return PairCorr(rho=rho, n=n)


def build_corr_matrix(legs: Sequence[Leg], pc: PairCorr) -> FloatArr:
    """Stat-level latent correlation for ``legs`` (side is handled at hit time), PSD-repaired."""
    k = len(legs)
    m = np.eye(k)
    for i in range(k):
        for j in range(i + 1, k):
            a, b = legs[i], legs[j]
            if a.game_id != b.game_id:
                continue
            rel = _relation(a.player_id, a.team_id, b.player_id, b.team_id)
            r = pc.get(rel, a.stat, b.stat)
            m[i, j] = m[j, i] = r
    return nearest_correlation(m)


def sample_latent_uniforms(
    corr: FloatArr, n_sims: int, seed: int, kind: str = "gaussian", df: float = 6.0
) -> FloatArr:
    """Seeded (n_sims, k) uniforms from a Gaussian or Student-t copula."""
    rng = np.random.default_rng(seed)
    k = corr.shape[0]
    chol = np.linalg.cholesky(corr)
    z = rng.standard_normal((n_sims, k)) @ chol.T
    if kind == "gaussian":
        return np.asarray(stats.norm.cdf(z), dtype=float)
    if kind == "t":
        w = np.sqrt(rng.chisquare(df, size=(n_sims, 1)) / df)
        return np.asarray(stats.t.cdf(z / w, df), dtype=float)
    raise ValueError(f"unknown copula kind {kind!r}")


def hits_from_uniforms(
    u: FloatArr, p_yes_vec: Sequence[float], sides: Sequence[str]
) -> NDArray[np.bool_]:
    """Boolean (n_sims, k) leg-hit matrix from latent uniforms."""
    thr = 1.0 - np.asarray(p_yes_vec, dtype=float)
    yes = u > thr[None, :]
    no_mask = np.asarray([s == "no" for s in sides])
    return np.asarray(np.where(no_mask[None, :], ~yes, yes), dtype=bool)
