"""Cold-start method 2: archetype priors via clustering.

Cluster players on height, weight, position, and prior-season tendencies;
a new/cold player's prior is their cluster's mean rate. Cluster count is
picked by backtest (:func:`select_n_clusters_by_backtest`), not by eye, per
CLAUDE.md -- though on data too small to compare candidates meaningfully
this degrades to the documented default
(``nba.coldstart.config.DEFAULT_N_CLUSTERS``) with a note.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

#: Coarse numeric encoding for position strings, used as one of the
#: clustering features. Combo positions average their components.
POSITION_ENCODING: dict[str, float] = {
    "G": 0.0,
    "G-F": 0.5,
    "F": 1.0,
    "F-G": 0.5,
    "F-C": 1.5,
    "C-F": 1.5,
    "C": 2.0,
}


def encode_position(position: str | None) -> float:
    if position is None:
        return 1.0  # unknown -> neutral (forward-ish) midpoint
    return POSITION_ENCODING.get(position, 1.0)


@dataclass
class ArchetypeModel:
    n_clusters: int
    scaler: StandardScaler
    kmeans: KMeans
    cluster_target_means: np.ndarray  # shape (n_clusters, n_targets)
    seed: int


def fit_archetypes(
    features: np.ndarray,
    targets: np.ndarray,
    n_clusters: int,
    seed: int = 0,
) -> ArchetypeModel:
    """Fit k-means on ``features`` (e.g. height, weight, position, prior
    tendencies) and compute each cluster's mean of ``targets`` (the rate
    stat(s) to use as the archetype prior). Deterministic given ``seed``.
    """
    if features.ndim != 2:
        raise ValueError("features must be a 2-D array (n_players, n_features)")
    if targets.ndim == 1:
        targets = targets.reshape(-1, 1)
    n_clusters = max(1, min(n_clusters, features.shape[0]))

    scaler = StandardScaler()
    X = scaler.fit_transform(features)
    kmeans = KMeans(n_clusters=n_clusters, random_state=seed, n_init=10)
    labels = kmeans.fit_predict(X)

    cluster_means = np.zeros((n_clusters, targets.shape[1]))
    overall_mean = targets.mean(axis=0)
    for c in range(n_clusters):
        mask = labels == c
        cluster_means[c] = targets[mask].mean(axis=0) if mask.any() else overall_mean

    return ArchetypeModel(
        n_clusters=n_clusters,
        scaler=scaler,
        kmeans=kmeans,
        cluster_target_means=cluster_means,
        seed=seed,
    )


def predict_archetype_prior(model: ArchetypeModel, features: np.ndarray) -> np.ndarray:
    """Prior for each row of ``features`` = its assigned cluster's mean target."""
    X = model.scaler.transform(features)
    labels = model.kmeans.predict(X)
    return np.asarray(model.cluster_target_means[labels], dtype=float)


def select_n_clusters_by_backtest(
    features: np.ndarray,
    targets: np.ndarray,
    candidate_ks: list[int] | None = None,
    seed: int = 0,
    min_rows_per_candidate: int = 10,
    default_k: int = 4,
) -> tuple[int, str]:
    """Pick the cluster count minimizing held-out squared error of the
    archetype prior against ``targets``, via a simple random split
    (repeated with the given seed, so deterministic) -- not "by eye" per
    CLAUDE.md. Degrades to ``default_k`` with a note when there are too few
    rows to hold any out meaningfully.
    """
    candidate_ks = candidate_ks or [2, 3, 4, 6, 8]
    n_rows = features.shape[0]
    if n_rows < min_rows_per_candidate:
        return default_k, (
            f"only {n_rows} players available (<{min_rows_per_candidate}); insufficient data to "
            "select n_clusters by backtest, using documented default"
        )

    rng = np.random.default_rng(seed)
    idx = rng.permutation(n_rows)
    split = max(1, int(n_rows * 0.7))
    train_idx, test_idx = idx[:split], idx[split:]
    if len(test_idx) == 0:
        return default_k, "holdout split was empty; using documented default"

    best_k = default_k
    best_mse = float("inf")
    for k in candidate_ks:
        if k > len(train_idx):
            continue
        model = fit_archetypes(features[train_idx], targets[train_idx], n_clusters=k, seed=seed)
        preds = predict_archetype_prior(model, features[test_idx])
        mse = float(np.mean((preds.reshape(preds.shape[0], -1) - targets[test_idx]) ** 2))
        if mse < best_mse:
            best_mse = mse
            best_k = k
    return best_k, ""
