"""Simulation harness that chose the coverage construction (Amendment A1.1; docs/CTXRES_V2.md).

Known truth only: integer outcomes from Poisson / NegBin / rounded-Gamma truths, with the exact 19-quantile
grid of the true law ("exact"), the grid of its continuous jittered latent clipped at 0 ("smooth"; what the
residual / quantile / MLP arms emit) or a moment-matched Gamma grid ("gamma_round"). Candidates: a_flat
(Amendment A1), b_lin, c1_cc_flat, c2_cc_lin (CHOSEN, = nba.eval.ctxres_v2_eval.pit_coverage80), d_param.
Run: uv run python -m nba.eval.ctxres_v2_coverage_sim out.json  (about 3 min). No experiment data used.
"""
# mypy: ignore-errors
# ruff: noqa

import sys

import numpy as np
from scipy.stats import gamma, nbinom, poisson

G = np.arange(1, 20) / 20.0
N = 50000
RNG = np.random.default_rng(7)


# ---------------- truth + model grids
def nb_params(mu, alpha):
    if alpha <= 0:
        return None
    n = 1.0 / alpha
    return n, n / (n + mu)


def draw(mu, alpha, n):
    if alpha <= 0:
        return RNG.poisson(mu, n).astype(float)
    nn, p = nb_params(mu, alpha)
    return nbinom.rvs(nn, p, size=n, random_state=RNG).astype(float)


def cdf_true(k, mu, alpha):
    return poisson.cdf(k, mu) if alpha <= 0 else nbinom.cdf(k, *nb_params(mu, alpha))


def pmf_true(k, mu, alpha):
    return poisson.pmf(k, mu) if alpha <= 0 else nbinom.pmf(k, *nb_params(mu, alpha))


def grid_exact(mu, alpha):
    return (poisson.ppf(G, mu) if alpha <= 0 else nbinom.ppf(G, *nb_params(mu, alpha))).astype(float)


def grid_smooth(mu, alpha):
    """Quantiles of the continuous latent X = Y + U(-.5,.5), clipped at 0 (what a continuous model emits)."""
    ks = np.arange(0, int(mu + 12 * np.sqrt(mu + alpha * mu * mu) + 20))
    cum = cdf_true(ks, mu, alpha)
    lo = np.r_[0.0, cum[:-1]]
    q = np.empty(19)
    for i, t in enumerate(G):
        k = int(np.searchsorted(cum, t))  # cell containing the quantile
        k = min(k, len(ks) - 1)
        q[i] = k - 0.5 + (t - lo[k]) / max(cum[k] - lo[k], 1e-12)
    return np.clip(q, 0, None)


def gamma_ab(mu, alpha):
    var = mu + alpha * mu * mu
    return mu * mu / var, var / mu  # shape, scale


def grid_gamma(mu, alpha):
    a, sc = gamma_ab(mu, alpha)
    return np.clip(gamma.ppf(G, a, scale=sc), 0, None)


def draw_gamma_round(mu, alpha, n):
    a, sc = gamma_ab(mu, alpha)
    return np.round(gamma.rvs(a, scale=sc, size=n, random_state=RNG))


# ---------------- F constructions: F(q (n,19), x (n,)) -> (n,)
def f_interp(q, x, tails):
    n = len(x)
    c = (q <= x[:, None]).sum(1)
    ar = np.arange(n)
    lo, hi = np.clip(c - 1, 0, 18), np.clip(c, 0, 18)
    ql, qh = q[ar, lo], q[ar, hi]
    frac = np.where(qh > ql, (x - ql) / np.where(qh > ql, qh - ql, 1.0), 0.0)
    f_int = G[lo] + np.clip(frac, 0, 1) * (G[hi] - G[lo])
    if tails == "flat":
        f_top = np.where(x > q[:, 18], 1.0, G[18])
        return np.where(c == 0, 0.0, np.where(c >= 19, f_top, f_int))
    # linear extrapolation from the two outer knots, clipped to [0,1]
    s_lo = np.where(q[:, 1] > q[:, 0], 0.05 / np.where(q[:, 1] > q[:, 0], q[:, 1] - q[:, 0], 1.0), 0.0)
    s_hi = np.where(q[:, 18] > q[:, 17], 0.05 / np.where(q[:, 18] > q[:, 17], q[:, 18] - q[:, 17], 1.0), 0.0)
    f_lo = np.clip(G[0] - s_lo * (q[:, 0] - x), 0.0, 1.0)
    f_hi = np.clip(G[18] + s_hi * (x - q[:, 18]), 0.0, 1.0)
    f_hi = np.where((x > q[:, 18]) & (s_hi == 0), 1.0, f_hi)  # tied top knots: all mass reached
    return np.where(c == 0, f_lo, np.where(c >= 19, f_hi, f_int))


def make_F(kind):
    if kind == "a_flat":
        return lambda q, x: f_interp(q, x, "flat")
    if kind == "b_lin":
        return lambda q, x: np.where(x < 0, 0.0, f_interp(q, x, "lin"))
    if kind == "c1_cc_flat":
        return lambda q, x: np.where(x < 0, 0.0, f_interp(q, x + 0.5, "flat"))
    if kind == "c2_cc_lin":
        return lambda q, x: np.where(x < 0, 0.0, f_interp(q, x + 0.5, "lin"))
    raise KeyError(kind)


# ---------------- parametric NB (d): lattice match of the 19 quantiles
MU = np.exp(np.linspace(np.log(0.05), np.log(70), 70))
AL = np.r_[0.0, np.exp(np.linspace(np.log(0.01), np.log(2.0), 24))]
LAT = [(m, a) for m in MU for a in AL]
_LATQ = []


def latq():
    if not _LATQ:
        _LATQ.append(np.array([grid_smooth(m, a) for m, a in LAT]))  # smooth-latent lattice
    return _LATQ[0]


def f_param(q, x):
    out = np.empty(len(x))
    uq, inv = np.unique(np.round(q, 6), axis=0, return_inverse=True)
    inv = inv.ravel()
    best = np.empty(len(uq), dtype=int)
    for i0 in range(0, len(uq), 2000):
        blk = uq[i0 : i0 + 2000]
        d = ((blk[:, None, :] - latq()[None, :, :]) ** 2).sum(-1)
        best[i0 : i0 + 2000] = d.argmin(1)
    mus = np.array([LAT[b][0] for b in best])[inv]
    als = np.array([LAT[b][1] for b in best])[inv]
    for a_val in np.unique(als):
        mk = als == a_val
        if a_val <= 0:
            out[mk] = poisson.cdf(x[mk], mus[mk])
        else:
            nn = 1.0 / a_val
            out[mk] = nbinom.cdf(x[mk], nn, nn / (nn + mus[mk]))
    return np.where(x < 0, 0.0, out)


# ---------------- estimators
def cov_cgh(F, q, y):
    """Czado-Gneiting-Held nonrandomized PIT: mean of [Fbar(.9) - Fbar(.1)]."""
    hi, lo = F(q, y), F(q, y - 1.0)
    lo = np.where(y <= 0, 0.0, lo)
    w = np.maximum(hi - lo, 1e-12)

    def fbar(u):
        return np.clip((u - lo) / w, 0.0, 1.0)

    return float((fbar(0.9) - fbar(0.1)).mean())


def cov_rand(F, q, y, seed=1):
    hi, lo = F(q, y), F(q, y - 1.0)
    lo = np.where(y <= 0, 0.0, lo)
    u = lo + np.random.default_rng(seed).uniform(size=len(y)) * (hi - lo)
    return float(((u >= 0.1) & (u <= 0.9)).mean())


KINDS = ["a_flat", "b_lin", "c1_cc_flat", "c2_cc_lin", "d_param"]
EST = {"cgh": cov_cgh, "rand": cov_rand}


def run():
    means = [0.3, 0.7, 1.5, 3, 6, 12, 25, 60]
    alphas = [0.0, 0.15, 0.4]
    rows = []
    for mu in means:
        for al in alphas:
            for gt in ("exact", "smooth", "gamma_round"):
                if gt == "gamma_round":
                    y = draw_gamma_round(mu, al if al > 0 else 0.05, N)
                    g = grid_gamma(mu, al if al > 0 else 0.05)
                else:
                    y = draw(mu, al, N)
                    g = grid_exact(mu, al) if gt == "exact" else grid_smooth(mu, al)
                q = np.tile(g, (N, 1))
                rec = {"mu": mu, "alpha": al, "grid": gt}
                for k in KINDS:
                    F = f_param if k == "d_param" else make_F(k)
                    rec[k] = cov_cgh(F, q, y)
                    if gt != "exact":  # misspec detection: spread around the median x1.4 / x0.7
                        med = np.tile(g[9], (N, 1))
                        rec[k + "_wide"] = cov_cgh(F, np.clip(med + 1.4 * (q - med), 0, None), y)
                        rec[k + "_narrow"] = cov_cgh(F, np.clip(med + 0.7 * (q - med), 0, None), y)
                rows.append(rec)
                print(rec["mu"], rec["alpha"], rec["grid"], {k: round(rec[k], 3) for k in KINDS}, flush=True)
    return rows


def main(argv=None):
    import json

    argv = sys.argv[1:] if argv is None else argv
    rows = run()
    if argv:
        json.dump(rows, open(argv[0], "w"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
