"""The content of one game card as plain data. No behaviour, no formatting, no Discord."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

PHASES = ("pretip", "live", "halftime", "final")


@dataclass(frozen=True)
class OverRow:
    """One over (Yes on a 'N+ stat' prop) exactly as a ``Play`` computed it."""

    pick_id: str
    player: str
    stat: str
    line: float
    ask: float  # dollars per $1 contract
    fee_c: float  # fee per contract, cents
    fair: float | None  # fair price used by the edge (probability)
    pin: float | None  # sharp (Pinnacle no-vig) probability, before any blend
    model: float | None  # our stored model's probability
    edge_c: float  # EV per contract after fee, cents
    ref_age_min: float | None
    label: str
    ticker: str
    venue: str
    player_id: int | None
    includes_p_plays: bool = False
    shown_at: str = ""
    quote_age_min: float | None = None  # age of the price when this card priced it (not frozen)
    stale: str = ""  # "" = priced now; else why the shown price is old (the over stays on the card)


@dataclass(frozen=True)
class HalfLeg:
    """One halftime entry check: a pre-tip over (or a scorer pick) against the live price."""

    player: str
    stat: str
    line: int
    cur: int | None  # the stat so far (None: not read)
    ask_c: int | None
    bid_c: int | None
    fee_c: int | None
    p_model: float | None
    edge_c: float | None  # model probability minus (ask + fee), cents
    verdict: str  # enter | skip
    why: str  # plain-words reason, built from the numbers above
    from_pick: bool = True


@dataclass(frozen=True)
class SettledRow:
    player: str
    stat: str
    line: float
    ask: float
    fee_c: float
    edge_c: float
    status: str  # settled | void
    won: bool | None
    units: float


@dataclass(frozen=True)
class Recap:
    rows: tuple[SettledRow, ...] = ()
    waiting: int = 0
    n_hit: int = 0
    n_miss: int = 0
    n_void: int = 0
    units: float = 0.0
    season_n: int = 0
    season_hit: int = 0
    season_units: float = 0.0
    season_dates: int = 0


@dataclass(frozen=True)
class Knows:
    out: dict[str, list[str]] = field(default_factory=dict)  # team -> "Name (reason)"
    gtd: dict[str, list[str]] = field(default_factory=dict)  # team -> "Name (questionable)"
    starters: dict[str, list[str]] = field(default_factory=dict)
    starters_note: dict[str, str] = field(
        default_factory=dict
    )  # team -> confirmed | expected | ...
    report_age_min: float | None = None
    lineup_age_min: float | None = None
    note: str = ""


@dataclass(frozen=True)
class CardData:
    game_id: str
    away: str
    home: str
    tip: datetime | None  # naive UTC
    phase: str
    as_of: datetime  # naive UTC
    away_score: int | None = None
    home_score: int | None = None
    clock: str = ""
    overs: tuple[OverRow, ...] = ()
    overs_frozen: bool = False  # True once the game has tipped: the picks shown pre-tip
    overs_note: str = ""  # said instead of the default no-over sentence when we cannot know
    half: tuple[HalfLeg, ...] = ()
    knows: Knows = field(default_factory=Knows)
    recap: Recap | None = None
    alert_note: str = ""
    feed_age_min: float | None = None
