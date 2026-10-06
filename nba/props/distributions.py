"""Distribution families for player props (CLAUDE.md "Phase 2 -- Props").

Every family below exposes the same small, duck-typed surface so
``nba/props/metrics.py`` and the report layer never need to branch on which
family a stat used:

    mean() -> float
    cdf(x) -> float in [0, 1]
    sf(x)  -> float in [0, 1]           (= 1 - cdf(x))
    p_ge(n) -> float in [0, 1]          P(stat >= n)
    ppf(q) -> float                     quantile function, q in (0, 1)
    family -> str
    params() -> dict[str, float]        JSON-serializable, for prop_predictions.dist_params

Per CLAUDE.md: "points ~ Normal or Gamma-shaped; rebounds/assists/3PM ~
Negative Binomial ... Check zero-inflation for 3PM". ``NegBinDist`` and
``ZeroInflatedNegBinDist`` cover the count stats; ``NormalDist``/``GammaDist``
cover points; ``MinutesHurdleDist`` (the DNP branch) is the mixture used by
``nba/props/minutes.py``.

All ``p_ge``/``cdf``/``ppf`` implementations are property-tested in
``tests/props/test_distributions.py`` for exactly the invariants CLAUDE.md's
CI/CD section requires: CDF monotone non-decreasing, probabilities in
[0, 1], P(stat >= N) non-increasing in N, and q10 <= q50 <= q90.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
from scipy import stats

#: Floor used everywhere a scale/shape/std parameter must stay strictly
#: positive (scipy distributions blow up or return NaN at exactly 0).
_EPS = 1e-6


class Distribution(Protocol):
    family: str

    def mean(self) -> float: ...

    def cdf(self, x: float) -> float: ...

    def sf(self, x: float) -> float: ...

    def p_ge(self, n: float) -> float: ...

    def ppf(self, q: float) -> float: ...

    def params(self) -> dict[str, float]: ...


@dataclass
class NormalDist:
    """Continuous family for points ("Normal or Gamma-shaped" per CLAUDE.md).

    Used when the compound frequency-severity moments imply a
    small-variance-relative-to-mean regime where a Gamma fit would be
    numerically degenerate (see ``nba.props.stat_models``).
    """

    mean_: float
    std: float
    family: str = "normal"

    def __post_init__(self) -> None:
        self.std = max(float(self.std), _EPS)
        self.mean_ = float(self.mean_)

    def mean(self) -> float:
        return self.mean_

    def cdf(self, x: float) -> float:
        return float(stats.norm.cdf(x, loc=self.mean_, scale=self.std))

    def sf(self, x: float) -> float:
        return float(stats.norm.sf(x, loc=self.mean_, scale=self.std))

    def p_ge(self, n: float) -> float:
        # Continuity correction: approximate P(integer-valued stat >= n) by
        # the continuous survival function evaluated half a unit below n,
        # a standard discrete-via-continuous approximation.
        return float(stats.norm.sf(n - 0.5, loc=self.mean_, scale=self.std))

    def ppf(self, q: float) -> float:
        return float(stats.norm.ppf(q, loc=self.mean_, scale=self.std))

    def params(self) -> dict[str, float]:
        return {"mean": self.mean_, "std": self.std}


@dataclass
class GammaDist:
    """Gamma(shape, scale) -- the default "Gamma-shaped" points family.

    Support is ``[0, inf)``, matching a non-negative stat, and right-skewed,
    matching scoring distributions better than a Normal in most regimes.
    """

    shape: float
    scale: float
    family: str = "gamma"

    def __post_init__(self) -> None:
        self.shape = max(float(self.shape), _EPS)
        self.scale = max(float(self.scale), _EPS)

    def mean(self) -> float:
        return float(self.shape * self.scale)

    def cdf(self, x: float) -> float:
        return float(stats.gamma.cdf(x, a=self.shape, scale=self.scale))

    def sf(self, x: float) -> float:
        return float(stats.gamma.sf(x, a=self.shape, scale=self.scale))

    def p_ge(self, n: float) -> float:
        return float(stats.gamma.sf(max(n - 0.5, 0.0), a=self.shape, scale=self.scale))

    def ppf(self, q: float) -> float:
        return float(stats.gamma.ppf(q, a=self.shape, scale=self.scale))

    def params(self) -> dict[str, float]:
        return {"shape": self.shape, "scale": self.scale}


def gamma_from_moments(mean: float, var: float) -> GammaDist:
    """Method-of-moments Gamma fit: shape = mean^2/var, scale = var/mean.

    Degenerate inputs (mean <= 0 or var <= 0, e.g. a player with zero
    projected minutes) fall back to a tiny-variance Gamma concentrated at
    ``max(mean, 0)`` rather than raising -- CLAUDE.md requires graceful,
    NaN-free degradation, not a crash, when the data is too sparse.
    """
    mean = max(float(mean), 0.0)
    if mean <= _EPS or var <= _EPS:
        scale = _EPS
        shape = max(mean / scale, _EPS)
        return GammaDist(shape=shape, scale=scale)
    scale = var / mean
    shape = mean / scale
    return GammaDist(shape=shape, scale=scale)


@dataclass
class NegBinDist:
    """NegBin(mean=mu, dispersion=alpha) in the "NB2" parametrization.

    ``Var = mu + alpha * mu^2``. Internally converted to scipy's
    ``(n, p)`` parametrization: ``n = 1/alpha`` (constant), ``p = 1 / (1 +
    alpha*mu)``. Used for rebounds/assists/3PM per CLAUDE.md.
    """

    mu: float
    alpha: float
    family: str = "negbin"

    def __post_init__(self) -> None:
        self.mu = max(float(self.mu), 0.0)
        self.alpha = max(float(self.alpha), _EPS)

    def _np(self) -> tuple[float, float]:
        n = 1.0 / self.alpha
        p = 1.0 / (1.0 + self.alpha * self.mu)
        return n, p

    def mean(self) -> float:
        return self.mu

    def cdf(self, x: float) -> float:
        n, p = self._np()
        return float(stats.nbinom.cdf(x, n, p))

    def sf(self, x: float) -> float:
        n, p = self._np()
        return float(stats.nbinom.sf(x, n, p))

    def p_ge(self, n_thresh: float) -> float:
        """P(X >= n_thresh); X is a non-negative integer count."""
        if n_thresh <= 0:
            return 1.0
        n, p = self._np()
        return float(stats.nbinom.sf(np.ceil(n_thresh) - 1, n, p))

    def ppf(self, q: float) -> float:
        n, p = self._np()
        return float(stats.nbinom.ppf(q, n, p))

    def params(self) -> dict[str, float]:
        return {"mu": self.mu, "alpha": self.alpha}


def negbin_from_moments(mean: float, var: float) -> NegBinDist:
    """Method-of-moments NegBin fit from a target mean/variance pair.

    If ``var <= mean`` (under-dispersed relative to Poisson -- can happen
    with small-sample shrinkage), ``alpha`` is floored at a tiny positive
    value so the distribution stays a valid (near-Poisson) NegBin instead
    of raising.
    """
    mean = max(float(mean), 0.0)
    if mean <= _EPS:
        return NegBinDist(mu=0.0, alpha=_EPS)
    alpha = (var - mean) / (mean * mean)
    return NegBinDist(mu=mean, alpha=max(alpha, _EPS))


@dataclass
class ZeroInflatedNegBinDist:
    """Zero-inflated NegBin: extra point mass ``pi`` at zero.

    ``F(x) = pi + (1-pi) * F_base(x)`` for ``x >= 0``. CLAUDE.md: "Check
    zero-inflation for 3PM/blocks/steals" -- ``pi`` is fit by
    :func:`nba.props.stat_models.fit_zero_inflation`.
    """

    pi: float
    base: NegBinDist
    family: str = "zinb"

    def __post_init__(self) -> None:
        self.pi = float(np.clip(self.pi, 0.0, 1.0))

    def mean(self) -> float:
        return (1.0 - self.pi) * self.base.mean()

    def cdf(self, x: float) -> float:
        if x < 0:
            return 0.0
        return float(self.pi + (1.0 - self.pi) * self.base.cdf(x))

    def sf(self, x: float) -> float:
        return 1.0 - self.cdf(x)

    def p_ge(self, n_thresh: float) -> float:
        if n_thresh <= 0:
            return 1.0
        return float((1.0 - self.pi) * self.base.sf(np.ceil(n_thresh) - 1))

    def ppf(self, q: float) -> float:
        if q <= self.pi:
            return 0.0
        base_q = (q - self.pi) / (1.0 - self.pi)
        base_q = float(np.clip(base_q, 0.0, 1.0))
        return self.base.ppf(base_q)

    def params(self) -> dict[str, float]:
        return {"pi": self.pi, **self.base.params()}


@dataclass
class MinutesHurdleDist:
    """Minutes distribution: hurdle mixture of a DNP point mass and a
    truncated-Normal "minutes given plays" branch.

    ``P(minutes = 0) = 1 - p_play``; with probability ``p_play``, minutes
    is drawn from ``Normal(mu, sigma)`` truncated to ``[0, max_minutes]``.
    This is the minutes model's output type (see ``nba.props.minutes``).
    """

    p_play: float
    mu: float
    sigma: float
    max_minutes: float = 48.0
    family: str = "minutes_hurdle"

    def __post_init__(self) -> None:
        self.p_play = float(np.clip(self.p_play, 0.0, 1.0))
        self.sigma = max(float(self.sigma), _EPS)
        self.mu = float(np.clip(self.mu, 0.0, self.max_minutes))

    def _trunc_bounds(self) -> tuple[float, float]:
        a = (0.0 - self.mu) / self.sigma
        b = (self.max_minutes - self.mu) / self.sigma
        return a, b

    def _trunc_cdf(self, x: float) -> float:
        a, b = self._trunc_bounds()
        return float(stats.truncnorm.cdf(x, a, b, loc=self.mu, scale=self.sigma))

    def _trunc_sf(self, x: float) -> float:
        a, b = self._trunc_bounds()
        return float(stats.truncnorm.sf(x, a, b, loc=self.mu, scale=self.sigma))

    def _trunc_ppf(self, q: float) -> float:
        a, b = self._trunc_bounds()
        return float(stats.truncnorm.ppf(q, a, b, loc=self.mu, scale=self.sigma))

    def _trunc_mean(self) -> float:
        a, b = self._trunc_bounds()
        return float(stats.truncnorm.mean(a, b, loc=self.mu, scale=self.sigma))

    def _trunc_var(self) -> float:
        a, b = self._trunc_bounds()
        return float(stats.truncnorm.var(a, b, loc=self.mu, scale=self.sigma))

    def mean(self) -> float:
        return self.p_play * self._trunc_mean()

    def var(self) -> float:
        """Mixture variance: ``p*Var[X|plays] + p*(1-p)*E[X|plays]^2``."""
        trunc_mean = self._trunc_mean()
        trunc_var = self._trunc_var()
        return float(self.p_play * trunc_var + self.p_play * (1.0 - self.p_play) * trunc_mean**2)

    def cdf(self, x: float) -> float:
        if x < 0:
            return 0.0
        return float((1.0 - self.p_play) + self.p_play * self._trunc_cdf(x))

    def sf(self, x: float) -> float:
        return 1.0 - self.cdf(x)

    def p_ge(self, n_thresh: float) -> float:
        if n_thresh <= 0:
            return 1.0
        return float(self.p_play * self._trunc_sf(n_thresh))

    def ppf(self, q: float) -> float:
        dnp_mass = 1.0 - self.p_play
        if q <= dnp_mass:
            return 0.0
        inner_q = float(np.clip((q - dnp_mass) / self.p_play, 0.0, 1.0))
        return self._trunc_ppf(inner_q)

    def params(self) -> dict[str, float]:
        return {"p_play": self.p_play, "mu": self.mu, "sigma": self.sigma}
