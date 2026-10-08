"""Tests for ``nba.eval.model_routing.route_by_bucket`` (synthetic
per-game CRPS arrays -- no DB needed, this function is pure)."""

from __future__ import annotations

import numpy as np
import pytest

from nba.eval.model_routing import route_by_bucket, routing_table_rows


def test_route_by_bucket_picks_the_lower_crps_model_per_bucket() -> None:
    rng = np.random.default_rng(0)
    n_per_bucket = 200
    labels = np.array(["low"] * n_per_bucket + ["high"] * n_per_bucket)

    # "low" bucket: model_a is clearly better (lower CRPS).
    crps_a = np.concatenate(
        [rng.normal(1.0, 0.1, n_per_bucket), rng.normal(5.0, 0.1, n_per_bucket)]
    )
    # "high" bucket: model_b is clearly better.
    crps_b = np.concatenate(
        [rng.normal(3.0, 0.1, n_per_bucket), rng.normal(2.0, 0.1, n_per_bucket)]
    )

    results = route_by_bucket(
        labels, {"model_a": crps_a, "model_b": crps_b}, min_bucket_n=30, n_boot=200, seed=0
    )
    by_bucket = {r.bucket: r for r in results}
    assert by_bucket["low"].best_model == "model_a"
    assert by_bucket["high"].best_model == "model_b"
    # model_b is worse than model_a in "low" -> CI on delta should be > 0.
    ci = by_bucket["low"].delta_vs_best_ci["model_b"]
    assert ci.lo > 0.0


def test_route_by_bucket_flags_insufficient_n() -> None:
    labels = np.array(["tiny"] * 5 + ["ok"] * 50)
    crps_a = np.random.default_rng(0).normal(1.0, 0.1, 55)
    crps_b = np.random.default_rng(1).normal(1.0, 0.1, 55)
    results = route_by_bucket(
        labels, {"a": crps_a, "b": crps_b}, min_bucket_n=30, n_boot=100, seed=0
    )
    by_bucket = {r.bucket: r for r in results}
    assert by_bucket["tiny"].sufficient is False
    assert "insufficient" in by_bucket["tiny"].note
    assert by_bucket["ok"].sufficient is True


def test_route_by_bucket_requires_at_least_one_model() -> None:
    with pytest.raises(ValueError, match="at least one entry"):
        route_by_bucket(np.array(["a"]), {})


def test_route_by_bucket_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="length"):
        route_by_bucket(np.array(["a", "b"]), {"m": np.array([1.0])})


def test_route_by_bucket_clustered_ci_wider_than_row_level() -> None:
    """docs/ACCEPTANCE_CRITERIA_2026-10-08.md: ``route_by_bucket`` must
    support ``cluster_ids`` and produce a wider (more conservative) CI than
    the row-level default under within-game correlation -- otherwise its
    CIs stay anti-conservative per the acceptance doc's prerequisite."""
    rng = np.random.default_rng(3)
    n_games, players_per_game = 60, 5
    n = n_games * players_per_game
    labels = np.array(["bucket"] * n)
    game_ids = np.repeat(np.arange(n_games), players_per_game)
    game_effects = np.repeat(rng.normal(0.0, 0.6, size=n_games), players_per_game)
    crps_b = rng.normal(1.0, 0.3, size=n) + rng.normal(0.0, 0.02, size=n)
    # model_a's delta vs model_b shares a per-game shock -- the within-game
    # correlation structure the clustered bootstrap exists to capture (see
    # tests/props/test_clustered_bootstrap.py's _make_correlated_games).
    crps_a = crps_b - 0.05 + game_effects + rng.normal(0.0, 0.02, size=n)

    row_level = route_by_bucket(
        labels, {"a": crps_a, "b": crps_b}, min_bucket_n=30, n_boot=2000, seed=0
    )
    clustered = route_by_bucket(
        labels,
        {"a": crps_a, "b": crps_b},
        min_bucket_n=30,
        n_boot=2000,
        seed=0,
        cluster_ids=game_ids,
    )
    row_ci = row_level[0].delta_vs_best_ci["b"]
    clustered_ci = clustered[0].delta_vs_best_ci["b"]
    row_half_width = (row_ci.hi - row_ci.lo) / 2.0
    clustered_half_width = (clustered_ci.hi - clustered_ci.lo) / 2.0
    assert clustered_half_width > row_half_width
    # Point estimate (plain mean of the delta) is unaffected by clustering.
    assert abs(row_ci.point - clustered_ci.point) < 1e-9


def test_route_by_bucket_cluster_ids_none_is_backward_compatible() -> None:
    labels = np.array(["a"] * 40)
    rng = np.random.default_rng(4)
    crps_a = rng.normal(size=40)
    crps_b = rng.normal(size=40)
    explicit_none = route_by_bucket(
        labels, {"a": crps_a, "b": crps_b}, min_bucket_n=30, n_boot=200, seed=0, cluster_ids=None
    )
    default = route_by_bucket(
        labels, {"a": crps_a, "b": crps_b}, min_bucket_n=30, n_boot=200, seed=0
    )
    ci_none = explicit_none[0].delta_vs_best_ci
    ci_default = default[0].delta_vs_best_ci
    for name in ci_none:
        assert ci_none[name].lo == ci_default[name].lo
        assert ci_none[name].hi == ci_default[name].hi


def test_routing_table_rows_tidy_output() -> None:
    labels = np.array(["a"] * 40 + ["b"] * 2)
    crps_a = np.full(42, 1.0)
    crps_b = np.full(42, 2.0)
    results = route_by_bucket(
        labels, {"x": crps_a, "y": crps_b}, min_bucket_n=30, n_boot=50, seed=0
    )
    rows = routing_table_rows(results)
    bucket_a_rows = [r for r in rows if r["bucket"] == "a"]
    bucket_b_rows = [r for r in rows if r["bucket"] == "b"]
    assert len(bucket_a_rows) == 2  # one row per model
    assert len(bucket_b_rows) == 1  # insufficient-n bucket collapses to one row
    assert all(r["best_model"] == "x" for r in bucket_a_rows)
