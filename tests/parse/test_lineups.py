"""Unit tests for the lineup/stint tracker (nba/parse/lineups.py).

CLAUDE.md flags the parser family as the highest-risk component
("validate hard"); this module additionally carries a hard time-box.
Each test isolates one piece of the documented heuristic (module
docstring in nba/parse/lineups.py) so a future change that breaks one
piece fails loudly rather than only showing up as a lower aggregate
clean-5-5 rate.
"""

from __future__ import annotations

import polars as pl
import pytest

from nba.parse.lineups import (
    attach_lineups_to_possessions,
    clean_lineup_rate,
    reconcile_stint_minutes,
    stint_minutes,
    track_lineups,
)
from nba.parse.possessions import parse_possessions
from tests.fixtures.loader import load_pbp
from tests.parse.helpers import AWAY, HOME, PbpBuilder, infer_approx_starters

HOME_STARTERS = [1, 2, 3, 4, 5]
AWAY_STARTERS = [11, 12, 13, 14, 15]
STARTERS = {HOME: HOME_STARTERS, AWAY: AWAY_STARTERS}


def _shot(b: PbpBuilder, period: str | int, clock: str, team: int, player: int) -> PbpBuilder:
    return b.add(
        period,
        clock,
        team,
        player,
        "Made Shot",
        sub_type="Jump Shot",
        description=f"P{player} Jump Shot (2 PTS)",
        shot_value=2,
        shot_distance=15,
        is_field_goal=1,
        location="h" if team == HOME else "v",
    )


def _sub(
    b: PbpBuilder, period: int, clock: str, team: int, out_id: int, out_name: str, in_name: str
) -> PbpBuilder:
    return b.add(
        period,
        clock,
        team,
        out_id,
        "Substitution",
        description=f"SUB: {in_name} FOR {out_name}",
        player_name=out_name,
    )


def _named(b: PbpBuilder, period: int, clock: str, team: int, player: int, name: str) -> PbpBuilder:
    """A made-shot action that also registers `name` for a player_id, for sub resolution."""
    return b.add(
        period,
        clock,
        team,
        player,
        "Made Shot",
        sub_type="Jump Shot",
        description=f"{name} Jump Shot (2 PTS)",
        player_name=name,
        shot_value=2,
        shot_distance=15,
        is_field_goal=1,
    )


def test_q1_is_seeded_with_starters() -> None:
    b = PbpBuilder()
    _shot(b, 1, "PT11M00.00S", HOME, 1)
    stints = track_lineups(b.df(), STARTERS)
    home_stint = stints.filter((pl.col("team_id") == HOME) & (pl.col("period") == 1))
    assert home_stint.height == 1
    assert sorted(home_stint["players"].to_list()[0]) == sorted(HOME_STARTERS)


def test_substitution_swaps_exactly_one_player() -> None:
    b = PbpBuilder()
    _named(b, 1, "PT11M00.00S", HOME, 1, "A")
    _sub(b, 1, "PT08M00.00S", HOME, 1, "A", "Bench")
    # The name-lookup is built from the whole game up front, so "Bench"
    # resolving to player 50 works even though this confirming action
    # for 50 comes after the sub in the dataframe -- it must, since a
    # bench player can't act before being subbed in.
    _named(b, 1, "PT07M30.00S", HOME, 50, "Bench")
    stints = track_lineups(b.df(), STARTERS)
    home = stints.filter(pl.col("team_id") == HOME).sort("start_clock", descending=True)
    assert home.height == 2
    before = set(home["players"].to_list()[0])
    after = set(home["players"].to_list()[1])
    assert len(before) == 5 and len(after) == 5
    # exactly one player changed: #1 left, the resolved "Bench" player (50) entered.
    assert before - after == {1}
    assert after - before == {50}


def test_unresolved_sub_name_is_a_noop() -> None:
    """An "in" name that never appears elsewhere in the game can't be
    resolved to a player_id; the sub must be skipped rather than shrink
    the five below 5."""
    b = PbpBuilder()
    _shot(b, 1, "PT11M00.00S", HOME, 1)
    _sub(b, 1, "PT08M00.00S", HOME, 1, "P1", "NobodyWeKnow")
    stints = track_lineups(b.df(), STARTERS)
    home = stints.filter(pl.col("team_id") == HOME)
    assert home.height == 1  # no lineup change was recorded
    assert sorted(home["players"].to_list()[0]) == sorted(HOME_STARTERS)


def test_period_boundary_carries_over_then_corrects_from_action() -> None:
    """Player 1 is subbed out mid-Q1 for player 99 (a bench player we
    name so the sub resolves); with no explicit sub event marking the
    reset, player 1 reappears acting at the start of Q2. The carried-
    over five (minus 99, which never acts) must be corrected to include
    1 again."""
    b = PbpBuilder()
    _named(b, 1, "PT11M00.00S", HOME, 1, "Starter1")
    _sub(b, 1, "PT09M00.00S", HOME, 1, "Starter1", "Bench99")
    # Bench99's first confirming action comes after the sub (realistic:
    # a bench player can't act before being subbed in); the name-lookup
    # used to resolve the sub itself is still built from the whole game.
    _named(b, 1, "PT08M30.00S", HOME, 99, "Bench99")
    # Q2 opens with no sub event, but Starter1 (not 99) is back on the floor.
    b.add(
        2,
        "PT11M30.00S",
        HOME,
        1,
        "Made Shot",
        sub_type="Jump Shot",
        description="Starter1 Jump Shot (2 PTS)",
        player_name="Starter1",
        shot_value=2,
        shot_distance=15,
        is_field_goal=1,
    )
    stints = track_lineups(b.df(), STARTERS)
    q2_home = stints.filter((pl.col("team_id") == HOME) & (pl.col("period") == 2)).sort(
        "start_clock", descending=True
    )
    # First Q2 stint is the (wrong) carry-over; it gets corrected at
    # Starter1's first action, producing a second stint containing 1.
    assert q2_home.height == 2
    corrected = q2_home["players"].to_list()[1]
    assert 1 in corrected
    assert len(corrected) == 5


def test_stints_always_have_exactly_five_players() -> None:
    b = PbpBuilder()
    _named(b, 1, "PT11M00.00S", HOME, 1, "A")
    _named(b, 1, "PT10M30.00S", AWAY, 11, "X")
    for i, (out_id, out_name, in_id, in_name) in enumerate(
        [(1, "A", 2, "B"), (2, "B", 3, "C"), (11, "X", 12, "Y")]
    ):
        team = HOME if out_id in (1, 2, 3) else AWAY
        clock = f"PT0{8 - i}M00.00S"
        b.add(
            1,
            clock,
            team,
            out_id,
            "Substitution",
            description=f"SUB: {in_name} FOR {out_name}",
            player_name=out_name,
        )
        b.add(
            1,
            f"PT0{7 - i}M30.00S",
            team,
            in_id,
            "Made Shot",
            sub_type="Jump Shot",
            description=f"{in_name} Jump Shot (2 PTS)",
            player_name=in_name,
            shot_value=2,
            shot_distance=15,
            is_field_goal=1,
        )
    stints = track_lineups(b.df(), STARTERS)
    lengths = stints["players"].list.len().to_list()
    assert all(n == 5 for n in lengths)
    # and no duplicate players within any single stint
    unique_lengths = stints["players"].list.unique().list.len().to_list()
    assert lengths == unique_lengths


def test_determinism() -> None:
    b = PbpBuilder()
    _named(b, 1, "PT11M00.00S", HOME, 1, "A")
    _sub(b, 1, "PT08M00.00S", HOME, 1, "A", "AwayA")  # unresolvable on purpose
    _named(b, 1, "PT07M00.00S", AWAY, 11, "AwayStart")
    pbp = b.df()
    s1 = track_lineups(pbp, STARTERS)
    s2 = track_lineups(pbp, STARTERS)
    assert s1.equals(s2)


def test_attach_lineups_to_possessions_gives_exactly_five() -> None:
    b = PbpBuilder()
    _shot(b, 1, "PT11M00.00S", HOME, 1)
    _shot(b, 1, "PT10M00.00S", AWAY, 11)
    pbp = b.df()
    poss = parse_possessions(pbp)
    stints = track_lineups(pbp, STARTERS)
    attached = attach_lineups_to_possessions(poss, stints)
    assert attached["off_players"].is_null().sum() == 0
    assert attached["def_players"].is_null().sum() == 0
    assert (attached["off_players"].list.len() == 5).all()
    assert (attached["def_players"].list.len() == 5).all()
    assert clean_lineup_rate(attached) == 1.0


def test_clean_rate_on_committed_fixture() -> None:
    """The tiny committed fixture (tests/fixtures/pbp/*.json) has no
    Substitution events, so the whole game should attach a clean 5-5
    lineup -- this is the no-network data-contract check."""
    for game_id, pbp in load_pbp().items():
        starters = infer_approx_starters(pbp)
        if len(starters) != 2 or any(len(v) != 5 for v in starters.values()):
            continue  # fixture game too short to exercise this
        poss = parse_possessions(pbp)
        if poss.is_empty():
            continue
        stints = track_lineups(pbp, starters)
        attached = attach_lineups_to_possessions(poss, stints)
        assert clean_lineup_rate(attached) == 1.0, game_id


def test_stint_minutes_matches_hand_computed_synthetic_total() -> None:
    """One substitution partway through a 12-minute period: the five who
    start play clock_start=720 down to the sub's clock; the subbed-in
    player plays from the sub's clock down to 0."""
    b = PbpBuilder()
    _named(b, 1, "PT11M00.00S", HOME, 1, "A")
    _sub(b, 1, "PT08M00.00S", HOME, 1, "A", "Bench99")
    _named(b, 1, "PT07M30.00S", HOME, 99, "Bench99")
    stints = track_lineups(b.df(), STARTERS)
    minutes = stint_minutes(stints)
    row_1 = minutes.filter((pl.col("team_id") == HOME) & (pl.col("player_id") == 1))
    row_99 = minutes.filter((pl.col("team_id") == HOME) & (pl.col("player_id") == 99))
    # Player 1 played 720s -> 480s (the PT08M00.00S sub) = 240s = 4.0 min.
    assert row_1["minutes"].to_list()[0] == pytest.approx(4.0)
    # Player 99 played 480s -> 0s = 480s = 8.0 min.
    assert row_99["minutes"].to_list()[0] == pytest.approx(8.0)
    # Every starter who was never subbed played the full 12-minute period.
    row_2 = minutes.filter((pl.col("team_id") == HOME) & (pl.col("player_id") == 2))
    assert row_2["minutes"].to_list()[0] == pytest.approx(12.0)


def test_reconcile_stint_minutes_flags_large_mismatch() -> None:
    b = PbpBuilder()
    _shot(b, 1, "PT11M00.00S", HOME, 1)
    stints = track_lineups(b.df(), STARTERS)
    box = pl.DataFrame(
        {
            "game_id": [b.game_id, b.game_id],
            "player_id": [1, 2],
            "minutes": [12.0, 1.0],  # player 2 under-played vs. the 12.0 the stint implies
        }
    )
    out = reconcile_stint_minutes(stints, box)
    row1 = out.filter(pl.col("player_id") == 1)
    row2 = out.filter(pl.col("player_id") == 2)
    assert row1["abs_diff"].to_list()[0] == pytest.approx(0.0)
    assert row2["abs_diff"].to_list()[0] == pytest.approx(11.0)


# --- Real play-by-play validation (gitignored, local-only; mirrors
# tests/parse/test_real_pbp_sanity.py's skip pattern) -----------------

from pathlib import Path  # noqa: E402

DATA_PBP_DIR = Path(__file__).resolve().parents[2] / "data" / "pbp"
_REAL_SAMPLE = sorted(DATA_PBP_DIR.glob("*.parquet"))[:50] if DATA_PBP_DIR.exists() else []

pytestmark_real = pytest.mark.skipif(
    not _REAL_SAMPLE, reason="data/pbp/*.parquet not present locally (gitignored, no network in CI)"
)


@pytestmark_real
def test_clean_5_5_rate_on_real_games_is_high() -> None:
    rates = []
    for path in _REAL_SAMPLE:
        pbp = pl.read_parquet(path)
        starters = infer_approx_starters(pbp)
        if len(starters) != 2 or any(len(v) != 5 for v in starters.values()):
            continue
        poss = parse_possessions(pbp)
        stints = track_lineups(pbp, starters)
        attached = attach_lineups_to_possessions(poss, stints)
        rates.append(clean_lineup_rate(attached))
    assert rates, "no usable real games found"
    mean_rate = sum(rates) / len(rates)
    assert mean_rate >= 0.95, f"mean clean 5-5 rate {mean_rate:.3f} across {len(rates)} games"
    assert min(rates) >= 0.4, f"worst-game clean 5-5 rate {min(rates):.3f}"
