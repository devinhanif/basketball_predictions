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


def add_final_box(con: duckdb.DuckDBPyConnection, game_id: str, n: int = 10) -> None:
    """``n`` played player_game_stats rows for ``game_id``, alternating home / away, whose team
    point sums equal the game score: settle grades a target only when the box agrees with the
    game (>= 10 played rows for a winner; summed points equal home_pts / away_pts)."""
    g = con.execute(
        "SELECT home_team, away_team, home_pts, away_pts FROM games WHERE game_id = ?", [game_id]
    ).fetchone()
    assert g is not None, f"insert the games row for {game_id} first"
    teams, pts = (g[0], g[1]), [int(g[2]), int(g[3])]
    per = [(n + 1) // 2, n // 2]  # rows per team: home gets the odd one
    for k in range(n):
        side = k % 2
        share = pts[side] // per[side] + (pts[side] % per[side] if k // 2 == 0 else 0)
        con.execute(
            "INSERT INTO player_game_stats "
            "(game_id, player_id, team_id, minutes, pts, reb, ast, fg3m) "
            "VALUES (?, ?, ?, 20, ?, 2, 1, 0)",
            [game_id, 70000 + k, teams[side], share],
        )


def align_box_to_score(con: duckdb.DuckDBPyConnection, game_id: str) -> None:
    """Make the box agree with the game score (settle's M2 gate) without touching a played row:
    both scores rise by the same amount if a team's played rows already sum above its score (the
    margin, so the winner, is kept), then each team gets one minutes = 0 filler row for the rest."""
    g = con.execute(
        "SELECT home_team, away_team, home_pts, away_pts FROM games WHERE game_id = ?", [game_id]
    ).fetchone()
    assert g is not None
    con.execute(
        "DELETE FROM player_game_stats WHERE game_id = ? AND player_id >= 990000", [game_id]
    )
    sums = []
    for team in (g[0], g[1]):
        row = con.execute(
            "SELECT coalesce(sum(pts), 0) FROM player_game_stats WHERE game_id = ? AND team_id = ?",
            [game_id, team],
        ).fetchone()
        assert row is not None
        sums.append(int(row[0]))
    bump = max(0, sums[0] - g[2], sums[1] - g[3])
    scores = [g[2] + bump, g[3] + bump]
    con.execute("UPDATE games SET home_pts = ?, away_pts = ? WHERE game_id = ?", [*scores, game_id])
    for k, team in enumerate((g[0], g[1])):
        con.execute(
            "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, reb, ast,"
            " fg3m) VALUES (?, ?, ?, 0, ?, 0, 0, 0)",
            [game_id, 990000 + k, team, scores[k] - sums[k]],
        )


def align_all(con: duckdb.DuckDBPyConnection) -> None:
    """``align_box_to_score`` for every game that has box rows (fixtures that build their own)."""
    for (gid,) in con.execute(
        "SELECT DISTINCT game_id FROM player_game_stats "
        "WHERE game_id IN (SELECT game_id FROM games)"
    ).fetchall():
        align_box_to_score(con, gid)


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


@pytest.fixture(autouse=True)
def _no_player_info_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Daily tests must never reach stats.nba.com for the rookie pedigree auto-fill."""

    def blocked(pid: int) -> pl.DataFrame:
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr("nba.ingest.players_static._fetch_player_info", blocked)
    monkeypatch.setattr("nba.ingest.players_static.time.sleep", lambda s: None)
