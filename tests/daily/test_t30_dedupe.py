"""Audit F1: duplicate (snapshot, game, team, player) rows must not skip a confirmed game."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

from nba.daily.schedule import ScheduledGame
from nba.daily.t30 import dedupe_snapshot_rows, load_game_lineup
from nba.lineups.source import CONFIRMED, EXPECTED, LineupRow
from nba.lineups.store import connect_lineups
from tests.lineups.helpers import add_snapshot, team_rows

TIP = datetime(2026, 2, 22, 0, 0)
GID, HOME, AWAY = "0022600901", 1, 2
HP = [1000 + i for i in range(8)]
AP = [1100 + i for i in range(8)]


def _rows(extra: list[LineupRow]) -> list[LineupRow]:
    return (
        team_rows(GID, HOME, True, HP[:5], HP[5:])
        + team_rows(GID, AWAY, False, AP[:5], AP[5:])
        + extra
    )


def _load(tmp_path: Path, rows: list[LineupRow]):  # type: ignore[no-untyped-def]
    lcon = connect_lineups(tmp_path / "l.duckdb")
    add_snapshot(lcon, TIP - timedelta(minutes=40), date(2026, 2, 21), rows)
    g = ScheduledGame(GID, TIP, HOME, AWAY)
    return load_game_lineup(lcon, g, TIP - timedelta(minutes=30))


def test_expected_twin_of_a_confirmed_player_does_not_skip_the_game(tmp_path: Path) -> None:
    base = team_rows(GID, HOME, True, HP[:5], HP[5:])[0]  # a confirmed starter ...
    twin = replace(base, lineup_status=EXPECTED)  # ... also listed as Expected
    bench = team_rows(GID, AWAY, False, AP[:5], AP[5:])[6]
    bench_twin = replace(bench, lineup_status=EXPECTED, announced_starter=False)
    gl, _, _ = _load(tmp_path, _rows([twin, bench_twin]))
    assert gl is not None and gl.home.lineup_status == CONFIRMED
    assert gl.away.lineup_status == CONFIRMED
    assert gl.n_dupes_collapsed == 2
    assert len(gl.home.starters) == 5


def test_two_confirmed_rows_conflicting_starter_collapse_to_starter_true(
    tmp_path: Path,
) -> None:
    starter = team_rows(GID, HOME, True, HP[:5], HP[5:])[0]
    gl, _, _ = _load(tmp_path, _rows([replace(starter, announced_starter=False)]))
    assert gl is not None and gl.n_dupes_collapsed == 1
    assert starter.player_id in gl.home.starters and len(gl.home.starters) == 5


def test_unfixable_lineup_still_skips_and_reports_dupes(tmp_path: Path) -> None:
    rows = team_rows(GID, HOME, True, HP[:5], HP[5:], status=EXPECTED) + team_rows(
        GID, AWAY, False, AP[:5], AP[5:]
    )
    gl, reason, _ = _load(tmp_path, rows + [rows[0]])
    assert gl is None and reason == (
        "lineup_not_confirmed:home=Expected,away=Confirmed; dupes_collapsed=1"
    )


def _r(status: str, starter: bool, ts: datetime | None, pid: int = 7) -> tuple:  # type: ignore[type-arg]
    return (1, status, starter, "Active", pid, ts)


def test_dedupe_rule_order() -> None:
    t0, t1 = datetime(2026, 1, 1, 1), datetime(2026, 1, 1, 2)
    # Confirmed beats Expected even when the Expected row is newer
    kept, n = dedupe_snapshot_rows([_r(CONFIRMED, False, t0), _r(EXPECTED, True, t1)])
    assert n == 1 and kept == [_r(CONFIRMED, False, t0)]
    # equal status: latest source_ts wins
    kept, _ = dedupe_snapshot_rows([_r(CONFIRMED, True, t0), _r(CONFIRMED, False, t1)])
    assert kept == [_r(CONFIRMED, False, t1)]
    # tie: announced starter wins, else the first row
    kept, _ = dedupe_snapshot_rows([_r(CONFIRMED, False, t0), _r(CONFIRMED, True, t0)])
    assert kept == [_r(CONFIRMED, True, t0)]
    kept, _ = dedupe_snapshot_rows([_r(CONFIRMED, False, None, 7), _r(CONFIRMED, False, None, 7)])
    assert len(kept) == 1
    # distinct players and teams are untouched
    rows = [_r(CONFIRMED, True, t0, 7), _r(CONFIRMED, True, t0, 8)]
    assert dedupe_snapshot_rows(rows) == (rows, 0)
