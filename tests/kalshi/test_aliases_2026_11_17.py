"""The 2026-11-17 reviewed alias rows resolve to players with season-2026 box scores.

The fixture was recorded from a read-only copy of the export DB (season 2026 rows only); the test
never opens a database. See docs/reviews/kalshi_aliases_2026-11-17.md for the evidence per name.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nba.kalshi.aliases import load_reviewed_aliases, resolve_kalshi_player_name
from nba.parse.availability import normalize_name

FIXTURE = Path(__file__).parent / "fixtures" / "alias_season2026_players_2026-11-17.json"
ADDED: dict[str, dict[str, object]] = json.loads(FIXTURE.read_text())["kalshi_names"]


def test_batch_size() -> None:
    assert len(ADDED) == 47


@pytest.mark.parametrize("name", sorted(ADDED))
def test_added_alias_resolves_to_a_season_2026_player(name: str) -> None:
    rec = ADDED[name]
    pid = resolve_kalshi_player_name(name, load_reviewed_aliases())
    assert pid == rec["player_id"]
    assert int(str(rec["games_played_2026"])) > 0
    assert normalize_name(str(rec["full_name"])) == normalize_name(name)


def test_no_added_key_points_two_ways() -> None:
    """Every added name normalizes to a key no other display name maps to a different id."""
    import yaml

    from nba.kalshi.aliases import DEFAULT_ALIAS_FILE

    seen: dict[str, int] = {}
    for raw, pid in yaml.safe_load(DEFAULT_ALIAS_FILE.read_text())["aliases"].items():
        key = normalize_name(str(raw))
        assert seen.setdefault(key, int(pid)) == int(pid), raw
