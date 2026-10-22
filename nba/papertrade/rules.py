"""Pure rule math: entry sizing, shrunk probabilities, candidate screen, slot choice, exit rule.

Everything here is a function of its arguments (no clock, no network, no DB) so each rule is tested
on fixtures. Fees come from the verified ``FeeModel`` only.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, replace
from datetime import datetime

from nba.papertrade.config import PaperConfig
from nba.parlay.config import FeeModel
from nba.parlay.ev import order_fee


class EntryRefused(Exception):
    """An entry at or after tip (or inside the lead time) is refused, never logged as a position."""


@dataclass(frozen=True)
class Leg:
    ticker: str
    event_ticker: str
    series: str
    game_id: str
    tipoff: datetime  # naive UTC
    stat: str  # pts|reb|ast|fg3m|win
    player_id: int
    threshold: float
    side: str  # yes | no
    p_model: float  # P(this leg's side hits) from the stored forecast
    bid: float  # bid for THIS side
    ask: float  # ask for THIS side
    bid_size: float
    ask_size: float

    @property
    def mid(self) -> float:
        return 0.5 * (self.bid + self.ask)

    @property
    def spread(self) -> float:
        return self.ask - self.bid


def shrunk(p_model: float, mid: float, w: float) -> float:
    return w * p_model + (1.0 - w) * mid


def plan_entry(price: float, fee: FeeModel, stake: float, series: str = "") -> tuple[int, float]:
    """(contracts, total entry fee) for ~``stake`` dollars at ``price``: the integer contract count
    whose cost incl. the per-order fee fits in the stake (at least 1)."""
    if not 0.0 < price < 1.0:
        raise ValueError("price must be inside (0, 1)")
    n = max(1, math.floor(stake / (price + fee.rate("taker") * price * (1.0 - price))))
    while n > 1 and n * price + order_fee(price, fee, n, series=series) > stake:
        n -= 1
    return n, order_fee(price, fee, n, series=series)


def ev_per_dollar(
    prob: float, price: float, fee: FeeModel, stake: float, series: str = ""
) -> float:
    """EV per $ of cost for a unit paying 1 with probability ``prob`` bought at ``price`` (ask)."""
    n, f = plan_entry(price, fee, stake, series)
    cost = n * price + f
    return (n * prob - cost) / cost


def screen_leg(leg: Leg, cfg: PaperConfig, *, now: datetime) -> tuple[bool, str]:
    """(passes, reason). Liquidity first (spread, quotes, size), then price band, then EV."""
    if leg.stat == "win" and not cfg.trade_win_legs:
        return False, "leg_type_disabled"
    if leg.bid <= 0.0 or leg.ask <= 0.0 or leg.ask >= 1.0:
        return False, "one_sided_or_unquoted"
    if leg.spread > cfg.max_spread + 1e-9:
        return False, "spread_over_cap"
    if not cfg.min_leg_ask <= leg.ask <= cfg.max_leg_ask:
        return False, "ask_outside_band"
    if (leg.tipoff - now).total_seconds() < cfg.entry_lead_min * 60:
        return False, "tip_too_close_or_started"
    n, _ = plan_entry(leg.ask, cfg.fee, cfg.stake_usd, leg.series)
    if leg.ask_size < n:
        return False, "ask_size_below_order"
    ev = ev_per_dollar(
        shrunk(leg.p_model, leg.mid, cfg.shrink), leg.ask, cfg.fee, cfg.stake_usd, leg.series
    )
    if ev < cfg.min_ev_per_dollar:
        return False, "ev_below_floor"
    return True, "ok"


def leg_ev(leg: Leg, cfg: PaperConfig) -> float:
    return ev_per_dollar(
        shrunk(leg.p_model, leg.mid, cfg.shrink), leg.ask, cfg.fee, cfg.stake_usd, leg.series
    )


@dataclass(frozen=True)
class Plan:
    slot: str
    legs: tuple[Leg, ...]
    n: int
    entry_price: float  # single: the ask; parlay: product of leg asks (synthetic, labelled)
    entry_fee: float
    cost: float
    p_model: float
    p_shrunk: float
    ev_per_dollar: float
    label: str


def build_plan(slot: str, legs: tuple[Leg, ...], cfg: PaperConfig) -> Plan:
    price = math.prod(leg.ask for leg in legs)
    n, fee = plan_entry(price, cfg.fee, cfg.stake_usd)
    cost = n * price + fee
    p_s = math.prod(shrunk(leg.p_model, leg.mid, cfg.shrink) for leg in legs)
    label = (
        "single: real ask"
        if len(legs) == 1
        else "parlay: product of leg asks (synthetic; a Kalshi combo quote may differ)"
    )
    return Plan(
        slot,
        legs,
        n,
        price,
        fee,
        cost,
        math.prod(leg.p_model for leg in legs),
        p_s,
        (n * p_s - cost) / cost,
        label,
    )


def _windows_distinct(legs: tuple[Leg, ...]) -> bool:
    return (
        len({leg.game_id for leg in legs}) == len(legs)
        and len({leg.tipoff for leg in legs}) == len(legs)
        and min(leg.ask_size for leg in legs) > 0
    )


def choose_slot(slot: str, passing: list[Leg], used: set[str], cfg: PaperConfig) -> Plan | None:
    """Best plan for one slot from legs not already used. single: best leg by shrunk EV per $.
    parlayK: best K-leg combination from the top ``top_legs_for_parlays`` legs, all in different
    games and tip windows, every leg's ask size covering the contract count. Cross-game legs are
    treated as independent (docs/BEST_PRACTICES.md section 5: cross-game residual r is about 0)."""
    pool = sorted((x for x in passing if x.ticker not in used), key=lambda x: -leg_ev(x, cfg))
    if slot == "single":
        return build_plan(slot, (pool[0],), cfg) if pool else None
    k = int(slot.removeprefix("parlay"))
    best: Plan | None = None
    for combo in itertools.combinations(pool[: cfg.top_legs_for_parlays], k):
        if not _windows_distinct(combo):
            continue
        plan = build_plan(slot, combo, cfg)
        if min(leg.ask_size for leg in combo) < plan.n:
            continue
        if plan.ev_per_dollar < cfg.min_ev_per_dollar:
            continue
        if best is None or plan.ev_per_dollar > best.ev_per_dollar:
            best = plan
    return best


def control_leg(leg: Leg, other_side: Leg) -> Leg:
    """The market-favourite side of the same market: YES if the YES mid is at least 0.5 else NO.
    ``leg`` is the strategy leg; ``other_side`` its flipped twin. Probabilities in the control are
    the market's own mid (it takes no view)."""
    yes, no = (leg, other_side) if leg.side == "yes" else (other_side, leg)
    fav = yes if yes.mid >= 0.5 else no
    return replace(fav, p_model=fav.mid)


def flipped(leg: Leg) -> Leg:
    """Same market, other side: bid/ask swap through 1 - price, p_model complement."""
    return Leg(
        leg.ticker, leg.event_ticker, leg.series, leg.game_id, leg.tipoff, leg.stat,
        leg.player_id, leg.threshold, "no" if leg.side == "yes" else "yes",
        1.0 - leg.p_model, 1.0 - leg.ask, 1.0 - leg.bid, leg.ask_size, leg.bid_size,
    )  # fmt: skip


# --------------------------------------------------------------------------- exit rule
@dataclass(frozen=True)
class LegMark:
    side: str
    p_model: float
    bid: float  # bid for the held side
    ask: float
    started: bool
    settled: float | None  # 1.0 / 0.0 once the market has a result for the held side


def value_position(marks: list[LegMark], cfg: PaperConfig) -> tuple[float, float, float, int, int]:
    """(fair, hold, exit_est, open_legs, started_legs) per contract.
    fair = product of leg mids (settled legs 1 or 0); hold = same but a not-yet-started leg uses
    shrink * model + (1 - shrink) * mid; exit_est = product of leg bids (settled legs 1 or 0)."""
    fair = hold = exit_est = 1.0
    open_legs = started = 0
    for m in marks:
        if m.settled is not None:
            fair *= m.settled
            hold *= m.settled
            exit_est *= m.settled
            started += 1
            continue
        open_legs += 1
        started += int(m.started)
        mid = 0.5 * (m.bid + m.ask)
        fair *= mid
        hold *= mid if m.started else shrunk(m.p_model, mid, cfg.shrink)
        exit_est *= m.bid
    return fair, hold, exit_est, open_legs, started


def exit_decision(
    marks: list[LegMark], n: int, cfg: PaperConfig, sizes_ok: bool = True
) -> tuple[str, float, float, float]:
    """('hold'|'exit'|'wait_pretip'|'dead'|'settling'|'size_blocked', fair, hold, exit_est)."""
    fair, hold, est, open_legs, started = value_position(marks, cfg)
    if fair == 0.0 and open_legs == 0:
        return "dead", fair, hold, est
    if open_legs == 0:
        return "settling", fair, hold, est
    if cfg.exit_from_first_tip and started == 0:
        return "wait_pretip", fair, hold, est
    if est > 0.0 and est >= max(fair, hold) - cfg.exit_margin:
        return ("exit" if sizes_ok else "size_blocked"), fair, hold, est
    return "hold", fair, hold, est


def exit_proceeds(price: float, n: int, cfg: PaperConfig) -> tuple[float, float]:
    """(fee, net proceeds) of selling ``n`` contracts at ``price`` (taker fee on the sale)."""
    fee = order_fee(price, cfg.fee, n) if 0.0 < price < 1.0 else 0.0
    return fee, n * price - fee
