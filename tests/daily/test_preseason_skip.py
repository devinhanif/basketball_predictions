"""Preseason (001) and all-star (003) games never enter a daily slate (Devin, 2026-10-11)."""

from __future__ import annotations

from datetime import timedelta

import duckdb
import pytest

from nba.daily.__main__ import _print_summary
from nba.daily.pipeline import run_daily
from nba.daily.schedule import (
    ScheduledGame,
    is_scorable_game_id,
    n_unscorable_on_date,
    slate_for_date,
)
from tests.daily.conftest import RUN_DATE, T1, T2, T3, T4, TIPOFF, before_tip, slate_games

PRE = [
    ScheduledGame("0012600011", TIPOFF, T1, T2),
    ScheduledGame("0012600012", TIPOFF + timedelta(hours=3), T3, T4),
]


def _run(con: duckdb.DuckDBPyConnection, games: list[ScheduledGame]):  # type: ignore[no-untyped-def]
    return run_daily(
        con, RUN_DATE, schedule_fn=lambda _s: games, now=before_tip(), skip_ingest=True,
        skip_injury=True, with_props=False,
    )  # fmt: skip


def _n_rows(con: duckdb.DuckDBPyConnection, prefix: str) -> int:
    r = con.execute(
        "SELECT count(*) FROM forward_predictions WHERE substr(game_id,1,3) = ?", [prefix]
    ).fetchone()
    assert r is not None
    return int(r[0])


def test_game_id_prefixes() -> None:
    assert not is_scorable_game_id("0012600011") and not is_scorable_game_id("0032600001")
    assert all(is_scorable_game_id(g) for g in ("0022600001", "0042500101", "0052500001"))


def test_slate_preseason_only_is_empty() -> None:
    assert slate_for_date(PRE, RUN_DATE) == []
    assert n_unscorable_on_date(PRE, RUN_DATE) == 2


def test_slate_mixed_keeps_regular_only() -> None:
    mixed = PRE + slate_games()
    got = slate_for_date(mixed, RUN_DATE)
    assert [g.game_id for g in got] == ["0022600001", "0022600002"]
    assert n_unscorable_on_date(mixed, RUN_DATE) == 2


def test_slate_regular_unchanged() -> None:
    games = slate_games()
    assert slate_for_date(games, RUN_DATE) == sorted(games[:2], key=lambda g: (g.tipoff, g.game_id))
    assert n_unscorable_on_date(games, RUN_DATE) == 0


def test_run_preseason_only_records_line_and_writes_nothing(
    con: duckdb.DuckDBPyConnection, capsys: pytest.CaptureFixture[str]
) -> None:
    s = _run(con, PRE)
    assert (s.n_slate, s.n_preseason_skipped, s.n_rows_written, s.errors) == (0, 2, 0, [])
    _print_summary(s)
    assert "no regular-season games: 2 preseason skipped" in capsys.readouterr().out
    assert _n_rows(con, "001") == 0


def test_run_mixed_predicts_regular_only(con: duckdb.DuckDBPyConnection) -> None:
    s = _run(con, PRE + slate_games())
    assert (s.n_slate, s.n_predicted_games, s.n_preseason_skipped) == (2, 2, 2)
    assert _n_rows(con, "001") == 0 and _n_rows(con, "002") > 0


def test_run_regular_unchanged(
    con: duckdb.DuckDBPyConnection, capsys: pytest.CaptureFixture[str]
) -> None:
    s = _run(con, slate_games())
    assert (s.n_slate, s.n_predicted_games, s.n_preseason_skipped) == (2, 2, 0)
    _print_summary(s)
    assert "no regular-season games" not in capsys.readouterr().out
