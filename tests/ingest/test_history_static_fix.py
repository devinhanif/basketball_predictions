"""History national_tv / players_static repair on a synthetic history DB (no network)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import polars as pl

from nba.db.connect import connect
from nba.ingest.history_static_fix import (
    apply_plan,
    coverage,
    national_tv_from_schedules,
    plan,
    uncached_ids,
)


def _schedules(d: Path) -> Path:
    d.mkdir()
    pl.DataFrame(
        {
            "gameId": ["0021900001", "0021900002"],
            "nationalBroadcasters_broadcasterAbbreviation": ["ESPN", None],
        }
    ).write_parquet(d / "raw_2019-20.parquet")
    pl.DataFrame(
        {
            "gameId": ["0022000001", "0022000002"],
            "nationalBroadcasters_0_broadcasterAbbreviation": ["TNT", ""],
            "nationalBroadcasters_1_broadcasterAbbreviation": [None, "TNTOT"],
        }
    ).write_parquet(d / "raw_2020-21.parquet")
    return d


def _cache(data_dir: Path, pid: int, birth: date | None, empty: bool = False) -> None:
    out = data_dir / "players_static"
    out.mkdir(parents=True, exist_ok=True)
    if empty:
        pl.DataFrame().write_parquet(out / f"{pid}.parquet")
        return
    pl.DataFrame(
        {"player_id": [pid], "birth_date": [birth], "position": ["G"]},
        schema={"player_id": pl.Int64, "birth_date": pl.Date, "position": pl.Utf8},
    ).write_parquet(out / f"{pid}.parquet")


def _db(path: Path) -> None:
    con = connect(path)
    con.execute(
        "INSERT INTO games (game_id, season) VALUES ('0021900001', 2019), ('0021900002', 2019)"
    )
    con.execute(
        "INSERT INTO games (game_id, season) VALUES ('0022000001', 2020), ('0022000002', 2020)"
    )
    for gid in ("0021900001", "0022000001"):
        for pid in (1, 2, 3):
            con.execute(
                "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes) "
                "VALUES (?, ?, 10, 20.0)",
                [gid, pid],
            )
    con.close()


def test_national_tv_first_slot_only_and_empty_dropped(tmp_path: Path) -> None:
    tv = national_tv_from_schedules(_schedules(tmp_path / "sched"))
    assert dict(zip(tv["game_id"], tv["national_tv"], strict=True)) == {
        "0021900001": "ESPN",
        "0022000001": "TNT",
    }


def test_apply_fills_only_known_and_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "h.duckdb"
    _db(db)
    sched = _schedules(tmp_path / "sched")
    data_dir = tmp_path / "data"
    _cache(data_dir, 1, date(1990, 1, 2))
    _cache(data_dir, 2, None)
    _cache(data_dir, 3, None, empty=True)  # a cached empty response is not data
    ro = duckdb.connect(str(db), read_only=True)
    p = plan(ro, data_dir, sched)
    ro.close()
    assert p["uncached_2019_21"] == [3]
    assert uncached_ids([1, 2, 3], data_dir) == [3]
    res = apply_plan(db, p, backup_dir=tmp_path / "bk", stamp="t")
    assert res["ok"] and Path(res["backup"]).exists()
    ro = duckdb.connect(str(db), read_only=True)
    cov = coverage(ro)
    assert cov["2019"]["national_tv"] == 1.0 and cov["2020"]["national_tv"] == 1.0
    assert ro.execute("SELECT count(*) FROM games WHERE national_tv IS NOT NULL").fetchone()[0] == 2  # type: ignore[index]
    assert cov["2019"]["birth_date"] == round(1 / 3, 4)  # only player 1 has a birth date
    assert ro.execute("SELECT count(*) FROM player_game_stats").fetchone()[0] == 6  # type: ignore[index]
    ro.close()
    again = apply_plan(db, p, backup_dir=tmp_path / "bk", stamp="t2")
    assert again["ok"] and again["after"] == res["after"]


def test_no_overwrite_of_existing_value(tmp_path: Path) -> None:
    db = tmp_path / "h.duckdb"
    _db(db)
    con = connect(db)
    con.execute("UPDATE games SET national_tv = 'KEEP' WHERE game_id = '0021900001'")
    con.close()
    ro = duckdb.connect(str(db), read_only=True)
    p = plan(ro, tmp_path / "data", _schedules(tmp_path / "sched"))
    ro.close()
    res = apply_plan(db, p, backup_dir=tmp_path / "bk", stamp="t")
    # the schedule disagrees with the kept cell: the gate refuses and rolls back
    assert not res["ok"]
    ro = duckdb.connect(str(db), read_only=True)
    assert (
        ro.execute("SELECT national_tv FROM games WHERE game_id='0022000001'").fetchone()[0] is None
    )  # type: ignore[index]
    ro.close()


def test_wide_range_covers_pre_2019_seasons(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    from nba.ingest.history_static_fix import fetch_missing

    db = tmp_path / "h.duckdb"
    _db(db)
    con = connect(db)
    con.execute("INSERT INTO games (game_id, season) VALUES ('0021300001', 2013)")
    con.execute("INSERT INTO games (game_id, season) VALUES ('0021500001', 2015)")
    for gid, pid in (("0021300001", 7), ("0021500001", 8), ("0021500001", 1)):
        con.execute(
            "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes) "
            "VALUES (?, ?, 10, 20.0)",
            [gid, pid],
        )
    con.close()
    data_dir = tmp_path / "data"
    _cache(data_dir, 1, date(1990, 1, 2))
    _cache(data_dir, 8, date(1991, 1, 2))
    # dry run: counts ids from 2013 on (7 uncached; 2, 3 uncached too), no network, no writes
    assert fetch_missing(db, data_dir, first_season=2013, dry_run=True) == 0
    assert "uncached players, seasons 2013-2021: 3 " in capsys.readouterr().out
    fetch_missing(db, data_dir, first_season=2019, dry_run=True)
    assert "seasons 2019-2021: 2 " in capsys.readouterr().out  # 2, 3 only
    ro = duckdb.connect(str(db), read_only=True)
    p = plan(ro, data_dir, _schedules(tmp_path / "sched"))
    ro.close()
    assert sorted(p["uncached_wide"]) == [2, 3, 7]
    assert sorted(p["static"]["player_id"].to_list()) == [1, 8]  # the 2015 id 8 is loaded
