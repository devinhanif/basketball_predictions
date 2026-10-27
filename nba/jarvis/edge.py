"""Edge labels, stake sizing and arbitrage detection. Pure functions; every threshold from config.

The label is COMPUTED, never worded:

* arbitrage  both legs bought at their asks, fees included, cost < $1 per pair on VERIFIED fees
* strong     edge >= strong_cents vs the Pinnacle no-vig probability, after the venue fee
* thin       0 <= edge < strong_cents
* none       edge < 0 (a fair or worse price: entertainment), or no reference to measure against

``edge`` is ``fair - ask - fee`` per $1 contract, in cents. A stale Pinnacle reference caps a
label at thin (a line hours old is not evidence of a mispricing).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from nba.jarvis.config import JarvisConfig
from nba.jarvis.models import PlayLeg, Quote
from nba.parlay.ev import order_fee

CENT = 0.01


def fee_per_contract_raw(cfg: JarvisConfig, venue: str, price: float, series: str = "") -> float:
    """Fee on a ONE-contract order (rounded to the cent like a real order). Conservative for larger
    orders, whose per-contract fee is lower; labels use it so they hold at the size staked."""
    if not 0.0 < price < 1.0:
        return 0.0
    return _order_fee(cfg, venue, price, 1, series)


def order_cost(
    cfg: JarvisConfig, venue: str, price: float, contracts: int, series: str = ""
) -> float:
    """Dollars to buy ``contracts`` at ``price``: contracts x price + the per-order fee."""
    if contracts <= 0:
        return 0.0
    return contracts * price + _order_fee(cfg, venue, price, contracts, series)


def _order_fee(cfg: JarvisConfig, venue: str, price: float, contracts: int, series: str) -> float:
    """Per-order fee with the venue's own rounding: Kalshi rounds up to the cent, Polymarket US
    rounds half-even (banker's) to the cent (configs/jarvis.yaml ``fees.polymarket.rounding``)."""
    if cfg.fee_rounding.get(venue) != "bankers_cent":
        return order_fee(price, cfg.fee(venue), contracts, series=series)
    fee = cfg.fee(venue)
    raw = Decimal(str(fee.multiplier(series))) * Decimal(str(fee.rate("taker"))) * contracts
    raw *= Decimal(str(price)) * (Decimal(1) - Decimal(str(price)))
    return float(raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def devig_pair(p_a: float, p_b: float) -> tuple[float, float]:
    """Proportional no-vig split of two raw implied probabilities."""
    s = p_a + p_b
    if s <= 0:
        raise ValueError("implied probabilities must be positive")
    return p_a / s, p_b / s


def american_to_prob(odds: float) -> float:
    return 100.0 / (odds + 100.0) if odds > 0 else -odds / (-odds + 100.0)


def edge_cents(cfg: JarvisConfig, venue: str, fair: float, ask: float, series: str = "") -> float:
    """Expected profit per $1 contract in cents, after the venue's taker fee."""
    return (fair - ask - fee_per_contract_raw(cfg, venue, ask, series)) * 100.0


def classify(cfg: JarvisConfig, edge: float | None, *, capped: bool = False) -> str:
    """strong / thin / none from the measured edge (cents). ``None`` = nothing to measure against.
    ``capped`` (stale or skewed prices, an unconfirmed prop rule) holds a label at thin."""
    if edge is None or edge < 0.0:
        return "none"
    if edge >= cfg.strong_cents and not capped:
        return "strong"
    return "thin"


def max_price(
    cfg: JarvisConfig,
    venue: str,
    fair: float | None,
    ask: float,
    label: str,
    series: str = "",
) -> float:
    """Worst price (whole cents) at which the play keeps its label. ``none`` = today's ask."""
    if fair is None or label == "none":
        return ask
    need = (cfg.strong_cents if label == "strong" else 0.0) / 100.0
    best = ask
    for c in range(int(round(ask * 100)), 100):
        p = c / 100.0
        if fair - p - fee_per_contract_raw(cfg, venue, p, series) >= need - 1e-12:
            best = p
        else:
            break
    return best


def arb_max_price(
    cfg: JarvisConfig, venue: str, series: str, other_cost: float, ask: float
) -> float:
    """Highest price (whole cents) for this leg that keeps the pair's total cost below $1, the other
    leg held at ``other_cost`` (its price plus fee)."""
    best = ask
    for c in range(int(round(ask * 100)), 100):
        p = c / 100.0
        if p + fee_per_contract_raw(cfg, venue, p, series) + other_cost < 1.0 - 1e-12:
            best = p
        else:
            break
    return best


def kelly_fraction_of_bankroll(fair: float, cost: float) -> float:
    """Full-Kelly fraction for a $1-payout binary bought at total ``cost`` (price + fee)."""
    if not 0.0 < cost < 1.0:
        return 0.0
    return max(0.0, (fair - cost) / (1.0 - cost))


def side_allowed(cfg: JarvisConfig, kind: str, side: str) -> bool:
    """Player props are bought on the sides in ``cfg.props_sides`` only (Over / Yes); every other
    market kind keeps both sides."""
    return kind != "prop" or side in cfg.props_sides


def stake_for(cfg: JarvisConfig, label: str, *, none_level: bool = False) -> float:
    """Dollar budget from the label ladder (docs/TRADING_CONTEXT.md). ``none_level`` is the kill
    switch: every label stakes like an entertainment play."""
    return cfg.ladder["none" if none_level else label]


def contracts_for(
    cfg: JarvisConfig,
    venue: str,
    price: float,
    budget: float,
    remaining: float,
    series: str = "",
    max_contracts: int | None = None,
) -> int:
    """Whole contracts whose order cost fits ``min(budget, remaining)``; 0 if not even one fits."""
    room = min(budget, remaining)
    if room <= 0 or not 0.0 < price < 1.0:
        return 0
    n = int(room / (price + fee_per_contract_raw(cfg, venue, price, series)))
    if max_contracts is not None:
        n = min(n, max_contracts)
    while n > 0 and order_cost(cfg, venue, price, n, series) > room + 1e-9:
        n -= 1
    return n


@dataclass(frozen=True)
class Cover:
    """Two legs that together cover a game winner, and what they cost."""

    q1: Quote
    s1: str
    q2: Quote
    s2: str
    profit_cents: float  # per pair, after fees (1-contract fee: conservative)
    fees_verified: bool
    confirmed: bool  # both legs confirmed by a depth-aware source and read within the gap limit


def best_arbitrage(cfg: JarvisConfig, quotes: Iterable[Quote]) -> Cover | None:
    """Cheapest complete cover of a game winner across the given quotes.

    Complementary legs of one game: YES on team A and YES on team B (any venues), or YES on A and
    NO on A (two venues). A Polymarket US NO leg is skipped (the other team's YES token is the same
    bet). Needs asks on both legs and a positive profit after fees.
    """
    qs = [q for q in quotes if q.kind == "game_win" and not q.indicative]
    best: Cover | None = None
    legs: list[tuple[Quote, str, float]] = []
    for q in qs:
        for side in ("yes", "no"):
            if side == "no" and q.venue == "polymarket":
                continue
            if not side_allowed(cfg, q.kind, side):
                continue
            a = q.ask(side)
            if a is not None and 0.0 < a < 1.0:
                legs.append((q, side, a))
    for i, (q1, s1, a1) in enumerate(legs):
        for q2, s2, a2 in legs[i + 1 :]:
            if q1.game_id != q2.game_id or not _complementary(q1, s1, q2, s2):
                continue
            c1 = a1 + fee_per_contract_raw(cfg, q1.venue, a1, q1.series)
            c2 = a2 + fee_per_contract_raw(cfg, q2.venue, a2, q2.series)
            profit = (1.0 - c1 - c2) * 100.0
            if profit > cfg.arbitrage_min_cents and (best is None or profit > best.profit_cents):
                ver = cfg.fee(q1.venue).verified and cfg.fee(q2.venue).verified
                gap = abs((q1.ts - q2.ts).total_seconds())
                conf = q1.confirmed and q2.confirmed and gap <= cfg.confirmed_max_gap_s
                best = Cover(q1, s1, q2, s2, profit, ver, conf)
    return best


def _complementary(q1: Quote, s1: str, q2: Quote, s2: str) -> bool:
    """The two bought events are mutually exclusive and exhaustive for a game winner."""
    if q1.team_id is None or q2.team_id is None:
        return False
    if q1.ticker == q2.ticker and q1.venue == q2.venue:
        return False
    # event "team wins" for yes, "team loses" for no; a game winner has exactly two teams
    same_team = q1.team_id == q2.team_id
    if same_team:
        return s1 != s2
    return s1 == s2 == "yes"


def _no_desc(q: Quote) -> str:
    """The NO side of a quote in plain words (no double negatives)."""
    if q.kind == "prop" and q.threshold is not None:
        who = q.label.split(":")[0].strip()
        word = q.label.rsplit(" ", 1)[-1] if " " in q.label else "total"
        return f"{who}: fewer than {q.threshold:g} {word}"
    return q.label_no or q.label.replace(" to win", " to lose")


def leg_from_quote(q: Quote, side: str, price: float) -> PlayLeg:
    return PlayLeg(
        venue=q.venue,
        ticker=q.ticker,
        side=side,
        price=price,
        kind=q.kind,
        desc=q.label if side == "yes" else _no_desc(q),
        team_id=q.team_id,
        player_id=q.player_id,
        stat=q.stat,
        threshold=q.threshold,
        series=q.series,
        quote_ts=q.ts,
    )


def whole_cents(x: float) -> int:
    return int(math.floor(x * 100 + 0.5))
