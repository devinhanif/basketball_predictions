"""Tests for ``research.features.time_decay`` (cold-start method 4: time-decay
season carryover + age curve).

Synthetic in-memory DuckDB only -- no real DB access, fast (CLAUDE.md
"no multi-minute jobs"). Mirrors ``tests/ml/test_player_rebound_assist_features.py``'s
conventions.
"""

from __future__ import annotations

import duckdb
import numpy as np
import polars as pl
import pytest

from nba.db.connect import connect
from research.features.time_decay import (
    TimeDecayConfig,
    blend_as_of_rate,
    build_player_season_decayed_rates,
    decayed_prior_season_rate,
    season_decay_weights,
)


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = connect(":memory:")
    yield c
    c.close()


def _insert_game(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    game_date: str,
    season: int,
    home: int = 1,
    away: int = 2,
) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, "
        "away_pts) VALUES (?, ?, ?, ?, ?, 100, 90)",
        [game_id, game_date, season, home, away],
    )


def _insert_pgs(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    player_id: int,
    team_id: int,
    pts: int,
    minutes: float = 30.0,
) -> None:
    con.execute(
        """
        INSERT INTO player_game_stats
            (game_id, player_id, team_id, minutes, pts, reb, ast, fg3m, stl, blk, tov, starter)
        VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, true)
        """,
        [game_id, player_id, team_id, minutes, pts],
    )


def _row(df: pl.DataFrame, game_id: str, player_id: int) -> dict[str, object]:
    sub = df.filter(pl.col("game_id") == game_id)
    sub = sub.filter(pl.col("player_id") == player_id)
    assert sub.height == 1, f"expected exactly 1 row for {game_id}/{player_id}, got {sub.height}"
    return sub.to_dicts()[0]


# ---------------------------------------------------------------------------
# Pure-math behavior
# ---------------------------------------------------------------------------


def test_season_decay_weights_last_season_is_full_weight() -> None:
    # gap=1 (last season) always gets weight 1.0 regardless of half-life.
    w = season_decay_weights(np.array([1.0]), half_life_seasons=1.5)
    assert np.isclose(w[0], 1.0)


def test_season_decay_weights_monotonically_decreasing_in_gap() -> None:
    gaps = np.array([1.0, 2.0, 3.0, 4.0])
    w = season_decay_weights(gaps, half_life_seasons=1.5)
    assert np.all(np.diff(w) < 0)


def test_decayed_prior_season_rate_empty_returns_nan_zero() -> None:
    rate, eff_n = decayed_prior_season_rate(
        np.array([]), np.array([]), np.array([]), half_life_seasons=1.5
    )
    assert np.isnan(rate)
    assert eff_n == 0.0


def test_decayed_prior_season_rate_weights_recent_season_more() -> None:
    # Two prior seasons with equal sample size but different rates: the
    # more recent one (smaller gap) should pull the blend closer to it.
    rate, eff_n = decayed_prior_season_rate(
        prior_rates=np.array([30.0, 10.0]),  # [last season, two seasons ago]
        prior_ns=np.array([70.0, 70.0]),
        season_gaps=np.array([1.0, 2.0]),
        half_life_seasons=1.5,
    )
    assert eff_n > 0
    assert rate > 20.0  # closer to 30 than to the midpoint 20


def test_blend_converges_to_observed_as_current_n_grows() -> None:
    config = TimeDecayConfig(k=150.0)
    small_n = blend_as_of_rate(
        cur_obs_rate=25.0,
        cur_n=5.0,
        prior_rates=np.array([10.0]),
        prior_ns=np.array([70.0]),
        season_gaps=np.array([1.0]),
        archetype_prior=12.0,
        config=config,
    )
    large_n = blend_as_of_rate(
        cur_obs_rate=25.0,
        cur_n=5000.0,
        prior_rates=np.array([10.0]),
        prior_ns=np.array([70.0]),
        season_gaps=np.array([1.0]),
        archetype_prior=12.0,
        config=config,
    )
    # With a huge current-season sample, the posterior should sit very
    # close to the observed rate, much closer than the small-n posterior.
    assert abs(large_n - 25.0) < abs(small_n - 25.0)
    assert abs(large_n - 25.0) < 0.5


def test_blend_converges_to_carryover_not_archetype_alone_at_cold_start() -> None:
    """The season-opener case (current n -> 0): the blend should land near
    the decayed-prior-season + archetype carryover component, which sits
    BETWEEN the archetype prior and last season's rate -- not collapse to
    the archetype prior alone. This is the concrete Tatum-style fix:
    a star's season opener should not revert to a generic position prior."""
    config = TimeDecayConfig(carryover_decay_weight=0.3, k=150.0)
    last_season_rate = 30.0  # a star coming off a big season
    archetype_prior = 12.0  # generic position-level prior
    result = blend_as_of_rate(
        cur_obs_rate=0.0,
        cur_n=0.0,  # literally the first game of the season: no current obs
        prior_rates=np.array([last_season_rate]),
        prior_ns=np.array([70.0]),
        season_gaps=np.array([1.0]),
        archetype_prior=archetype_prior,
        config=config,
    )
    # Must be well above the archetype prior alone, and below last season's
    # raw rate (carryover_decay_weight regresses partway toward the prior).
    assert archetype_prior < result < last_season_rate


def test_blend_with_zero_history_falls_back_to_archetype_prior() -> None:
    """A true rookie: zero current-season AND zero prior-season observations
    must land exactly on the archetype prior, not nan or zero."""
    config = TimeDecayConfig()
    result = blend_as_of_rate(
        cur_obs_rate=0.0,
        cur_n=0.0,
        prior_rates=np.array([]),
        prior_ns=np.array([]),
        season_gaps=np.array([]),
        archetype_prior=14.5,
        config=config,
    )
    assert np.isclose(result, 14.5)


def test_age_curve_adjustment_moves_blend_in_expected_direction() -> None:
    """Over a plausible age range, being far from peak age pulls the
    carryover component (and thus the overall blend) down relative to
    being at peak age, holding everything else fixed -- monotonic in the
    expected direction (peak > young-aging-up > old-aging-down is not
    required here; only that off-peak < at-peak, per age_curve_multiplier's
    own contract)."""
    config = TimeDecayConfig(carryover_decay_weight=0.0, k=0.0)  # isolate the age effect
    at_peak = blend_as_of_rate(
        cur_obs_rate=0.0,
        cur_n=0.0,
        prior_rates=np.array([20.0]),
        prior_ns=np.array([70.0]),
        season_gaps=np.array([1.0]),
        archetype_prior=0.0,
        config=config,
        age=27.0,
    )
    far_from_peak = blend_as_of_rate(
        cur_obs_rate=0.0,
        cur_n=0.0,
        prior_rates=np.array([20.0]),
        prior_ns=np.array([70.0]),
        season_gaps=np.array([1.0]),
        archetype_prior=0.0,
        config=config,
        age=40.0,
    )
    assert far_from_peak < at_peak


# ---------------------------------------------------------------------------
# DB-level builder + leakage
# ---------------------------------------------------------------------------


def test_future_game_excluded_from_as_of_window(con: duckdb.DuckDBPyConnection) -> None:
    """Plant a future (strictly later) game with an extreme stat value and
    assert it never contributes to an earlier game's current-season
    cumulative numerator/denominator/n -- the cardinal no-leakage rule."""
    _insert_game(con, "g1", "2023-10-24", season=2023)
    _insert_game(con, "g2", "2023-10-26", season=2023)
    _insert_game(con, "g3", "2023-10-28", season=2023)
    _insert_pgs(con, "g1", 101, 1, pts=20)
    _insert_pgs(con, "g2", 101, 1, pts=22)
    # Planted future game with an extreme value that must NOT leak backward.
    _insert_pgs(con, "g3", 101, 1, pts=999)

    df = build_player_season_decayed_rates(con, "pts", "1", archetype_prior=10.0)

    row_g1 = _row(df, "g1", 101)
    row_g2 = _row(df, "g2", 101)
    row_g3 = _row(df, "g3", 101)

    # g1 is the player's first game: zero as-of history.
    assert row_g1["n_cur"] == 0
    # g2's as-of cumulative is exactly g1's pts (20), never touching g3's 999.
    assert row_g2["rate_cur_obs"] == pytest.approx(20.0)
    assert row_g2["n_cur"] == 1
    # g3's as-of cumulative is the mean of g1+g2 (20, 22) = 21, not inflated
    # by its own 999.
    assert row_g3["rate_cur_obs"] == pytest.approx(21.0)
    assert row_g3["n_cur"] == 2


def test_prior_season_totals_excluded_for_same_season_games(con: duckdb.DuckDBPyConnection) -> None:
    """A game in season 2023 must never pull in totals from another 2023
    game as a 'prior season' -- only strictly earlier seasons qualify."""
    _insert_game(con, "g1", "2023-10-24", season=2023)
    _insert_game(con, "g2", "2023-10-26", season=2023)
    _insert_pgs(con, "g1", 101, 1, pts=20)
    _insert_pgs(con, "g2", 101, 1, pts=22)

    df = build_player_season_decayed_rates(con, "pts", "1", archetype_prior=10.0)
    row_g1 = _row(df, "g1", 101)
    # First game of the only season on record: no prior seasons exist, so
    # the blend must fall back to the archetype prior exactly.
    assert row_g1["rate_carryover_blended"] == pytest.approx(10.0)


def test_builder_picks_up_decayed_prior_season_at_season_opener(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """A player who scored well in a full prior season should carry some
    of that into game 1 of the new season -- the concrete fix for the
    documented Tatum-style season-opener underprediction."""
    # Season 2023: a full, strong season.
    for i in range(10):
        gid = f"s23_g{i}"
        _insert_game(con, gid, f"2023-11-{i + 1:02d}", season=2023)
        _insert_pgs(con, gid, 101, 1, pts=30)
    # Season 2024 opener: zero current-season history.
    _insert_game(con, "s24_g0", "2024-10-22", season=2024)
    _insert_pgs(con, "s24_g0", 101, 1, pts=0)  # pts irrelevant to the as-of feature itself

    df = build_player_season_decayed_rates(con, "pts", "1", archetype_prior=12.0)
    opener = _row(df, "s24_g0", 101)
    assert opener["n_cur"] == 0
    # Must sit strictly between the generic archetype prior (12) and last
    # season's observed rate (30) -- not collapsed to either extreme.
    assert 12.0 < opener["rate_carryover_blended"] < 30.0


def test_builder_is_idempotent(con: duckdb.DuckDBPyConnection) -> None:
    _insert_game(con, "g1", "2023-10-24", season=2023)
    _insert_pgs(con, "g1", 101, 1, pts=20)
    df1 = build_player_season_decayed_rates(con, "pts", "1", archetype_prior=10.0)
    df2 = build_player_season_decayed_rates(con, "pts", "1", archetype_prior=10.0)
    assert df1.equals(df2)
