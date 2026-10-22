"""``scan``: candidate legs (model vs the Kalshi ask after fees) and up to N paper positions.

Rules are in configs/papertrade.yaml and docs/prereg/PAPER_TRADER.md. A scan that finds nothing
records ``no_positive_ev_found``; a scan never enters at or after a leg's tip.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
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
    choose_slot,
    control_leg,
    flipped,
    leg_ev,
    screen_leg,
)

NO_EV = "no_positive_ev_found"


@dataclass
class CandidateRow:
    leg: Leg
    passes: bool
    reason: str
    p_shrunk: float
    ev: float
    ticker_series: str


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
    m: dict[str, Any], p_yes: float, game_id: str, tip: datetime, stat: str, pid: int, thr: float
) -> list[Leg] | None:
    q = kp.quote(m)
    yb, ya = q["yes_bid"], q["yes_ask"]
    if yb is None or ya is None:
        return None
    series = str(m["ticker"]).split("-")[0]
    yes = Leg(
        str(m["ticker"]), str(m["event_ticker"]), series, game_id, tip, stat, pid, thr, "yes",
        p_yes, yb, ya, q["yes_bid_size"] or 0.0, q["yes_ask_size"] or 0.0,
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
            both = _sides(m, p, w.game_id, w.tipoff, "win", 0, 0.0)
        elif series in cfg.prop_series:
            stat = cfg.prop_series[series]
            try:
                name, _ = parse_prop_title(str(m.get("title", "")))
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
            w2 = game_of_event.get(teams) if teams else None
            if w2 is not None and w2.game_id != f.game_id:
                bump("event_game_mismatch")
                continue
            if f.p_play < cfg.min_p_play:
                bump("p_play_below_floor")
                continue
            k = int(strike // 1) + 1
            p_cond = f.p_ge(k)
            if p_cond is None:
                bump("beyond_forecast_support")
                continue
            both = _sides(m, p_cond, f.game_id, f.tipoff, stat, pid, float(k))
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


def _existing(con: duckdb.DuckDBPyConnection, day: date) -> tuple[list[str], set[str]]:
    rows = con.execute(
        "SELECT slot FROM positions WHERE slate_date = ? AND role = 'strategy'", [day]
    ).fetchall()
    used = con.execute(
        "SELECT l.ticker FROM position_legs l JOIN positions p USING (position_id) "
        "WHERE p.slate_date = ?",
        [day],
    ).fetchall()
    return [r[0] for r in rows], {r[0] for r in used}


def check_entry(plan: Plan, now: datetime, cfg: PaperConfig) -> None:
    """Refuse an entry made at or after any leg's tip (and inside the configured lead)."""
    for leg in plan.legs:
        if now >= leg.tipoff:
            raise EntryRefused(f"entry at or after tip: {leg.ticker} tip {leg.tipoff} now {now}")
        if (leg.tipoff - now).total_seconds() < cfg.entry_lead_min * 60:
            raise EntryRefused(f"entry inside the lead time: {leg.ticker}")


def control_plan(plan: Plan, cfg: PaperConfig) -> Plan | None:
    from nba.papertrade.rules import build_plan

    legs = tuple(control_leg(leg, flipped(leg)) for leg in plan.legs)
    if any(not (0.0 < leg.ask < 1.0) for leg in legs):
        return None
    return build_plan(plan.slot, legs, cfg)


def insert_position(
    con: duckdb.DuckDBPyConnection, plan: Plan, scan_id: str, day: date, now: datetime,
    cfg: PaperConfig, role: str, twin_id: str | None,
) -> str:  # fmt: skip
    pid = uuid.uuid4().hex
    first_tip = min(leg.tipoff for leg in plan.legs)
    con.execute(
        "INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [pid, scan_id, day, now, role, plan.slot, twin_id, len(plan.legs), plan.n,
         plan.entry_price, plan.entry_fee, plan.cost, first_tip, plan.p_model, plan.p_shrunk,
         plan.ev_per_dollar, plan.label, cfg.rule_version],
    )  # fmt: skip
    for i, leg in enumerate(plan.legs):
        con.execute(
            "INSERT INTO position_legs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [pid, i, leg.ticker, leg.event_ticker, leg.series, leg.game_id, leg.tipoff, leg.side,
             leg.stat, leg.player_id, leg.threshold, leg.p_model,
             leg.p_model if role == "control" else _shr(leg, cfg), leg.bid, leg.ask, leg.mid,
             leg.ask_size, leg.bid_size],
        )  # fmt: skip
    return pid


def _shr(leg: Leg, cfg: PaperConfig) -> float:
    from nba.papertrade.rules import shrunk

    return shrunk(leg.p_model, leg.mid, cfg.shrink)


def refresh_open_legs(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    """Sidecar for the capture job: tickers of positions neither settled nor exited."""
    rows = con.execute(
        "SELECT DISTINCT l.ticker FROM position_legs l JOIN positions p USING (position_id) "
        "WHERE p.position_id NOT IN (SELECT position_id FROM results) "
        "AND p.position_id NOT IN (SELECT position_id FROM exits)"
    ).fetchall()
    write_json_atomic(path, {"tickers": sorted(r[0] for r in rows)})


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
) -> ScanResult:
    """Pure of network: markets and forecasts are passed in (the CLI fetches them)."""
    scan_id = uuid.uuid4().hex[:12]
    legs, unmatched, skipped = legs_from_markets(markets, fc, cfg, aliases)
    rows: list[CandidateRow] = []
    for leg in legs:
        ok, why = screen_leg(leg, cfg, now=now)
        p_s = _shr(leg, cfg)
        rows.append(
            CandidateRow(
                leg, ok, why, p_s, leg_ev(leg, cfg) if 0 < leg.ask < 1 else 0.0, leg.series
            )
        )
    passing = [r.leg for r in rows if r.passes]
    res = ScanResult(scan_id, NO_EV, len(markets), len(passing), unmatched=unmatched)
    res.notes = {"skipped_markets": skipped, "n_legs_screened": len(rows)}
    with db.writer(db_path) as con:
        filled, used = _existing(con, day)
        todo = list(cfg.slots)
        for s in filled:
            if s in todo:
                todo.remove(s)
        if not todo:
            res.verdict = "slots_already_filled"
        for slot in todo:
            plan = choose_slot(slot, passing, used, cfg)
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
            cplan = control_plan(plan, cfg)
            if cplan is not None:
                insert_position(con, cplan, scan_id, day, now, cfg, "control", sid)
            res.positions.append(sid)
            used |= {leg.ticker for leg in plan.legs}
            res.verdict = "positions_opened"
        con.execute(
            "INSERT INTO scans VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [scan_id, day, now, cfg.rule_version, res.verdict, res.n_markets, res.n_candidates,
             len(res.positions), json.dumps(unmatched), json.dumps(cfg.params()),
             json.dumps(res.notes)],
        )  # fmt: skip
        for r in rows:
            lg = r.leg
            con.execute(
                "INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [scan_id, lg.ticker + ("|no" if lg.side == "no" else ""), lg.series, lg.game_id,
                 lg.tipoff, lg.stat, lg.player_id, lg.threshold, lg.p_model, r.p_shrunk, lg.bid,
                 lg.ask, lg.spread, lg.ask_size, r.ev, r.passes, r.reason],
            )  # fmt: skip
        refresh_open_legs(con, legs_file)
    return res
