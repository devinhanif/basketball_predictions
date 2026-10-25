"""Cross-venue arbitrage and Pinnacle-lag checks. Pure functions; fees come from verified config.

Arbitrage on one proposition P: buy YES at venue X and NO at venue Y. Exactly one pays $1
(half-point lines, no push), so the net per contract is 1 - ask_yes - ask_no - (fee_x + fee_y) / n,
where each venue's fee is rounded once per ORDER at n contracts. The scan walks each side's
visible levels, so a margin is a margin at a stated size, not at the top price only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from nba.markets.fastarb.config import FastArbConfig
from nba.markets.fastarb.props import Ask, PropKey, QuoteBook
from nba.markets.fastarb.venues import PM_INTL, PM_US, K
from nba.parlay.ev import order_fee as kalshi_order_fee

FeeFn = Callable[[float, int], float]
MAX_SCAN = 500


def fee_functions(cfg: FastArbConfig) -> dict[str, FeeFn]:
    """venue -> fee in dollars for one order of (price, contracts). Config objects only."""
    return {
        K: lambda p, n: kalshi_order_fee(p, cfg.kalshi_fee, n, series=""),
        PM_US: cfg.us_fee.order_fee,
        PM_INTL: cfg.intl_fee.order_fee,
    }


def cost_of(levels: list[tuple[float, float]], n: int) -> tuple[float, float] | None:
    """(total dollars, last-fill price) to buy n contracts through the levels; None if too deep."""
    left, total, last = float(n), 0.0, 0.0
    for p, q in levels:
        take = min(left, q)
        total += take * p
        last = p
        left -= take
        if left <= 1e-9:
            return total, last
    return None


def _levels(a: Ask) -> list[tuple[float, float]]:
    return a.levels or [(a.price, float("inf"))]


@dataclass
class ArbResult:
    prop: PropKey
    yes_ask: Ask
    no_ask: Ask
    top_margin: (
        float  # at n = 1 contract, fees per order, as a fee-free-rounding-neutral top-of-book
    )
    margin: float  # per contract at the evaluation size
    n_eval: int
    max_n: int  # largest n (scan) whose margin is still >= min_gap; 0 if none
    depth_known: bool
    gross_gap: float  # 1 - ask_yes - ask_no, before fees
    notes: list[str] = field(default_factory=list)

    @property
    def venues(self) -> tuple[str, str]:
        return self.yes_ask.venue, self.no_ask.venue


def margin_at(yes: Ask, no: Ask, n: int, fees: dict[str, FeeFn]) -> float | None:
    cy, cn = cost_of(_levels(yes), n), cost_of(_levels(no), n)
    if cy is None or cn is None:
        return None
    fee = fees[yes.venue](cy[1], n) + fees[no.venue](cn[1], n)
    return 1.0 - (cy[0] + cn[0] + fee) / n


def evaluate_pair(
    prop: PropKey, yes: Ask, no: Ask, cfg: FastArbConfig, fees: dict[str, FeeFn]
) -> ArbResult:
    alert = cfg.section("alert")
    gap = cfg.min_gap
    n_ref = int(alert["fee_eval_contracts"])
    known = yes.depth_known() and no.depth_known()
    cap = MAX_SCAN if known else n_ref
    max_n = 0
    for n in range(1, cap + 1):
        mg = margin_at(yes, no, n, fees)
        if mg is None:
            break
        if mg >= gap:
            max_n = n
    n_eval, mg_eval = 1, None
    for n in range(n_ref, 0, -1):  # the largest order <= n_ref the visible depth can fill
        mg_eval = margin_at(yes, no, n, fees)
        if mg_eval is not None:
            n_eval = n
            break
    top = margin_at(yes, no, 1, fees)
    return ArbResult(
        prop,
        yes,
        no,
        top if top is not None else float("-inf"),
        mg_eval if mg_eval is not None else float("-inf"),
        n_eval,
        max_n,
        known,
        1.0 - yes.price - no.price,
    )


def find_arbs(
    book: QuoteBook, cfg: FastArbConfig, fees: dict[str, FeeFn], venues: tuple[str, ...]
) -> list[ArbResult]:
    """Every proposition quoted by two venues in ``venues``: both directions, best first."""
    out: list[ArbResult] = []
    for prop, per_venue in book.quotes.items():
        vs = [v for v in venues if v in per_venue]
        for x in vs:
            for y in vs:
                if x == y:
                    continue
                yes, no = per_venue[x].get("yes"), per_venue[y].get("no")
                if yes is None or no is None:
                    continue
                out.append(evaluate_pair(prop, yes, no, cfg, fees))
    out.sort(key=lambda r: r.margin, reverse=True)
    return out


def is_alertable(r: ArbResult, cfg: FastArbConfig) -> str:
    """'alert' | 'thin' | 'below_gap'."""
    if r.margin < cfg.min_gap:
        return "below_gap"
    if r.depth_known and r.max_n < int(cfg.section("alert")["min_depth_contracts"]):
        return "thin"
    return "alert"


# ------------------------------------------------------------------------------------- Pinnacle


@dataclass
class LagResult:
    prop: PropKey
    ask: Ask
    side: str  # yes | no
    pinnacle_p: float  # no-vig probability that this side wins
    edge: float  # pinnacle_p - ask
    net_edge: float  # edge minus the venue's per-contract fee at n_eval
    pinnacle_updated: str


def find_lags(
    book: QuoteBook,
    pin: dict[PropKey, float],
    cfg: FastArbConfig,
    fees: dict[str, FeeFn],
    venues: tuple[str, ...],
    pin_updated: dict[PropKey, str],
) -> list[LagResult]:
    """Sides priced at least ``lag_gap`` under Pinnacle's no-vig probability for that side."""
    n_ref = int(cfg.section("alert")["fee_eval_contracts"])
    out: list[LagResult] = []
    for prop, p_yes in pin.items():
        for v in venues:
            for side, p in (("yes", p_yes), ("no", 1.0 - p_yes)):
                ask = book.quotes.get(prop, {}).get(v, {}).get(side)
                if ask is None:
                    continue
                edge = p - ask.price
                if edge < cfg.lag_gap:
                    continue
                net = edge - fees[v](ask.price, n_ref) / n_ref
                out.append(LagResult(prop, ask, side, p, edge, net, pin_updated.get(prop, "")))
    out.sort(key=lambda r: r.edge, reverse=True)
    return out
