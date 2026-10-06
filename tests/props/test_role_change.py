"""Tests for CUSUM role-change detection (CLAUDE.md "borrowed from
manufacturing QC") and its cold-start-weight wiring."""

from __future__ import annotations

import numpy as np

from nba.props.role_change import RoleChangeConfig, cusum_detect, k_multiplier


def test_flat_series_yields_no_false_changepoint() -> None:
    # Slightly more conservative than the module defaults (still a
    # perfectly reasonable CUSUM tuning) to keep this a non-flaky check of
    # "no real shift -> no flag": with the module's more sensitive
    # defaults, CUSUM's known false-alarm rate on ~40 samples of pure
    # noise is non-negligible by design (that is the standard ARL0/ARL1
    # tradeoff in quality control, not a bug).
    rng = np.random.default_rng(0)
    flat = 24.0 + rng.normal(0.0, 0.5, size=40)  # steady ~24 minutes, small noise
    assert cusum_detect(flat, threshold=5.0, drift=0.75) == []


def test_constant_series_yields_no_changepoint() -> None:
    assert cusum_detect(np.full(20, 24.0)) == []


def test_level_shift_is_flagged_near_the_injected_index() -> None:
    rng = np.random.default_rng(1)
    before = 15.0 + rng.normal(0.0, 1.0, size=20)  # bench role, ~15 min
    after = 32.0 + rng.normal(0.0, 1.0, size=20)  # starter role, ~32 min
    series = np.concatenate([before, after])
    shift_index = 20
    flags = cusum_detect(series, threshold=4.0, drift=0.5)
    assert flags, "expected at least one flag after an obvious level shift"
    # CUSUM accumulates evidence over a few games after the true shift, so
    # "near" means within a generous window, not exactly index 20.
    assert any(shift_index - 2 <= f <= shift_index + 10 for f in flags)


def test_cusum_is_deterministic() -> None:
    rng = np.random.default_rng(2)
    series = np.concatenate([rng.normal(10, 1, 15), rng.normal(30, 1, 15)])
    assert cusum_detect(series) == cusum_detect(series)


def test_cusum_handles_degenerate_inputs_without_crashing() -> None:
    assert cusum_detect(np.array([])) == []
    assert cusum_detect(np.array([5.0])) == []


def test_flagged_change_increases_cold_start_weight() -> None:
    cfg = RoleChangeConfig(raise_factor=3.0, decay_games=8.0)
    assert k_multiplier(None, cfg) == 1.0
    just_flagged = k_multiplier(0.0, cfg)
    assert just_flagged > 1.0
    assert just_flagged == 1.0 + cfg.raise_factor
    # Decays back toward 1.0 as more games pass since the flagged change.
    mid = k_multiplier(4.0, cfg)
    long_after = k_multiplier(100.0, cfg)
    assert 1.0 < mid < just_flagged
    assert long_after == 1.0


def test_build_role_change_features_on_fixture_is_nan_free_and_deterministic() -> None:
    from nba.props.role_change import build_role_change_features
    from tests.fixtures.loader import build_fixture_db

    con = build_fixture_db(":memory:")
    try:
        feats_a = build_role_change_features(con)
        feats_b = build_role_change_features(con)
    finally:
        con.close()
    assert feats_a.height == 18
    k = feats_a.select("k_multiplier").to_series().to_numpy()
    assert not np.isnan(k).any()
    assert (k >= 1.0).all()
    assert feats_a.equals(feats_b)


def test_build_role_change_features_never_sees_the_target_games_own_future_minutes() -> None:
    """No-leakage check: the first game for every player must have no
    changepoint flagged (there is no prior history to run CUSUM over at
    all), regardless of what that player does later in the fixture."""
    from nba.props.role_change import build_role_change_features
    from tests.fixtures.loader import build_fixture_db

    con = build_fixture_db(":memory:")
    try:
        feats = build_role_change_features(con)
    finally:
        con.close()
    first_games = (
        feats.sort(["player_id", "game_date", "game_id"])
        .group_by("player_id", maintain_order=True)
        .first()
    )
    assert (first_games.select("k_multiplier").to_series().to_numpy() == 1.0).all()
