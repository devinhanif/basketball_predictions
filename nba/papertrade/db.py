"""Append-only paper-trade store (its own DuckDB file; never nba.duckdb).

Every write is an INSERT; no row is ever modified or removed (a test greps this package for the
mutating statements); "state" such as exited or settled is derived from later rows.
Connections are opened per operation and closed, so scan, monitor and settle never hold the
single writer between writes.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

DDL = """
CREATE TABLE IF NOT EXISTS scans (
  scan_id VARCHAR, slate_date DATE, made_at TIMESTAMP, rule_version VARCHAR, verdict VARCHAR,
  n_markets INT, n_candidates INT, n_positions INT, unmatched_names VARCHAR, params_json VARCHAR,
  notes_json VARCHAR, alias_sha VARCHAR, code_digest VARCHAR, digest_ok BOOLEAN);
CREATE TABLE IF NOT EXISTS candidates (
  scan_id VARCHAR, ticker VARCHAR, series VARCHAR, game_id VARCHAR, tipoff TIMESTAMP, stat VARCHAR,
  player_id INT, threshold DOUBLE, p_model DOUBLE, p_shrunk DOUBLE, bid DOUBLE, ask DOUBLE,
  spread DOUBLE, ask_size DOUBLE, ev_per_dollar DOUBLE, passes BOOLEAN, reason VARCHAR,
  forecast_made_at TIMESTAMP, forecast_version VARCHAR);
CREATE TABLE IF NOT EXISTS positions (
  position_id VARCHAR, scan_id VARCHAR, slate_date DATE, made_at TIMESTAMP, role VARCHAR,
  slot VARCHAR, twin_id VARCHAR, n_legs INT, n_contracts INT, entry_price DOUBLE,
  entry_fee DOUBLE, cost DOUBLE, first_tip TIMESTAMP, p_model DOUBLE, p_shrunk DOUBLE,
  ev_per_dollar DOUBLE, entry_label VARCHAR, rule_version VARCHAR, synthetic BOOLEAN);
CREATE TABLE IF NOT EXISTS position_legs (
  position_id VARCHAR, leg_idx INT, ticker VARCHAR, event_ticker VARCHAR, series VARCHAR,
  game_id VARCHAR, tipoff TIMESTAMP, side VARCHAR, stat VARCHAR, player_id INT, threshold DOUBLE,
  p_model DOUBLE, p_shrunk DOUBLE, bid DOUBLE, ask DOUBLE, mid DOUBLE, ask_size DOUBLE,
  bid_size DOUBLE, forecast_made_at TIMESTAMP, forecast_version VARCHAR, entry_source VARCHAR,
  book_ts TIMESTAMP);
CREATE TABLE IF NOT EXISTS marks (
  position_id VARCHAR, made_at TIMESTAMP, fair DOUBLE, hold DOUBLE, exit_est DOUBLE,
  open_legs INT, started_legs INT, decision VARCHAR, detail_json VARCHAR);
CREATE TABLE IF NOT EXISTS exits (
  position_id VARCHAR, made_at TIMESTAMP, exit_price DOUBLE, exit_fee DOUBLE, n_contracts INT,
  fair DOUBLE, hold DOUBLE, reason VARCHAR);
CREATE TABLE IF NOT EXISTS leg_results (
  position_id VARCHAR, leg_idx INT, settled_at TIMESTAMP, market_result VARCHAR, leg_hit BOOLEAN,
  settlement_value DOUBLE);
CREATE TABLE IF NOT EXISTS results (
  position_id VARCHAR, settled_at TIMESTAMP, slate_date DATE, role VARCHAR, status VARCHAR,
  cost DOUBLE, payout_per_contract DOUBLE, pnl_hold DOUBLE, pnl_exit_rule DOUBLE, exited BOOLEAN);
CREATE TABLE IF NOT EXISTS refusals (
  made_at TIMESTAMP, slate_date DATE, reason VARCHAR, detail_json VARCHAR);
"""


def guard_path(path: Path) -> None:
    if path.name == "nba.duckdb":
        raise ValueError("the paper trader never opens nba.duckdb for writing")


@contextmanager
def writer(
    path: Path, *, wait_s: float = 60.0, sleep: Any = time.sleep
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open the paper store for a short write, retrying on the single-writer lock."""
    guard_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait_s
    while True:
        try:
            con = duckdb.connect(str(path))
            break
        except duckdb.IOException:
            if time.monotonic() > deadline:
                raise
            sleep(1.0)
    try:
        con.execute(DDL)
        yield con
    finally:
        con.close()


@contextmanager
def reader(path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    guard_path(path)
    con = duckdb.connect(str(path), read_only=True)
    try:
        yield con
    finally:
        con.close()
