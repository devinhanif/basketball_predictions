"""Game-order sort (nba/parse/ordering.py) and the possession parser's use of it.

Regression for roadmap item 8 / ADR 0002 item 5: the feed files post-hoc corrections after
the end of the period with the right clock but a late ``action_number``. Sorted by action
number, a late rebound closed a trip that had started after it was stamped, giving a
possession that ends after it starts.
"""

from __future__ import annotations

import pytest

import nba.parse.possessions as possessions_mod
from nba.parse.ordering import in_game_order
from nba.parse.possessions import parse_possessions
from tests.parse.helpers import AWAY, HOME, PbpBuilder


def _shot(
    b: PbpBuilder, period: int, clock: str, team: int, pid: int, made: bool, **kw: object
) -> None:
    b.add(
        period,
        clock,
        team,
        pid,
        "Made Shot" if made else "Missed Shot",
        sub_type="Jump Shot",
        description=f"P{pid} Jump Shot" + (" (2 PTS)" if made else ""),
        shot_value=2,
        shot_distance=15,
        is_field_goal=1,
        location="h" if team == HOME else "v",
        **kw,  # type: ignore[arg-type]
    )


def _rebound(b: PbpBuilder, period: int, clock: str, team: int, pid: int) -> None:
    b.add(
        period,
        clock,
        team,
        pid,
        "Rebound",
        description=f"P{pid} REBOUND",
        location="h" if team == HOME else "v",
    )


def appended_correction_game() -> PbpBuilder:
    """A misses, B rebounds (filed LATE, after the period end), B scores, A misses again."""
    b = PbpBuilder()
    _shot(b, 1, "PT05M00.00S", HOME, 1, False)
    _shot(b, 1, "PT04M45.00S", AWAY, 11, True, score_home="0", score_away="2")
    _shot(b, 1, "PT04M30.00S", HOME, 2, False, score_home="0", score_away="2")
    b.add(1, "PT00M00.00S", 0, 0, "period", sub_type="End", description="End of 1st Period")
    # post-hoc correction: right period and clock (4:58), but the highest action number
    _rebound(b, 1, "PT04M58.00S", AWAY, 12)
    return b


def _durations_ok(poss) -> bool:  # type: ignore[no-untyped-def]
    return bool((poss["clock_end"] <= poss["clock_start"]).all())


def test_appended_rebound_gives_no_negative_possession(monkeypatch: pytest.MonkeyPatch) -> None:
    pbp = appended_correction_game().df()

    # Old behaviour (sort by period, action_number): the late rebound closes HOME's second
    # trip (started 4:30) at 4:58, so it ends after it starts.
    monkeypatch.setattr(
        possessions_mod, "in_game_order", lambda df: df.sort(["period", "action_number"])
    )
    old = parse_possessions(pbp)
    assert not _durations_ok(old)
    assert old["clock_end"].to_list()[-1] == 298.0 and old["clock_start"].to_list()[-1] == 270.0
    monkeypatch.undo()

    new = parse_possessions(pbp)
    assert _durations_ok(new)
    assert new["off_team"].to_list() == [HOME, AWAY, HOME]
    # the rebound now ends HOME's first trip where it happened (5:00 -> 4:58)
    assert new["clock_start"].to_list() == [300.0, 285.0, 270.0]
    assert new["clock_end"].to_list() == [298.0, 270.0, 0.0]
    # chronological, no overlaps: each start is at or before the previous end
    starts, ends = new["clock_start"].to_list(), new["clock_end"].to_list()
    assert all(starts[i + 1] <= ends[i] for i in range(len(starts) - 1))


def test_poss_idx_follows_game_order_not_feed_order() -> None:
    new = parse_possessions(appended_correction_game().df())
    assert new["poss_idx"].to_list() == [0, 1, 2]
    assert new["clock_start"].to_list() == sorted(new["clock_start"].to_list(), reverse=True)


def test_ordering_sorts_by_period_then_clock_descending() -> None:
    b = PbpBuilder()
    _shot(b, 2, "PT03M00.00S", HOME, 1, False)
    _shot(b, 1, "PT01M00.00S", HOME, 2, False)
    _shot(b, 1, "PT09M00.00S", HOME, 3, False)
    got = in_game_order(b.df())
    assert got["period"].to_list() == [1, 1, 2]
    assert got["clock"].to_list() == ["PT09M00.00S", "PT01M00.00S", "PT03M00.00S"]


def test_tie_rule_substitution_first_then_action_number() -> None:
    b = PbpBuilder()
    _shot(b, 1, "PT05M00.00S", HOME, 1, False)  # an 1
    _rebound(b, 1, "PT05M00.00S", HOME, 2)  # an 2
    b.add(1, "PT05M00.00S", HOME, 3, "Substitution", description="SUB: P9 FOR P3")  # an 3
    _shot(b, 1, "PT05M00.00S", HOME, 2, True)  # an 4
    got = in_game_order(b.df())
    # the substitution jumps ahead of the other events at that instant; the rest keep their
    # feed sequence (miss, rebound, putback)
    assert got["action_number"].to_list() == [3, 1, 2, 4]


def test_tie_rule_keeps_miss_rebound_putback_in_one_possession() -> None:
    """Miss, offensive rebound and putback all stamped 5:00 stay one trip with oreb."""
    b = PbpBuilder()
    _shot(b, 1, "PT05M00.00S", HOME, 1, False)
    _rebound(b, 1, "PT05M00.00S", HOME, 2)
    _shot(b, 1, "PT05M00.00S", HOME, 2, True, score_home="2", score_away="0")
    _shot(b, 1, "PT04M40.00S", AWAY, 11, False, score_home="2", score_away="0")
    out = parse_possessions(b.df())
    assert out["off_team"].to_list() == [HOME, AWAY]
    assert out["outcome"].to_list() == ["FGM2", "FGA_miss"]
    assert out["oreb"].to_list() == [True, False]


def test_tie_rule_keeps_free_throw_sequence() -> None:
    b = PbpBuilder()
    for n in (1, 2):
        b.add(
            1,
            "PT04M21.00S",
            HOME,
            1,
            "Free Throw",
            sub_type=f"Free Throw {n} of 2",
            description=f"P1 Free Throw {n} of 2 (1 PTS)",
            player_name="P1",
            location="h",
        )
    _shot(b, 1, "PT04M05.00S", AWAY, 11, True, score_home="2", score_away="2")
    out = parse_possessions(b.df())
    assert out["outcome"].to_list()[:2] == ["FT_trip", "FGM2"]
    assert out["fta"].to_list()[0] == 2 and out["pts"].to_list()[0] == 2


def test_unparseable_clock_sorts_to_period_end() -> None:
    b = PbpBuilder()
    _shot(b, 1, "", HOME, 1, False)
    _shot(b, 1, "PT05M00.00S", HOME, 2, False)
    assert in_game_order(b.df())["clock"].to_list() == ["PT05M00.00S", ""]
