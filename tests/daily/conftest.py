"""Fixture helpers: fixture DB + a synthetic 2025-26 history and 2026-27 slate."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import duckdb
import polars as pl
import pytest

from nba.daily.schedule import ScheduledGame
from nba.ingest.cache import insert_rows
from tests.fixtures.loader import build_fixture_db

T1, T2, T3, T4 = 1610612738, 1610612755, 1610612747, 1610612744
RUN_DATE = date(2026, 10, 28)
TIPOFF = datetime(2026, 10, 28, 23, 30)  # naive UTC == 7:30pm ET
STAT_COLS = [
    "game_id", "player_id", "team_id", "minutes", "pts", "reb", "ast", "fg3m",
    "stl", "blk", "tov", "starter", "fgm", "fga", "fg3a", "ftm", "fta", "oreb", "dreb", "pf",
]  # fmt: skip


def _box(gid: str, team: int, base: int, pts: int) -> list[dict[str, object]]:
    return [
        {
            "game_id": gid, "player_id": base + i, "team_id": team,
            "minutes": 30.0 if i < 5 else 0.0, "pts": pts + i, "reb": 4 + i, "ast": 3,
            "fg3m": 1, "stl": 0, "blk": 0, "tov": 1, "starter": i < 3, "fgm": 3, "fga": 8,
            "fg3a": 3, "ftm": 1, "fta": 2, "oreb": 1, "dreb": 3, "pf": 2,
        }
        for i in range(6)
    ]  # fmt: skip


def add_history(con: duckdb.DuckDBPyConnection, n: int = 12) -> None:
    """n completed 2025-26 games T1(home) vs T2 and T3 vs T4, one every 2 days."""
    games, stats = [], []
    for i in range(n):
        d = date(2026, 1, 1) + timedelta(days=2 * i)
        for k, (h, a, hb) in enumerate([(T1, T2, 1000), (T3, T4, 3000)]):
            gid = f"00225{k}{i:04d}"
            games.append(
                {"game_id": gid, "game_date": d, "season": 2025, "home_team": h,
                 "away_team": a, "home_pts": 110 + i, "away_pts": 100}
            )  # fmt: skip
            stats += _box(gid, h, hb, 10) + _box(gid, a, hb + 100, 8)
    g = pl.DataFrame(games)
    insert_rows(con, "games", g.columns, g)
    s = pl.DataFrame(stats)
    insert_rows(con, "player_game_stats", STAT_COLS, s.select(STAT_COLS))


def slate_games() -> list[ScheduledGame]:
    return [
        ScheduledGame("0022600001", TIPOFF, T1, T2),
        ScheduledGame("0022600002", TIPOFF + timedelta(hours=3), T3, T4),
        ScheduledGame("0022600003", TIPOFF + timedelta(days=1), T1, T3),  # other day
    ]


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = build_fixture_db(":memory:")
    add_history(c)
    return c


def before_tip(minutes: int = 120) -> datetime:
    return TIPOFF - timedelta(minutes=minutes)


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _isolated_model_cache(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never write fitted models into the repo's data/ directory from tests."""
    from nba.daily import predict

    monkeypatch.setattr(predict, "DEFAULT_CONTEXT_CACHE", tmp_path_factory.mktemp("ctxcache"))
