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
