"""As-of / no-leakage / permutation-invariance tests for the rung-4 player-pooled features.

Synthetic 3-game DuckDB (no torch import, so this runs with the normal suite).
"""

from __future__ import annotations

import datetime as dt

import polars as pl

from nba.db.connect import connect
from nba.ingest.cache import insert_rows
from research.features.possession_step_features import (
    PLAYER_STAT_PRIORS,
    POSSESSION_STEP_FEATURE_COLUMNS,
    POSSESSION_STEP_NEUTRAL,
    POSSESSION_STEP_PLAYER_FEATURE_COLUMNS,
    build_possession_step_training_frame,
)

LINEUP_A = [11, 12, 13, 14, 15]
LINEUP_B = [21, 22, 23, 24, 25]


def make_con(  # type: ignore[no-untyped-def]
    g3_outcome: str = "FGA_miss",
    g2_outcome: str = "FGM2",
    shuffle: bool = False,
    null_lineup_game: str | None = None,
):
    """Synthetic 3-game in-memory DB (open connection; caller closes)."""
    con = connect(":memory:")
    games = pl.DataFrame(
        {
            "game_id": ["g1", "g2", "g3"],
            "game_date": [dt.date(2024, 1, d) for d in (1, 3, 5)],
            "season": [2024] * 3,
            "home_team": [1, 2, 1],
            "away_team": [2, 1, 2],
            "home_pts": [100, 100, 100],
            "away_pts": [90, 90, 90],
        }
    )
    insert_rows(con, "games", games.columns, games)
    rows = []
    idx = 0
    for gid, outcome in (("g1", "FGM3"), ("g2", g2_outcome), ("g3", g3_outcome)):
        for k in range(40):
            off_team = 1 if k % 2 == 0 else 2
            lineup = LINEUP_A if off_team == 1 else LINEUP_B
            other = LINEUP_B if off_team == 1 else LINEUP_A
            if shuffle:
                lineup = lineup[::-1]
            null = null_lineup_game == gid
            rows.append(
                {
                    "game_id": gid,
                    "poss_idx": idx,
                    "period": 1,
                    "off_team": off_team,
                    "def_team": 3 - off_team,
                    "off_players": None if null else lineup,
                    "def_players": None if null else other,
                    "outcome": outcome,
                    # player 11 is the shooter on all of team 1's trips
                    "shooter_id": lineup[0] if off_team == 1 else lineup[1],
                    "shot_zone": "above3" if outcome == "FGM3" else "rim",
                    "pts": 3 if outcome == "FGM3" else 0,
                }
            )
            idx += 1
    df = pl.DataFrame(rows, schema_overrides={"off_players": pl.List(pl.Int64)})
    insert_rows(con, "possessions", df.columns, df)
    return con


def _build(**kw) -> pl.DataFrame:  # type: ignore[no-untyped-def]
    con = make_con(**kw)
    try:
        return build_possession_step_training_frame(con)
    finally:
        con.close()


def _g(frame: pl.DataFrame, gid: str) -> pl.DataFrame:
    return frame.filter(pl.col("game_id") == gid).sort("poss_idx")


def test_columns_present_and_no_nulls() -> None:
    f = _build()
    for c in POSSESSION_STEP_FEATURE_COLUMNS:
        assert c in f.columns
    assert (
        f.select(POSSESSION_STEP_PLAYER_FEATURE_COLUMNS).null_count().sum_horizontal().item() == 0
    )


def test_first_game_is_exactly_the_league_prior() -> None:
    g1 = _g(_build(), "g1")
    for c in POSSESSION_STEP_PLAYER_FEATURE_COLUMNS:
        assert (g1[c] - POSSESSION_STEP_NEUTRAL[c]).abs().max() < 1e-6, c


def test_later_game_moves_off_prior_and_logn_counts_history() -> None:
    f = _build()
    g2 = _g(f, "g2")
    # player 11 took every team-1 shot in g1 (all above3) -> usage max rises
    assert g2["off_pa_usage_max"].max() > PLAYER_STAT_PRIORS["usage"] + 0.01
    assert g2["off_pa_zmix_above3_max"].max() > PLAYER_STAT_PRIORS["zmix_above3"] + 0.01
    assert g2["off_pa_logn_min"].min() > 0.0


def test_no_leakage_future_and_own_outcomes_do_not_touch_earlier_rows() -> None:
    base = _build()
    changed_future = _build(g3_outcome="TOV")
    changed_self = _build(g2_outcome="TOV")
    cols = ["game_id", "poss_idx", *POSSESSION_STEP_FEATURE_COLUMNS]
    for gid in ("g1", "g2"):
        assert _g(base, gid).select(cols).equals(_g(changed_future, gid).select(cols))
    # g2's OWN outcomes never leak into g2's features; g1 untouched too
    for gid in ("g1", "g2"):
        assert _g(base, gid).select(cols).equals(_g(changed_self, gid).select(cols))
    # ... but they do reach the next game (the as-of window is working, not frozen)
    assert not _g(base, "g3").select(cols).equals(_g(changed_self, "g3").select(cols))


def test_pooling_is_permutation_invariant() -> None:
    cols = ["game_id", "poss_idx", *POSSESSION_STEP_PLAYER_FEATURE_COLUMNS]
    assert _build().select(cols).equals(_build(shuffle=True).select(cols))


def test_null_lineup_falls_back_to_neutral() -> None:
    g2 = _g(_build(null_lineup_game="g2"), "g2")
    for c in POSSESSION_STEP_PLAYER_FEATURE_COLUMNS:
        assert (g2[c] - POSSESSION_STEP_NEUTRAL[c]).abs().max() < 1e-6, c
