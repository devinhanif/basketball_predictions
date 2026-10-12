# ruff: noqa: E501
"""Rivalry flags for F10a (docs/prereg/F10A_RIVALRY.md, DRAFT). Pure functions, no database.

The list is the one Devin approved (docs/research/rivalries_2026-10-09.md, Tier 1 and Tier 2 marked Y,
frozen at commit b844256). Tier 3 (player histories) is parked and not encoded.

* ``div_rival``: the two teams share a division (60 pairs; no realignment in 2022-24).
* ``hist_rival``: the pair is one of the Tier 2 pairs H1-H20 AND its AS-OF strength is >= 2. As-of strength
  uses only meetings whose series ended in an EARLIER calendar year than the game (granularity of a
  season: a series that ended in May or June of year Y counts for games dated Y+1 or later; a game dated
  in year Y never sees a year-Y series, which is conservative for games in January-June of Y). Rubric,
  adapted from the doc: 3 = three or more documented meetings including one in the previous 6 years;
  2 = a Finals meeting, or two or more meetings in the previous 10 years; 1 = any meeting; 0 = none.
  The doc's "overlapping current cores" clause cannot be observed and is dropped.
* ``any_rival`` = ``div_rival`` or ``hist_rival``.

``older`` counts documented meetings with unknown years that all precede 2015 (the doc gives an all-time
count and lists only the recent ones); they raise the meeting count but never the "previous 10 years" test.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

#: team ids as in the ``games`` table (NBA franchise ids)
DIVISIONS: dict[str, tuple[int, ...]] = {
    "Atlantic": (1610612738, 1610612751, 1610612752, 1610612755, 1610612761),
    "Central": (1610612741, 1610612739, 1610612765, 1610612754, 1610612749),
    "Southeast": (1610612737, 1610612766, 1610612748, 1610612753, 1610612764),
    "Northwest": (1610612743, 1610612750, 1610612760, 1610612757, 1610612762),
    "Pacific": (1610612744, 1610612746, 1610612747, 1610612756, 1610612758),
    "Southwest": (1610612742, 1610612745, 1610612763, 1610612740, 1610612759),
}

BOS, BKN, NYK, PHI, TOR = 1610612738, 1610612751, 1610612752, 1610612755, 1610612761
CHI, CLE, DET, IND, MIL = 1610612741, 1610612739, 1610612765, 1610612754, 1610612749
ATL, CHA, MIA, ORL, WAS = 1610612737, 1610612766, 1610612748, 1610612753, 1610612764
DEN, MIN, OKC, POR, UTA = 1610612743, 1610612750, 1610612760, 1610612757, 1610612762
GSW, LAC, LAL, PHX, SAC = 1610612744, 1610612746, 1610612747, 1610612756, 1610612758
DAL, HOU, MEM, NOP, SAS = 1610612742, 1610612745, 1610612763, 1610612740, 1610612759


@dataclass(frozen=True)
class Pair:
    """One Tier 2 pair: documented meetings as (year the series ended, is_finals) plus ``older`` pre-2015 meetings."""

    tag: str
    a: int
    b: int
    meetings: tuple[tuple[int, bool], ...]
    older: int = 0


def _f(*years: int) -> tuple[tuple[int, bool], ...]:
    return tuple((y, True) for y in years)


def _s(*years: int) -> tuple[tuple[int, bool], ...]:
    return tuple((y, False) for y in years)


#: Tier 2, H1-H20 as listed in the doc (all marked Y). Years are series end years.
TIER2: tuple[Pair, ...] = (
    Pair(
        "H1", LAL, BOS, _f(1959, 1962, 1963, 1965, 1966, 1968, 1969, 1984, 1985, 1987, 2008, 2010)
    ),
    Pair("H2", CLE, GSW, _f(2015, 2016, 2017, 2018)),
    Pair("H3", SAS, MIA, _f(2013, 2014)),
    Pair("H4", NYK, SAS, _f(1999, 2026)),
    Pair("H5", OKC, IND, _f(2025)),
    Pair("H6", GSW, BOS, _f(2022)),
    Pair("H7", DEN, MIA, _f(2023)),
    Pair("H8", BOS, DAL, _f(2024)),
    Pair("H9", LAL, MIA, _f(2020)),
    Pair("H10", LAL, DET, _f(1988, 1989, 2004)),
    Pair("H11", MIA, DAL, _f(2006, 2011)),
    Pair("H12", HOU, GSW, _s(2015, 2016, 2018, 2019, 2025)),
    Pair("H13", HOU, LAL, _s(2020, 2026), older=8),
    Pair("H14", NYK, IND, _s(2024, 2025), older=7),
    Pair("H15", BOS, MIA, _s(2010, 2011, 2012, 2020, 2022, 2023, 2024)),
    Pair("H16", NYK, MIA, _s(1997, 1998, 1999, 2000, 2012, 2023)),
    Pair("H17", DEN, LAL, _s(2020, 2023, 2024), older=6),
    Pair("H18", OKC, SAS, _s(2012, 2014, 2016, 2026)),
    Pair("H19", NYK, CLE, _s(2023, 2026)),
    Pair("H20", DAL, MIN, _s(2024)),
)

HIST_MIN_STRENGTH = 2
STRENGTH3_RECENT_YEARS = 6
STRENGTH2_WINDOW_YEARS = 10


def division_of(team: int) -> str | None:
    for name, teams in DIVISIONS.items():
        if team in teams:
            return name
    return None


def div_rival(t1: int, t2: int) -> bool:
    d1 = division_of(t1)
    return d1 is not None and t1 != t2 and d1 == division_of(t2)


def asof_strength(pair: Pair, game_date: dt.date) -> int:
    """Strength 0-3 from the documented meetings that ended in an earlier calendar year than ``game_date``."""
    y = game_date.year
    known = [(yr, fin) for yr, fin in pair.meetings if yr < y]
    total = len(known) + pair.older
    if total == 0:
        return 0
    if total >= 3 and any(yr >= y - STRENGTH3_RECENT_YEARS for yr, _ in known):
        return 3
    if (
        any(fin for _, fin in known)
        or sum(1 for yr, _ in known if yr >= y - STRENGTH2_WINDOW_YEARS) >= 2
    ):
        return 2
    return 1


def _pair_for(t1: int, t2: int) -> Pair | None:
    for p in TIER2:
        if {p.a, p.b} == {t1, t2}:
            return p
    return None


def hist_rival(t1: int, t2: int, game_date: dt.date) -> bool:
    p = _pair_for(t1, t2)
    return p is not None and asof_strength(p, game_date) >= HIST_MIN_STRENGTH


def flags(t1: int, t2: int, game_date: dt.date) -> dict[str, bool]:
    d, h = div_rival(t1, t2), hist_rival(t1, t2, game_date)
    return {"div_rival": d, "hist_rival": h, "any_rival": d or h}
