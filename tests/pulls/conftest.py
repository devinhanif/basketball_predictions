# ruff: noqa: E501
"""Fixtures for the pull-orchestrator tests: tmp stores, a fake vendor, no network, no real data dir."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.odds import live_close as lc
from nba.odds.the_odds_api import HistoryConfig
from nba.pulls.config import PullsConfig
from nba.pulls.orchestrator import tick
from nba.pulls.store import PullStore
from tests.odds.test_live_close import ENV_ON, NYK, PHI, RES, SLATE, TIP, FakeHttp

G1 = "G1"
GAME = lc.LiveGame(G1, TIP, PHI, NYK)


@pytest.fixture
def lcfg(tmp_path: Path) -> lc.LiveConfig:
    hist = HistoryConfig(min_interval_s=0.0, retry_backoff_s=0.0, ledger_path=tmp_path / "h.json")
    return lc.LiveConfig(
        hist=hist,
        out_db=tmp_path / "market_asof.duckdb",
        tips_db=tmp_path / "lineups.duckdb",
        games_db=tmp_path / "nba_missing.duckdb",
        raw_dir=tmp_path / "raw",
        ledger_dir=tmp_path / "budget",
        schedule=(lc.ScheduleEntry("close", 0, 10), lc.ScheduleEntry("t60", 55, 65)),
    )


@pytest.fixture
def pcfg(tmp_path: Path) -> PullsConfig:
    return PullsConfig(requests_db=tmp_path / "ops" / "pulls.duckdb", store_wait_s=0.0)


@pytest.fixture
def store(pcfg: PullsConfig) -> PullStore:
    return PullStore(pcfg.requests_db, wait_s=0.0)


def at(minutes_before_tip: float) -> datetime:
    return (TIP - timedelta(minutes=minutes_before_tip)).replace(tzinfo=UTC)


def file_request(
    store: PullStore,
    now: datetime,
    game_id: str = G1,
    *,
    need_by_min: float = 15.0,
    max_age_min: float = 30.0,
) -> str:
    res = store.request(
        source="pinnacle", scope_game_id=game_id, slate_date=SLATE,
        need_by_utc=(now + timedelta(minutes=need_by_min)).replace(tzinfo=None),
        max_age_min=max_age_min, requester="jarvis", reason="test", now=now,
    )  # fmt: skip
    return res.request.request_id


def run_tick(
    now: datetime,
    pcfg: PullsConfig,
    lcfg: lc.LiveConfig,
    store: PullStore,
    http: FakeHttp | None = None,
    games: list[lc.LiveGame] | None = None,
    environ: dict[str, str] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Any:
    return tick(
        now, cfg=pcfg, live_cfg=lcfg, store=store, environ=ENV_ON if environ is None else environ,
        games_fn=lambda _d: games if games is not None else [GAME], resolver=RES,
        sleep=lambda _s: None, http_get=http or FakeHttp(), clock=clock or (lambda: now),
    )  # fmt: skip


def market_rows(lcfg: lc.LiveConfig, sql: str) -> list[tuple[Any, ...]]:
    con = duckdb.connect(str(lcfg.out_db), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()
