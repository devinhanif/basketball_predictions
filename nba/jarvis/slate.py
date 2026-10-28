"""Gather the slate's state and turn it into sized, labelled plays (always a best play per game).

Pipeline: games (real tips) -> venue quotes (Kalshi snapshot, Polymarket gamma, hook sources) ->
Pinnacle no-vig reference -> candidate bets -> best per game -> label, max price, stake. The label
and every number are computed here from data; text is composed later and cannot change them.

Hook: ``QUOTE_SOURCES`` takes callables ``(cfg, games, slate, now) -> list[Quote]``. The fast
Kalshi/Polymarket arb poller (branch agent/market-fast-arb, not merged when this was built) plugs in
by appending one; until then JARVIS polls the snapshot store and the public gamma API itself.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Protocol

from nba.jarvis import edge as E
from nba.jarvis.book import KillState, PaperBook, kill_switch, sha
from nba.jarvis.config import JarvisConfig
from nba.jarvis.models import Game, Play, PlayLeg, Quote, Ref
from nba.jarvis.polymarket import PolyResult, load_polymarket_quotes
from nba.jarvis.sources import (
    PinnacleRefs,
    age_min,
    load_games,
    load_kalshi_quotes,
    load_pinnacle,
    utcnow,
)
from nba.jarvis.weights import blend, load_weights, stat_key

QuoteSource = Callable[[JarvisConfig, list[Game], date, datetime], list[Quote]]
QUOTE_SOURCES: list[QuoteSource] = []  # hook for the fast-arb poller (see module docstring)

MIN_ASK, MAX_ASK = 0.02, 0.98


class ModelLike(Protocol):
    def p_yes(self, q: Quote) -> float | None: ...


@dataclass
class SlateState:
    slate: date
    now: datetime
    games: list[Game]
    quotes: list[Quote]
    refs: PinnacleRefs
    notes: list[str] = field(default_factory=list)
    poly: PolyResult | None = None
    model: ModelLike | None = None
    weights: dict[str, float] = field(default_factory=dict)

    def game(self, gid: str) -> Game | None:
        return next((g for g in self.games if g.game_id == gid), None)

    def tradable(self, cfg: JarvisConfig) -> list[Quote]:
        """Quotes fresh enough to act on, on games that have not (almost) tipped."""
        out = []
        for q in self.quotes:
            g = self.game(q.game_id)
            if q.indicative or g is None or g.tipoff is None:
                continue
            if (g.tipoff - self.now) < timedelta(minutes=cfg.min_minutes_to_tip):
                continue
            if age_min(q.ts, self.now) > cfg.max_quote_age_min:
                continue
            out.append(q)
        return out


def gather(
    cfg: JarvisConfig,
    slate: date,
    now: datetime | None = None,
    *,
    poly_client: object | None = None,
    model: ModelLike | None = None,
    use_polymarket: bool = True,
) -> SlateState:
    n = now or utcnow()
    games = load_games(cfg, slate)
    notes: list[str] = []
    if not games:
        notes.append(f"no games found for {slate}")
    no_tip = [g.matchup for g in games if g.tipoff is None]
    if no_tip:
        notes.append(f"no real tip time for {', '.join(no_tip)}: no play is offered for them")
    quotes = load_kalshi_quotes(cfg, games, slate, n)
    if not quotes and games:
        notes.append("no Kalshi snapshot rows before now (snapshot job down?)")
    poly = None
    ahead = [g for g in games if g.tipoff is not None and g.tipoff > n]
    if use_polymarket and cfg.polymarket.get("enabled", True) and ahead:
        poly = load_polymarket_quotes(cfg, ahead, slate, n, poly_client)  # type: ignore[arg-type]
        if poly.error:
            notes.append(f"Polymarket US unavailable ({poly.error}); Kalshi + Pinnacle only")
        else:
            quotes += poly.quotes
        if poly.unmatched:
            notes.append(f"{len(poly.unmatched)} Polymarket market(s) with unmatched team names")
    for src in QUOTE_SOURCES:
        quotes += src(cfg, games, slate, n)
    quotes = newest_per_ticker(quotes)
    refs = load_pinnacle(cfg, games, slate, n)
    if not refs.win and games:
        notes.append("no Pinnacle reference captured yet: every label is capped at none")
    return SlateState(slate, n, games, quotes, refs, notes, poly, model, load_weights(cfg))


# ------------------------------------------------------------------------------ candidates


def newest_per_ticker(quotes: list[Quote]) -> list[Quote]:
    """One quote per (venue, ticker): a confirmed live read beats a snapshot, else the newest."""
    best: dict[tuple[str, str], Quote] = {}
    for q in quotes:
        k = (q.venue, q.ticker)
        cur = best.get(k)
        if cur is None or (q.confirmed, q.ts) > (cur.confirmed, cur.ts):
            best[k] = q
    return list(best.values())


def _c(x: float) -> str:
    return f"{x * 100:.1f}c"


def single_candidate(cfg: JarvisConfig, st: SlateState, q: Quote, side: str) -> Play | None:
    if not E.side_allowed(cfg, q.kind, side, q.venue):
        return None  # a player-prop No is offered only on a venue whose sides allow it
    ask = q.ask(side)
    if ask is None or not MIN_ASK <= ask <= MAX_ASK:
        return None
    g = st.game(q.game_id)
    if g is None:
        return None
    ref = st.refs.for_quote(q)
    fee = E.fee_per_contract_raw(cfg, q.venue, ask, q.series)
    fair: float | None = None
    pin_fair: float | None = None
    w: float | None = None
    edge: float | None = None
    ref_age: float | None = None
    capped = False
    notes: list[str] = []
    q_age = age_min(q.ts, st.now)
    reason: str
    if ref is not None:
        p_over = ref.p_yes
        if q.kind == "prop" and not cfg.kalshi_prop_dnp_rule_verified:
            # Pinnacle voids a DNP, Kalshi's rule is unconfirmed: price on P(plays) x P(over|plays)
            pp = _p_play(st, q)
            if pp is None:
                capped = True
                notes.append("prop DNP rule unverified and no P(plays): capped at thin")
            else:
                notes.append("fair price includes P(plays) because Kalshi's DNP rule is unverified")
            pp1 = 1.0 if pp is None else pp
            fair = p_over * pp1 if side == "yes" else (1.0 - p_over) * pp1
            if side == "no":
                # a prop No also depends on how a DNP settles (a No may win or void); until read,
                # the label is held at thin whatever P(plays) says (Devin, 2026-10-27)
                capped = True
                notes.append("prop No while Kalshi's DNP rule is unverified: label capped at thin")
        else:
            fair = p_over if side == "yes" else 1.0 - p_over
        pin_fair = fair
        w = st.weights.get(stat_key(q.kind, q.stat), 0.0)
        mdl = _model_side(st, q, side)
        if w > 0.0 and mdl is not None:
            fair = blend(
                pin_fair, mdl, w
            )  # paper-only adaptation, earned weight (configs/jarvis.yaml)
        edge = E.edge_cents(cfg, q.venue, fair, ask, q.series)
        ref_age = age_min(ref.ts, st.now)
        skew = (ref.ts - q.ts).total_seconds() / 60.0
        reason = (
            f"ask {_c(ask)} ({q_age:.0f} min old) + fee {_c(fee)} vs Pinnacle no-vig {_c(fair)} "
            f"({ref.line}, {ref_age:.0f} min old): {edge:+.1f}c per $1"
        )
        if ref_age > cfg.max_ref_age_min:
            capped = True
            notes.append(f"Pinnacle line is {ref_age:.0f} min old; label capped at thin")
        if skew > cfg.max_ref_lead_min:
            capped = True
            notes.append(f"Pinnacle is {skew:.0f} min newer than this price; capped at thin")
        if q_age > cfg.max_alert_quote_age_min:
            capped = True
            notes.append(
                f"price is {q_age:.0f} min old (snapshot); a strong label needs a fresh one"
            )
    else:
        reason = (
            f"ask {_c(ask)} + fee {_c(fee)}; no Pinnacle line for this bet, so no edge is measured"
        )
    label = E.classify(cfg, edge, capped=capped)
    friction = _friction(q, side, ask, fee)
    mp = st.model.p_yes(q) if st.model is not None else None
    if mp is not None and side == "no":
        mp = 1.0 - mp
    leg = E.leg_from_quote(q, side, ask)
    mx = E.max_price(cfg, q.venue, fair, ask, label, q.series)
    return Play(
        game_id=q.game_id,
        matchup=g.matchup,
        tipoff=g.tipoff,
        legs=(leg,),
        label=label,
        edge_cents=edge if edge is not None else float("nan"),
        fair_prob=fair,
        fee_cents=fee * 100.0,
        max_price=mx,
        contracts=0,
        stake_usd=0.0,
        reason=reason,
        model_prob=mp,
        ref_age_min=ref_age,
        notes=tuple(notes),
        pin_prob=pin_fair,
        w_model=w,
        inputs={
            "friction": friction,
            "quote": [q.venue, q.ticker, q.yes_bid, q.yes_ask, str(q.ts)],
            "side": side,
            "ref": None if ref is None else [round(ref.p_yes, 5), str(ref.ts), ref.line],
        },
    )


def _model_side(st: SlateState, q: Quote, side: str) -> float | None:
    if st.model is None:
        return None
    m = st.model.p_yes(q)
    return None if m is None else (m if side == "yes" else 1.0 - m)


def _p_play(st: SlateState, q: Quote) -> float | None:
    fn = getattr(st.model, "p_play", None)
    return None if fn is None else fn(q)


def _friction(q: Quote, side: str, ask: float, fee: float) -> float:
    """What buying costs against the venue's own mid (spread + fee), as a negative number; the
    least-bad entertainment bet when no sharp reference exists. Worst case if there is no bid."""
    if q.yes_bid is None or q.yes_ask is None:
        return -0.5
    mid = (q.yes_bid + q.yes_ask) / 2.0
    return (mid if side == "yes" else 1.0 - mid) - ask - fee


RESOLUTION_RISK = "RESOLUTION RISK: postponement/OT rules differ by venue, not verified"


def arbitrage_candidate(
    cfg: JarvisConfig, st: SlateState, game: Game, quotes: list[Quote]
) -> Play | None:
    """A cover of the winner market. It is only ``arbitrage`` when a depth-aware live source has
    confirmed both legs (read within ``confirmed_max_gap_s``) and the fees are verified; JARVIS's
    own snapshot reads never qualify, so they produce ``arb_candidate``, which never alerts."""
    best = E.best_arbitrage(cfg, [q for q in quotes if q.game_id == game.game_id])
    if best is None:
        return None
    q1, s1, q2, s2 = best.q1, best.s1, best.q2, best.s2
    a1, a2 = q1.ask(s1), q2.ask(s2)
    if a1 is None or a2 is None:
        return None
    confirmed = best.confirmed and best.fees_verified
    label = "arbitrage" if confirmed else "arb_candidate"
    gap = abs((q1.ts - q2.ts).total_seconds()) / 60.0
    oldest = max(age_min(q1.ts, st.now), age_min(q2.ts, st.now))
    notes = []
    if not confirmed:
        notes.append(
            f"unconfirmed: legs up to {max(gap, oldest):.0f} min apart or old, depth unknown"
        )
    if not best.fees_verified:
        notes.append("a venue fee is unverified in configs/jarvis.yaml")
    if q1.venue != q2.venue and not cfg.settlement_rules_verified:
        notes.append(RESOLUTION_RISK)
    notes.append("two legs are not simultaneous: place the thinner-looking leg first")
    f1 = E.fee_per_contract_raw(cfg, q1.venue, a1, q1.series)
    f2 = E.fee_per_contract_raw(cfg, q2.venue, a2, q2.series)
    legs = (E.leg_from_quote(q1, s1, a1), E.leg_from_quote(q2, s2, a2))
    mx1 = E.arb_max_price(cfg, q1.venue, q1.series, a2 + f2, a1)
    mx2 = E.arb_max_price(cfg, q2.venue, q2.series, a1 + f1, a2)
    return Play(
        game_id=game.game_id,
        matchup=game.matchup,
        tipoff=game.tipoff,
        legs=legs,
        label=label,
        edge_cents=best.profit_cents,
        fair_prob=None,
        fee_cents=(f1 + f2) * 100.0,
        max_price=mx1,
        contracts=0,
        stake_usd=0.0,
        reason=(
            f"{legs[0].venue} {legs[0].desc} {_c(a1)} + {legs[1].venue} {legs[1].desc} {_c(a2)}, "
            f"fees {_c(f1)} + {_c(f2)}: cost {_c(a1 + a2 + f1 + f2)} for $1, "
            f"{best.profit_cents:+.1f}c per pair"
        ),
        notes=tuple(notes),
        inputs={
            "legs": [
                [q1.venue, q1.ticker, s1, a1, str(q1.ts)],
                [q2.venue, q2.ticker, s2, a2, str(q2.ts)],
            ],
            "max_prices": [mx1, mx2],
        },
    )


def rank_key(p: Play) -> tuple[int, float, float]:
    """Higher is better: arbitrage > measured edge (any label) > unmeasured by least friction."""
    measured = p.edge_cents == p.edge_cents  # not NaN
    if p.label == "arbitrage":
        return (3, p.edge_cents, 0.0)
    if measured:
        return (2, p.edge_cents, 0.0)
    kind_pref = 1.0 if p.legs[0].kind == "game_win" else 0.0
    return (1, kind_pref, float(p.inputs.get("friction", -1.0)))  # type: ignore[arg-type]


def best_per_game(cfg: JarvisConfig, st: SlateState) -> dict[str, Play]:
    """The single best unsized play of every game that has at least one tradable quote. A measured
    label is the best of N priced bets, and says so (selection over many near-fair prices)."""
    trad = st.tradable(cfg)
    by_game: dict[str, list[Quote]] = {}
    for q in trad:
        by_game.setdefault(q.game_id, []).append(q)
    best: dict[str, Play] = {}
    for g in st.games:
        qs = by_game.get(g.game_id, [])
        cands: list[Play] = []
        for q in qs:
            for side in ("yes", "no"):
                if q.venue == "polymarket" and side == "no":
                    continue  # the other team's token is the same bet
                p = single_candidate(cfg, st, q, side)
                if p is not None:
                    cands.append(p)
        n_measured = sum(1 for c in cands if c.edge_cents == c.edge_cents)
        arb = arbitrage_candidate(cfg, st, g, qs)
        if arb is not None:
            cands.append(arb)
        if cands:
            top = max(cands, key=rank_key)
            if top.legs and len(top.legs) == 1 and top.edge_cents == top.edge_cents:
                top = replace(
                    top, notes=(*top.notes, f"best of {n_measured} priced bets in this game")
                )
            best[g.game_id] = top
    return best


# ------------------------------------------------------------------------------ sizing

REAL_LABELS = ("strong", "arbitrage")  # these (only) may use the cap reserved for real edges


def size_plays(
    cfg: JarvisConfig,
    plays: list[Play],
    book: PaperBook | None,
    slate: date,
    *,
    none_level: bool = False,
) -> list[Play]:
    """Size best-edge-first from the label ladder against the nightly cap, of which
    ``reserve_for_strong`` is kept for strong/arbitrage plays only. ``none_level`` is the kill
    switch (every stake at the entertainment size). A play already in the book today keeps its
    recorded size (re-emission is not new money)."""
    real, other = book.spent_split(slate) if book is not None else (0.0, 0.0)
    out: list[Play] = []
    for p in sorted(plays, key=rank_key, reverse=True):
        prior = book.first_stake(p.key, slate) if book is not None else None
        if prior is not None:
            out.append(_with_size(cfg, p, prior[0], note="size as first recorded tonight"))
            continue
        is_real = p.label in REAL_LABELS
        total_room = max(0.0, cfg.nightly_cap - real - other)
        pool_room = (
            total_room
            if is_real
            else max(0.0, cfg.nightly_cap - cfg.reserve_for_strong - other - 0.0)
        )
        remaining = total_room if is_real else min(total_room, pool_room)
        n = _contracts(cfg, p, remaining, none_level)
        sized = _with_size(cfg, p, n)
        if is_real:
            real += sized.stake_usd
        else:
            other += sized.stake_usd
        if n == 0:
            why = (
                "nightly cap used"
                if remaining <= 0
                else "the order does not fit the room left tonight"
            )
            sized = _with_size(cfg, p, 0, note=f"stake $0: {why}", zero_reason=why)
        out.append(sized)
    return out


def _contracts(cfg: JarvisConfig, p: Play, remaining: float, none_level: bool) -> int:
    budget = E.stake_for(cfg, p.label, none_level=none_level)
    budget = min(budget, cfg.per_label_cap[p.label], cfg.per_bet_cap)
    entertainment = none_level or p.label == "none"
    if len(p.legs) == 1:
        leg = p.legs[0]
        return E.contracts_for(
            cfg, leg.venue, leg.price, budget, remaining, leg.series,
            cfg.none_max_contracts if entertainment else None,
        )  # fmt: skip
    cap = min(budget, remaining)
    n = 0
    while n < 500:
        m = n + 1
        costs = [E.order_cost(cfg, leg.venue, leg.price, m, leg.series) for leg in p.legs]
        if sum(costs) > cap + 1e-9:
            break
        if any(c > cfg.bankroll[leg.venue] for c, leg in zip(costs, p.legs, strict=True)):
            break
        n = m
        if entertainment and n >= cfg.none_max_contracts:
            break
    return n


def _with_size(cfg: JarvisConfig, p: Play, n: int, note: str = "", zero_reason: str = "") -> Play:
    costs = [E.order_cost(cfg, leg.venue, leg.price, n, leg.series) for leg in p.legs]
    inputs = dict(p.inputs)
    inputs["leg_costs"] = [round(c, 4) for c in costs]
    if zero_reason:
        inputs["zero_reason"] = zero_reason
    notes = (*p.notes, note) if note else p.notes
    return replace(p, contracts=n, stake_usd=round(sum(costs), 2), notes=notes, inputs=inputs)


def plan_slate(cfg: JarvisConfig, st: SlateState, book: PaperBook | None = None) -> list[Play]:
    """Best play per game, sized, ordered best edge first. The kill switch (mean CLV of the last
    ``kill_switch.min_bets`` settled bets < 0) drops every stake to the entertainment size."""
    kill = kill_switch(cfg, book) if book is not None else KillState(False, 0, None)
    plays = size_plays(
        cfg, list(best_per_game(cfg, st).values()), book, st.slate, none_level=kill.active
    )
    if kill.active:
        plays = [replace(p, notes=(*p.notes, kill.message())) for p in plays]
    return plays


def best_overall(plays: list[Play]) -> Play | None:
    return max(plays, key=rank_key) if plays else None


def slate_input_hash(st: SlateState, plays: list[Play]) -> str:
    return sha([p.inputs for p in plays] + [str(st.slate)])


__all__ = [
    "QUOTE_SOURCES",
    "SlateState",
    "Ref",
    "PlayLeg",
    "best_overall",
    "best_per_game",
    "gather",
    "plan_slate",
    "single_candidate",
    "size_plays",
]
