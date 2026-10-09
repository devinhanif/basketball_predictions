"""Replay truncation hides results on/after the date; restore brings them back."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb

from nba.daily.rehearsal import restore_results, truncate_for_replay
from tests.daily.conftest import add_history


def _counts(con: duckdb.DuckDBPyConnection) -> tuple[int, int, int]:
    return (
        con.execute("SELECT count(*) FROM games WHERE home_pts IS NOT NULL").fetchone()[0],  # type: ignore[index]
        con.execute("SELECT count(*) FROM games").fetchone()[0],  # type: ignore[index]
        con.execute("SELECT count(*) FROM player_game_stats").fetchone()[0],  # type: ignore[index]
    )


def test_truncate_then_restore(tmp_path: Path) -> None:
    full, work = tmp_path / "full.duckdb", tmp_path / "work.duckdb"
    from tests.fixtures.loader import build_fixture_db

    src = build_fixture_db()
    add_history(src, 6)
    src.execute(f"ATTACH '{full}' AS out")
    src.execute("CREATE TABLE out.games AS SELECT * FROM games")
    src.execute("CREATE TABLE out.player_game_stats AS SELECT * FROM player_game_stats")
    src.execute("DETACH out")
    work.write_bytes(full.read_bytes())
    con = duckdb.connect(str(work))
    played0, n0, stats0 = _counts(con)
    cut = date(2026, 1, 7)  # games at days 0,2,4,6,... -> dates >= Jan 7 hidden
    removed = truncate_for_replay(con, cut)
    played1, n1, stats1 = _counts(con)
    assert n1 == n0 and played1 < played0 and stats1 < stats0 and removed["games"] > 0
    assert (
        con.execute("SELECT max(game_date) FROM games WHERE home_pts IS NOT NULL").fetchone()[0]
        < cut
    )  # type: ignore[index]
    restore_results(con, str(full), date(2026, 1, 8), cut)
    played2, _, stats2 = _counts(con)
    assert played1 < played2 <= played0 and stats1 < stats2 <= stats0
    restore_results(con, str(full), date(2026, 1, 8), cut)  # idempotent
    assert _counts(con)[2] == stats2
