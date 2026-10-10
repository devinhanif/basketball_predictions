"""Tests for the rebound + assist attribution added to
``research.sim.player_attribution`` (companion to the existing points-only tests
in ``tests/ml/test_player_attribution.py``).

All synthetic (no DB) -- fast, per the "no multi-minute jobs" rule.
"""

from __future__ import annotations

import numpy as np

from research.sim.player_attribution import (
    PlayerSimProfile,
    _attribute_assists,
    player_assists_prediction_rows,
    player_rebounds_prediction_rows,
    profiles_from_features,
    simulate_game_with_players,
)

LEAGUE_AVG = 1.146


def _profile(
    player_id: int,
    shot_share: float = 0.2,
    projected_minutes: float = 24.0,
    zone_mix: tuple[float, float, float] = (0.34, 0.26, 0.40),
    zone_make_prob: tuple[float, float, float] = (0.72, 0.52, 0.42),
    ft_trip_rate: float = 0.3,
    ft_pct: float = 0.78,
    orb_rate: float = 0.05,
    drb_rate: float = 0.15,
    ast_rate: float = 0.12,
) -> PlayerSimProfile:
    return PlayerSimProfile(
        player_id=player_id,
        shot_share=shot_share,
        zone_mix=zone_mix,
        zone_make_prob=zone_make_prob,
        ft_trip_rate=ft_trip_rate,
        ft_pct=ft_pct,
        projected_minutes=projected_minutes,
        orb_rate=orb_rate,
        drb_rate=drb_rate,
        ast_rate=ast_rate,
    )


def _toy_roster(n: int = 5) -> list[PlayerSimProfile]:
    shares = [0.30, 0.25, 0.20, 0.15, 0.10][:n]
    return [_profile(i, shot_share=shares[i]) for i in range(n)]


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
            a.home_player_rebounds[pid].samples, b.home_player_rebounds[pid].samples
        )
        np.testing.assert_array_equal(
            a.home_player_assists[pid].samples, b.home_player_assists[pid].samples
        )


def test_runs_end_to_end_without_nan() -> None:
    home = _toy_roster()
    away = _toy_roster()
    result = simulate_game_with_players(home, away, **_sim_kwargs(n_sims=400, seed=0))
    all_dists = {
        **result.home_player_rebounds,
        **result.away_player_rebounds,
        **result.home_player_assists,
        **result.away_player_assists,
    }
    for dist in all_dists.values():
        for v in (dist.mean(), dist.ppf(0.1), dist.ppf(0.5), dist.ppf(0.9), dist.p_ge(2.0)):
            assert v == v  # NaN check


def test_team_rebounds_are_in_a_realistic_range() -> None:
    """Required test: per-game simulated team rebounds should be in a
    realistic NBA range (~roughly league-average team total rebounds),
    not degenerate (e.g. ~0 or ~300). The exact mean depends on this
    module's documented, not-yet-tuned MISSED_FG_SHARE_OF_ZERO_OUTCOME
    constant (see module docstring) -- this test checks plausibility, not
    a precisely fit value."""
    home = _toy_roster()
    away = _toy_roster()
    result = simulate_game_with_players(home, away, **_sim_kwargs(n_sims=2000, seed=5))

    home_team_reb = sum(d.samples for d in result.home_player_rebounds.values())
    away_team_reb = sum(d.samples for d in result.away_player_rebounds.values())
    assert 20.0 <= float(home_team_reb.mean()) <= 60.0
    assert 20.0 <= float(away_team_reb.mean()) <= 60.0
    assert np.all(home_team_reb >= 0)
    assert np.all(away_team_reb >= 0)


def test_high_orb_player_gets_more_offensive_rebounds() -> None:
    """A synthetic player with a much higher ORB propensity than their
    teammates should out-rebound them on average."""
    boxout_specialist = _profile(0, orb_rate=0.40, drb_rate=0.10)
    teammates = [_profile(i, orb_rate=0.02, drb_rate=0.10) for i in range(1, 5)]
    home = [boxout_specialist, *teammates]
    away = _toy_roster()

    result = simulate_game_with_players(home, away, **_sim_kwargs(n_sims=3000, seed=9))
    specialist_mean = result.home_player_rebounds[0].mean()
    teammate_means = [result.home_player_rebounds[i].mean() for i in range(1, 5)]
    assert specialist_mean > max(teammate_means)


def test_high_drb_player_gets_more_defensive_rebounds() -> None:
    """Symmetric check on the defensive side: a much higher DRB propensity
    should out-rebound teammates holding ORB roughly fixed."""
    rebounder = _profile(0, orb_rate=0.05, drb_rate=0.50)
    teammates = [_profile(i, orb_rate=0.05, drb_rate=0.05) for i in range(1, 5)]
    home = [rebounder, *teammates]
    away = _toy_roster()

    result = simulate_game_with_players(home, away, **_sim_kwargs(n_sims=3000, seed=10))
    rebounder_mean = result.home_player_rebounds[0].mean()
    teammate_means = [result.home_player_rebounds[i].mean() for i in range(1, 5)]
    assert rebounder_mean > max(teammate_means)


def test_assists_never_go_to_the_scorer() -> None:
    """Hard invariant: a made-FG scorer can never be credited with the
    assist on their own shot, even when every player (including the
    scorer) shares the exact same ast_rate -- a scenario where a buggy
    "most-likely-but-not-impossible" self-exclusion would still leak
    self-assists, but the actual implementation hard-zeroes the scorer's
    own weight before normalizing (see
    ``_normalized_weights_excluding_self``). Only player 0 ever scores
    (``made[:, 0] = 10`` in every sim), so if self-assists were possible
    at all, player 0 would show a nonzero assist total."""
    rng = np.random.default_rng(0)
    n_sims = 500
    n_players = 4
    made = np.zeros((n_sims, n_players))
    made[:, 0] = 10.0  # only player 0 ever scores, every single sim.
    profiles = [_profile(i, ast_rate=0.5) for i in range(n_players)]  # equal weights, incl. scorer.

    assists = _attribute_assists(made, profiles, assisted_fg_rate=1.0, rng=rng)
    assert np.all(assists[:, 0] == 0.0)
    assert assists[:, 1:].sum() > 0
    # And the full-sim path agrees: a team where one player does ~all the
    # scoring should still see nonzero team assists landing on teammates.
    star = _profile(0, shot_share=0.9, ast_rate=0.5)
    bench = [_profile(i, shot_share=0.025, ast_rate=0.5) for i in range(1, 5)]
    home = [star, *bench]
    away = _toy_roster()
    result = simulate_game_with_players(home, away, **_sim_kwargs(n_sims=2000, seed=11))
    teammate_assist_total = sum(result.home_player_assists[i].samples for i in range(1, 5))
    assert teammate_assist_total.sum() > 0


def test_player_rebounds_prediction_rows_shape() -> None:
    home = _toy_roster()
    away = _toy_roster()
    result = simulate_game_with_players(home, away, **_sim_kwargs(n_sims=300, seed=0))
    rows = player_rebounds_prediction_rows("g1", result)
    assert len(rows) == 10
    row = rows[0]
    assert row["stat"] == "reb"
    assert row["q10"] <= row["q50"] <= row["q90"]


def test_player_assists_prediction_rows_shape() -> None:
    home = _toy_roster()
    away = _toy_roster()
    result = simulate_game_with_players(home, away, **_sim_kwargs(n_sims=300, seed=0))
    rows = player_assists_prediction_rows("g1", result)
    assert len(rows) == 10
    row = rows[0]
    assert row["stat"] == "ast"
    assert row["q10"] <= row["q50"] <= row["q90"]


def test_profiles_from_features_without_reb_ast_rates_defaults_to_zero() -> None:
    """Backward compatibility: callers who don't pass ``reb_ast_rates``
    (e.g. the existing points-only eval path) get profiles with 0.0
    orb/drb/ast rates -- no crash, no accidental nonzero default."""
    import polars as pl

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
    profiles = profiles_from_features(shot_rates, {1: 30.0}, [1])
    assert profiles[0].orb_rate == 0.0
    assert profiles[0].drb_rate == 0.0
    assert profiles[0].ast_rate == 0.0


def test_profiles_from_features_with_reb_ast_rates() -> None:
    import polars as pl

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
    reb_ast_rates = pl.DataFrame(
        {
            "player_id": [1],
            "orb_rate_prior": [0.07],
            "drb_rate_prior": [0.18],
            "ast_rate_prior": [0.22],
        }
    )
    profiles = profiles_from_features(shot_rates, {1: 30.0}, [1], reb_ast_rates=reb_ast_rates)
    assert profiles[0].orb_rate == 0.07
    assert profiles[0].drb_rate == 0.18
    assert profiles[0].ast_rate == 0.22

    # A brand-new player (no row anywhere) gets the documented league
    # default rebound/assist rates, not a crash or a silent zero.
    from research.features.player_rebound_assist_features import (
        LEAGUE_AST_RATE_DEFAULT,
        LEAGUE_DRB_RATE_DEFAULT,
        LEAGUE_ORB_RATE_DEFAULT,
    )

    profiles_new = profiles_from_features(shot_rates, {2: 15.0}, [2], reb_ast_rates=reb_ast_rates)
    assert profiles_new[0].orb_rate == LEAGUE_ORB_RATE_DEFAULT
    assert profiles_new[0].drb_rate == LEAGUE_DRB_RATE_DEFAULT
    assert profiles_new[0].ast_rate == LEAGUE_AST_RATE_DEFAULT
