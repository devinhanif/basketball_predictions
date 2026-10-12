"""Rivalry flags for the P(play) shadow log (F10A option 1, docs/prereg/F10A_RIVALRY.md).

Two as-of columns per game, ``div_rival`` and ``hist_rival``, from the frozen pair list
(docs/research/rivalries_2026-10-09.md, Tier 1 + Tier 2, frozen at b844256) in
``rivalry_pairs.csv`` next to this module (80 rows; ``tests/daily/test_rivalry_flags.py`` pins the
file to the doc). The flags are logged only: no model reads them, no test is run on them.

* ``div_rival``: the two teams are a Tier 1 (division) pair.
* ``hist_rival``: the pair is a Tier 2 pair and its AS-OF strength is >= 2. Strength uses only
  meetings whose series ended in an EARLIER calendar year than the game (a year-Y series is
  invisible to every game dated in year Y). 3 = three or more meetings including one in the
  previous 6 years; 2 = a Finals meeting, or two or more meetings in the previous 10 years;
  1 = any; 0 = none. ``older`` meetings (years unknown, all before 2015) count toward the total
  but never toward the "previous N years" tests. Same arithmetic as ``research/features/rivalry.py``
  (a test compares them); this copy exists because ``nba/`` never imports ``research``.
"""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PAIRS_CSV = Path(__file__).with_name("rivalry_pairs.csv")
HIST_MIN_STRENGTH = 2
STRENGTH3_RECENT_YEARS = 6
STRENGTH2_WINDOW_YEARS = 10


@dataclass(frozen=True)
class Pair:
    tier: int
    tag: str
    a: int
    b: int
    #: (year the series ended, is_finals); Tier 2 only
    meetings: tuple[tuple[int, bool], ...]
    older: int


@lru_cache(maxsize=1)
def load_pairs(path: Path = PAIRS_CSV) -> tuple[Pair, ...]:
    out: list[Pair] = []
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            meetings = tuple((int(m[:-1]), m[-1] == "F") for m in r["meetings"].split("|") if m)
            out.append(
                Pair(int(r["tier"]), r["tag"], int(r["team_a"]), int(r["team_b"]), meetings,
                     int(r["older"]))
            )  # fmt: skip
    return tuple(out)


def _find(t1: int, t2: int, tier: int) -> Pair | None:
    for p in load_pairs():
        if p.tier == tier and {p.a, p.b} == {t1, t2}:
            return p
    return None


def asof_strength(pair: Pair, game_date: dt.date) -> int:
    """Strength 0-3 from the documented meetings that ended in an earlier calendar year."""
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


def div_rival(t1: int, t2: int) -> bool:
    return t1 != t2 and _find(t1, t2, 1) is not None


def hist_rival(t1: int, t2: int, game_date: dt.date) -> bool:
    p = _find(t1, t2, 2)
    return p is not None and asof_strength(p, game_date) >= HIST_MIN_STRENGTH


def flags(home: int, away: int, game_date: dt.date) -> dict[str, bool]:
    return {"div_rival": div_rival(home, away), "hist_rival": hist_rival(home, away, game_date)}
