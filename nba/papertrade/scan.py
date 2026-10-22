"""``scan``: candidate legs (model vs the Kalshi ask after fees) and up to N paper positions.

Rules are in configs/papertrade.yaml and docs/prereg/PAPER_TRADER.md. A scan that finds nothing
records ``no_positive_ev_found``; a scan never enters at or after a leg's tip; a scored rule
version never opens a position when the rule digest does not match the freeze record.
"""

from __future__ import annotations

import hashlib
import json
import random
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from nba.kalshi.aliases import UnmatchedKalshiNameError, resolve_kalshi_player_name
from nba.kalshi.thresholds import UnparseableTitleError, parse_prop_title
from nba.live import kalshi_pull as kp
from nba.live.http import Fetcher
from nba.live.store import RawStore, write_json_atomic
from nba.papertrade import db
from nba.papertrade.config import PaperConfig
from nba.papertrade.forecasts import Forecasts
from nba.papertrade.rules import (
    EntryRefused,
    Leg,
    Plan,
    build_plan,
    choose_slot,
    draw_placebo,
    flipped,
    leg_ev,
    screen_leg,
    shrunk,
)

NO_EV = "no_positive_ev_found"
REFUSED_DIGEST = "refused_digest_mismatch"
BookFn = Callable[[str], dict[str, Any] | None]


@dataclass
class CandidateRow:
    leg: Leg
    passes: bool
    reason: str
    p_shrunk: float
    ev: float


@dataclass
class ScanResult:
    scan_id: str
    verdict: str
    n_markets: int = 0
    n_candidates: int = 0
    positions: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)


def _sides(
    m: dict[str, Any], p_yes: float, game_id: str, tip: datetime, stat: str, pid: int, thr: float,
    made_at: datetime | None = None, version: str = "",
) -> list[Leg] | None:  # fmt: skip
    q = kp.quote(m)
    yb, ya = q["yes_bid"], q["yes_ask"]
    if yb is None or ya is None:
        return None
    series = str(m["ticker"]).split("-")[0]
    yes = Leg(
        str(m["ticker"]), str(m["event_ticker"]), series, game_id, tip, stat, pid, thr, "yes",
        p_yes, yb, ya, q["yes_bid_size"] or 0.0, q["yes_ask_size"] or 0.0,
        made_at, version,
    )  # fmt: skip
    return [yes, flipped(yes)]


def legs_from_markets(
    markets: list[dict[str, Any]],
    fc: Forecasts,
    cfg: PaperConfig,
    aliases: dict[str, int],
) -> tuple[list[Leg], list[str], dict[str, int]]:
    """Both sides of every market the forecasts can price: (legs, unmatched names, skip counts)."""
    legs: list[Leg] = []
    unmatched: set[str] = set()
    skip: dict[str, int] = {}

    def bump(why: str) -> None:
        skip[why] = skip.get(why, 0) + 1

    game_of_event = {(w.away, w.home): w for w in fc.wins.values()}
    for m in markets:
        ticker = str(m.get("ticker", ""))
        series = ticker.split("-")[0]
        teams = kp.event_teams(str(m.get("event_ticker", "")))
        if series == "KXNBAGAME":
            w = game_of_event.get(teams) if teams else None
            if w is None:
                bump("win_no_forecast")
                continue
            side_abbr = ticker.rsplit("-", 1)[-1]
            p = w.p_home if side_abbr == w.home else 1.0 - w.p_home if side_abbr == w.away else None
            if p is None:
                bump("win_team_not_in_event")
                continue
            both = _sides(m, p, w.game_id, w.tipoff, "win", 0, 0.0, w.made_at, "")
        elif series in cfg.prop_series:
            stat = cfg.prop_series[series]
            try:
                name, title_n = parse_prop_title(str(m.get("title", "")))
            except UnparseableTitleError:
                bump("prop_title_unparseable")
                continue
            try:
                pid = resolve_kalshi_player_name(name, aliases)
            except UnmatchedKalshiNameError:
                unmatched.add(name)
                bump("unmatched_name")
                continue
            f = fc.props.get((pid, stat))
            strike = kp.to_f(m.get("floor_strike"))
            if f is None or strike is None:
                bump("no_forecast_for_player")
                continue
            k = int(strike // 1) + 1
            if str(m.get("strike_type", "")) != "greater" or float(k) != title_n:
                bump("threshold_mismatch")
                continue
            w2 = game_of_event.get(teams) if teams else None
            if w2 is not None and w2.game_id != f.game_id:
                bump("event_game_mismatch")
                continue
            if f.p_play < cfg.min_p_play:
                bump("p_play_below_floor")
                continue
            p_cond = f.p_ge(k)
            if p_cond is None:
                bump("beyond_forecast_support")
                continue
            both = _sides(m, p_cond, f.game_id, f.tipoff, stat, pid, float(k), f.made_at, f.version)
        else:
            continue
        if both is None:
            bump("no_quote")
            continue
        legs += both
    return legs, sorted(unmatched), skip


def fetch_markets(
    fetcher: Fetcher, day: date, series: list[str], raw: RawStore | None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in series:
        for page, resp, body in kp.iter_series_pages(fetcher, s):
            if resp is None or body is None:
                break
            kept, _ = kp.filter_for_date([m for m in body.get("markets", [])], day)
            out += kept
            if raw is not None:
                raw.save(
                    "scan_markets", f"{s}_p{page}",
                    json.dumps({"markets": kept}, sort_keys=True).encode(),
                    http_status=resp.status,
                )  # fmt: skip
    return out


# ------------------------------------------------------------------------------ order-book entry
def _levels(book: dict[str, Any], key: str) -> list[tuple[float, float]]:
    out = []
    for lv in (book.get("orderbook_fp") or {}).get(key) or []:
        try:
            out.append((float(lv[0]), float(lv[1])))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def refine_leg(leg: Leg, book: dict[str, Any], cfg: PaperConfig, now: datetime) -> Leg | None:
    """Reprice a leg from the top of the order book (the held side's ask is 1 - the best opposite
    bid). None = refuse: one-sided book, or the book ask is worse than the listing ask by more
    than ``max_book_slippage``."""
    own, opp = ("yes_dollars", "no_dollars") if leg.side == "yes" else ("no_dollars", "yes_dollars")
    own_l, opp_l = _levels(book, own), _levels(book, opp)
    if not own_l or not opp_l:
        return None
    best_opp = max(opp_l)
    best_own = max(own_l)
    ask = round(1.0 - best_opp[0], 4)
    if ask - leg.ask > cfg.max_book_slippage + 1e-9:
        return None
    return replace(
        leg, ask=ask, ask_size=best_opp[1], bid=best_own[0], bid_size=best_own[1],
        entry_source="book", book_ts=now,
    )  # fmt: skip


def refine_plan(
    plan: Plan, book_fn: BookFn | None, cfg: PaperConfig, now: datetime
) -> tuple[Plan | None, set[str]]:
    """Plan repriced from the books; (None, bad tickers) if a leg is refused or the repriced plan
    no longer clears the size and EV screens. Without a book (no function or no answer) the
    listing quote is kept and labelled ``listing``."""
    if book_fn is None:
        return plan, set()
    legs: list[Leg] = []
    for leg in plan.legs:
        book = book_fn(leg.ticker)
        if book is None:
            legs.append(leg)
            continue
        new = refine_leg(leg, book, cfg, now)
        if new is None:
            return None, {leg.ticker}
        legs.append(new)
    out = replace(build_plan(plan.slot, tuple(legs), cfg), label=plan.label)
    if min(x.ask_size for x in legs) < out.n or any(not 0 < x.ask < 1 for x in legs):
        return None, {x.ticker for x in legs}
    if (
        plan.slot != "placebo"
        and not _is_placebo(plan)
        and out.ev_per_dollar < cfg.min_ev_per_dollar
    ):
        return None, {x.ticker for x in legs}
    return out, set()


def _is_placebo(plan: Plan) -> bool:
    return plan.label.startswith("placebo")


# ------------------------------------------------------------------------------ persistence
def _existing(
    con: duckdb.DuckDBPyConnection, day: date, rule_version: str
) -> tuple[list[str], set[str]]:
    rows = con.execute(
        "SELECT slot FROM positions WHERE slate_date = ? AND role = 'strategy' "
        "AND rule_version = ?",
        [day, rule_version],
    ).fetchall()
    used = con.execute(
        "SELECT l.ticker, l.game_id, l.player_id, l.stat FROM position_legs l "
        "JOIN positions p USING (position_id) WHERE p.slate_date = ? AND p.rule_version = ?",
        [day, rule_version],
    ).fetchall()
    keys: set[str] = set()
    for t, g, pid, stat in used:
        keys |= {t, f"{g}|{pid}|{stat}"}
    return [r[0] for r in rows], keys


def check_entry(plan: Plan, now: datetime, cfg: PaperConfig) -> None:
    """Refuse an entry made at or after any leg's tip (and inside the configured lead)."""
    for leg in plan.legs:
        if now >= leg.tipoff:
            raise EntryRefused(f"entry at or after tip: {leg.ticker} tip {leg.tipoff} now {now}")
        if (leg.tipoff - now).total_seconds() < cfg.entry_lead_min * 60:
            raise EntryRefused(f"entry inside the lead time: {leg.ticker}")


def insert_position(
    con: duckdb.DuckDBPyConnection, plan: Plan, scan_id: str, day: date, now: datetime,
    cfg: PaperConfig, role: str, twin_id: str | None,
) -> str:  # fmt: skip
    pid = uuid.uuid4().hex
    first_tip = min(leg.tipoff for leg in plan.legs)
    con.execute(
        "INSERT INTO positions (position_id, scan_id, slate_date, made_at, role, slot, twin_id, "
        "n_legs, n_contracts, entry_price, entry_fee, cost, first_tip, p_model, p_shrunk, "
        "ev_per_dollar, entry_label, rule_version, synthetic) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [pid, scan_id, day, now, role, plan.slot, twin_id, len(plan.legs), plan.n,
         plan.entry_price, plan.entry_fee, plan.cost, first_tip, plan.p_model, plan.p_shrunk,
         plan.ev_per_dollar, plan.label, cfg.rule_version, plan.synthetic],
    )  # fmt: skip
    for i, leg in enumerate(plan.legs):
        con.execute(
            "INSERT INTO position_legs (position_id, leg_idx, ticker, event_ticker, series, "
            "game_id, tipoff, side, stat, player_id, threshold, p_model, p_shrunk, bid, ask, mid, "
            "ask_size, bid_size, forecast_made_at, forecast_version, entry_source, book_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [pid, i, leg.ticker, leg.event_ticker, leg.series, leg.game_id, leg.tipoff, leg.side,
             leg.stat, leg.player_id, leg.threshold, leg.p_model,
             leg.p_model if role == "placebo" else shrunk(leg.p_model, leg.mid, cfg.shrink),
             leg.bid, leg.ask, leg.mid, leg.ask_size, leg.bid_size, leg.forecast_made_at,
             leg.forecast_version, leg.entry_source, leg.book_ts],
        )  # fmt: skip
    return pid


def refresh_open_legs(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    """Sidecar for the capture job: tickers of positions neither settled nor exited."""
    rows = con.execute(
        "SELECT DISTINCT l.ticker FROM position_legs l JOIN positions p USING (position_id) "
        "WHERE p.position_id NOT IN (SELECT position_id FROM results) "
        "AND p.position_id NOT IN (SELECT position_id FROM exits)"
    ).fetchall()
    write_json_atomic(path, {"tickers": sorted(r[0] for r in rows)})


def placebo_rng(scan_id: str) -> random.Random:
    return random.Random(int(hashlib.sha256(scan_id.encode()).hexdigest(), 16) % (2**63))


def run_scan(
    day: date,
    now: datetime,
    cfg: PaperConfig,
    *,
    markets: list[dict[str, Any]],
    fc: Forecasts,
    aliases: dict[str, int],
    db_path: Path,
    legs_file: Path,
    book_fn: BookFn | None = None,
    digest_status: tuple[bool, str] = (True, "not checked"),
    digests: dict[str, str] | None = None,
) -> ScanResult:
    """Pure of network except ``book_fn`` (the CLI passes the public order-book GET)."""
    scan_id = uuid.uuid4().hex[:12]
    legs, unmatched, skipped = legs_from_markets(markets, fc, cfg, aliases)
    rows: list[CandidateRow] = []
    for leg in legs:
        ok, why = screen_leg(leg, cfg, now=now)
        p_s = shrunk(leg.p_model, leg.mid, cfg.shrink)
        rows.append(CandidateRow(leg, ok, why, p_s, leg_ev(leg, cfg) if 0 < leg.ask < 1 else 0.0))
    passing = [r.leg for r in rows if r.passes]
    universe = [x for x in legs if screen_leg(x, cfg, now=now, check_ev=False)[0]]
    res = ScanResult(scan_id, NO_EV, len(markets), len(passing), unmatched=unmatched)
    res.notes = {
        "skipped_markets": skipped,
        "n_legs_screened": len(rows),
        "n_placebo_universe": len(universe),
        "digest": digest_status[1],
    }
    digests = digests or {}
    refused = cfg.scored and not digest_status[0]
    if refused:
        res.verdict = REFUSED_DIGEST
    with db.writer(db_path) as con:
        filled, used = _existing(con, day, cfg.rule_version)
        todo = [] if refused else list(cfg.slots)
        for s in filled:
            if s in todo:
                todo.remove(s)
        if not todo and not refused:
            res.verdict = "slots_already_filled"
        rng = placebo_rng(scan_id)
        for slot in todo:
            plan: Plan | None = None
            rejected: set[str] = set()
            for _ in range(8):
                pool = [x for x in passing if x.ticker not in rejected]
                cand = choose_slot(slot, pool, used, cfg)
                if cand is None:
                    break
                plan, bad = refine_plan(cand, book_fn, cfg, now)
                if plan is not None:
                    break
                rejected |= bad
                res.notes.setdefault("book_refusals", []).append(sorted(bad))
            if plan is None:
                res.notes.setdefault("slots_without_plan", []).append(slot)
                continue
            try:
                check_entry(plan, now, cfg)
            except EntryRefused as exc:
                res.refusals.append(str(exc))
                con.execute(
                    "INSERT INTO refusals VALUES (?,?,?,?)",
                    [now, day, "entry_refused", json.dumps({"slot": slot, "why": str(exc)})],
                )
                continue
            sid = insert_position(con, plan, scan_id, day, now, cfg, "strategy", None)
            res.positions.append(sid)
            used |= {x.ticker for x in plan.legs} | {x.family for x in plan.legs}
            res.verdict = "positions_opened"
            placebo = draw_placebo(plan, universe, rng, cfg)
            if placebo is not None:
                placebo = replace(placebo, label="placebo: " + placebo.label)
                placebo, _bad = refine_plan(placebo, book_fn, cfg, now)
            if placebo is not None:
                insert_position(con, placebo, scan_id, day, now, cfg, "placebo", sid)
            else:
                res.notes.setdefault("placebo_missing", []).append(slot)
        con.execute(
            "INSERT INTO scans (scan_id, slate_date, made_at, rule_version, verdict, n_markets, "
            "n_candidates, n_positions, unmatched_names, params_json, notes_json, alias_sha, "
            "code_digest, digest_ok) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [scan_id, day, now, cfg.rule_version, res.verdict, res.n_markets, res.n_candidates,
             len(res.positions), json.dumps(unmatched), json.dumps(cfg.params()),
             json.dumps(res.notes), digests.get("aliases", ""), digests.get("combined", ""),
             digest_status[0]],
        )  # fmt: skip
        for r in rows:
            lg = r.leg
            con.execute(
                "INSERT INTO candidates (scan_id, ticker, series, game_id, tipoff, stat, "
                "player_id, "
                "threshold, p_model, p_shrunk, bid, ask, spread, ask_size, ev_per_dollar, passes, "
                "reason, forecast_made_at, forecast_version) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [scan_id, lg.ticker + ("|no" if lg.side == "no" else ""), lg.series, lg.game_id,
                 lg.tipoff, lg.stat, lg.player_id, lg.threshold, lg.p_model, r.p_shrunk, lg.bid,
                 lg.ask, lg.spread, lg.ask_size, r.ev, r.passes, r.reason, lg.forecast_made_at,
                 lg.forecast_version],
            )  # fmt: skip
        refresh_open_legs(con, legs_file)
    return res
