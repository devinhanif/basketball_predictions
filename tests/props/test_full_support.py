"""Full-support P(Y >= k) storage and integer-support CRPS (RPS)."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nba.props import full_support as fs
from nba.props.config import THRESHOLDS
from nba.props.context_residual import to_integer_support
from nba.props.distributions import NormalDist
from nba.props.forward import OUTPUT_COLUMNS, QUANTILE_TAUS, _ctx_row, _row_from_dist


def brute_rps(pmf: np.ndarray, y: int) -> float:
    """Reference ranked probability score straight from the definition (explicit loops)."""
    total = 0.0
    cum = 0.0
    for k in range(len(pmf) + max(y, 0) + 1):
        cum += pmf[k] if k < len(pmf) else 0.0  # F(k) = P(Y <= k)
        total += (cum - (1.0 if y <= k else 0.0)) ** 2
    return total


def surv_from_pmf(pmf: np.ndarray) -> np.ndarray:
    return 1.0 - np.cumsum(pmf)[:-1] if len(pmf) > 1 else np.zeros(0)


def test_rps_hand_example() -> None:
    # P(Y=0..2) = .2, .5, .3 ; y = 1: F = [.2, .7, 1] -> (.2-0)^2 + (.7-1)^2 + 0 = .04 + .09
    surv = np.array([0.8, 0.3])  # P(Y>=1), P(Y>=2)
    assert fs.crps_int_surv(surv, 1) == pytest.approx(0.13)
    assert fs.crps_int({"1": 0.8, "2": 0.3}, 1) == pytest.approx(0.13)
    assert fs.crps_int({"1": 0.8, "2": 0.3}, 1, kmax=10) == pytest.approx(0.13)
    # y beyond the stored support: F=[.2,.7,1,1], obs=[0,0,0,0,1]-> only k=y-1.. terms counted
    assert fs.crps_int_surv(surv, 5) == pytest.approx(brute_rps(np.array([0.2, 0.5, 0.3]), 5))


def test_point_mass_is_zero_and_nonnegative() -> None:
    surv = np.array([1.0, 1.0, 1.0, 0.0])  # Y = 3 surely
    assert fs.crps_int_surv(surv, 3) == 0.0
    assert fs.crps_int_surv(surv, 5) > 0


@settings(max_examples=80, deadline=None)
@given(
    w=st.lists(st.floats(0.0, 1.0), min_size=2, max_size=40).filter(lambda v: sum(v) > 1e-6),
    y=st.integers(0, 60),
)
def test_rps_matches_brute_force(w: list[float], y: int) -> None:
    pmf = np.asarray(w) / np.sum(w)
    surv = surv_from_pmf(pmf)
    got = fs.crps_int_surv(surv, y)
    assert got >= -1e-12
    assert got == pytest.approx(brute_rps(pmf, y), abs=1e-9)


def test_crps_int_rejects_gaps() -> None:
    with pytest.raises(ValueError):
        fs.crps_int({"1": 0.9, "3": 0.1}, 1)


def test_from_samples_matches_legacy_p_ge_and_integer_transform() -> None:
    rng = np.random.default_rng(0)
    q = np.sort(rng.gamma(4.0, 5.0, 199))  # pts-like continuous quantiles
    for samples in (q, to_integer_support(q)):
        full = fs.from_samples(samples, "pts")
        surv = fs.survival(full)
        assert surv is not None and fs.is_complete(surv)
        for t in THRESHOLDS["pts"]:
            legacy = float(np.mean(q > t - 0.5))  # exactly what _ctx_row stores in p_ge
            got = float(surv[t - 1]) if t - 1 < surv.size else 0.0
            assert got == legacy
    # convention invariance: ceil(q-0.5) leaves every P(Y>=k) unchanged -> RPS identical
    a = fs.survival(fs.from_samples(q, "pts"))
    b = fs.survival(fs.from_samples(to_integer_support(q), "pts"))
    assert a is not None and b is not None and np.array_equal(a, b)
    assert fs.crps_int_surv(a, 17) == fs.crps_int_surv(b, 17)


def test_truncation_covers_999_and_cap() -> None:
    q = np.full(199, 3.0)  # point mass at 3
    full = fs.from_samples(q, "reb")
    assert full["c"] == [199, 199, 199, 0]  # stops at the first entry <= 1e-3
    wide = fs.from_samples(np.linspace(0, 400, 199), "fg3m")
    assert len(wide["c"]) == fs.KMAX["fg3m"]  # capped: last entry still large -> unscorable
    assert not fs.is_complete(fs.survival(wide))


def test_from_dist_matches_p_ge_to_resolution() -> None:
    d = NormalDist(18.0, 6.0)
    surv = fs.survival(fs.from_dist(d, "pts"))
    assert surv is not None and fs.is_complete(surv)
    for k in (1, 5, 10, 18, 25, 30):
        assert abs(float(surv[k - 1]) - d.p_ge(float(k))) <= 0.5 / fs.PARAM_DENOM + 1e-12
    assert np.all(np.diff(surv) <= 0)


def test_bytes_per_row_is_small() -> None:
    rng = np.random.default_rng(1)
    sizes = {}
    for stat, scale in (("pts", 6.0), ("reb", 2.5), ("ast", 2.0), ("fg3m", 1.2)):
        mean = {"pts": 18, "reb": 6, "ast": 4, "fg3m": 2}[stat]
        q = np.sort(np.maximum(rng.normal(mean, scale, 199), 0))
        sizes[stat] = len(fs.dumps(fs.from_samples(q, stat)))
    assert sizes["pts"] < 260 and max(sizes.values()) < 260


def _legacy_ctx_row(base: dict[str, Any], mean: float, q19: Any, q199: Any, thr: list[int]) -> Any:
    """The body of ``_ctx_row`` before the additive field existed (reference)."""
    p_ge = {str(t): float(np.mean(q199 > t - 0.5)) for t in thr}
    std = float(np.std(q199))
    pp = float(base["p_play"])
    return {
        **base,
        "model": "context_residual",
        "mean": mean,
        "std": std,
        "dist_family": "context_residual",
        "dist_params": json.dumps({"mean": mean, "std": std}, sort_keys=True),
        "p_ge": json.dumps(p_ge, sort_keys=True),
        "q10": float(q19[1]),
        "q50": float(q19[9]),
        "q90": float(q19[17]),
        "q_grid": json.dumps([round(float(v), 4) for v in q19]),
        "mean_uncond": pp * mean,
        "p_ge_uncond": json.dumps({k: pp * v for k, v in p_ge.items()}, sort_keys=True),
    }


def test_ctx_row_existing_fields_byte_identical() -> None:
    rng = np.random.default_rng(5)
    q199 = np.sort(rng.gamma(3.0, 4.0, 199))
    q19 = np.quantile(q199, QUANTILE_TAUS)
    base = {"p_play": 0.83, "stat": "pts", "game_id": "g", "player_id": 3}
    new = _ctx_row(base, 12.34, q19, q199, THRESHOLDS["pts"])
    old = _legacy_ctx_row(base, 12.34, q19, q199, THRESHOLDS["pts"])
    assert new.pop("p_ge_full")
    assert json.dumps(new, sort_keys=True) == json.dumps(old, sort_keys=True)
    assert "p_ge_full" in OUTPUT_COLUMNS


def test_row_from_dist_existing_fields_byte_identical() -> None:
    d = NormalDist(9.0, 3.0)
    row = _row_from_dist(d, THRESHOLDS["reb"], "reb")
    full = row.pop("p_ge_full")
    legacy = {
        "mean": d.mean(),
        "std": float(d.params().get("std", float("nan"))),
        "dist_family": d.family,
        "dist_params": json.dumps(d.params(), sort_keys=True),
        "p_ge": json.dumps({str(t): d.p_ge(float(t)) for t in THRESHOLDS["reb"]}, sort_keys=True),
        "q10": d.ppf(0.10),
        "q50": d.ppf(0.50),
        "q90": d.ppf(0.90),
        "q_grid": json.dumps([round(d.ppf(t), 4) for t in QUANTILE_TAUS]),
    }
    assert json.dumps(row, sort_keys=True) == json.dumps(legacy, sort_keys=True)
    assert json.loads(str(full))["n"] == fs.PARAM_DENOM
