"""``llm_shadow_calls``: one row per attempt, in the shadow database (its own DuckDB file).

Also the token budget: the day's and month's spend are sums over this table (real-provider rows
only), so there is no second ledger to drift. The guard runs BEFORE a call.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

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
                                 -- | monthly_cap_usd_not_set | wall_cap_skip | usage_limit
                                 -- | usage_limit_skip | auth_failed
                                 -- | tool_restriction_violated | search_cap_exceeded
                                 -- | not_sampled
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
#: audit columns (claude_code provider); added with ALTER so an existing database migrates in place.
#: Queries and URLs only: no result text and no price is ever stored.
AUDIT_COLUMNS: dict[str, str] = {
    "provider": "VARCHAR",
    "model_requested": "VARCHAR",
    "web_queries": "VARCHAR",  # JSON list
    "web_urls": "VARCHAR",  # JSON list
    "n_searches": "INTEGER",
    "n_fetches": "INTEGER",
    "odds_domain_flag": "BOOLEAN",  # any URL or result snippet from a sportsbook / odds domain
    "odds_domains": "VARCHAR",  # JSON list of the matched domains
    "odds_url_terms": "VARCHAR",  # JSON list of betting terms hit in a result URL path or title
    "odds_query_flag": "BOOLEAN",  # a search query used odds / spread / sportsbook terms
    "wall_s": "DOUBLE",
    "api_key_source": "VARCHAR",
    "injection_flag": "BOOLEAN",  # an injection marker in any tool-result text (search output)
    "injection_markers": "VARCHAR",  # JSON list of marker NAMES (never the matched text)
    "injection_suspected": "BOOLEAN",  # the model's own report in its answer (NULL if absent)
    "tools_used": "VARCHAR",  # JSON list of tool names called in the stream
}


def open_shadow(path: Path, retries: int = 6, wait_s: float = 2.0) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for _ in range(max(1, retries)):
        try:
            con = duckdb.connect(str(path))
            con.execute(DDL)
            for col, typ in AUDIT_COLUMNS.items():
                con.execute(f"ALTER TABLE llm_shadow_calls ADD COLUMN IF NOT EXISTS {col} {typ}")
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
    audit: Mapping[str, Any] | None = None,
) -> None:
    base: dict[str, Any] = {
        "game_id": game_id, "slate_date": slate, "requested_at": requested_at,
        "received_at": received_at, "status": status, "model_id": model_id,
        "prompt_sha256": prompt_sha256, "payload_sha256": payload_sha256,
        "tokens_in": tokens_in, "tokens_out": tokens_out, "budget_skip": budget_skip,
        "provider_real": provider_real, "detail": detail[:300],
    }  # fmt: skip
    if audit:
        base.update(audit_columns(audit))
    cols = list(base)
    con.execute(
        f"INSERT INTO llm_shadow_calls ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        list(base.values()),
    )


def audit_columns(audit: Mapping[str, Any]) -> dict[str, Any]:
    """The audit dict a provider returns -> llm_shadow_calls columns (lists as JSON text)."""
    out: dict[str, Any] = {
        "provider": audit.get("provider"),
        "model_requested": audit.get("model_requested"),
        "web_queries": json.dumps(list(audit.get("web_queries", []))),
        "web_urls": json.dumps(list(audit.get("web_urls", []))),
        "n_searches": int(audit.get("n_searches", 0)),
        "n_fetches": len(audit.get("web_fetch_urls", [])),
        "odds_domain_flag": bool(audit.get("odds_domain_flag", False)),
        "odds_domains": json.dumps(list(audit.get("odds_domains", []))),
        "odds_url_terms": json.dumps(list(audit.get("odds_url_terms", []))),
        "odds_query_flag": bool(audit.get("odds_query_flag", False)),
        "wall_s": float(audit.get("wall_s", 0.0)),
        "api_key_source": audit.get("api_key_source"),
        "injection_flag": bool(audit.get("injection_flag", False)),
        "injection_markers": json.dumps(list(audit.get("injection_markers", []))),
        "injection_suspected": audit.get("injection_suspected"),
        "tools_used": json.dumps(list(audit.get("tools_used", []))),
    }
    return out


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


def usage_limited(con: duckdb.DuckDBPyConnection, slate: date) -> bool:
    """A subscription usage limit or auth failure already stopped tonight's slate."""
    row = con.execute(
        "SELECT count(*) FROM llm_shadow_calls WHERE slate_date = ? AND provider_real "
        "AND status IN ('usage_limit', 'auth_failed')",
        [slate],
    ).fetchone()
    return bool(row and row[0])


def guard(
    con: duckdb.DuckDBPyConnection,
    slate: date,
    cfg: ShadowConfig,
    *,
    real: bool,
    usd_cap_required: bool = True,
    usage_limit_stops_night: bool = False,
) -> str | None:
    """Why the next call must be refused, or None. Counts the planning allowance for the call.

    ``usd_cap_required`` applies to the API provider only; the subscription provider is guarded by
    the token ceiling, the monthly token backstop and ``usage_limit_stops_night``."""
    plan = cfg.planning_tokens_per_game
    if real and usd_cap_required and (cfg.monthly_cap_usd is None or cfg.monthly_cap_usd <= 0):
        return "monthly_cap_usd_not_set"
    if real and usage_limit_stops_night and usage_limited(con, slate):
        return "usage_limit_skip"
    if real and tokens_day(con, slate) + plan > cfg.nightly_token_ceiling:
        return "budget_skip"
    if real and tokens_month(con, slate) + plan > cfg.monthly_token_backstop:
        return "monthly_backstop"
    return None
