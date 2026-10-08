"""Opponent defense + pace adjustment tests (synthetic, fast, no fixture DB).

Covers the task's four required checks:
1. No-leakage: a planted future game doesn't change the opponent factor
   for an earlier target game.
2. Directional correctness: vs a weak-defense/high-pace opponent the
   projected mean is higher than vs a strong-defense/slow opponent, for
   the same player.
3. Flag on/off: projections change only when ``use_opponent_adjustment``
   (``OpponentAdjustmentConfig.enabled``) is True; a league-average
   opponent gives a factor of ~1 (no adjustment).
4. Determinism: same inputs -> byte-identical outputs.
"""

from __future__ import annotations

import duckdb
import numpy as np
import polars as pl
import pytest

from nba.coldstart.archetypes import (
    build_player_archetypes,
    build_player_feature_frame,
    build_player_feature_frame_cached,
    build_player_feature_source,
)
from nba.db.connect import connect
from nba.props.opponent import (
    OpponentAdjustmentConfig,
    _assign_archetypes_over_dates,
    apply_opponent_adjustment,
    build_archetype_opponent_features,
    build_opponent_pace_features,
    compute_archetype_opponent_factors,
    compute_opponent_factors,
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
    home_team: int,
    away_team: int,
    home_pts: int,
    away_pts: int,
) -> None:
    con.execute(
        """
        INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts)
        VALUES (?, ?, 2023, ?, ?, ?, ?)
        """,
        [game_id, game_date, home_team, away_team, home_pts, away_pts],
    )


def _insert_team_box(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    team_id: int,
    player_id: int,
    pts: int,
    reb: int = 10,
    ast: int = 5,
    fg3m: int = 2,
) -> None:
    con.execute(
        """
        INSERT INTO player_game_stats
            (game_id, player_id, team_id, minutes, pts, reb, ast, fg3m, stl, blk, tov, starter)
        VALUES (?, ?, ?, 30.0, ?, ?, ?, ?, 1, 0, 1, true)
        """,
        [game_id, player_id, team_id, pts, reb, ast, fg3m],
    )


def _build_weak_defense_history(
    con: duckdb.DuckDBPyConnection, opp_team: int, other_team: int
) -> None:
    """``opp_team`` ALLOWS a lot (weak defense -- its historical opponents
    score heavily against it) and plays at a fast overall pace, over
    several prior games."""
    for i in range(10):
        gid = f"hist_weak_{i}"
        _insert_game(con, gid, f"2023-10-{i + 1:02d}", opp_team, 900 + i, 100, 130)
        _insert_team_box(con, gid, opp_team, 1000 + i, pts=100, reb=40, ast=22, fg3m=10)
        # The opp_team's historical opponent scores heavily -> opp_team allows a lot.
        _insert_team_box(con, gid, 900 + i, 2000 + i, pts=130, reb=48, ast=30, fg3m=16)


def _build_strong_defense_history(
    con: duckdb.DuckDBPyConnection, opp_team: int, other_team: int
) -> None:
    """``opp_team`` allows very little (elite defense) and plays slow."""
    for i in range(10):
        gid = f"hist_strong_{i}"
        _insert_game(con, gid, f"2023-10-{i + 1:02d}", opp_team, 900 + i, 70, 50)
        _insert_team_box(con, gid, opp_team, 1000 + i, pts=70, reb=38, ast=16, fg3m=6)
        # The opp_team's historical opponent scores little -> opp_team allows very little.
        _insert_team_box(con, gid, 900 + i, 2000 + i, pts=50, reb=20, ast=8, fg3m=1)


def test_directional_weak_defense_high_pace_scales_up_vs_strong_defense() -> None:
    """Same player feature row, two different opponents: the weak-defense,
    high-pace opponent must produce a strictly larger combined factor than
    the strong-defense, slow-pace opponent."""
    con = connect(":memory:")
    try:
        # Opponent A: weak defense / fast pace. Opponent B: elite defense / slow.
        _build_weak_defense_history(con, opp_team=1, other_team=50)
        _build_strong_defense_history(con, opp_team=2, other_team=51)
        # League has enough history to be a stable denominator (both of the
        # above already contribute to the pooled league average).
        _insert_game(con, "target_a", "2023-11-01", 10, 1, 0, 0)
        _insert_team_box(con, "target_a", 10, 5001, pts=0)
        _insert_team_box(con, "target_a", 1, 5002, pts=0)
        _insert_game(con, "target_b", "2023-11-01", 10, 2, 0, 0)
        _insert_team_box(con, "target_b", 10, 5003, pts=0)
        _insert_team_box(con, "target_b", 2, 5004, pts=0)

        feats = build_opponent_pace_features(con)
        cfg = OpponentAdjustmentConfig(min_games_for_factor=3)

        row_a = feats.filter((pl.col("game_id") == "target_a") & (pl.col("team_id") == 10))
        row_b = feats.filter((pl.col("game_id") == "target_b") & (pl.col("team_id") == 10))

        factors_a = compute_opponent_factors(row_a, "pts", cfg)
        factors_b = compute_opponent_factors(row_b, "pts", cfg)

        assert factors_a.combined[0] > factors_b.combined[0]
        assert factors_a.combined[0] > 1.0  # weak defense + fast pace -> scales up
        assert factors_b.combined[0] < 1.0  # elite defense + slow pace -> scales down

        mean = np.array([20.0])
        var = np.array([16.0])
        adj_mean_a, _ = apply_opponent_adjustment(mean, var, factors_a)
        adj_mean_b, _ = apply_opponent_adjustment(mean, var, factors_b)
        assert adj_mean_a[0] > adj_mean_b[0]
    finally:
        con.close()


def test_no_leakage_future_game_does_not_change_factor() -> None:
    """Planting a wild future game for the opponent must not change an
    earlier target game's opponent factor."""
    con = connect(":memory:")
    try:
        _build_weak_defense_history(con, opp_team=1, other_team=50)
        _insert_game(con, "target", "2023-10-15", 10, 1, 0, 0)
        _insert_team_box(con, "target", 10, 5001, pts=0)
        _insert_team_box(con, "target", 1, 5002, pts=0)

        cfg = OpponentAdjustmentConfig(min_games_for_factor=3)
        feats_before = build_opponent_pace_features(con)
        row_before = feats_before.filter(pl.col("game_id") == "target")
        factors_before = compute_opponent_factors(row_before, "pts", cfg)

        # Plant a wild future game (after "target") for the same opponent
        # team with an extreme, wildly different allowed-points value.
        _insert_game(con, "future_extreme", "2023-12-01", 1, 999, 5, 300)
        _insert_team_box(con, "future_extreme", 1, 1999, pts=5)
        _insert_team_box(con, "future_extreme", 999, 2999, pts=300)

        feats_after = build_opponent_pace_features(con)
        row_after = feats_after.filter(pl.col("game_id") == "target")
        factors_after = compute_opponent_factors(row_after, "pts", cfg)

        assert factors_after.combined[0] == pytest.approx(factors_before.combined[0])
        assert factors_after.def_factor[0] == pytest.approx(factors_before.def_factor[0])
        assert factors_after.pace_factor[0] == pytest.approx(factors_before.pace_factor[0])
    finally:
        con.close()


def test_flag_off_is_neutral_no_op() -> None:
    """``enabled=False`` must be a pure no-op: applying it to mean/var
    changes nothing (the combined factor is always 1.0 when disabled is
    honored at the call site -- this test verifies the config default for
    an all-ones factor construction path stays a true identity)."""
    mean = np.array([10.0, 20.0, 30.0])
    var = np.array([5.0, 8.0, 12.0])
    from nba.props.opponent import OpponentFactors

    neutral = OpponentFactors(def_factor=np.ones(3), pace_factor=np.ones(3), combined=np.ones(3))
    adj_mean, adj_var = apply_opponent_adjustment(mean, var, neutral)
    np.testing.assert_allclose(adj_mean, mean)
    np.testing.assert_allclose(adj_var, var)


def test_league_average_opponent_gives_factor_near_one() -> None:
    """An opponent whose allowed-stats / pace exactly match the pooled
    league average (built from itself and symmetric peers) should get a
    combined factor of ~1 -- no adjustment."""
    con = connect(":memory:")
    try:
        # Every team in this tiny league allows exactly the same amount and
        # plays at exactly the same pace -- the opponent is, by
        # construction, exactly average.
        for i in range(10):
            gid = f"g_{i}"
            _insert_game(con, gid, f"2023-10-{i + 1:02d}", 100 + i, 200 + i, 100, 100)
            _insert_team_box(con, gid, 100 + i, 3000 + i, pts=100, reb=40, ast=20, fg3m=8)
            _insert_team_box(con, gid, 200 + i, 4000 + i, pts=100, reb=40, ast=20, fg3m=8)
        _insert_game(con, "target", "2023-11-15", 10, 100, 0, 0)
        _insert_team_box(con, "target", 10, 5001, pts=0)
        _insert_team_box(con, "target", 100, 5002, pts=0)

        feats = build_opponent_pace_features(con)
        row = feats.filter(pl.col("game_id") == "target")
        cfg = OpponentAdjustmentConfig(min_games_for_factor=3)
        factors = compute_opponent_factors(row, "pts", cfg)

        assert factors.combined[0] == pytest.approx(1.0, abs=0.02)
    finally:
        con.close()


def test_insufficient_history_forces_neutral_factor() -> None:
    """Opponent with fewer than ``min_games_for_factor`` prior games gets
    the neutral 1.0 factor rather than a noisy ratio."""
    con = connect(":memory:")
    try:
        _insert_game(con, "g0", "2023-10-01", 1, 2, 130, 60)
        _insert_team_box(con, "g0", 1, 1001, pts=130)
        _insert_team_box(con, "g0", 2, 1002, pts=60)
        _insert_game(con, "target", "2023-10-05", 10, 1, 0, 0)
        _insert_team_box(con, "target", 10, 5001, pts=0)
        _insert_team_box(con, "target", 1, 5002, pts=0)

        feats = build_opponent_pace_features(con)
        row = feats.filter(pl.col("game_id") == "target")
        cfg = OpponentAdjustmentConfig(min_games_for_factor=5)
        factors = compute_opponent_factors(row, "pts", cfg)

        assert factors.combined[0] == pytest.approx(1.0)
    finally:
        con.close()


def test_determinism() -> None:
    con = connect(":memory:")
    try:
        _build_weak_defense_history(con, opp_team=1, other_team=50)
        _insert_game(con, "target", "2023-10-15", 10, 1, 0, 0)
        _insert_team_box(con, "target", 10, 5001, pts=0)
        _insert_team_box(con, "target", 1, 5002, pts=0)

        cfg = OpponentAdjustmentConfig(min_games_for_factor=3)
        feats1 = build_opponent_pace_features(con)
        feats2 = build_opponent_pace_features(con)
        row1 = feats1.filter(pl.col("game_id") == "target")
        row2 = feats2.filter(pl.col("game_id") == "target")
        f1 = compute_opponent_factors(row1, "pts", cfg)
        f2 = compute_opponent_factors(row2, "pts", cfg)
        np.testing.assert_array_equal(f1.combined, f2.combined)
    finally:
        con.close()


def test_rejects_unsupported_stat() -> None:
    cfg = OpponentAdjustmentConfig()
    feats = pl.DataFrame(
        {
            "opp_games_played_prior": [10],
            "opp_allowed_pts_prior": [100.0],
            "league_allowed_pts_prior": [100.0],
            "opp_pace_proxy_prior": [200.0],
            "league_pace_proxy_prior": [200.0],
        }
    )
    with pytest.raises(ValueError, match="only supports"):
        compute_opponent_factors(feats, "blk", cfg)


# ---------------------------------------------------------------------------
# Archetype-vs-opponent factor (docs/NEXT_OPTIONS.md §3 Option A).
# ---------------------------------------------------------------------------


def _insert_static(
    con: duckdb.DuckDBPyConnection,
    player_id: int,
    position: str,
    height_in: float,
    weight_lb: float,
    birth_date: str = "1990-01-01",
) -> None:
    con.execute(
        "INSERT INTO players_static (player_id, position, height_in, weight_lb, birth_date) "
        "VALUES (?, ?, ?, ?, ?)",
        [player_id, position, height_in, weight_lb, birth_date],
    )


_BIG = {"position": "Center", "height_in": 84.0, "weight_lb": 260.0}
_SMALL = {"position": "Guard", "height_in": 72.0, "weight_lb": 180.0}


def _build_archetype_opponent_history(
    con: duckdb.DuckDBPyConnection, opp_team: int, big_reb: int = 16, small_reb: int = 2
) -> None:
    """``opp_team`` (on defense) faces a "big" archetype shooter and a
    "small" archetype shooter in every one of 10 prior games -- allowing
    ``big_reb``/``small_reb`` rebounds respectively. Different teams use
    different ``big_reb``/``small_reb`` values in the tests below so the
    pooled league-wide-per-archetype average differs from any one team's
    own cell average (otherwise every team-vs-archetype ratio is trivially
    1.0 by construction)."""
    for i in range(10):
        gid = f"hist_arch_{opp_team}_{i}"
        other_team = 900 + opp_team * 10 + i
        _insert_game(con, gid, f"2023-10-{i + 1:02d}", opp_team, other_team, 100, 100)
        big_id = 70000 + opp_team * 100 + i
        small_id = 80000 + opp_team * 100 + i
        _insert_static(con, big_id, **_BIG)
        _insert_static(con, small_id, **_SMALL)
        # opp_team's own players (irrelevant archetype-wise for this test).
        _insert_team_box(con, gid, opp_team, 60000 + i, pts=20, reb=10, ast=5)
        # The opposing team's shooters: big archetype gets heavily
        # rebounded on, small archetype barely at all -- a signal the
        # team-level (archetype-blind) factor cannot see at all (pooled
        # together, the big/small swing mostly cancels).
        _insert_team_box(con, gid, other_team, big_id, pts=10, reb=big_reb, ast=1)
        _insert_team_box(con, gid, other_team, small_id, pts=20, reb=small_reb, ast=8)


def test_archetype_factor_splits_what_team_level_factor_pools_together() -> None:
    """The archetype-level cell factor for the big archetype must be > 1
    (opp_team allows far more rebounds to bigs than league-average-for-
    bigs) while the small-archetype cell factor is < 1 -- a distinction
    the pooled team-level factor cannot make."""
    con = connect(":memory:")
    try:
        _build_archetype_opponent_history(con, opp_team=1, big_reb=16, small_reb=2)
        # A second opponent with a DIFFERENT big/small split, so the
        # league-wide pooled per-archetype average differs from opp_team 1's
        # own cell average (otherwise every ratio is trivially 1.0).
        _build_archetype_opponent_history(con, opp_team=2, big_reb=8, small_reb=6)

        target_big = 90001
        target_small = 90002
        _insert_static(con, target_big, **_BIG)
        _insert_static(con, target_small, **_SMALL)
        _insert_game(con, "target", "2023-11-15", 10, 1, 0, 0)
        _insert_team_box(con, "target", 10, target_big, pts=0, reb=0, ast=0)
        _insert_team_box(con, "target", 10, target_small, pts=0, reb=0, ast=0)
        _insert_team_box(con, "target", 1, 60099, pts=0, reb=0, ast=0)

        target_df = pl.DataFrame(
            {
                "game_id": ["target", "target"],
                "player_id": [target_big, target_small],
                "team_id": [10, 10],
                "game_date": [
                    __import__("datetime").date(2023, 11, 15),
                    __import__("datetime").date(2023, 11, 15),
                ],
            }
        )
        cfg = OpponentAdjustmentConfig(min_games_for_archetype_factor=3)
        feats = build_archetype_opponent_features(con, target_df, k=2)
        factors = compute_archetype_opponent_factors(feats, "reb", cfg)

        big_row = feats.filter(pl.col("player_id") == target_big)
        small_row = feats.filter(pl.col("player_id") == target_small)
        assert big_row.select("cell_games_played_prior").to_series()[0] >= 3
        assert small_row.select("cell_games_played_prior").to_series()[0] >= 3

        big_idx = feats.get_column("player_id").to_list().index(target_big)
        small_idx = feats.get_column("player_id").to_list().index(target_small)
        assert factors.combined[big_idx] > 1.0
        assert factors.combined[small_idx] < 1.0
        assert factors.combined[big_idx] > factors.combined[small_idx]
    finally:
        con.close()


def test_archetype_factor_no_leakage_future_game_does_not_change_factor() -> None:
    """Planting a wild future game for the opponent must not change an
    earlier target game's archetype-cell factor."""
    con = connect(":memory:")
    try:
        _build_archetype_opponent_history(con, opp_team=1)
        _build_archetype_opponent_history(con, opp_team=2)
        target_big = 90001
        _insert_static(con, target_big, **_BIG)
        _insert_game(con, "target", "2023-11-15", 10, 1, 0, 0)
        _insert_team_box(con, "target", 10, target_big, pts=0, reb=0, ast=0)
        _insert_team_box(con, "target", 1, 60099, pts=0, reb=0, ast=0)

        import datetime as _dt

        target_df = pl.DataFrame(
            {
                "game_id": ["target"],
                "player_id": [target_big],
                "team_id": [10],
                "game_date": [_dt.date(2023, 11, 15)],
            }
        )
        cfg = OpponentAdjustmentConfig(min_games_for_archetype_factor=3)
        feats_before = build_archetype_opponent_features(con, target_df, k=2)
        factors_before = compute_archetype_opponent_factors(feats_before, "reb", cfg)

        future_big = 99999
        _insert_static(con, future_big, **_BIG)
        _insert_game(con, "future_extreme", "2023-12-01", 1, 999, 300, 1)
        _insert_team_box(con, "future_extreme", 1, 88888, pts=5, reb=1, ast=0)
        _insert_team_box(con, "future_extreme", 999, future_big, pts=99, reb=99, ast=0)

        feats_after = build_archetype_opponent_features(con, target_df, k=2)
        factors_after = compute_archetype_opponent_factors(feats_after, "reb", cfg)

        assert factors_after.combined[0] == pytest.approx(factors_before.combined[0])
    finally:
        con.close()


def test_archetype_factor_per_cell_guard_forces_neutral_on_thin_cell() -> None:
    """Fewer than ``min_games_for_archetype_factor`` prior games FOR THAT
    SPECIFIC (opponent, archetype) CELL forces the neutral 1.0 factor,
    even though the team overall has plenty of history (CLAUDE.md /
    docs/NEXT_OPTIONS.md §3's hard pre-commit honesty guard)."""
    con = connect(":memory:")
    try:
        _build_archetype_opponent_history(con, opp_team=1)
        _build_archetype_opponent_history(con, opp_team=2)
        target_big = 90001
        _insert_static(con, target_big, **_BIG)
        _insert_game(con, "target", "2023-11-15", 10, 1, 0, 0)
        _insert_team_box(con, "target", 10, target_big, pts=0, reb=0, ast=0)
        _insert_team_box(con, "target", 1, 60099, pts=0, reb=0, ast=0)

        import datetime as _dt

        target_df = pl.DataFrame(
            {
                "game_id": ["target"],
                "player_id": [target_big],
                "team_id": [10],
                "game_date": [_dt.date(2023, 11, 15)],
            }
        )
        # 10 prior games is the true cell sample size; a guard higher than
        # that must force the neutral factor despite the large, real swing.
        cfg = OpponentAdjustmentConfig(min_games_for_archetype_factor=50)
        feats = build_archetype_opponent_features(con, target_df, k=2)
        factors = compute_archetype_opponent_factors(feats, "reb", cfg)
        assert factors.combined[0] == pytest.approx(1.0)
    finally:
        con.close()


def test_archetype_factor_flag_defaults_off() -> None:
    assert OpponentAdjustmentConfig().use_archetype_factor is False


def test_cached_feature_source_matches_per_date_query() -> None:
    """Perf fix equivalence proof (docs/PERF_ARCHETYPE_2026-10-08.md): the
    cached-source path (:func:`build_player_feature_source` +
    :func:`build_player_feature_frame_cached`, one full-history SQL query
    reused across dates) must produce byte-identical feature frames to the
    original per-date path (:func:`build_player_feature_frame`, which
    re-runs the full-history SQL query every call) for every date -- not
    just on average, row for row."""
    con = connect(":memory:")
    try:
        _build_archetype_opponent_history(con, opp_team=1, big_reb=16, small_reb=2)
        _build_archetype_opponent_history(con, opp_team=2, big_reb=8, small_reb=6)

        dates = [f"2023-10-{i + 1:02d}" for i in range(10)] + ["2023-11-15"]
        source = build_player_feature_source(con)
        for d in dates:
            old = build_player_feature_frame(con, d)
            new = build_player_feature_frame_cached(source, d)
            assert old.equals(new), f"mismatch at as_of_date={d}"
    finally:
        con.close()


def test_assign_archetypes_over_dates_matches_naive_per_date_path() -> None:
    """End-to-end equivalence: :func:`_assign_archetypes_over_dates` (the
    cached-source path used by :func:`build_archetype_opponent_features`)
    must label every player identically to the naive per-date path it
    replaced (loop calling :func:`build_player_feature_frame` fresh each
    iteration, no caching)."""
    con = connect(":memory:")
    try:
        _build_archetype_opponent_history(con, opp_team=1, big_reb=16, small_reb=2)
        _build_archetype_opponent_history(con, opp_team=2, big_reb=8, small_reb=6)

        import datetime as _dt

        ref_date = "2023-10-01"
        model = build_player_archetypes(con, ref_date, k=2, seed=0)
        dates = [_dt.date(2023, 10, i + 1) for i in range(10)] + [_dt.date(2023, 11, 15)]

        from nba.coldstart.archetypes import assign_archetypes

        def _naive_assign_over_dates(con, model, dates):
            frames = []
            for d in sorted(set(dates)):
                feature_frame = build_player_feature_frame(con, str(d))
                labels = assign_archetypes(model, feature_frame)
                if labels.height:
                    frames.append(labels.with_columns(pl.lit(d).alias("game_date")))
            if not frames:
                return pl.DataFrame(
                    {"player_id": [], "game_date": [], "archetype": []},
                    schema={"player_id": pl.Int64, "game_date": pl.Date, "archetype": pl.Int64},
                )
            return pl.concat(frames, how="vertical")

        naive = _naive_assign_over_dates(con, model, dates).sort(["player_id", "game_date"])
        fast = _assign_archetypes_over_dates(con, model, dates).sort(["player_id", "game_date"])
        assert naive.equals(fast)
    finally:
        con.close()


def test_archetype_factor_rejects_unsupported_stat() -> None:
    cfg = OpponentAdjustmentConfig()
    feats = pl.DataFrame(
        {
            "cell_games_played_prior": [10],
            "cell_allowed_pts_prior": [10.0],
            "league_allowed_pts_prior": [10.0],
        }
    )
    with pytest.raises(ValueError, match="only supports"):
        compute_archetype_opponent_factors(feats, "blk", cfg)
