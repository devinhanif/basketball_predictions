"""Rivalry flags (F10a): division pairs, as-of strength, planted-future."""

from __future__ import annotations

import datetime as dt
from itertools import combinations

from research.features.rivalry import (
    BOS,
    DAL,
    DEN,
    DIVISIONS,
    GSW,
    HOU,
    IND,
    LAL,
    MIA,
    OKC,
    SAS,
    TIER2,
    asof_strength,
    div_rival,
    flags,
    hist_rival,
)


def test_sixty_division_pairs_and_thirty_teams() -> None:
    teams = [t for ts in DIVISIONS.values() for t in ts]
    assert len(teams) == len(set(teams)) == 30
    n = sum(div_rival(a, b) for a, b in combinations(teams, 2))
    assert n == 60
    assert not div_rival(BOS, BOS)


def test_twenty_tier2_pairs() -> None:
    assert len(TIER2) == 20


def test_same_year_series_is_invisible_planted_future() -> None:
    # OKC-IND met in the 2025 Finals (ended June 2025): never visible to a 2025 game or earlier
    assert not hist_rival(OKC, IND, dt.date(2025, 3, 1))
    assert not hist_rival(OKC, IND, dt.date(2025, 12, 1))  # same calendar year: conservative
    assert (
        asof_strength(TIER2[4], dt.date(2026, 12, 1)) == 2
    )  # a Finals meeting is strength 2 from 2026
    # DEN-MIA 2023 Finals: not visible during the 2023 playoffs, visible from January 2024
    assert not hist_rival(DEN, MIA, dt.date(2023, 3, 1))
    assert hist_rival(DEN, MIA, dt.date(2024, 1, 15))


def test_strength_rubric() -> None:
    pair = {p.tag: p for p in TIER2}
    assert (
        asof_strength(pair["H12"], dt.date(2017, 1, 1)) == 2
    )  # 2015, 2016 inside 10 years, no third meeting
    assert (
        asof_strength(pair["H12"], dt.date(2024, 1, 1)) == 3
    )  # four documented, 2019 within 6 years
    assert asof_strength(pair["H20"], dt.date(2026, 1, 1)) == 1  # a single series
    assert (
        asof_strength(pair["H1"], dt.date(2023, 1, 1)) == 2
    )  # Finals, last 2010 is older than 6 years


def test_flags_symmetric_and_any() -> None:
    f = flags(HOU, SAS, dt.date(2023, 12, 1))
    assert f["div_rival"] and f["any_rival"]
    assert flags(SAS, HOU, dt.date(2023, 12, 1)) == f
    g = flags(LAL, BOS, dt.date(2023, 12, 1))
    assert (not g["div_rival"]) and g["hist_rival"] and g["any_rival"]
    assert not any(flags(DAL, GSW, dt.date(2023, 12, 1)).values())
