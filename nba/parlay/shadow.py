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
import numpy as np
from scipy import stats

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
            [None if status in ("void", "last_price") else hit, sid],
        )
        n += 1
    return n


@dataclass(frozen=True)
class TrackRecord:
    n_settled: int
    brier_model: float | None
    brier_market: float | None
    skill: float  # 1 - Brier_model / Brier_market; 0 if undefined
    n_dates: int = 0  # distinct settled game dates (props on one night are correlated)
    # Paired per-row Brier difference (market - model; > 0 means the model is better), its mean
    # and the LOWER bound of a two-sided game-clustered CI. None = no interval available, which
    # keeps the gate shut (the default verdict is no_positive_ev_found).
    diff_mean: float | None = None
    diff_lo: float | None = None
    n_games: int = 0  # clusters behind the interval

    def gated_skill(self, min_dates: int) -> float:
        """``skill`` only if (a) the history spans ``min_dates`` distinct dates and (b) the lower
        bound of the game-clustered CI of the paired Brier difference is above 0; else 0.

        Thirty settled rows can arrive in a single night; they share that night's luck, so the
        row count alone overstates the evidence, and a point estimate at small n opens by luck
        about one time in three even when the true skill is negative (ledger audit pass 2 N6).
        Below either bar the engine defers to the market entirely.
        """
        if self.n_dates < min_dates or self.diff_lo is None or self.diff_lo <= 0.0:
            return 0.0
        return self.skill

    def describe(self) -> str:
        if self.n_settled == 0:
            return "no settled shadow history (n=0)"
        return (
            f"n={self.n_settled} over {self.n_dates} date(s), Brier model {self.brier_model:.4f} "
            f"vs market {self.brier_market:.4f}, skill {self.skill:+.4f}; "
            + (
                f"paired Brier gain {self.diff_mean:+.4f}, lower bound {self.diff_lo:+.4f} "
                f"({self.n_games} games)"
                if self.diff_lo is not None and self.diff_mean is not None
                else f"no game-clustered interval ({self.n_games} games): gate shut"
            )
        )


def clustered_lower_bound(
    d: np.ndarray, groups: np.ndarray, ci_level: float
) -> tuple[float, float | None, int]:
    """(mean, lower bound or None, n_clusters) of per-row differences ``d`` with cluster-robust
    SE (CR1) and a t quantile on G-1 df. Fewer than 3 clusters gives no bound."""
    n = len(d)
    mean = float(d.mean())
    ids, inv = np.unique(groups, return_inverse=True)
    g = len(ids)
    if g < 3:
        return mean, None, g
    sums = np.bincount(inv, weights=d - mean, minlength=g)
    se = float(np.sqrt(g / (g - 1.0) * float((sums**2).sum())) / n)
    q = float(stats.t.ppf(1.0 - (1.0 - ci_level) / 2.0, g - 1))
    return mean, mean - q * se, g


def track_record(con: duckdb.DuckDBPyConnection, ci_level: float = 0.95) -> TrackRecord:
    """Skill of the raw model vs the market mid on settled shadow rows.

    Only ``side = 'yes'`` rows count: ``evaluate`` logs BOTH sides of every market, and the NO
    row is the same event (same outcome, complementary probability), so counting it would double
    ``n`` and open the ``min_settled`` gate on half the real evidence.

    ``n_dates`` counts distinct SLATE (game) dates, read from the ``<slate>|<ticker>|<side>``
    shadow_id that ``evaluate`` writes. ``created_at`` is a UTC wall clock: one game night's
    props posted before and after 00:00Z would count as two dates and open the 14-date gate
    early. Rows with a free-form id fall back to the created_at date.
    """
    row = con.execute(
        "SELECT count(*), avg(pow(raw_model_prob - CAST(outcome AS DOUBLE), 2)), "
        "avg(pow(market_mid - CAST(outcome AS DOUBLE), 2)), "
        "count(DISTINCT CASE WHEN regexp_matches(shadow_id, '^[0-9]{4}-[0-9]{2}-[0-9]{2}[|]') "
        "THEN split_part(shadow_id, '|', 1) ELSE CAST(CAST(created_at AS DATE) AS VARCHAR) END) "
        "FROM shadow_predictions WHERE settled AND outcome IS NOT NULL AND side = 'yes'"
    ).fetchone()
    n = int(row[0]) if row else 0
    n_dates = int(row[3]) if row else 0
    if n == 0 or row is None or row[2] is None or row[2] <= 0:
        return TrackRecord(n, None, None, 0.0, n_dates)
    skill = 1.0 - float(row[1]) / float(row[2])
    rows = con.execute(
        "SELECT raw_model_prob, market_mid, CAST(outcome AS DOUBLE), "
        "COALESCE(json_extract_string(legs, '$[0].game_id'), shadow_id) "
        "FROM shadow_predictions WHERE settled AND outcome IS NOT NULL AND side = 'yes'"
    ).fetchall()
    a = np.array([[r[0], r[1], r[2]] for r in rows], dtype=float)
    diff = (a[:, 1] - a[:, 2]) ** 2 - (a[:, 0] - a[:, 2]) ** 2
    mean, lo, n_games = clustered_lower_bound(diff, np.array([str(r[3]) for r in rows]), ci_level)
    return TrackRecord(n, float(row[1]), float(row[2]), skill, n_dates, mean, lo, n_games)
