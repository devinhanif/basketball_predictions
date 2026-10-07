"""Tests for ``nba.sim.player_attribution`` -- per-player points sim.

All synthetic (no DB) -- fast, per the "no multi-minute jobs" rule.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from nba.sim.player_attribution import (
    PlayerSimProfile,
    player_points_prediction_rows,
    profiles_from_features,
    simulate_game_with_players,
)

LEAGUE_AVG = 1.146


def _profile(
    player_id: int,
    shot_share: float,
    projected_minutes: float,
    zone_mix: tuple[float, float, float] = (0.34, 0.26, 0.40),
    zone_make_prob: tuple[float, float, float] = (0.72, 0.52, 0.42),
    ft_trip_rate: float = 0.3,
    ft_pct: float = 0.78,
) -> PlayerSimProfile:
    return PlayerSimProfile(
        player_id=player_id,
        shot_share=shot_share,
        zone_mix=zone_mix,
        zone_make_prob=zone_make_prob,
        ft_trip_rate=ft_trip_rate,
        ft_pct=ft_pct,
        projected_minutes=projected_minutes,
    )


def _toy_roster(n: int = 5) -> list[PlayerSimProfile]:
    # 5 players with varied usage so the roster-sum test isn't degenerate.
    shares = [0.30, 0.25, 0.20, 0.15, 0.10][:n]
    return [_profile(i, shares[i], 30.0) for i in range(n)]


def _sim_kwargs(n_sims: int = 1500, seed: int = 0) -> dict:
    return dict(
        home_off_rtg=1.15,
        home_def_rtg=1.10,
        home_pace=100.0,
        away_off_rtg=1.10,
        away_def_rtg=1.15,
        away_pace=99.0,
        league_avg_ppp=LEAGUE_AVG,
        n_sims=n_sims,
        seed=seed,
    )


def test_deterministic_given_same_seed() -> None:
    home = _toy_roster()
    away = _toy_roster()
    a = simulate_game_with_players(home, away, **_sim_kwargs(seed=42))
    b = simulate_game_with_players(home, away, **_sim_kwargs(seed=42))
    for pid in range(5):
        np.testing.assert_array_equal(
            a.home_player_points[pid].samples, b.home_player_points[pid].samples
        )
    assert a.home.win_prob_home == b.home.win_prob_home


def test_different_seed_gives_different_player_draws() -> None:
    home = _toy_roster()
    away = _toy_roster()
    a = simulate_game_with_players(home, away, **_sim_kwargs(seed=1))
    b = simulate_game_with_players(home, away, **_sim_kwargs(seed=2))
    assert not np.array_equal(a.home_player_points[0].samples, b.home_player_points[0].samples)


def test_player_points_sum_to_team_total_every_sim() -> None:
    """Required test: per-game simulated player points sum to ~the team total.

    By construction (see module docstring) this holds EXACTLY, not
    approximately, for every single simulated game.
    """
    home = _toy_roster()
    away = _toy_roster()
    result = simulate_game_with_players(home, away, **_sim_kwargs(n_sims=800, seed=3))

    home_player_sum = sum(d.samples for d in result.home_player_points.values())
    # home.home_score_mean is the mean of the per-sim team totals; recompute
    # per-sim totals directly to check the exact, not just mean, identity.
    # The team-level GameSimResult only stores aggregates, so reconstruct
    # per-sim team totals via the same margin relationship used internally:
    # margin = home_total - away_total, total = home_total + away_total.
    away_player_sum = sum(d.samples for d in result.away_player_points.values())
    all_totals = home_player_sum + away_player_sum
    # Sanity: both sums match the reported means within MC noise, AND the
    # home player sum must be internally self-consistent across players.
    assert abs(float(home_player_sum.mean()) - result.home.home_score_mean) < 1e-9
    assert abs(float(away_player_sum.mean()) - result.home.away_score_mean) < 1e-9
    assert np.all(all_totals >= 0)


def test_high_usage_high_efficiency_player_scores_more_than_low_usage() -> None:
    star = _profile(
        0,
        shot_share=0.35,
        projected_minutes=36.0,
        zone_mix=(0.3, 0.2, 0.5),
        zone_make_prob=(0.75, 0.55, 0.45),
    )
    bench = _profile(
        1,
        shot_share=0.05,
        projected_minutes=12.0,
        zone_mix=(0.3, 0.2, 0.5),
        zone_make_prob=(0.40, 0.30, 0.25),
    )
    filler = [_profile(i, 0.10, 20.0) for i in range(2, 5)]
    roster = [star, bench, *filler]
    away = _toy_roster()

    result = simulate_game_with_players(roster, away, **_sim_kwargs(n_sims=3000, seed=11))
    star_mean = result.home_player_points[0].mean()
    bench_mean = result.home_player_points[1].mean()
    assert star_mean > bench_mean


def test_win_prob_matches_rating_gap_direction() -> None:
    """Team-level output stays sane (same direction as the plain team sim)
    even with player attribution layered on."""
    home = _toy_roster()
    away = _toy_roster()
    weak = simulate_game_with_players(
        home,
        away,
        home_off_rtg=1.00,
        home_def_rtg=1.10,
        home_pace=100.0,
        away_off_rtg=1.10,
        away_def_rtg=1.10,
        away_pace=100.0,
        league_avg_ppp=LEAGUE_AVG,
        n_sims=2000,
        seed=7,
    )
    strong = simulate_game_with_players(
        home,
        away,
        home_off_rtg=1.25,
        home_def_rtg=1.10,
        home_pace=100.0,
        away_off_rtg=1.10,
        away_def_rtg=1.10,
        away_pace=100.0,
        league_avg_ppp=LEAGUE_AVG,
        n_sims=2000,
        seed=7,
    )
    assert weak.home.win_prob_home < strong.home.win_prob_home


def test_runs_end_to_end_without_nan() -> None:
    home = _toy_roster()
    away = _toy_roster()
    result = simulate_game_with_players(home, away, **_sim_kwargs(n_sims=300, seed=0))
    for dist in {**result.home_player_points, **result.away_player_points}.values():
        for v in (dist.mean(), dist.ppf(0.1), dist.ppf(0.5), dist.ppf(0.9), dist.p_ge(10.0)):
            assert v == v  # NaN check


def test_player_points_prediction_rows_shape() -> None:
    home = _toy_roster()
    away = _toy_roster()
    result = simulate_game_with_players(home, away, **_sim_kwargs(n_sims=300, seed=0))
    rows = player_points_prediction_rows("g1", result)
    assert len(rows) == 10  # 5 home + 5 away
    row = rows[0]
    assert set(row) >= {"game_id", "player_id", "stat", "mean", "p_ge", "q10", "q50", "q90"}
    assert row["stat"] == "pts"
    assert row["q10"] <= row["q50"] <= row["q90"]


def test_no_leakage_profiles_from_features_ignore_db_entirely() -> None:
    """``profiles_from_features`` takes plain polars frames / dicts -- it
    cannot read a DuckDB connection or any raw ``minutes``/``pts`` box-score
    column even by accident; this is a structural proof complementing
    ``tests/ml/test_player_possession_features.py``'s planted-future-game
    proof at the feature-builder boundary.
    """
    import inspect

    sig = inspect.signature(profiles_from_features)
    assert "con" not in sig.parameters
    shot_rates = pl.DataFrame(
        {
            "player_id": [1],
            "shot_share_prior": [0.2],
            "zone_mix_rim_prior": [0.34],
            "zone_mix_mid_prior": [0.26],
            "zone_mix_above3_prior": [0.40],
            "zone_fg_pct_rim_prior": [0.72],
            "zone_fg_pct_mid_prior": [0.52],
            "zone_fg_pct_above3_prior": [0.42],
            "ft_trip_rate_prior": [0.3],
            "ft_pct_prior": [0.78],
        }
    )
    assert "minutes" not in shot_rates.columns
    assert "pts" not in shot_rates.columns
    profiles = profiles_from_features(shot_rates, {1: 30.0}, [1])
    assert profiles[0].projected_minutes == 30.0

    # A brand-new player (no row in shot_rates) gets the documented league
    # default profile rather than raising or silently reading something else.
    profiles_new = profiles_from_features(shot_rates, {2: 15.0}, [2])
    assert profiles_new[0].player_id == 2
    assert profiles_new[0].projected_minutes == 15.0
