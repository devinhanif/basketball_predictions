"""Cold-start method 1: empirical-Bayes shrinkage (CLAUDE.md, ship first).

For each rate ``r``:

    posterior = (n * r_obs + k * r_prior) / (n + k)

``n`` = possessions observed (or whatever sample-size unit the rate was
computed over), ``k`` = a per-stat pseudo-count. As ``n -> infinity`` the
posterior converges to ``r_obs``; as ``n -> 0`` it converges to ``r_prior``.
``k`` is tunable by walk-forward CV (:func:`tune_pseudo_count`) -- on data
too small to tune meaningfully (e.g. the committed fixture) this degrades
gracefully to the documented default in
``research.coldstart.config.DEFAULT_PSEUDO_COUNTS`` with a note, rather than
fabricating a tuned number.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def shrink_rate(
    r_obs: np.ndarray | float,
    n: np.ndarray | float,
    r_prior: np.ndarray | float,
    k: np.ndarray | float,
) -> np.ndarray:
    """Vectorized empirical-Bayes shrinkage posterior.

    Degenerate case ``n == 0 and k == 0`` (no observations, no prior
    pseudo-count either) falls back fully to ``r_prior`` rather than
    dividing by zero.
    """
    r_obs_a = np.asarray(r_obs, dtype=float)
    n_a = np.asarray(n, dtype=float)
    r_prior_a = np.asarray(r_prior, dtype=float)
    k_a = np.asarray(k, dtype=float)

    denom = n_a + k_a
    safe_denom = np.where(denom == 0, 1.0, denom)
    posterior = (n_a * r_obs_a + k_a * r_prior_a) / safe_denom
    posterior = np.where(denom == 0, r_prior_a, posterior)
    return np.asarray(posterior, dtype=float)


def classify_source(n: np.ndarray | float, full_trust_n: float = 1000.0) -> np.ndarray:
    """Label a rate's provenance for ``player_rates.source`` per CLAUDE.md schema.

    'prior': no observations at all (n == 0). 'blended': some observations
    but still shrunk (0 < n < full_trust_n). 'observed': enough observations
    that shrinkage barely moves the estimate (n >= full_trust_n).
    """
    n_a = np.asarray(n, dtype=float)
    out = np.full(n_a.shape, "blended", dtype=object)
    out[n_a <= 0] = "prior"
    out[n_a >= full_trust_n] = "observed"
    return out


@dataclass
class PseudoCountTuneResult:
    stat: str
    k: float
    candidates_tried: list[float]
    note: str


def tune_pseudo_count(
    n_obs: np.ndarray,
    r_obs: np.ndarray,
    r_prior: np.ndarray,
    r_future: np.ndarray,
    stat: str,
    default_k: float,
    candidates: list[float] | None = None,
    min_folds: int = 5,
) -> PseudoCountTuneResult:
    """Pick ``k`` by minimizing squared error of the shrunk estimate against
    a strictly-later observed rate (``r_future``), i.e. walk-forward CV: fit
    (choose k) on data available "now", validate against what actually
    happened next. Each row is one (player, as_of) walk-forward fold.

    Degrades gracefully: with fewer than ``min_folds`` usable rows, tuning
    is not statistically meaningful on this little data (true of the
    committed 3-game fixture) -- returns ``default_k`` with a note instead
    of claiming a tuned value.
    """
    candidates = candidates or [10.0, 50.0, 100.0, 150.0, 250.0, 500.0, 1000.0]
    n_a = np.asarray(n_obs, dtype=float)
    r_obs_a = np.asarray(r_obs, dtype=float)
    r_prior_a = np.asarray(r_prior, dtype=float)
    r_future_a = np.asarray(r_future, dtype=float)

    mask = ~(np.isnan(n_a) | np.isnan(r_obs_a) | np.isnan(r_prior_a) | np.isnan(r_future_a))
    n_valid = int(mask.sum())
    if n_valid < min_folds:
        return PseudoCountTuneResult(
            stat=stat,
            k=default_k,
            candidates_tried=[],
            note=(
                f"only {n_valid} walk-forward fold(s) available (<{min_folds}); "
                "insufficient data to tune k meaningfully, using documented default"
            ),
        )

    best_k = default_k
    best_mse = float("inf")
    for k in candidates:
        posterior = shrink_rate(r_obs_a[mask], n_a[mask], r_prior_a[mask], k)
        mse = float(np.mean((posterior - r_future_a[mask]) ** 2))
        if mse < best_mse:
            best_mse = mse
            best_k = k
    return PseudoCountTuneResult(stat=stat, k=best_k, candidates_tried=candidates, note="")
