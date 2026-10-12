"""nbastats adapter, derived box, season build, QC and load on a synthetic two-team game.

No network, no real data: the CSV layout of shufinskiy/nba_data is written to ``tmp_path``.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from nba.db.connect import connect
from nba.ingest.nbastats_box import derive_box, derive_starters
from nba.ingest.nbastats_pbp import (
    RAW_COLUMNS,
    duplicate_keys,
    load_subtype_map,
    read_nbastats,
    to_v3,
)
from nba.parse.history_nbastats import build_season
from nba.parse.history_nbastats_load import load_stage, qc_stage
from nba.parse.history_reconcile import reconcile_box
from nba.parse.possessions import parse_possessions

HOME, AWAY = 1610612737, 1610612738
HOME_P = [101, 102, 103, 104, 105]
AWAY_P = [201, 202, 203, 204, 205]
NAMES = {
    101: "Al Hawk",
    102: "Bo Hawk",
    103: "Cy Crane",
    104: "Di Dove",
    105: "Ed Egret",
    106: "Fy Finch",
    201: "Gus Lion",
    202: "Hal Lynx",
    203: "Ian Moose",
    204: "Jay Newt",
    205: "Ken Otter",
    206: "Lee Pike",
}


def _row(
    gid: int, num: int, et: int, ea: int, period: int, clock: str, **kw: Any
) -> dict[str, Any]:
    r: dict[str, Any] = dict.fromkeys(RAW_COLUMNS)
    r.update(GAME_ID=str(gid), EVENTNUM=str(num), EVENTMSGTYPE=str(et), EVENTMSGACTIONTYPE=str(ea))
    r.update(PERIOD=str(period), PCTIMESTRING=clock)
    for k, v in kw.items():
        r[k] = None if v is None else str(v)
    return r


def _game(gid: int = 21500001) -> list[dict[str, Any]]:
    """12 minutes of one period: starters act, one sub per side, a 3, a FT trip, a steal, a
    block."""
    rows = [_row(gid, 1, 12, 0, 1, "12:00", NEUTRALDESCRIPTION="Start of 1st Period")]

    def who(pid: int) -> dict[str, Any]:
        home = pid < 200
        return {
            "PLAYER1_ID": pid,
            "PLAYER1_NAME": NAMES[pid],
            "PLAYER1_TEAM_ID": HOME if home else AWAY,
            "PERSON1TYPE": 4 if home else 5,
        }

    def other(n: int, pid: int) -> dict[str, Any]:
        home = pid < 200
        return {
            f"PLAYER{n}_ID": pid,
            f"PLAYER{n}_NAME": NAMES[pid],
            f"PLAYER{n}_TEAM_ID": HOME if home else AWAY,
        }

    def desc(pid: int, text: str) -> dict[str, Any]:
        return {"HOMEDESCRIPTION": text} if pid < 200 else {"VISITORDESCRIPTION": text}

    # made three by 101 assisted by 102 (home +3)
    rows.append(
        _row(
            gid,
            2,
            1,
            1,
            1,
            "11:40",
            SCORE="0 - 3",
            **who(101),
            **other(2, 102),
            **desc(101, "Hawk 25' 3PT Jump Shot (3 PTS) (Hawk 1 AST)"),
        )
    )
    # away miss by 201 blocked by 103, rebound 202 (defence of the away team? no: 202 is away ->
    # offensive)
    rows.append(
        _row(
            gid,
            3,
            2,
            1,
            1,
            "11:20",
            **who(201),
            **other(3, 103),
            **desc(201, "MISS Lion 12' Jump Shot"),
        )
    )
    rows.append(
        _row(gid, 4, 4, 0, 1, "11:18", **who(202), **desc(202, "Lynx REBOUND (Off:1 Def:0)"))
    )
    # away turnover by 203 stolen by 104
    rows.append(
        _row(
            gid,
            5,
            5,
            1,
            1,
            "11:00",
            **who(203),
            **other(2, 104),
            **desc(203, "Moose Bad Pass Turnover (P1.T1)"),
        )
    )
    # home two free throws by 105: make, miss
    rows.append(
        _row(
            gid,
            6,
            3,
            11,
            1,
            "10:30",
            SCORE="0 - 4",
            **who(105),
            **desc(105, "Egret Free Throw 1 of 2 (1 PTS)"),
        )
    )
    rows.append(
        _row(gid, 7, 3, 12, 1, "10:30", **who(105), **desc(105, "MISS Egret Free Throw 2 of 2"))
    )
    # team rebound (no player team id): PERSON1TYPE team
    rows.append(
        _row(
            gid,
            8,
            4,
            0,
            1,
            "10:30",
            PLAYER1_ID=AWAY,
            PERSON1TYPE=3,
            VISITORDESCRIPTION="LIONS Rebound",
        )
    )
    # away personal foul by 204, away 204 makes a layup assisted by 205 after home sub
    rows.append(
        _row(
            gid, 9, 6, 1, 1, "9:50", **who(204), **other(2, 101), **desc(204, "Newt P.FOUL (P1.T1)")
        )
    )
    # home sub: 106 in for 104
    rows.append(
        _row(
            gid,
            10,
            8,
            0,
            1,
            "9:00",
            **who(104),
            **other(2, 106),
            **desc(104, "SUB: Finch FOR Dove"),
        )
    )
    # away: 205 makes a layup, assisted by 204
    rows.append(
        _row(
            gid,
            11,
            1,
            5,
            1,
            "8:40",
            SCORE="2 - 4",
            **who(205),
            **other(2, 204),
            **desc(205, "Otter 2' Layup (2 PTS) (Newt 1 AST)"),
        )
    )
    # home technical foul by 106 (excluded from PF)
    rows.append(
        _row(gid, 12, 6, 11, 1, "8:00", **who(106), **desc(106, "Finch T.FOUL (Def.3.Sec)"))
    )
    # away: 206 subbed in for 201 (so 201 leaves with fga)
    rows.append(
        _row(
            gid, 13, 8, 0, 1, "7:00", **who(201), **other(2, 206), **desc(201, "SUB: Pike FOR Lion")
        )
    )
    rows.append(_row(gid, 14, 13, 0, 1, "0:00", NEUTRALDESCRIPTION="End of 1st Period"))
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]], cols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) is None else r[c]) for c in cols})


def _frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=dict.fromkeys(RAW_COLUMNS, pl.Utf8))


SUBS = load_subtype_map()


def test_to_v3_maps_fields() -> None:
    v3 = to_v3(_frame(_game()), SUBS)
    assert v3["game_id"][0] == "0021500001"
    made = v3.filter(pl.col("action_type") == "Made Shot").row(0, named=True)
    assert (made["team_id"], made["player_id"], made["shot_value"], made["shot_distance"]) == (
        HOME,
        101,
        3,
        25,
    )
    assert made["score_home"] == "3" and made["score_away"] == "0" and made["location"] == "h"
    ft = v3.filter(pl.col("action_type") == "Free Throw")
    assert ft["sub_type"].to_list() == ["Free Throw 1 of 2", "Free Throw 2 of 2"]
    team_reb = v3.filter((pl.col("action_type") == "Rebound") & (pl.col("team_id") == 0)).row(
        0, named=True
    )
    assert team_reb["player_id"] == AWAY and team_reb["player_name"] == ""
    sub = v3.filter(pl.col("action_type") == "Substitution").row(0, named=True)
    assert sub["player_id"] == 104 and sub["description"] == "SUB: Finch FOR Dove"
    assert v3.filter(pl.col("action_type") == "period")["sub_type"].to_list() == ["start", "end"]
    # the existing possession parser runs on the mapped frame
    poss = parse_possessions(v3)
    assert poss["pts"].sum() == 3 + 1 + 2


def test_same_surname_teammates_get_initials() -> None:
    rows = _game()
    # 102 "Bo Hawk" subs in for 101 "Al Hawk": both named Hawk on the same team
    rows.append(
        _row(
            21500001,
            15,
            8,
            0,
            1,
            "6:00",
            PLAYER1_ID=101,
            PLAYER1_NAME=NAMES[101],
            PLAYER1_TEAM_ID=HOME,
            PERSON1TYPE=4,
            PLAYER2_ID=102,
            PLAYER2_NAME=NAMES[102],
            PLAYER2_TEAM_ID=HOME,
            HOMEDESCRIPTION="SUB: Hawk FOR Hawk",
        )
    )
    v3 = to_v3(_frame(rows), SUBS)
    sub = v3.filter(pl.col("action_number") == 15).row(0, named=True)
    assert sub["description"] == "SUB: B. Hawk FOR A. Hawk"


def test_unmapped_codes_are_counted_not_guessed() -> None:
    rows = _game()
    rows.append(
        _row(
            21500001,
            16,
            1,
            9999,
            1,
            "5:00",
            SCORE="2 - 6",
            PLAYER1_ID=101,
            PLAYER1_NAME=NAMES[101],
            PLAYER1_TEAM_ID=HOME,
            HOMEDESCRIPTION="Hawk 3' Odd Shot (5 PTS)",
        )
    )
    stats: dict[str, int] = {}
    v3 = to_v3(_frame(rows), SUBS, stats)
    assert stats["unmapped_subtype"] == 1
    assert v3.filter(pl.col("action_number") == 16)["sub_type"][0] == ""


def test_shot_distance_falls_back_to_shotdetail() -> None:
    rows = _game()
    rows.append(
        _row(
            21500001,
            16,
            1,
            5,
            1,
            "5:00",
            SCORE="2 - 6",
            PLAYER1_ID=101,
            PLAYER1_NAME=NAMES[101],
            PLAYER1_TEAM_ID=HOME,
            HOMEDESCRIPTION="Hawk  Layup (5 PTS)",
        )
    )
    st: dict[str, int] = {}
    v3 = to_v3(_frame(rows), SUBS, st, {16: 2})
    assert v3.filter(pl.col("action_number") == 16)["shot_distance"][0] == 2
    assert st["distance_from_shotdetail"] == 1


def test_box_counts_and_technical_excluded() -> None:
    raw = _game()
    box = derive_box(raw, SUBS, {}, None)
    b = {r["player_id"]: r for r in box.iter_rows(named=True)}
    assert (b[101]["pts"], b[101]["fg3m"], b[101]["fga"]) == (3, 1, 1)
    assert b[102]["ast"] == 1 and b[103]["blk"] == 1 and b[104]["stl"] == 1
    assert b[202]["oreb"] == 1 and b[202]["reb"] == 1
    assert (b[105]["ftm"], b[105]["fta"], b[105]["pts"]) == (1, 2, 1)
    assert b[204]["pf"] == 1 and b[204]["ast"] == 1
    assert b[106]["pf"] == 0  # technical
    assert b[203]["tov"] == 1 and b[205]["pts"] == 2


def test_reconcile_box_flags_planted_differences() -> None:
    box = derive_box(_game(), SUBS, {101: 10.0, 102: 10.0}, None)
    off = box.with_columns(
        pl.when(pl.col("player_id") == 101)
        .then(pl.col("pts") + 1)
        .otherwise(pl.col("pts"))
        .alias("pts"),
        pl.when(pl.col("player_id") == 102)
        .then(12.5)
        .otherwise(pl.col("minutes"))
        .alias("minutes"),
    )
    rec = reconcile_box(box, off)
    assert rec["per_stat"]["pts"]["mismatch"] == 1
    assert rec["minutes"]["within_tol"] == rec["minutes"]["n"] - 1


def _starter_game(gid: int = 21500001) -> list[dict[str, Any]]:
    """Every starter acts early so five per side exist; later events as in ``_game``."""
    rows = [_row(gid, 1, 12, 0, 1, "12:00", NEUTRALDESCRIPTION="Start of 1st Period")]
    n = 2
    for pid in HOME_P + AWAY_P:
        home = pid < 200
        rows.append(
            _row(
                gid,
                n,
                3,
                10,
                1,
                f"11:{59 - n:02d}",
                PLAYER1_ID=pid,
                PLAYER1_NAME=NAMES[pid],
                PLAYER1_TEAM_ID=HOME if home else AWAY,
                PERSON1TYPE=4 if home else 5,
                **(
                    {"HOMEDESCRIPTION": "X Free Throw 1 of 1 (1 PTS)"}
                    if home
                    else {"VISITORDESCRIPTION": "X Free Throw 1 of 1 (1 PTS)"}
                ),
                SCORE=None,
            )
        )
        n += 1
    rows.append(
        _row(
            gid,
            n,
            8,
            0,
            1,
            "9:00",
            PLAYER1_ID=104,
            PLAYER1_NAME=NAMES[104],
            PLAYER1_TEAM_ID=HOME,
            PERSON1TYPE=4,
            PLAYER2_ID=106,
            PLAYER2_NAME=NAMES[106],
            PLAYER2_TEAM_ID=HOME,
            HOMEDESCRIPTION="SUB: Finch FOR Dove",
        )
    )
    rows.append(_row(gid, n + 1, 13, 0, 1, "0:00", NEUTRALDESCRIPTION="End of 1st Period"))
    return rows


def test_starters_found_from_first_actions_before_subbing_in() -> None:
    st = derive_starters(_starter_game(), SUBS)
    assert st is not None
    assert sorted(st[HOME]) == HOME_P and sorted(st[AWAY]) == AWAY_P


def test_read_dedupes_exact_rows(tmp_path: Path) -> None:
    rows = _game()
    p = tmp_path / "x.csv"
    _write_csv(p, rows + rows[:3], RAW_COLUMNS)
    df = read_nbastats([p])
    assert df.height == len(rows) and duplicate_keys(df) == 0
    conflict = [*rows, {**rows[1], "PLAYER1_ID": "999"}]
    _write_csv(p, conflict, RAW_COLUMNS)
    assert duplicate_keys(read_nbastats([p])) == 1


SHOT_COLS = [
    "GRID_TYPE",
    "GAME_ID",
    "GAME_EVENT_ID",
    "PLAYER_ID",
    "PLAYER_NAME",
    "TEAM_ID",
    "TEAM_NAME",
    "PERIOD",
    "MINUTES_REMAINING",
    "SECONDS_REMAINING",
    "EVENT_TYPE",
    "ACTION_TYPE",
    "SHOT_TYPE",
    "SHOT_ZONE_BASIC",
    "SHOT_ZONE_AREA",
    "SHOT_ZONE_RANGE",
    "SHOT_DISTANCE",
    "LOC_X",
    "LOC_Y",
    "SHOT_ATTEMPTED_FLAG",
    "SHOT_MADE_FLAG",
    "GAME_DATE",
    "HTM",
    "VTM",
]


def _extracted(tmp_path: Path) -> Path:
    ex = tmp_path / "extracted"
    rows = _starter_game()
    _write_csv(ex / "nbastats_2015" / "nbastats_2015.csv", rows, RAW_COLUMNS)
    _write_csv(ex / "nbastats_po_2015" / "nbastats_po_2015.csv", [], RAW_COLUMNS)
    shot = {
        "GRID_TYPE": "Shot Chart Detail",
        "GAME_ID": "21500001",
        "GAME_EVENT_ID": "2",
        "PLAYER_ID": "101",
        "PLAYER_NAME": "Al Hawk",
        "TEAM_ID": str(HOME),
        "TEAM_NAME": "Hawks",
        "PERIOD": "1",
        "MINUTES_REMAINING": "11",
        "SECONDS_REMAINING": "40",
        "EVENT_TYPE": "Made Shot",
        "ACTION_TYPE": "Jump Shot",
        "SHOT_TYPE": "3PT Field Goal",
        "SHOT_ZONE_BASIC": "Above the Break 3",
        "SHOT_ZONE_AREA": "Center(C)",
        "SHOT_ZONE_RANGE": "24+ ft.",
        "SHOT_DISTANCE": "25",
        "LOC_X": "0",
        "LOC_Y": "250",
        "SHOT_ATTEMPTED_FLAG": "1",
        "SHOT_MADE_FLAG": "1",
        "GAME_DATE": "20151027",
        "HTM": "HWK",
        "VTM": "CRN",
    }
    _write_csv(ex / "shotdetail_2015" / "shotdetail_2015.csv", [shot], SHOT_COLS)
    _write_csv(ex / "shotdetail_po_2015" / "shotdetail_po_2015.csv", [], SHOT_COLS)
    return ex


def test_build_qc_load_roundtrip(tmp_path: Path) -> None:
    ex = _extracted(tmp_path)
    hist = tmp_path / "history"
    stage = tmp_path / "stage" / "2015"
    summ = build_season(ex, 2015, stage, hist, progress_every=0)
    assert summ["games"] == 1 and summ["n_problems"] == 0
    assert (hist / "pbp" / "0021500001.parquet").exists()
    games = pl.read_parquet(stage / "games.parquet")
    assert games["home_team"][0] == HOME and games["away_team"][0] == AWAY
    assert str(games["game_date"][0]) == "2015-10-27"
    qc = qc_stage(stage)
    assert qc["games"] == 1 and qc["possession_points_equal_box_points"] == 1.0

    db = tmp_path / "hist.duckdb"
    connect(db).close()
    res = load_stage(db, tmp_path / "stage", [2015], hist, tmp_path / "backups")
    assert res["before"]["games"] == 0 and res["after"]["games"] == 1
    assert res["after"]["game_era_flags"] == 1
    assert Path(res["backup"]).exists()
    con = connect(db, read_only=True)
    try:
        flags = con.execute("SELECT no_injury_report, box_derived FROM game_era_flags").fetchone()
        assert flags == (True, True)
        assert con.execute("SELECT count(*) FROM player_game_stats").fetchone()[0] == len(  # type: ignore[index]
            pl.read_parquet(stage / "box.parquet")
        )
        assert con.execute("SELECT count(*) FROM shots").fetchone() == (1,)
    finally:
        con.close()
    # a second load of the same ids must abort and change nothing
    with pytest.raises(RuntimeError, match="already in games"):
        load_stage(db, tmp_path / "stage", [2015], hist, tmp_path / "backups2")
    con = connect(db, read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM games").fetchone() == (1,)
    finally:
        con.close()
    # existing pbp files are never overwritten by a rebuild
    before = (hist / "pbp" / "0021500001.parquet").stat().st_mtime_ns
    build_season(ex, 2015, tmp_path / "stage2" / "2015", hist, progress_every=0)
    assert (hist / "pbp" / "0021500001.parquet").stat().st_mtime_ns == before


def test_extra_game_in_file_does_not_change_other_game(tmp_path: Path) -> None:
    """Per-game transform: adding a second game (a planted 'future' game) leaves game 1
    identical."""
    ex1 = _extracted(tmp_path / "a")
    ex2 = _extracted(tmp_path / "b")
    later = _starter_game(21500002)
    for r in later:
        if r["PLAYER1_ID"] in (str(p) for p in HOME_P):
            r["PLAYER1_ID"] = str(int(r["PLAYER1_ID"]) + 1000)
    path = ex2 / "nbastats_2015" / "nbastats_2015.csv"
    _write_csv(path, [*_starter_game(), *later], RAW_COLUMNS)
    s1 = build_season(ex1, 2015, tmp_path / "s1", tmp_path / "h1", progress_every=0)
    s2 = build_season(ex2, 2015, tmp_path / "s2", tmp_path / "h2", progress_every=0)
    assert s1["games"] == 1 and s2["games"] == 2
    b1 = pl.read_parquet(tmp_path / "s1" / "box.parquet").sort("player_id")
    b2 = (
        pl.read_parquet(tmp_path / "s2" / "box.parquet")
        .filter(pl.col("game_id") == "0021500001")
        .sort("player_id")
    )
    assert b1.equals(b2)


def test_boxsample_runs_ids_through_the_puller_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nba.ingest import boxsample

    ids = tmp_path / "ids.txt"
    ids.write_text("0021500001\n0021500002\n")
    monkeypatch.setattr(boxsample, "SAMPLE_DIR", tmp_path / "bs")
    monkeypatch.setattr(boxsample, "DB_PATH", tmp_path / "bs" / "bs.duckdb")
    seen: list[str] = []

    def fake_pull(con: object, gid: str, **kw: object) -> None:
        seen.append(gid)

    monkeypatch.setattr(boxsample, "pull_game_boxscore", fake_pull)
    assert boxsample.main(["--ids-file", str(ids), "--rate-limit-s", "2.5"]) == 0
    assert seen == ["0021500001", "0021500002"]
    with pytest.raises(SystemExit):
        boxsample.main(["--ids-file", str(ids), "--rate-limit-s", "0.5"])


def test_coverage_flags_a_missing_game_and_a_broken_series() -> None:
    from datetime import date

    from nba.parse.history_coverage import playoffs, regular_season

    reg = pl.DataFrame(
        {
            "game_id": [f"00215{n:05d}" for n in range(1, 1231) if n != 77],
            "game_date": [date(2015, 11, 1)] * 1229,
            "home_team": [(n % 30) + 1 for n in range(1, 1231) if n != 77],
            "away_team": [((n + 7) % 30) + 1 for n in range(1, 1231) if n != 77],
        }
    )
    rep = regular_season(reg, 2015)
    assert rep["games"] == 1229 and rep["missing_game_numbers"] == [77]
    po = pl.DataFrame(
        {
            "game_id": ["0041500111", "0041500112", "0041500114"],  # game 3 missing
            "game_date": [date(2016, 4, 17)] * 3,
            "home_team": [1, 2, 2],
            "away_team": [2, 1, 1],
            "home_pts": [100, 90, 95],
            "away_pts": [90, 100, 90],
        }
    )
    probs = playoffs(po)["structure_problems"]
    assert any("game numbers not 1..3" in p for p in probs)
