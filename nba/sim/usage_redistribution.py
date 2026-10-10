"""Pickle-compatibility shim; the module itself lives in ``research/sim/usage_redistribution.py``.

Model caches written before 2026-10-10 (``data/models/context_residual/<date>/models.pkl``) pickled
``ReportTriggerConfig`` under this module path, because the report-trigger code was born here before
Wave B moved it to ``nba.features.injury_report``. Unpickling looks the class up by the stored path,
so this file keeps the name resolvable and a warm cache stays a cache hit instead of a
``context model failed: ModuleNotFoundError`` fallback to the recency baseline. It imports live code
only. Delete it once every cache on disk post-dates the move (or the fingerprint changes).
"""

from nba.features.injury_report import ReportTriggerConfig

__all__ = ["ReportTriggerConfig"]
