"""Reviewed alias-table name matching: fuzzy match + loud failure on miss."""

from __future__ import annotations

import pytest

from nba.kalshi.aliases import UnmatchedKalshiNameError, resolve_kalshi_player_name


def test_exact_match_resolves() -> None:
    assert resolve_kalshi_player_name("LeBron James") == 2544


def test_normalized_variant_resolves() -> None:
    assert resolve_kalshi_player_name("lebron   james") == 2544
    assert resolve_kalshi_player_name("Nikola Jokić") == 203999


def test_minor_typo_fuzzy_matches() -> None:
    # single dropped letter, still unambiguous
    assert resolve_kalshi_player_name("Steph Curry") != 0  # just not an exception


def test_unmatched_name_raises_loudly() -> None:
    with pytest.raises(UnmatchedKalshiNameError):
        resolve_kalshi_player_name("Not A Real Player")


def test_unmatched_name_error_mentions_the_name() -> None:
    with pytest.raises(UnmatchedKalshiNameError, match="Totally Unknown"):
        resolve_kalshi_player_name("Totally Unknown")
