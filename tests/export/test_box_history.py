"""Opening-night box history: each slate player's own earlier games ride in ``box_scores``,
strictly before the slate date, capped at a bounded size. Season-2025 box rows are allowed as
display history only (guard.DISPLAY_TABLES, Devin 2026-10-15); no 2025 forecast or score ever is."""

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


def test_only_asked_players_and_2025_is_display_history(src: Path) -> None:
    con = open_read_only(src)
    rows = box_history(con, [STAR], DAY, history_seasons(con))
    assert OTHER not in {r["player_id"] for r in rows}
    assert 2025 in {r["season"] for r in rows}  # 2025 box rows are display history
    assert "0022500001" in {r["game_id"] for r in rows}
    seasons = history_seasons(con)
    assert {2023, 2025, 2026} <= set(seasons)
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
    assert {r[2] for r in rows} == {2023, 2025, 2026}  # 2025 box = display; no 2024 for STAR
    assert len(rows) == len(set((r[0], r[1]) for r in rows))  # one row per game and player
    assert m["row_counts"]["box_scores"] == len(rows)
    assert m["box_history"] == {"games": 82, "days": 365}
    # season-to-date is unchanged: the slate day's own final row stays for settlement
    assert (SLATE, STAR) in {(r[0], r[1]) for r in rows}
    assert "0022500001" in {r[0] for r in rows}  # the 2025 box row enters box_scores history
    assert "0012400001" not in {r[0] for r in rows}  # preseason never
    assert m["box_history_seasons"] == [2023, 2024, 2025, 2026]


def test_holdout_still_refused_for_history() -> None:
    from nba.export import resolve_seasons

    with pytest.raises(ExportRefused):
        resolve_seasons([2026, 2025])


def test_display_allow_list_is_exactly_box_history() -> None:
    from nba.export import guard

    assert set(guard.DISPLAY_TABLES) == {"box_scores_history"}
    assert guard.resolve_seasons([2025], table="box_scores_history") == (2025,)
    for table in (None, "forward_predictions", "forward_scores", "prop_grids", "box_scores"):
        with pytest.raises(ExportRefused):
            guard.resolve_seasons([2025], table=table)
    with pytest.raises(ExportRefused):  # the display exception does not widen anything else
        guard.resolve_seasons([2027], table="box_scores_history")


def test_2025_forecast_and_score_rows_never_enter_the_export(src: Path, tmp_path: Path) -> None:
    from nba.daily import stamps

    hold = "0022500001"
    hold_tip = datetime(2026, 1, 10, 23, 30)
    w = duckdb.connect(str(src))
    w.execute(stamps.DDL)
    pred = '{"mean": 12.0, "std": 4.0, "p_play": 0.9, "proj_minutes": 30.0, "q_grid": []}'
    ins = "INSERT INTO forward_predictions VALUES (?, ?, ?, ?, 'props_context_residual', 'v1', "
    ins += "'pts', ?, ?)"
    w.execute(ins, ["r0", hold_tip - timedelta(hours=2), hold, hold_tip, STAR, pred])
    w.execute(ins, ["r1", TIP - timedelta(hours=2), SLATE, TIP, STAR, pred])
    w.execute(
        "INSERT INTO forward_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [hold_tip, hold, date(2026, 1, 10), 2025, "rung0_mov_elo", "v1", "pts", STAR,
         hold_tip - timedelta(hours=2), 35.0, 0.5, 0.7, 0.2, 1.0, "scored"],
    )  # fmt: skip
    w.close()
    out = tmp_path / "out"
    build_export(
        src,
        out,
        DAY,
        parlay_dir=tmp_path / "none",
        git=lambda: {"git_sha": None, "git_dirty": None},
        version=lambda: None,
        now=lambda: datetime(2026, 10, 20, 12, 0),
    )
    con = duckdb.connect(str(out / DB_NAME), read_only=True)
    tables = [
        r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    ]
    try:
        for t in tables:
            cols = [r[0] for r in con.execute(f"DESCRIBE {t}").fetchall()]  # noqa: S608
            if t == "box_scores" or "game_id" not in cols:
                continue
            n = con.execute(  # noqa: S608
                f"SELECT count(*) FROM {t} WHERE game_id = ?", [hold]
            ).fetchone()
            assert n is not None and n[0] == 0, f"2025 game rows leaked into {t}"
            if "season" in cols:
                m = con.execute(f"SELECT count(*) FROM {t} WHERE season = 2025").fetchone()  # noqa: S608
                assert m is not None and m[0] == 0, f"season 2025 rows in {t}"
        box = con.execute("SELECT count(*) FROM box_scores WHERE season = 2025").fetchone()
        assert box is not None and box[0] == 1  # only the raw box row, as display
    finally:
        con.close()
