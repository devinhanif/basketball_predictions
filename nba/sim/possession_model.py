"""Rung-3 possession step model: per-possession points distribution.

CLAUDE.md architecture ladder, rung 3: "given the offense's as-of
off-rating and the defense's as-of def-rating (and home/away), produce a
per-possession points distribution". This module supplies the pieces the
Monte Carlo engine (``nba.sim.engine``) samples from:

1. ``BASE_OUTCOME_SUPPORT``/``BASE_OUTCOME_PROBS``: a documented, fixed
   shape for "how many points does a single possession end in" --
   computed once from the full historical ``possessions`` table (see
   module docstring below for the exact numbers) and treated as a global
   basketball-structure constant, analogous to the league-average
   constants in ``nba.features.team_features.FEATURE_DEFAULTS``. It is
   *not* team- or date-specific and is not refit per walk-forward fold,
   so it carries no game-level leakage risk -- the only team- and as-of-
   specific inputs to the sim are the shrunk ``off_rtg_prior``/
   ``def_rtg_prior``/``pace_prior`` features from
   ``nba.features.possession_features``.
2. ``tilt_outcome_probs``: exponential tilting of that fixed shape so its
   *mean* matches a per-matchup target points-per-possession, while
   keeping (most of) the realistic variance/skew of real NBA possessions
   instead of e.g. naively rescaling probabilities (which can go negative
   or lose the discrete shape entirely).

Empirical base shape (``possessions`` table, 1,049,740 rows, 2022-23
through 2025-26 seasons, grouped by ``pts``):

    pts:   0        1       2        3        4      5     6    7   8
    n:  510735    36526  344205   155986     1794   397    90    4   3
    p:  0.4866   0.0348  0.3279   0.1486   0.0017  0.0004 ~0    ~0  ~0

Mean = 1.146 pts/possession, matching ``LEAGUE_AVG_PPP_DEFAULT``.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import brentq

#: See module docstring for provenance. Values are frequencies, not
#: probabilities; normalized once at import time.
_BASE_OUTCOME_COUNTS: dict[int, int] = {
    0: 510735,
    1: 36526,
    2: 344205,
    3: 155986,
    4: 1794,
    5: 397,
    6: 90,
    7: 4,
    8: 3,
}

BASE_OUTCOME_SUPPORT: np.ndarray = np.array(sorted(_BASE_OUTCOME_COUNTS), dtype=float)
_counts = np.array([_BASE_OUTCOME_COUNTS[int(x)] for x in BASE_OUTCOME_SUPPORT], dtype=float)
BASE_OUTCOME_PROBS: np.ndarray = _counts / _counts.sum()

#: Mean of the base (untilted) shape -- should equal
#: ``nba.features.possession_features.LEAGUE_AVG_PPP_DEFAULT`` up to
#: rounding; both are derived from the same historical possessions table.
BASE_MEAN_PPP: float = float(np.sum(BASE_OUTCOME_SUPPORT * BASE_OUTCOME_PROBS))

#: Exponential-tilt parameter search bounds. Past this, the tilted
#: distribution is essentially a point mass at the support's min/max;
#: matchup-level target PPPs (roughly 0.7-1.7 in practice) never get
#: close to needing a wider range.
_THETA_BOUNDS = (-5.0, 5.0)


def tilted_mean(theta: float, support: np.ndarray, base_probs: np.ndarray) -> float:
    """Mean of the exponentially-tilted distribution ``p(x) ~ base(x) * exp(theta*x)``."""
    weights = base_probs * np.exp(theta * support)
    weights = weights / weights.sum()
    return float(np.sum(support * weights))


def tilt_outcome_probs(
    target_mean: float,
    support: np.ndarray | None = None,
    base_probs: np.ndarray | None = None,
) -> np.ndarray:
    """Exponentially tilt ``base_probs`` over ``support`` to hit ``target_mean`` exactly.

    Exponential ("Esscher") tilting: ``p_theta(x) ~ base(x) * exp(theta * x)``,
    normalized. As ``theta`` increases, probability mass shifts toward
    higher-point outcomes (and vice versa for negative ``theta``), while
    every outcome that had positive base probability keeps positive
    probability -- unlike a naive linear rescale, this can never produce a
    negative probability. ``theta`` is solved by bisection
    (:func:`scipy.optimize.brentq`) so the tilted mean matches
    ``target_mean`` to numerical precision.

    Clips ``target_mean`` to just inside ``(support.min(), support.max())``
    -- an exact mean at or beyond the support's edge would require
    ``theta`` to diverge to +/-infinity (a degenerate point mass), which
    is never a realistic per-possession target anyway.
    """
    support = BASE_OUTCOME_SUPPORT if support is None else np.asarray(support, dtype=float)
    base_probs = BASE_OUTCOME_PROBS if base_probs is None else np.asarray(base_probs, dtype=float)
    lo_mean, hi_mean = float(support.min()), float(support.max())
    eps = 1e-6 * (hi_mean - lo_mean)
    clamped_target = float(np.clip(target_mean, lo_mean + eps, hi_mean - eps))

    def _f(theta: float) -> float:
        return tilted_mean(theta, support, base_probs) - clamped_target

    lo_theta, hi_theta = _THETA_BOUNDS
    f_lo, f_hi = _f(lo_theta), _f(hi_theta)
    if f_lo > 0 or f_hi < 0:
        # Target is outside what the bounded search range can reach (only
        # possible for extreme targets near the support edges after
        # clamping); fall back to whichever bound gets closest rather than
        # letting brentq raise.
        theta = lo_theta if abs(f_lo) < abs(f_hi) else hi_theta
    else:
        theta = brentq(_f, lo_theta, hi_theta, xtol=1e-10)

    weights = base_probs * np.exp(theta * support)
    probs = weights / weights.sum()
    return np.asarray(probs, dtype=float)
