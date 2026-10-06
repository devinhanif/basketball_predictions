"""Cold-start method flags and tunable parameters (CLAUDE.md "Cold start
modeling" -> "Methods (implement in this order, each behind a flag,
evaluate each)").

A single :class:`ColdStartConfig` dataclass gates methods 1-4. Each method
module in ``nba/coldstart/`` is fully usable on its own (no hard dependency
on this dataclass) -- this just gives the eval harness / CLI one place to
turn a method on or off and to record which methods were active for a given
registry run (``tags.cold_start_flags``, see ``nba/registry/metadata.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: Per-stat pseudo-counts (method 1 default prior). Tunable by walk-forward
#: CV via ``nba.coldstart.shrinkage.tune_pseudo_count``; these are just the
#: starting point when CV can't run (e.g. too little data, see that
#: module's docstring).
DEFAULT_PSEUDO_COUNTS: dict[str, float] = {
    "usage": 150.0,
    "tov_rate": 150.0,
    "ft_pct": 100.0,
    "foul_draw": 150.0,
    "orb_rate": 150.0,
    "drb_rate": 150.0,
}

#: Default archetype cluster count (method 2). Overridden by
#: ``nba.coldstart.archetypes.select_n_clusters_by_backtest`` when there is
#: enough data to compare candidates.
DEFAULT_N_CLUSTERS = 4

#: Default carryover decay/age parameters (method 4).
DEFAULT_CARRYOVER_DECAY_WEIGHT = 0.3
DEFAULT_PEAK_AGE = 27.0
DEFAULT_AGE_CURVE_WIDTH = 9.0

#: CLAUDE.md "Cold start modeling" -> threshold used to define the
#: cold-start bucket for backtest slicing: ">=1 starter has <500 career
#: possessions".
CAREER_POSS_COLD_START_THRESHOLD = 500.0


@dataclass
class ColdStartConfig:
    """Flags + params for cold-start methods 1-4. Each method is independently toggleable."""

    shrinkage_enabled: bool = True
    archetypes_enabled: bool = False
    rookie_priors_enabled: bool = False
    carryover_enabled: bool = False

    pseudo_counts: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_PSEUDO_COUNTS))
    n_clusters: int = DEFAULT_N_CLUSTERS
    carryover_decay_weight: float = DEFAULT_CARRYOVER_DECAY_WEIGHT
    peak_age: float = DEFAULT_PEAK_AGE
    age_curve_width: float = DEFAULT_AGE_CURVE_WIDTH
    seed: int = 0

    def active_flags(self) -> list[str]:
        """Names of enabled methods, for ``tags.cold_start_flags`` in the registry."""
        flags = []
        if self.shrinkage_enabled:
            flags.append("shrinkage")
        if self.archetypes_enabled:
            flags.append("archetypes")
        if self.rookie_priors_enabled:
            flags.append("rookie_priors")
        if self.carryover_enabled:
            flags.append("carryover")
        return flags


def load_coldstart_config(path: str | Path) -> ColdStartConfig:
    """Load a ``ColdStartConfig`` from a YAML file (config-driven experiments,
    CLAUDE.md "CI/CD" -> "Repo hygiene"). Unknown keys are rejected so a typo
    in a config file fails loudly instead of silently using a default."""
    with open(path) as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}
    return ColdStartConfig(**raw)
