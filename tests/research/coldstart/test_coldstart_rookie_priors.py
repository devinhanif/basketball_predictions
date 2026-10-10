"""Unit tests for cold-start method 3 (rookie priors from draft + college).

Synthetic inputs constructed here (not tests/fixtures/).
"""

from __future__ import annotations

import numpy as np

from research.coldstart.rookie_priors import (
    build_feature_matrix,
    fit_rookie_prior,
    predict_rookie_prior,
)


def test_build_feature_matrix_flags_missing_college_stats() -> None:
    draft_pick = np.array([1.0, np.nan, 30.0])
    age = np.array([19.0, 22.0, 21.0])
    height = np.array([78.0, 74.0, 80.0])
    position = ["F", "G", "C"]
    college_stats = np.array([[20.0, 5.0], [np.nan, np.nan], [15.0, 10.0]])

    X, has_college = build_feature_matrix(draft_pick, age, height, position, college_stats)
    assert has_college.tolist() == [True, False, True]
    # undrafted player's missing draft_pick is imputed to replacement level (61),
    # never silently dropped (CLAUDE.md: draft slot must never be skipped).
    assert X[1, 0] == 61.0
    assert X.shape == (3, 6)  # 4 base cols + 2 college cols


def test_build_feature_matrix_without_college_stats_marks_all_missing() -> None:
    draft_pick = np.array([1.0, 30.0])
    age = np.array([19.0, 21.0])
    height = np.array([78.0, 80.0])
    position = ["F", "C"]
    X, has_college = build_feature_matrix(draft_pick, age, height, position, college_stats=None)
    assert has_college.tolist() == [False, False]
    assert X.shape == (2, 4)


def test_fit_predict_rookie_prior_deterministic_given_seed() -> None:
    rng = np.random.default_rng(0)
    n = 50
    draft_pick = rng.integers(1, 60, size=n).astype(float)
    age = rng.uniform(19, 23, size=n)
    height = rng.uniform(72, 84, size=n)
    position = ["F"] * n
    college_stats = np.column_stack([rng.uniform(10, 25, n), rng.uniform(2, 8, n)])
    # true relationship: better (lower) draft pick -> higher first-season usage
    y = 0.3 - 0.002 * draft_pick + rng.normal(0, 0.005, n)

    X, has_college = build_feature_matrix(draft_pick, age, height, position, college_stats)
    model_a = fit_rookie_prior(X, y, seed=3)
    model_b = fit_rookie_prior(X, y, seed=3)
    preds_a = predict_rookie_prior(model_a, X, has_college, archetype_fallback=np.full(n, 0.2))
    preds_b = predict_rookie_prior(model_b, X, has_college, archetype_fallback=np.full(n, 0.2))
    np.testing.assert_allclose(preds_a, preds_b)

    # draft slot is the strongest signal: a #1 pick should get a higher
    # predicted rate than a #59 pick, holding everything else fixed.
    x_top_pick, _ = build_feature_matrix(
        np.array([1.0]), np.array([20.0]), np.array([78.0]), ["F"], np.array([[18.0, 5.0]])
    )
    x_late_pick, _ = build_feature_matrix(
        np.array([59.0]), np.array([20.0]), np.array([78.0]), ["F"], np.array([[18.0, 5.0]])
    )
    pred_top = predict_rookie_prior(
        model_a, x_top_pick, np.array([True]), archetype_fallback=np.array([0.2])
    )
    pred_late = predict_rookie_prior(
        model_a, x_late_pick, np.array([True]), archetype_fallback=np.array([0.2])
    )
    assert pred_top[0] > pred_late[0]


def test_predict_rookie_prior_falls_back_to_archetype_when_college_missing() -> None:
    rng = np.random.default_rng(0)
    n = 30
    draft_pick = rng.integers(1, 60, size=n).astype(float)
    age = rng.uniform(19, 23, size=n)
    height = rng.uniform(72, 84, size=n)
    position = ["G"] * n
    college_stats = np.column_stack([rng.uniform(10, 25, n), rng.uniform(2, 8, n)])
    y = rng.uniform(0.1, 0.3, n)

    X, has_college = build_feature_matrix(draft_pick, age, height, position, college_stats)
    model = fit_rookie_prior(X[has_college], y[has_college], seed=0)

    # one new player with no college stats at all
    x_new, has_college_new = build_feature_matrix(
        np.array([15.0]), np.array([20.0]), np.array([79.0]), ["G"], np.array([[np.nan, np.nan]])
    )
    fallback = np.array([0.42])
    out = predict_rookie_prior(model, x_new, has_college_new, archetype_fallback=fallback)
    assert out[0] == 0.42
