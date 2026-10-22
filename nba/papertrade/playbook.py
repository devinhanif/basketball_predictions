"""DESCRIPTIVE playbook arm: risky-to-safe ladders per tip window, paper only, synthetic prices.

Devin's observed night (docs/FAN_KNOWLEDGE.md, 2026-10-21): several combos across games and tip
windows, one high-probability combo with cheap long shots, cash out when the offer beats an informed
estimate, hold near-zero positions, let the winner run. This module replays that as paper rows with
``role = 'playbook'`` in its own store. It is never part of the primary summary and never a claim;
prices are products of leg asks (entry) and of leg bids minus per-leg fees (exit), both labelled
synthetic. Joint probabilities multiply shrunk leg probabilities (independence): same-game legs
co-move, so legs from at least two games are preferred and a single-game ladder is flagged.
Nothing here talks to a market except the public GETs passed in by the caller.
"""

from __future__ import annotations

import itertools
import json
import math
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from nba.papertrade import db
from nba.papertrade.config import ROOT, PaperConfig
from nba.papertrade.monitor import MarketGetter, leg_mark, open_positions
from nba.papertrade.rules import Leg, LegMark, Plan, leg_ev, plan_entry, screen_leg, shrunk
from nba.papertrade.scan import (
    BookFn,
    insert_position,
    legs_from_markets,
    refine_leg,
    refresh_open_legs,
)
from nba.parlay.ev import order_fee

PLAYBOOK_PATH = ROOT / "configs" / "papertrade_playbook.yaml"
PLAYBOOK_DB = ROOT / "data" / "papertrade" / "papertrade_playbook.duckdb"
PLAYBOOK_LEGS_FILE = ROOT / "data" / "papertrade" / "open_legs_playbook.json"
ROLE = "playbook"
LABEL = "SYNTHETIC playbook ladder: product of leg asks, not a tradable price (descriptive only)"


@dataclass(frozen=True)
class TierSpec:
    name: str
    min_legs: int
    max_legs: int
    joint_lo: float
    joint_hi: float
    stake_usd: float
    min_leg_p: float | None = None
    max_leg_ask: float | None = None
    take_profit_x: float | None = None


@dataclass(frozen=True)
class PlaybookConfig:
    rule_version: str
    scan_lead_min: float
    max_spread: float
    min_leg_ask: float
    max_leg_ask: float
    pool_size: int
    near_zero_frac: float
    let_run_mid: float
    tiers: tuple[TierSpec, ...]  # ordered safe, medium, risky

    def params(self) -> dict[str, Any]:
        return asdict(self)


def load_playbook(path: Path = PLAYBOOK_PATH) -> PlaybookConfig:
    raw = yaml.safe_load(path.read_text())
    tiers = tuple(TierSpec(name=str(k), **v) for k, v in raw["tiers"].items())
    return PlaybookConfig(
        rule_version=str(raw["rule_version"]),
        scan_lead_min=float(raw["scan_lead_min"]),
        max_spread=float(raw["max_spread"]),
        min_leg_ask=float(raw["min_leg_ask"]),
        max_leg_ask=float(raw["max_leg_ask"]),
        pool_size=int(raw["pool_size"]),
        near_zero_frac=float(raw["near_zero_frac"]),
        let_run_mid=float(raw["let_run_mid"]),
        tiers=tiers,
    )


def playbook_cfg(cfg: PaperConfig, pb: PlaybookConfig) -> PaperConfig:
    """The paper config with the playbook's rule version and leg screen (spread, price band)."""
    return replace(
        cfg,
        rule_version=pb.rule_version,
        max_spread=pb.max_spread,
        min_leg_ask=pb.min_leg_ask,
        max_leg_ask=pb.max_leg_ask,
    )


# ------------------------------------------------------------------------------ building
@dataclass(frozen=True)
class Ladder:
    tier: TierSpec
    legs: tuple[Leg, ...]
    n: int
    entry_price: float
    entry_fee: float
    cost: float
    joint: float  # product of shrunk leg probabilities
    p_model: float
    ev: float  # per $ of cost, shrunk joint vs product of asks, after the entry fee
    n_games: int


def price_ladder(tier: TierSpec, legs: tuple[Leg, ...], cfg: PaperConfig) -> Ladder:
    price = math.prod(x.ask for x in legs)
    n, fee = plan_entry(price, cfg.fee, tier.stake_usd)
    cost = n * price + fee
    joint = math.prod(shrunk(x.p_model, x.mid, cfg.shrink) for x in legs)
    return Ladder(
        tier, legs, n, price, fee, cost, joint, math.prod(x.p_model for x in legs),
        (n * joint - cost) / cost, len({x.game_id for x in legs}),
    )  # fmt: skip


def _pool(tier: TierSpec, eligible: list[Leg], cfg: PaperConfig, pb: PlaybookConfig) -> list[Leg]:
    ok = [
        x
        for x in eligible
        if (tier.max_leg_ask is None or x.ask <= tier.max_leg_ask + 1e-9)
        and (tier.min_leg_p is None or shrunk(x.p_model, x.mid, cfg.shrink) >= tier.min_leg_p)
    ]
    if tier.min_leg_p is not None:  # safe: the most probable legs first
        key: Callable[[Leg], tuple[float, str]] = lambda x: (  # noqa: E731
            -shrunk(x.p_model, x.mid, cfg.shrink),
            x.ticker,
        )
    else:
        key = lambda x: (-leg_ev(x, cfg), x.ticker)  # noqa: E731
    best: dict[tuple[int, str], Leg] = {}
    for x in sorted(ok, key=key):  # one leg per (player, stat): no duplicates in a ladder
        best.setdefault((x.player_id, x.stat), x)
    return sorted(best.values(), key=key)[: pb.pool_size]


def select_ladder(
    tier: TierSpec, eligible: list[Leg], cfg: PaperConfig, pb: PlaybookConfig,
    rejected: set[str] = frozenset(),  # type: ignore[assignment]
) -> tuple[Ladder | None, str]:  # fmt: skip
    """Best positive-EV ladder for one tier. (ladder, reason); reason is 'ok' or why it was skipped:
    no_eligible_legs | pool_too_small | no_combo_in_joint_range | no_positive_ev."""
    pool = _pool(tier, [x for x in eligible if x.ticker not in rejected], cfg, pb)
    if not pool:
        return None, "no_eligible_legs"
    if len(pool) < tier.min_legs:
        return None, "pool_too_small"
    in_range = 0
    best_multi: Ladder | None = None
    best_any: Ladder | None = None
    for k in range(tier.min_legs, min(tier.max_legs, len(pool)) + 1):
        for combo in itertools.combinations(pool, k):
            joint = math.prod(shrunk(x.p_model, x.mid, cfg.shrink) for x in combo)
            if not tier.joint_lo - 1e-12 <= joint <= tier.joint_hi + 1e-12:
                continue
            in_range += 1
            lad = price_ladder(tier, combo, cfg)
            if best_any is None or lad.ev > best_any.ev:
                best_any = lad
            if lad.n_games >= 2 and (best_multi is None or lad.ev > best_multi.ev):
                best_multi = lad
    if in_range == 0:
        return None, "no_combo_in_joint_range"
    pick = best_multi if best_multi is not None and best_multi.ev > 0 else best_any
    if pick is None or pick.ev <= 0:
        return None, "no_positive_ev"
    return pick, "ok" if pick.n_games >= 2 else "ok_single_game_only"


def refine_ladder(
    lad: Ladder, book_fn: BookFn | None, cfg: PaperConfig, pb: PlaybookConfig, now: datetime
) -> tuple[Ladder | None, set[str], str]:  # fmt: skip
    """Reprice from the order-book top (listing quote kept when no book). None = refuse: a leg's
    book is one-sided or slips past the limit, or the repriced ladder leaves its joint range, has no
    positive EV, or is thinner than the contract count."""
    if book_fn is None:
        return lad, set(), "ok"
    legs: list[Leg] = []
    for x in lad.legs:
        book = book_fn(x.ticker)
        if book is None:
            legs.append(x)
            continue
        new = refine_leg(x, book, cfg, now)
        if new is None:
            return None, {x.ticker}, "book_refused"
        legs.append(new)
    out = price_ladder(lad.tier, tuple(legs), cfg)
    t = lad.tier
    bad = {x.ticker for x in legs}
    if any(not 0 < x.ask < 1 for x in legs) or min(x.ask_size for x in legs) < out.n:
        return None, bad, "book_too_thin"
    if not t.joint_lo - 1e-12 <= out.joint <= t.joint_hi + 1e-12:
        return None, bad, "book_joint_out_of_range"
    if out.ev <= 0:
        return None, bad, "book_no_positive_ev"
    return out, set(), "ok"


def to_plan(lad: Ladder, slot: str, ev_note: str) -> Plan:
    return Plan(
        slot, lad.legs, lad.n, lad.entry_price, lad.entry_fee, lad.cost, lad.p_model,
        lad.joint, lad.ev, f"{LABEL}; {ev_note}", True,
    )  # fmt: skip


def window_of(legs: list[Leg], now: datetime, cfg: PaperConfig) -> datetime | None:
    """The next tip window: earliest tip at least ``entry_lead_min`` away."""
    tips = sorted(
        {x.tipoff for x in legs if (x.tipoff - now).total_seconds() >= cfg.entry_lead_min * 60}
    )
    return tips[0] if tips else None


@dataclass
class WindowResult:
    scan_id: str
    window: datetime | None
    built: dict[str, str] = field(default_factory=dict)  # tier -> position_id
    skipped: dict[str, str] = field(default_factory=dict)  # tier -> reason
    notes: dict[str, Any] = field(default_factory=dict)


def run_playbook_scan(
    day: date, now: datetime, cfg0: PaperConfig, pb: PlaybookConfig, *,
    markets: list[dict[str, Any]], fc: Any, aliases: dict[str, int], db_path: Path,
    legs_file: Path, book_fn: BookFn | None = None,
) -> WindowResult:  # fmt: skip
    """One scan: a safe/medium/risky ladder for the next tip window, or a logged skip reason for
    each tier. Re-running the same window adds nothing for tiers already built."""
    cfg = playbook_cfg(cfg0, pb)
    scan_id = "pb" + uuid.uuid4().hex[:10]
    legs, unmatched, skipped_mk = legs_from_markets(markets, fc, cfg, aliases)
    eligible = [x for x in legs if screen_leg(x, cfg, now=now, check_ev=False)[0]]
    win = window_of(eligible, now, cfg)
    res = WindowResult(scan_id, win)
    res.notes = {
        "window": None if win is None else win.isoformat(),
        "n_legs": len(legs),
        "n_eligible_legs": len(eligible),
        "skipped_markets": skipped_mk,
        "unmatched": len(unmatched),
        "tiers": {},
    }
    with db.writer(db_path) as con:
        done: set[str] = set()
        if win is not None:
            rows = con.execute(
                "SELECT slot FROM positions WHERE slate_date = ? AND rule_version = ? "
                "AND role = ? AND slot LIKE ?",
                [day, cfg.rule_version, ROLE, f"%|{win.isoformat()}"],
            ).fetchall()
            done = {str(r[0]).split("|")[0] for r in rows}
        for tier in pb.tiers:
            info: dict[str, Any] = {}
            res.notes["tiers"][tier.name] = info
            if win is None:
                res.skipped[tier.name] = info["reason"] = "no_unstarted_window"
                continue
            if tier.name in done:
                info["reason"] = "already_built"
                continue
            avail = [x for x in eligible if x.tipoff >= win]  # games not yet started only
            rejected: set[str] = set()
            lad: Ladder | None = None
            reason = "no_eligible_legs"
            for _ in range(8):
                cand, reason = select_ladder(tier, avail, cfg, pb, rejected)
                if cand is None:
                    break
                lad, bad, why = refine_ladder(cand, book_fn, cfg, pb, now)
                if lad is not None:
                    break
                rejected |= bad
                info.setdefault("book_retries", []).append(why)
                reason = why
            if lad is None:
                res.skipped[tier.name] = info["reason"] = reason
                continue
            slot = f"{tier.name}|{win.isoformat()}"
            pid = insert_position(
                con, to_plan(lad, slot, reason), scan_id, day, now, cfg, ROLE, None
            )
            res.built[tier.name] = pid
            info.update(
                status="built", position_id=pid, n_legs=len(lad.legs), n_games=lad.n_games,
                joint=round(lad.joint, 4), ev_per_dollar=round(lad.ev, 4), n=lad.n,
                cost=round(lad.cost, 2), note=reason,
            )  # fmt: skip
        verdict = "playbook_ladders_built" if res.built else "no_positive_ev_found"
        con.execute(
            "INSERT INTO scans (scan_id, slate_date, made_at, rule_version, verdict, n_markets, "
            "n_candidates, n_positions, unmatched_names, params_json, notes_json, alias_sha, "
            "code_digest, digest_ok) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [scan_id, day, now, cfg.rule_version, verdict, len(markets), len(eligible),
             len(res.built), json.dumps(unmatched), json.dumps(pb.params()),
             json.dumps(res.notes, default=str), "", "", True],
        )  # fmt: skip
        refresh_open_legs(con, legs_file)
    return res


# ------------------------------------------------------------------------------ exit policy
def exit_net(marks: list[LegMark], n: int, cfg: PaperConfig) -> tuple[float, float, float]:
    """(gross product of bids, per-contract sum of per-leg exit fees, conservative net) per
    contract. Settled legs count 1 or 0 and carry no fee; the net never goes below zero."""
    gross = 1.0
    fee = 0.0
    for m in marks:
        if m.settled is not None:
            gross *= m.settled
            continue
        gross *= m.bid
        if 0.0 < m.bid < 1.0:
            fee += order_fee(m.bid, cfg.fee, n) / n
    return gross, fee, max(0.0, gross - fee)


def playbook_decision(
    marks: list[LegMark], n: int, cost: float, tier: TierSpec, cfg: PaperConfig,
    pb: PlaybookConfig,
) -> tuple[str, dict[str, float]]:  # fmt: skip
    """('hold'|'exit_take_profit'|'exit_ge_hold'|'hold_near_zero'|'let_run'|'wait_pretip'|
    'settling'|'dead', numbers). Values are per contract; cost is per contract incl. entry fee."""
    c = cost / n
    open_marks = [m for m in marks if m.settled is None]
    fair = hold = 1.0
    for m in marks:
        if m.settled is not None:
            fair *= m.settled
            hold *= m.settled
            continue
        mid = 0.5 * (m.bid + m.ask)
        fair *= mid
        # not started: shrunk model; live: mid (no in-game model yet, DECISIONS)
        hold *= mid if m.started else shrunk(m.p_model, mid, cfg.shrink)
    _, _, net = exit_net(marks, n, cfg)
    nums = {"fair": fair, "hold": hold, "net_exit": net, "cost_per_contract": c}
    if any(m.settled == 0.0 for m in marks):
        return "dead", nums
    if not open_marks:
        return "settling", nums
    if cfg.exit_from_first_tip and not any(m.started or m.settled is not None for m in marks):
        return "wait_pretip", nums
    hits = sum(1 for m in marks if m.settled == 1.0)
    if len(open_marks) == 1 and hits == len(marks) - 1:
        last = open_marks[0]
        if 0.5 * (last.bid + last.ask) >= pb.let_run_mid:
            return "let_run", nums
    if tier.take_profit_x is not None:
        if net >= tier.take_profit_x * c:
            return "exit_take_profit", nums
        return "hold", nums
    if hold < pb.near_zero_frac * c:
        return "hold_near_zero", nums
    if net > 0.0 and net >= hold:
        return "exit_ge_hold", nums
    return "hold", nums


def playbook_monitor_once(
    day: date, now: datetime, cfg0: PaperConfig, pb: PlaybookConfig, get_market: MarketGetter,
    db_path: Path, legs_file: Path,
) -> dict[str, int]:  # fmt: skip
    cfg = playbook_cfg(cfg0, pb)
    tiers = {t.name: t for t in pb.tiers}
    counts = {"positions": 0, "exits": 0, "skipped": 0}
    with db.writer(db_path) as con:
        cache: dict[str, dict[str, Any] | None] = {}
        for pos in open_positions(con, day, roles=(ROLE,)):
            counts["positions"] += 1
            row = con.execute(
                "SELECT slot, cost FROM positions WHERE position_id = ?", [pos.position_id]
            ).fetchone()
            assert row is not None
            tier = tiers[str(row[0]).split("|")[0]]
            cost = float(row[1])
            marks: list[LegMark] = []
            missing = False
            for leg in pos.legs:
                t = str(leg["ticker"])
                if t not in cache:
                    cache[t] = get_market(t)
                m = cache[t]
                if m is None:
                    missing = True
                    break
                marks.append(leg_mark(leg, m, now)[0])
            if missing:
                counts["skipped"] += 1
                continue
            decision, nums = playbook_decision(marks, pos.n, cost, tier, cfg, pb)
            open_legs = sum(1 for k in marks if k.settled is None)
            started = sum(1 for k in marks if k.started or k.settled is not None)
            gross, fee_pc, net = exit_net(marks, pos.n, cfg)
            con.execute(
                "INSERT INTO marks VALUES (?,?,?,?,?,?,?,?,?)",
                [pos.position_id, now, nums["fair"], nums["hold"], gross, open_legs, started,
                 decision, json.dumps({"legs": [[k.bid, k.ask, k.settled] for k in marks],
                                       "net_exit": net, "fee_per_contract": fee_pc,
                                       "cost_per_contract": nums["cost_per_contract"]})],
            )  # fmt: skip
            if decision in ("exit_take_profit", "exit_ge_hold"):
                con.execute(
                    "INSERT INTO exits VALUES (?,?,?,?,?,?,?,?)",
                    [pos.position_id, now, gross, fee_pc * pos.n, pos.n, nums["fair"],
                     nums["hold"], decision],
                )  # fmt: skip
                counts["exits"] += 1
        refresh_open_legs(con, legs_file)
    return counts


# ------------------------------------------------------------------------------ report
def playbook_report(db_path: Path, rule_version: str, day: date | None = None) -> dict[str, Any]:
    """Per-night ladder counts with skip reasons, and running paper P&L per tier: exit policy vs
    hold-to-end. Descriptive: n is shown on every figure, no interval, no claim."""
    out: dict[str, Any] = {"rule_version": rule_version, "claim": "none (descriptive, one arm)"}
    with db.reader(db_path) as con:
        nights: dict[str, Any] = {}
        for d, notes in con.execute(
            "SELECT slate_date, notes_json FROM scans WHERE rule_version = ? ORDER BY made_at",
            [rule_version],
        ).fetchall():
            n = nights.setdefault(str(d), {"scans": 0, "built": {}, "skipped": {}})
            n["scans"] += 1
            for tier, info in json.loads(notes).get("tiers", {}).items():
                if info.get("status") == "built":
                    n["built"][tier] = n["built"].get(tier, 0) + 1
                elif info.get("reason") not in (None, "already_built"):
                    r = n["skipped"].setdefault(tier, {})
                    r[info["reason"]] = r.get(info["reason"], 0) + 1
        out["nights"] = nights if day is None else {str(day): nights.get(str(day))}
        run: dict[str, Any] = {}
        for tier, _nb, nx, nset, cost, pe, ph in con.execute(
            "SELECT split_part(p.slot, '|', 1), count(*), "
            "sum(CASE WHEN r.exited THEN 1 ELSE 0 END),"
            " sum(CASE WHEN r.status = 'settled' THEN 1 ELSE 0 END), sum(r.cost),"
            " sum(r.pnl_exit_rule), sum(r.pnl_hold) FROM positions p "
            "JOIN results r ON r.position_id = p.position_id "
            "WHERE p.rule_version = ? AND p.role = ? AND r.status = 'settled' GROUP BY 1",
            [rule_version, ROLE],
        ).fetchall():
            run[tier] = {
                "n_settled": int(nset), "n_exited": int(nx), "cost": cost,
                "pnl_exit_policy": pe, "pnl_hold_to_end": ph,
            }  # fmt: skip
        out["running_by_tier"] = run
        waiting = con.execute(
            "SELECT split_part(slot, '|', 1), count(*) FROM positions WHERE rule_version = ? "
            "AND role = ? AND position_id NOT IN (SELECT position_id FROM results) GROUP BY 1",
            [rule_version, ROLE],
        ).fetchall()
        out["open_by_tier"] = {str(t): int(c) for t, c in waiting}
        out["exits_by_reason"] = {
            str(r): int(c)
            for r, c in con.execute(
                "SELECT e.reason, count(*) FROM exits e JOIN positions p USING (position_id) "
                "WHERE p.rule_version = ? AND p.role = ? GROUP BY 1",
                [rule_version, ROLE],
            ).fetchall()
        }
    return out
