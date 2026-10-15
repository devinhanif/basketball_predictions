"""Top-3 two- and three-leg tickets by calibrated joint probability (contract top_parlays/1).

Read-only and paper only: nothing here prices an order, sizes a stake or talks to a market. The
module is pure (no database, no network); ``top_source`` (live) and ``top_history`` (hindsight
fixture) build the ``LegCand`` list and call ``build_report``.

Method, in the order it runs:

1. Refuse legs priced at or after tip, and legs whose game has tipped by the run clock.
2. One leg per (book, player, stat): the side with the higher calibrated probability.
3. Prune each book's legs to the top ``K`` by calibrated probability (K is stated in the output).
   A ticket never mixes books: a ticket is one book's parlay.
4. Enumerate every 2- and 3-leg ticket of the pruned set. Same-game legs share one Gaussian copula
   draw per (book, game) with the configured pair correlations; different games multiply
   (independent, as the cross-game check in reports/parlay/independence_check.* supports).
   Legs carry ``p_cal`` (the frozen LINE_CALIBRATION map) as their marginals; ``p_joint_raw`` uses
   the raw probabilities on the same draws.
5. Rank by ``p_joint_cal`` descending, ties by higher EV, then by a fixed key (deterministic).
6. Price: the book's stacked price is the product of the legs' implied probabilities; the fee is the
   verified fee object (one contract, ceil to the cent); EV per dollar = p / (price + fee) - 1; the
   80% interval is the configured logit-scale calibration noise on the joint probability.

The verdict is ``keep_your_money`` unless the frozen gate is open AND some listed ticket has
``ev_low80 > 0``.
"""

from __future__ import annotations

import itertools
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from nba.parlay.assistant.numguard import check_prose, collect_numbers
from nba.parlay.config import ParlayConfig
from nba.parlay.copula import PairCorr, hits_from_uniforms, sample_latent_uniforms
from nba.parlay.ev import calibration_sd, fee_per_contract, prob_interval
from nba.parlay.joint import GameCtx, entity_corr

SCHEMA = "top_parlays/2"
POSITIVE = "positive_ev_found"
KEEP = "keep_your_money"
K_DEFAULT = 40  # legs kept per book before the ticket search (C(40,3) = 9,880 triples per book)
SIZES = (2, 3)
TOP_N = 3
EPS = 1e-6
STAT_LABEL = {"pts": "points", "reb": "rebounds", "ast": "assists", "fg3m": "threes"}
T490_CAVEAT = (
    "Two-leg parlays built from legs the raw model put at 70% or more hit 9.8 points less often "
    "than the model said (95% CI 6.8 to 12.9 points, n 3,337 parlays, date-clustered, 2023-24 and "
    "2024-25; T490). Three-leg: 8.7 points (5.5 to 11.9, n 3,360)."
)
JOINT_STATUS = "legs calibrated; joint not yet tested"


@dataclass(frozen=True)
class LegCand:
    """One priced leg. ``p_raw`` / ``p_cal`` are the probabilities that THIS side (over or under)
    hits; ``p_over_*`` are the model's P(stat > line) before the side is applied."""

    game_id: str
    tipoff: datetime  # naive UTC
    player_id: int
    player_name: str
    team_id: int
    opp_id: int
    team: str
    opp: str
    stat: str
    side: str  # over | under
    line: float
    book: str
    price_american: int
    price_at: datetime  # naive UTC, when the book's price was captured
    p_raw: float
    p_cal: float
    model_source: str
    p_avg_raw: float | None = None  # AVG P(this side), display only, never ranks
    drivers: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.game_id}|{self.player_id}|{self.stat}|{self.side}|{self.line}|{self.book}"


def american_to_decimal(price: int) -> float:
    if price == 0:
        raise ValueError("american price 0")
    return 1.0 + (price / 100.0 if price > 0 else 100.0 / -price)


def implied_prob(price: int) -> float:
    """Raw implied probability of an American price (the book's margin is still in it)."""
    return 1.0 / american_to_decimal(price)


def make_leg(
    *,
    p_over_raw: float,
    p_over_cal: float,
    side: str,
    p_avg_over: float | None = None,
    **kw: Any,
) -> LegCand:
    """Apply the side to the model's P(over): an under leg hits with probability 1 - P(over)."""
    if side not in ("over", "under"):
        raise ValueError(f"side must be over|under, got {side!r}")
    po, pc = float(p_over_raw), float(p_over_cal)
    if side == "under":
        po, pc = 1.0 - po, 1.0 - pc
    pa = None if p_avg_over is None else (float(p_avg_over) if side == "over" else 1.0 - p_avg_over)
    return LegCand(side=side, p_raw=po, p_cal=pc, p_avg_raw=pa, **kw)


def refuse_post_tip(
    legs: Sequence[LegCand], now: datetime | None
) -> tuple[list[LegCand], dict[str, int]]:
    """Drop legs priced at or after their game's tip, and (when ``now`` is given) legs whose game
    has already tipped at the run clock. Never silent: the counts go in the output."""
    kept: list[LegCand] = []
    n_priced_late = n_started = 0
    for leg in legs:
        if leg.price_at >= leg.tipoff:
            n_priced_late += 1
        elif now is not None and now >= leg.tipoff:
            n_started += 1
        else:
            kept.append(leg)
    return kept, {"priced_at_or_after_tip": n_priced_late, "game_already_tipped": n_started}


def dedupe(legs: Sequence[LegCand]) -> list[LegCand]:
    """One leg per (book, player, stat): the higher calibrated probability; ties by the better
    (longer) price, then the key."""
    best: dict[tuple[str, int, str], LegCand] = {}
    for leg in legs:
        k = (leg.book, leg.player_id, leg.stat)
        cur = best.get(k)
        if cur is None or _rank_key(leg) < _rank_key(cur):
            best[k] = leg
    return sorted(best.values(), key=_rank_key)


def _rank_key(leg: LegCand) -> tuple[float, float, str]:
    return (-round(leg.p_cal, 12), -american_to_decimal(leg.price_american), leg.key)


def prune(legs: Sequence[LegCand], k: int) -> dict[str, list[LegCand]]:
    """Top ``k`` legs per book by calibrated probability."""
    by_book: dict[str, list[LegCand]] = {}
    for leg in sorted(legs, key=_rank_key):
        by_book.setdefault(leg.book, [])
        if len(by_book[leg.book]) < k:
            by_book[leg.book].append(leg)
    return by_book


@dataclass(frozen=True)
class Ticket:
    legs: tuple[LegCand, ...]
    p_joint_cal: float
    p_joint_raw: float
    book_implied: float
    fee: float
    ev_per_dollar: float

    @property
    def key(self) -> str:
        return "||".join(sorted(leg.key for leg in self.legs))

    @property
    def same_game(self) -> bool:
        return len({leg.game_id for leg in self.legs}) == 1


class _Sims:
    """Hit vectors for the pruned legs of one book, one copula draw per game (seeded)."""

    def __init__(self, legs: Sequence[LegCand], pc: PairCorr, n_sims: int, seed: int) -> None:
        self.cal: dict[str, np.ndarray] = {}
        self.raw: dict[str, np.ndarray] = {}
        by_game: dict[tuple[str, str], list[LegCand]] = {}
        for leg in legs:
            by_game.setdefault((leg.book, leg.game_id), []).append(leg)
        for (book, gid), glegs in sorted(by_game.items()):
            if len(glegs) < 2:
                continue
            team_of = {leg.player_id: leg.team_id for leg in glegs}
            ctx = GameCtx(gid, glegs[0].team_id, glegs[0].opp_id, 0.0, 1.0, 0.0, 1.0, {}, team_of)
            ents = [("player", gid, leg.player_id, leg.stat) for leg in glegs]
            R = entity_corr(ents, {gid: ctx}, pc)
            gseed = zlib.crc32(f"{book}|{gid}".encode()) % (2**31)
            u = sample_latent_uniforms(R, n_sims, seed + gseed, "gaussian")
            sides = ["yes" if leg.side == "over" else "no" for leg in glegs]
            p_cal_over = [leg.p_cal if leg.side == "over" else 1.0 - leg.p_cal for leg in glegs]
            p_raw_over = [leg.p_raw if leg.side == "over" else 1.0 - leg.p_raw for leg in glegs]
            h_cal = hits_from_uniforms(u, p_cal_over, sides)
            h_raw = hits_from_uniforms(u, p_raw_over, sides)
            for i, leg in enumerate(glegs):
                self.cal[leg.key], self.raw[leg.key] = h_cal[:, i], h_raw[:, i]
        self.n = n_sims
        self._memo: dict[tuple[str, ...], tuple[float, float]] = {}

    def group(self, legs: tuple[LegCand, ...]) -> tuple[float, float]:
        """(cal, raw) joint probability of legs in ONE game."""
        if len(legs) == 1:
            return legs[0].p_cal, legs[0].p_raw
        key = tuple(leg.key for leg in legs)
        got = self._memo.get(key)
        if got is None:
            ok_c = np.logical_and.reduce([self.cal[k] for k in key])
            ok_r = np.logical_and.reduce([self.raw[k] for k in key])
            lo = 0.5 / (self.n + 1.0)
            got = (max(float(ok_c.mean()), lo), max(float(ok_r.mean()), lo))
            self._memo[key] = got
        return got


def _price_ticket(legs: tuple[LegCand, ...], sims: _Sims, cfg: ParlayConfig) -> Ticket:
    groups: dict[str, list[LegCand]] = {}
    for leg in legs:
        groups.setdefault(leg.game_id, []).append(leg)
    p_cal = p_raw = 1.0
    for gid in sorted(groups):
        c, r = sims.group(tuple(sorted(groups[gid], key=lambda x: x.key)))
        p_cal *= c
        p_raw *= r
    implied = 1.0
    for leg in legs:
        implied *= implied_prob(leg.price_american)
    fee = fee_per_contract(min(max(implied, 0.0), 1.0), cfg.fee, order_type=cfg.order_type)
    return Ticket(legs, p_cal, p_raw, implied, fee, p_cal / (implied + fee) - 1.0)


def search(
    by_book: Mapping[str, Sequence[LegCand]], pc: PairCorr, cfg: ParlayConfig
) -> tuple[dict[int, list[Ticket]], dict[str, Any]]:
    """Top ``TOP_N`` tickets per size across books, plus search counts."""
    best: dict[int, list[Ticket]] = {n: [] for n in SIZES}
    counts = {"tickets_searched": 0, "tickets_with_positive_point_ev": 0}
    for book in sorted(by_book):
        legs = list(by_book[book])
        sims = _Sims(legs, pc, cfg.n_sims, cfg.seed)
        for n in SIZES:
            for combo in itertools.combinations(legs, n):
                t = _price_ticket(combo, sims, cfg)
                counts["tickets_searched"] += 1
                counts["tickets_with_positive_point_ev"] += int(t.ev_per_dollar > 0)
                best[n].append(t)
            best[n] = sorted(best[n], key=_ticket_order)[:TOP_N]
    return best, counts


def _ticket_order(t: Ticket) -> tuple[float, float, str]:
    return (-round(t.p_joint_cal, 12), -round(t.ev_per_dollar, 12), t.key)


@dataclass(frozen=True)
class Gate:
    """The frozen track-record gate (30 settled, 14 dates, skill > 0). ``skill`` is the model's
    skill against the market, reported only when the game-clustered lower bound clears zero
    (``TrackRecord.gated_skill``); None otherwise, which keeps the gate shut."""

    n_settled: int = 0
    n_dates: int = 0
    skill: float | None = None

    def is_open(self, cfg: ParlayConfig) -> bool:
        return (
            self.n_settled >= cfg.min_settled
            and self.n_dates >= cfg.min_settled_dates
            and self.skill is not None
            and self.skill > 0.0
        )

    def to_dict(self, cfg: ParlayConfig) -> dict[str, Any]:
        return {
            "settled": self.n_settled,
            "settled_min": cfg.min_settled,
            "slate_dates": self.n_dates,
            "dates_min": cfg.min_settled_dates,
            "skill_vs_market": self.skill,
            "open": self.is_open(cfg),
        }

    def describe(self, cfg: ParlayConfig) -> str:
        return (
            f"{self.n_settled} settled over {self.n_dates} dates "
            f"(need {cfg.min_settled} and {cfg.min_settled_dates}), skill "
            f"{'not shown (lower bound not above zero)' if self.skill is None else self.skill}"
        )


# ------------------------------------------------------------------------------------ text


def _pct(x: float) -> str:
    return f"{100.0 * x:.0f}%"


def _spct(x: float) -> str:
    return f"{100.0 * x:+.0f}%"


def leg_reason(leg: LegCand) -> str:
    """One deterministic sentence from the leg's own fields. Every number it prints is in
    ``leg_dict(leg)`` (checked by ``guard_text``)."""
    d = leg.drivers
    stat = STAT_LABEL.get(leg.stat, leg.stat)
    head = f"{leg.player_name} {leg.side} {leg.line:.1f} {stat} ({leg.team} vs {leg.opp})"
    bits: list[str] = []
    if d.get("mean") is not None:
        bits.append(f"the model's mean is {d['mean']:.1f}")
    if d.get("recent_avg") is not None:
        rel = "above" if d["recent_avg"] > leg.line else "below"
        bits.append(f"his recent average is {d['recent_avg']:.1f}, {rel} the line")
    if d.get("minutes") is not None:
        kind = "projected" if d.get("minutes_kind") == "projected" else "recent average"
        bits.append(f"{kind} minutes are {d['minutes']:.1f}")
    names = d.get("teammates_out_names") or []
    if names:
        bits.append(f"{' and '.join(names[:2])} {'is' if len(names[:2]) == 1 else 'are'} out")
    elif d.get("n_out") is not None and d["n_out"] > 0:
        bits.append(f"{int(d['n_out'])} rotation teammate(s) out")
    lead = "; ".join(bits)
    tail = (
        f"calibrated chance {_pct(leg.p_cal)} (raw {_pct(leg.p_raw)}) against "
        f"{_pct(implied_prob(leg.price_american))} implied by {leg.book} at {leg.price_american:+d}"
    )
    return f"{head}: {lead + '; ' if lead else ''}{tail}."


def guard_text(text: str, obj: Any, extra: Sequence[float] = ()) -> tuple[str, int]:
    """Run ``text`` through numguard against every number in ``obj``; return (text, n_removed).
    On any removal the text is replaced by a number-free sentence rather than shipped with a
    ``[number removed]`` marker."""
    out, removed = check_prose(text, [*collect_numbers(obj), *extra])
    if removed:
        return "See this leg's own fields for the numbers.", removed
    return out, 0


def leg_dict(leg: LegCand) -> dict[str, Any]:
    return {
        "game_id": leg.game_id,
        "tipoff": leg.tipoff.isoformat() + "Z",
        "player_id": leg.player_id,
        "player_name": leg.player_name,
        "team": leg.team,
        "opp": leg.opp,
        "stat": leg.stat,
        "side": leg.side,
        "line": leg.line,
        "book": leg.book,
        "price_american": leg.price_american,
        "price_at": leg.price_at.isoformat() + "Z",
        "p_raw": round(leg.p_raw, 4),
        "p_cal": round(leg.p_cal, 4),
        "model_source": leg.model_source,
        "p_avg_raw": None if leg.p_avg_raw is None else round(leg.p_avg_raw, 4),
        "drivers": {**leg.drivers, "implied": round(implied_prob(leg.price_american), 4)},
    }


def ticket_why(t: Ticket, tj: Mapping[str, Any], n_games: int) -> str:
    """Two or three plain sentences from the ticket's own numbers."""
    n = len(t.legs)
    where = "all in one game" if n_games == 1 else f"across {n_games} games"
    s1 = (
        f"{n} legs {where}: each leg is calibrated but the joint is not yet tested, so "
        f"{_pct(tj['p_joint_cal'])} (raw {_pct(tj['p_joint_raw'])}) is the joint of those legs."
    )
    s2 = (
        f"{t.legs[0].book} stacks them at an implied {_pct(tj['book_implied'])}; after the fee "
        f"schedule that is {_spct(tj['ev_per_dollar'])} per dollar, and the low end of the 80% "
        f"interval is {_spct(tj['ev_low80'])}."
    )
    s3 = (
        "Legs from different games are treated as independent; legs in one game share the copula."
        if n_games > 1 and n_games < n
        else (
            "Legs from different games are treated as independent."
            if n_games > 1
            else "Legs in one game share the copula."
        )
    )
    return f"{s1} {s2} {s3}"


# ---------------------------------------------------------------------------------- report


def ticket_dict(
    rank: int, t: Ticket, cfg: ParlayConfig, counters: dict[str, int]
) -> dict[str, Any]:
    p = min(max(t.p_joint_cal, EPS), 1.0 - EPS)
    seed = cfg.seed + zlib.crc32(t.key.encode()) % (2**31)
    lo, hi = prob_interval(
        int(round(p * 1000)), 1000, cfg.alpha, calibration_sd(cfg, p), cfg.n_draws, seed
    )
    cost = t.book_implied + t.fee
    legs = []
    for leg in t.legs:
        lj = leg_dict(leg)
        lj["reason"], rem = guard_text(leg_reason(leg), lj, [cfg.tail_cut])
        counters["numguard_removed"] += rem
        legs.append(lj)
    tj: dict[str, Any] = {
        "rank": rank,
        "same_game": t.same_game,
        "legs": legs,
        "p_joint_raw": round(t.p_joint_raw, 4),
        "p_joint_cal": round(t.p_joint_cal, 4),
        "p_joint_cal_80": [round(lo, 4), round(hi, 4)],
        "book_implied": round(t.book_implied, 4),
        "fee": round(t.fee, 4),
        "ev_per_dollar": round(t.ev_per_dollar, 4),
        "ev_low80": round(lo / cost - 1.0, 4),
    }
    n_games = len({leg.game_id for leg in t.legs})
    tj["why"], rem = guard_text(
        ticket_why(t, tj, n_games), tj, [cfg.tail_cut, round(1.0 - cfg.alpha, 6)]
    )
    counters["numguard_removed"] += rem
    return tj


def build_report(
    legs: Sequence[LegCand],
    *,
    slate_date: str,
    made_at: datetime,
    now: datetime | None,
    cfg: ParlayConfig,
    pc: PairCorr,
    calibration: Mapping[str, Any],
    gate: Gate,
    k: int = K_DEFAULT,
    no_slate: bool = False,
    illustration: bool = False,
    pre_refused: Mapping[str, int] | None = None,
    notes: Sequence[str] = (),
    extra_caveats: Sequence[str] = (),
) -> dict[str, Any]:
    kept, refused = refuse_post_tip(legs, now)
    uniq = dedupe(kept)
    by_book = prune(uniq, k)
    best, counts = search(by_book, pc, cfg)
    counters = {"numguard_removed": 0}
    sizes = {
        str(n): [ticket_dict(i + 1, t, cfg, counters) for i, t in enumerate(best[n])] for n in SIZES
    }
    reasons: list[str] = []
    if no_slate:
        reasons.append("no slate")
    elif not legs:
        reasons.append("no priced legs")
    elif not kept:
        reasons.append("no legs priced before tip")
    else:
        for n in SIZES:
            have = len(sizes[str(n)])
            if have == 0:
                reasons.append(f"size {n}: fewer than {n} eligible legs in one book")
            elif have < TOP_N:
                reasons.append(f"size {n}: only {have} ticket(s) available")
    gate_open = gate.is_open(cfg)
    any_pos = any(t["ev_low80"] > 0 for ts in sizes.values() for t in ts)
    verdict = POSITIVE if (gate_open and any_pos and not illustration) else KEEP
    return {
        "schema": SCHEMA,
        "illustration": illustration,
        "slate_date": slate_date,
        "made_at": made_at.isoformat() + "Z",
        "calibration": {**calibration, "joint_status": JOINT_STATUS},
        "gate": gate.to_dict(cfg),
        "ranking": "calibrated joint probability, highest first; ties broken by higher EV",
        "verdict": verdict,
        "empty_reason": "; ".join(reasons) if reasons else None,
        "sizes": sizes,
        "caveats": [T490_CAVEAT, *extra_caveats],
        "diagnostics": {
            "no_positive_ev_found": not any_pos,
            "gate_describe": gate.describe(cfg),
            "search": {
                "k_per_book": k,
                "legs_in": len(legs),
                "legs_after_refusal": len(kept),
                "legs_after_dedupe": len(uniq),
                "legs_searched": {b: len(v) for b, v in sorted(by_book.items())},
                **counts,
                "n_sims_same_game": cfg.n_sims,
            },
            "refused": {**refused, **(dict(pre_refused or {}))},
            "numguard_removed": counters["numguard_removed"],
            "fee_model": cfg.fee.name,
            "fee_verified": cfg.fee.verified,
            "notes": list(notes),
        },
    }
