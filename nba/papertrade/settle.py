"""``settle``: P&L after fees from Kalshi's own results, for the exit rule, hold-to-end and control.

(a) strategy with the exit rule (the logged exit if one fired, else held to the end),
(b) strategy held to the end, (c) market-favourite control held to the end. A void or fair-priced
leg (inactive player) with no stated settlement value voids the position and it is counted, not
guessed. Nothing here talks to a market except GET /markets/{ticker}.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

from nba.live import kalshi_pull as kp
from nba.papertrade import db
from nba.papertrade.config import PaperConfig
from nba.papertrade.monitor import MarketGetter
from nba.papertrade.stats import Ratio, cluster_ratio

FINAL_STATUSES = ("finalized", "settled", "determined")


def leg_factor(market: dict[str, Any], side: str) -> tuple[str, float | None, float | None]:
    """(market_result, payout factor for the held side or None if unresolved/void, stated value)."""
    result = str(market.get("result") or "")
    if result in ("yes", "no"):
        return result, float(result == side), None
    if str(market.get("status", "")) in FINAL_STATUSES:
        v = kp.to_f(market.get("settlement_value_dollars"))
        if v is not None:  # settled to a fair price: yes pays v, no pays 1 - v
            return result or "fair_price", (v if side == "yes" else 1.0 - v), v
        return result or "void", None, None
    return "", None, None


def settle_positions(
    day: date, now: datetime, cfg: PaperConfig, get_market: MarketGetter, db_path: Path
) -> dict[str, int]:  # fmt: skip
    counts = {"settled": 0, "void": 0, "waiting": 0}
    with db.writer(db_path) as con:
        pos = con.execute(
            "SELECT position_id, slate_date, role, n_contracts, cost FROM positions "
            "WHERE slate_date <= ? AND position_id NOT IN (SELECT position_id FROM results)",
            [day],
        ).fetchall()
        cache: dict[str, dict[str, Any] | None] = {}
        for pid, sdate, role, n, cost in pos:
            legs = con.execute(
                "SELECT leg_idx, ticker, side FROM position_legs WHERE position_id = ? "
                "ORDER BY leg_idx",
                [pid],
            ).fetchall()
            facts: list[tuple[int, str, float | None, float | None]] = []
            unresolved = False
            for idx, ticker, side in legs:
                if ticker not in cache:
                    cache[ticker] = get_market(ticker)
                m = cache[ticker]
                if m is None:
                    unresolved = True
                    break
                res, factor, val = leg_factor(m, side)
                if res == "":
                    unresolved = True
                    break
                facts.append((idx, res, factor, val))
            if unresolved:
                counts["waiting"] += 1
                continue
            void = any(f[2] is None for f in facts)
            payout = None if void else math.prod(f[2] for f in facts if f[2] is not None)
            for idx, res, factor, val in facts:
                con.execute(
                    "INSERT INTO leg_results VALUES (?,?,?,?,?,?)",
                    [pid, idx, now, res, None if factor is None else factor >= 1.0, val],
                )
            ex = con.execute(
                "SELECT exit_price, exit_fee, n_contracts FROM exits WHERE position_id = ?", [pid]
            ).fetchone()
            pnl_hold = None if payout is None else n * payout - cost
            if void:
                pnl_exit = None
            elif ex is not None:
                pnl_exit = ex[2] * ex[0] - ex[1] - cost
            else:
                pnl_exit = pnl_hold
            con.execute(
                "INSERT INTO results VALUES (?,?,?,?,?,?,?,?,?,?)",
                [pid, now, sdate, role, "void" if void else "settled", cost, payout, pnl_hold,
                 pnl_exit, ex is not None],
            )  # fmt: skip
            counts["void" if void else "settled"] += 1
    return counts


def summarize(db_path: Path, cfg: PaperConfig) -> dict[str, Any]:
    """Primary and secondary comparisons: n, date-clustered CIs, insufficient-sample flag."""
    with db.reader(db_path) as con:
        rows = con.execute(
            "SELECT s.position_id, s.slate_date, s.cost, s.pnl_hold, s.pnl_exit_rule, s.exited, "
            "c.cost, c.pnl_hold FROM results s "
            "LEFT JOIN (SELECT p2.twin_id, r.cost, r.pnl_hold FROM results r JOIN positions p2 "
            "USING (position_id) WHERE r.role = 'control' AND r.status = 'settled') c "
            "ON c.twin_id = s.position_id "
            "WHERE s.role = 'strategy' AND s.status = 'settled'"
        ).fetchall()
        n_void = con.execute("SELECT count(*) FROM results WHERE status = 'void'").fetchone()
        n_pos = con.execute("SELECT count(*) FROM positions WHERE role = 'strategy'").fetchone()
        n_scans = con.execute(
            "SELECT count(*), sum(CASE WHEN verdict = 'no_positive_ev_found' THEN 1 ELSE 0 END) "
            "FROM scans"
        ).fetchone()
    kw: dict[str, Any] = {
        "reps": cfg.bootstrap_reps, "seed": cfg.bootstrap_seed,
        "min_dates": cfg.min_slate_dates, "min_positions": cfg.min_positions,
    }  # fmt: skip
    dates = [str(r[1]) for r in rows]
    cost = [float(r[2]) for r in rows]

    def ratio(pnl: list[float], c: list[float], d: list[str]) -> Ratio:
        return cluster_ratio(d, pnl, c, **kw)

    a = ratio([float(r[4]) for r in rows], cost, dates)
    b = ratio([float(r[3]) for r in rows], cost, dates)
    both = [r for r in rows if r[6] is not None]
    cd = [str(r[1]) for r in both]
    cc = [float(r[2]) for r in both]
    a_vs_c = ratio([float(r[4]) - float(r[7]) * float(r[2]) / float(r[6]) for r in both], cc, cd)
    b_vs_a = ratio([float(r[3]) - float(r[4]) for r in rows], cost, dates)
    ctrl = ratio([float(r[7]) for r in both], [float(r[6]) for r in both], cd)

    def pack(r: Ratio) -> dict[str, Any]:
        return {
            "n_positions": r.n_positions, "n_slate_dates": r.n_dates, "cost": r.cost,
            "pnl": r.pnl, "pnl_per_dollar": r.per_dollar, "ci95_by_date": r.ci,
            "verdict": "insufficient_sample" if not r.sufficient else "claim_eligible",
        }  # fmt: skip

    return {
        "rule_version": cfg.rule_version,
        "a_exit_rule": pack(a),
        "b_hold_to_end": pack(b),
        "c_control_hold": pack(ctrl),
        "b_minus_a_per_dollar_of_a_cost": pack(b_vs_a),
        "a_minus_c_stake_matched": pack(a_vs_c),
        "n_void": int(n_void[0]) if n_void else 0,
        "n_strategy_positions": int(n_pos[0]) if n_pos else 0,
        "n_scans": int(n_scans[0]) if n_scans else 0,
        "n_scans_no_positive_ev_found": int(n_scans[1] or 0) if n_scans else 0,
        "note": "No claim below the pre-registered minimum; see docs/prereg/PAPER_TRADER.md.",
    }


def dump(summary: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str))
