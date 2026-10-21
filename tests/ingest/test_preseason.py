"""Preseason ingest on a tiny in-memory fixture (no network)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import polars as pl
import pytest

from nba.ingest import preseason as ps
from nba.ingest.cache import RateLimiter

HOME, AWAY, FOREIGN = 1610612737, 1610612738, 15020


def _games_raw() -> pl.DataFrame:
    rows = []
    for gid, h, a in [("0012200001", HOME, AWAY), ("0012200002", HOME, FOREIGN)]:
        rows.append(("ATL", "ATL vs. XXX", gid, h, 100, "2022-10-02"))
        rows.append(("XXX", "XXX @ ATL", gid, a, 90, "2022-10-02"))
    rows.append(("ATL", "ATL vs. BOS", "0022200003", HOME, 99, "2022-10-20"))  # regular season
    return pl.DataFrame(
        rows,
        schema=["TEAM_ABBREVIATION", "MATCHUP", "GAME_ID", "TEAM_ID", "PTS", "GAME_DATE"],
        orient="row",
    )


def _box_raw(minutes: list[str]) -> pl.DataFrame:
    n = len(minutes)
    z = [0] * n
    return pl.DataFrame(
        {
            "personId": list(range(1, n + 1)),
            "teamId": [HOME] * n,
            "position": ["G"] + [""] * (n - 1),
            "minutes": minutes,
            "points": [10] * n,
            "reboundsTotal": z,
            "assists": z,
            "threePointersMade": z,
            "steals": z,
            "blocks": z,
            "turnovers": z,
            "fieldGoalsMade": z,
            "fieldGoalsAttempted": [5] * n,
            "threePointersAttempted": z,
            "freeThrowsMade": z,
            "freeThrowsAttempted": [2] * n,
            "reboundsOffensive": z,
            "reboundsDefensive": z,
            "foulsPersonal": z,
        }
    )


def test_season_string() -> None:
    assert ps.season_string(2025) == "2025-26"
    assert ps.season_string(2099) == "2099-00"


def test_normalize_keeps_preseason_nba_only() -> None:
    g = ps.normalize_games(_games_raw(), 2022)
    assert g["game_id"].to_list() == ["0012200001"]
    assert g["home_team"].to_list() == [HOME] and g["away_pts"].to_list() == [90]
    assert g["game_date"].to_list() == [dt.date(2022, 10, 2)]


def test_write_once_never_overwrites_or_writes_empty(tmp_path: Path) -> None:
    p = tmp_path / "a.parquet"
    assert not ps.write_once(pl.DataFrame(), p) and not p.exists()
    assert ps.write_once(pl.DataFrame({"x": [1]}), p)
    assert not ps.write_once(pl.DataFrame({"x": [2]}), p)
    assert pl.read_parquet(p)["x"].to_list() == [1]


def test_fetch_resumes_and_does_not_cache_empty(tmp_path: Path) -> None:
    calls: list[str] = []

    def box(gid: str) -> pl.DataFrame:
        calls.append(gid)
        return pl.DataFrame()  # throttled / empty

    s1 = ps.fetch_season(
        tmp_path,
        2022,
        limiter=RateLimiter(0),
        games_fetch=lambda s: _games_raw(),
        box_fetch=box,
        sleep=lambda _: None,
    )
    assert s1["failed"] == ["0012200001"] and not ps.box_path(tmp_path, "0012200001").exists()
    assert s1["games_dropped_non_nba_side"] == 1
    assert len(calls) == ps._MAX_ATTEMPTS

    s2 = ps.fetch_season(
        tmp_path,
        2022,
        limiter=RateLimiter(0),
        games_fetch=lambda s: pytest.fail("list refetched"),
        box_fetch=lambda gid: _box_raw(["30:00", "", "PT12M30.00S"]),
        sleep=lambda _: None,
    )
    assert s2["boxes_fetched"] == 1 and s2["failed"] == []
    s3 = ps.fetch_season(
        tmp_path,
        2022,
        limiter=RateLimiter(0),
        games_fetch=lambda s: pytest.fail("list refetched"),
        box_fetch=lambda gid: pytest.fail("box refetched"),
        sleep=lambda _: None,
    )
    assert s3["boxes_cached"] == 1


def test_build_and_load_idempotent_dnp_null(tmp_path: Path) -> None:
    ps.write_once(_games_raw(), ps.games_path(tmp_path, 2022))
    ps.write_once(_box_raw(["30:00", "", "PT12M30.00S"]), ps.box_path(tmp_path, "0012200001"))
    when = dt.datetime(2026, 10, 20, 12, 0)
    games, stats, missing = ps.build_tables(tmp_path, [2022], when)
    assert missing == [] and games.height == 1 and stats.height == 3
    con = duckdb.connect(":memory:")
    c1 = ps.load_tables(con, games, stats)
    c2 = ps.load_tables(con, games, stats)
    assert c1 == c2 == {"preseason_games": 1, "preseason_player_game_stats": 3}
    mins = [
        r[0]
        for r in con.execute(
            "SELECT minutes FROM preseason_player_game_stats ORDER BY player_id"
        ).fetchall()
    ]
    assert mins[0] == 30.0 and mins[1] is None and abs(mins[2] - 12.5) < 1e-6
    assert con.execute(
        "SELECT count(*) FROM preseason_player_game_stats WHERE starter"
    ).fetchone() == (1,)


def test_games_without_box_get_no_games_row(tmp_path: Path) -> None:
    ps.write_once(_games_raw(), ps.games_path(tmp_path, 2022))
    games, stats, missing = ps.build_tables(tmp_path, [2022], dt.datetime(2026, 10, 20))
    assert games.height == 0 and stats.height == 0 and missing == ["0012200001"]


def test_gate_counts_changer_and_planted_future_row() -> None:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT)")
    con.execute(
        "CREATE TABLE player_game_stats "
        "(game_id VARCHAR, player_id INT, team_id INT, minutes FLOAT)"
    )
    # player 1 moves 100 -> 200 in summer 2024; player 2 stays; player 3 moves, 1 preseason game
    g = [
        ("0022300001", "2024-04-01", 2023),
        ("0022400001", "2024-10-25", 2024),
        ("0022400002", "2024-11-01", 2024),
    ]
    con.executemany("INSERT INTO games VALUES (?, ?, ?)", g)
    pgs = [
        ("0022300001", 1, 100, 30),
        ("0022300001", 2, 100, 30),
        ("0022300001", 3, 100, 30),
        ("0022400001", 1, 200, 30),
        ("0022400001", 2, 100, 30),
        ("0022400001", 3, 200, 30),
        ("0022400002", 1, 300, 30),  # later trade must not change the "first team"
    ]
    con.executemany("INSERT INTO player_game_stats VALUES (?, ?, ?, ?)", pgs)
    con.execute(ps.DDL.replace("PRIMARY KEY", ""))
    con.execute(
        "INSERT INTO preseason_games VALUES ('0012400001','2024-10-05',2024,200,100,1,1,now()), "
        "('0012400002','2024-10-08',2024,200,100,1,1,now())"
    )
    rows = [
        ("0012400001", 1, 200, 20),
        ("0012400002", 1, 200, 15),
        ("0012400001", 3, 200, 10),
        ("0012400002", 2, 100, 10),
        ("0012400001", 1, 100, 25),
    ]
    con.executemany(
        "INSERT INTO preseason_player_game_stats (game_id, player_id, team_id, minutes) "
        "VALUES (?,?,?,?)",
        rows,
    )
    out = ps.gate_counts(con)
    assert out["summer_team_changers"][2024] == {  # type: ignore[index]
        "changers": 2,
        "with_ge2_preseason_games_new_team": 1,
        "with_ge1_preseason_game_new_team": 2,
    }
    assert out["per_season"][0]["games"] == 2  # type: ignore[index]


def test_bad_minutes_string_becomes_null_not_crash() -> None:
    out = ps._blank_bad_minutes(_box_raw(["30:00", "-1:0-57", "", "PT0M00.00S", "12"]))
    assert out["minutes"].to_list() == ["30:00", None, None, "PT0M00.00S", "12"]
