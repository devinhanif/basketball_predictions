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
from nba.papertrade.stats import Ratio, cluster_ratio, concentration

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


def look_dates(
    per_date_counts: list[tuple[str, int]], cfg: PaperConfig, last_date: str
) -> dict[str, str | None]:
    """L1 / L2 slate dates from (date, settled primary positions on that date), dates ascending.
    L1: first date with cumulative positions >= look1_min_positions and dates >= look1_min_dates.
    L2: the look2_dates-th slate date, or the first slate date on/after the season end."""
    cum = 0
    l1 = l2 = None
    for i, (d, n) in enumerate(per_date_counts, start=1):
        cum += n
        if l1 is None and cum >= cfg.look1_min_positions and i >= cfg.look1_min_dates:
            l1 = d
        if l2 is None and (i >= cfg.look2_dates or d >= cfg.season_end_date.isoformat()):
            l2 = d
    return {"L1": l1, "L2": l2}


def _arm(rows: list[tuple[Any, ...]], col: int, cfg: PaperConfig, alpha: float) -> Ratio:
    return cluster_ratio(
        [str(r[1]) for r in rows], [float(r[col]) for r in rows], [float(r[2]) for r in rows],
        reps=cfg.bootstrap_reps, seed=cfg.bootstrap_seed, alpha=alpha,
    )  # fmt: skip


def _pack(r: Ratio, with_ci: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "n_positions": r.n_positions, "n_slate_dates": r.n_dates, "cost": r.cost, "pnl": r.pnl,
        "pnl_per_dollar": r.per_dollar,
    }  # fmt: skip
    if with_ci:
        out["ci"] = r.ci
    return out


def evaluate_look(
    rows: list[tuple[Any, ...]], clv: list[tuple[str, float]], cfg: PaperConfig, alpha: float
) -> dict[str, Any]:
    """The look's result on primary-scope rows (singles at real prices) up to the look date."""
    a = _arm(rows, 4, cfg, alpha)
    b = _arm(rows, 3, cfg, alpha)
    sec_alpha = alpha / 2  # Holm over the two secondaries (a-b, CLV): conservative half
    ab = cluster_ratio(
        [str(r[1]) for r in rows], [float(r[4]) - float(r[3]) for r in rows],
        [float(r[2]) for r in rows], reps=cfg.bootstrap_reps, seed=cfg.bootstrap_seed,
        alpha=sec_alpha,
    )  # fmt: skip
    both = [r for r in rows if r[7] is not None]
    ap = cluster_ratio(
        [str(r[1]) for r in both],
        [float(r[4]) - float(r[8]) * float(r[2]) / float(r[7]) for r in both],
        [float(r[2]) for r in both], reps=cfg.bootstrap_reps, seed=cfg.bootstrap_seed, alpha=alpha,
    )  # fmt: skip
    placebo = cluster_ratio(
        [str(r[1]) for r in both], [float(r[8]) for r in both], [float(r[7]) for r in both],
        reps=cfg.bootstrap_reps, seed=cfg.bootstrap_seed, alpha=alpha,
    )  # fmt: skip
    cl = cluster_ratio(
        [d for d, _ in clv], [v for _, v in clv], [1.0] * len(clv),
        reps=cfg.bootstrap_reps, seed=cfg.bootstrap_seed, alpha=sec_alpha,
    )  # fmt: skip
    conc = concentration([str(r[1]) for r in rows], [float(r[4]) for r in rows])
    lo = a.ci[0] if a.ci else None
    hi = a.ci[1] if a.ci else None
    gate = {
        "a_ci_lower_above_zero": lo is not None and lo > 0,
        "a_minus_placebo_ci_lower_above_zero": bool(ap.ci and ap.ci[0] > 0),
        "concentration_ok": conc is not None and conc <= cfg.concentration_cap,
    }
    return {
        "alpha": alpha,
        "a_exit_rule": _pack(a, True),
        "b_hold_to_end": _pack(b, True),
        "a_minus_b": _pack(ab, True) | {"alpha": sec_alpha},
        "a_minus_placebo_stake_matched": _pack(ap, True) | {"n_pairs": len(both)},
        "placebo_hold": _pack(placebo, True),
        "clv_per_contract": _pack(cl, True)
        | {"alpha": sec_alpha, "unit": "mid_before_tip - entry_ask"},
        "concentration_top_date_share_of_abs_pnl": conc,
        "futility_close": hi is not None and hi < 0,
        "live_money_conditions": gate,
        "may_consider_live_money": all(gate.values()),
    }


def clv_rows(con: Any, position_ids: list[str], dates: dict[str, str]) -> list[tuple[str, float]]:
    """(slate date, last pre-tip mid of the held side - entry ask) per single-leg position."""
    out = []
    for pid in position_ids:
        leg = con.execute(
            "SELECT ask, tipoff FROM position_legs WHERE position_id = ? AND leg_idx = 0", [pid]
        ).fetchone()
        if leg is None:
            continue
        m = con.execute(
            "SELECT detail_json FROM marks WHERE position_id = ? AND made_at < ? "
            "ORDER BY made_at DESC LIMIT 1",
            [pid, leg[1]],
        ).fetchone()
        if m is None:
            continue
        bid, ask, _ = json.loads(m[0])[0]
        out.append((dates[pid], 0.5 * (float(bid) + float(ask)) - float(leg[0])))
    return out


def summarize(db_path: Path, cfg: PaperConfig) -> dict[str, Any]:
    """Counts and point values always; CIs only at the two named looks, on data up to the look date.
    Scope: this rule_version, slate_date >= freeze_date, singles at real prices (parlays are
    synthetic and descriptive). A shakedown rule version is never scored."""
    if not cfg.scored:
        return {"rule_version": cfg.rule_version, "verdict": "unscored_shakedown"}
    with db.reader(db_path) as con:
        rows = con.execute(
            "SELECT s.position_id, s.slate_date, s.cost, s.pnl_hold, s.pnl_exit_rule, p.slot, "
            "p.synthetic, c.cost, c.pnl_hold FROM results s "
            "JOIN positions p ON p.position_id = s.position_id "
            "LEFT JOIN (SELECT p2.twin_id, r.cost, r.pnl_hold FROM results r "
            "JOIN positions p2 ON p2.position_id = r.position_id "
            "WHERE r.role = 'placebo' AND r.status = 'settled') c ON c.twin_id = s.position_id "
            "WHERE s.role = 'strategy' AND s.status = 'settled' AND p.rule_version = ? "
            "AND p.slate_date >= ? ORDER BY s.slate_date",
            [cfg.rule_version, cfg.freeze_date],
        ).fetchall()
        n_void = con.execute(
            "SELECT count(*) FROM results r JOIN positions p ON p.position_id = r.position_id "
            "WHERE r.status = 'void' AND p.rule_version = ? AND p.slate_date >= ?",
            [cfg.rule_version, cfg.freeze_date],
        ).fetchone()
        n_waiting = con.execute(
            "SELECT count(*) FROM positions WHERE role = 'strategy' AND rule_version = ? "
            "AND slate_date >= ? AND position_id NOT IN (SELECT position_id FROM results)",
            [cfg.rule_version, cfg.freeze_date],
        ).fetchone()
        scans = con.execute(
            "SELECT count(*), sum(CASE WHEN verdict = 'no_positive_ev_found' THEN 1 ELSE 0 END) "
            "FROM scans WHERE rule_version = ? AND slate_date >= ?",
            [cfg.rule_version, cfg.freeze_date],
        ).fetchone()
        assert n_void and n_waiting and scans
        primary = [r for r in rows if not r[6]]
        synthetic = [r for r in rows if r[6]]
        by_date: dict[str, int] = {}
        for r in primary:
            by_date[str(r[1])] = by_date.get(str(r[1]), 0) + 1
        ordered = sorted(by_date.items())
        looks = look_dates(ordered, cfg, ordered[-1][0] if ordered else "")
        out_looks: dict[str, Any] = {}
        for name, alpha in (("L1", cfg.alpha_l1), ("L2", cfg.alpha_l2)):
            d = looks[name]
            if d is None:
                out_looks[name] = {"status": "not_reached"}
                continue
            sub = [r for r in primary if str(r[1]) <= d]
            ids = [str(r[0]) for r in sub]
            clv = clv_rows(con, ids, {str(r[0]): str(r[1]) for r in sub})
            out_looks[name] = {"status": "taken", "data_through": d} | evaluate_look(
                sub, clv, cfg, alpha
            )
    cur = {
        "a_exit_rule": _pack(_arm(primary, 4, cfg, 0.05), False) if primary else None,
        "b_hold_to_end": _pack(_arm(primary, 3, cfg, 0.05), False) if primary else None,
    }
    syn = _pack(_arm(synthetic, 4, cfg, 0.05), False) if synthetic else None
    sc = scans
    return {
        "rule_version": cfg.rule_version,
        "freeze_date": cfg.freeze_date.isoformat(),
        "scope": "singles at real prices; slate_date >= freeze_date; settled non-void only",
        "current_not_a_look": cur
        | {"status": "not_a_look", "note": "counts and point values only; no CI, no claim"},
        "looks": out_looks,
        "synthetic_parlays_descriptive": syn,
        "n_void": int(n_void[0]),
        "n_waiting_positions": int(n_waiting[0]),
        "n_scans": int(sc[0]),
        "n_scans_no_positive_ev_found": int(sc[1] or 0),
    }


def dump(summary: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str))
