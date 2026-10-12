"""shot_events: the shot-to-PBP join, the block fan-out rule, known_at, and the shots reload."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import polars as pl
import pytest

from nba.data.shot_events import (
    build_shot_events,
    fga_reconciliation_by_season,
    join_audit,
    load_pbp_shot_rows,
    materialize,
)
from nba.ingest import postgame as pg
from tests.fixtures.loader import build_fixture_db

G = "0022200001"


def _shots() -> pl.DataFrame:
    base = {
        "team_id": 10,
        "period": 1,
        "clock_seconds": 600,
        "loc_x": 5,
        "loc_y": 20,
        "shot_distance": 3,
        "shot_zone_basic": "Restricted Area",
        "shot_zone_area": "Center(C)",
        "shot_zone_range": "Less Than 8 ft.",
        "action_type": "Layup",
        "shot_type": "2PT Field Goal",
    }
    rows = [
        {**base, "game_id": G, "game_event_id": 7, "player_id": 1, "made": True},
        {**base, "game_id": G, "game_event_id": 9, "player_id": 2, "made": False},
        {**base, "game_id": G, "game_event_id": 11, "player_id": 3, "made": False},
    ]
    return pl.DataFrame(rows)


def _pbp_all() -> pl.DataFrame:
    """Event 9 was blocked: PBP holds the shot AND a block row reusing action_number 9."""

    def row(n: int, pid: int, team: int, at: str, desc: str, fg: int) -> dict[str, object]:
        return {
            "game_id": G,
            "action_number": n,
            "team_id": team,
            "player_id": pid,
            "action_type": at,
            "description": desc,
            "is_field_goal": fg,
        }

    return pl.DataFrame(
        [
            row(7, 1, 10, "Made Shot", "A layup", 1),
            row(9, 2, 10, "Missed Shot", "B layup", 1),
            row(9, 99, 20, "", "Blocker BLOCK (1 BLK)", 0),
            row(11, 4, 10, "Missed Shot", "D jumper", 1),  # PBP names a different player
        ]
    )


def _fg(pbp: pl.DataFrame) -> pl.DataFrame:
    return pbp.filter(
        (pl.col("is_field_goal") == 1) & pl.col("action_type").is_in(["Made Shot", "Missed Shot"])
    )


def _games() -> pl.DataFrame:
    return pl.DataFrame({"game_id": [G], "season": [2022], "game_date": [date(2022, 10, 18)]})


def test_block_row_does_not_fan_out_or_steal_the_shot() -> None:
    out = build_shot_events(_shots(), _fg(_pbp_all()), _games())
    assert out.height == 3  # no fan-out
    r = out.filter(pl.col("action_number") == 9).row(0, named=True)
    assert r["pbp_player_id"] == 2 and r["player_match"] is True  # not the blocker (99)
    # the naive join would have produced the blocker as a mismatch
    naive = _shots().join(
        _pbp_all(), left_on=["game_id", "game_event_id"], right_on=["game_id", "action_number"]
    )
    assert naive.height == 4


def test_player_mismatch_is_flagged_not_hidden() -> None:
    out = build_shot_events(_shots(), _fg(_pbp_all()), _games()).sort("action_number")
    assert out["player_match"].to_list() == [True, True, False]
    assert out["made_match"].to_list() == [True, True, True]
    bad = out.filter(~pl.col("player_match")).row(0, named=True)
    assert bad["shot_player_id"] == 3 and bad["pbp_player_id"] == 4


def test_unmatched_shot_is_kept_with_null_pbp_fields() -> None:
    pbp = _fg(_pbp_all()).filter(pl.col("action_number") != 7)
    out = build_shot_events(_shots(), pbp, _games()).sort("action_number")
    r = out.row(0, named=True)
    assert r["pbp_matched"] is False and r["player_match"] is None and r["made_match"] is None
    assert r["player_id"] == 1  # falls back to the shot chart's player
    assert out["pbp_matched"].to_list() == [False, True, True]


def test_known_at_is_after_the_game_and_before_the_next_slate() -> None:
    out = build_shot_events(_shots(), _fg(_pbp_all()), _games())
    ka = out["known_at"][0]
    assert ka == datetime(2022, 10, 19, 12, 0)
    assert set(out["known_at"].to_list()) == {ka}


def test_planted_future_row_never_precedes_its_own_known_at() -> None:
    games = pl.DataFrame({"game_id": [G], "season": [2022], "game_date": [date(2022, 10, 18)]})
    out = build_shot_events(_shots(), _fg(_pbp_all()), games)
    tip_next_day = datetime(2022, 10, 19, 0, 0)  # a feature for a game on the 19th (00:00 UTC)
    usable = out.filter(pl.col("known_at") < tip_next_day)
    assert usable.height == 0  # the same-night shots are not visible before the day rolls over


def test_duplicate_pbp_key_raises() -> None:
    dup = pl.concat([_fg(_pbp_all()), _fg(_pbp_all()).head(1)])
    with pytest.raises(ValueError, match="duplicate"):
        build_shot_events(_shots(), dup, _games())
    with pytest.raises(ValueError, match="duplicate"):
        build_shot_events(pl.concat([_shots(), _shots().head(1)]), _fg(_pbp_all()), _games())


def test_load_pbp_shot_rows_drops_block_rows(tmp_path: Path) -> None:
    _pbp_all().write_parquet(tmp_path / f"{G}.parquet")
    fg = load_pbp_shot_rows([G, "0022209999"], tmp_path)  # missing file is skipped
    assert fg.height == 3 and 99 not in fg["player_id"].to_list()


def test_materialize_and_audits_on_fixture(tmp_path: Path) -> None:
    con = build_fixture_db()
    g = con.execute("SELECT game_id, season, game_date, home_team FROM games LIMIT 1").fetchone()
    assert g is not None
    gid, _season, _d, team = g
    shots = _shots().with_columns(pl.lit(gid).alias("game_id"), pl.lit(team).alias("team_id"))
    pg.insert_rows(con, "shots", pg.TABLES["shots"][1], shots.select(pg.TABLES["shots"][1]))
    pbp = _pbp_all().with_columns(pl.lit(gid).alias("game_id"))
    pbp.write_parquet(tmp_path / f"{gid}.parquet")
    assert materialize(con, tmp_path) == 3
    audit = join_audit(con)
    assert audit[0]["n"] == 3 and audit[0]["joined"] == 1.0
    assert audit[0]["player_mismatch"] == 1
    assert fga_reconciliation_by_season(con) is not None


def test_shots_reload_across_batches_keeps_both_teams(tmp_path: Path) -> None:
    """Both teams' files hold one game; a later batch must not delete the earlier rows."""
    con = build_fixture_db()
    gid = con.execute("SELECT game_id FROM games LIMIT 1").fetchone()[0]  # type: ignore[index]
    d = tmp_path / "shots"
    d.mkdir()
    for team, ev in ((10, 1), (20, 2)):
        s = (
            _shots()
            .head(1)
            .with_columns(
                pl.lit(gid).alias("game_id"),
                pl.lit(team).alias("team_id"),
                pl.lit(ev).alias("game_event_id"),
            )
        )
        s.write_parquet(d / f"{team}_2022-23_RS.parquet")
    pg.load_source(con, "shots", data_dir=tmp_path, batch_files=1)
    assert con.execute("SELECT count(*) FROM shots").fetchone() == (2,)
    pg.load_source(con, "shots", data_dir=tmp_path, batch_files=1)  # idempotent
    assert con.execute("SELECT count(*) FROM shots").fetchone() == (2,)
