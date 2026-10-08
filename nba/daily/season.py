"""Season encoding helpers.

``games.season`` is the START year of the season: ``2025`` means 2025-26, so
2026-27 is ``2026`` (see ``nba.ingest.games.season_to_int('2026-27') == 2026``).
"""

from __future__ import annotations

from datetime import date

from nba.ingest.games import season_to_int

#: Season id of the first genuinely out-of-sample season (2026-27).
FORWARD_SEASON = 2026
#: HOLDOUT_ACCESS_LOG.md rule 3: roll the holdout over at this many games.
ROLLOVER_MIN_GAMES = 20


def season_int_for_date(d: date) -> int:
    """Start-year season id for a calendar date (Aug-Dec -> year, Jan-Jul -> year-1)."""
    return d.year if d.month >= 8 else d.year - 1


def season_str(season_int: int) -> str:
    """2026 -> '2026-27' (the form the ingest pullers take)."""
    return f"{season_int}-{(season_int + 1) % 100:02d}"


def season_str_for_date(d: date) -> str:
    return season_str(season_int_for_date(d))


def check_roundtrip(season: str) -> int:
    """Return the int id for a season string (delegates to the ingest rule)."""
    return season_to_int(season)
