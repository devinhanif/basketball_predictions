"""Combo stat distributions: PRA, P+R, P+A, R+A (CLAUDE.md Phase 2, milestone 8).

Scope note: combos are built from the *pre-coherence* per-stat component
moments (the compound frequency-severity moments computed in
``research.props.stat_models``, before ``research.props.coherence`` reconciles each
base stat's own player-sums to its team total). Hierarchical coherence is a
property of a single stat summed across *players on a team*; it has no
natural analogue for a *derived combo stat for one player*, so applying it
to combos is out of scope here and the combo moments are computed straight
from each base stat's own fitted mean/variance.

**Correlation method (do NOT assume independence):** points, rebounds, and
assists for the *same player in the same game* are positively correlated --
a big-minutes game tends to lift all three together. This module uses a
**documented moment-matched sum**: each combo's variance is the sum of its
components' variances *plus* the covariance cross-terms implied by a single
global pairwise Pearson correlation per stat pair,

    Var[A+B] = Var[A] + Var[B] + 2*rho_AB*sqrt(Var[A]*Var[B])

and the resulting (mean, variance) is refit as a Gamma via method-of-moments
(reusing ``nba.props.distributions.gamma_from_moments``) to get a full
distribution, not just a mean. **Stated assumption:** one global
``rho_AB`` per pair (pts-reb, pts-ast, reb-ast) is a crude but honest
summary of the *within-player* dependence structure -- it captures the
right sign and rough magnitude of the "good night lifts everything"
effect on the combo's spread, but it is not a per-player, per-game joint
sample, and it does not capture tail dependence or skew interactions. A
full within-player copula (sampling each marginal under a learned
correlation/copula structure) is the documented, heavier alternative this
intentionally substitutes for -- flagged here as a scope cut, not hidden.

The correlation itself is estimated *within player* (each player's own
games are demeaned by that player's own mean before correlating) rather
than pooled across players, specifically to avoid the across-player
confound where e.g. centers have high rebounds and low assists by role --
that pattern would bias a naive pooled correlation toward a negative
pts/ast-reb relationship that has nothing to do with game-to-game
co-movement for a single player.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import polars as pl

from nba.props.distributions import GammaDist, gamma_from_moments

#: Combo name -> the base stats it sums.
COMBOS: dict[str, tuple[str, ...]] = {
    "pra": ("pts", "reb", "ast"),
    "pr": ("pts", "reb"),
    "pa": ("pts", "ast"),
    "ra": ("reb", "ast"),
}

#: P(combo >= N) thresholds, span a plausible range per combo.
COMBO_THRESHOLDS: dict[str, list[int]] = {
    "pra": [20, 30, 40, 50],
    "pr": [15, 20, 25, 30],
    "pa": [15, 20, 25, 30],
    "ra": [8, 12, 16, 20],
}

#: Documented default pairwise within-player correlations -- a positive,
#: moderate prior ("a big game tends to lift everything a little"), used
#: whenever there isn't enough as-of history to estimate the pair directly.
DEFAULT_CORRELATION: dict[tuple[str, str], float] = {
    ("pts", "reb"): 0.25,
    ("pts", "ast"): 0.20,
    ("reb", "ast"): 0.15,
}

#: Minimum player-games with sufficient within-player variance before the
#: *estimated* correlation is trusted over the documented default.
MIN_PAIRS_FOR_CORR = 30


def _corr_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if (a, b) in DEFAULT_CORRELATION else (b, a)


def estimate_within_player_correlation(
    con: duckdb.DuckDBPyConnection, min_pairs: int = MIN_PAIRS_FOR_CORR
) -> dict[tuple[str, str], float]:
    """Pearson correlation of each (pts, reb, ast) pair's within-player residuals.

    See module docstring for the "within-player" demeaning rationale. Falls
    back to :data:`DEFAULT_CORRELATION` (fully or per-pair) when there is
    too little data, or a pair has ~zero within-player variance, to
    estimate a meaningful correlation (true of the committed 3-game
    fixture).
    """
    con.execute("SELECT player_id, pts, reb, ast FROM player_game_stats")
    cols = [d[0] for d in con.description]
    rows = con.fetchall()
    out = dict(DEFAULT_CORRELATION)
    if not rows:
        return out
    schema = {c: (pl.Int64 if c == "player_id" else pl.Float64) for c in cols}
    df = pl.DataFrame(rows, schema=schema, orient="row")
    if df.height < min_pairs:
        return out
    df = df.with_columns(
        [
            (pl.col(stat) - pl.col(stat).mean().over("player_id")).alias(f"d_{stat}")
            for stat in ("pts", "reb", "ast")
        ]
    )
    for a, b in DEFAULT_CORRELATION:
        da = df.select(f"d_{a}").to_series().to_numpy()
        db = df.select(f"d_{b}").to_series().to_numpy()
        if np.std(da) < 1e-9 or np.std(db) < 1e-9:
            continue
        rho = float(np.corrcoef(da, db)[0, 1])
        if np.isfinite(rho):
            out[(a, b)] = float(np.clip(rho, -0.95, 0.95))
    return out


@dataclass
class ComboMoments:
    mean: np.ndarray
    var: np.ndarray


def combo_moments(
    component_means: dict[str, np.ndarray],
    component_vars: dict[str, np.ndarray],
    components: tuple[str, ...],
    correlation: dict[tuple[str, str], float],
) -> ComboMoments:
    """Moment-matched sum of correlated marginals -- see module docstring."""
    mean = np.zeros_like(component_means[components[0]], dtype=float)
    var = np.zeros_like(component_vars[components[0]], dtype=float)
    for s in components:
        mean = mean + component_means[s]
        var = var + component_vars[s]
    for i, a in enumerate(components):
        for b in components[i + 1 :]:
            rho = correlation.get(_corr_key(a, b), 0.0)
            cov = (
                2.0
                * rho
                * np.sqrt(
                    np.clip(component_vars[a], 0.0, None) * np.clip(component_vars[b], 0.0, None)
                )
            )
            var = var + cov
    var = np.clip(var, 1e-6, None)
    return ComboMoments(mean=np.asarray(mean, dtype=float), var=np.asarray(var, dtype=float))


def build_combo_distributions(moments: ComboMoments) -> list[GammaDist]:
    """Gamma-via-method-of-moments fit for each combo's (mean, variance) --
    non-negative support, right-skewed, matching the combo's own fitted
    moments exactly (CDF/quantiles then follow from the Gamma family's
    already-tested implementation in ``nba.props.distributions``)."""
    return [
        gamma_from_moments(float(m), float(v))
        for m, v in zip(moments.mean, moments.var, strict=True)
    ]
