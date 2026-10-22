"""Cluster-by-slate-date bootstrap for pnl per $ staked (ratio of sums), at a stated alpha."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Ratio:
    n_positions: int
    n_dates: int
    cost: float
    pnl: float
    per_dollar: float | None
    ci: tuple[float, float] | None


def cluster_ratio(
    dates: list[str], pnl: list[float], cost: list[float], *, reps: int, seed: int, alpha: float,
) -> Ratio:  # fmt: skip
    """pnl / cost over positions; two-sided (1 - alpha) CI by resampling whole slate dates."""
    n = len(pnl)
    if n == 0:
        return Ratio(0, 0, 0.0, 0.0, None, None)
    uniq = sorted(set(dates))
    idx = {d: i for i, d in enumerate(uniq)}
    k = len(uniq)
    cs = np.zeros(k)
    ps = np.zeros(k)
    for d, p, c in zip(dates, pnl, cost, strict=True):
        cs[idx[d]] += c
        ps[idx[d]] += p
    tc, tp = float(cs.sum()), float(ps.sum())
    point = tp / tc if tc > 0 else None
    ci = None
    if k >= 2 and tc > 0:
        rng = np.random.default_rng(seed)
        pick = rng.integers(0, k, size=(reps, k))
        den = cs[pick].sum(axis=1)
        ok = den > 0
        est = ps[pick].sum(axis=1)[ok] / den[ok]
        ci = (float(np.quantile(est, alpha / 2)), float(np.quantile(est, 1 - alpha / 2)))
    return Ratio(n, k, tc, tp, point, ci)


def concentration(dates: list[str], pnl: list[float]) -> float | None:
    """Share of total |P&L| coming from the single largest-|P&L| date: max_d |P_d| / sum_d |P_d|."""
    by: dict[str, float] = {}
    for d, p in zip(dates, pnl, strict=True):
        by[d] = by.get(d, 0.0) + p
    tot = sum(abs(v) for v in by.values())
    return max(abs(v) for v in by.values()) / tot if tot > 0 else None
