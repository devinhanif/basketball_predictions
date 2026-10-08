"""Distribution from the 19-point quantile grid emitted by the context-residual props model.

The grid holds quantiles at tau = 0.05, 0.10, ..., 0.95 of the stat CONDITIONAL on playing.
The CDF is the piecewise-linear interpolation of those knots, with the two tails extended by
one grid step (tau 0 and tau 1) at the end slopes; the lower end is floored at 0 (counts).
``p_ge(t) = P(Y >= t) = 1 - F(t - 0.5)`` matches ``nba.props.forward`` (integer stats).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

GRID_TAUS: NDArray[np.float64] = np.round(0.05 * np.arange(1, 20), 2)
_P_MIN = 1e-4


def _knots(q: NDArray[np.float64]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    lo = max(0.0, float(q[0] - (q[1] - q[0])))
    hi = float(q[-1] + (q[-1] - q[-2]))
    xs = np.concatenate([[lo], q, [hi]])
    ts = np.concatenate([[0.0], GRID_TAUS, [1.0]])
    return xs, ts


@dataclass(frozen=True)
class QuantileGridDist:
    q: tuple[float, ...]

    @staticmethod
    def from_grid(q: Sequence[float]) -> QuantileGridDist:
        if len(q) != len(GRID_TAUS):
            raise ValueError(f"expected {len(GRID_TAUS)} quantiles, got {len(q)}")
        return QuantileGridDist(tuple(float(v) for v in np.maximum.accumulate(np.asarray(q))))

    def cdf(self, x: float) -> float:
        xs, ts = _knots(np.asarray(self.q, dtype=float))
        if x < xs[0]:
            return 0.0
        if x >= xs[-1]:
            return 1.0
        return float(np.interp(x, xs, ts))

    def p_ge(self, t: float) -> float:
        """P(Y >= t), clipped away from 0 and 1."""
        return min(max(1.0 - self.cdf(t - 0.5), _P_MIN), 1.0 - _P_MIN)

    def pit_bounds(self, y: float) -> tuple[float, float]:
        """(F(y - 0.5), F(y + 0.5)) for a randomized PIT of an integer outcome."""
        return self.cdf(y - 0.5), self.cdf(y + 0.5)
