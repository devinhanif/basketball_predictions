"""Poisson score-model total head for parlay total legs (adapter, default OFF).

The default total marginal in ``nba.parlay.game_model`` is Normal(a + c * f, sd). This module
offers an alternative: the scaled bivariate-Poisson total from ``PoissonScoreArm``
(``nba.models.winprob_family``): total = k * (Poisson(l1 + l2) + 2 * Poisson(lam3)). Nothing in
the parlay path uses it unless ``TotalsHeadConfig(kind="poisson")`` is passed to
:func:`make_total_head`; wiring it into ``GameCtx`` / ``leg_marginal`` is a change in
``nba/parlay/joint.py`` that is out of scope here (heads expose ``p_over`` and ``z_of`` for that).

PRE-REGISTERED RULE (written 2026-10-08, BEFORE the first run; mirrored in
docs/BACKLOG_SMALL_2026-10-08.md; not edited after results)
------------------------------------------------------------------------------------------
* Rows: the walk-forward rows of ``nba.eval.winprob_family_eval`` (month blocks, MIN_TRAIN 500,
  seasons 2022-2024, raw drift variant) where both the Poisson arm ``poisson_dc`` and the
  Normal game model have a prediction.
* Primary: per-game paired total CRPS delta (Poisson - Normal), 95% bootstrap CI over games,
  upper bound < 0. The Normal incumbent uses its own per-row walk-forward sd (no look-ahead);
  the documented mean-sd figure (10.555 vs 10.647) is reproduced as a check only.
* Total-leg calibration must be NOT WORSE: over/under legs at ``round(mu_t) + d + 0.5`` for
  d in -8..8 (the ``joint_eval.synth_parlays`` rule), kept where the Normal marginal is in
  [0.15, 0.85]; identical (game, threshold) pairs for both heads; outcome = total > threshold.
  Not worse = (point delta log loss <= 0, or <= +0.0005 with CI containing 0) AND ECE (10
  equal-mass bins) rises by no more than 0.005. Game-clustered CI.
* Joint check (descriptive, never decides): 2-leg parlays {total over, total under} x home win
  under a Gaussian copula, rho from earlier-row randomized-PIT normal scores; joint log loss delta.
* KEEP iff the primary and the total-leg calibration rule both hold; otherwise the head stays off.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import numpy as np
from numpy.polynomial.legendre import leggauss
from numpy.typing import NDArray
from scipy import stats

from nba.models.winprob_family import crps_discrete


@dataclass(frozen=True)
class TotalsHeadConfig:
    """``kind="normal"`` (default) keeps the production game-model head."""

    kind: Literal["normal", "poisson"] = "normal"


class TotalHead(Protocol):
    """Marginal of one game's combined points."""

    def mean(self) -> float: ...

    def p_over(self, line: float) -> float: ...

    def cdf(self, x: float) -> float: ...

    def crps(self, y: float) -> float: ...

    def z_of(self, y: float, u: float = 0.5) -> float:
        """Standard-normal score of the outcome (randomized PIT with ``u`` in [0, 1])."""
        ...


@dataclass(frozen=True)
class NormalTotal:
    mu: float
    sd: float

    def mean(self) -> float:
        return self.mu

    def cdf(self, x: float) -> float:
        return float(stats.norm.cdf((x - self.mu) / self.sd))

    def p_over(self, line: float) -> float:
        return 1.0 - self.cdf(line)

    def crps(self, y: float) -> float:
        z = (y - self.mu) / self.sd
        return float(
            self.sd
            * (z * (2 * stats.norm.cdf(z) - 1) + 2 * stats.norm.pdf(z) - 1 / math.sqrt(math.pi))
        )

    def z_of(self, y: float, u: float = 0.5) -> float:
        return (y - self.mu) / self.sd


def total_pmf(
    lam_sum: float, k: float, lam3: float
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """(support in points, pmf) of ``k * (Poisson(lam_sum) + 2 * Poisson(lam3))``."""
    nmax = int(lam_sum + 10.0 * math.sqrt(max(lam_sum, 1.0)) + 10)
    ps = stats.poisson.pmf(np.arange(nmax + 1), lam_sum)
    if lam3 > 0:
        m3 = int(lam3 + 10.0 * math.sqrt(lam3) + 10)
        p3 = stats.poisson.pmf(np.arange(m3 + 1), lam3)
        p3x = np.zeros(2 * m3 + 1)
        p3x[::2] = p3
        pt = np.convolve(ps, p3x)
    else:
        pt = ps
    pt = pt / pt.sum()
    return np.asarray(k * np.arange(len(pt)), dtype=float), np.asarray(pt, dtype=float)


class PoissonTotal:
    """Discrete total distribution for one game (support k * integers)."""

    def __init__(self, l1: float, l2: float, k: float, lam3: float) -> None:
        self.l1, self.l2, self.k, self.lam3 = float(l1), float(l2), float(k), float(lam3)
        self.x, self.pmf = total_pmf(self.l1 + self.l2, self.k, self.lam3)
        self._cdf = np.cumsum(self.pmf)

    def mean(self) -> float:
        return float(np.sum(self.x * self.pmf))

    def sd(self) -> float:
        m = self.mean()
        return float(math.sqrt(np.sum(self.pmf * (self.x - m) ** 2)))

    def cdf(self, x: float) -> float:
        """P(total <= x)."""
        i = int(np.searchsorted(self.x, x + 1e-9, side="right"))
        return 0.0 if i == 0 else float(self._cdf[i - 1])

    def p_over(self, line: float) -> float:
        return 1.0 - self.cdf(line)

    def crps(self, y: float) -> float:
        return float(crps_discrete(self.x, self.pmf, y))

    def z_of(self, y: float, u: float = 0.5) -> float:
        hi = self.cdf(y)
        lo = float(self.pmf[self.x < y - 1e-9].sum())
        lo = min(lo, hi)
        v = lo + u * (hi - lo)
        return float(stats.norm.ppf(min(max(v, 1e-9), 1 - 1e-9)))


def make_total_head(
    cfg: TotalsHeadConfig,
    *,
    mu: float,
    sd: float,
    poisson: tuple[float, float, float, float] | None = None,
) -> TotalHead:
    """Head for one game. ``poisson`` = (l1, l2, k, lam3); required only for kind="poisson"."""
    if cfg.kind == "poisson":
        if poisson is None:
            raise ValueError("poisson parameters required for kind='poisson'")
        return PoissonTotal(*poisson)
    return NormalTotal(mu, sd)


def bvn_cdf(a: NDArray[np.float64], c: NDArray[np.float64], rho: float, n_nodes: int = 96) -> Any:
    """P(Z1 <= a, Z2 <= c) for a standard bivariate normal, vectorised Gauss-Legendre over Z1."""
    a = np.asarray(a, dtype=float)
    c = np.asarray(c, dtype=float)
    r = float(np.clip(rho, -0.999, 0.999))
    nodes, weights = leggauss(n_nodes)
    lo = -9.0
    half = (a - lo) / 2.0  # shape (n,)
    x = half[:, None] * (nodes[None, :] + 1.0) + lo
    inner = stats.norm.cdf((c[:, None] - r * x) / math.sqrt(1.0 - r * r))
    integ = (stats.norm.pdf(x) * inner * weights[None, :]).sum(axis=1) * half
    return np.where(a <= lo, 0.0, integ)
