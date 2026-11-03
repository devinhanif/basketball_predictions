"""As-of logic of the MINUTES_DECISION gate counts (synthetic frames; no database)."""

from __future__ import annotations

import pandas as pd

from research.eval import minutes_decision_gate as G


def _games() -> pd.DataFrame:
    # team 1 plays g1..g4; coach changes A -> B at g3; season 2023 starts at g4
    return pd.DataFrame(
        {
            "game_id": ["g1", "g2", "g3", "g4"],
            "game_date": pd.to_datetime(["2022-11-01", "2022-11-02", "2022-11-05", "2023-10-25"]),
            "season": [2022, 2022, 2022, 2023],
            "home_team": [1, 2, 1, 1],
            "away_team": [2, 1, 2, 2],
            "home_pts": [100, 90, 130, 100],
            "away_pts": [95, 100, 100, 99],
        }
    )


def _tcg() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "game_id": ["g1", "g1", "g2", "g2", "g3", "g3", "g4", "g4"],
            "team_id": [1, 2, 1, 2, 1, 2, 1, 2],
            "coach_name": ["A", "X", "A", "X", "B", "X", "C", "A"],
        }
    )


def test_asof_coach_is_previous_game_within_season_and_opener_reference() -> None:
    tg = G.team_games(_games(), _tcg())
    t1 = tg[tg.team_id == 1].set_index("game_id")
    assert pd.isna(t1.loc["g1", "prev_coach"])
    assert t1.loc["g1", "asof_coach"] == "A"  # first game: season reference coach
    assert t1.loc["g3", "asof_coach"] == "A"  # change seen one game late
    assert t1.loc["g4", "asof_coach"] == "C"  # opener: reference coach
    assert bool(t1.loc["g2", "b2b"]) is True
    assert bool(t1.loc["g3", "b2b"]) is False


def test_coach_history_counts_only_strictly_earlier_dates() -> None:
    tg = G.team_games(_games(), _tcg())
    ch = G.coach_history(tg).set_index(["game_id", "team_id"])
    assert ch.loc[("g1", 1), "ch_n"] == 0
    assert ch.loc[("g2", 1), "ch_n"] == 1  # A coached g1 only before g2
    assert ch.loc[("g3", 1), "ch_n"] == 2  # as-of coach A: g1, g2
    assert ch.loc[("g3", 1), "ch_blow20"] == 0
    # coach A moves to team 2 for g4: his history follows him (g1, g2 with team 1)
    assert ch.loc[("g4", 2), "ch_n"] == 2


def test_familiarity_with_and_against_are_as_of() -> None:
    games, tcg = _games(), _tcg()
    tg = G.team_games(games, tcg)
    box = pd.DataFrame(
        {
            "game_id": ["g1", "g2", "g3", "g4"],
            "player_id": [7, 7, 7, 7],
            "team_id": [2, 2, 2, 2],
            "minutes": [20.0, 21.0, 22.0, 30.0],
        }
    )
    rows = box[["game_id", "player_id", "team_id"]].merge(
        games[["game_id", "game_date", "season"]], on="game_id"
    )
    rows = rows.merge(tg[["game_id", "team_id", "asof_coach"]], on=["game_id", "team_id"])
    fam = G.familiarity(rows, box, tg).set_index("game_id")
    # player 7: for X (team 2) in g1-g3; vs A in g1, g2; vs B in g3; under A in g4
    assert fam.loc["g1", "pc_with_n"] == 0
    assert fam.loc["g3", "pc_with_n"] == 2
    assert fam.loc["g4", "pc_with_n"] == 0
    assert fam.loc["g4", "pc_against_n"] == 2  # g1 and g2 against A; g3 was against B


def test_familiarity_ignores_a_planted_future_game() -> None:
    games, tcg = _games(), _tcg()
    box = pd.DataFrame(
        {
            "game_id": ["g1", "g2", "g3"],
            "player_id": [7, 7, 7],
            "team_id": [2, 2, 2],
            "minutes": [1.0, 1.0, 1.0],
        }
    )
    tg = G.team_games(games, tcg)
    rows = (
        box[["game_id", "player_id", "team_id"]]
        .merge(games[["game_id", "game_date", "season"]], on="game_id")
        .merge(tg[["game_id", "team_id", "asof_coach"]], on=["game_id", "team_id"])
    )
    a = G.familiarity(rows, box, tg)
    box2 = pd.concat(
        [
            box,
            pd.DataFrame({"game_id": ["g4"], "player_id": [7], "team_id": [2], "minutes": [99.0]}),
        ]
    )
    b = G.familiarity(rows, box2, tg)
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))
