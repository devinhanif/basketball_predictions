"""Shadow log: every evaluated contract's RAW model probability next to the market price.

Paper trades are logged only for flagged positives, but while the engine defers to the market
no positive can ever be flagged, so the track record that would lift that deferral could never
accrue. The shadow log records raw model vs market for every evaluated contract (no stake
implied) and settles it against actuals; ``track_record`` turns settled rows into the skill
statistic that ``shrink_to_market`` uses. Log only; nothing here talks to a market.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import duckdb

from nba.parlay.legs import Leg
from nba.parlay.papertrade import resolve_legs

SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_predictions (
  shadow_id VARCHAR PRIMARY KEY, created_at TIMESTAMP, ticker VARCHAR, side VARCHAR,
  legs JSON, raw_model_prob DOUBLE, market_mid DOUBLE, price DOUBLE, engine VARCHAR,
  settled BOOLEAN, outcome BOOLEAN
)
"""
PAPER_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
  trade_id VARCHAR PRIMARY KEY, created_at TIMESTAMP, legs JSON,
  model_prob FLOAT, model_prob_lo FLOAT, model_prob_hi FLOAT,
  price FLOAT, fee_model VARCHAR, expected_value FLOAT,
  settled BOOLEAN, outcome BOOLEAN, realized_pnl FLOAT
)
"""


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(SCHEMA)
    con.execute(PAPER_SCHEMA)


def log_shadow(
    con: duckdb.DuckDBPyConnection,
    *,
    shadow_id: str,
    ticker: str,
    side: str,
    legs: list[Leg],
    raw_model_prob: float,
    market_mid: float,
    price: float,
    engine: str,
    now: datetime | None = None,
) -> bool:
    """Insert once per shadow_id (first prediction of the day stands). True if inserted."""
    before = con.execute("SELECT count(*) FROM shadow_predictions").fetchone()
    con.execute(
        "INSERT INTO shadow_predictions VALUES (?,?,?,?,?,?,?,?,?,FALSE,NULL) "
        "ON CONFLICT DO NOTHING",
        [
            shadow_id,
            now or datetime.now(UTC).replace(tzinfo=None),
            ticker,
            side,
            json.dumps([leg.to_dict() for leg in legs]),
            raw_model_prob,
            market_mid,
            price,
            engine,
        ],
    )
    after = con.execute("SELECT count(*) FROM shadow_predictions").fetchone()
    return bool(before and after and after[0] > before[0])


def settle_shadow(
    con: duckdb.DuckDBPyConnection,
    results_con: duckdb.DuckDBPyConnection,
    dnp_policy: str = "void",
) -> int:
    n = 0
    for sid, legs_json in con.execute(
        "SELECT shadow_id, legs FROM shadow_predictions WHERE NOT settled"
    ).fetchall():
        legs = [Leg.from_dict(d) for d in json.loads(legs_json)]
        status, hit = resolve_legs(results_con, legs, dnp_policy)
        if status == "open":
            continue
        con.execute(
            "UPDATE shadow_predictions SET settled=TRUE, outcome=? WHERE shadow_id=?",
            [None if status == "void" else hit, sid],
        )
        n += 1
    return n


@dataclass(frozen=True)
class TrackRecord:
    n_settled: int
    brier_model: float | None
    brier_market: float | None
    skill: float  # 1 - Brier_model / Brier_market; 0 if undefined

    def describe(self) -> str:
        if self.n_settled == 0:
            return "no settled shadow history (n=0)"
        return (
            f"n={self.n_settled}, Brier model {self.brier_model:.4f} vs market "
            f"{self.brier_market:.4f}, skill {self.skill:+.4f}"
        )


def track_record(con: duckdb.DuckDBPyConnection) -> TrackRecord:
    """Skill of the raw model vs the market mid on settled shadow rows.

    Only ``side = 'yes'`` rows count: ``evaluate`` logs BOTH sides of every market, and the NO
    row is the same event (same outcome, complementary probability), so counting it would double
    ``n`` and open the ``min_settled`` gate on half the real evidence.
    """
    row = con.execute(
        "SELECT count(*), avg(pow(raw_model_prob - CAST(outcome AS DOUBLE), 2)), "
        "avg(pow(market_mid - CAST(outcome AS DOUBLE), 2)) "
        "FROM shadow_predictions WHERE settled AND outcome IS NOT NULL AND side = 'yes'"
    ).fetchone()
    n = int(row[0]) if row else 0
    if n == 0 or row is None or row[2] is None or row[2] <= 0:
        return TrackRecord(n, None, None, 0.0)
    return TrackRecord(n, float(row[1]), float(row[2]), 1.0 - float(row[1]) / float(row[2]))
