# ruff: noqa: E501
"""Local scoring of the basketball GPT (Colab job ``pbp_gpt``) on season 2024.

PRE-REGISTERED PROTOCOL AND KEEP RULES (written 2026-10-08 BEFORE any Colab run of the job; full
text and rationale in ``docs/PBP_GPT.md``; none of this is changed after seeing results).

Question. A decoder-only transformer trained on tokenized play-by-play (``research.features.pbp_tokens``)
predicts the next event. Sampling continuations from any in-game state gives (a) LIVE win
probability, margin / total and live player lines, and (b) pregame full-game samples. Does it beat
standard baselines, with honest calibration?

Design. Seasons 2022-2024 only; 2025 is never loaded. Train = 2022 + first 80% of 2023; early
stopping and trial selection on the last 20% of 2023 (next-token loss only). The selected model is
fixed and scored once on season 2024 (report). Evaluation games: a seeded random subset of ``ok``
2024 games (score of the token stream equals the box score; all five checkpoints valid; >= 9
rostered players per side), identical for every target.

TARGETS.
  (G0) Sanity gate -- next-token perplexity on 2024 (all learned tokens): GPT vs (a) slot-conditional
       unigram and (b) interpolated trigram (weights chosen on the 2023 val slice). Paired
       game-clustered bootstrap of the per-token NLL difference. The gate PASSES iff the upper 95%
       bound of (GPT - trigram) < 0. If it fails, the sampled targets below are reported but flagged:
       a language model that does not beat a trigram is not a simulator.
  (P1) PRIMARY -- LIVE win probability at four checkpoints (start of Q2, start of Q3, start of Q4,
       first event boundary at <= 5:00 left in Q4). GPT p = (home wins + 0.5 ties + 0.5)/(S + 1)
       over S sampled continuations (rows that did not finish within the token cap count with their
       current margin). Baseline: logistic regression on [diff/sqrt(t_rem+0.5),
       logit(p_pre)*sqrt(t_rem/48), 1] (t_rem in minutes, diff = home - away at the checkpoint, p_pre
       = pregame injury-Elo), fitted ONLY on train-split checkpoint states of all four checkpoints
       pooled, no re-fit on 2024. Metrics per checkpoint: log loss (decision metric), Brier, ECE (10
       equal-width bins), game-clustered 95% bootstrap CIs (2000 resamples, seed 20261008).
       Family = the four per-checkpoint log-loss differences (GPT - baseline); Benjamini-Hochberg at
       q = 0.05 across the family. A checkpoint PASSES iff upper CI < 0, BH-adjusted p <= 0.05 and
       ECE(GPT) - ECE(baseline) <= +0.01 (point estimate). TARGET KEPT iff at least one checkpoint
       passes and no checkpoint is significantly WORSE (lower CI > 0 and BH-adjusted p <= 0.05).
       No post-hoc recalibration of the GPT is used for the decision.
  (P2) SECONDARY -- pregame (tip checkpoint, full-game samples): win probability vs pregame
       injury-Elo (log loss), margin and total CRPS vs ``nba.parlay.game_model`` Normal(mu, sd).
       Expected to LOSE; reported with game-level CIs and BH within this three-test family; kept
       only if all three upper CIs < 0.
  (P3) SECONDARY -- live remaining-game player points / rebounds / assists at the four live
       checkpoints for rostered players with as-of mean minutes >= 12: CRPS of the GPT sample
       distribution vs "current + pro-rated recency" (as-of last-10 mean x fraction of regulation
       remaining) with a Negative Binomial whose dispersion is fitted on TRAIN-split checkpoints.
       Paired player-game bootstrap clustered by game; BH across the 12 (stat x checkpoint) tests; a
       cell wins iff upper CI < 0 and adjusted p <= 0.05. Descriptive: bias, 80% interval coverage.
  Descriptive only (never used to keep or tune): cold-start token NLL (NEW-player actors vs known),
  per-category NLL, throughput, split-half agreement of the sampled probabilities, Elo-only
  reference, unfinished-row share.

Anything else is NOT KEPT; a negative result is reported as a negative result.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from scipy import stats

SEED = 20261008
N_BOOT = 2000
CATS = ("dt", "evt", "actor", "actor_new", "assist", "sub_out", "sub_in")
CKPT_NAMES = ("tip", "q2", "q3", "q4", "q4_5min")
LIVE = ("q2", "q3", "q4", "q4_5min")
STAT_NAMES = ("pts", "reb", "ast")
ECE_TOL = 0.01
MIN_ASOF_MIN = 12.0
SL_ACTOR, SL_ASSIST, SL_OUT, SL_IN, SL_DT, SL_EVT = 3, 4, 5, 6, 1, 2


# ----------------------------------------------------------------------------- primitives


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _ll_vec(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.asarray(-(y * np.log(p) + (1 - y) * np.log(1 - p)), dtype=float)


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((np.asarray(p, dtype=float) - y) ** 2))


def ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    p = np.asarray(p, dtype=float)
    idx = np.minimum((p * bins).astype(int), bins - 1)
    tot = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            tot += m.mean() * abs(float(p[m].mean()) - float(np.mean(y[m])))
    return float(tot)


def bh_adjust(p: list[float] | np.ndarray) -> list[float]:
    """Benjamini-Hochberg adjusted p-values (monotone, capped at 1)."""
    arr = np.asarray(p, dtype=float)
    n = len(arr)
    order = np.argsort(arr)
    adj = np.empty(n)
    run = 1.0
    for rank in range(n - 1, -1, -1):
        i = order[rank]
        run = min(run, arr[i] * n / (rank + 1))
        adj[i] = run
    return [float(min(a, 1.0)) for a in adj]


def cluster_bootstrap_mean(
    d: np.ndarray, clusters: np.ndarray, n_boot: int = N_BOOT, seed: int = SEED
) -> tuple[float, float, float]:
    """95% CI and two-sided p of the mean of paired differences ``d`` (cluster resampling)."""
    d = np.asarray(d, dtype=float)
    _, inv = np.unique(clusters, return_inverse=True)
    k = int(inv.max()) + 1
    sums = np.bincount(inv, weights=d, minlength=k)
    cnts = np.bincount(inv, minlength=k).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, k, size=(n_boot, k))
    boot = sums[idx].sum(1) / cnts[idx].sum(1)
    lo, hi = np.quantile(boot, [0.025, 0.975])
    p = 2.0 * min((np.sum(boot >= 0) + 1) / (n_boot + 1), (np.sum(boot <= 0) + 1) / (n_boot + 1))
    return float(lo), float(hi), float(min(p, 1.0))


def cluster_bootstrap_ratio(
    num: np.ndarray, den: np.ndarray, n_boot: int = N_BOOT, seed: int = SEED
) -> tuple[float, float, float, float]:
    """Point, 95% CI, p of ``sum(num)/sum(den)`` resampling rows (= games)."""
    num = np.asarray(num, dtype=float)
    den = np.asarray(den, dtype=float)
    rng = np.random.default_rng(seed)
    k = len(num)
    idx = rng.integers(0, k, size=(n_boot, k))
    boot = num[idx].sum(1) / den[idx].sum(1)
    lo, hi = np.quantile(boot, [0.025, 0.975])
    p = 2.0 * min((np.sum(boot >= 0) + 1) / (n_boot + 1), (np.sum(boot <= 0) + 1) / (n_boot + 1))
    return float(num.sum() / den.sum()), float(lo), float(hi), float(min(p, 1.0))


def crps_samples(samples: np.ndarray, y: float | np.ndarray) -> float | np.ndarray:
    """CRPS of an empirical distribution: E|X-y| - 0.5 E|X-X'| (exact for integer outcomes)."""
    x = np.sort(np.asarray(samples, dtype=float), axis=-1)
    s = x.shape[-1]
    yy = np.asarray(y, dtype=float)
    e1 = np.abs(x - yy[..., None]).mean(-1) if yy.ndim else np.abs(x - yy).mean(-1)
    w = 2.0 * np.arange(1, s + 1) - s - 1.0
    e2 = (w * x).sum(-1) * 2.0 / (s * s)
    out = e1 - 0.5 * e2
    return float(out) if np.ndim(out) == 0 else out


def crps_normal(mu: np.ndarray, sd: np.ndarray, y: np.ndarray) -> np.ndarray:
    z = (y - mu) / sd
    out = sd * (z * (2 * stats.norm.cdf(z) - 1) + 2 * stats.norm.pdf(z) - 1 / math.sqrt(math.pi))
    return np.asarray(out, dtype=float)


def crps_nbinom_grid(m: np.ndarray, phi: float, y: np.ndarray, kmax: int = 120) -> np.ndarray:
    """Exact CRPS of NB(mean m, var m + phi m^2) at integer y (support grid 0..kmax)."""
    m = np.maximum(np.asarray(m, dtype=float), 1e-9)
    ks = np.arange(kmax + 1)
    if phi <= 1e-9:
        cdf = stats.poisson.cdf(ks[None, :], m[:, None])
    else:
        n = 1.0 / phi
        pr = n / (n + m)
        cdf = stats.nbinom.cdf(ks[None, :], n, pr[:, None])
    ind = (ks[None, :] >= np.asarray(y)[:, None]).astype(float)
    return np.asarray(((cdf - ind) ** 2).sum(1), dtype=float)


def nbinom_quantiles(m: np.ndarray, phi: float, qs: tuple[float, ...]) -> np.ndarray:
    m = np.maximum(np.asarray(m, dtype=float), 1e-9)
    if phi <= 1e-9:
        return np.stack([stats.poisson.ppf(q, m) for q in qs], 1)
    n = 1.0 / phi
    pr = n / (n + m)
    return np.stack([stats.nbinom.ppf(q, n, pr) for q in qs], 1)


# ----------------------------------------------------------------------------- live baseline


def live_features(diff: np.ndarray, t_rem_min: np.ndarray, logit_pre: np.ndarray) -> np.ndarray:
    tr = np.asarray(t_rem_min, dtype=float)
    return np.stack(
        [
            np.asarray(diff, dtype=float) / np.sqrt(tr + 0.5),
            np.asarray(logit_pre, dtype=float) * np.sqrt(np.maximum(tr, 0.0) / 48.0),
        ],
        1,
    )


def fit_live_baseline(f: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Unpenalised logistic regression with intercept (Newton iterations). Returns [w1, w2, b]."""
    x = np.hstack([f, np.ones((len(f), 1))])
    w = np.zeros(x.shape[1])
    for _ in range(50):
        z = x @ w
        p = 1 / (1 + np.exp(-z))
        g = x.T @ (p - y) + 1e-6 * w
        h = (x * (p * (1 - p))[:, None]).T @ x + 1e-6 * np.eye(len(w))
        step = np.linalg.solve(h, g)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w


def predict_live_baseline(w: np.ndarray, f: np.ndarray) -> np.ndarray:
    z = np.hstack([f, np.ones((len(f), 1))]) @ w
    return np.asarray(np.clip(1 / (1 + np.exp(-z)), 1e-4, 1 - 1e-4), dtype=float)


def live_keep_verdict(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pre-registered keep rule for the live win-probability target."""
    passing = [
        r["ckpt"] for r in rows if r["hi"] < 0 and r["p_adj"] <= 0.05 and r["ece_diff"] <= ECE_TOL
    ]
    worse = [r["ckpt"] for r in rows if r["lo"] > 0 and r["p_adj"] <= 0.05]
    return {"kept": bool(passing) and not worse, "passing": passing, "significantly_worse": worse}


# ----------------------------------------------------------------------------- n-gram baselines


def _cat_of(slot: np.ndarray, tok: np.ndarray, new0: int, pl0: int) -> np.ndarray:
    cat = np.full(slot.shape, -1, dtype=np.int64)
    cat[slot == SL_DT] = 0
    cat[slot == SL_EVT] = 1
    is_new = (tok >= new0) & (tok < pl0)
    cat[slot == SL_ACTOR] = np.where(is_new[slot == SL_ACTOR], 3, 2)
    cat[slot == SL_ASSIST] = 4
    cat[slot == SL_OUT] = 5
    cat[slot == SL_IN] = 6
    return cat


def _collect(
    tokens: np.ndarray, slots: np.ndarray, offsets: np.ndarray, idx: np.ndarray, new0: int, pl0: int
) -> dict[str, np.ndarray]:
    t_l, p1_l, p2_l, s_l, g_l = [], [], [], [], []
    for gi, g in enumerate(idx):
        a, b = int(offsets[g]), int(offsets[g + 1])
        tk = tokens[a:b].astype(np.int64)
        sl = slots[a:b].astype(np.int64)
        pos = np.nonzero(sl > 0)[0]
        pos = pos[pos >= 2]
        t_l.append(tk[pos])
        p1_l.append(tk[pos - 1])
        p2_l.append(tk[pos - 2])
        s_l.append(sl[pos])
        g_l.append(np.full(len(pos), gi))
    t = np.concatenate(t_l)
    s = np.concatenate(s_l)
    return {
        "tok": t,
        "p1": np.concatenate(p1_l),
        "p2": np.concatenate(p2_l),
        "slot": s,
        "game": np.concatenate(g_l),
        "cat": _cat_of(s, t, new0, pl0),
    }


def _lookup(keys_sorted: np.ndarray, counts: np.ndarray, q: np.ndarray) -> np.ndarray:
    pos = np.searchsorted(keys_sorted, q)
    pos = np.minimum(pos, len(keys_sorted) - 1)
    hit = keys_sorted[pos] == q
    return np.where(hit, counts[pos], 0)


def ngram_baselines(
    tokens: np.ndarray,
    slots: np.ndarray,
    offsets: np.ndarray,
    vocab: dict[str, Any],
    train_idx: np.ndarray,
    val_idx: np.ndarray,
    report_idx: np.ndarray,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Slot-conditional unigram and interpolated trigram per-game category NLL on ``report_idx``."""
    lay = vocab["layout"]
    v = int(vocab["size"])
    new0, pl0 = int(lay["new0"]), int(lay["pl0"])
    n_slot_vocab = {
        SL_DT: int(lay["n_dt"]),
        SL_EVT: int(lay["n_evt"]),
        SL_ACTOR: v - new0,
        SL_ASSIST: v - new0,
        SL_OUT: v - new0,
        SL_IN: v - new0,
    }
    tr = _collect(tokens, slots, offsets, train_idx, new0, pl0)
    uni = np.zeros((8, v))
    np.add.at(uni, (tr["slot"], tr["tok"]), 1.0)
    uni_n = uni.sum(1)
    k1 = np.unique(tr["p1"] * v + tr["tok"], return_counts=True)
    c1 = np.unique(tr["p1"], return_counts=True)
    k2 = np.unique((tr["p2"] * v + tr["p1"]) * v + tr["tok"], return_counts=True)
    c2 = np.unique(tr["p2"] * v + tr["p1"], return_counts=True)

    def probs(d: dict[str, np.ndarray], lam2: float, lam3: float) -> tuple[np.ndarray, np.ndarray]:
        slot, tok = d["slot"], d["tok"]
        ksz = np.ones(8)
        for s, kz in n_slot_vocab.items():
            ksz[s] = kz
        p1 = (uni[slot, tok] + 0.1) / (uni_n[slot] + 0.1 * ksz[slot])
        n2 = _lookup(c1[0], c1[1], d["p1"]).astype(float)
        m2 = _lookup(k1[0], k1[1], d["p1"] * v + tok).astype(float)
        has2 = n2 > 0
        p2 = np.where(has2, m2 / np.maximum(n2, 1), 0.0)
        n3 = _lookup(c2[0], c2[1], d["p2"] * v + d["p1"]).astype(float)
        m3 = _lookup(k2[0], k2[1], (d["p2"] * v + d["p1"]) * v + tok).astype(float)
        has3 = n3 > 0
        p3 = np.where(has3, m3 / np.maximum(n3, 1), 0.0)
        w3 = lam3 * has3
        w2 = lam2 * has2
        denom = w3 + w2 + 1.0
        return p1, (w3 * p3 + w2 * p2 + p1) / denom

    va = _collect(tokens, slots, offsets, val_idx, new0, pl0)
    best = (1.0, 1.0)
    best_nll = np.inf
    for l2 in (0.25, 1.0, 4.0, 16.0):
        for l3 in (0.25, 1.0, 4.0, 16.0, 64.0):
            _, p = probs(va, l2, l3)
            nll = float(-np.log(np.maximum(p, 1e-12)).mean())
            if nll < best_nll:
                best_nll, best = nll, (l2, l3)
    rp = _collect(tokens, slots, offsets, report_idx, new0, pl0)
    p_uni, p_tri = probs(rp, *best)
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    n_g = len(report_idx)
    for name, p in (("unigram", p_uni), ("trigram", p_tri)):
        sums = np.zeros((n_g, len(CATS)))
        cnts = np.zeros((n_g, len(CATS)))
        nll = -np.log(np.maximum(p, 1e-12))
        np.add.at(sums, (rp["game"], rp["cat"]), nll)
        np.add.at(cnts, (rp["game"], rp["cat"]), 1.0)
        out[name] = (sums, cnts)
    return out


# ----------------------------------------------------------------------------- evaluation


def _load_samples(run: Path, name: str) -> dict[str, np.ndarray] | None:
    p = run / f"samples_{name}.npz"
    if not p.exists():
        return None
    z = np.load(p)
    return {k: z[k] for k in z.files}


def _ckpt_state(ck: dict[str, np.ndarray], gi: np.ndarray, c: int) -> dict[str, np.ndarray]:
    sc = ck["scal"][gi, c]
    return {
        "diff": sc[:, 2] - sc[:, 3],
        "tr_min": (sc[:, 5] + np.clip(4 - sc[:, 4], 0, 4) * 720.0) / 60.0,
    }


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.asarray(np.log(p / (1 - p)), dtype=float)


def _ci_row(delta_vec: np.ndarray, clusters: np.ndarray) -> dict[str, float]:
    lo, hi, p = cluster_bootstrap_mean(delta_vec, clusters)
    return {"delta": float(delta_vec.mean()), "lo": lo, "hi": hi, "p": p}


def evaluate_run(run_dir: str | Path, data_dir: str | Path) -> dict[str, Any]:
    """Score a pulled job run. Reads only exported / job files (no database)."""
    run, data = Path(run_dir), Path(data_dir)
    games = pl.read_parquet(data / "games.parquet")
    if int(games["season"].to_numpy().max()) >= 2025:
        raise ValueError("season 2025 present: the frozen holdout must not be scored")
    with np.load(data / "ckpt.npz") as z:
        ck = {k: z[k] for k in z.files}
    with np.load(data / "tokens.npz") as z:
        tokens, slots, offsets = z["tokens"], z["slots"], z["offsets"]
    vocab = json.loads((data / "vocab.json").read_text())
    metrics = (
        json.loads((run / "metrics.json").read_text()) if (run / "metrics.json").exists() else {}
    )
    split = games["split"].to_numpy()
    res: dict[str, Any] = {"run": str(run), "n_games_export": games.height}

    # ---- (G0) perplexity
    ppl = pl.read_parquet(run / "ppl_by_game.parquet")
    rep = ppl.filter(pl.col("split") == "report")
    rep_idx = rep["game_idx"].to_numpy()
    gs = np.stack([rep[f"nll_{c}"].to_numpy() for c in CATS], 1)
    gn = np.stack([rep[f"n_{c}"].to_numpy() for c in CATS], 1)
    tr_idx = np.where(split == "train")[0]
    va_idx = np.where(split == "val")[0]
    base = ngram_baselines(tokens, slots, offsets, vocab, tr_idx, va_idx, rep_idx)
    g0: dict[str, Any] = {"n_games": int(len(rep_idx)), "n_tokens": int(gn.sum())}
    g0["gpt_nll"] = float(gs.sum() / gn.sum())
    for name, (bs, bn) in base.items():
        assert np.allclose(bn, gn)
        pt_, lo, hi, p = cluster_bootstrap_ratio(gs.sum(1) - bs.sum(1), gn.sum(1))
        g0[name] = {
            "nll": float(bs.sum() / bn.sum()),
            "gpt_minus_base": pt_,
            "lo": lo,
            "hi": hi,
            "p": p,
        }
    g0["by_category"] = {
        c: {
            "gpt": float(gs[:, i].sum() / max(1.0, gn[:, i].sum())),
            "trigram": float(base["trigram"][0][:, i].sum() / max(1.0, gn[:, i].sum())),
            "unigram": float(base["unigram"][0][:, i].sum() / max(1.0, gn[:, i].sum())),
            "n": int(gn[:, i].sum()),
        }
        for i, c in enumerate(CATS)
    }
    g0["gate_pass"] = bool(g0["trigram"]["hi"] < 0)
    res["G0_perplexity"] = g0

    # ---- evaluation game set
    ok = games["ok"].to_numpy() & (split == "report")
    p_elo = games["p_elo"].to_numpy()
    home_win = (games["home_pts"].to_numpy() > games["away_pts"].to_numpy()).astype(float)

    # ---- (P1) live win probability
    train_ok = np.where(games["ok"].to_numpy() & (split == "train"))[0]
    fx, fy = [], []
    for c in range(1, 5):
        valid = ck["scal"][train_ok, c, 0] > 0
        gi = train_ok[valid]
        s = _ckpt_state(ck, gi, c)
        fx.append(live_features(s["diff"], s["tr_min"], _logit(p_elo[gi])))
        fy.append(home_win[gi])
    w = fit_live_baseline(np.vstack(fx), np.concatenate(fy))
    res["live_baseline_weights"] = [float(x) for x in w]
    live_rows: list[dict[str, Any]] = []
    for name in LIVE:
        smp = _load_samples(run, name)
        if smp is None:
            continue
        c = CKPT_NAMES.index(name)
        gi = smp["game_idx"]
        keep = ok[gi]
        gi = gi[keep]
        m = smp["margin"][keep].astype(float)
        wins = ((m > 0) + 0.5 * (m == 0)).sum(1)
        s_ = m.shape[1]
        p_gpt = (wins + 0.5) / (s_ + 1.0)
        s = _ckpt_state(ck, gi, c)
        p_base = predict_live_baseline(w, live_features(s["diff"], s["tr_min"], _logit(p_elo[gi])))
        y = home_win[gi]
        d = _ll_vec(p_gpt, y) - _ll_vec(p_base, y)
        row = {
            "ckpt": name,
            "n": int(len(gi)),
            "sims": int(s_),
            "ll_gpt": log_loss(p_gpt, y),
            "ll_base": log_loss(p_base, y),
            "ll_elo": log_loss(p_elo[gi], y),
            "brier_gpt": brier(p_gpt, y),
            "brier_base": brier(p_base, y),
            "ece_gpt": ece(p_gpt, y),
            "ece_base": ece(p_base, y),
        }
        row["ece_diff"] = float(row["ece_gpt"]) - float(row["ece_base"])  # type: ignore[arg-type]
        row.update(_ci_row(d, gi))
        bd = (p_gpt - y) ** 2 - (p_base - y) ** 2
        lo, hi, _ = cluster_bootstrap_mean(bd, gi)
        row.update(brier_delta=float(bd.mean()), brier_lo=lo, brier_hi=hi)
        half = s_ // 2
        if half >= 2:
            pa = (m[:, :half] > 0).mean(1)
            pb = (m[:, half:] > 0).mean(1)
            row["split_half_corr"] = (
                float(np.corrcoef(pa, pb)[0, 1]) if pa.std() > 0 and pb.std() > 0 else None
            )
        row["frac_unfinished"] = float(1.0 - smp["done"][keep].mean())
        live_rows.append(row)
    padj = bh_adjust([r["p"] for r in live_rows]) if live_rows else []
    for r, pa_ in zip(live_rows, padj, strict=True):
        r["p_adj"] = pa_
    res["P1_live_wp"] = {
        "rows": live_rows,
        "verdict": live_keep_verdict(live_rows) if live_rows else None,
    }

    # ---- (P2) pregame
    tip = _load_samples(run, "tip")
    if tip is not None:
        gi = tip["game_idx"]
        keep = ok[gi]
        gi = gi[keep]
        m = tip["margin"][keep].astype(float)
        t = tip["total"][keep].astype(float)
        wins = ((m > 0) + 0.5 * (m == 0)).sum(1)
        p_gpt = (wins + 0.5) / (m.shape[1] + 1.0)
        y = home_win[gi]
        d_ll = _ll_vec(p_gpt, y) - _ll_vec(p_elo[gi], y)
        mm = games["home_pts"].to_numpy()[gi] - games["away_pts"].to_numpy()[gi]
        tt = games["home_pts"].to_numpy()[gi] + games["away_pts"].to_numpy()[gi]
        c_g_m = np.asarray(crps_samples(m, mm.astype(float)))
        c_g_t = np.asarray(crps_samples(t, tt.astype(float)))
        c_n_m = crps_normal(
            games["mu_margin"].to_numpy()[gi], games["sd_margin"].to_numpy()[gi], mm
        )
        c_n_t = crps_normal(games["mu_total"].to_numpy()[gi], games["sd_total"].to_numpy()[gi], tt)
        rows = []
        for lab, dv in (
            ("win_logloss", d_ll),
            ("margin_crps", c_g_m - c_n_m),
            ("total_crps", c_g_t - c_n_t),
        ):
            r = {"metric": lab, "n": int(len(gi)), **_ci_row(dv, gi)}
            rows.append(r)
        for r, pa_ in zip(rows, bh_adjust([r["p"] for r in rows]), strict=True):
            r["p_adj"] = pa_
        res["P2_pregame"] = {
            "rows": rows,
            "sims": int(m.shape[1]),
            "ll_gpt": log_loss(p_gpt, y),
            "ll_elo": log_loss(p_elo[gi], y),
            "ece_gpt": ece(p_gpt, y),
            "ece_elo": ece(p_elo[gi], y),
            "margin_sd_gpt": float(m.std(1).mean()),
            "total_mean_gpt": float(t.mean()),
            "total_mean_actual": float(tt.mean()),
            "kept": bool(all(r["hi"] < 0 for r in rows)),
        }

    # ---- (P3) live player lines
    phi = _fit_dispersion(ck, games, train_ok)
    cells: list[dict[str, Any]] = []
    for name in LIVE:
        smp = _load_samples(run, name)
        if smp is None:
            continue
        c = CKPT_NAMES.index(name)
        gi_all = smp["game_idx"]
        for si, st_name in enumerate(STAT_NAMES):
            gcl, dv, bias_g, bias_b, cov_g, cov_b = [], [], [], [], [], []
            for row_i, g in enumerate(gi_all):
                if not ok[g]:
                    continue
                tr_min = (ck["scal"][g, c, 5] + max(0, 4 - ck["scal"][g, c, 4]) * 720.0) / 60.0
                asof = ck["asof"][g, c]  # [2,RM,4]
                rost = ck["rost"][g, c]
                elig = (rost > 0) & (asof[..., 3] >= MIN_ASOF_MIN)
                if not elig.any():
                    continue
                sd_, jj = np.nonzero(elig)
                y = (ck["final"][g, c][sd_, jj, si] - ck["box"][g, c][sd_, jj, si]).astype(float)
                x = smp["pstat"][row_i][:, sd_, jj, si].T.astype(float)  # [n, S]
                mu = asof[sd_, jj, si] * min(1.0, tr_min / 48.0)
                cg = np.asarray(crps_samples(x, y))
                cb = crps_nbinom_grid(mu, phi[si], y)
                gcl.append(np.full(len(y), g))
                dv.append(cg - cb)
                bias_g.append(x.mean(1) - y)
                bias_b.append(mu - y)
                q = np.quantile(x, [0.1, 0.9], axis=1).T
                cov_g.append(((y >= q[:, 0]) & (y <= q[:, 1])).astype(float))
                qb = nbinom_quantiles(mu, phi[si], (0.1, 0.9))
                cov_b.append(((y >= qb[:, 0]) & (y <= qb[:, 1])).astype(float))
            if not dv:
                continue
            d = np.concatenate(dv)
            cl = np.concatenate(gcl)
            cell = {
                "ckpt": name,
                "stat": st_name,
                "n_player_games": int(len(d)),
                "phi": float(phi[si]),
                **_ci_row(d, cl),
                "bias_gpt": float(np.concatenate(bias_g).mean()),
                "bias_base": float(np.concatenate(bias_b).mean()),
                "cov80_gpt": float(np.concatenate(cov_g).mean()),
                "cov80_base": float(np.concatenate(cov_b).mean()),
            }
            cells.append(cell)
    for cell, pa_ in zip(cells, bh_adjust([c_["p"] for c_ in cells]) if cells else [], strict=True):
        cell["p_adj"] = pa_
        cell["wins"] = bool(float(cell["hi"]) < 0 and pa_ <= 0.05)  # type: ignore[arg-type]
    res["P3_player_lines"] = {"cells": cells, "n_wins": int(sum(c_["wins"] for c_ in cells))}
    res["throughput"] = metrics.get("sampling")
    res["device"] = metrics.get("device")
    res["best_cfg"] = metrics.get("best_cfg")
    res["best_trial"] = metrics.get("best_trial")
    return res


def _fit_dispersion(
    ck: dict[str, np.ndarray], games: pl.DataFrame, train_ok: np.ndarray
) -> list[float]:
    """NB dispersion of the pro-rated recency baseline by stat, from TRAIN-split live checkpoints."""
    out = []
    for si in range(3):
        num = den = 0.0
        for c in range(1, 5):
            for g in train_ok:
                if ck["scal"][g, c, 0] <= 0:
                    continue
                tr_min = (ck["scal"][g, c, 5] + max(0, 4 - ck["scal"][g, c, 4]) * 720.0) / 60.0
                asof = ck["asof"][g, c]
                rost = ck["rost"][g, c]
                elig = (rost > 0) & (asof[..., 3] >= MIN_ASOF_MIN)
                if not elig.any():
                    continue
                sd_, jj = np.nonzero(elig)
                y = (ck["final"][g, c][sd_, jj, si] - ck["box"][g, c][sd_, jj, si]).astype(float)
                mu = asof[sd_, jj, si] * min(1.0, tr_min / 48.0)
                num += float(((y - mu) ** 2 - mu).sum())
                den += float((mu**2).sum())
        out.append(max(0.0, num / den) if den > 0 else 0.0)
    return out


# ----------------------------------------------------------------------------- report


def _fmt(x: float, k: int = 4) -> str:
    return f"{x:+.{k}f}" if isinstance(x, float) else str(x)


def render_report(res: dict[str, Any]) -> str:
    L = ["# basketball GPT - 2024 report (pre-registered, docs/PBP_GPT.md)", ""]
    g0 = res["G0_perplexity"]
    L += [
        "## G0 next-token perplexity (2024, learned tokens)",
        f"games {g0['n_games']}, tokens {g0['n_tokens']:,}. GPT NLL {g0['gpt_nll']:.4f} (ppl {math.exp(g0['gpt_nll']):.3f})",
        "",
        "| baseline | NLL | GPT - base | 95% CI | p |",
        "|---|---|---|---|---|",
    ]
    for b in ("unigram", "trigram"):
        r = g0[b]
        L.append(
            f"| {b} | {r['nll']:.4f} | {r['gpt_minus_base']:+.4f} | [{r['lo']:+.4f}, {r['hi']:+.4f}] | {r['p']:.3g} |"
        )
    L += [
        "",
        f"Gate (CI vs trigram excludes 0, better): **{'PASS' if g0['gate_pass'] else 'FAIL'}**",
        "",
        "| category | n | GPT | trigram | unigram |",
        "|---|---|---|---|---|",
    ]
    for c, r in g0["by_category"].items():
        L.append(f"| {c} | {r['n']:,} | {r['gpt']:.4f} | {r['trigram']:.4f} | {r['unigram']:.4f} |")
    p1 = res["P1_live_wp"]
    L += [
        "",
        "## P1 live win probability (primary)",
        "",
        "| ckpt | n | sims | LL gpt | LL base | LL elo | delta | 95% CI | p_adj | ECE gpt | ECE base | Brier d |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in p1["rows"]:
        L.append(
            f"| {r['ckpt']} | {r['n']} | {r['sims']} | {r['ll_gpt']:.4f} | {r['ll_base']:.4f} | {r['ll_elo']:.4f} | {r['delta']:+.4f} | [{r['lo']:+.4f}, {r['hi']:+.4f}] | {r['p_adj']:.3g} | {r['ece_gpt']:.3f} | {r['ece_base']:.3f} | {r['brier_delta']:+.4f} |"
        )
    v = p1["verdict"]
    L += ["", f"Verdict: **{'KEPT' if v and v['kept'] else 'NOT KEPT'}** ({json.dumps(v)})"]
    if "P2_pregame" in res:
        p2 = res["P2_pregame"]
        L += [
            "",
            "## P2 pregame (secondary)",
            f"sims {p2['sims']}; LL gpt {p2['ll_gpt']:.4f} vs elo {p2['ll_elo']:.4f}; mean total gpt {p2['total_mean_gpt']:.1f} vs actual {p2['total_mean_actual']:.1f}",
            "",
            "| metric | n | delta (gpt - ref) | 95% CI | p_adj |",
            "|---|---|---|---|---|",
        ]
        for r in p2["rows"]:
            L.append(
                f"| {r['metric']} | {r['n']} | {r['delta']:+.4f} | [{r['lo']:+.4f}, {r['hi']:+.4f}] | {r['p_adj']:.3g} |"
            )
        L.append(f"\nKept: **{p2['kept']}**")
    p3 = res["P3_player_lines"]
    L += [
        "",
        "## P3 live player lines (secondary)",
        f"cells won: {p3['n_wins']}/{len(p3['cells'])}",
        "",
        "| ckpt | stat | n | CRPS delta | 95% CI | p_adj | bias gpt | bias base | cov80 gpt | cov80 base |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in p3["cells"]:
        L.append(
            f"| {c['ckpt']} | {c['stat']} | {c['n_player_games']} | {c['delta']:+.4f} | [{c['lo']:+.4f}, {c['hi']:+.4f}] | {c['p_adj']:.3g} | {c['bias_gpt']:+.3f} | {c['bias_base']:+.3f} | {c['cov80_gpt']:.3f} | {c['cov80_base']:.3f} |"
        )
    if res.get("throughput"):
        t = res["throughput"]
        L += [
            "",
            f"Sampling throughput: {t.get('rows_per_s', 0):.0f} continuations/s, {t.get('tokens_per_s', 0):,.0f} tokens/s ({res.get('device')})",
        ]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score a pulled pbp_gpt run on season 2024.")
    ap.add_argument(
        "--run",
        required=True,
        help="pulled artifact dir (metrics.json, samples_*.npz, ppl_by_game.parquet)",
    )
    ap.add_argument(
        "--data",
        default="data/colab/pbp_gpt",
        help="export dir (games.parquet, tokens.npz, ckpt.npz, vocab.json)",
    )
    a = ap.parse_args(argv)
    res = evaluate_run(a.run, a.data)
    out = Path(a.run)
    (out / "eval.json").write_text(json.dumps(res, indent=1, default=float))
    (out / "report.md").write_text(render_report(res))
    print(render_report(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
