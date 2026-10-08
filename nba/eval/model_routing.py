"""Model-routing analysis: "which predictor is best for whom" (registry
discovery pattern from CLAUDE.md's "Registry retrofit", re-implemented
generically -- no employer code/tables referenced).

Core, generic, fully-tested piece: :func:`route_by_bucket`. Given a bucket
label per player-game (e.g. archetype cluster from
``nba.coldstart.archetypes``, or SB volatility quadrant from
``nba.coldstart.sb_classification``) and one already-computed per-game
score array (CRPS, lower-is-better) per candidate predictor, it reports,
per bucket: sample size, each predictor's mean score, and a paired
per-game bootstrap CI on every predictor's score delta vs. the bucket's
best predictor (reusing ``nba.props.metrics.paired_score_delta_ci`` --
the same paired-bootstrap machinery the rest of the project uses, not a
new implementation). This is the piece unit-tested here on synthetic data.

Wiring this to the *real* candidate predictors (season-average, last-10
baseline, possession sim) on the real DB is the maintainer's job -- see
this module's docstring tail and the agent status/report for the exact
commands. ``nba.eval.player_points_sim_eval`` /
``nba.eval.player_reb_ast_sim_eval`` already compute per-player-game CRPS
arrays for the sim vs. baselines internally; the maintainer script below
re-exposes that same per-row data (not duplicated logic) and joins it
against a bucket-label DataFrame built from this project's cold-start
modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from nba.eval.metrics import ConfidenceInterval
from nba.props.metrics import paired_score_delta_ci


@dataclass
class RouteBucketResult:
    bucket: str
    n: int
    sufficient: bool
    note: str
    mean_crps_by_model: dict[str, float] = field(default_factory=dict)
    #: CI on mean(crps_model - crps_best) for every model other than the
    #: bucket's best (empty dict when ``not sufficient``). A CI entirely
    #: above 0 is positive evidence "best" really does beat that model in
    #: this bucket at this sample size; overlapping 0 means "no clear
    #: winner between these two in this bucket", which is itself a useful,
    #: honest routing-table entry.
    delta_vs_best_ci: dict[str, ConfidenceInterval] = field(default_factory=dict)
    best_model: str = ""


def route_by_bucket(
    bucket_labels: np.ndarray,
    crps_by_model: dict[str, np.ndarray],
    min_bucket_n: int = 30,
    n_boot: int = 2000,
    seed: int = 0,
) -> list[RouteBucketResult]:
    """Per-bucket CRPS comparison across every model in ``crps_by_model``.

    ``bucket_labels`` and every array in ``crps_by_model`` must be the same
    length and index-aligned (row ``i`` is the same player-game
    everywhere) -- the same "paired by game" contract every other
    bootstrap comparison in this project relies on.

    Buckets with fewer than ``min_bucket_n`` rows get ``sufficient=False``
    and an explanatory note instead of a numeric best-model claim that
    would look precise but isn't (same honesty pattern as
    ``nba.props.volatility.evaluate_volatility_buckets``).
    """
    if not crps_by_model:
        raise ValueError("crps_by_model must have at least one entry")
    labels_arr = np.asarray(bucket_labels)
    n_total = len(labels_arr)
    for name, arr in crps_by_model.items():
        if len(arr) != n_total:
            raise ValueError(f"crps_by_model[{name!r}] length {len(arr)} != n={n_total}")

    results: list[RouteBucketResult] = []
    for bucket in sorted(set(labels_arr.tolist()), key=str):
        mask = labels_arr == bucket
        n = int(mask.sum())
        if n < min_bucket_n:
            results.append(
                RouteBucketResult(
                    bucket=str(bucket),
                    n=n,
                    sufficient=False,
                    note=f"insufficient data for a trustworthy routing decision "
                    f"(n={n} < min_bucket_n={min_bucket_n})",
                )
            )
            continue

        means = {
            name: float(np.nanmean(np.asarray(arr, dtype=float)[mask]))
            for name, arr in crps_by_model.items()
        }
        best = min(means, key=lambda name: means[name])
        deltas = {
            name: paired_score_delta_ci(
                np.asarray(arr, dtype=float)[mask],
                np.asarray(crps_by_model[best], dtype=float)[mask],
                n_boot=n_boot,
                seed=seed,
            )
            for name, arr in crps_by_model.items()
            if name != best
        }
        results.append(
            RouteBucketResult(
                bucket=str(bucket),
                n=n,
                sufficient=True,
                note="",
                mean_crps_by_model=means,
                delta_vs_best_ci=deltas,
                best_model=best,
            )
        )
    return results


def routing_table_rows(results: list[RouteBucketResult]) -> list[dict[str, object]]:
    """Flatten :func:`route_by_bucket` output into tidy
    ``(bucket, n, best_model, model, mean_crps, delta_vs_best_lo, delta_vs_best_hi, note)``
    rows, one per (bucket, model) -- directly writable as a markdown table
    for ``report.md``."""
    rows: list[dict[str, object]] = []
    for r in results:
        if not r.sufficient:
            rows.append(
                {
                    "bucket": r.bucket,
                    "n": r.n,
                    "best_model": "",
                    "model": "",
                    "mean_crps": float("nan"),
                    "delta_vs_best_lo": float("nan"),
                    "delta_vs_best_hi": float("nan"),
                    "note": r.note,
                }
            )
            continue
        for name, mean_crps in r.mean_crps_by_model.items():
            ci = r.delta_vs_best_ci.get(name)
            rows.append(
                {
                    "bucket": r.bucket,
                    "n": r.n,
                    "best_model": r.best_model,
                    "model": name,
                    "mean_crps": mean_crps,
                    "delta_vs_best_lo": ci.lo if ci else 0.0,
                    "delta_vs_best_hi": ci.hi if ci else 0.0,
                    "note": "",
                }
            )
    return rows


__all__ = ["RouteBucketResult", "route_by_bucket", "routing_table_rows"]

# ---------------------------------------------------------------------------
# Maintainer entry point (NOT run by this agent -- real-DB, multi-season,
# can take minutes; see CLAUDE.md "no multi-minute jobs" for agent runs).
# Sketch of how to assemble crps_by_model + bucket_labels on the real DB
# once nba.eval.player_points_sim_eval exposes raw per-row CRPS arrays
# (today it only returns an aggregate SimPlayerPointsEvalResult -- a small,
# separate follow-up to add a `return_raw=True` path there, reusing its
# existing per-game loop, is the natural next step before running this for
# real). Until then, run the three pieces that ARE wired and testable today:
#
#   from nba.db.connect import connect
#   from nba.coldstart.archetypes import build_player_archetypes
#   from nba.coldstart.sb_classification import build_sb_classification, \
#       summarize_sb_classes
#   con = connect("nba.duckdb", read_only=True)
#   archetypes = build_player_archetypes(con, as_of_date="2024-10-01")
#   print(archetypes.k, archetypes.k_selection_method, archetypes.note)
#   sb_pts = build_sb_classification(con, "pts", min_games=10)
#   print(summarize_sb_classes(sb_pts))
#
# then join `archetypes.assignments` / `sb_pts` (latest row per player) onto
# the per-player-game CRPS arrays from the sim/baseline eval modules on
# `player_id`, and call `route_by_bucket` on the result.
