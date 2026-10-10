"""Routed predictor: pick sim-vs-season-average per player-game by as-of bucket.

The 2026-10-08 routing analysis (research.eval.model_routing) found the possession
sim's edge is concentrated in cold-start / intermittent / erratic players while
the season average wins for smooth high-volume regulars. This module turns that
observation into an actual predictor and evaluates it HONESTLY:

  - the routing map (bucket -> which model) is learned ONLY on training games;
  - it is then applied to strictly-future test games (a frozen holdout season,
    or a walk-forward split), so the "which model wins this bucket" decision is
    never fit on the games it's scored on;
  - the per-row bucket (Syntetos-Boylan volatility class) is itself computed
    as-of (nba.features.sb_classification), so routing adds no leakage.

A routed predictor that does NOT beat both the sim alone and the season average
alone out-of-sample is not worth shipping -- this module is built to find that
out, not to assume it. Pure functions over index-aligned arrays (no DB
coupling); the real-DB assembly lives in the maintainer runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from nba.props.metrics import ConfidenceInterval, paired_score_delta_ci


def learn_routing_map(buckets: np.ndarray, crps_by_model: dict[str, np.ndarray]) -> dict[str, str]:
    """For each bucket, the model with the lowest mean CRPS on these rows.

    Call this on TRAINING rows only. Buckets with no rows are simply absent
    from the returned map; the caller supplies a ``default_model`` for buckets
    unseen in training (or seen only in test).
    """
    models = list(crps_by_model)
    out: dict[str, str] = {}
    for bucket in sorted({str(b) for b in buckets.tolist()}):
        mask = buckets == bucket
        if not mask.any():
            continue
        means = {m: float(np.nanmean(crps_by_model[m][mask])) for m in models}
        out[bucket] = min(means, key=lambda m: means[m])
    return out


def apply_routed_crps(
    buckets: np.ndarray,
    crps_by_model: dict[str, np.ndarray],
    route_map: dict[str, str],
    default_model: str,
) -> np.ndarray:
    """Per-row CRPS of the routed predictor: for each row take the CRPS of the
    model ``route_map`` assigns to that row's bucket (``default_model`` when the
    bucket is absent from the map)."""
    n = len(buckets)
    out = np.empty(n, dtype=float)
    for i in range(n):
        model = route_map.get(str(buckets[i]), default_model)
        out[i] = crps_by_model[model][i]
    return out


@dataclass
class RoutedEvalResult:
    stat: str
    n_train: int
    n_test: int
    crps_routed: float
    crps_by_model: dict[str, float]
    #: routed - model, per candidate (negative => routed beats that candidate).
    routed_vs_model: dict[str, ConfidenceInterval] = field(default_factory=dict)
    route_map: dict[str, str] = field(default_factory=dict)
    default_model: str = ""

    def summary(self) -> str:
        parts = [
            f"{m}={self.routed_vs_model[m].lo:+.3f}..{self.routed_vs_model[m].hi:+.3f}"
            for m in self.routed_vs_model
        ]
        return (
            f"stat={self.stat} n_test={self.n_test} crps_routed={self.crps_routed:.3f} "
            f"(routed-model CI: {'; '.join(parts)}; neg => routed wins)"
        )


def evaluate_routing(
    stat: str,
    buckets: np.ndarray,
    crps_by_model: dict[str, np.ndarray],
    train_mask: np.ndarray,
    n_boot: int = 2000,
    seed: int = 0,
    cluster_ids: np.ndarray | None = None,
) -> RoutedEvalResult:
    """Learn the routing map on ``train_mask`` rows, apply to the rest, and
    compare the routed predictor's CRPS to every candidate model on the TEST
    rows via paired per-row bootstrap.

    ``buckets`` and each array in ``crps_by_model`` are index-aligned
    (row i = same player-game). ``train_mask`` is a boolean array; test = ~mask.
    ``default_model`` is the globally-best model on the training rows (used for
    buckets unseen in training).

    ``cluster_ids`` (optional, one id per row aligned with ``buckets``/
    ``crps_by_model``, typically ``game_id``) switches the routed-vs-model
    comparison on the TEST rows to a cluster/block bootstrap -- same
    rationale and contract as ``research.eval.model_routing.route_by_bucket`` and
    ``research.props.run`` (docs/ACCEPTANCE_CRITERIA_2026-10-08.md). ``None``
    (default) preserves the exact prior row-level bootstrap behavior.
    """
    models = list(crps_by_model)
    test_mask = ~train_mask
    train_buckets = buckets[train_mask]
    train_crps = {m: crps_by_model[m][train_mask] for m in models}
    route_map = learn_routing_map(train_buckets, train_crps)
    default_model = min(models, key=lambda m: float(np.nanmean(train_crps[m])))

    test_buckets = buckets[test_mask]
    test_crps = {m: crps_by_model[m][test_mask] for m in models}
    routed = apply_routed_crps(test_buckets, test_crps, route_map, default_model)
    test_cluster_ids = None if cluster_ids is None else np.asarray(cluster_ids)[test_mask]

    routed_vs_model = {
        m: paired_score_delta_ci(
            routed, test_crps[m], n_boot=n_boot, seed=seed, cluster_ids=test_cluster_ids
        )
        for m in models
    }
    return RoutedEvalResult(
        stat=stat,
        n_train=int(train_mask.sum()),
        n_test=int(test_mask.sum()),
        crps_routed=float(np.nanmean(routed)),
        crps_by_model={m: float(np.nanmean(test_crps[m])) for m in models},
        routed_vs_model=routed_vs_model,
        route_map=route_map,
        default_model=default_model,
    )


__all__ = [
    "RoutedEvalResult",
    "apply_routed_crps",
    "evaluate_routing",
    "learn_routing_map",
]
