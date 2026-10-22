"""``monitor``: mark every open strategy position each tick and log the paper exit at the real bids.

Exit rule (frozen in docs/prereg/PAPER_TRADER.md): once any leg's game has tipped, exit when the
value of selling every leg at its bid is >= max(fair, hold) - 1c. Exits are rows; nothing is sent.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from nba.live import kalshi_pull as kp
from nba.papertrade import db
from nba.papertrade.config import PaperConfig
from nba.papertrade.rules import LegMark, exit_decision, exit_proceeds
from nba.papertrade.scan import refresh_open_legs

EXIT_REASON = "net_proceeds_ge_hold_x_1_plus_margin"
MarketGetter = Callable[[str], dict[str, Any] | None]


@dataclass
class OpenPosition:
    position_id: str
    n: int
    role: str
    legs: list[dict[str, Any]]


def open_positions(
    con: Any, day: date, roles: tuple[str, ...] = ("strategy", "placebo")
) -> list[OpenPosition]:
    """Positions neither exited nor settled for the slate. Strategy and placebo are both marked
    every tick (the pre-tip marks give the closing-line value); only strategy positions can exit."""
    rows = con.execute(
        "SELECT position_id, n_contracts, role FROM positions WHERE slate_date = ? "
        "AND position_id NOT IN (SELECT position_id FROM exits) "
        "AND position_id NOT IN (SELECT position_id FROM results) ORDER BY made_at",
        [day],
    ).fetchall()
    out = []
    for pid, n, role in rows:
        if role not in roles:
            continue
        cur = con.execute(
            "SELECT ticker, series, side, p_model, tipoff FROM position_legs "
            "WHERE position_id = ? ORDER BY leg_idx",
            [pid],
        )
        names = [d[0] for d in cur.description]
        legs = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
        out.append(OpenPosition(pid, int(n), str(role), legs))
    return out


def leg_mark(leg: dict[str, Any], market: dict[str, Any], now: datetime) -> tuple[LegMark, float]:
    """(mark for the held side, the held side's bid size)."""
    q = kp.quote(market)
    side = str(leg["side"])
    result = str(market.get("result") or "")
    settled = None if result not in ("yes", "no") else float(result == side)
    bid = (q["yes_bid"] if side == "yes" else q["no_bid"]) or 0.0
    ask = (q["yes_ask"] if side == "yes" else q["no_ask"]) or 0.0
    size = (q["yes_bid_size"] if side == "yes" else q["yes_ask_size"]) or 0.0
    started = now >= leg["tipoff"]
    return LegMark(side, float(leg["p_model"]), bid, ask, started, settled), size


def monitor_once(
    day: date, now: datetime, cfg: PaperConfig, get_market: MarketGetter, db_path: Path,
    legs_file: Path,
) -> dict[str, int]:  # fmt: skip
    """One tick over every open strategy position. Returns counts (positions, exits, skipped)."""
    counts = {"positions": 0, "exits": 0, "skipped": 0}
    with db.writer(db_path) as con:
        positions = open_positions(con, day)
        cache: dict[str, dict[str, Any] | None] = {}
        for pos in positions:
            counts["positions"] += 1
            marks: list[LegMark] = []
            sizes_ok = True
            missing = False
            for leg in pos.legs:
                t = str(leg["ticker"])
                if t not in cache:
                    cache[t] = get_market(t)
                m = cache[t]
                if m is None:
                    missing = True
                    break
                mk, size = leg_mark(leg, m, now)
                marks.append(mk)
                if mk.settled is None and size < pos.n:
                    sizes_ok = False
            if missing:
                counts["skipped"] += 1
                continue
            series = str(pos.legs[0]["series"]) if len(pos.legs) == 1 else ""
            decision, fair, hold, est = exit_decision(marks, pos.n, cfg, sizes_ok, series)
            if pos.role != "strategy" and decision in ("exit", "size_blocked"):
                decision = "hold"  # placebo positions are marked, never exited
            open_legs = sum(1 for k in marks if k.settled is None)
            started = sum(1 for k in marks if k.started or k.settled is not None)
            con.execute(
                "INSERT INTO marks VALUES (?,?,?,?,?,?,?,?,?)",
                [pos.position_id, now, fair, hold, est, open_legs, started, decision,
                 json.dumps([[k.bid, k.ask, k.settled] for k in marks])],
            )  # fmt: skip
            if decision == "exit":
                fee, _ = exit_proceeds(est, pos.n, cfg, series)
                con.execute(
                    "INSERT INTO exits VALUES (?,?,?,?,?,?,?,?)",
                    [pos.position_id, now, est, fee, pos.n, fair, hold, EXIT_REASON],
                )  # fmt: skip
                counts["exits"] += 1
        refresh_open_legs(con, legs_file)
    return counts
