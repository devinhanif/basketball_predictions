"""Cold-start modeling (CLAUDE.md "Cold start modeling").

Methods 1-4, each independently flag-gated via
:class:`nba.coldstart.config.ColdStartConfig`:

1. :mod:`nba.coldstart.shrinkage` -- empirical-Bayes shrinkage (baseline).
2. :mod:`nba.coldstart.archetypes` -- clustering-based archetype priors.
3. :mod:`nba.coldstart.rookie_priors` -- draft/college -> first-season rate.
4. :mod:`nba.coldstart.carryover` -- time-decay carryover + age curve.
"""

from __future__ import annotations

from nba.coldstart.config import ColdStartConfig

__all__ = ["ColdStartConfig"]
