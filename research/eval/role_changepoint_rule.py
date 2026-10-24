# ruff: noqa: E501
"""ROLE_CHANGEPOINT rule constants and pure functions (rule: docs/prereg/ROLE_CHANGEPOINT.md, DRAFT).

Nothing here reads a database or an outcome. Every constant below is fixed in code before any gate number was computed; the four tuning grids are
declared here and are searched ONLY on non-scored seasons (2019-2022) after the rule is frozen. Season convention: ``season`` = start year
(2023 = 2023-24). Season 2025 is the spent holdout and is never joined to anything by this module.

Detector (Adams and MacKay 2007, run-length posterior): Gaussian level model with KNOWN observation sd ``SIGMA``; at the start of each run
the level is drawn from N(mu0, TAU^2), mu0 = the player's own exponentially weighted minutes mean (half-life ``LONG_HL`` played games) at the
moment the run starts; constant hazard ``HAZARD``; run length truncated at ``RMAX`` (the last bucket keeps a forgetting sum). The series is the
player's PLAYED minutes (minutes > 0) in game order, chained across seasons (DNP and absence belong to P(play), not to this stage).
Outputs at row t use games strictly before t only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

ID = "ROLE_CHANGEPOINT"
SELECT = 2023
REPORT = 2024
MAX_SEASON = 2024
STATS = ("pts", "reb", "ast", "fg3m")
FLOORS = {"pts": 0.005, "reb": 0.003, "ast": 0.002, "fg3m": 0.001}

# --- detector constants (primary; the gate uses exactly these)
SIGMA = 5.0  # observation sd of a player's minutes within a stable role
TAU = 6.0  # sd of a new run's level around mu0
HAZARD = 1.0 / 30.0
RMAX = 60
LONG_HL = 40.0
LEAGUE_MU0 = 17.0  # mu0 before a player has a played game
K_PRIMARY = 8  # P(change within the last K played games) = P(run length < K); K = 8 spans about one half-life of the production recency windows
KS = (3, 5, 8)
THR_PRIMARY = 0.5
THRS = (0.3, 0.5, 0.7)
MIN_PRIOR = 10  # cohort rows need this many prior played games
MIN_SEASON_GAMES = 10  # PRIMARY slice: at least this many played games this season (season-start rows belong to SEASON_BOUNDARY)
# --- grids searched on non-scored seasons only, after the freeze
GRID_SIGMA = (4.0, 5.0, 6.0, 7.0)
GRID_TAU = (4.0, 6.0, 8.0)
GRID_HAZARD = (1.0 / 15.0, 1.0 / 30.0, 1.0 / 60.0)
GRID_EW_HL = (2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 15.0)
# --- comparator constants (B3: two-window and Page-CUSUM, local standardisation)
FAST_W = 3
SLOW_W = 20
CUSUM_LOCAL_W = 20
CUSUM_THRESHOLD = 4.0
CUSUM_DRIFT = 0.5
CUSUM_SD_FLOOR = 2.0
# --- data version: the stored A0 files (reports/odds_history/model_rows_*) predate commit d872dc7, which back-filled these 5 regular-season games
# (118 played rows) into nba.duckdb. States and scoring for this rule exclude them so that the A0 identity (min10, starter10, n_prior) is exact.
BACKFILLED_AFTER_A0 = ("0022400147", "0022400621", "0022400633", "0022401229", "0022401230")
MINUTES_FLOOR_SLICE = 0.10  # practical floor on the cohort slice, continuous minutes CRPS
# --- inference
N_BOOT = 2000
SEED = 0
ALPHA = 0.05


@dataclass(frozen=True)
class BocpdConfig:
    sigma: float = SIGMA
    tau: float = TAU
    hazard: float = HAZARD
    rmax: int = RMAX
    long_hl: float = LONG_HL
    league_mu0: float = LEAGUE_MU0


def ew_mean_before(x: np.ndarray, hl: float) -> np.ndarray:
    """Exponentially weighted mean of x[:t] for every t (NaN at t = 0). Weight 0.5 ** (age / hl)."""
    a = 0.5 ** (1.0 / hl)
    out = np.full(len(x), np.nan)
    s = d = 0.0
    for t in range(len(x)):
        if d > 0:
            out[t] = s / d
        s = a * s + x[t]
        d = a * d + 1.0
    return out


def _logpdf(x: float, m: np.ndarray, v: np.ndarray) -> np.ndarray:
    return np.asarray(-0.5 * (np.log(2.0 * np.pi * v) + (x - m) ** 2 / v))


def bocpd_asof(
    x: np.ndarray, cfg: BocpdConfig | None = None, ks: tuple[int, ...] = KS
) -> dict[str, np.ndarray]:
    """As-of run-length posterior features for every row t of one player's played-minutes series ``x`` (state after x[:t], row t's own minutes unseen).

    Returns arrays of length len(x): ``p{k}`` = P(run length < k), ``mean`` = predictive mean of the next game's level (hazard-mixed),
    ``sd`` = predictive sd of the level, ``mode`` = modal run length, ``mu0`` = the long-run prior mean used at t.
    """
    c = cfg or BocpdConfig()
    n = len(x)
    mu_long = ew_mean_before(x, c.long_hl)
    out: dict[str, np.ndarray] = {f"p{k}": np.full(n, np.nan) for k in ks}
    out.update({m: np.full(n, np.nan) for m in ("mean", "sd", "mode", "mu0")})
    R = c.rmax
    logw = np.full(R + 1, -np.inf)
    logw[0] = 0.0
    ssum = np.zeros(R + 1)  # sum of observations in the run (forgetting sum at r = R)
    mu0_run = np.full(R + 1, c.league_mu0)
    cnt = np.arange(R + 1, dtype=float)
    inv_t2 = 1.0 / (c.tau**2)
    inv_s2 = 1.0 / (c.sigma**2)
    log_h, log_1h = np.log(c.hazard), np.log1p(-c.hazard)
    for t in range(n):
        mu_now = c.league_mu0 if np.isnan(mu_long[t]) else mu_long[t]
        mu0_run[0] = mu_now  # a run that starts now (r = 0) has prior mean mu_now
        prec = inv_t2 + cnt * inv_s2
        post_m = (mu0_run * inv_t2 + ssum * inv_s2) / prec
        w = np.exp(logw - logw.max())
        w /= w.sum()
        for k in ks:
            out[f"p{k}"][t] = w[: min(k, R + 1)].sum()
        # next-game level: with prob (1 - h) the current run continues, with prob h a fresh run starts at mu_now
        m_cont = float((w * post_m).sum())
        v_cont = float((w * (1.0 / prec + (post_m - m_cont) ** 2)).sum())
        out["mean"][t] = (1.0 - c.hazard) * m_cont + c.hazard * mu_now
        v_mix = (1.0 - c.hazard) * (v_cont + (m_cont - out["mean"][t]) ** 2) + c.hazard * (
            c.tau**2 + (mu_now - out["mean"][t]) ** 2
        )
        out["sd"][t] = float(np.sqrt(v_mix))
        out["mode"][t] = float(np.argmax(w))
        out["mu0"][t] = mu_now
        # --- update with x[t]
        lp = _logpdf(x[t], post_m, c.sigma**2 + 1.0 / prec)
        joint = logw + lp
        new = np.full(R + 1, -np.inf)
        cp = np.logaddexp.reduce(joint) + log_h
        new[1:] = joint[:-1] + log_1h
        new[R] = np.logaddexp(new[R], joint[R] + log_1h)
        new[0] = cp
        s_new = np.zeros(R + 1)
        s_new[1:] = ssum[:-1] + x[t]
        mu0_new = np.full(R + 1, c.league_mu0)
        mu0_new[1:] = mu0_run[:-1]
        mu0_new[0] = mu_now
        # absorbing bucket R: mixes the run that just reached R and the run already at R (forgetting sum, effective count R)
        a, b = joint[R - 1] + log_1h, joint[R] + log_1h
        tot = np.logaddexp(a, b)
        wa = 1.0 if not np.isfinite(tot) else float(np.exp(a - tot))
        s_new[R] = wa * (ssum[R - 1] + x[t]) + (1.0 - wa) * (ssum[R] * (R - 1) / R + x[t])
        mu0_new[R] = wa * mu0_run[R - 1] + (1.0 - wa) * mu0_run[R]
        logw = new - np.logaddexp.reduce(new)
        ssum, mu0_run = s_new, mu0_new
    return out


# ------------------------------------------------------------------ comparators


def ewma_before(x: np.ndarray, hl: float) -> np.ndarray:
    return ew_mean_before(x, hl)


def two_window_z(x: np.ndarray, fast: int = FAST_W, slow: int = SLOW_W) -> np.ndarray:
    """(mean of the last ``fast`` games - mean of the ``slow`` games before them) / local sd; NaN until fast + 5 games exist."""
    n = len(x)
    out = np.full(n, np.nan)
    for t in range(n):
        if t < fast + 5:
            continue
        f = x[t - fast : t]
        s = x[max(0, t - fast - slow) : t - fast]
        sd = max(float(np.std(x[max(0, t - fast - slow) : t], ddof=1)), CUSUM_SD_FLOOR)
        out[t] = (float(f.mean()) - float(s.mean())) / sd
    return out


def cusum_post(x: np.ndarray) -> dict[str, np.ndarray]:
    """Page two-sided CUSUM with a LOCAL baseline (mean and sd of the previous ``CUSUM_LOCAL_W`` games, sd floored); on a flag the accumulators reset.

    Returns ``since`` (games since the last flag, 30 if none), ``dir`` (+1 / -1 / 0) and ``post_mean`` (mean of the games since the flag, NaN when
    since > 10 or no flag). All as-of: row t sees x[:t]."""
    n = len(x)
    since = np.full(n, 30.0)
    direction = np.zeros(n)
    post = np.full(n, np.nan)
    s_hi = s_lo = 0.0
    last_flag = -1
    last_dir = 0.0
    for t in range(n):
        if last_flag >= 0:
            since[t] = min(float(t - 1 - last_flag), 30.0)
            direction[t] = last_dir
            if since[t] <= 10 and t - 1 >= last_flag:
                post[t] = float(x[last_flag:t].mean())
        if t >= 4:
            base = x[max(0, t - CUSUM_LOCAL_W) : t]
            sd = max(float(np.std(base, ddof=1)), CUSUM_SD_FLOOR)
            z = (x[t] - float(base.mean())) / sd
            s_hi = max(0.0, s_hi + z - CUSUM_DRIFT)
            s_lo = min(0.0, s_lo + z + CUSUM_DRIFT)
            if s_hi > CUSUM_THRESHOLD or -s_lo > CUSUM_THRESHOLD:
                last_dir = 1.0 if s_hi > CUSUM_THRESHOLD else -1.0
                last_flag = t
                s_hi = s_lo = 0.0
    return {"since": since, "dir": direction, "post_mean": post}


# ------------------------------------------------------------------ placebo


def donor_permute(
    values: np.ndarray, date_key: np.ndarray, bucket: np.ndarray, seed: int
) -> np.ndarray:
    """PLACEBO (past-only): permute rows of ``values`` (n, d) within every (date, bucket) cell. Same as-of timing and marginal law, link to the
    player broken. Cells of one row keep the row."""
    rng = np.random.default_rng(seed)
    out = np.array(values, copy=True)
    key = np.char.add(date_key.astype(str), np.char.add("|", bucket.astype(str)))
    order = np.argsort(key, kind="stable")
    ks = key[order]
    cuts = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1], True])
    for a, b in zip(cuts[:-1], cuts[1:], strict=True):
        idx = order[a:b]
        if len(idx) > 1:
            out[idx] = values[rng.permutation(idx)]
    return out


def lag_series(v: np.ndarray, lag: int) -> np.ndarray:
    """PLACEBO (past-only): the same player's feature ``lag`` played games earlier (NaN for the first ``lag`` rows)."""
    out = np.full(len(v), np.nan)
    if len(v) > lag:
        out[lag:] = v[:-lag]
    return out


# ------------------------------------------------------------------ synthetic controls


def synth_step_series(
    n: int, change_at: int, delta: float, level: float, sigma: float, seed: int
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = level + sigma * rng.standard_normal(n)
    x[change_at:] += delta
    return np.clip(x, 0.5, 48.0)


def detection_delay(p: np.ndarray, change_at: int, thr: float, horizon: int = 15) -> float:
    """Games after the change (row index - change_at) at which the as-of feature first reaches ``thr``; NaN if never within ``horizon``.
    Row ``change_at + j`` has seen j post-change games, so delay j = 1 means one post-change game seen."""
    for j in range(1, horizon + 1):
        i = change_at + j
        if i < len(p) and p[i] >= thr:
            return float(j)
    return float("nan")
