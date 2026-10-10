"""Line-level (threshold) routing: P(stat >= N) events from the 19-level grids.

Each (player-game, line N) becomes one binary row scored by log loss, so the same
gates / walk-forward / evaluator used for ``win`` apply (target ``thr_<stat>``).

``grid_exceed_prob`` reads the survival probability off the quantile grid by
linear interpolation of the CDF between the 19 levels and clamps to
``[CLAMP, 1 - CLAMP]``. This is a coarse, candidate-independent approximation
(identical for every candidate, so comparisons are fair; absolute log losses are
NOT comparable to the 199-level / exact-family ``tll`` of the exp-2 report). Lines
far in the tails are dropped: a row is kept only when the equal-weight pooled
probability lies in ``[P_LO, P_HI]`` (a function of as-of predictions, not of the
outcome).

Line region is defined relative to the player's own distribution:
``line_z = (N - 0.5 - median) / sigma`` with median/sigma from the equal-weight
pooled grid (sigma = IQR / 1.349), regions ``below`` (z < -0.5), ``near``,
``above`` (z > 0.5).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nba.props.config import THRESHOLDS
from nba.stack.oof import TAUS
from research.stack.data import StackData

CLAMP = 0.025
P_LO, P_HI = 0.05, 0.95
REGION_CUT = 0.5
LADDERS: dict[str, list[int]] = {k: list(v) for k, v in THRESHOLDS.items()}


def grid_cdf(q: np.ndarray, x: np.ndarray) -> np.ndarray:
    """CDF at ``x`` (n,) from grids ``q`` (n, 19) at ``TAUS``; clamped to [CLAMP, 1-CLAMP]."""
    q = np.asarray(q, dtype=float)
    x = np.asarray(x, dtype=float)
    n, m = q.shape
    below = (q < x[:, None]).sum(axis=1)  # grid points strictly below x
    lo_i = np.clip(below - 1, 0, m - 2)
    rows = np.arange(n)
    q0, q1 = q[rows, lo_i], q[rows, lo_i + 1]
    t0, t1 = TAUS[lo_i], TAUS[lo_i + 1]
    den = q1 - q0
    frac = np.where(den > 0, (x - q0) / np.where(den > 0, den, 1.0), 1.0)
    f = t0 + np.clip(frac, 0.0, 1.0) * (t1 - t0)
    f = np.where(below == 0, CLAMP, f)
    f = np.where(below >= m, 1.0 - CLAMP, f)
    return np.asarray(np.clip(f, CLAMP, 1.0 - CLAMP))


def grid_exceed_prob(q: np.ndarray, line: np.ndarray) -> np.ndarray:
    """P(Y >= N) for integer-valued Y: ``1 - F(N - 0.5)``."""
    return np.asarray(1.0 - grid_cdf(q, np.asarray(line, dtype=float) - 0.5))


def line_region(z: np.ndarray) -> np.ndarray:
    return np.where(z < -REGION_CUT, "below", np.where(z > REGION_CUT, "above", "near"))


def expand_thresholds(data: StackData, stat: str) -> tuple[StackData, np.ndarray]:
    """Props ``StackData`` -> binary threshold ``StackData`` (target ``thr_<stat>``).

    Returns the expanded data and ``src`` (index of each expanded row in ``data``)
    so that slice labels of the source rows can be carried over. The context gains
    ``line_z``, ``line_n`` and ``line_region``; ``line_region`` is also a gate feature.
    """
    if data.target != stat:
        raise ValueError(f"data.target {data.target!r} != stat {stat!r}")
    ladder = LADDERS[stat]
    k = len(data.candidates)
    pooled = data.cand.mean(axis=1)  # equal weights, (n, 19)
    med = pooled[:, 9]
    sigma = np.maximum((pooled[:, 14] - pooled[:, 4]) / 1.349, 1e-3)  # q75 - q25
    parts_src: list[np.ndarray] = []
    parts_line: list[np.ndarray] = []
    parts_p: list[np.ndarray] = []
    parts_z: list[np.ndarray] = []
    for n_line in ladder:
        line = np.full(data.n, float(n_line))
        p_pool = grid_exceed_prob(pooled, line)
        keep = (p_pool >= P_LO) & (p_pool <= P_HI)
        src = np.flatnonzero(keep)
        if not len(src):
            continue
        pk = np.column_stack([grid_exceed_prob(data.cand[src, j], line[src]) for j in range(k)])
        parts_src.append(src)
        parts_line.append(line[src])
        parts_p.append(pk)
        parts_z.append((line[src] - 0.5 - med[src]) / sigma[src])
    if not parts_src:
        raise ValueError("no threshold rows survive the informative-line filter")
    src = np.concatenate(parts_src)
    line = np.concatenate(parts_line)
    p = np.vstack(parts_p)
    z = np.concatenate(parts_z)
    order = np.argsort(data.dates[src], kind="stable")
    src, line, p, z = src[order], line[order], p[order], z[order]
    ctx = data.context.iloc[src].reset_index(drop=True).copy()
    ctx["line_z"] = z
    ctx["line_n"] = line
    ctx["line_region"] = line_region(z)
    keys = data.keys.iloc[src].reset_index(drop=True)
    y = (data.y[src] >= line).astype(float)
    return (
        StackData(f"thr_{stat}", list(data.candidates), keys, y, p, ctx),
        src,
    )


def region_slices(ctx: pd.DataFrame) -> dict[str, np.ndarray]:
    return {"line_region": ctx["line_region"].astype(str).to_numpy()}
