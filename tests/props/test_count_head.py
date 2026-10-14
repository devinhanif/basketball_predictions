# ruff: noqa: E501
"""NB count head (nba.props.count_head): parameter recovery, integer output, no leakage of the held-out window."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from nba.props.context_residual import ContextResidualConfig
from nba.props.count_head import NBCountModel, fit_alpha, nb_quantiles


def test_alpha_recovered_and_quantiles_are_integers() -> None:
    rng = np.random.default_rng(0)
    mu = rng.uniform(0.2, 3.0, 40000)
    alpha = 0.6
    r = 1.0 / alpha
    y = rng.negative_binomial(r, r / (r + mu))
    assert abs(fit_alpha(y.astype(float), mu) - alpha) < 0.06
    taus = (np.arange(1, 200) - 0.5) / 199
    q = nb_quantiles(mu[:50], np.full(50, alpha), taus)
    assert np.array_equal(q, np.round(q)) and (np.diff(q, axis=1) >= 0).all() and q.min() >= 0
    # Poisson limit
    qp = nb_quantiles(mu[:5], np.zeros(5), taus)
    assert np.array_equal(qp, np.round(qp))


def test_fit_predict_on_synthetic_counts() -> None:
    rng = np.random.default_rng(1)
    n = 6000
    x = rng.normal(size=n)
    mu = np.exp(-0.7 + 0.5 * x)
    y = rng.poisson(mu * rng.gamma(2.0, 0.5, n))
    days = [dt.date(2023, 1, 1) + dt.timedelta(days=i // 20) for i in range(n)]
    df = pl.DataFrame(
        {
            "x": x,
            "y": y.astype(float),
            "game_date": days,
            "game_id": [str(i) for i in range(n)],
            "player_id": list(range(n)),
        }
    )
    cfg = ContextResidualConfig(n_estimators=60, min_child_samples=30, min_cal_rows=500, n_jobs=1)
    m = NBCountModel("blk", cfg, ["x"]).fit(df.sort("game_date"))
    mean, q = m.predict(df.head(500), (np.arange(1, 200) - 0.5) / 199)
    assert q.shape == (500, 199) and np.array_equal(q, np.round(q))
    assert abs(float(mean.mean()) - float(y[:500].mean())) < 0.15
    assert m.info["alpha_max"] > 0.1  # the over-dispersion (gamma mixing) is found
