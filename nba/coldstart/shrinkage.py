"""Moved to ``nba.features.shrinkage`` (2026-10-09, edge 8). Research importers only."""

from nba.features.shrinkage import (
    PseudoCountTuneResult,
    classify_source,
    shrink_rate,
    tune_pseudo_count,
)

__all__ = ["PseudoCountTuneResult", "classify_source", "shrink_rate", "tune_pseudo_count"]
