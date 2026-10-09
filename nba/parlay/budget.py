"""Budget and target-payout questions: "I have $B, what is the best EV?" / "...win at least $T?".

Pure order math on top of the priced candidates from ``analysis`` (no new probability or fee
model): whole Kalshi contracts, the verified fee schedule with its per-ORDER round-up, EV per
order (not per contract), ranking by EV at the conservative low bound, informational
fractional-Kelly sizing capped by config, and never more than the budget.

Honesty rules: while the market-as-prior gate is closed the model weight is 0, the shrunk
probability collapses onto the market and the answer is "keep your money". The unshrunk model
view is always labelled a hypothesis. Analysis only; no order is ever placed.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from nba.parlay.analysis import (
    DISCLAIMER,
    Analysis,
    Candidate,
    build_analysis,
    cand_dict,
    enumerate_combos,
    pool_legs,
    price_combos,
)
from nba.parlay.config import ParlayConfig
from nba.parlay.ev import NO_POSITIVE_EV, POSITIVE_EV, order_fee

KEEP_YOUR_MONEY = "keep your money: no positive-EV bet at the conservative bound"
HYPOTHESIS = "hypothesis, not a recommendation"
DEFAULT_TARGETS = (5.0, 10.0, 25.0, 50.0, 100.0)
_EPS = 1e-9


@dataclass(frozen=True)
class OrderPlan:
    """One order of ``n`` whole contracts of one candidate, all money in dollars."""

    n: int
    cost: float  # n * ask + order fee (what leaves the account)
    fee: float  # per-ORDER fee, rounded up once
    payout: float  # gross $n if every leg hits
    profit_if_hit: float
    ev: float
    ev_low: float
    raw_ev: float
    raw_ev_low: float


def order_cost(c: Candidate, n: int, cfg: ParlayConfig) -> tuple[float, float]:
    """(total cost, fee) of ``n`` contracts at the ask: the fee is rounded up once per order."""
    fee = order_fee(c.ask, cfg.fee, n, order_type=cfg.order_type, series=c.series)
    return round(n * c.ask + fee, 2), fee


def max_affordable(c: Candidate, budget: float, cfg: ParlayConfig) -> int:
    """Largest whole number of contracts whose total cost (fees included) fits ``budget``."""
    n = int(math.floor(budget / c.ask + _EPS))
    while n > 0 and order_cost(c, n, cfg)[0] > budget + _EPS:
        n -= 1
    return max(n, 0)


def plan(c: Candidate, n: int, cfg: ParlayConfig) -> OrderPlan:
    """EV of an ``n``-contract order: n * p - cost, with ONE round-up fee for the whole order."""
    cost, fee = order_cost(c, n, cfg)
    r = c.rec
    lo = r.prob_interval[0]
    return OrderPlan(
        n=n,
        cost=cost,
        fee=fee,
        payout=float(n),
        profit_if_hit=float(n) - cost,
        ev=n * r.model_prob - cost,
        ev_low=n * lo - cost,
        raw_ev=n * r.raw_model_prob - cost,
        raw_ev_low=n * c.raw_lo - cost,
    )


def kelly_contracts(c: Candidate, budget: float, cfg: ParlayConfig) -> int:
    """Informational capped fractional-Kelly size in whole contracts (0 when no edge/too small)."""
    stake = c.rec.kelly * budget
    return int(math.floor(stake / (c.ask + c.rec.fees) + _EPS)) if c.rec.kelly > 0 else 0


def _opt(c: Candidate, p: OrderPlan | None, *, mode: str) -> dict[str, Any]:
    d = cand_dict(c)
    d.pop("legs")
    d |= {"legs_text": c.text, "n_legs": len(c.legs), "n_games": len(c.games)}
    if p is not None:
        d["order"] = {
            "contracts": p.n,
            "cost": p.cost,
            "fee": p.fee,
            "payout": p.payout,
            "profit_if_hit": p.profit_if_hit,
            "ev": p.ev,
            "ev_low": p.ev_low,
            "raw_ev": p.raw_ev,
            "raw_ev_low": p.raw_ev_low,
            "ev_low_per_dollar": per_dollar(p),
        }
    d["mode"] = mode
    if p is not None:
        d["order_verdict"] = POSITIVE_EV if p.ev_low > 0.0 else NO_POSITIVE_EV
    return d


def _header(an: Analysis) -> dict[str, Any]:
    return {
        "date": an.slate.isoformat(),
        "now": an.now.isoformat(),
        "gate": an.gate_dict(),
        "model_weight_zero": not an.gate_dict()["gate_open"],
        "fee_model": an.cfg.fee.to_json_dict(),
        "interval_level": 1.0 - an.cfg.alpha,
        "kelly_fraction": an.cfg.kelly_fraction,
        "kelly_cap": an.cfg.kelly_cap,
        "n_singles": len(an.singles),
        "disclaimer": DISCLAIMER,
        "orders_placed": 0,
    }


# --------------------------------------------------------------------------- budget mode
def per_dollar(p: OrderPlan) -> float:
    """EV at the low bound per dollar staked (cost includes the order fee)."""
    return p.ev_low / p.cost if p.cost > 0 else 0.0


def rank_for_budget(
    cands: list[Candidate], budget: float, cfg: ParlayConfig
) -> list[tuple[Candidate, OrderPlan]]:
    """Each affordable candidate at its ranking size (Kelly size, at least 1 contract), best
    EV at the low bound PER DOLLAR staked first (dollar EV is shown, not ranked on)."""
    rows: list[tuple[Candidate, OrderPlan]] = []
    for c in cands:
        n_aff = max_affordable(c, budget, cfg)
        if n_aff < 1:
            continue
        n = min(max(kelly_contracts(c, budget, cfg), 1), n_aff)
        rows.append((c, plan(c, n, cfg)))
    rows.sort(key=lambda t: (-per_dollar(t[1]), -t[1].ev_low, t[0].key))
    return rows


def best_for_budget_from(
    an: Analysis,
    budget_usd: float,
    *,
    top_n: int = 5,
    pool_k: int = 12,
    max_legs: int = 3,
    max_combos: int = 300,
) -> dict[str, Any]:
    """Best use of ``budget_usd`` on an already-built analysis (singles + engine-priced parlays)."""
    if not budget_usd > 0:
        raise ValueError("budget_usd must be positive")
    cfg = an.cfg
    if not an.combos:
        pool = pool_legs(an.singles, pool_k)
        an.combos = price_combos(an, enumerate_combos(pool, max_legs, max_combos, "any"))
    cands = [*an.singles, *an.combos]
    ranked = rank_for_budget(cands, budget_usd, cfg)
    flagged = [(c, p) for c, p in ranked if c.tradable and p.ev_low > 0.0 and p.cost <= budget_usd]
    out = _header(an) | {
        "mode": "budget",
        "budget_usd": budget_usd,
        "n_options_considered": len(cands),
        "n_parlays_considered": len(an.combos),
        "ranked_by": "order EV at the conservative low bound per $ staked (after fees)",
        "ranked": [_opt(c, p, mode="budget") for c, p in ranked[:top_n]],
    }
    if flagged:
        c, p = flagged[0]
        out |= {
            "verdict": POSITIVE_EV,
            "answer": (
                f"best order at the conservative bound: {p.n} contract(s) of {c.text} "
                f"(informational, noisy edge)"
            ),
            "recommendation": _opt(c, p, mode="budget"),
        }
    else:
        out |= {"verdict": NO_POSITIVE_EV, "answer": KEEP_YOUR_MONEY, "recommendation": None}
    raw = sorted(ranked, key=lambda t: (-t[1].raw_ev_low / t[1].cost, t[0].key))[:3]
    out["raw_hypothesis"] = {
        "label": HYPOTHESIS,
        "note": "unshrunk model probability; the model has no proven edge over the market yet",
        "options": [_opt(c, p, mode="budget") for c, p in raw],
    }
    return out


# --------------------------------------------------------------------------- target mode
def target_plan(c: Candidate, budget: float, target: float, cfg: ParlayConfig) -> OrderPlan | None:
    """Smallest whole-contract order whose profit-if-hit after fees reaches ``target`` and whose
    total cost fits ``budget``; None if the target is unreachable within the budget."""
    n_aff = max_affordable(c, budget, cfg)
    for n in range(1, n_aff + 1):
        p = plan(c, n, cfg)
        if p.profit_if_hit >= target - _EPS:
            return p
    return None


def _p_key(t: tuple[Candidate, OrderPlan]) -> tuple[float, float, str]:
    """Most likely first (shrunk P(hit)); ties broken by EV at the low bound per dollar."""
    return (-t[0].rec.model_prob, -per_dollar(t[1]), t[0].key)


def _frontier_entry(
    c: Candidate, p: OrderPlan, *, n_reach: int, target: float, other: tuple[Candidate, OrderPlan]
) -> dict[str, Any]:
    lo, hi = c.rec.prob_interval
    oc, op = other
    return {
        "target_profit": target,
        "reachable": True,
        "n_reaching": n_reach,
        "option": c.text,
        "kind": c.kind,
        "tradable": c.tradable,
        "p_hit": c.rec.model_prob,
        "p_hit_interval": [lo, hi],
        "raw_p_hit": c.rec.raw_model_prob,
        "contracts": p.n,
        "cost": p.cost,
        "profit_if_hit": p.profit_if_hit,
        "ev": p.ev,
        "ev_low": p.ev_low,
        "ev_low_per_dollar": per_dollar(p),
        "raw_ev_low": p.raw_ev_low,
        "best_ev_per_dollar": {
            "option": oc.text,
            "tradable": oc.tradable,
            "p_hit": oc.rec.model_prob,
            "contracts": op.n,
            "cost": op.cost,
            "ev": op.ev,
            "ev_low": op.ev_low,
            "ev_low_per_dollar": per_dollar(op),
        },
    }


def frontier_from(
    cands: list[Candidate], budget: float, targets: tuple[float, ...], cfg: ParlayConfig
) -> list[dict[str, Any]]:
    """Per target level: the MOST LIKELY way to make it (highest P(hit) whose profit-if-hit after
    fees reaches the target within the budget), with its EV, EV_low and EV_low per $; the best
    EV-low-per-$ option is shown alongside as a secondary. Feasible sets are nested (anything
    reaching a bigger target reaches a smaller one), so P(hit) is non-increasing in the target."""
    out: list[dict[str, Any]] = []
    for t in sorted(set(targets)):
        reach: list[tuple[Candidate, OrderPlan]] = []
        for c in cands:
            p = target_plan(c, budget, t, cfg)
            if p is not None:
                reach.append((c, p))
        if not reach:
            out.append({"target_profit": t, "reachable": False, "n_reaching": 0})
            continue
        top = min(reach, key=_p_key)
        eff = min(reach, key=lambda x: (-per_dollar(x[1]), -x[0].rec.model_prob, x[0].key))
        out.append(_frontier_entry(top[0], top[1], n_reach=len(reach), target=t, other=eff))
    ok = [f for f in out if f["reachable"]]
    for a, b in itertools.pairwise(ok):
        b["p_hit_not_above_previous"] = b["p_hit"] <= a["p_hit"] + _EPS
    return out


def best_for_target_from(
    an: Analysis,
    budget_usd: float,
    target_profit_usd: float,
    *,
    top_n: int = 5,
    targets: tuple[float, ...] = DEFAULT_TARGETS,
    pool_k: int = 12,
    max_legs: int = 4,
    max_combos: int = 400,
) -> dict[str, Any]:
    """Options reaching +``target_profit_usd`` after fees within ``budget_usd``. Primary answer =
    highest P(hit) ("most likely way to make it"); best EV-low per $ is the secondary column.
    The frontier repeats this for several target levels."""
    if not budget_usd > 0 or not target_profit_usd > 0:
        raise ValueError("budget_usd and target_profit_usd must be positive")
    if max_legs > 4:
        raise ValueError("parlay legs are capped at 4")
    cfg = an.cfg
    pool = pool_legs(an.singles, pool_k)
    an.combos = price_combos(an, enumerate_combos(pool, max_legs, max_combos, "any"))
    cands = [*an.singles, *an.combos]
    reach: list[tuple[Candidate, OrderPlan]] = []
    for c in cands:
        p = target_plan(c, budget_usd, target_profit_usd, cfg)
        if p is not None:
            reach.append((c, p))
    reach.sort(key=_p_key)
    flagged = [(c, p) for c, p in reach if c.tradable and p.ev_low > 0.0]
    by_eff = sorted(reach, key=lambda t: (-per_dollar(t[1]), t[0].key))
    out = _header(an) | {
        "mode": "target",
        "budget_usd": budget_usd,
        "target_profit_usd": target_profit_usd,
        "n_options_considered": len(cands),
        "n_parlays_considered": len(an.combos),
        "n_reaching_target": len(reach),
        "max_parlay_legs": max_legs,
        "ranked_by": "highest P(hit) among options reaching the target",
        "ranked": [_opt(c, p, mode="target") for c, p in reach[:top_n]],
        "best_ev_per_dollar": [_opt(c, p, mode="target") for c, p in by_eff[:3]],
        "frontier": frontier_from(cands, budget_usd, tuple({*targets, target_profit_usd}), cfg),
        "parlay_price_note": "parlays are priced at the product of leg asks; no combo contract "
        "is ingested, so that price is NOT tradable and a real combo quote will differ",
    }
    if flagged:
        c, p = flagged[0]
        out |= {
            "verdict": POSITIVE_EV,
            "answer": f"{p.n} contract(s) of {c.text} reach the target with positive EV at the "
            "conservative bound (informational, noisy edge)",
            "recommendation": _opt(c, p, mode="target"),
        }
    elif not reach:
        out |= {
            "verdict": NO_POSITIVE_EV,
            "answer": f"no option on this slate reaches +${target_profit_usd:g} within "
            f"${budget_usd:g}; keep your money",
            "recommendation": None,
        }
    else:
        out |= {
            "verdict": NO_POSITIVE_EV,
            "answer": f"no positive-EV option reaches +${target_profit_usd:g} at the "
            "conservative bound; keep your money",
            "recommendation": None,
        }
    raw = sorted(reach, key=lambda t: (-t[1].raw_ev_low / t[1].cost, t[0].key))[:3]
    out["raw_hypothesis"] = {
        "label": HYPOTHESIS,
        "note": "unshrunk model probability; the model has no proven edge over the market yet",
        "options": [_opt(c, p, mode="target") for c, p in raw],
    }
    return out


# --------------------------------------------------------------------------- public entry points
def best_for_budget(
    budget_usd: float,
    slate: date,
    now: datetime | None = None,
    *,
    analysis: Analysis | None = None,
    **paths: Any,
) -> dict[str, Any]:
    """``best_for_budget(20, date, now)``: engine answer for "I have $20, best EV?"."""
    an = analysis or build_analysis(slate, now, **paths)
    return best_for_budget_from(an, budget_usd)


def best_for_target(
    budget_usd: float,
    target_profit_usd: float,
    slate: date,
    now: datetime | None = None,
    *,
    analysis: Analysis | None = None,
    **paths: Any,
) -> dict[str, Any]:
    """``best_for_target(20, 100, date, now)``: options that can win at least the target."""
    an = analysis or build_analysis(slate, now, **paths)
    return best_for_target_from(an, budget_usd, target_profit_usd)
