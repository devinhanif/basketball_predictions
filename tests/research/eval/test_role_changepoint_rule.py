# ruff: noqa: E501
"""ROLE_CHANGEPOINT detector and placebo builders: as-of discipline (planted future), shapes, detection of a planted step, past-only placebos."""

from __future__ import annotations

import numpy as np

from research.eval import role_changepoint_rule as R


def _series(n: int = 90, seed: int = 0) -> np.ndarray:
    return R.synth_step_series(n, 60, 9.0, 20.0, 5.0, seed)


def test_bocpd_features_are_asof_planted_future() -> None:
    x = _series()
    base = R.bocpd_asof(x)
    y = x.copy()
    y[40:] = 999.0  # wild future from game 40 on
    alt = R.bocpd_asof(y)
    for k, v in base.items():
        # row t sees x[:t]; rows 0..40 only see games before index 40
        assert np.array_equal(v[:41], alt[k][:41], equal_nan=True), k
    assert not np.allclose(
        base["mean"][45:], alt["mean"][45:]
    )  # control: the edit does reach later rows


def test_bocpd_outputs_are_probabilities_and_finite() -> None:
    o = R.bocpd_asof(_series())
    for k in R.KS:
        p = o[f"p{k}"]
        assert np.all((p >= 0) & (p <= 1 + 1e-12))
    assert np.all(np.isfinite(o["mean"])) and np.all(o["sd"] > 0)
    assert o["p3"][0] <= o["p5"][0] <= o["p8"][0]


def test_bocpd_no_nan_after_first_row_and_first_row_is_prior() -> None:
    o = R.bocpd_asof(_series(20))
    assert o["mean"][0] == R.LEAGUE_MU0 or abs(o["mean"][0] - R.LEAGUE_MU0) < 1.0
    assert not np.isnan(o["p8"]).any()


def test_bocpd_detects_planted_step_and_not_a_stationary_series() -> None:
    hits, quiet = [], []
    for s in range(30):
        x = R.synth_step_series(120, 70, 10.0, 20.0, 5.0, s)
        o = R.bocpd_asof(x)
        hits.append(np.nanmax(o["p8"][71:80]) >= 0.5)
        quiet.append((o["p8"][15:70] >= 0.5).mean())
    assert np.mean(hits) >= 0.8
    assert np.mean(quiet) <= 0.03


def test_level_estimate_beats_slow_ewma_after_a_step() -> None:
    errs_b, errs_e = [], []
    for s in range(30):
        x = R.synth_step_series(120, 70, 12.0, 20.0, 5.0, s)
        errs_b.append(abs(R.bocpd_asof(x)["mean"][72] - 32.0))
        errs_e.append(abs(R.ew_mean_before(x, 10.0)[72] - 32.0))
    assert np.mean(errs_b) < np.mean(errs_e)


def test_ew_mean_before_excludes_the_row_itself() -> None:
    x = np.array([10.0, 20.0, 30.0, 40.0])
    m = R.ew_mean_before(x, 5.0)
    assert np.isnan(m[0]) and m[1] == 10.0
    y = x.copy()
    y[3] = 1e6
    assert R.ew_mean_before(y, 5.0)[3] == m[3]


def test_comparators_are_asof() -> None:
    x = _series(80, 3)
    y = x.copy()
    y[50:] = 500.0
    a, b = R.two_window_z(x), R.two_window_z(y)
    assert np.array_equal(a[:51], b[:51], equal_nan=True)
    ca, cb = R.cusum_post(x), R.cusum_post(y)
    for k in ca:
        assert np.array_equal(ca[k][:51], cb[k][:51], equal_nan=True), k


def test_cusum_flags_a_step() -> None:
    x = R.synth_step_series(80, 40, 12.0, 20.0, 3.0, 1)
    cu = R.cusum_post(x)
    assert cu["since"][46] <= 6 and cu["dir"][46] == 1.0


def test_donor_permute_is_within_cell_and_breaks_the_link() -> None:
    n = 200
    v = np.arange(n, dtype=float)[:, None]
    date = np.repeat(np.arange(20), 10)
    bucket = np.tile(np.arange(2), 100)
    out = R.donor_permute(v, date, bucket, 0)
    for d in range(20):
        for b in range(2):
            m = (date == d) & (bucket == b)
            assert sorted(out[m, 0]) == sorted(v[m, 0])
    assert (out[:, 0] != v[:, 0]).mean() > 0.5
    assert np.array_equal(out, R.donor_permute(v, date, bucket, 0))


def test_lag_series_is_pastonly() -> None:
    v = np.arange(10, dtype=float)
    out = R.lag_series(v, 3)
    assert np.isnan(out[:3]).all() and out[3] == 0.0 and out[9] == 6.0


def test_detection_delay() -> None:
    p = np.zeros(30)
    p[25:] = 0.9
    assert R.detection_delay(p, 20, 0.5) == 5.0
    assert np.isnan(R.detection_delay(np.zeros(30), 20, 0.5))
