# ruff: noqa: E501
"""Negative-binomial count head for low-mean stats (blk, stl): LightGBM Poisson mean + calibrated dispersion.

Why it exists (docs/prereg/COMBO_STOCKS_SHADOW.md): the production recipe (recency mean + LightGBM residual + conformal
standardised quantiles, then rounding) builds a continuous scale family and rounds it. For a stat that is 54-67% zeros
that shape misplaces P(Y = 0): on 2023 blocks the head said P(0) = 3% where 19.6% happened (1,011 rows) and 69% where
54% happened (5,527 rows). This head models the count directly:

* mean ``mu(x)``: ``LGBMRegressor(objective="poisson")`` on the oldest 85% of the training window, same features and
  same learner settings as ``ContextResidualModel`` (``_params``), target = the count itself;
* dispersion: ``Var = mu + alpha(mu) * mu**2`` with ``alpha`` fitted by maximum likelihood in ``n_bins`` bins of the
  predicted mean on the newest 15% (the same held-out calibration window the conformal head uses);
* read-out: the NB quantile function at the requested taus (already integers), so no rounding step exists.

Fitted on PLAYED rows strictly before the block/date, like the production heads. Nothing is shared with the target of
the row being predicted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import lightgbm as lgb
import numpy as np
import polars as pl
from scipy import stats
from scipy.optimize import minimize_scalar
from scipy.special import gammaln

from nba.props.context_residual import ContextResidualConfig, _matrix, _params, split_calibration

MU_FLOOR = 0.02


def nb_loglik(y: np.ndarray, mu: np.ndarray, alpha: float) -> float:
    """Sum of NB(mean mu, variance mu + alpha mu^2) log-pmf; alpha -> 0 is Poisson."""
    if alpha < 1e-8:
        return float(np.sum(y * np.log(mu) - mu - gammaln(y + 1)))
    r = 1.0 / alpha
    return float(
        np.sum(
            gammaln(y + r)
            - gammaln(r)
            - gammaln(y + 1)
            + r * np.log(r / (r + mu))
            + y * np.log(mu / (r + mu))
        )
    )


def fit_alpha(y: np.ndarray, mu: np.ndarray) -> float:
    res = minimize_scalar(lambda a: -nb_loglik(y, mu, a), bounds=(1e-6, 5.0), method="bounded")
    return float(res.x)


def nb_quantiles(mu: np.ndarray, alpha: np.ndarray, taus: np.ndarray) -> np.ndarray:
    """Integer quantiles (n, len(taus)) of NB(mean mu, variance mu + alpha mu^2), alpha ~ 0 -> Poisson."""
    out = np.empty((len(mu), len(taus)))
    pois = alpha < 1e-6
    if pois.any():
        out[pois] = stats.poisson.ppf(taus[None, :], mu[pois, None])
    nb = ~pois
    if nb.any():
        r = 1.0 / alpha[nb]
        p = r / (r + mu[nb])
        out[nb] = stats.nbinom.ppf(taus[None, :], r[:, None], p[:, None])
    return out


@dataclass
class NBCountModel:
    """Mean head (Poisson objective) + binned NB dispersion. Same ``fit`` / ``predict`` surface as ``ContextResidualModel``."""

    stat: str
    cfg: ContextResidualConfig
    names: list[str]
    n_bins: int = 4
    mean_head: lgb.LGBMRegressor | None = None
    edges: np.ndarray | None = None
    alphas: np.ndarray | None = None
    info: dict[str, float] = field(default_factory=dict)

    def fit(self, train: pl.DataFrame) -> NBCountModel:
        fit_df, cal_df = split_calibration(train, self.cfg)
        if fit_df.height < 200 or cal_df.height < 50:
            raise ValueError("not enough rows to fit + calibrate")
        self.mean_head = lgb.LGBMRegressor(**_params(self.cfg, "poisson")).fit(  # type: ignore[arg-type]
            _matrix(fit_df, self.names), fit_df["y"].to_numpy()
        )
        mu = self._mu(cal_df)
        y = cal_df["y"].to_numpy()
        self.edges = np.quantile(mu, np.linspace(0, 1, self.n_bins + 1)[1:-1])
        b = np.searchsorted(self.edges, mu)
        self.alphas = np.array(
            [
                fit_alpha(y[b == k], mu[b == k]) if (b == k).sum() >= 50 else np.nan
                for k in range(self.n_bins)
            ]
        )
        if np.isnan(self.alphas).any():
            self.alphas = np.where(np.isnan(self.alphas), fit_alpha(y, mu), self.alphas)
        self.info = {"alpha_min": float(self.alphas.min()), "alpha_max": float(self.alphas.max())}
        return self

    def _mu(self, df: pl.DataFrame) -> np.ndarray:
        assert self.mean_head is not None
        pred = np.asarray(self.mean_head.predict(_matrix(df, self.names)), dtype=float)
        return np.asarray(np.maximum(pred, MU_FLOOR))

    def predict(self, df: pl.DataFrame, taus: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(mean, integer quantiles[n, len(taus)])."""
        assert self.edges is not None and self.alphas is not None
        mu = self._mu(df)
        alpha = self.alphas[np.searchsorted(self.edges, mu)]
        return mu, nb_quantiles(mu, alpha, np.asarray(taus, dtype=float))
