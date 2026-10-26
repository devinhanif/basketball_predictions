"""Canonical propositions shared by every venue, and the team mapping between venues.

Every game market on every venue reduces to ONE proposition about the final score, with a YES side
(the proposition holds) and a NO side (it does not):

* ``margin`` c : home margin (home points - away points) is greater than ``c``. Moneyline is c = 0.
  A spread on team T with handicap h ("T +3.5" is h = +3.5): T covers  <=>  margin_T + h > 0.
  For T = home that is YES of margin > -h; for T = away it is NO of margin > h.
* ``total`` c : total points are greater than ``c`` (YES = Over).

Half-point lines only (no pushes). A venue quoting a whole-number line is skipped and counted.
Matching is by (away team id, home team id, ET date, kind, c); a team that does not map to our ids
means the market is skipped and counted, never guessed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from nba.ingest.teams import TEAM_ABBREV_TO_ID

#: Venue abbreviations that differ from nba_api's, reviewed by hand. Anything not here or in
#: TEAM_ABBREV_TO_ID is unmatched (skip + count).
ABBR_ALIASES: dict[str, str] = {
    "NY": "NYK",
    "GS": "GSW",
    "SA": "SAS",
    "NO": "NOP",
    "WSH": "WAS",
    "UTAH": "UTA",
    "PHO": "PHX",
    "BRK": "BKN",
    "CHO": "CHA",
}
ID_TO_ABBR: dict[int, str] = {v: k for k, v in TEAM_ABBREV_TO_ID.items()}


def team_id(abbr: str) -> int | None:
    a = abbr.strip().upper()
    a = ABBR_ALIASES.get(a, a)
    return TEAM_ABBREV_TO_ID.get(a)


@dataclass(frozen=True)
class GameKey:
    away: int
    home: int
    day: str  # ET date, YYYY-MM-DD

    def label(self) -> str:
        return f"{ID_TO_ABBR.get(self.away, self.away)}@{ID_TO_ABBR.get(self.home, self.home)}"

    def slug_suffix(self) -> str:
        return f"{ID_TO_ABBR[self.away].lower()}-{ID_TO_ABBR[self.home].lower()}-{self.day}"


@dataclass(frozen=True)
class PropKey:
    game: GameKey
    kind: str  # margin | total
    c: float

    def label(self) -> str:
        if self.kind == "total":
            return f"total>{self.c:g}"
        if self.c == 0:
            return "moneyline"
        return f"home margin>{self.c:g}"


def is_half_point(c: float) -> bool:
    return abs(c - round(c) - 0.5) < 1e-9 or abs(c - round(c) + 0.5) < 1e-9


@dataclass
class Ask:
    """The cheapest way one venue sells one side. ``levels`` = (price, contracts), best first."""

    venue: str
    price: float
    desc: str
    ref: str
    levels: list[tuple[float, float]] = field(default_factory=list)
    short: str = ""  # phone-length buy text, e.g. 'Kalshi KXNBAGAME-...-PHI YES'

    @property
    def size(self) -> float | None:
        return self.levels[0][1] if self.levels else None

    def depth_known(self) -> bool:
        return bool(self.levels)


def margin_side(
    game: GameKey, team: int, handicap: float, yes_side: bool
) -> tuple[float, bool] | None:
    """(c, is_yes) for 'team covers handicap' (``yes_side``) or its opposite, as a margin prop."""
    if team == game.home:
        return -handicap, yes_side
    if team == game.away:
        return handicap, not yes_side
    return None


@dataclass
class QuoteBook:
    """prop -> venue -> 'yes'|'no' -> cheapest Ask; plus per-reason skip counters."""

    quotes: dict[PropKey, dict[str, dict[str, Ask]]] = field(default_factory=dict)
    skipped: Counter[str] = field(default_factory=Counter)

    def add(self, prop: PropKey, venue: str, side: str, ask: Ask) -> None:
        if not 0.0 < ask.price < 1.0:
            self.skipped[f"{venue}:no_price"] += 1
            return
        cur = self.quotes.setdefault(prop, {}).setdefault(venue, {}).get(side)
        if cur is None or ask.price < cur.price:
            self.quotes[prop][venue][side] = ask

    def merge(self, other: QuoteBook) -> None:
        for prop, venues in other.quotes.items():
            for venue, sides in venues.items():
                for side, ask in sides.items():
                    self.add(prop, venue, side, ask)
        self.skipped.update(other.skipped)
