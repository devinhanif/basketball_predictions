"""Cluster bootstrap and the decision interval (LONGSHOT Section 6, UNDERS Section 6).

Percentile bootstrap, B = 10,000, ``numpy.random.default_rng(2026)`` re-seeded at each read. Two
clusterings (whole games, whole ET dates). The decision CI is the WIDER of the two. One-sided level
alpha (0.005 at Read 1, 0.02 at Read 2) means the decision interval is the matching two-sided
percentile interval ``[alpha, 1 - alpha]``.

Resampling works on per-cluster sufficient statistics (one row per cluster), so a replicate is a sum
of
resampled rows. Order of rng draws is fixed (games first, then dates) so a read is reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

CHUNK = 250


def cluster_stats(cluster_idx: np.ndarray, columns: np.ndarray, n_clusters: int) -> np.ndarray:
    """Sum ``columns`` (N x k) within clusters; returns (n_clusters x k)."""
    k = columns.shape[1]
    out = np.zeros((n_clusters, k))
    for j in range(k):
        out[:, j] = np.bincount(cluster_idx, weights=columns[:, j], minlength=n_clusters)
    return out


def bootstrap_sums(
    per_cluster: np.ndarray, b: int, rng: np.random.Generator, chunk: int = CHUNK
) -> np.ndarray:
    """B replicate sums of resampled clusters; shape (b, k)."""
    g = per_cluster.shape[0]
    out = np.empty((b, per_cluster.shape[1]))
    done = 0
    while done < b:
        m = min(chunk, b - done)
        idx = rng.integers(0, g, size=(m, g))
        out[done : done + m] = per_cluster[idx].sum(axis=1)
        done += m
    return out


def ratio_mean(sums: np.ndarray) -> np.ndarray:
    """Mean from (sum, n) columns (0, 1)."""
    return np.asarray(sums[:, 0] / sums[:, 1])


def ols_from_sums(sums: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """OLS ``y = a + b x`` from columns (n, Sx, Sy, Sxx, Sxy); returns (a, b)."""
    n, sx, sy, sxx, sxy = (sums[:, i] for i in range(5))
    den = n * sxx - sx * sx
    with np.errstate(divide="ignore", invalid="ignore"):
        b = (n * sxy - sx * sy) / den
        a = (sy - b * sx) / n
    return a, b


@dataclass(frozen=True)
class Interval:
    lo: float
    hi: float
    clustering: str  # "game" | "date"

    @property
    def width(self) -> float:
        return self.hi - self.lo


def percentile_interval(dist: np.ndarray, alpha: float) -> tuple[float, float]:
    lo, hi = np.percentile(dist, [100.0 * alpha, 100.0 * (1.0 - alpha)])
    return float(lo), float(hi)


def decision_interval(
    game_dist: np.ndarray, date_dist: np.ndarray, alpha: float
) -> tuple[Interval, Interval, Interval]:
    """(game interval, date interval, decision interval = the wider of the two)."""
    g = Interval(*percentile_interval(game_dist, alpha), "game")
    d = Interval(*percentile_interval(date_dist, alpha), "date")
    return g, d, (g if g.width >= d.width else d)


def codes(values: list[object] | np.ndarray) -> tuple[np.ndarray, int]:
    """Dense integer codes for a cluster label array; returns (codes, n_clusters)."""
    uniq, inv = np.unique(np.asarray(values, dtype=object).astype(str), return_inverse=True)
    return inv.astype(np.int64), len(uniq)


@dataclass(frozen=True)
class MeanCI:
    """A mean of per-contract values with both clustered intervals and the decision one."""

    n: int
    n_games: int
    n_dates: int
    mean: float
    game: Interval
    date: Interval
    decision: Interval


def mean_with_ci(
    values: np.ndarray,
    game_codes: np.ndarray,
    n_games: int,
    date_codes: np.ndarray,
    n_dates: int,
    alpha: float,
    b: int,
    rng: np.random.Generator,
) -> MeanCI:
    cols = np.column_stack([values, np.ones(len(values))])
    gs = cluster_stats(game_codes, cols, n_games)
    ds = cluster_stats(date_codes, cols, n_dates)
    gdist = ratio_mean(bootstrap_sums(gs, b, rng))
    ddist = ratio_mean(bootstrap_sums(ds, b, rng))
    g, d, dec = decision_interval(gdist, ddist, alpha)
    return MeanCI(len(values), n_games, n_dates, float(values.mean()), g, d, dec)


def mean_dist_decision(
    values: np.ndarray,
    game_codes: np.ndarray,
    n_games: int,
    date_codes: np.ndarray,
    n_dates: int,
    alpha: float,
    b: int,
    rng: np.random.Generator,
) -> tuple[np.ndarray, Interval]:
    """Bootstrap distribution of the mean under the WIDER clustering, with its interval."""
    cols = np.column_stack([values, np.ones(len(values))])
    gdist = ratio_mean(bootstrap_sums(cluster_stats(game_codes, cols, n_games), b, rng))
    ddist = ratio_mean(bootstrap_sums(cluster_stats(date_codes, cols, n_dates), b, rng))
    g, d, dec = decision_interval(gdist, ddist, alpha)
    return (gdist if dec.clustering == "game" else ddist), dec
