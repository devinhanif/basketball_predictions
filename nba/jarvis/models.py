"""Plain dataclasses shared by the JARVIS modules. No behaviour here."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Game:
    game_id: str
    away_team: int
    home_team: int
    away_abbr: str
    home_abbr: str
    tipoff: datetime | None  # naive UTC, REAL scheduled tip (None = unknown: no play is emitted)

    @property
    def matchup(self) -> str:
        return f"{self.away_abbr}@{self.home_abbr}"


@dataclass(frozen=True)
class Quote:
    """One tradable YES contract on a venue (a team to win, or a player stat line). ``yes_bid`` and
    ``yes_ask`` are dollars in (0, 1). Buying YES costs the ask; buying NO costs ``1 - yes_bid``."""

    venue: str
    ticker: str
    game_id: str
    kind: str  # "game_win" | "prop"
    ts: datetime  # naive UTC, when the venue was read
    yes_bid: float | None
    yes_ask: float | None
    team_id: int | None = None
    player_id: int | None = None
    stat: str | None = None
    threshold: float | None = None
    series: str = ""
    label: str = ""  # human description of the YES event, e.g. "LAL to win"
    indicative: bool = False  # True = last/mid only, never offered as a tradable price
    mid: float | None = None  # vigged implied probability for an indicative quote
    label_no: str = ""  # plain words for the NO contract (the other side to win), e.g. "LAC to win"
    confirmed: bool = (
        False  # set only by a depth-aware live source (QUOTE_SOURCES hook), never here
    )

    def ask(self, side: str) -> float | None:
        if side == "yes":
            return self.yes_ask
        return None if self.yes_bid is None else 1.0 - self.yes_bid

    def bid(self, side: str) -> float | None:
        if side == "yes":
            return self.yes_bid
        return None if self.yes_ask is None else 1.0 - self.yes_ask


@dataclass(frozen=True)
class Ref:
    """Pinnacle no-vig probability of a quote's YES event."""

    p_yes: float
    ts: datetime  # naive UTC, when the book was read
    line: str = ""  # e.g. "o24.5" or "ml"
    book: str = "pinnacle"


@dataclass(frozen=True)
class PlayLeg:
    venue: str
    ticker: str
    side: str  # "yes" | "no"
    price: float  # the ask we would pay, dollars per $1 contract
    kind: str
    desc: str
    team_id: int | None = None
    player_id: int | None = None
    stat: str | None = None
    threshold: float | None = None
    series: str = ""
    quote_ts: datetime | None = None


@dataclass(frozen=True)
class Play:
    """One bet (one leg) or one arbitrage bundle (two legs). Everything numeric is computed."""

    game_id: str
    matchup: str
    tipoff: datetime | None
    legs: tuple[PlayLeg, ...]
    label: str  # arbitrage | strong | thin | none
    edge_cents: float  # after fee, per $1 contract (arbitrage: guaranteed profit per pair)
    fair_prob: float | None  # Pinnacle no-vig P(first leg wins); None for arbitrage
    fee_cents: float
    max_price: float  # worst price at which the play keeps its label (none: today's ask)
    contracts: int
    stake_usd: float  # contracts x ask + order fee, all legs
    reason: str  # one computed line: where the number comes from
    model_prob: float | None = None
    ref_age_min: float | None = None
    notes: tuple[str, ...] = ()
    inputs: dict[str, object] = field(default_factory=dict)  # for the input hash
    pin_prob: float | None = (
        None  # Pinnacle no-vig fair price for the bought side (before the blend)
    )
    w_model: float | None = None  # weight on (model - Pinnacle) in fair_prob; 0 until earned
    key_suffix: str = ""  # makes the dedupe key unique for picks and tickets that must not dedupe

    @property
    def payout_usd(self) -> float:
        """What the ticket pays if it wins: $1 per contract (per pair for a two-leg cover)."""
        return float(self.contracts)

    @property
    def multiple(self) -> float | None:
        return None if self.stake_usd <= 0 else self.payout_usd / self.stake_usd

    @property
    def key(self) -> str:
        """Dedupe key: same game, same legs (venue, ticker, side), same label."""
        legs = "|".join(f"{leg.venue}:{leg.ticker}:{leg.side}" for leg in self.legs)
        return f"{self.game_id}/{legs}/{self.label}{self.key_suffix}"
