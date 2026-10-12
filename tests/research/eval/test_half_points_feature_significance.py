"""Unit checks for the descriptive half-points and feature-significance harnesses (no real data)."""

from __future__ import annotations

import numpy as np

from nba.props.context_residual import PROP_STATS
from research.eval import feature_significance as FS
from research.eval import half_points as HP


def test_groups_partition_production_features() -> None:
    for stat in PROP_STATS:
        arms = FS.arms_for(stat)  # raises if the groups do not partition the production list
        assert "full" in arms and "add:recent_role" in arms
        assert len(arms) == 18  # full, seed1, 15 drop-group arms, add recent-role


def test_binomial_split_quantiles() -> None:
    t = np.full((3, 199), 10)
    q = HP.binom_split_quantiles(t, np.array([0.5, 0.0, 1.0]))
    assert q[0, 99] == 5
    assert (q[1] == 0).all()
    assert (q[2] == 10).all()
    assert (np.diff(q, axis=1) >= 0).all()
