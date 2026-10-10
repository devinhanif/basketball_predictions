"""Cold-start modeling: what is left here after the 2026-10-10 restructure (Wave A).

Only shims for research importers: ``shrinkage`` and ``sb_classification`` re-export from
``nba.features``. Archived to ``research/coldstart``: ``archetypes`` (M3A, T048-T060),
``rookie_priors``, ``carryover`` (TIME_DECAY_2026-10-08) and ``config`` (``ColdStartConfig``, the
cold-start threshold read by the rung ladder's ``research.features.player_features``).
"""
