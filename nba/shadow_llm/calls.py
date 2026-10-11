"""``llm_shadow_calls``: one row per attempt, in the shadow database (its own DuckDB file).

Also the token budget: the day's and month's spend are sums over this table (real-provider rows
only), so there is no second ledger to drift. The guard runs BEFORE a call.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from pathlib import Path

import duckdb

from nba.shadow_llm.config import ShadowConfig

DDL = """
CREATE TABLE IF NOT EXISTS llm_shadow_calls (
    game_id VARCHAR,
    slate_date DATE,
    requested_at TIMESTAMP,      -- our clock before the request (naive UTC)
    received_at TIMESTAMP,       -- our clock after the response (NULL when nothing was called)
    status VARCHAR,              -- ok | dry_run | schema_violation | post_tip_refused
                                 -- | provider_error | budget_skip | monthly_backstop
                                 -- | monthly_cap_usd_not_set
    model_id VARCHAR,
    prompt_sha256 VARCHAR,
    payload_sha256 VARCHAR,
    tokens_in INT,
    tokens_out INT,
    budget_skip BOOLEAN,
    provider_real BOOLEAN,       -- stub rows never count toward the budget
    detail VARCHAR
);
"""


def open_shadow(path: Path, retries: int = 6, wait_s: float = 2.0) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for _ in range(max(1, retries)):
        try:
            con = duckdb.connect(str(path))
            con.execute(DDL)
            return con
        except duckdb.IOException as exc:
            last = exc
            time.sleep(wait_s)
    assert last is not None
    raise last


def record_call(
    con: duckdb.DuckDBPyConnection,
    *,
    game_id: str,
    slate: date,
    requested_at: datetime,
    received_at: datetime | None,
    status: str,
    model_id: str = "",
    prompt_sha256: str = "",
    payload_sha256: str = "",
    tokens_in: int = 0,
    tokens_out: int = 0,
    budget_skip: bool = False,
    provider_real: bool = True,
    detail: str = "",
) -> None:
    con.execute(
        "INSERT INTO llm_shadow_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            game_id, slate, requested_at, received_at, status, model_id, prompt_sha256,
            payload_sha256, tokens_in, tokens_out, budget_skip, provider_real, detail[:300],
        ],
    )  # fmt: skip


def tokens_day(con: duckdb.DuckDBPyConnection, slate: date) -> int:
    row = con.execute(
        "SELECT coalesce(sum(tokens_in + tokens_out), 0) FROM llm_shadow_calls "
        "WHERE slate_date = ? AND provider_real",
        [slate],
    ).fetchone()
    return int(row[0]) if row else 0


def tokens_month(con: duckdb.DuckDBPyConnection, slate: date) -> int:
    row = con.execute(
        "SELECT coalesce(sum(tokens_in + tokens_out), 0) FROM llm_shadow_calls "
        "WHERE provider_real AND year(slate_date) = ? AND month(slate_date) = ?",
        [slate.year, slate.month],
    ).fetchone()
    return int(row[0]) if row else 0


def guard(
    con: duckdb.DuckDBPyConnection, slate: date, cfg: ShadowConfig, *, real: bool
) -> str | None:
    """Why the next call must be refused, or None. Counts the planning allowance for the call."""
    plan = cfg.planning_tokens_per_game
    if real and (cfg.monthly_cap_usd is None or cfg.monthly_cap_usd <= 0):
        return "monthly_cap_usd_not_set"
    if real and tokens_day(con, slate) + plan > cfg.nightly_token_ceiling:
        return "budget_skip"
    if real and tokens_month(con, slate) + plan > cfg.monthly_token_backstop:
        return "monthly_backstop"
    return None
