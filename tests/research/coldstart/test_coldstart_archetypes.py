"""Unit tests for cold-start method 2 (archetype priors via clustering).

Synthetic inputs constructed here (not tests/fixtures/, owned by another
engineer).
"""

from __future__ import annotations

import numpy as np

from research.coldstart.archetypes import (
    encode_position,
    fit_archetypes,
    predict_archetype_prior,
    select_n_clusters_by_backtest,
)


def _two_group_synthetic(seed: int = 0, n_per_group: int = 20) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    # Group A: short/light guards with low usage. Group B: tall/heavy
    # centers with high usage. Clustering on height/weight should recover
    # these two groups cleanly.
    heights_a = rng.normal(74, 1, n_per_group)
    weights_a = rng.normal(190, 5, n_per_group)
    usage_a = rng.normal(0.18, 0.01, n_per_group)

    heights_b = rng.normal(83, 1, n_per_group)
    weights_b = rng.normal(255, 5, n_per_group)
    usage_b = rng.normal(0.24, 0.01, n_per_group)

    features = np.column_stack(
        [
            np.concatenate([heights_a, heights_b]),
            np.concatenate([weights_a, weights_b]),
        ]
    )
    targets = np.concatenate([usage_a, usage_b])
    return features, targets


def test_encode_position_known_and_unknown() -> None:
    assert encode_position("G") == 0.0
    assert encode_position("C") == 2.0
    assert encode_position(None) == 1.0
    assert encode_position("totally-unknown") == 1.0


def test_fit_archetypes_is_deterministic_given_seed() -> None:
    features, targets = _two_group_synthetic()
    model_a = fit_archetypes(features, targets, n_clusters=2, seed=7)
    model_b = fit_archetypes(features, targets, n_clusters=2, seed=7)
    np.testing.assert_allclose(
        sorted(model_a.cluster_target_means.ravel()),
        sorted(model_b.cluster_target_means.ravel()),
    )


def test_fit_archetypes_recovers_two_distinct_cluster_means() -> None:
    features, targets = _two_group_synthetic()
    model = fit_archetypes(features, targets, n_clusters=2, seed=0)
    means = sorted(model.cluster_target_means.ravel())
    # the two synthetic groups' usage means are ~0.18 and ~0.24
    assert means[0] < 0.21 < means[1]


def test_predict_archetype_prior_shape() -> None:
    features, targets = _two_group_synthetic()
    model = fit_archetypes(features, targets, n_clusters=2, seed=0)
    new_players = np.array([[74.0, 190.0], [83.0, 255.0]])
    preds = predict_archetype_prior(model, new_players)
    assert preds.shape == (2, 1)
    assert preds[0, 0] < preds[1, 0]


def test_select_n_clusters_degrades_gracefully_on_tiny_data() -> None:
    features = np.array([[74.0, 190.0], [83.0, 255.0]])
    targets = np.array([0.18, 0.24])
    k, note = select_n_clusters_by_backtest(features, targets, seed=0, min_rows_per_candidate=10)
    assert k == 4  # documented default
    assert "insufficient data" in note


def test_select_n_clusters_by_backtest_with_enough_data() -> None:
    features, targets = _two_group_synthetic(n_per_group=30)
    k, note = select_n_clusters_by_backtest(
        features, targets, candidate_ks=[1, 2, 3], seed=0, min_rows_per_candidate=10
    )
    assert note == ""
    assert k in (1, 2, 3)
