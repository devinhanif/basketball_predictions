"""Paper-trade log: write recommendations, auto-settle against actuals, rolling report.

Log only. Nothing here talks to a market. Caller supplies the DuckDB connection
(the real DB is never opened for writing by this module's tests).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import duckdb
import numpy as np

from nba.parlay.config import FeeModel
from nba.parlay.ev import Recommendation, fee_per_contract
from nba.parlay.legs import Leg

_COMBOS: dict[str, tuple[str, ...]] = {
    "pra": ("pts", "reb", "ast"),
    "pr": ("pts", "reb"),
    "pa": ("pts", "ast"),
    "ra": ("reb", "ast"),
}
_BASE = {"pts", "reb", "ast", "fg3m", "stl", "blk", "tov"}


def log_trade(
    con: duckdb.DuckDBPyConnection,
    rec: Recommendation,
    fee: FeeModel,
    *,
    now: datetime | None = None,
    trade_id: str | None = None,
) -> str:
    """Insert one recommendation. Every recommendation (including no-EV ones) may be logged."""
    tid = trade_id or uuid.uuid4().hex
    ts = now or datetime.now(UTC).replace(tzinfo=None)
    con.execute(
        "INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?,?,?,FALSE,NULL,NULL)",
        [
            tid,
            ts,
            json.dumps(rec.legs),
            rec.model_prob,
            rec.prob_interval[0],
            rec.prob_interval[1],
            rec.price,
            json.dumps(fee.to_json_dict()),
            rec.ev,
        ],
    )
    return tid


_GAME_STATS = ("win", "spread", "total")


def _leg_yes(con: duckdb.DuckDBPyConnection, leg: Leg) -> bool | None:
    """True/False = the leg's YES condition resolved; None = no result yet.

    Raises ``DnpLeg`` when the player has a box-score row but did not play.
    """
    if leg.stat in _GAME_STATS:
        row = con.execute(
            "SELECT home_team, home_pts, away_pts FROM games WHERE game_id=?", [leg.game_id]
        ).fetchone()
        if row is None or row[1] is None or row[2] is None or row[1] + row[2] <= 0:
            return None
        if leg.stat == "total":
            return float(row[1] + row[2]) > leg.threshold
        margin = float(row[1] - row[2])
        sg = 1.0 if leg.team_id == row[0] else -1.0
        return sg * margin > (0.0 if leg.stat == "win" else leg.threshold)
    cols = _COMBOS.get(leg.stat) or ((leg.stat,) if leg.stat in _BASE else None)
    if cols is None:
        raise ValueError(f"cannot settle unknown stat {leg.stat!r}")
    row = con.execute(
        f"SELECT {' + '.join(f'COALESCE({c},0)' for c in cols)}, minutes FROM player_game_stats "
        "WHERE game_id=? AND player_id=?",
        [leg.game_id, leg.player_id],
    ).fetchone()
    if row is None or row[0] is None:
        return None
    if row[1] is None or row[1] <= 0:
        raise DnpLeg(leg)
    return float(row[0]) >= leg.threshold


class DnpLeg(Exception):
    """A player leg whose player did not play."""

    def __init__(self, leg: Leg) -> None:
        super().__init__(f"DNP: player {leg.player_id} in {leg.game_id}")
        self.leg = leg


def resolve_legs(
    res: duckdb.DuckDBPyConnection, legs: list[Leg], dnp_policy: str
) -> tuple[str, bool]:
    """('open'|'void'|'done', hit). A DNP player leg is void or a loss per ``dnp_policy``."""
    yes: list[bool | None] = []
    void = False
    lost_dnp = False
    for leg in legs:
        try:
            yes.append(_leg_yes(res, leg))
        except DnpLeg:
            yes.append(True)
            if dnp_policy == "void":
                void = True
            else:
                lost_dnp = True
    if any(v is None for v in yes):
        return "open", False
    if void:
        return "void", False
    hit = not lost_dnp and all(
        bool(v) == (leg.side == "yes") for v, leg in zip(yes, legs, strict=True)
    )
    return "done", hit


def settle_trades(
    con: duckdb.DuckDBPyConnection,
    results_con: duckdb.DuckDBPyConnection | None = None,
    dnp_policy: str = "void",
) -> int:
    """Settle trades whose every leg has a result. Returns count settled.

    ``results_con`` (default ``con``) holds games / player_game_stats and may be read-only.
    A leg with no result yet (e.g. game not played) leaves the trade open. A DNP player leg:
    ``void`` -> trade settled with outcome NULL and pnl 0 (stake refunded); ``loss`` -> loses.
    """
    res = results_con or con
    open_rows = con.execute(
        "SELECT trade_id, legs, price, fee_model FROM paper_trades WHERE NOT settled"
    ).fetchall()
    n = 0
    for tid, legs_json, price, fee_json in open_rows:
        legs = [Leg.from_dict(d) for d in json.loads(legs_json)]
        status, hit = resolve_legs(res, legs, dnp_policy)
        if status == "open":
            continue
        if status == "void":
            con.execute(
                "UPDATE paper_trades SET settled=TRUE, outcome=NULL, realized_pnl=0 "
                "WHERE trade_id=?",
                [tid],
            )
        else:
            fee = fee_per_contract(float(price), FeeModel.from_json_dict(json.loads(fee_json)))
            pnl = (1.0 if hit else 0.0) - float(price) - fee
            con.execute(
                "UPDATE paper_trades SET settled=TRUE, outcome=?, realized_pnl=? WHERE trade_id=?",
                [hit, pnl, tid],
            )
        n += 1
    return n


def rolling_report(con: duckdb.DuckDBPyConnection, min_settled: int, n_bins: int = 5) -> str:
    """Markdown: realized vs expected return, joint calibration, sample sizes."""
    rows = con.execute(
        "SELECT model_prob, outcome, expected_value, realized_pnl FROM paper_trades WHERE settled AND outcome IS NOT NULL"  # noqa: E501
    ).fetchall()
    total = con.execute("SELECT count(*) FROM paper_trades").fetchone()
    n_total = int(total[0]) if total else 0
    n = len(rows)
    lines = ["# Paper-trade rolling report", "", f"Trades logged: {n_total}; settled: n={n}"]
    if n == 0:
        return "\n".join([*lines, "", "No settled trades; nothing to report."])
    p = np.array([r[0] for r in rows], float)
    y = np.array([float(r[1]) for r in rows])
    ev = np.array([r[2] for r in rows], float)
    pnl = np.array([r[3] for r in rows], float)
    se = float(pnl.std(ddof=1) / np.sqrt(n)) if n > 1 else float("nan")
    lines += [
        "",
        f"- mean expected EV/contract: {ev.mean():+.4f}",
        f"- mean realized PnL/contract: {pnl.mean():+.4f} (+/- {1.96 * se:.4f} 95%, n={n})",
        f"- Brier of model_prob: {float(((p - y) ** 2).mean()):.4f}; hit rate {y.mean():.3f} "
        f"vs mean predicted {p.mean():.3f}",
    ]
    if n < min_settled:
        lines.append(
            f"- WARNING: n={n} < min_settled={min_settled}; no performance claim is supported."
        )
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    lines += ["", "| bin | n | mean pred | observed |", "|---|---|---|---|"]
    for a, b in zip(edges[:-1], edges[1:], strict=True):
        m = (p >= a) & ((p < b) if b < 1.0 else (p <= b))
        if m.any():
            lines.append(
                f"| [{a:.1f},{b:.1f}) | {int(m.sum())} | {p[m].mean():.3f} | {y[m].mean():.3f} |"
            )
    return "\n".join(lines)


def settle_and_report(con: duckdb.DuckDBPyConnection, min_settled: int) -> dict[str, Any]:
    n = settle_trades(con)
    return {"newly_settled": n, "report": rolling_report(con, min_settled)}
