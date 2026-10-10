"""Tests for ``research.sim.usage_redistribution`` (docs/NEXT_OPTIONS.md §2, A/B).

Synthetic in-memory DuckDB only -- no real DB access, fast (CLAUDE.md "no
multi-minute jobs"), same convention as
``tests/ml/test_player_possession_features.py``.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pytest

from nba.db.connect import connect
from research.sim.player_attribution import PlayerSimProfile
from research.sim.usage_redistribution import (
    UsageRedistributionConfig,
    adjust_profiles_for_absence,
    apply_usage_redistribution,
    effective_boost_fraction,
    historical_player_boost_reference,
    historical_team_boost_reference,
)


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = connect(":memory:")
    yield c
    c.close()


def _profile(
    player_id: int, shot_share: float, projected_minutes: float = 30.0
) -> PlayerSimProfile:
    return PlayerSimProfile(
        player_id=player_id,
        shot_share=shot_share,
        zone_mix=(0.34, 0.26, 0.40),
        zone_make_prob=(0.72, 0.52, 0.42),
        ft_trip_rate=0.3,
        ft_pct=0.78,
        projected_minutes=projected_minutes,
    )


# ---------------------------------------------------------------------------
# adjust_profiles_for_absence / apply_usage_redistribution / cap
# ---------------------------------------------------------------------------


def test_noop_when_no_absent_players() -> None:
    profiles = [_profile(1, 0.30), _profile(2, 0.20), _profile(3, 0.10)]
    out = adjust_profiles_for_absence(profiles, set(), boost_fraction=0.3)
    assert out is profiles  # literally the same object, not just equal


def test_noop_when_boost_fraction_zero() -> None:
    profiles = [_profile(1, 0.30), _profile(2, 0.20), _profile(3, 0.10)]
    out = adjust_profiles_for_absence(profiles, {1}, boost_fraction=0.0)
    assert out is profiles


def test_noop_when_absent_player_is_not_top_usage() -> None:
    """A bench scrub sitting has no usage worth redistributing -- confirms
    the "no-op when nobody relevant is out" full-sample regression guard."""
    profiles = [_profile(1, 0.30), _profile(2, 0.25), _profile(3, 0.01)]
    out = adjust_profiles_for_absence(profiles, {3}, boost_fraction=0.3, top_n_usage=2)
    assert out is profiles


def test_redistribution_is_capped_and_richer_get_richer() -> None:
    """Star (id=1, share=0.30) sits; remaining players 2 (0.20) and 3 (0.05)
    should both gain share, but player 2 (richer) must gain strictly more
    than player 3 -- and the total added across present players must not
    exceed boost_fraction * freed_usage (the cap)."""
    profiles = [_profile(1, 0.30), _profile(2, 0.20), _profile(3, 0.05)]
    boost_fraction = 0.3
    out = adjust_profiles_for_absence(profiles, {1}, boost_fraction=boost_fraction, top_n_usage=2)
    by_id = {p.player_id: p for p in out}

    assert by_id[1].shot_share == 0.30  # absent player's own share untouched
    gain_2 = by_id[2].shot_share - 0.20
    gain_3 = by_id[3].shot_share - 0.05
    assert gain_2 > 0
    assert gain_3 > 0
    assert gain_2 > gain_3  # richer-get-richer

    freed_usage = 0.30
    total_added = gain_2 + gain_3
    assert total_added <= boost_fraction * freed_usage + 1e-9


def test_cap_is_enforced_via_effective_boost_fraction() -> None:
    config = UsageRedistributionConfig(enabled=True, max_boost_fraction=0.15)
    assert effective_boost_fraction(0.90, config) == pytest.approx(0.15)
    assert effective_boost_fraction(0.05, config) == pytest.approx(0.05)
    assert effective_boost_fraction(-0.2, config) == pytest.approx(0.0)


def test_apply_usage_redistribution_disabled_flag_is_true_noop() -> None:
    profiles = [_profile(1, 0.30), _profile(2, 0.20), _profile(3, 0.05)]
    config = UsageRedistributionConfig(enabled=False)
    out = apply_usage_redistribution(profiles, {1}, config, raw_boost=0.5)
    assert out is profiles


def test_apply_usage_redistribution_enabled_changes_weights() -> None:
    profiles = [_profile(1, 0.30), _profile(2, 0.20), _profile(3, 0.05)]
    config = UsageRedistributionConfig(enabled=True, max_boost_fraction=0.3)
    out = apply_usage_redistribution(profiles, {1}, config, raw_boost=0.5)
    by_id = {p.player_id: p for p in out}
    assert by_id[2].shot_share > 0.20


# ---------------------------------------------------------------------------
# Historical reference functions -- as-of discipline + shrinkage convergence
# ---------------------------------------------------------------------------


def _insert_game(con: duckdb.DuckDBPyConnection, game_id: str, game_date: str) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, "
        "away_pts) VALUES (?, ?, 2023, 1, 2, 100, 90)",
        [game_id, game_date],
    )


def _insert_pgs(
    con: duckdb.DuckDBPyConnection, game_id: str, player_id: int, team_id: int, minutes: float
) -> None:
    con.execute(
        """
        INSERT INTO player_game_stats
            (game_id, player_id, team_id, minutes, pts, reb, ast, fg3m, stl, blk, tov, starter)
        VALUES (?, ?, ?, ?, 10, 5, 3, 1, 1, 0, 1, true)
        """,
        [game_id, player_id, team_id, minutes],
    )


def _insert_shot(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    poss_idx: int,
    off_team: int,
    def_team: int,
    shooter_id: int,
    shot_zone: str = "rim",
    outcome: str = "FGM2",
    pts: int = 2,
) -> None:
    con.execute(
        """
        INSERT INTO possessions
            (game_id, poss_idx, period, clock_start, clock_end, off_team, def_team,
             score_diff, outcome, shooter_id, shot_zone, fta, pts)
        VALUES (?, ?, 1, 700.0, 680.0, ?, ?, 0, ?, ?, ?, 0, ?)
        """,
        [game_id, poss_idx, off_team, def_team, outcome, shooter_id, shot_zone, pts],
    )


def _build_star_sits_history(con: duckdb.DuckDBPyConnection, n_qualifying_games: int) -> None:
    """Team 1 has a star (player 1, usually ~50% usage) and two teammates
    (2, 3, ~25% each). First establish the star's usage level over a run of
    normal games, then play ``n_qualifying_games`` games where the star sits
    (minutes=0, no shots) and teammate 2 absorbs essentially ALL of the
    star's usual share (actual_shot_share far above the simple
    proportional-renorm baseline) -- a strong, known, synthetic "excess"
    signal the reference function should pick up as ``n`` grows.
    """
    base = dt.date(2023, 10, 1)
    day_offset = 0
    # Establish the star's trailing usage over a LONG run of normal games
    # (60, not a handful) -- both so the star's lead over the field is
    # large enough to survive many subsequent sit-games without the
    # trailing-usage ranking flipping (a correctness requirement, not a
    # tuning hack: a backup who starts every single game for 150+ games
    # straight eventually *does* out-rank a frozen, long-inactive "star" in
    # trailing usage, which is the right behavior -- the test just needs
    # enough normal-game history that this doesn't happen within the
    # n_qualifying_games range exercised here), and so cumulative FGA counts
    # approach the shrinkage pseudo-count (150) enough for a real signal to
    # show through. Player 2 is deliberately the SECOND-highest usage
    # teammate (not tied with player 3) so the later redistribution signal
    # is asymmetric (richer-get-richer), not a symmetric 2-player excess
    # that would average to exactly 0 by construction.
    for i in range(60):
        gid = f"normal{i}"
        _insert_game(con, gid, (base + dt.timedelta(days=day_offset)).isoformat())
        day_offset += 1
        for p in (1, 2, 3):
            _insert_pgs(con, gid, p, 1, 20.0)
        for j in range(4):
            _insert_shot(con, gid, j, 1, 2, 1)  # star takes 4/6 shots
        _insert_shot(con, gid, 4, 1, 2, 2)  # teammate 2 takes 1/6
        _insert_shot(con, gid, 5, 1, 2, 3)  # teammate 3 takes 1/6

    # Star sits; teammate 2 (already the richer remaining teammate) takes
    # over almost everything -- richer-get-richer excess beyond the
    # proportional-renormalization baseline.
    for i in range(n_qualifying_games):
        gid = f"sit{i}"
        _insert_game(con, gid, (base + dt.timedelta(days=day_offset)).isoformat())
        day_offset += 1
        _insert_pgs(con, gid, 1, 1, 0.0)  # DNP
        _insert_pgs(con, gid, 2, 1, 30.0)
        _insert_pgs(con, gid, 3, 1, 20.0)
        for j in range(5):
            _insert_shot(con, gid, j, 1, 2, 2)
        _insert_shot(con, gid, 5, 1, 2, 3)


def test_player_reference_is_noop_with_no_qualifying_games(con: duckdb.DuckDBPyConnection) -> None:
    _build_star_sits_history(con, n_qualifying_games=0)
    boost, n = historical_player_boost_reference(con, dt.date(2024, 1, 1))
    assert n == 0
    assert boost == 0.0


def test_player_reference_converges_toward_observed_as_n_grows(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Shrinkage convergence (CLAUDE.md required unit test, mirrored for
    this module's own reference rate): a thin qualifying sample shrinks
    heavily toward 0 (the no-op prior); a large qualifying sample should
    move the reference meaningfully off of 0 and closer to the strong
    synthetic signal baked into the fixture."""
    con_small = connect(":memory:")
    _build_star_sits_history(con_small, n_qualifying_games=2)
    boost_small, n_small = historical_player_boost_reference(
        con_small, dt.date(2024, 6, 1), pseudo_count=20.0
    )

    con_large = connect(":memory:")
    _build_star_sits_history(con_large, n_qualifying_games=200)
    boost_large, n_large = historical_player_boost_reference(
        con_large, dt.date(2024, 6, 1), pseudo_count=20.0
    )

    assert n_small > 0 and n_large > n_small
    assert boost_small < boost_large  # more data -> more trust in the observed excess
    assert boost_small < 0.01  # thin sample should be heavily shrunk toward 0
    assert boost_large > 0.01  # large, consistent sample should move well off 0
    con_small.close()
    con_large.close()


def test_player_reference_as_of_boundary_ignores_future_games(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """No-leakage: a reference computed as-of an earlier date must not see
    (or be influenced by) games dated on/after that boundary."""
    _build_star_sits_history(con, n_qualifying_games=5)
    boost_before, n_before = historical_player_boost_reference(con, dt.date(2023, 10, 1))
    # Everything in the fixture is dated 2023-10 onward -- an as_of_date
    # before all of it must see zero qualifying history.
    assert n_before == 0
    assert boost_before == 0.0

    boost_after, n_after = historical_player_boost_reference(con, dt.date(2025, 1, 1))
    assert n_after > 0


def test_team_reference_is_noop_with_no_qualifying_games(con: duckdb.DuckDBPyConnection) -> None:
    _build_star_sits_history(con, n_qualifying_games=0)
    boost, n = historical_team_boost_reference(con, dt.date(2024, 1, 1))
    assert n == 0
    assert boost == 0.0


def test_team_reference_converges_toward_observed_as_n_grows(
    con: duckdb.DuckDBPyConnection,
) -> None:
    con_small = connect(":memory:")
    _build_star_sits_history(con_small, n_qualifying_games=2)
    boost_small, n_small = historical_team_boost_reference(
        con_small, dt.date(2024, 6, 1), pseudo_count=20.0
    )

    con_large = connect(":memory:")
    _build_star_sits_history(con_large, n_qualifying_games=200)
    boost_large, n_large = historical_team_boost_reference(
        con_large, dt.date(2024, 6, 1), pseudo_count=20.0
    )

    assert n_small > 0 and n_large > n_small
    assert boost_small < boost_large
    con_small.close()
    con_large.close()


def test_fixture_db_smoke_both_references_run_without_crashing() -> None:
    """Smoke test against the committed tiny fixture (CLAUDE.md fixture
    convention) -- games.json has no possessions loaded, so both
    references should return the honest no-signal ``(0.0, 0)`` default
    rather than crashing or fabricating a number."""
    from tests.fixtures.loader import build_fixture_db

    con = build_fixture_db()
    try:
        boost_a, n_a = historical_player_boost_reference(con, dt.date(2026, 1, 1))
        boost_b, n_b = historical_team_boost_reference(con, dt.date(2026, 1, 1))
        assert n_a == 0 and boost_a == 0.0
        assert n_b == 0 and boost_b == 0.0
    finally:
        con.close()
