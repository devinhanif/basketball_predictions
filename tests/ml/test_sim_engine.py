"""Tests for the Monte Carlo possession-sim engine (``nba/sim/engine.py``).

All synthetic (no DB) -- fast, per the "no multi-minute jobs" operating
rule. Checks determinism, win-prob monotonicity in rating gap, and
realism bounds on simulated totals/margins (CLAUDE.md "Sim outputs":
totals ~220+/-20, margin SD ~12-14).
"""

from __future__ import annotations

from nba.sim.engine import simulate_game

LEAGUE_AVG = 1.146


def test_deterministic_given_same_seed() -> None:
    kwargs = dict(
        home_off_rtg=1.15,
        home_def_rtg=1.10,
        home_pace=100.0,
        away_off_rtg=1.10,
        away_def_rtg=1.15,
        away_pace=99.0,
        league_avg_ppp=LEAGUE_AVG,
        n_sims=500,
        seed=42,
    )
    a = simulate_game(**kwargs)
    b = simulate_game(**kwargs)
    assert a.win_prob_home == b.win_prob_home
    assert a.margin_mean == b.margin_mean
    assert a.total_mean == b.total_mean


def test_different_seed_gives_different_draws() -> None:
    kwargs = dict(
        home_off_rtg=1.12,
        home_def_rtg=1.12,
        home_pace=100.0,
        away_off_rtg=1.12,
        away_def_rtg=1.12,
        away_pace=100.0,
        league_avg_ppp=LEAGUE_AVG,
        n_sims=500,
    )
    a = simulate_game(seed=1, **kwargs)
    b = simulate_game(seed=2, **kwargs)
    assert a.margin_mean != b.margin_mean


def test_win_prob_in_open_unit_interval() -> None:
    result = simulate_game(
        home_off_rtg=1.12,
        home_def_rtg=1.12,
        home_pace=100.0,
        away_off_rtg=1.12,
        away_def_rtg=1.12,
        away_pace=100.0,
        league_avg_ppp=LEAGUE_AVG,
        n_sims=500,
        seed=0,
    )
    assert 0.0 < result.win_prob_home < 1.0


def test_win_prob_monotonic_in_rating_gap() -> None:
    """A better home offense (higher off_rtg) should win more often, all else equal."""
    base = dict(
        home_def_rtg=1.10,
        home_pace=100.0,
        away_off_rtg=1.10,
        away_def_rtg=1.10,
        away_pace=100.0,
        league_avg_ppp=LEAGUE_AVG,
        n_sims=3000,
        seed=7,
    )
    weak = simulate_game(home_off_rtg=1.00, **base)
    average = simulate_game(home_off_rtg=1.10, **base)
    strong = simulate_game(home_off_rtg=1.25, **base)
    assert weak.win_prob_home < average.win_prob_home < strong.win_prob_home


def test_win_prob_monotonic_in_defensive_rating_gap() -> None:
    """A worse home defense (higher def_rtg = more points allowed) should win less often."""
    base = dict(
        home_off_rtg=1.10,
        home_pace=100.0,
        away_off_rtg=1.10,
        away_def_rtg=1.10,
        away_pace=100.0,
        league_avg_ppp=LEAGUE_AVG,
        n_sims=3000,
        seed=7,
    )
    strong_def = simulate_game(home_def_rtg=1.00, **base)
    average_def = simulate_game(home_def_rtg=1.10, **base)
    weak_def = simulate_game(home_def_rtg=1.25, **base)
    assert weak_def.win_prob_home < average_def.win_prob_home < strong_def.win_prob_home


def test_realistic_total_and_margin_sd_for_two_average_teams() -> None:
    result = simulate_game(
        home_off_rtg=1.146,
        home_def_rtg=1.146,
        home_pace=100.0,
        away_off_rtg=1.146,
        away_def_rtg=1.146,
        away_pace=100.0,
        league_avg_ppp=LEAGUE_AVG,
        n_sims=4000,
        seed=3,
        home_ppp_bonus=0.0,  # isolate symmetry from the home-court bonus below
    )
    # CLAUDE.md "Sim outputs": totals ~220+/-20, margin SD ~12-14. Allow a
    # wider band since this is a single matchup, not a season average.
    assert 190.0 < result.total_mean < 250.0
    assert 8.0 < result.margin_sd < 20.0
    assert 0.45 < result.win_prob_home < 0.55  # two identical teams, no home bonus


def test_home_ppp_bonus_gives_identical_teams_a_home_edge() -> None:
    result = simulate_game(
        home_off_rtg=1.146,
        home_def_rtg=1.146,
        home_pace=100.0,
        away_off_rtg=1.146,
        away_def_rtg=1.146,
        away_pace=100.0,
        league_avg_ppp=LEAGUE_AVG,
        n_sims=4000,
        seed=3,
    )
    assert result.win_prob_home > 0.5


def test_higher_pace_increases_total_points() -> None:
    kwargs = dict(
        home_off_rtg=1.12,
        home_def_rtg=1.12,
        away_off_rtg=1.12,
        away_def_rtg=1.12,
        league_avg_ppp=LEAGUE_AVG,
        n_sims=3000,
        seed=5,
    )
    slow = simulate_game(home_pace=90.0, away_pace=90.0, **kwargs)
    fast = simulate_game(home_pace=110.0, away_pace=110.0, **kwargs)
    assert slow.total_mean < fast.total_mean


def test_runs_end_to_end_without_nan() -> None:
    result = simulate_game(
        home_off_rtg=1.146,
        home_def_rtg=1.146,
        home_pace=99.6,
        away_off_rtg=1.146,
        away_def_rtg=1.146,
        away_pace=99.6,
        league_avg_ppp=LEAGUE_AVG,
        n_sims=200,
        seed=0,
    )
    for value in (
        result.win_prob_home,
        result.margin_mean,
        result.margin_sd,
        result.total_mean,
        result.total_sd,
    ):
        assert value == value  # NaN check (NaN != NaN)
