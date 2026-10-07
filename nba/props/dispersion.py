"""Native predictive-dispersion calibration (CLAUDE.md Phase 2: "calibrated
P(stat >= N)" and the 80%-interval-coverage target).

Real 4-season validation (n=138,409/stat) found the *native* (pre-conformal)
80% interval badly miscalibrated in both directions: points at 0.54 (far too
narrow -- the compound frequency-severity variance under-states real
game-to-game scoring variance) and rebounds/assists/3PM at ~0.96 (far too
wide -- the Negative Binomial dispersion, shrunk toward one global default
per ``nba.props.config.CountStatConfig.default_alpha``, over-states real
count variance for most players).

Fix: a single scalar **variance multiplier per stat**, fit once on a
strictly chronologically-earlier calibration split (never random -- same
discipline as ``nba.props.conformal.chronological_split``), that scales the
distribution's method-of-moments variance *before* the family is refit
(Gamma shape/scale or NegBin mu/alpha), so the native interval's own width
moves toward nominal coverage instead of relying solely on the conformal
wrapper to paper over a systematically wrong variance. This is a property
of the *model*, reported as "native 80% coverage"; split-conformal
(``nba.props.conformal``, now two-sided) is the separate, distribution-free
safety net applied on top of this.

Real 4-season validation of the *full* recalibration (multiplier chosen to
land calibration-split coverage as close to 80% as the grid allows) found
it over-corrects: it wins on native 80% coverage but hands back more CRPS
than it should (points CRPS 3.66 -> 4.03, assists 1.00 -> 1.47), because
pushing coverage all the way to nominal on a *calibration* split can chase
split-specific noise in the tails rather than the stable part of the
miscalibration. ``dispersion_strength`` (``DispersionConfig.dispersion_strength``,
default ``0.5``) is a single tunable knob in ``[0.0, 1.0]`` that linearly
blends the no-op multiplier (``1.0``, i.e. the original fitted dispersion)
with the fully-calibrated multiplier found by the grid search:

    blended_multiplier = 1.0 + dispersion_strength * (full_multiplier - 1.0)

This blend is in **variance-multiplier space**, which is equivalent to a
direct blend of the two *variances* themselves (since
``apply_dispersion_multiplier`` scales ``var`` by the multiplier linearly):

    var_applied = (1 - s) * var_original + s * var_fully_calibrated

Variance space (rather than the family's native dispersion parameter --
Gamma ``scale``/NegBin ``alpha``) is the right blending space because it is
the one quantity both families share and both ``multiplier`` values were
*fit* in; blending in, say, NegBin ``alpha`` directly would require
re-deriving a multiplier-equivalent per family and would not generalize to
Gamma. ``dispersion_strength = 0.0`` reproduces the original (pre-fix,
sharpest) dispersion exactly; ``dispersion_strength = 1.0`` reproduces the
full recalibration described above; everything in between moves the
applied variance monotonically between the two, trading some of the
coverage gain for less CRPS cost. Sweep it from config
(``cfg.dispersion.dispersion_strength``) without any code changes.

Only two families are handled -- "gamma" (points) and "negbin" (rebounds /
assists / 3PM, fit on the *base* NegBin before any zero-inflation mixing,
since the zero-inflation point mass ``pi`` already explains its own slice of
calibration and should not be re-explained by dispersion scaling too).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

from nba.props.conformal import chronological_split

#: Multiplicative search grid for the variance multiplier -- geometric
#: spacing so it probes both "needs tightening a lot" (small multiplier)
#: and "needs widening a lot" (large multiplier) with equal relative
#: resolution; 1.0 (no-op) is included exactly so a well-calibrated stat
#: is never nudged off of its already-correct native dispersion.
DEFAULT_GRID: np.ndarray = np.geomspace(0.05, 20.0, 80)


def _coverage_for_multiplier(
    family: str, mean: np.ndarray, var: np.ndarray, y: np.ndarray, multiplier: float
) -> float:
    """80% native-interval coverage if every row's variance were scaled by
    ``multiplier`` before refitting its distribution family by method of
    moments -- mirrors the exact fits in ``nba.props.stat_models`` /
    ``nba.props.distributions`` (``gamma_from_moments`` / NB2) so this
    calibration search uses the same machinery as the real pipeline.
    """
    scaled_var = np.clip(np.asarray(var, dtype=float) * float(multiplier), 1e-9, None)
    mean_c = np.clip(np.asarray(mean, dtype=float), 1e-9, None)
    if family == "gamma":
        scale = scaled_var / mean_c
        shape = mean_c / np.clip(scale, 1e-9, None)
        q10 = stats.gamma.ppf(0.10, a=shape, scale=scale)
        q90 = stats.gamma.ppf(0.90, a=shape, scale=scale)
    elif family == "negbin":
        alpha = np.clip((scaled_var - mean_c) / (mean_c * mean_c), 1e-6, None)
        n_param = 1.0 / alpha
        p_param = 1.0 / (1.0 + alpha * mean_c)
        q10 = stats.nbinom.ppf(0.10, n_param, p_param)
        q90 = stats.nbinom.ppf(0.90, n_param, p_param)
    else:
        raise ValueError(f"unsupported family {family!r}; expected 'gamma' or 'negbin'")
    y_arr = np.asarray(y, dtype=float)
    inside = (y_arr >= q10) & (y_arr <= q90)
    return float(np.mean(inside))


@dataclass
class DispersionCalibration:
    stat: str
    family: str
    multiplier: float
    n_cal: int
    coverage_cal_before: float
    coverage_cal_after: float
    note: str
    #: The grid-search multiplier that fully recalibrates the *calibration*
    #: split to ``target_coverage`` (``dispersion_strength = 1.0``), kept
    #: alongside the blended ``multiplier`` actually applied so the
    #: trade-off is auditable. Equal to ``multiplier`` when
    #: ``dispersion_strength == 1.0`` or when calibration was skipped.
    multiplier_full: float = 1.0
    #: The ``dispersion_strength`` in ``[0, 1]`` used to produce
    #: ``multiplier`` from ``multiplier_full`` -- see module docstring.
    strength: float = 1.0


def fit_dispersion_multiplier(
    family: str,
    mean: np.ndarray,
    var: np.ndarray,
    y: np.ndarray,
    dates: np.ndarray,
    stat: str,
    target_coverage: float = 0.8,
    cal_frac: float = 0.5,
    min_cal_n: int = 50,
    grid: np.ndarray | None = None,
    strength: float = 1.0,
) -> DispersionCalibration:
    """Chronologically-fit scalar variance multiplier for one stat.

    Only the *calibration* (chronologically earliest ``cal_frac``) rows
    ever inform the multiplier -- no leakage into later games, same
    contract as ``nba.props.conformal.evaluate_conformal``. Degrades
    gracefully (multiplier left at the documented no-op ``1.0``) when
    there are fewer than ``min_cal_n`` calibration rows.

    ``strength`` (``dispersion_strength``, clamped to ``[0, 1]``) blends
    the no-op multiplier ``1.0`` with the fully-calibrated grid-search
    multiplier -- see module docstring for the variance-space blending
    rationale. ``strength=1.0`` (default here; ``DispersionConfig``
    defaults to the gentler ``0.5``) reproduces the original full
    recalibration exactly.
    """
    mean_a = np.asarray(mean, dtype=float)
    var_a = np.asarray(var, dtype=float)
    y_a = np.asarray(y, dtype=float)
    search_grid = DEFAULT_GRID if grid is None else np.asarray(grid, dtype=float)
    s = float(np.clip(strength, 0.0, 1.0))

    cal_mask, _test_mask = chronological_split(dates, cal_frac)
    n_cal = int(cal_mask.sum())
    if n_cal < min_cal_n:
        note = (
            f"only {n_cal} calibration-split player-games (<{min_cal_n}); dispersion "
            "multiplier left at the no-op default 1.0 rather than fit to noise."
        )
        return DispersionCalibration(
            stat, family, 1.0, n_cal, float("nan"), float("nan"), note, 1.0, s
        )

    mean_c, var_c, y_c = mean_a[cal_mask], var_a[cal_mask], y_a[cal_mask]
    coverage_before = _coverage_for_multiplier(family, mean_c, var_c, y_c, 1.0)

    best_m = 1.0
    best_gap = abs(coverage_before - target_coverage)
    for m in search_grid:
        cov = _coverage_for_multiplier(family, mean_c, var_c, y_c, float(m))
        gap = abs(cov - target_coverage)
        if gap < best_gap:
            best_gap = gap
            best_m = float(m)
    blended_m = 1.0 + s * (best_m - 1.0)
    coverage_after = _coverage_for_multiplier(family, mean_c, var_c, y_c, blended_m)
    return DispersionCalibration(
        stat, family, blended_m, n_cal, coverage_before, coverage_after, "", best_m, s
    )


def apply_dispersion_multiplier(var: np.ndarray, multiplier: float) -> np.ndarray:
    """Scale ``var`` by ``multiplier``, floored so a refit distribution
    never sees a non-positive variance."""
    return np.clip(np.asarray(var, dtype=float) * float(multiplier), 1e-9, None)
