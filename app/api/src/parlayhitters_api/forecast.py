"""Turn a stored 19-point quantile grid into probabilities at a betting line.

The stored grid is the model's out-of-fold quantile function at taus 0.05, 0.10, ..., 0.95.
Between grid points the CDF is interpolated linearly; outside [q05, q95] it is clamped to
0.05 / 0.95 and the result is flagged `extrapolated` (the tail is not stored, so we do not
invent it). Counts are integers, so a line is converted with a continuity correction:

    over  L  means  X >= floor(L) + 1   ->  1 - F(floor(L) + 0.5)
    under L  means  X <= ceil(L) - 1    ->  F(ceil(L) - 0.5)

For a half-point line both thresholds equal L; for a whole-number line the remainder is the
push mass. The grid comes from a continuous fit, so this is an approximation, labelled as such.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

TAUS: tuple[float, ...] = tuple(round(0.05 * i, 2) for i in range(1, 20))


@dataclass(frozen=True)
class LineProbs:
    p_over: float
    p_under: float
    p_push: float
    extrapolated: bool


def cdf(q: Sequence[float], x: float, taus: Sequence[float] = TAUS) -> tuple[float, bool]:
    """P(X <= x) from quantiles, plus whether x fell outside the stored range."""
    if len(q) != len(taus):
        raise ValueError("quantile grid length does not match taus")
    if any(b < a for a, b in zip(q, q[1:], strict=False)):
        raise ValueError("quantile grid must be non-decreasing")
    if x < q[0]:
        return taus[0], True
    if x >= q[-1]:
        return taus[-1], x > q[-1]
    i = max(k for k in range(len(q) - 1) if q[k] <= x)  # last grid point at or below x
    lo, hi = q[i], q[i + 1]
    frac = (x - lo) / (hi - lo)  # hi > lo here: q[i+1] > x >= q[i]
    return taus[i] + (taus[i + 1] - taus[i]) * frac, False


def line_probs(q: Sequence[float], line: float) -> LineProbs:
    over_t = math.floor(line) + 0.5
    under_t = math.ceil(line) - 0.5
    f_over, ex1 = cdf(q, over_t)
    f_under, ex2 = cdf(q, under_t)
    p_over = 1.0 - f_over
    p_under = f_under
    return LineProbs(
        p_over=p_over,
        p_under=p_under,
        p_push=max(0.0, 1.0 - p_over - p_under),
        extrapolated=ex1 or ex2,
    )


def american_to_raw_prob(price: int) -> float:
    if price == 0 or -100 < price < 100:
        raise ValueError(f"not an American price: {price}")
    return 100 / (price + 100) if price > 0 else -price / (-price + 100)


def devig_over(over_price: int, under_price: int) -> float:
    """Multiplicative de-vig of a two-sided quote; returns P(over) implied by the book."""
    po = american_to_raw_prob(over_price)
    pu = american_to_raw_prob(under_price)
    return po / (po + pu)
