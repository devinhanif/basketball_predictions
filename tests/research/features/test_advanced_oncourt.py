# ruff: noqa: E501
"""On-court denominators and shot buckets on hand-computed in-memory fixtures."""

from __future__ import annotations

import duckdb
import pytest

from research.features import advanced_shots as sh
from research.features.advanced_oncourt import build_oncourt


def _con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    c.execute(
        "CREATE TABLE pbp (game_id VARCHAR, team_id BIGINT, period BIGINT, clock VARCHAR, "
        "is_field_goal BIGINT, shot_result VARCHAR, shot_value BIGINT, action_type VARCHAR, "
        "sub_type VARCHAR, shot_distance BIGINT, player_id BIGINT)"
    )
    rows = [
        ("g", 1, 1, "PT10M00.00S", 1, "Made", 2, "Made Shot", "Layup Shot", 2, 1),  # in stint A
        ("g", 1, 1, "PT06M40.00S", 0, "", 0, "Turnover", "", 0, 5),  # 400 s: boundary -> A
        ("g", 1, 1, "PT05M00.00S", 0, "", 0, "Free Throw", "", 0, 6),  # 300 s -> B
        ("g", 2, 1, "PT09M00.00S", 1, "Missed", 3, "Missed Shot", "Jump Shot", 25, 11),  # team 2
    ]
    c.executemany("INSERT INTO pbp VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.execute(
        "CREATE TABLE stints (game_id VARCHAR, team_id INTEGER, period INTEGER, start_clock FLOAT, "
        "end_clock FLOAT, players INTEGER[5], stint_idx INTEGER)"
    )
    c.execute(
        "INSERT INTO stints VALUES ('g',1,1,720,400,[1,2,3,4,5],0), ('g',1,1,400,0,[1,2,3,4,6],1),"
        "('g',2,1,720,0,[11,12,13,14,15],0)"
    )
    c.execute(
        "CREATE TABLE possessions (game_id VARCHAR, off_players INTEGER[5], def_players INTEGER[5], "
        "n_oreb INTEGER, n_dreb INTEGER)"
    )
    c.execute(
        "INSERT INTO possessions VALUES "
        "('g',[1,2,3,4,5],[11,12,13,14,15],1,0), ('g',[11,12,13,14,15],[1,2,3,4,5],0,2)"
    )
    return c


def test_oncourt_events_minutes_and_rebound_availability() -> None:
    oc = build_oncourt(_con()).sort("player_id")
    by = {int(r["player_id"]): r for r in oc.iter_rows(named=True)}
    # players 1-4: layup FGA/FGM (A), turnover at exactly 400 s (A), FT at 300 s (B)
    for p in (1, 2, 3, 4):
        assert (by[p]["oc_fga"], by[p]["oc_fgm"], by[p]["oc_tov"], by[p]["oc_fta"]) == (1, 1, 1, 1)
    assert (by[5]["oc_fga"], by[5]["oc_tov"], by[5]["oc_fta"]) == (1, 1, 0)
    assert (by[6]["oc_fga"], by[6]["oc_tov"], by[6]["oc_fta"]) == (0, 0, 1)
    assert by[1]["oc_min"] == pytest.approx(12.0)  # 320 s + 400 s
    assert by[5]["oc_min"] == pytest.approx(320 / 60)
    assert by[6]["oc_min"] == pytest.approx(400 / 60)
    # team 1 on offence: own OREB 1, opposing DREB 0; on defence: own DREB 0 (team 2 had 2 DREB
    # credited to ITS def side = team 1 on offence in possession 1?) -> see assertions below
    assert (by[1]["oc_poss_off"], by[1]["oc_poss_def"]) == (1, 1)
    assert (by[1]["oc_oreb"], by[1]["oc_opp_dreb"]) == (1, 0)
    assert (by[1]["oc_dreb"], by[1]["oc_opp_oreb"]) == (2, 0)
    assert by[6]["oc_poss_off"] == 0  # 6 only appears in stints: counts are explicit zeros


def test_bucket_and_subtype_sql_equals_python() -> None:
    c = duckdb.connect()
    vals = [(2, 0), (2, 4), (2, 5), (2, 13), (2, 14), (3, 22), (3, 23), (3, 40)]
    for v, d in vals:
        sql = c.execute(
            f"SELECT {sh.BUCKET_CASE.replace('shot_value', str(v)).replace('shot_distance', str(d))}"
        )
        assert sql.fetchone()[0] == sh.shot_bucket(v, d)  # type: ignore[index]
    for t in ["Jump Shot", "Pullup Jump shot", "Running Pull-Up Jump Shot", "Step Back Jump shot",
              "Driving Floating Bank Jump Shot", "Alley Oop Dunk Shot", "Tip Layup Shot",
              "Turnaround Hook Shot", "Fadeaway Jump Shot"]:  # fmt: skip
        got = c.execute(f"SELECT {sh.SUBTYPE_CASE.replace('sub_type', repr(t))}").fetchone()
        assert got[0] == sh.shot_subtype(t)  # type: ignore[index]
    assert sh.shot_subtype("Driving Floating Bank Jump Shot") == "floater"
    assert sh.shot_bucket(3, 22) == "corner3" and sh.shot_bucket(2, 4) == "rim"


def test_play_type_counts_and_putback_home() -> None:
    from research.features.advanced_shots import play_type_counts, team_allowed_fast

    c = duckdb.connect()
    c.execute(
        "CREATE TABLE pbp (game_id VARCHAR, team_id BIGINT, is_field_goal BIGINT, shot_result VARCHAR, "
        "shot_value BIGINT, shot_distance BIGINT, sub_type VARCHAR, player_id BIGINT)"
    )
    c.executemany(
        "INSERT INTO pbp VALUES (?,?,?,?,?,?,?,?)",
        [
            ("g", 1, 1, "Made", 2, 1, "Putback Layup Shot", 7),
            ("g", 1, 1, "Missed", 2, 2, "Tip Dunk Shot", 7),
            ("g", 1, 1, "Made", 2, 2, "Layup Shot", 7),
        ],
    )
    c.execute(
        "CREATE TABLE possessions (game_id VARCHAR, def_team INTEGER, clock_start FLOAT, clock_end FLOAT, "
        "outcome VARCHAR, shooter_id INTEGER, assister_id INTEGER, pts INTEGER)"
    )
    c.executemany(
        "INSERT INTO possessions VALUES (?,?,?,?,?,?,?,?)",
        [
            ("g", 2, 700.0, 695.0, "FGM2", 7, 8, 2),  # fast (5 s), assisted make
            ("g", 2, 600.0, 580.0, "FGA_miss", 7, None, 0),  # slow miss
            ("g", 2, 500.0, 493.0, "FGM3", 7, None, 3),  # exactly 7 s counts as fast, unassisted
            ("g", 2, 400.0, 390.0, "TOV", None, None, 0),
        ],
    )
    r = play_type_counts(c).row(0, named=True)
    assert (r["fsp"], r["tr_fsp"], r["tr_pts"], r["mk"], r["ast_mk"]) == (3, 2, 5, 2, 1)
    assert (r["pb_att"], r["pb_made"]) == (2, 1)  # 'Putback' and 'Tip' both land in PLAY_TYPE
    assert sh.shot_subtype("Tip Dunk Shot") == "putback"
    assert "putback" not in sh.SHOT_PROFILE_SUBTYPES  # one home per column
    f = team_allowed_fast(c).row(0, named=True)
    assert (f["faced"], f["faced_fast"]) == (4, 2)
