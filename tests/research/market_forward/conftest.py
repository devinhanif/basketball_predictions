"""Fixtures: a tiny synthetic Kalshi capture (DuckDB file, gz raw pages, schedule parquet)."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest

from research.market_forward import data as D

TIP = datetime(2026, 10, 27, 23, 0)  # 19:00 ET, ET date 2026-10-27
GAME = "26OCT27AAABBB"


@dataclass
class Mk:
    ticker: str
    series: str
    result: str | None
    bid: float | None
    ask: float | None
    ts: datetime | None = None  # entry row time; default tip - 70 min
    stat: str = "pts"
    threshold: float = 10.5
    player_id: int | None = 1
    scalar: str | None = None
    settled_ts: datetime | None = None
    event: str | None = None
    extra_rows: list[tuple[datetime, float, float]] = field(default_factory=list)


def build_capture(
    tmp: Path, mks: list[Mk], tip: datetime = TIP, game: str = GAME
) -> dict[str, Path]:
    db = tmp / "kalshi_copy.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        "CREATE TABLE kalshi_markets (ticker VARCHAR, series_ticker VARCHAR, event_ticker VARCHAR, "
        "title VARCHAR, player_id INTEGER, stat VARCHAR, threshold FLOAT, open_time TIMESTAMP, "
        "close_time TIMESTAMP, settled_ts TIMESTAMP, result VARCHAR)"
    )
    con.execute(
        "CREATE TABLE kalshi_prices (ticker VARCHAR, ts TIMESTAMP, yes_bid FLOAT, yes_ask FLOAT, "
        "last FLOAT, volume INTEGER, open_interest INTEGER, source VARCHAR)"
    )
    occ = tip + timedelta(minutes=180)
    raw_markets: list[tuple[str, dict[str, str]]] = []
    for m in mks:
        ev = m.event or f"{m.series}-{game}"
        con.execute(
            "INSERT INTO kalshi_markets VALUES (?,?,?,?,?,?,?,NULL,NULL,?,?)",
            [m.ticker, m.series, ev, "t", m.player_id, m.stat, m.threshold, m.settled_ts, m.result],
        )
        ts = m.ts or (tip - timedelta(minutes=70))
        if m.bid is not None or m.ask is not None:
            con.execute(
                "INSERT INTO kalshi_prices VALUES (?,?,?,?,NULL,0,0,'live')",
                [m.ticker, ts, m.bid, m.ask],
            )
        for t2, b2, a2 in m.extra_rows:
            con.execute(
                "INSERT INTO kalshi_prices VALUES (?,?,?,?,NULL,0,0,'live')", [m.ticker, t2, b2, a2]
            )
        rm = {
            "ticker": m.ticker,
            "event_ticker": ev,
            "occurrence_datetime": occ.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "result": m.result or "",
            "expiration_value": "Confirmed DNP" if m.result == "scalar" else "7",
        }
        if m.scalar is not None:
            rm["settlement_value_dollars"] = m.scalar
        raw_markets.append((m.series, rm))
    con.close()
    raw = tmp / "raw"
    day = raw / (tip + timedelta(days=1)).date().isoformat()
    day.mkdir(parents=True)
    for series in {s for s, _ in raw_markets}:
        page = {"cursor": "", "markets": [rm for s, rm in raw_markets if s == series]}
        with gzip.open(day / f"010101_{series}_settled_0.json.gz", "wt") as fh:
            json.dump(page, fh)
    sched = tmp / "live_tips.parquet"
    pl.DataFrame(
        {
            "gameId": ["0022600099", "0022600100"],
            "gameDateTimeUTC": [
                tip.strftime("%Y-%m-%dT%H:%M:%SZ"),
                (tip + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ],
            "gameDateTimeEst": ["x", "y"],
        }
    ).write_parquet(sched)
    repin(sched)
    return {"db": db, "raw": raw, "sched": sched}


def repin(sched: Path) -> None:
    """Pin the fixture schedule for this test (the real pin is exercised by its negative test)."""
    D.SCHEDULE_SHA256 = D.file_sha256(sched)


@pytest.fixture(autouse=True)
def _restore_schedule_pin() -> object:
    orig = D.SCHEDULE_SHA256
    yield
    D.SCHEDULE_SHA256 = orig
