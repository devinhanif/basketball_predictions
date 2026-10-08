"""Clustered (per-game) bootstrap correctness tests.

Statistician finding (docs/FDR_AUDIT_2026-10-08.md section 0): resampling
individual player-games ignores that teammates in the same game are
correlated, which understates the true standard error -- every CI built
that way is anti-conservative (too narrow). ``paired_score_delta_ci`` and
``mean_bias_ci`` now accept an optional ``cluster_ids`` array (one id per
row, e.g. ``game_id``) that switches the resampling unit from "row" to
"whole cluster". These tests prove:

1. On synthetic data with strong within-cluster correlation, the clustered
   CI is strictly wider than the row-level (iid) CI.
2. In the degenerate case where every cluster is a singleton, the clustered
   result is numerically identical to the iid result (same RNG path).
3. Both bootstraps are seed-deterministic.
"""

from __future__ import annotations

import numpy as np

from nba.props.metrics import mean_bias_ci, paired_score_delta_ci


def _make_correlated_games(
    n_games: int, players_per_game: int, game_effect_sd: float, player_noise_sd: float, seed: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Synthetic per-player-game scores with a shared per-game shock.

    Returns ``(score_a, score_b, game_ids)`` where ``score_a - score_b`` has
    mean ``delta`` plus a per-game random effect shared by every player in
    that game (the correlation structure the clustered bootstrap exists to
    capture) plus independent per-player noise.
    """
    rng = np.random.default_rng(seed)
    delta = -0.05
    game_effects = rng.normal(0.0, game_effect_sd, size=n_games)
    game_ids = np.repeat(np.arange(n_games), players_per_game)
    player_noise = rng.normal(0.0, player_noise_sd, size=n_games * players_per_game)
    score_b = rng.normal(1.0, 0.3, size=n_games * players_per_game)
    score_a = score_b + delta + np.repeat(game_effects, players_per_game) + player_noise
    return score_a, score_b, game_ids


def test_clustered_ci_wider_than_iid_under_within_game_correlation() -> None:
    score_a, score_b, game_ids = _make_correlated_games(
        n_games=80,
        players_per_game=5,
        game_effect_sd=0.5,  # large shared per-game shock -> strong correlation
        player_noise_sd=0.02,  # tiny independent noise on top
        seed=7,
    )
    iid_ci = paired_score_delta_ci(score_a, score_b, n_boot=2000, seed=0)
    clustered_ci = paired_score_delta_ci(
        score_a, score_b, n_boot=2000, seed=0, cluster_ids=game_ids
    )

    iid_half_width = (iid_ci.hi - iid_ci.lo) / 2.0
    clustered_half_width = (clustered_ci.hi - clustered_ci.lo) / 2.0

    assert clustered_half_width > iid_half_width
    # Point estimates (plain means) are identical -- only the CI width changes.
    assert abs(iid_ci.point - clustered_ci.point) < 1e-9


def test_clustered_ci_wider_than_iid_mean_bias() -> None:
    """Same property for mean_bias_ci (used for the +/-0.5 bias target)."""
    pred, y, game_ids = _make_correlated_games(
        n_games=60,
        players_per_game=4,
        game_effect_sd=0.6,
        player_noise_sd=0.01,
        seed=11,
    )
    iid_ci = mean_bias_ci(pred, y, n_boot=2000, seed=0)
    clustered_ci = mean_bias_ci(pred, y, n_boot=2000, seed=0, cluster_ids=game_ids)

    iid_half_width = (iid_ci.hi - iid_ci.lo) / 2.0
    clustered_half_width = (clustered_ci.hi - clustered_ci.lo) / 2.0
    assert clustered_half_width > iid_half_width


def test_singleton_clusters_match_iid_bootstrap_exactly() -> None:
    """When every row is its own cluster, the clustered path must reduce to
    the exact same resample arithmetic as the row-level (iid) bootstrap --
    same RNG seed, same ``_MAX_BOOTSTRAP_CELLS`` batching formula (``n``
    rows == ``n`` clusters), same mean-of-resampled-rows computation."""
    rng = np.random.default_rng(42)
    n = 150
    score_a = rng.normal(0.0, 1.0, size=n)
    score_b = rng.normal(0.0, 1.0, size=n)
    singleton_ids = np.arange(n)  # one row per cluster, already in row order

    iid_ci = paired_score_delta_ci(score_a, score_b, n_boot=1000, seed=3)
    clustered_ci = paired_score_delta_ci(
        score_a, score_b, n_boot=1000, seed=3, cluster_ids=singleton_ids
    )

    assert clustered_ci.point == iid_ci.point
    assert np.isclose(clustered_ci.lo, iid_ci.lo, atol=1e-10)
    assert np.isclose(clustered_ci.hi, iid_ci.hi, atol=1e-10)


def test_singleton_clusters_match_iid_mean_bias_exactly() -> None:
    rng = np.random.default_rng(5)
    n = 80
    pred = rng.normal(10.0, 2.0, size=n)
    y = rng.normal(10.0, 2.0, size=n)
    singleton_ids = np.arange(n)

    iid_ci = mean_bias_ci(pred, y, n_boot=1000, seed=1)
    clustered_ci = mean_bias_ci(pred, y, n_boot=1000, seed=1, cluster_ids=singleton_ids)

    assert np.isclose(clustered_ci.lo, iid_ci.lo, atol=1e-10)
    assert np.isclose(clustered_ci.hi, iid_ci.hi, atol=1e-10)


def test_clustered_bootstrap_is_seed_deterministic() -> None:
    score_a, score_b, game_ids = _make_correlated_games(
        n_games=40, players_per_game=5, game_effect_sd=0.3, player_noise_sd=0.05, seed=2
    )
    ci_1 = paired_score_delta_ci(score_a, score_b, n_boot=500, seed=99, cluster_ids=game_ids)
    ci_2 = paired_score_delta_ci(score_a, score_b, n_boot=500, seed=99, cluster_ids=game_ids)
    assert ci_1.lo == ci_2.lo
    assert ci_1.hi == ci_2.hi


def test_cluster_ids_none_is_unchanged_backward_compatible_default() -> None:
    """``cluster_ids=None`` (the default) must be byte-identical to calling
    with no ``cluster_ids`` kwarg at all -- no other caller should see any
    behavior change from this patch."""
    rng = np.random.default_rng(13)
    n = 50
    score_a = rng.normal(size=n)
    score_b = rng.normal(size=n)
    explicit_none = paired_score_delta_ci(score_a, score_b, n_boot=500, seed=0, cluster_ids=None)
    default = paired_score_delta_ci(score_a, score_b, n_boot=500, seed=0)
    assert explicit_none.lo == default.lo
    assert explicit_none.hi == default.hi
    assert explicit_none.point == default.point


def test_reliability_note_gates_on_cluster_count_not_row_count() -> None:
    """docs/QA_AUDIT_2026-10-08.md item 2c: a few-games/many-players slate
    (g small, n large) must be flagged as unreliable -- the old
    row-count-only gate would have cleared this silently since n=50 >= 30."""
    rng = np.random.default_rng(0)
    n_games, players_per_game = 5, 10  # g=5 (few), n=50 (would pass a row-count-only gate)
    game_ids = np.repeat(np.arange(n_games), players_per_game)
    pred = rng.normal(10.0, 1.0, size=n_games * players_per_game)
    y = rng.normal(10.0, 1.0, size=n_games * players_per_game)

    ci = mean_bias_ci(pred, y, n_boot=500, seed=0, cluster_ids=game_ids)

    assert ci.n == n_games * players_per_game  # row count unchanged
    assert "insufficient data for a reliable CI" in ci.note
    assert "g=5" in ci.note


def test_reliability_note_clear_when_cluster_count_is_large() -> None:
    """Sanity check the gate's other side: plenty of distinct games -> no
    caveat, even though this is the exact same code path."""
    rng = np.random.default_rng(1)
    n_games, players_per_game = 40, 5  # g=40 >= MIN_RELIABLE_N=30
    game_ids = np.repeat(np.arange(n_games), players_per_game)
    score_a = rng.normal(size=n_games * players_per_game)
    score_b = rng.normal(size=n_games * players_per_game)

    ci = paired_score_delta_ci(score_a, score_b, n_boot=500, seed=0, cluster_ids=game_ids)

    assert ci.note == ""


def test_mismatched_cluster_ids_length_raises() -> None:
    score_a = np.array([1.0, 2.0, 3.0])
    score_b = np.array([0.5, 1.5, 2.5])
    bad_ids = np.array([0, 1])  # wrong length
    try:
        paired_score_delta_ci(score_a, score_b, cluster_ids=bad_ids)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for mismatched cluster_ids length")
