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
from typing import Any

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


TAIL_EPS = 1e-3  # the stored table is complete when its last P(Y >= k) is at most this


def surv_complete(counts: Sequence[int], n: int) -> bool:
    """True when the stored table reaches the tail: its last entry is within TAIL_EPS of zero."""
    return n > 0 and len(counts) > 0 and counts[-1] / n <= TAIL_EPS


def _p_ge(counts: Sequence[int], n: int, k: int) -> float:
    """P(Y >= k) from the stored table c_k / n (k = 1..K); 1 at k <= 0, 0 beyond the table."""
    if k <= 0:
        return 1.0
    return counts[k - 1] / n if k - 1 < len(counts) else 0.0


def surv_probs(counts: Sequence[int], n: int, line: float) -> LineProbs:
    """Exact probabilities on integer support from the stored full-support table.

    over L means Y >= floor(L) + 1; under L means Y <= ceil(L) - 1; the rest is the push mass.
    Nothing is interpolated, so nothing is flagged as extrapolated.
    """
    p_over = _p_ge(counts, n, math.floor(line) + 1)
    p_under = 1.0 - _p_ge(counts, n, math.ceil(line))
    return LineProbs(
        p_over=p_over,
        p_under=p_under,
        p_push=max(0.0, 1.0 - p_over - p_under),
        extrapolated=False,
    )


def forecast_probs(forecast: dict[str, Any], line: float) -> LineProbs:
    """P(over / under / push) at a line from a stored forecast: the exact full-support table when
    the export carries a complete one, else the interpolated quantile grid (fixtures)."""
    surv = forecast.get("surv")
    if surv and surv_complete(surv["c"], int(surv["n"])):
        return surv_probs(surv["c"], int(surv["n"]), line)
    return line_probs([float(v) for v in forecast["q"]], line)


def forecast_method(forecast: dict[str, Any]) -> str:
    surv = forecast.get("surv")
    if surv and surv_complete(surv["c"], int(surv["n"])):
        return (
            f"Read from the stored full-support table (denominator {int(surv['n'])}): the share "
            "of the stored forecast above the line. Exact on whole-number counts."
        )
    return (
        "Linear interpolation of the stored 19 quantiles (0.05..0.95) with a continuity "
        "correction for integer counts. Approximate; tails beyond 5%/95% are clamped."
    )


def reference_line(forecast: dict[str, Any]) -> float:
    """A half-point starting line at the middle of the stored forecast (its 50% quantile).

    It is a place to start the slider, never a sportsbook line."""
    return math.floor(float(forecast["q"][9])) + 0.5


def american_to_raw_prob(price: int) -> float:
    if price == 0 or -100 < price < 100:
        raise ValueError(f"not an American price: {price}")
    return 100 / (price + 100) if price > 0 else -price / (-price + 100)


def devig_over(over_price: int, under_price: int) -> float:
    """Multiplicative de-vig of a two-sided quote; returns P(over) implied by the book."""
    po = american_to_raw_prob(over_price)
    pu = american_to_raw_prob(under_price)
    return po / (po + pu)
