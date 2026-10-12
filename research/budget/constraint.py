"""The team-minutes budget constraint (TEAM_BUDGET): rake to the budget, joint draws.

Why raking and not a softmax head or a Dirichlet likelihood: the constraint is ONE linear equation
per team-game, ``sum_i e_i = B``. Multiplicative raking (scale every unconditional expected-minutes
value by one factor, then cap at the per-player maximum and re-spread the excess over the uncapped
players) is the KL projection of the production forecasts onto that equation, has no parameters to
tune, is exact to 1e-9, and leaves the ordering of players unchanged. A learned softmax would add a
model; it enters only in arm T2 (a share model whose output is raked by this same function).
"""

from __future__ import annotations

import numpy as np

REG_MINUTES = 240.0
OT_MINUTES = 25.0
MAX_PLAYER_MINUTES = 48.0
MAX_ITER = 50


def team_budget(ot_rate: float | np.ndarray) -> np.ndarray:
    """Expected team minutes ``240 + 25 * P(OT)`` (``ot_rate`` as-of, in [0, 1])."""
    r = np.asarray(ot_rate, dtype=float)
    if np.any((r < 0.0) | (r > 1.0)):
        raise ValueError("ot_rate must lie in [0, 1]")
    return REG_MINUTES + OT_MINUTES * r


def rake_to_budget(
    e: np.ndarray, budget: float, cap: float | np.ndarray = MAX_PLAYER_MINUTES
) -> np.ndarray:
    """Scale non-negative ``e`` (expected unconditional minutes per roster player) so it sums to
    ``budget`` with ``e_i <= cap_i``. Iterative proportional scaling with caps: players that hit the
    cap are frozen and the remainder is raked over the rest. Infeasible budgets (sum of caps below
    ``budget``) raise; an all-zero ``e`` is spread uniformly over the caps' proportions."""
    x = np.asarray(e, dtype=float)
    if x.ndim != 1 or np.any(x < 0) or not np.all(np.isfinite(x)):
        raise ValueError("e must be a finite non-negative vector")
    c = np.broadcast_to(np.asarray(cap, dtype=float), x.shape).copy()
    if budget < 0:
        raise ValueError("budget must be non-negative")
    if c.sum() < budget - 1e-9:
        raise ValueError("budget exceeds the sum of per-player caps")
    if budget == 0.0:
        return np.zeros_like(x)
    base = x if x.sum() > 0 else np.where(np.isfinite(c), c, 1.0)
    out = np.zeros_like(x)
    free = np.ones(x.shape, dtype=bool)
    remaining = float(budget)
    for _ in range(MAX_ITER):
        mass = base[free].sum()
        if mass <= 0.0:  # every free player has zero weight: use caps as weights
            w = np.where(free, c, 0.0)
            mass = w.sum()
        else:
            w = np.where(free, base, 0.0)
        trial = remaining * w / mass
        over = free & (trial > c + 1e-12)
        if not over.any():
            out[free] = trial[free]
            return out
        out[over] = c[over]
        remaining -= float(c[over].sum())
        free &= ~over
        if not free.any():
            break
    return out


def rake_groups(
    e: np.ndarray, group: np.ndarray, budget: np.ndarray, cap: float = MAX_PLAYER_MINUTES
) -> np.ndarray:
    """``rake_to_budget`` per team-game: ``group`` is an integer id per row and ``budget`` one
    value per id (indexed by the id, so ids are 0..G-1)."""
    out = np.empty_like(np.asarray(e, dtype=float))
    order = np.argsort(group, kind="stable")
    g_sorted = group[order]
    cuts = np.flatnonzero(np.diff(g_sorted)) + 1
    for idx in np.split(order, cuts):
        out[idx] = rake_to_budget(np.asarray(e)[idx], float(budget[group[idx[0]]]), cap)
    return out


def joint_minutes_draws(
    e: np.ndarray, budget: float, kappa: float, n_draws: int, rng: np.random.Generator
) -> np.ndarray:
    """Joint draws of the roster's minutes that sum to ``budget`` exactly: Dirichlet over the raked
    shares with concentration ``kappa`` (the sum constraint is structural, not hoped for). Shape
    ``[n_draws, n_players]``. Players with zero mean get exactly zero."""
    r = rake_to_budget(e, budget, cap=np.inf)
    pos = r > 0
    out = np.zeros((n_draws, len(r)))
    if pos.sum() == 1:
        out[:, np.flatnonzero(pos)[0]] = budget
        return out
    alpha = kappa * r[pos] / budget
    out[:, pos] = rng.dirichlet(alpha, size=n_draws) * budget
    return out
