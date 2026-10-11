"""Unit tests for nba/parse/possessions.py segmentation + classification.

Each test builds a tiny synthetic play-by-play sequence (real V3 schema,
see tests/parse/helpers.py) covering one segmentation rule from CLAUDE.md,
so each rule is independently provable -- CLAUDE.md flags this parser as
the highest-risk component.
"""

from __future__ import annotations

from nba.parse.possessions import _shot_zone, parse_possessions
from tests.parse.helpers import AWAY, HOME, PbpBuilder


def test_made_fg_ends_and_flips_possession() -> None:
    b = PbpBuilder()
    b.add(
        1,
        "PT11M00.00S",
        HOME,
        1,
        "Made Shot",
        sub_type="Jump Shot",
        description="P1 Jump Shot (2 PTS)",
        score_home="2",
        score_away="0",
        shot_value=2,
        shot_distance=15,
        is_field_goal=1,
        location="h",
    )
    b.add(
        1,
        "PT10M40.00S",
        AWAY,
        2,
        "Made Shot",
        sub_type="Jump Shot",
        description="P2 Jump Shot (2 PTS)",
        score_home="2",
        score_away="2",
        shot_value=2,
        shot_distance=14,
        is_field_goal=1,
        location="v",
    )
    out = parse_possessions(b.df())
    assert out.height == 2
    assert out["off_team"].to_list() == [HOME, AWAY]
    assert out["outcome"].to_list() == ["FGM2", "FGM2"]
    assert out["pts"].to_list() == [2, 2]


def test_offensive_rebound_continues_same_possession() -> None:
    b = PbpBuilder()
    b.add(
        1,
        "PT11M00.00S",
        HOME,
        1,
        "Missed Shot",
        sub_type="Jump Shot",
        description="MISS P1 Jump Shot",
        score_home="0",
        score_away="0",
        shot_value=2,
        shot_distance=15,
        is_field_goal=1,
        location="h",
    )
    b.add(
        1,
        "PT10M58.00S",
        HOME,
        2,
        "Rebound",
        sub_type="Normal Rebound",
        description="P2 REBOUND (Off:1 Def:0)",
        score_home="0",
        score_away="0",
        location="h",
    )
    b.add(
        1,
        "PT10M50.00S",
        HOME,
        2,
        "Made Shot",
        sub_type="Layup Shot",
        description="P2 Layup (2 PTS)",
        score_home="2",
        score_away="0",
        shot_value=2,
        shot_distance=2,
        is_field_goal=1,
        location="h",
    )
    # opponent event so home/away can both be derived
    b.add(
        1,
        "PT10M30.00S",
        AWAY,
        3,
        "Turnover",
        sub_type="Bad Pass",
        description="P3 Bad Pass Turnover",
        score_home="2",
        score_away="0",
        location="v",
    )
    out = parse_possessions(b.df())
    # exactly one HOME trip (the oreb continuation), not two
    home_trips = out.filter(out["off_team"] == HOME)
    assert home_trips.height == 1
    assert home_trips["oreb"].to_list() == [True]
    assert home_trips["outcome"].to_list() == ["FGM2"]
    assert home_trips["pts"].to_list() == [2]


def test_defensive_rebound_flips_possession() -> None:
    b = PbpBuilder()
    b.add(
        1,
        "PT11M00.00S",
        HOME,
        1,
        "Missed Shot",
        sub_type="Jump Shot",
        description="MISS P1 Jump Shot",
        score_home="0",
        score_away="0",
        shot_value=2,
        shot_distance=15,
        is_field_goal=1,
        location="h",
    )
    b.add(
        1,
        "PT10M58.00S",
        AWAY,
        2,
        "Rebound",
        sub_type="Normal Rebound",
        description="P2 REBOUND (Off:0 Def:1)",
        score_home="0",
        score_away="0",
        location="v",
    )
    b.add(
        1,
        "PT10M50.00S",
        AWAY,
        2,
        "Made Shot",
        sub_type="Layup Shot",
        description="P2 Layup (2 PTS)",
        score_home="0",
        score_away="2",
        shot_value=2,
        shot_distance=2,
        is_field_goal=1,
        location="v",
    )
    out = parse_possessions(b.df())
    assert out.height == 2
    assert out["off_team"].to_list() == [HOME, AWAY]
    assert out["outcome"].to_list() == ["FGA_miss", "FGM2"]
    assert out[0, "oreb"] is False


def test_turnover_flips_possession() -> None:
    b = PbpBuilder()
    b.add(
        1,
        "PT11M00.00S",
        HOME,
        1,
        "Turnover",
        sub_type="Bad Pass",
        description="P1 Bad Pass Turnover",
        score_home="0",
        score_away="0",
        location="h",
    )
    b.add(
        1,
        "PT10M50.00S",
        AWAY,
        2,
        "Made Shot",
        sub_type="Layup Shot",
        description="P2 Layup (2 PTS)",
        score_home="0",
        score_away="2",
        shot_value=2,
        shot_distance=2,
        is_field_goal=1,
        location="v",
    )
    out = parse_possessions(b.df())
    assert out.height == 2
    assert out["outcome"].to_list() == ["TOV", "FGM2"]
    assert out["off_team"].to_list() == [HOME, AWAY]
    assert out[0, "fta"] == 0
    assert out[0, "pts"] == 0


def test_team_level_turnover_quirk_resolves_via_player_id() -> None:
    """team_id=0 team-level turnovers carry the real team id in player_id."""
    b = PbpBuilder()
    b.add(1, "PT12M00.00S", HOME, 1, "Jump Ball", description="Jump Ball", location="h")
    b.add(
        1,
        "PT11M00.00S",
        0,
        HOME,
        "Turnover",
        sub_type="Shot Clock Turnover",
        description="TEAM Turnover: Shot Clock",
        score_home="0",
        score_away="0",
        location="h",
    )
    b.add(
        1,
        "PT10M50.00S",
        AWAY,
        2,
        "Made Shot",
        sub_type="Layup Shot",
        description="P2 Layup (2 PTS)",
        score_home="0",
        score_away="2",
        shot_value=2,
        shot_distance=2,
        is_field_goal=1,
        location="v",
    )
    out = parse_possessions(b.df())
    assert out["off_team"].to_list() == [HOME, AWAY]
    assert out["outcome"].to_list() == ["TOV", "FGM2"]


def test_and_one_stays_in_same_trip_as_fgm_not_ft_trip() -> None:
    b = PbpBuilder()
    b.add(
        1,
        "PT11M00.00S",
        HOME,
        1,
        "Made Shot",
        sub_type="Layup Shot",
        description="P1 Layup (2 PTS)",
        score_home="2",
        score_away="0",
        shot_value=2,
        shot_distance=2,
        is_field_goal=1,
        location="h",
    )
    b.add(
        1,
        "PT10M59.00S",
        AWAY,
        2,
        "Foul",
        sub_type="Shooting",
        description="P2 S.FOUL",
        score_home="2",
        score_away="0",
        location="v",
    )
    b.add(
        1,
        "PT10M58.00S",
        HOME,
        1,
        "Free Throw",
        sub_type="Free Throw 1 of 1",
        description="P1 Free Throw 1 of 1 (3 PTS)",
        score_home="3",
        score_away="0",
        location="h",
    )
    b.add(
        1,
        "PT10M40.00S",
        AWAY,
        3,
        "Turnover",
        sub_type="Bad Pass",
        description="P3 Bad Pass Turnover",
        score_home="3",
        score_away="0",
        location="v",
    )
    out = parse_possessions(b.df())
    home_trip = out.filter(out["off_team"] == HOME)
    assert home_trip.height == 1
    assert home_trip["outcome"].to_list() == ["FGM2"]
    assert home_trip["pts"].to_list() == [3]
    assert home_trip["fta"].to_list() == [1]


def test_ft_trip_without_preceding_fgm_is_ft_trip_outcome() -> None:
    b = PbpBuilder()
    b.add(
        1,
        "PT11M00.00S",
        AWAY,
        2,
        "Foul",
        sub_type="Shooting",
        description="P2 S.FOUL",
        score_home="0",
        score_away="0",
        location="v",
    )
    b.add(
        1,
        "PT10M59.00S",
        HOME,
        1,
        "Free Throw",
        sub_type="Free Throw 1 of 2",
        description="P1 Free Throw 1 of 2 (1 PTS)",
        score_home="1",
        score_away="0",
        location="h",
    )
    b.add(
        1,
        "PT10M58.00S",
        HOME,
        1,
        "Free Throw",
        sub_type="Free Throw 2 of 2",
        description="P1 Free Throw 2 of 2 (2 PTS)",
        score_home="2",
        score_away="0",
        location="h",
    )
    b.add(
        1,
        "PT10M40.00S",
        AWAY,
        3,
        "Turnover",
        sub_type="Bad Pass",
        description="P3 Bad Pass Turnover",
        score_home="2",
        score_away="0",
        location="v",
    )
    out = parse_possessions(b.df())
    home_trip = out.filter(out["off_team"] == HOME)
    assert home_trip.height == 1
    assert home_trip["outcome"].to_list() == ["FT_trip"]
    assert home_trip["fta"].to_list() == [2]
    assert home_trip["pts"].to_list() == [2]


def test_missed_last_ft_waits_for_rebound_before_closing() -> None:
    b = PbpBuilder()
    b.add(
        1,
        "PT11M00.00S",
        AWAY,
        2,
        "Foul",
        sub_type="Shooting",
        description="P2 S.FOUL",
        score_home="0",
        score_away="0",
        location="v",
    )
    b.add(
        1,
        "PT10M59.00S",
        HOME,
        1,
        "Free Throw",
        sub_type="Free Throw 1 of 2",
        description="MISS P1 Free Throw 1 of 2",
        score_home="0",
        score_away="0",
        location="h",
    )
    b.add(
        1,
        "PT10M58.00S",
        HOME,
        1,
        "Free Throw",
        sub_type="Free Throw 2 of 2",
        description="MISS P1 Free Throw 2 of 2",
        score_home="0",
        score_away="0",
        location="h",
    )
    b.add(
        1,
        "PT10M57.00S",
        AWAY,
        2,
        "Rebound",
        sub_type="Normal Rebound",
        description="P2 REBOUND (Off:0 Def:1)",
        score_home="0",
        score_away="0",
        location="v",
    )
    b.add(
        1,
        "PT10M50.00S",
        AWAY,
        2,
        "Made Shot",
        sub_type="Layup Shot",
        description="P2 Layup (2 PTS)",
        score_home="0",
        score_away="2",
        shot_value=2,
        shot_distance=2,
        is_field_goal=1,
        location="v",
    )
    out = parse_possessions(b.df())
    # one closed HOME trip (the missed FT trip), then an AWAY trip
    assert out["off_team"].to_list() == [HOME, AWAY]
    assert out["outcome"].to_list() == ["FT_trip", "FGM2"]
    assert out[0, "pts"] == 0
    assert out[0, "fta"] == 2


def test_end_of_period_force_closes_dangling_trip() -> None:
    b = PbpBuilder()
    b.add(
        1,
        "PT00M05.00S",
        HOME,
        1,
        "Missed Shot",
        sub_type="Jump Shot",
        description="MISS P1 Jump Shot",
        score_home="0",
        score_away="0",
        shot_value=3,
        shot_distance=26,
        is_field_goal=1,
        location="h",
    )
    b.add(
        1,
        "PT00M00.00S",
        0,
        0,
        "period",
        sub_type="end",
        description="End of 1st Period",
        score_home="0",
        score_away="0",
    )
    b.add(
        2,
        "PT12M00.00S",
        0,
        0,
        "period",
        sub_type="start",
        description="Start of 2nd Period",
        score_home="0",
        score_away="0",
    )
    b.add(
        2,
        "PT11M50.00S",
        AWAY,
        2,
        "Made Shot",
        sub_type="Layup Shot",
        description="P2 Layup (2 PTS)",
        score_home="0",
        score_away="2",
        shot_value=2,
        shot_distance=2,
        is_field_goal=1,
        location="v",
    )
    out = parse_possessions(b.df())
    assert out.height == 2
    assert out[0, "outcome"] == "FGA_miss"
    assert out[0, "clock_end"] == 0.0
    assert out[0, "period"] == 1
    assert out[1, "period"] == 2


def test_assist_parsed_from_description() -> None:
    b = PbpBuilder()
    b.add(
        1,
        "PT11M00.00S",
        HOME,
        1,
        "Made Shot",
        sub_type="Jump Shot",
        description="Tatum Jump Shot (2 PTS) (Smart 1 AST)",
        score_home="2",
        score_away="0",
        shot_value=2,
        shot_distance=15,
        is_field_goal=1,
        location="h",
        player_name="Tatum",
    )
    b.add(1, "PT10M58.00S", HOME, 7, "Substitution", description="SUB: x FOR y")
    b.add(
        1,
        "PT10M40.00S",
        AWAY,
        2,
        "Turnover",
        sub_type="Bad Pass",
        description="P2 Bad Pass Turnover",
        score_home="2",
        score_away="0",
        location="v",
    )
    # Smart's player_id/name must appear somewhere for the lookup to resolve.
    b.rows.insert(
        0,
        {
            "game_id": b.game_id,
            "action_number": 0,
            "period": 1,
            "clock": "PT12M00.00S",
            "team_id": HOME,
            "player_id": 9,
            "player_name": "Smart",
            "action_type": "period",
            "sub_type": "start",
            "description": "noop",
            "score_home": "0",
            "score_away": "0",
            "points_total": 0,
            "shot_result": "",
            "shot_value": 0,
            "shot_distance": 0,
            "is_field_goal": 0,
            "location": "h",
        },
    )
    out = parse_possessions(b.df())
    home_trip = out.filter(out["off_team"] == HOME)
    assert home_trip["assister_id"].to_list() == [9]


def test_shot_zone_rim_mid_and_three() -> None:
    assert _shot_zone(2, 2, "Layup Shot", "P1 Layup") == "rim"
    assert _shot_zone(2, 12, "Jump Shot", "P1 Jump Shot") == "mid"
    assert _shot_zone(3, 24, "Jump Shot", "P1 3PT Jump Shot") == "above3"
    assert _shot_zone(3, 22, "Corner Jump Shot", "P1 Corner 3") == "corner3"


def _two_fts(descs: tuple[str, str], player_name: str) -> list[dict]:
    b = PbpBuilder()
    b.add(1, "PT12M00.00S", HOME, 1, "Jump Ball", description="Jump Ball", location="h")
    b.add(1, "PT10M00.00S", AWAY, 2, "Foul", description="P2 foul", location="v")
    for i, d in enumerate(descs, start=1):
        b.add(
            1, "PT09M55.00S", HOME, 1, "Free Throw", sub_type=f"Free Throw {i} of 2",
            description=d, player_name=player_name, score_home="0", score_away="0", location="h",
        )  # fmt: skip
    return parse_possessions(b.df()).to_dicts()


def test_made_free_throws_of_a_player_named_missi_are_not_misses() -> None:
    """Regression (F6): "Missi Free Throw 1 of 2 (3 PTS)" starts with MISS but is a make."""
    rows = _two_fts(("Missi Free Throw 1 of 2 (3 PTS)", "Missi Free Throw 2 of 2 (4 PTS)"), "Missi")
    assert len(rows) == 1
    assert rows[0]["pts"] == 2 and rows[0]["fta"] == 2 and rows[0]["outcome"] == "FT_trip"


def test_real_missed_free_throw_marker_still_counts_as_miss() -> None:
    rows = _two_fts(("MISS Missi Free Throw 1 of 2", "Missi Free Throw 2 of 2 (3 PTS)"), "Missi")
    assert rows[0]["pts"] == 1


def test_team_turnover_with_no_team_anywhere_attaches_to_the_offense() -> None:
    """Regression (F7): team_id 0 and player_id 0 must not create an off_team=0 possession."""
    b = PbpBuilder()
    b.add(1, "PT12M00.00S", HOME, 1, "Jump Ball", description="Jump Ball", location="h")
    b.add(1, "PT11M00.00S", HOME, 1, "Missed Shot", sub_type="Jump Shot", description="MISS P1",
          shot_value=2, shot_distance=15, is_field_goal=1, location="h")  # fmt: skip
    b.add(1, "PT10M58.00S", 0, 0, "Turnover", sub_type="Excess Timeout Turnover",
          description="Excess Timeout Turnover", location="h")  # fmt: skip
    b.add(1, "PT10M40.00S", AWAY, 2, "Made Shot", sub_type="Layup Shot",
          description="P2 Layup (2 PTS)", score_home="0", score_away="2", shot_value=2,
          shot_distance=2, is_field_goal=1, location="v")  # fmt: skip
    out = parse_possessions(b.df())
    assert 0 not in out["off_team"].to_list()
    assert out["off_team"].to_list() == [HOME, AWAY]
    assert out["outcome"].to_list() == ["TOV", "FGM2"]


def test_team_turnover_with_no_team_and_no_open_trip_is_skipped() -> None:
    b = PbpBuilder()
    b.add(1, "PT12M00.00S", HOME, 1, "Jump Ball", description="Jump Ball", location="h")
    b.add(1, "PT11M59.00S", AWAY, 2, "Jump Ball", description="Jump Ball", location="v")
    b.add(1, "PT11M58.00S", 0, 0, "Turnover", sub_type="Shot Clock Turnover",
          description="Shot Clock Turnover", location="h")  # fmt: skip
    out = parse_possessions(b.df())
    assert 0 not in out["off_team"].to_list()
