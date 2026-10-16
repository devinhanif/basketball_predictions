"""Re-stamp migration: stored report stamps move from the file-name time to the printed header
time, on a copy only; a report published after tip-60 no longer feeds the OUT set."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import duckdb
import polars as pl
import pytest

from nba.data.restamp_reports import apply_to_copy, audit, build_plan, change_table
from nba.db.connect import connect
from nba.features.injury_report import ReportTriggerConfig, latest_pretip_flagged

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "injury_report"
HOUR_ONLY = "Injury-Report_2019-10-21_05PM.pdf"  # named 17:00, prints 17:30
MINUTES = "Injury-Report_2026-10-07_09_45AM.pdf"  # named 09:45, prints 09:50


@pytest.fixture
def world(tmp_path: Path) -> tuple[Path, Path]:
    raw = tmp_path / "raw"
    raw.mkdir()
    for n in (HOUR_ONLY, MINUTES):
        shutil.copy(FIX / n, raw / n)
    src = tmp_path / "prod.duckdb"
    con = connect(str(src))
    try:
        con.execute(
            "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, "
            "away_pts) VALUES ('g1', '2019-10-21', 2019, 1, 2, 0, 0)"
        )
        for stamp, pid in (("2019-10-21 17:00:00", 11), ("2026-10-07 09:45:00", 22)):
            con.execute(
                "INSERT INTO player_availability VALUES (?, ?, 'g1', 'out', NULL, "
                "'nba_official_report', ?)",
                [pid, stamp, stamp],
            )
    finally:
        con.close()
    return raw, src


def test_audit_flags_the_filename_stamps(world: tuple[Path, Path]) -> None:
    raw, src = world
    con = duckdb.connect(str(src), read_only=True)
    try:
        a = audit(con, build_plan(raw))
    finally:
        con.close()
    assert a["stale_filename_stamps"] == 2
    assert a["stamps_equal_to_header"] == 0


def test_apply_restamps_the_copy_and_never_the_source(
    world: tuple[Path, Path], tmp_path: Path
) -> None:
    raw, src = world
    dst = tmp_path / "rehearsal" / "stampfix.duckdb"
    res = apply_to_copy(src, dst, build_plan(raw))
    assert res["rows_updated"] == 2
    assert res["after"]["stamps_not_equal_to_header"] == 0
    c = duckdb.connect(str(dst), read_only=True)
    s = duckdb.connect(str(src), read_only=True)
    try:
        got = c.execute("SELECT player_id, as_of, pulled_at FROM player_availability ORDER BY 1")
        assert got.fetchall() == [
            (11, datetime(2019, 10, 21, 17, 30), datetime(2019, 10, 21, 17, 30)),
            (22, datetime(2026, 10, 7, 9, 50), datetime(2026, 10, 7, 9, 50)),
        ]
        stored = s.execute("SELECT as_of FROM player_availability ORDER BY player_id").fetchall()
        assert stored == [(datetime(2019, 10, 21, 17, 0),), (datetime(2026, 10, 7, 9, 45),)]
        t = change_table(s, build_plan(raw))
        assert t["rows_changed"].sum() == 2
    finally:
        c.close()
        s.close()


def test_refuses_to_write_the_source_or_any_nba_duckdb(
    world: tuple[Path, Path], tmp_path: Path
) -> None:
    raw, src = world
    with pytest.raises(ValueError):
        apply_to_copy(src, src, build_plan(raw))
    with pytest.raises(ValueError):
        apply_to_copy(src, tmp_path / "x" / "nba.duckdb", build_plan(raw))


def test_planted_future_report_leaves_the_t60_snapshot_after_restamp(
    world: tuple[Path, Path], tmp_path: Path
) -> None:
    """Tip 18:15 -> cutoff 17:15. The report named 17:00 prints 17:30: before the fix it was
    admitted (stamp 17:00 <= 17:15); after, it is not (stamp 17:30 > 17:15)."""
    raw, src = world
    dst = tmp_path / "copy.duckdb"
    apply_to_copy(src, dst, build_plan(raw))
    cfg = ReportTriggerConfig(tip_source="real", lead_minutes=60)

    def flagged(db: Path) -> dict[str, set[int]]:
        c = duckdb.connect(str(db), read_only=True)
        try:
            r = c.execute(
                "SELECT game_id, player_id, status, as_of FROM player_availability WHERE "
                "game_id = 'g1' AND player_id = 11"
            ).fetchall()
        finally:
            c.close()
        df = pl.DataFrame(
            r,
            schema={
                "game_id": pl.Utf8,
                "player_id": pl.Int64,
                "status": pl.Utf8,
                "as_of": pl.Datetime("us"),
            },
            orient="row",
        ).with_columns(
            pl.lit(datetime(2019, 10, 21)).cast(pl.Date).alias("game_date"),
            pl.lit(datetime(2019, 10, 21, 18, 15)).cast(pl.Datetime("us")).alias("tip_et"),
        )
        return latest_pretip_flagged(df, cfg)[0]

    assert flagged(src) == {"g1": {11}}  # the leak
    assert flagged(dst) == {}  # report not yet published at tip-60
