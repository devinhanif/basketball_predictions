"""PTS_COMPONENTS (docs/prereg/PTS_COMPONENTS.md, frozen sha256 7e6fd8f0): points as 2s, 3s, FTs.

Points = 2 * fg2m + 3 * fg3m + ftm.  Each component is a count with its own attempt head (NegBin,
mean = recency + gradient-boosted residual, the production learner and hyper-parameters) and a
Beta-Binomial make head (empirical-Bayes shrunk prior-40 make rate, per-stat concentration).  One
shared "activity" factor z ~ N(0, 1) per row scales the three attempt means as ``exp(beta_k z)``
(A1); ``beta = 0`` is the independent-sum ablation (A2).  The points distribution is the exact
enumeration of the integer grid: a Gauss-Hermite mixture over z of the convolution (by FFT) of the
three make distributions.

Research module only.  ``nba/`` is untouched: the doc's ``ContextResidualConfig.pts_components``
flag is not added (production stays byte-identical by construction; see ``UNSTATED`` in the eval).

All features are as-of (built from games strictly before the target date, the official pre-tip
report through the production ``flagged`` map).  Reuse of the production builders is by aliasing:
the attempt columns are fed to ``player_states``, ``team_game_context`` and ``vacated_features``
in the slots production uses for pts / reb / ast, so every recency, opponent and vacated feature
has exactly production's definition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl
from numpy.polynomial.hermite_e import hermegauss
from scipy.optimize import minimize, minimize_scalar
from scipy.special import betaln, gammaln, logsumexp

from nba.props.context_residual import (
    CRPS_TAUS,
    ContextResidualConfig,
    _matrix,
    _params,
    _sel,
    player_states,
    split_calibration,
    team_game_context,
    vacated_features,
)

ATT: tuple[str, ...] = ("fg2a", "fg3a", "fta")
MAKE: tuple[str, ...] = ("fg2m", "fg3m", "ftm")
POINTS_PER: tuple[int, ...] = (2, 3, 1)
CAPS: tuple[int, ...] = (40, 25, 30)
MAX_PTS = 2 * CAPS[0] + 3 * CAPS[1] + CAPS[2]  # 185
FFT_LEN = 256
PSEUDO = 50.0
RATE_WINDOW = 40
N_NODES = 15
MU_FLOOR = 0.05
LEAGUE_PRIOR: tuple[float, ...] = (0.52, 0.36, 0.78)  # only the very first game date (never scored)
ALIAS: dict[str, str] = {"pts": "fg2a", "reb": "fg3a", "ast": "fta"}  # production slot -> attempt
RATE_COLS: tuple[str, ...] = ("pr_fg2", "pr_fg3", "pr_ft")
TG_COLS: tuple[str, ...] = tuple(f"tg_{c}" for c in (*ATT, *MAKE))
KEYS = ["game_id", "player_id"]


def attempt_feature_names(a: str) -> list[str]:
    return [f"{p}_{a}" for p in ("m10", "m5", "m40", "m100", "sd10", "rate", "opp_allow", "vac")]


COMP_FEATURES: tuple[str, ...] = (
    *(n for a in ATT for n in attempt_feature_names(a)),
    *RATE_COLS,
)


def component_names(base: list[str]) -> list[str]:
    """Production ``stat_feature_names('pts')`` plus the 27 component columns."""
    return [*base, *COMP_FEATURES]


# --------------------------------------------------------------------------- targets and features


def add_derived(pgs: pl.DataFrame) -> pl.DataFrame:
    """fg2m / fg2a from fgm, fga and the three-point columns."""
    return pgs.with_columns(
        (pl.col("fgm") - pl.col("fg3m")).alias("fg2m"),
        (pl.col("fga") - pl.col("fg3a")).alias("fg2a"),
    )


def identity_ok(pgs: pl.DataFrame) -> pl.Series:
    """True where the box inputs are non-null and pts == 2 fg2m + 3 fg3m + ftm."""
    nn = pl.all_horizontal(
        [pl.col(c).is_not_null() for c in ("fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "pts")]
    )
    ident = pl.col("pts") == 2 * (pl.col("fgm") - pl.col("fg3m")) + 3 * pl.col("fg3m") + pl.col(
        "ftm"
    )
    return pgs.select((nn & ident).fill_null(False).alias("ok"))["ok"]


def _aliased(pgs: pl.DataFrame) -> pl.DataFrame:
    """pgs with the production slots pts / reb / ast carrying the attempt columns."""
    return add_derived(pgs).with_columns(
        pl.col("fg2a").alias("pts"), pl.col("fg3a").alias("reb"), pl.col("fta").alias("ast")
    )


def build_component_features(
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    flagged: dict[str, set[int]],
) -> pl.DataFrame:
    """One row per PLAYED player-game: ``COMP_FEATURES`` and the six ``tg_*`` targets.

    ``pgs`` carries game_id, player_id, team_id, minutes, starter, pts, reb, ast, fg3m and the box
    columns fgm, fga, fg3a, ftm, fta.  Everything is as-of; the targets are labels only.
    """
    games = games.with_columns(pl.col("game_date").cast(pl.Date))
    al = _aliased(pgs)
    played = (
        al.join(games.select(["game_id", "game_date", "season", "home_team"]), on="game_id")
        .filter(pl.col("minutes") > 0)
        .sort(["player_id", "game_date", "game_id"])
    )
    st = player_states(played)
    pre, n_prior = st["pre"], st["n_prior"]
    min10 = _sel(pre, 10.0, "min")
    cols: dict[str, np.ndarray] = {}
    for slot, a in ALIAS.items():
        cols[f"m5_{a}"] = _sel(pre, 5.0, slot)
        cols[f"m10_{a}"] = _sel(pre, 10.0, slot)
        cols[f"m40_{a}"] = _sel(pre, 40.0, slot)
        cols[f"m100_{a}"] = _sel(pre, 100.0, slot)
        var = np.clip(_sel(pre, 10.0, f"{slot}2") - cols[f"m10_{a}"] ** 2, 0.0, None)
        nn = np.maximum(n_prior, 2)
        sd = np.sqrt(var * nn / (nn - 1))
        # degenerate sd -> 1.0 (production's per-stat default std has no attempt analogue)
        cols[f"sd10_{a}"] = np.where((n_prior >= 2) & (sd > 0), sd, 1.0)
        cols[f"rate_{a}"] = cols[f"m10_{a}"] / np.maximum(min10, 1.0)
    base = played.select(
        "game_id",
        "player_id",
        "team_id",
        "game_date",
        *[pl.col(m).alias(f"tg_{m}") for m in (*ATT, *MAKE)],
    )
    feats = base.with_columns([pl.Series(k, v) for k, v in cols.items()])
    # opponent allowed (rolling 20, shifted) with the production builder on the aliased columns
    stub = games.select("game_id").with_columns(pl.lit(0.0).alias("exp_margin_home"))
    tctx = team_game_context(games, al, stub).select(
        "game_id",
        "team_id",
        *[pl.col(f"opp_allow_{s}").alias(f"opp_allow_{a}") for s, a in ALIAS.items()],
    )
    feats = feats.join(tctx, on=["game_id", "team_id"], how="left")
    game_info = {
        str(g): (d, int(h), int(a_))
        for g, d, h, a_ in games.select(
            ["game_id", "game_date", "home_team", "away_team"]
        ).iter_rows()
    }
    vac = vacated_features(played, st, flagged, game_info)
    reported = pl.DataFrame({"game_id": sorted(flagged)}, schema={"game_id": pl.Utf8}).with_columns(
        pl.lit(1).alias("_has_report")
    )
    vac = vac.select(
        "game_id", "team_id", *[pl.col(f"vac_{s}").alias(f"vac_{a}") for s, a in ALIAS.items()]
    )
    feats = (
        feats.join(vac, on=["game_id", "team_id"], how="left")
        .join(reported, on="game_id", how="left")
        .with_columns(pl.col("_has_report").fill_null(0))
    )
    feats = feats.with_columns(
        [
            pl.when(pl.col("_has_report") == 1)
            .then(pl.col(f"vac_{a}").fill_null(0.0))
            .otherwise(None)
            .alias(f"vac_{a}")
            for a in ATT
        ]
    ).drop("_has_report")
    feats = feats.join(make_rate_features(played), on=KEYS, how="left")
    return feats.drop("team_id", "game_date").sort(KEYS)


def make_rate_features(played: pl.DataFrame) -> pl.DataFrame:
    """Prior-40 make rates, empirical-Bayes shrunk to the as-of league rate (50 pseudo-attempts).

    ``played`` is sorted by (player_id, game_date, game_id) with fg2m / fg2a etc.  Window: the
    player's previous 40 PLAYED games (shifted, so tonight is never in it); league rate: every
    row on a STRICTLY earlier date.
    """
    d = played.select("game_id", "player_id", "game_date", *MAKE, *ATT)
    d = d.with_columns(
        [
            pl.col(c)
            .shift(1)
            .rolling_sum(RATE_WINDOW, min_samples=1)
            .over("player_id")
            .fill_null(0.0)
            .alias(f"w_{c}")
            for c in (*MAKE, *ATT)
        ]
    )
    daily = (
        d.group_by("game_date")
        .agg([pl.col(c).sum().cast(pl.Float64).alias(f"d_{c}") for c in (*MAKE, *ATT)])
        .sort("game_date")
        .with_columns([pl.col(f"d_{c}").cum_sum().shift(1).alias(f"c_{c}") for c in (*MAKE, *ATT)])
        .select("game_date", *[f"c_{c}" for c in (*MAKE, *ATT)])
    )
    d = d.join(daily, on="game_date", how="left")
    out = []
    for i, (mk, at, name) in enumerate(zip(MAKE, ATT, RATE_COLS, strict=True)):
        lg = (
            pl.when(pl.col(f"c_{at}") > 0)
            .then(pl.col(f"c_{mk}") / pl.col(f"c_{at}"))
            .otherwise(pl.lit(LEAGUE_PRIOR[i]))
        )
        out.append(((pl.col(f"w_{mk}") + PSEUDO * lg) / (pl.col(f"w_{at}") + PSEUDO)).alias(name))
    return d.select("game_id", "player_id", *out)


def permute_targets(frame: pl.DataFrame, seed: int = 0) -> pl.DataFrame:
    """A4 placebo: the six component targets are permuted JOINTLY within player (keeps the
    marginals and the within-game joint structure, breaks the link to the row's features)."""
    d = frame.with_row_index("_ord")
    groups = d.group_by("player_id", maintain_order=True).agg(pl.col("_ord"))
    rng = np.random.default_rng(seed)
    src = np.arange(d.height)
    for ords in groups["_ord"].to_list():
        o = np.asarray(ords)
        src[o] = o[rng.permutation(len(o))]
    cols = [pl.Series(c, d[c].to_numpy()[src]) for c in TG_COLS]
    return d.with_columns(cols).drop("_ord")


# --------------------------------------------------------------------------- distributions


def nb_logpmf(y: np.ndarray, mu: np.ndarray, r: float) -> np.ndarray:
    """log NegBin(y; mean mu, size r): var = mu + mu^2 / r."""
    return np.asarray(
        gammaln(y + r)
        - gammaln(r)
        - gammaln(y + 1.0)
        + r * (np.log(r) - np.log(r + mu))
        + y * (np.log(mu) - np.log(r + mu))
    )


def bb_logpmf(m: np.ndarray, a: np.ndarray, p: np.ndarray, kappa: float) -> np.ndarray:
    """log BetaBinomial(m; a, alpha = p kappa, beta = (1 - p) kappa); -inf where m > a."""
    al, be = p * kappa, (1.0 - p) * kappa
    lp = (
        gammaln(a + 1.0)
        - gammaln(m + 1.0)
        - gammaln(np.maximum(a - m, 0.0) + 1.0)
        + betaln(m + al, np.maximum(a - m, 0.0) + be)
        - betaln(al, be)
    )
    return np.asarray(np.where(m <= a, lp, -np.inf))


def gh_nodes(n: int = N_NODES) -> tuple[np.ndarray, np.ndarray]:
    """Probabilists' Gauss-Hermite nodes and weights summing to 1 (N(0, 1) quadrature)."""
    x, w = hermegauss(n)
    return x, w / w.sum()


def fit_activity(
    y: np.ndarray, mu: np.ndarray, shared: bool, n_nodes: int = N_NODES
) -> tuple[np.ndarray, np.ndarray]:
    """MLE of NegBin sizes ``r`` (3,) and activity loadings ``beta`` (3,).

    ``shared``: the joint marginal likelihood integrates z by quadrature, so beta is identified by
    the cross-head covariance.  Not shared: beta = 0 and each r_k is its own 1-D MLE.
    """
    k = y.shape[1]
    if not shared:
        r = np.empty(k)
        for j in range(k):
            res = minimize_scalar(
                lambda lr, j=j: -float(nb_logpmf(y[:, j], mu[:, j], float(np.exp(lr))).sum()),
                bounds=(np.log(0.3), np.log(2000.0)),
                method="bounded",
            )
            r[j] = float(np.exp(res.x))
        return r, np.zeros(k)
    x, w = gh_nodes(n_nodes)
    lw = np.log(w)

    def nll(theta: np.ndarray) -> float:
        r, beta = np.exp(theta[:k]), theta[k:]
        ll = np.zeros((y.shape[0], len(x)))
        for j in range(k):
            muj = mu[:, j, None] * np.exp(beta[j] * x[None, :] - 0.5 * beta[j] ** 2)
            ll += nb_logpmf(y[:, j, None], muj, float(r[j]))
        return -float(logsumexp(ll + lw[None, :], axis=1).sum())

    r0, _ = fit_activity(y, mu, False, n_nodes)
    theta0 = np.r_[np.log(r0), np.full(k, 0.15)]
    bounds = [(np.log(0.3), np.log(2000.0))] * k + [(-1.5, 1.5)] * k
    res = minimize(nll, theta0, method="L-BFGS-B", bounds=bounds, options={"maxiter": 60})
    th = res.x
    return np.exp(th[:k]), th[k:]


def fit_kappa(m: np.ndarray, a: np.ndarray, p: np.ndarray) -> float:
    """Beta-Binomial concentration by 1-D MLE on rows with attempts."""
    keep = a > 0
    m, a, p = m[keep], a[keep], p[keep]
    if len(m) < 50:
        return 200.0
    res = minimize_scalar(
        lambda lk: -float(bb_logpmf(m, a, p, float(np.exp(lk))).sum()),
        bounds=(np.log(2.0), np.log(5000.0)),
        method="bounded",
    )
    return float(np.exp(res.x))


def _attempt_pmf(mu: np.ndarray, beta: float, r: float, x: np.ndarray, cap: int) -> np.ndarray:
    """(n, J, cap + 1) NegBin pmf of attempts at the quadrature nodes; mass above ``cap`` folded
    onto the cap cell so every row sums to exactly one."""
    muj = mu[:, None] * np.exp(beta * x[None, :] - 0.5 * beta**2)  # (n, J), E[factor] = 1
    a = np.arange(cap + 1, dtype=float)
    pm = np.exp(nb_logpmf(a[None, None, :], muj[:, :, None], r))
    pm[:, :, cap] = np.maximum(1.0 - pm[:, :, :cap].sum(axis=2), 0.0)
    return pm


def _make_kernel(p: np.ndarray, kappa: float, cap: int) -> np.ndarray:
    """(n, cap + 1 attempts, cap + 1 makes) Beta-Binomial pmf, rows over makes sum to one."""
    a = np.arange(cap + 1, dtype=float)
    lp = bb_logpmf(a[None, None, :], a[None, :, None], p[:, None, None], kappa)
    return np.asarray(np.exp(lp))


def points_pmf(
    mu: np.ndarray,
    p: np.ndarray,
    r: np.ndarray,
    beta: np.ndarray,
    kappa: np.ndarray,
    chunk: int = 400,
) -> tuple[np.ndarray, np.ndarray]:
    """Exact points pmf on 0..MAX_PTS and the fg3m marginal pmf on 0..CAPS[1].

    ``mu`` (n, 3) attempt means, ``p`` (n, 3) shrunk make rates.  A zero ``beta`` collapses the
    quadrature to one node (the independent sum).
    """
    x, w = gh_nodes() if np.any(beta != 0.0) else (np.zeros(1), np.ones(1))
    n = mu.shape[0]
    pts = np.empty((n, MAX_PTS + 1))
    m3 = np.empty((n, CAPS[1] + 1))
    for s in range(0, n, chunk):
        sl = slice(s, min(s + chunk, n))
        arr = []
        for k in range(3):
            apm = _attempt_pmf(mu[sl, k], float(beta[k]), float(r[k]), x, CAPS[k])
            ker = _make_kernel(p[sl, k], float(kappa[k]), CAPS[k])
            mk = np.einsum("nja,nam->njm", apm, ker)
            if k == 1:
                m3[sl] = np.einsum("njm,j->nm", mk, w)
            emb = np.zeros((mk.shape[0], mk.shape[1], FFT_LEN))
            emb[:, :, : POINTS_PER[k] * CAPS[k] + 1 : POINTS_PER[k]] = mk
            arr.append(np.fft.rfft(emb, axis=-1))
        prod = arr[0] * arr[1] * arr[2]
        out = np.fft.irfft(prod, n=FFT_LEN, axis=-1)
        pts[sl] = np.einsum("njp,j->np", out, w)[:, : MAX_PTS + 1]
    return np.maximum(pts, 0.0), np.maximum(m3, 0.0)


def pmf_to_quantiles(pmf: np.ndarray, taus: np.ndarray = CRPS_TAUS, chunk: int = 500) -> np.ndarray:
    """Integer quantile grid: the smallest k with CDF(k) >= tau (monotone in tau)."""
    out = np.empty((pmf.shape[0], len(taus)), dtype=np.int16)
    cdf = np.cumsum(pmf, axis=1)
    if cdf[:, -1].min() < taus.max():
        raise ValueError("pmf does not reach the top tau")
    for s in range(0, pmf.shape[0], chunk):
        c = cdf[s : s + chunk]
        out[s : s + chunk] = (c[:, :, None] >= taus[None, None, :] - 1e-12).argmax(axis=1)
    return out


# --------------------------------------------------------------------------- model


@dataclass
class ActivityParams:
    r: np.ndarray
    beta: np.ndarray
    kappa: np.ndarray


@dataclass
class ComponentModel:
    """Three attempt heads (production learner, seed 0) + nuisance fits on the calibration window.

    ``fit`` trains the heads on the fit window; ``params(shared)`` fits sizes / loadings /
    concentrations on the held-out calibration window (rows the heads never saw), as production
    fits its residual quantiles.  The same heads serve A1 (shared) and A2 (independent).
    """

    cfg: ContextResidualConfig
    names: list[str]
    heads: list[lgb.LGBMRegressor] | None = None
    cal_mu: np.ndarray | None = None
    cal_y: np.ndarray | None = None
    cal_m: np.ndarray | None = None
    cal_a: np.ndarray | None = None
    cal_p: np.ndarray | None = None

    def mean_attempts(self, df: pl.DataFrame) -> np.ndarray:
        assert self.heads is not None
        x = _matrix(df, self.names)
        out = np.empty((df.height, 3))
        for k, a in enumerate(ATT):
            out[:, k] = np.maximum(
                df[f"m10_{a}"].to_numpy() + np.asarray(self.heads[k].predict(x)), MU_FLOOR
            )
        return out

    def fit(self, train: pl.DataFrame) -> ComponentModel:
        fit_df, cal_df = split_calibration(train, self.cfg)
        if fit_df.height < 200 or cal_df.height < 50:
            raise ValueError("not enough rows to fit + calibrate")
        x_fit = _matrix(fit_df, self.names)
        self.heads = []
        for a in ATT:
            r_fit = fit_df[f"tg_{a}"].to_numpy().astype(float) - fit_df[f"m10_{a}"].to_numpy()
            self.heads.append(lgb.LGBMRegressor(**_params(self.cfg)).fit(x_fit, r_fit))  # type: ignore[arg-type]
        self.cal_mu = self.mean_attempts(cal_df)
        self.cal_y = np.column_stack([cal_df[f"tg_{a}"].to_numpy().astype(float) for a in ATT])
        self.cal_m = np.column_stack([cal_df[f"tg_{m}"].to_numpy().astype(float) for m in MAKE])
        self.cal_a = self.cal_y
        self.cal_p = np.column_stack([cal_df[c].to_numpy().astype(float) for c in RATE_COLS])
        return self

    def params(self, shared: bool) -> ActivityParams:
        assert self.cal_mu is not None and self.cal_y is not None
        assert self.cal_m is not None and self.cal_p is not None and self.cal_a is not None
        r, beta = fit_activity(self.cal_y, self.cal_mu, shared)
        kappa = np.array(
            [fit_kappa(self.cal_m[:, k], self.cal_a[:, k], self.cal_p[:, k]) for k in range(3)]
        )
        return ActivityParams(r=r, beta=beta, kappa=kappa)

    def predict(
        self, df: pl.DataFrame, prm: ActivityParams
    ) -> tuple[np.ndarray, np.ndarray, float]:
        """(points pmf, fg3m pmf, max |row sum - 1|)."""
        mu = self.mean_attempts(df)
        p = np.column_stack([df[c].to_numpy().astype(float) for c in RATE_COLS])
        pts, m3 = points_pmf(mu, p, prm.r, prm.beta, prm.kappa)
        dev = float(np.abs(pts.sum(axis=1) - 1.0).max()) if len(pts) else 0.0
        return pts, m3, dev


def summary(prm: ActivityParams) -> dict[str, Any]:
    return {
        "r": [float(v) for v in prm.r],
        "beta": [float(v) for v in prm.beta],
        "kappa": [float(v) for v in prm.kappa],
    }
