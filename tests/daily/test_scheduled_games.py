"""Today's scheduled games are written into `games` with NULL scores (2026-10-21).

The ingest feed returns only started or finished games, so before this step the coverage
`schedule` check failed every game day and the morning job depended on the finder's row."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import duckdb
import polars as pl
import pytest

from nba.daily.__main__ import run_ingest
from nba.daily.ingest_step import insert_scheduled_games
from nba.daily.pipeline import run_daily
from nba.daily.schedule import ScheduledGame
from nba.daily.settle import settle_pending
from nba.ingest.cache import RateLimiter
from nba.ops.coverage import OK, Game, check_schedule
from nba.ops.exitcodes import RC_DEGRADED
from tests.daily.conftest import RUN_DATE, T1, T2, T3, T4, TIPOFF, before_tip, slate_games

EMPTY = pl.DataFrame(
    schema={
        "game_id": pl.Utf8, "game_date": pl.Date, "season": pl.Int64, "home_team": pl.Int64,
        "away_team": pl.Int64, "home_pts": pl.Int64, "away_pts": pl.Int64,
    }
)  # fmt: skip


def _row(con: duckdb.DuckDBPyConnection, gid: str) -> tuple[object, ...] | None:
    return con.execute(
        "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
        "FROM games WHERE game_id = ?",
        [gid],
    ).fetchone()


def _run(con: duckdb.DuckDBPyConnection, games: list[ScheduledGame]):  # type: ignore[no-untyped-def]
    return run_daily(
        con, RUN_DATE, schedule_fn=lambda _s: games, now=before_tip(), skip_injury=True,
        with_props=False, fetch_games=lambda _s: EMPTY, pull_box=lambda c, g: None,
    )  # fmt: skip


def test_run_daily_inserts_todays_slate_with_null_scores(con: duckdb.DuckDBPyConnection) -> None:
    assert _row(con, "0022600001") is None
    s = _run(con, slate_games())
    assert s.errors == [] and s.ingest["scheduled_rows_inserted"] == 2
    assert _row(con, "0022600001") == ("0022600001", RUN_DATE, 2026, T1, T2, None, None)
    assert _row(con, "0022600002") == ("0022600002", RUN_DATE, 2026, T3, T4, None, None)
    assert _row(con, "0022600003") is None  # tomorrow's game is not today's slate
    s2 = _run(con, slate_games())  # idempotent
    assert s2.ingest["scheduled_rows_inserted"] == 0


def test_existing_finder_row_is_never_modified(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts)"
        " VALUES ('0022600001', ?, 2026, ?, ?, 101, 99)",
        [RUN_DATE, T1, T2],
    )
    s = _run(con, slate_games())
    assert s.ingest["scheduled_rows_inserted"] == 1
    assert _row(con, "0022600001") == ("0022600001", RUN_DATE, 2026, T1, T2, 101, 99)


def test_preseason_id_is_not_inserted(con: duckdb.DuckDBPyConnection) -> None:
    pre = [ScheduledGame("0012600011", TIPOFF, T1, T2), *slate_games()]
    _run(con, pre)
    assert _row(con, "0012600011") is None and _row(con, "0022600001") is not None


def test_skip_ingest_inserts_nothing(con: duckdb.DuckDBPyConnection) -> None:
    s = run_daily(
        con, RUN_DATE, schedule_fn=lambda _s: slate_games(), now=before_tip(), skip_ingest=True,
        skip_injury=True, with_props=False,
    )  # fmt: skip
    assert "scheduled_rows_inserted" not in s.ingest
    assert _row(con, "0022600001") is None


def test_settle_never_scores_a_null_score_row(con: duckdb.DuckDBPyConnection) -> None:
    _run(con, slate_games())
    n_before = con.execute("SELECT count(*) FROM forward_scores").fetchone()
    settle_pending(con, TIPOFF + timedelta(days=2))
    assert con.execute("SELECT count(*) FROM forward_scores").fetchone() == n_before
    assert con.execute(
        "SELECT count(*) FROM forward_scores WHERE game_id IN ('0022600001', '0022600002')"
    ).fetchone() == (0,)


def test_coverage_schedule_check_passes_after_the_insert(con: duckdb.DuckDBPyConnection) -> None:
    sched = slate_games()[:2]
    games = [Game(g.game_id, g.tipoff, g.home_team, g.away_team, True) for g in sched]
    assert check_schedule(con, games)[0].status != OK  # before: rows missing
    insert_scheduled_games(con, sched, RUN_DATE, 2026)
    assert check_schedule(con, games)[0].status == OK


def test_insert_uses_the_et_date_of_the_tip(con: duckdb.DuckDBPyConnection) -> None:
    late = ScheduledGame("0022600009", datetime(2026, 10, 29, 2, 0), T1, T2)  # 22:00 ET on 10-28
    assert insert_scheduled_games(con, [late], date(2026, 10, 28), 2026) == 1
    assert _row(con, "0022600009") == ("0022600009", date(2026, 10, 28), 2026, T1, T2, None, None)


def test_ingest_subcommand_inserts_todays_slate(con: duckdb.DuckDBPyConnection) -> None:
    rc = run_ingest(
        con, RUN_DATE, rate_limiter=RateLimiter(0.0), fetch_games=lambda _s: EMPTY,
        pull_box=lambda c, g: None, schedule_fn=lambda _s: slate_games(),
    )  # fmt: skip
    assert rc == 0
    assert _row(con, "0022600001") == ("0022600001", RUN_DATE, 2026, T1, T2, None, None)


def test_ingest_subcommand_degrades_when_schedule_is_unavailable(
    con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    monkeypatch.setattr("nba.daily.__main__.SCHEDULE_CACHE_DIR", tmp_path)

    def down(_s: str) -> list[ScheduledGame]:
        raise RuntimeError("blocked")

    rc = run_ingest(
        con, RUN_DATE, rate_limiter=RateLimiter(0.0), fetch_games=lambda _s: EMPTY,
        pull_box=lambda c, g: None, schedule_fn=down,
    )  # fmt: skip
    assert rc == RC_DEGRADED and _row(con, "0022600001") is None


def _null_row(con: duckdb.DuckDBPyConnection, gid: str, d: date) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team) "
        "VALUES (?, ?, 2026, ?, ?)",
        [gid, d, T1, T2],
    )


def test_stale_null_row_removed_after_a_successful_refresh(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _null_row(con, "0022600500", RUN_DATE - timedelta(days=1))  # postponed yesterday
    _null_row(con, "0022600501", RUN_DATE)  # today's own placeholder is kept
    s = _run(con, slate_games())
    assert s.ingest["stale_scheduled_removed"] == 1
    assert _row(con, "0022600500") is None and _row(con, "0022600501") is not None
    scored = con.execute("SELECT count(*) FROM games WHERE home_pts > 0").fetchone()
    assert scored is not None and scored[0] > 0  # scored history untouched


def test_stale_null_row_kept_when_the_refresh_fails(con: duckdb.DuckDBPyConnection) -> None:
    _null_row(con, "0022600500", RUN_DATE - timedelta(days=1))

    def down(_s: str) -> pl.DataFrame:
        raise RuntimeError("blocked")

    s = run_daily(
        con, RUN_DATE, schedule_fn=lambda _s: slate_games(), now=before_tip(),
        skip_injury=True, with_props=False, fetch_games=down,
    )  # fmt: skip
    assert any(e.startswith("ingest: failed") for e in s.errors)
    assert _row(con, "0022600500") is not None


def test_ingest_subcommand_writes_the_slate_even_when_the_finder_fails(
    con: duckdb.DuckDBPyConnection,
) -> None:
    def down(_s: str) -> pl.DataFrame:
        raise RuntimeError("blocked")

    rc = run_ingest(
        con, RUN_DATE, rate_limiter=RateLimiter(0.0), fetch_games=down,
        schedule_fn=lambda _s: slate_games(),
    )  # fmt: skip
    assert rc == RC_DEGRADED and _row(con, "0022600001") is not None


def test_inserted_count_is_actual_inserts(con: duckdb.DuckDBPyConnection) -> None:
    assert insert_scheduled_games(con, slate_games()[:2], RUN_DATE, 2026) == 2
    assert insert_scheduled_games(con, slate_games()[:2], RUN_DATE, 2026) == 0
