"""The reviewed live table resolves the 2026-10-23 unmatched names exactly (no fuzzy path)."""

from __future__ import annotations

import pytest

from nba.kalshi.aliases import UnmatchedKalshiNameError, load_reviewed_aliases
from nba.parse.availability import normalize_name

EXPECTED = {
    "Aaron Gordon": 203932,
    "Jabari Smith Jr.": 1631095,  # not the retired namesake Jabari Smith (2074)
    "Jaren Jackson Jr.": 1628991,  # not Jaren Jackson (1114)
    "Tim Hardaway Jr.": 203501,
    "Nikola Vučević": 202696,  # diacritics folded
    "Cameron Boozer": 1643409,  # rookie, official lineup feed id
}


@pytest.mark.parametrize(("name", "pid"), sorted(EXPECTED.items()))
def test_exact_resolution(name: str, pid: int) -> None:
    table = load_reviewed_aliases()
    assert table[normalize_name(name)] == pid


def test_unknown_name_still_fails_loudly() -> None:
    from nba.kalshi.aliases import resolve_kalshi_player_name

    with pytest.raises(UnmatchedKalshiNameError):
        resolve_kalshi_player_name("Zzyzx Qwerty", load_reviewed_aliases())
