"""Real names from the static ``nba_api`` lists (local data, no network)."""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _players() -> dict[int, str]:
    try:
        from nba_api.stats.static import players

        return {int(p["id"]): str(p["full_name"]) for p in players.get_players()}
    except Exception:  # pragma: no cover - static data missing; fall back to ids
        return {}


@lru_cache(maxsize=1)
def _teams() -> dict[int, tuple[str, str]]:
    try:
        from nba_api.stats.static import teams

        return {
            int(t["id"]): (str(t["abbreviation"]), str(t["full_name"])) for t in teams.get_teams()
        }
    except Exception:  # pragma: no cover
        return {}


def player_name(player_id: int, fallback: dict[int, str] | None = None) -> str:
    """Static-list name, else the books' spelling, else ``Player <id>``."""
    name = _players().get(player_id)
    if name:
        return name
    if fallback and player_id in fallback:
        return fallback[player_id]
    return f"Player {player_id}"


def team_abbr(team_id: int | None) -> str:
    if team_id is None:
        return "?"
    return _teams().get(team_id, (str(team_id), str(team_id)))[0]


def team_name(team_id: int) -> str:
    return _teams().get(team_id, (str(team_id), str(team_id)))[1]
