"""Season guard: the 2025 holdout is never exported, and nothing outside the allowed set is.

``games.season`` is the start year (2026 = 2026-27). Allowed: the live season (2026) and, for
fixtures only, seasons before the holdout (<= 2024). Season 2025 and any future season other than
the live one are refused before a single row is read. Row level: the season of a row is
``games.season`` when the game is known, else decoded from the NBA game id (``002YY#####``).
"""

from __future__ import annotations

from collections.abc import Iterable

HOLDOUT_SEASON = 2025
LIVE_SEASON = 2026
LAST_PRE_HOLDOUT_SEASON = 2024

#: Game-id prefixes that are never exported: preseason (001) and all-star (003) games are
#: unscorable rehearsal rows (docs/DAILY_PIPELINE.md, "Preseason and all-star games are skipped").
SKIP_PREFIXES = ("001", "003")

#: SQL for the season of a forward row: the games table when it knows the game, else the id.
#: ``alias_g`` is the alias of the (LEFT JOINed) games table, ``col`` the game-id column.
SEASON_SQL = (
    "COALESCE({g}.season, CASE WHEN {c} SIMILAR TO '00[0-9]{{7}}' "
    "THEN 2000 + CAST(substr({c}, 4, 2) AS INTEGER) END)"
)


class ExportRefused(ValueError):
    """The requested export would touch the holdout (or an unsupported season)."""


def season_of_game_id(game_id: str) -> int | None:
    """Season start year from an NBA game id, or None when the id is not ``00T YY #####``."""
    if len(game_id) == 10 and game_id.startswith("00") and game_id.isdigit():
        return 2000 + int(game_id[3:5])
    return None


def resolve_seasons(requested: Iterable[int] | None = None) -> tuple[int, ...]:
    """The seasons to export; refuses 2025 (and anything after the live season) loudly."""
    seasons = tuple(
        sorted({int(s) for s in (requested if requested is not None else [LIVE_SEASON])})
    )
    if not seasons:
        raise ExportRefused("no seasons requested")
    for s in seasons:
        if s == HOLDOUT_SEASON:
            raise ExportRefused(
                f"season {HOLDOUT_SEASON} is the frozen holdout and is never exported "
                "(docs/HOLDOUT_ACCESS_LOG.md)"
            )
        if s > LIVE_SEASON or (LAST_PRE_HOLDOUT_SEASON < s < LIVE_SEASON):
            raise ExportRefused(f"season {s} is not exportable (allowed: <= 2024 or {LIVE_SEASON})")
    return seasons


def season_sql(game_alias: str = "g", id_col: str = "p.game_id") -> str:
    return SEASON_SQL.format(g=game_alias, c=id_col)


def game_id_allowed(game_id: str) -> bool:
    return not game_id.startswith(SKIP_PREFIXES)
