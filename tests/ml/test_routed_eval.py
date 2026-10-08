"""Unit tests for nba.eval.routed_eval (pure-array router)."""

from __future__ import annotations

import numpy as np

from nba.eval.routed_eval import (
    apply_routed_crps,
    evaluate_routing,
    learn_routing_map,
)


def test_learn_routing_map_picks_lowest_crps_per_bucket() -> None:
    buckets = np.array(["A", "A", "B", "B"])
    crps = {
        "sim": np.array([1.0, 1.0, 5.0, 5.0]),
        "savg": np.array([2.0, 2.0, 1.0, 1.0]),
    }
    assert learn_routing_map(buckets, crps) == {"A": "sim", "B": "savg"}


def test_apply_routed_crps_uses_map_then_default() -> None:
    buckets = np.array(["A", "B", "C"])  # C unseen in map -> default
    crps = {"sim": np.array([1.0, 9.0, 3.0]), "savg": np.array([8.0, 2.0, 4.0])}
    route_map = {"A": "sim", "B": "savg"}
    out = apply_routed_crps(buckets, crps, route_map, default_model="sim")
    assert list(out) == [1.0, 2.0, 3.0]


def test_evaluate_routing_beats_both_when_buckets_are_separable() -> None:
    # Two buckets, each model good at exactly one. Train/test split; the routed
    # predictor should beat BOTH single models out-of-sample.
    rng = np.random.default_rng(0)
    n = 400
    buckets = np.array(["cold"] * (n // 2) + ["smooth"] * (n // 2))
    # sim great on cold, poor on smooth; savg the reverse.
    sim = np.where(buckets == "cold", rng.normal(1.0, 0.1, n), rng.normal(3.0, 0.1, n))
    savg = np.where(buckets == "cold", rng.normal(3.0, 0.1, n), rng.normal(1.0, 0.1, n))
    crps = {"sim": sim, "savg": savg}
    train_mask = np.zeros(n, dtype=bool)
    train_mask[: n // 2 : 2] = True  # some cold
    train_mask[n // 2 :: 2] = True  # some smooth
    res = evaluate_routing("pts", buckets, crps, train_mask, n_boot=500, seed=0)
    assert res.route_map == {"cold": "sim", "smooth": "savg"}
    # routed CRPS ~1.0; both single models ~2.0 on test -> routed beats both (CI < 0).
    assert res.crps_routed < 1.3
    assert res.routed_vs_model["sim"].hi < 0
    assert res.routed_vs_model["savg"].hi < 0


def test_evaluate_routing_no_free_lunch_when_one_model_dominates() -> None:
    # If one model is uniformly better, routing should just pick it and NOT
    # claim to beat it (CI straddles 0).
    rng = np.random.default_rng(1)
    n = 300
    buckets = np.array(["x", "y"] * (n // 2))
    sim = rng.normal(1.0, 0.2, n)
    savg = rng.normal(2.0, 0.2, n)  # always worse
    crps = {"sim": sim, "savg": savg}
    train_mask = np.arange(n) % 2 == 0
    res = evaluate_routing("pts", buckets, crps, train_mask, n_boot=500, seed=0)
    assert set(res.route_map.values()) == {"sim"}
    # routed == sim, so routed-vs-sim CI must include 0 (no spurious win).
    assert res.routed_vs_model["sim"].lo <= 0 <= res.routed_vs_model["sim"].hi
