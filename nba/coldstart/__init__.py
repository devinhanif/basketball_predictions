"""Cold-start modeling: what is left here after the 2026-10-10 restructure (Wave A).

Live: :class:`nba.coldstart.config.ColdStartConfig` (the cold-start threshold the rung ladder's
``nba.features.player_features`` reads). Shims for research importers: ``shrinkage`` and
``sb_classification`` re-export from ``nba.features``. Archived to ``research/coldstart``:
``archetypes`` (M3A, T048-T060), ``rookie_priors``, ``carryover`` (TIME_DECAY_2026-10-08).
"""

from __future__ import annotations

from nba.coldstart.config import ColdStartConfig

__all__ = ["ColdStartConfig"]
