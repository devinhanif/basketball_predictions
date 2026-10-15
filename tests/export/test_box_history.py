"""Opening-night box history: each slate player's own earlier games ride in ``box_scores``,
strictly before the slate date, never from the holdout season, capped at a bounded size."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from nba.export import ExportRefused
from nba.export.build import DB_NAME, build_export
from nba.export.reader import box_history, history_seasons, open_read_only
from tests.fixtures.loader import build_fixture_db

DAY = date(2026, 10, 20)
TIP = datetime(2026, 10, 20, 23, 30)
SLATE = "0022600001"
STAR, BENCH, OTHER = 9001, 9002, 9003
TEAM = 1610612738


def _game(
    con: duckdb.DuckDBPyConnection, gid: str, d: date, season: int, final: bool = True
) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) "
        "VALUES (?,?,?,?,?,?,?)",
        [gid, d, season, TEAM, 1610612755, 100 if final else None, 90 if final else None],
    )


def _box(con: duckdb.DuckDBPyConnection, gid: str, pid: int, minutes: float | None) -> None:
    con.execute(
        "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, reb, ast, "
        "fg3m, starter, stl, blk, tov) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [gid, pid, TEAM, minutes, 10 if minutes else 0, 1, 1, 1, True, 0, 0, 0],
    )


@pytest.fixture
def src(tmp_path: Path) -> Path:
    path = tmp_path / "nba.duckdb"
    con = build_fixture_db(path)
    # STAR: 100 played games in 2023 (far outside 365 days), 5 in 2024, 3 DNP + 4 played in 2024
    for i in range(100):
        gid = f"00223{50000 + i:05d}"
        _game(con, gid, date(2023, 11, 1) + timedelta(days=i), 2023)
        _box(con, gid, STAR, 30.0)
        _box(con, gid, OTHER, 20.0)
    # BENCH: one played game in 2024; a DNP older than 365 days (dropped), a DNP inside (kept)
    _game(con, "0022400001", date(2025, 2, 1), 2024)
    _box(con, "0022400001", BENCH, 12.0)
    _game(con, "0022400002", DAY - timedelta(days=400), 2024)
    _box(con, "0022400002", BENCH, None)
    _game(con, "0022400003", DAY - timedelta(days=300), 2024)
    _box(con, "0022400003", BENCH, None)
    # the holdout season, the slate day itself, a future game, a non-final game, preseason
    _game(con, "0022500001", date(2026, 1, 10), 2025)
    _box(con, "0022500001", STAR, 35.0)
    _game(con, "0022600000", date(2026, 10, 19), 2026)  # live season, yesterday
    _box(con, "0022600000", STAR, 28.0)
    _game(con, SLATE, DAY, 2026, final=True)  # slate day: not history (post-game row)
    _box(con, SLATE, STAR, 40.0)
    _game(con, "0022600009", DAY + timedelta(days=1), 2026, final=False)
    _box(con, "0022600009", STAR, 1.0)
    _game(con, "0012400001", date(2025, 10, 5), 2024)
    _box(con, "0012400001", STAR, 9.0)
    con.close()
    return path


def _ids(rows: list[dict[str, object]]) -> set[tuple[object, object]]:
    return {(r["game_id"], r["player_id"]) for r in rows}


def test_last_82_games_strictly_before_the_slate(src: Path) -> None:
    con = open_read_only(src)
    rows = box_history(con, [STAR], DAY, (2023, 2024, 2026))
    con.close()
    star = [r for r in rows if r["player_id"] == STAR]
    played = [r for r in star if r["minutes"] is not None]
    assert len(played) == 82 + 0  # 100 old games capped at 82 (2023 rows; yesterday inside)
    assert all(r["game_date"] < DAY for r in star)
    assert "0022600000" in {r["game_id"] for r in star}  # season to date, yesterday
    assert SLATE not in {r["game_id"] for r in rows}  # the slate day itself
    assert "0022600009" not in {r["game_id"] for r in rows}  # future / not final
    assert "0012400001" not in {r["game_id"] for r in rows}  # preseason
    newest = max(r["game_date"] for r in star)
    assert newest == date(2026, 10, 19)
    oldest_kept = min(r["game_date"] for r in star)
    assert oldest_kept == date(2023, 11, 1) + timedelta(days=100 - 81)  # newest 82 of 100, +1 day


def test_days_window_keeps_recent_dnp_and_drops_old_dnp(src: Path) -> None:
    con = open_read_only(src)
    rows = box_history(con, [BENCH], DAY, (2024, 2026))
    con.close()
    assert {r["game_id"] for r in rows} == {"0022400001", "0022400003"}  # 400-day DNP dropped
    assert any(r["minutes"] is None for r in rows)  # the DNP stays explicit


def test_only_asked_players_and_never_the_holdout(src: Path) -> None:
    con = open_read_only(src)
    rows = box_history(con, [STAR], DAY, history_seasons(con))
    assert OTHER not in {r["player_id"] for r in rows}
    assert 2025 not in {r["season"] for r in rows}
    assert "0022500001" not in {r["game_id"] for r in rows}
    seasons = history_seasons(con)
    assert 2025 not in seasons and 2026 in seasons and 2023 in seasons
    assert history_seasons(con, (2023,)) == (2023,)  # explicit old-season export stays itself
    con.close()
    assert box_history(open_read_only(src), [], DAY, (2026,)) == []


def test_export_carries_history_for_slate_players_only(src: Path, tmp_path: Path) -> None:
    w = duckdb.connect(str(src))
    from nba.daily import stamps

    w.execute(stamps.DDL)
    pred = '{"mean": 12.0, "std": 4.0, "p_play": 0.9, "proj_minutes": 30.0, "q_grid": []}'
    w.execute(
        "INSERT INTO forward_predictions VALUES ('r1', ?, ?, ?, 'props_context_residual', 'v1', "
        "'pts', ?, ?)",
        [TIP - timedelta(hours=2), SLATE, TIP, STAR, pred],
    )
    w.close()
    out = tmp_path / "out"
    m = build_export(
        src,
        out,
        DAY,
        parlay_dir=tmp_path / "none",
        git=lambda: {"git_sha": None, "git_dirty": None},
        version=lambda: None,
        now=lambda: datetime(2026, 10, 20, 12, 0),
    )
    con = duckdb.connect(str(out / DB_NAME), read_only=True)
    rows = con.execute("SELECT game_id, player_id, season, game_date FROM box_scores").fetchall()
    con.close()
    assert {r[1] for r in rows} == {STAR}  # OTHER / BENCH are not on the slate
    assert {r[2] for r in rows} == {2023, 2026}  # no holdout, no 2024 (not asked for this player)
    assert len(rows) == len(set((r[0], r[1]) for r in rows))  # one row per game and player
    assert m["row_counts"]["box_scores"] == len(rows)
    assert m["box_history"] == {"games": 82, "days": 365}
    # season-to-date is unchanged: the slate day's own final row stays for settlement
    assert (SLATE, STAR) in {(r[0], r[1]) for r in rows}
    assert not any(r[0] in {"0022500001", "0012400001"} for r in rows)


def test_holdout_still_refused_for_history() -> None:
    from nba.export import resolve_seasons

    with pytest.raises(ExportRefused):
        resolve_seasons([2026, 2025])
