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


def _stat_value(
    con: duckdb.DuckDBPyConnection, game_id: str, player_id: int, stat: str
) -> float | None:
    cols = _COMBOS.get(stat) or ((stat,) if stat in _BASE else None)
    if cols is None:
        raise ValueError(f"cannot settle unknown stat {stat!r}")
    row = con.execute(
        f"SELECT {' + '.join(f'COALESCE({c},0)' for c in cols)} FROM player_game_stats "
        "WHERE game_id=? AND player_id=?",
        [game_id, player_id],
    ).fetchone()
    return None if row is None or row[0] is None else float(row[0])


def settle_trades(con: duckdb.DuckDBPyConnection) -> int:
    """Settle trades whose every leg has a box-score row. Returns count settled.

    A leg with no box-score row (e.g. game not yet played) leaves the trade open.
    """
    open_rows = con.execute(
        "SELECT trade_id, legs, price, fee_model FROM paper_trades WHERE NOT settled"
    ).fetchall()
    n = 0
    for tid, legs_json, price, fee_json in open_rows:
        legs = [Leg.from_dict(d) for d in json.loads(legs_json)]
        vals = [_stat_value(con, leg.game_id, leg.player_id, leg.stat) for leg in legs]
        if any(v is None for v in vals):
            continue
        hit = all(
            (v >= leg.threshold) == (leg.side == "yes")
            for v, leg in zip(vals, legs, strict=True)
            if v is not None
        )
        fd = json.loads(fee_json)
        fee = fee_per_contract(
            float(price), FeeModel(fd["name"], fd["formula"], float(fd["coefficient"]))
        )
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
        "SELECT model_prob, outcome, expected_value, realized_pnl FROM paper_trades WHERE settled"
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
