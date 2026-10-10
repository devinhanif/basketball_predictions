"""The ``possessions.oreb`` flag: a trip extended by a player-credited offensive rebound.

Rule (nba/parse/possessions.py, "The oreb flag"): True iff the trip contains a rebound of the
offensive team that carries a team_id. Team rebounds (team_id 0, team parked in player_id)
extend nothing and are not flagged. Segmentation must not depend on the flag.
"""

from __future__ import annotations

import polars as pl

from nba.parse.possessions import parse_possessions
from tests.parse.helpers import AWAY, HOME, PbpBuilder
from tests.parse.test_ordering import _rebound, _shot


def _team_rebound(b: PbpBuilder, period: int, clock: str, team: int) -> None:
    """The feed's deadball team rebound: team_id 0, the team id parked in player_id."""
    b.add(
        period,
        clock,
        0,
        team,
        "Rebound",
        description="TEAM Rebound",
        location="h" if team == HOME else "v",
    )


def _opp_turnover(b: PbpBuilder, clock: str) -> None:
    """An away event closing the home trip, so the home trip is observable."""
    b.add(
        1,
        clock,
        AWAY,
        11,
        "Turnover",
        sub_type="Bad Pass",
        description="P11 Bad Pass Turnover",
        location="v",
    )


def _home(out: pl.DataFrame) -> pl.DataFrame:
    return out.filter(pl.col("off_team") == HOME)


def test_miss_offensive_rebound_made_putback_is_oreb_true_and_fgm() -> None:
    b = PbpBuilder()
    _shot(b, 1, "PT10M00.00S", HOME, 1, False)
    _rebound(b, 1, "PT09M58.00S", HOME, 2)
    _shot(b, 1, "PT09M55.00S", HOME, 2, True, score_home="2", score_away="0")
    _opp_turnover(b, "PT09M40.00S")
    home = _home(parse_possessions(b.df()))
    assert home.height == 1
    assert home["oreb"].to_list() == [True]
    assert home["outcome"].to_list() == ["FGM2"]  # the outcome is the final event
    assert home["pts"].to_list() == [2]


def test_made_shot_with_no_rebound_is_oreb_false() -> None:
    b = PbpBuilder()
    _shot(b, 1, "PT10M00.00S", HOME, 1, True, score_home="2", score_away="0")
    _opp_turnover(b, "PT09M40.00S")
    home = _home(parse_possessions(b.df()))
    assert home["outcome"].to_list() == ["FGM2"]
    assert home["oreb"].to_list() == [False]


def test_turnover_with_no_rebound_is_oreb_false() -> None:
    b = PbpBuilder()
    b.add(
        1,
        "PT10M00.00S",
        HOME,
        1,
        "Turnover",
        sub_type="Bad Pass",
        description="P1 Bad Pass Turnover",
        location="h",
    )
    _shot(b, 1, "PT09M40.00S", AWAY, 11, False)
    out = parse_possessions(b.df())
    assert out["outcome"].to_list()[0] == "TOV"
    assert out["oreb"].to_list()[0] is False


def test_miss_offensive_rebound_then_turnover_is_oreb_true_tov() -> None:
    """An earlier offensive rebound genuinely extended the trip; the outcome is the turnover."""
    b = PbpBuilder()
    _shot(b, 1, "PT10M00.00S", HOME, 1, False)
    _rebound(b, 1, "PT09M58.00S", HOME, 2)
    b.add(
        1,
        "PT09M50.00S",
        HOME,
        2,
        "Turnover",
        sub_type="Bad Pass",
        description="P2 Turnover",
        location="h",
    )
    _shot(b, 1, "PT09M40.00S", AWAY, 11, False)  # an away event so both teams are derivable
    home = _home(parse_possessions(b.df()))
    assert home["outcome"].to_list() == ["TOV"]
    assert home["oreb"].to_list() == [True]


def test_defensive_rebound_is_oreb_false_on_both_trips() -> None:
    b = PbpBuilder()
    _shot(b, 1, "PT10M00.00S", HOME, 1, False)
    _rebound(b, 1, "PT09M58.00S", AWAY, 11)
    _shot(b, 1, "PT09M50.00S", AWAY, 11, True, score_home="0", score_away="2")
    out = parse_possessions(b.df())
    assert out["off_team"].to_list() == [HOME, AWAY]
    assert out["outcome"].to_list() == ["FGA_miss", "FGM2"]
    assert out["oreb"].to_list() == [False, False]


def test_two_offensive_rebounds_in_one_trip_are_one_flag_and_two_counts() -> None:
    b = PbpBuilder()
    _shot(b, 1, "PT10M00.00S", HOME, 1, False)
    _rebound(b, 1, "PT09M58.00S", HOME, 2)
    _shot(b, 1, "PT09M56.00S", HOME, 2, False)
    _rebound(b, 1, "PT09M54.00S", HOME, 3)
    _shot(b, 1, "PT09M52.00S", HOME, 3, True, score_home="2", score_away="0")
    _opp_turnover(b, "PT09M40.00S")
    out = _home(parse_possessions(b.df(), with_counts=True))
    assert out["oreb"].to_list() == [True]
    assert out["n_oreb"].to_list() == [2]


def test_team_rebound_after_a_miss_does_not_set_oreb_nor_split_the_trip() -> None:
    b = PbpBuilder()
    _shot(b, 1, "PT10M00.00S", HOME, 1, False)
    _team_rebound(b, 1, "PT09M58.00S", HOME)
    _shot(b, 1, "PT09M55.00S", HOME, 2, True, score_home="2", score_away="0")
    _opp_turnover(b, "PT09M40.00S")
    home = _home(parse_possessions(b.df(), with_counts=True))
    assert home.height == 1  # still one trip, outcome the putback
    assert home["outcome"].to_list() == ["FGM2"]
    assert home["oreb"].to_list() == [False]
    assert home["n_oreb"].to_list() == [0]


def test_team_rebound_between_free_throws_does_not_set_oreb() -> None:
    b = PbpBuilder()
    for n, miss in ((1, True), (2, False)):
        b.add(
            1,
            f"PT10M0{3 - n}.00S",
            HOME,
            1,
            "Free Throw",
            sub_type=f"Free Throw {n} of 2",
            description=("MISS " if miss else "") + f"P1 Free Throw {n} of 2",
            score_home="0" if miss else "1",
            score_away="0",
            location="h",
        )
        if miss:
            _team_rebound(b, 1, "PT10M01.50S", HOME)
    _opp_turnover(b, "PT09M40.00S")
    home = _home(parse_possessions(b.df()))
    assert home["outcome"].to_list() == ["FT_trip"]
    assert home["fta"].to_list() == [2]
    assert home["oreb"].to_list() == [False]


def test_flag_is_not_a_resegmentation() -> None:
    """Dropping the team rebounds changes no column: the flag fix cannot move poss_idx."""
    b = PbpBuilder()
    _shot(b, 1, "PT10M00.00S", HOME, 1, False)
    _team_rebound(b, 1, "PT09M58.00S", HOME)
    _shot(b, 1, "PT09M55.00S", HOME, 2, False)
    _rebound(b, 1, "PT09M53.00S", HOME, 3)
    _shot(b, 1, "PT09M50.00S", HOME, 3, True, score_home="2", score_away="0")
    _shot(b, 1, "PT09M30.00S", AWAY, 11, False, score_home="2", score_away="0")
    _team_rebound(b, 1, "PT09M28.00S", AWAY)
    _rebound(b, 1, "PT09M27.00S", HOME, 4)
    full = b.df()
    without = full.filter(~((pl.col("action_type") == "Rebound") & (pl.col("team_id") == 0)))
    a, c = parse_possessions(full), parse_possessions(without)
    assert a.height == c.height
    assert a.drop("oreb").equals(c.drop("oreb"))
    assert a["oreb"].to_list() == c["oreb"].to_list()


def test_flag_is_exactly_n_oreb_positive() -> None:
    """No trip carries oreb without a player-credited rebound, and none with one is False."""
    b = PbpBuilder()
    _shot(b, 1, "PT10M00.00S", HOME, 1, False)
    _rebound(b, 1, "PT09M58.00S", HOME, 2)
    b.add(
        1,
        "PT09M50.00S",
        HOME,
        2,
        "Turnover",
        sub_type="Bad Pass",
        description="P2 TO",
        location="h",
    )
    _shot(b, 1, "PT09M40.00S", AWAY, 11, False)
    _team_rebound(b, 1, "PT09M38.00S", AWAY)
    _shot(b, 1, "PT09M35.00S", AWAY, 12, True, score_home="0", score_away="2")
    _shot(b, 1, "PT09M20.00S", HOME, 1, True, score_home="2", score_away="2")
    out = parse_possessions(b.df(), with_counts=True)
    assert out.height == 3
    assert out["oreb"].to_list() == (out["n_oreb"] > 0).to_list() == [True, False, False]
