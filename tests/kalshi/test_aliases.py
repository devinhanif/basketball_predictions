"""Reviewed alias-table name matching: exact match only, loud failure on miss."""

from __future__ import annotations

import pytest

from nba.kalshi.aliases import UnmatchedKalshiNameError, resolve_kalshi_player_name


def test_exact_match_resolves() -> None:
    assert resolve_kalshi_player_name("LeBron James") == 2544


def test_normalized_variant_resolves() -> None:
    assert resolve_kalshi_player_name("lebron   james") == 2544
    assert resolve_kalshi_player_name("Nikola Jokić") == 203999


def test_near_miss_of_known_name_is_not_matched() -> None:
    # one dropped letter, similarity far above the old 0.84 cutoff, single neighbour
    table = {"lebron james": 2544, "nikola jokic": 203999}
    with pytest.raises(UnmatchedKalshiNameError):
        resolve_kalshi_player_name("Lebron Jame", table)
    with pytest.raises(UnmatchedKalshiNameError):
        resolve_kalshi_player_name("Nikola Jokik", table)


def test_alias_table_entry_still_matches() -> None:
    assert resolve_kalshi_player_name("Zed Q", {"zed q": 7}) == 7


def test_unmatched_name_raises_loudly() -> None:
    with pytest.raises(UnmatchedKalshiNameError):
        resolve_kalshi_player_name("Not A Real Player")


def test_unmatched_name_error_mentions_the_name() -> None:
    with pytest.raises(UnmatchedKalshiNameError, match="Totally Unknown"):
        resolve_kalshi_player_name("Totally Unknown")
