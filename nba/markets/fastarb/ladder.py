"""Within-venue ladder arbitrage on nested events. Pure maths plus a back-to-back confirm step.

A ladder is a set of propositions on ONE game that nest: ``A(c) = {stat > c}`` shrinks as ``c``
grows. For ``c1 <= c2`` (same venue, same settlement source):

    buy YES on A(c1) at its ask  +  buy NO on A(c2) at its ask
    payoff = 1[A(c1)] + 1 - 1[A(c2)]  >=  1,   because A(c2) implies A(c1).

The payoff is $1 below c1 and above c2, and $2 in between. The guaranteed floor is therefore $1 per
pair, and the pair is risk-free when ``ask_yes(c1) + ask_no(c2) + both fees < 1``. Nothing here
depends on a tie rule: only half-point lines are used (integer stats and integer scores cannot
land on them), and the NBA plays overtime until one team leads, so a moneyline cannot tie.

Rung families: ``margin`` (moneyline c = 0 and "wins by over N" spreads, both teams, in home-margin
coordinates), ``total`` and ``team_total:<id>``. Player-stat rungs (``prop:*``) are parsed only so
a dry run can count them; they are EXCLUDED from alerts because a player who is inactive or never
takes the court settles every rung at a "fair price" that the contract does not promise is monotone.
Evidence (settled Kalshi markets, 2026-10-08..25, raw snapshots): 49 DNP ladders, 287 rung
pairs, every rung settled "scalar" at a fair price (e.g. 0.05 to 0.83; "Confirmed DNP"), strictly
decreasing in the threshold in all 287 pairs. That is an observed habit, not a contract term, so
the floor of $1 is not guaranteed and the rungs stay out.

Fees come from the verified config objects, per order, at the series' own multiplier. A pair is
alertable only with depth on both legs (Kalshi's top-of-book sizes, Polymarket's book levels) and
after a back-to-back re-fetch of both legs (:class:`LadderScanner.confirm`).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from nba.live import kalshi_pull as kp
from nba.markets.fastarb import venues
from nba.markets.fastarb.arb import MAX_SCAN, cost_of
from nba.markets.fastarb.config import FastArbConfig
from nba.markets.fastarb.polymarket_us import PolymarketUSClient
from nba.markets.fastarb.props import Ask, GameKey, PropKey, QuoteBook, is_half_point, team_id
from nba.markets.fastarb.venues import PM_US, K
from nba.parlay.ev import order_fee as kalshi_order_fee

TEAM_TOTAL_SERIES = "KXNBATEAMTOTAL"
PROP_SERIES = (
    "KXNBAPTS",
    "KXNBAREB",
    "KXNBAAST",
    "KXNBA3PT",
    "KXNBASTL",
    "KXNBABLK",
    "KXNBAPRA",
)
EXCLUDED_PROP = "player_prop_dnp_fair_price"
KALSHI_VOID_RISK = "void risk: a game voided >48h settles at a fair price (Kalshi rules)"

_TEAM_SUFFIX = re.compile(r"^([A-Z]+?)(\d+)$")

LegFee = Callable[[Ask, float, int], float]


def is_game_ladder_kind(kind: str) -> bool:
    return kind in ("margin", "total") or kind.startswith("team_total:")


def is_prop_kind(kind: str) -> bool:
    return kind.startswith("prop:")


# ---------------------------------------------------------------------------- fees (per leg)


def leg_fee_fn(cfg: FastArbConfig) -> LegFee:
    """Fee (dollars) for one order on one leg: venue from the Ask, Kalshi series from the ticker."""

    def fee(ask: Ask, price: float, n: int) -> float:
        if ask.venue == K:
            series = ask.ref.split("-")[0]
            return kalshi_order_fee(price, cfg.kalshi_fee, n, series=series)
        if ask.venue == PM_US:
            return cfg.us_fee.order_fee(price, n)
        raise ValueError(f"no verified fee object for venue {ask.venue!r}")

    return fee


# ------------------------------------------------------------------------ Kalshi parsers


def _ask_pair(
    book: QuoteBook, prop: PropKey, m: dict[str, Any], canonical_yes_is_contract_yes: bool
) -> None:
    ticker = str(m.get("ticker", ""))
    title = str(m.get("title", ticker))
    yes_ask, no_ask = kp.to_f(m.get("yes_ask_dollars")), kp.to_f(m.get("no_ask_dollars"))
    yes_sz, no_sz = kp.to_f(m.get("yes_ask_size_fp")), kp.to_f(m.get("yes_bid_size_fp"))
    for is_contract_yes, px, sz in ((True, yes_ask, yes_sz), (False, no_ask, no_sz)):
        if px is None:
            continue
        side_txt = "YES" if is_contract_yes else "NO"
        canonical_yes = is_contract_yes == canonical_yes_is_contract_yes
        book.add(
            prop,
            K,
            "yes" if canonical_yes else "no",
            Ask(
                K,
                px,
                f"Kalshi {side_txt} on '{title}' ({ticker})",
                ticker,
                [(px, sz)] if sz else [],
                f"Kalshi {ticker} {side_txt}",
            ),
        )


def _open_game(m: dict[str, Any], book: QuoteBook) -> GameKey | None:
    if str(m.get("status", "active")) not in ("active", "open"):
        book.skipped[f"{K}:not_open"] += 1
        return None
    ev = str(m.get("event_ticker", ""))
    teams, day = kp.event_teams(ev), kp.event_date(ev)
    if teams is None or day is None:
        book.skipped[f"{K}:bad_event"] += 1
        return None
    away, home = team_id(teams[0]), team_id(teams[1])
    if away is None or home is None:
        book.skipped[f"{K}:unmatched_team:{teams[0]}/{teams[1]}"] += 1
        return None
    return GameKey(away, home, day.isoformat())


def kalshi_team_total_quotes(markets: list[dict[str, Any]]) -> QuoteBook:
    """KXNBATEAMTOTAL ("Orlando over 97.5 points scored") -> ``team_total:<id>`` rungs."""
    book = QuoteBook()
    for m in markets:
        ticker = str(m.get("ticker", ""))
        if ticker.split("-")[0] != TEAM_TOTAL_SERIES:
            continue
        game = _open_game(m, book)
        if game is None:
            continue
        mm = _TEAM_SUFFIX.match(ticker.rsplit("-", 1)[-1])
        tid = team_id(mm[1]) if mm else None
        strike = kp.to_f(m.get("floor_strike"))
        if tid is None or tid not in (game.away, game.home):
            book.skipped[f"{K}:unmatched_team_total:{ticker}"] += 1
            continue
        if strike is None or not is_half_point(strike) or m.get("strike_type") != "greater":
            book.skipped[f"{K}:bad_line"] += 1
            continue
        _ask_pair(book, PropKey(game, f"team_total:{tid}", strike), m, True)
    return book


def kalshi_prop_quotes(markets: list[dict[str, Any]]) -> QuoteBook:
    """Player-stat "X+" markets -> ``prop:<market base>`` rungs. DRY-RUN COUNTING ONLY: the live
    poller never calls this and :func:`is_ladder_alertable` refuses these rungs."""
    book = QuoteBook()
    for m in markets:
        ticker = str(m.get("ticker", ""))
        if ticker.split("-")[0] not in PROP_SERIES:
            continue
        game = _open_game(m, book)
        strike = kp.to_f(m.get("floor_strike"))
        if game is None or strike is None or not is_half_point(strike):
            continue
        _ask_pair(book, PropKey(game, f"prop:{ticker.rsplit('-', 1)[0]}", strike), m, True)
    return book


def kalshi_ladder_quotes(markets: list[dict[str, Any]]) -> QuoteBook:
    """Game, spread, total and team-total markets -> one book (the live ladder universe)."""
    book = venues.kalshi_quotes(
        [m for m in markets if str(m.get("ticker", "")).split("-")[0] != TEAM_TOTAL_SERIES]
    )
    tt = kalshi_team_total_quotes(markets)
    book.merge(tt)
    return book


# ------------------------------------------------------------------------------ evaluation


@dataclass
class LadderResult:
    """Buy YES on the lower rung and NO on the higher rung (same venue)."""

    venue: str
    lo: PropKey
    hi: PropKey
    yes: Ask  # YES of A(lo)
    no: Ask  # NO of A(hi)
    gross_gap: float  # 1 - ask_yes - ask_no, before fees
    margin: float  # guaranteed profit per pair after both fees, at n_eval pairs
    n_eval: int
    max_n: int  # largest pair count (scan) whose margin is still >= min_gap
    depth_known: bool
    top_margin: float
    notes: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return self.lo.kind

    @property
    def excluded(self) -> str:
        return EXCLUDED_PROP if is_prop_kind(self.lo.kind) else ""

    def label(self) -> str:
        if self.lo.kind == "margin":
            return f"margin>{self.lo.c:g} vs margin>{self.hi.c:g}"
        base = self.lo.label().rsplit(">", 1)[0]
        return f"{base}>{self.lo.c:g} vs >{self.hi.c:g}"


def pair_margin(yes: Ask, no: Ask, n: int, cfg: FastArbConfig, fee: LegFee) -> float | None:
    """Profit per pair at the $1 floor; each leg's fee is rounded once per order at n contracts."""
    levels_y = yes.levels or [(yes.price, float("inf"))]
    levels_n = no.levels or [(no.price, float("inf"))]
    cy, cn = cost_of(levels_y, n), cost_of(levels_n, n)
    if cy is None or cn is None:
        return None
    return 1.0 - (cy[0] + cn[0] + fee(yes, cy[1], n) + fee(no, cn[1], n)) / n


def evaluate_ladder(
    venue: str,
    lo: PropKey,
    hi: PropKey,
    yes: Ask,
    no: Ask,
    cfg: FastArbConfig,
    fee: LegFee,
    max_scan: int = MAX_SCAN,
) -> LadderResult:
    n_ref = int(cfg.section("alert")["fee_eval_contracts"])
    gap = float(cfg.section("ladder")["min_gap"])
    known = yes.depth_known() and no.depth_known()
    cap = max_scan if known else n_ref
    max_n = 0
    for n in range(1, cap + 1):
        mg = pair_margin(yes, no, n, cfg, fee)
        if mg is None:
            break
        if mg >= gap:
            max_n = n
    n_eval, mg_eval = 1, None
    for n in range(n_ref, 0, -1):
        mg_eval = pair_margin(yes, no, n, cfg, fee)
        if mg_eval is not None:
            n_eval = n
            break
    top = pair_margin(yes, no, 1, cfg, fee)
    return LadderResult(
        venue,
        lo,
        hi,
        yes,
        no,
        1.0 - yes.price - no.price,
        mg_eval if mg_eval is not None else float("-inf"),
        n_eval,
        max_n,
        known,
        top if top is not None else float("-inf"),
    )


def find_ladder_violations(
    book: QuoteBook,
    venue: str,
    cfg: FastArbConfig,
    fee: LegFee,
    *,
    min_gross: float = 0.0,
    include_props: bool = False,
    max_scan: int = MAX_SCAN,
) -> list[LadderResult]:
    """Every pair c1 <= c2 of one (game, kind) ladder on ``venue`` with gross gap > ``min_gross``.

    ``min_gross = 0`` returns the "apparent violations" (the two asks sum under $1 before fees);
    the caller decides alertability with :func:`is_ladder_alertable`. All pairs, not just
    neighbours: bid/ask spreads accumulate, so a far pair can be violated when no neighbour is."""
    groups: dict[tuple[GameKey, str], list[tuple[PropKey, dict[str, Ask]]]] = {}
    for prop, per in book.quotes.items():
        sides = per.get(venue)
        if not sides:
            continue
        if is_prop_kind(prop.kind) and not include_props:
            continue
        if not (is_game_ladder_kind(prop.kind) or is_prop_kind(prop.kind)):
            continue
        groups.setdefault((prop.game, prop.kind), []).append((prop, sides))
    out: list[LadderResult] = []
    for rungs in groups.values():
        rungs.sort(key=lambda r: r[0].c)
        for i, (p1, s1) in enumerate(rungs):
            yes = s1.get("yes")
            if yes is None:
                continue
            for p2, s2 in rungs[i:]:
                no = s2.get("no")
                if no is None or (p2 == p1 and no.ref == yes.ref):
                    continue
                if 1.0 - yes.price - no.price <= min_gross:
                    continue
                out.append(evaluate_ladder(venue, p1, p2, yes, no, cfg, fee, max_scan))
    out.sort(key=lambda r: r.margin, reverse=True)
    return out


def is_ladder_alertable(r: LadderResult, cfg: FastArbConfig) -> str:
    """'alert' | 'excluded' | 'below_gap' | 'unknown_depth' | 'thin'.

    ``excluded`` = player-stat rungs (DNP fair-price settlement). ``unknown_depth`` = a leg with
    no sized price (an event quote, or a Kalshi side with zero size): a candidate, not an alert."""
    lc = cfg.section("ladder")
    if r.excluded and not bool(lc["include_player_props"]):
        return "excluded"
    if r.margin < float(lc["min_gap"]):
        return "below_gap"
    if not (r.yes.depth_known() and r.no.depth_known()):
        return "unknown_depth"
    if r.max_n < int(lc["min_depth_pairs"]):
        return "thin"
    return "alert"


# ----------------------------------------------------------------------- confirm / scan


class LadderScanner:
    """Candidate selection and the back-to-back confirm for one venue pair of legs.

    ``fetcher`` has ``get(url, params=None) -> Response | None`` (a ``nba.live.http.Fetcher``, the
    package's PoliteFetcher, or any object with that method). Public GETs only."""

    def __init__(
        self,
        cfg: FastArbConfig,
        fetcher: Any,
        pm: PolymarketUSClient,
        *,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg, self.f, self.pm, self.mono = cfg, fetcher, pm, mono
        self.fee = leg_fee_fn(cfg)

    # ---- Polymarket US candidate lines (event quotes select; books decide)
    def pm_candidate_slugs(self, event: dict[str, Any]) -> list[str]:
        """Market slugs of every pair whose EVENT-quote gross gap is within ``pm_select_slack`` of
        profitable. Event quotes can be minutes old, so they only choose which books to read."""
        lc = self.cfg.section("ladder")
        meta = venues.us_quotes(event, self.cfg.us_fee.coefficient)
        cands = find_ladder_violations(
            meta, PM_US, self.cfg, self.fee, min_gross=-float(lc["pm_select_slack"])
        )
        slugs: list[str] = []
        for r in cands:
            for ref in (r.yes.ref, r.no.ref):
                if ref not in slugs:
                    slugs.append(ref)
        return slugs[: int(lc["max_books_per_game"])]

    # ---- the confirm step
    def confirm(
        self, r: LadderResult, event: dict[str, Any] | None = None
    ) -> tuple[LadderResult | None, dict[str, Any]]:
        """Re-fetch both legs back to back; the pair stands only if it still clears.

        None when a leg cannot be re-read, the two reads span more than
        ``alert.confirm_max_gap_s``, a market is no longer open, a ref changed, or the margin or
        depth no longer clears the ladder gates. ``info`` carries the evidence either way."""
        info: dict[str, Any] = {"result": "refused"}
        if r.venue == PM_US and event is None:
            info["reason"] = "no_event_metadata"
            return None, info
        legs = ((r.lo, "yes", r.yes), (r.hi, "no", r.no))
        bodies: list[Any] = []
        t0 = self.mono()
        for _prop, _side, ask in legs:
            if r.venue == K:
                bodies.append(kp.get_market(self.f, ask.ref))
            else:
                bodies.append(self.pm.book(ask.ref))
        gap = self.mono() - t0
        info["fetch_window_s"] = round(gap, 2)
        if any(b is None for b in bodies):
            info["reason"] = "refetch_failed"
            return None, info
        if gap > float(self.cfg.section("alert")["confirm_max_gap_s"]):
            info["reason"] = "legs_too_far_apart"
            return None, info
        fresh: list[Ask | None] = []
        for (prop, side, ask), body in zip(legs, bodies, strict=True):
            if r.venue == K:
                qb = kalshi_ladder_quotes([body])
                qb.merge(kalshi_prop_quotes([body]))
                a = qb.quotes.get(prop, {}).get(K, {}).get(side)
                info[f"{side}_updated_time"] = body.get("updated_time")
            else:
                assert event is not None
                one = dict(event)
                one["markets"] = [m for m in event.get("markets", []) if m.get("slug") == ask.ref]
                qb = venues.us_quotes(one, self.cfg.us_fee.coefficient, {ask.ref: body})
                a = qb.quotes.get(prop, {}).get(PM_US, {}).get(side)
                info[f"{side}_transact_time"] = (body.get("marketData") or {}).get("transactTime")
            fresh.append(a if a is not None and a.ref == ask.ref else None)
        ay, an = fresh
        if ay is None or an is None or not (ay.depth_known() and an.depth_known()):
            info["reason"] = "side_missing_or_unsized_after_refetch"
            return None, info
        new = evaluate_ladder(r.venue, r.lo, r.hi, ay, an, self.cfg, self.fee)
        info["confirmed_margin"] = round(new.margin, 4)
        verdict = is_ladder_alertable(new, self.cfg)
        if verdict != "alert":
            info["reason"] = f"after_refetch:{verdict}"
            return None, info
        info["result"] = "confirmed"
        info["gap_s"] = gap
        return new, info
