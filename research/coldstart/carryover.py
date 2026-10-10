"""Cold-start method 4: time-decay carryover for returning players.

Blend last season's observed rate with a regression-to-the-mean prior
(e.g. the position/archetype mean) and an age curve. ``decay_weight`` and
the age-curve shape are tunable by backtest; see
``research.coldstart.config.ColdStartConfig``.
"""

from __future__ import annotations

import numpy as np


def age_curve_multiplier(
    age: np.ndarray | float,
    peak_age: float = 27.0,
    width: float = 9.0,
) -> np.ndarray:
    """Unimodal multiplier peaking at ``peak_age``, per CLAUDE.md's "age curve".

    Returns 1.0 at ``peak_age`` and falls off smoothly (quadratically) on
    both sides, floored at 0.5 so very young/old ages never zero out a
    player's carried-over rate outright. Monotonically increasing for
    ``age < peak_age`` and monotonically decreasing for ``age > peak_age``.
    """
    age_a = np.asarray(age, dtype=float)
    z = (age_a - peak_age) / width
    raw = 1.0 - z * z * 0.5
    return np.asarray(np.clip(raw, 0.5, 1.0), dtype=float)


def carryover_blend(
    last_season_rate: np.ndarray | float,
    prior_mean: np.ndarray | float,
    decay_weight: float,
    age: np.ndarray | float | None = None,
    peak_age: float = 27.0,
    age_curve_width: float = 9.0,
) -> np.ndarray:
    """Blend a returning player's last-season rate with a prior mean.

    ``decay_weight`` in ``[0, 1]`` is the regression-to-the-mean weight: 0
    means "trust last season's rate completely" (after the age-curve
    adjustment), 1 means "fully regress to the prior mean" (new-season
    uncertainty dominates). Monotonic in ``decay_weight`` by construction:
    increasing it moves the blend strictly toward ``prior_mean``.

    The age curve only scales the *last-season* component (a player's own
    observed rate, adjusted for expected age-related change) -- it is not
    applied to the prior mean, which is already a population average.
    """
    last_a = np.asarray(last_season_rate, dtype=float)
    prior_a = np.asarray(prior_mean, dtype=float)
    w = float(np.clip(decay_weight, 0.0, 1.0))

    if age is not None:
        mult = age_curve_multiplier(age, peak_age=peak_age, width=age_curve_width)
        last_adjusted = last_a * mult
    else:
        last_adjusted = last_a

    return np.asarray((1.0 - w) * last_adjusted + w * prior_a, dtype=float)
