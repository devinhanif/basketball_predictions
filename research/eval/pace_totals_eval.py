# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""PACE_TOTALS harness (rule: docs/prereg/PACE_TOTALS.md; refuses unless the rule text above the marker hashes to ``--sha`` AND a commit
``prereg(PACE_TOTALS): <sha>`` is reachable in git log).

    M=research.eval.pace_totals_eval
    .venv/bin/python -W ignore -m $M selftest                 # synthetic data, no DB, no sha; proves the pipeline runs
    .venv/bin/python -W ignore -m $M check --sha <64 hex>     # freeze refusal + row counts (read-only DB)
    .venv/bin/python -W ignore -m $M run   --sha <64 hex>     # P/M/B walk-forward, guards, slices, periods, verdict (heavy.lock held)

Arms: P (pace x efficiency, OLS), M (Normal(line, sigma_M)), B (line + w (mu_P - line), w by in-window integer CRPS), all Normal on the integer grid.
Walk-forward by calendar month over games with a line and as-of features (2022 has no odds: warm-up for features only). Select 2023, report 2024.
Reads ``nba.duckdb`` and ``odds_history.duckdb`` read-only with ``season <= 2024`` in SQL and asserted after load. Season 2025 is never loaded.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from scipy.special import ndtr

from nba.eval.context_residual_eval import bh_adjust

ID = "PACE_TOTALS"
MARKER = "=== RESULTS BELOW ==="
ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/prereg/PACE_TOTALS.md"
OUT_DIR = ROOT / "reports/prereg_pace_totals"
LOCK = ROOT / "data/ops/heavy.lock"
MAX_SEASON = 2024
SELECT_SEASON, REPORT_SEASON = 2023, 2024
N_BOOT = 2000
SEED = 20261012
MIN_PRIOR = 600
W_GRID = np.round(np.arange(0.0, 1.0001, 0.05), 2)
FLOOR_CRPS, FLOOR_LL, FLOOR_ROI = -0.05, -0.002, 0.01
EV_MIN, MIN_BETS = 0.05, 300
SHUFFLE_WORSE = 0.5
PER = ["q1", "q2", "q3", "q4", "h1", "h2"]
FEATS = ["pace_h", "off_h", "def_h", "pace_a", "off_a", "def_a"]
PAD = 100  # integer-grid padding around the observed range, identical for every arm of a target


def _log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


# ------------------------------------------------------------------------------------------------ freeze
def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout


def head_text(doc: Path = DOC) -> str:
    out: list[str] = []
    for line in doc.read_text().splitlines(keepends=True):
        if line.rstrip("\n") == MARKER:
            break
        out.append(line)
    return "".join(out)


def frozen_sha(doc: Path = DOC) -> str:
    return hashlib.sha256(head_text(doc).encode()).hexdigest()


def freeze_commit(sha: str, git: Callable[..., str] = _git) -> str | None:
    for line in git("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        if subj.strip() == f"prereg({ID}): {sha}":
            return h
    return None


def require_frozen(sha: str | None, doc: Path | None = None, git: Callable[..., str] = _git) -> str:
    doc = doc or DOC
    if not sha or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise RuntimeError("--sha <64 hex sha256 of the committed rule> is required")
    if MARKER not in doc.read_text().splitlines():
        raise RuntimeError(f"{doc} has no `{MARKER}` line: not a freezable rule")
    got = frozen_sha(doc)
    if got != sha:
        raise RuntimeError(
            f"{doc} text above the marker hashes to {got[:8]}, not {sha[:8]}: not the frozen rule"
        )
    if freeze_commit(sha, git) is None:
        raise RuntimeError(
            f"no commit `prereg({ID}): {sha}` in git log: the rule is not frozen; no arm may be fitted"
        )
    return sha


@contextlib.contextmanager
def heavy_lock(path: Path = LOCK, wait_s: int = 600, retry_s: int = 120) -> Iterator[None]:
    """mkdir lock with a pid file; retries every 120 s; removes ONLY a lock this process created."""
    t0 = time.time()
    while True:
        try:
            path.mkdir(parents=True)
            break
        except FileExistsError:
            if time.time() - t0 > wait_s:
                raise RuntimeError(f"{path} is held by someone else; not removed") from None
            _log(f"{path} held; retry in {retry_s}s")
            time.sleep(retry_s)
    (path / "pid").write_text(str(os.getpid()))
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            (path / "pid").unlink()
            path.rmdir()


# ------------------------------------------------------------------------------------------------ as-of features
def rolling_prior(
    vals: np.ndarray, window: int = 20, min_n: int = 5, rng: np.random.Generator | None = None
) -> np.ndarray:
    """Mean of the previous ``window`` values (shift 1), NaN unless >= min_n non-null in the window.
    With ``rng`` (past-only placebo) the window is a random draw WITHOUT replacement from ALL prior non-null values: same marginal, no recency."""
    n = len(vals)
    out = np.full(n, np.nan)
    for i in range(min_n, n):
        if rng is None:
            w = vals[max(0, i - window) : i]
            w = w[~np.isnan(w)]
            if len(w) >= min_n:
                out[i] = w.mean()
        else:
            pool = vals[:i]
            pool = pool[~np.isnan(pool)]
            if len(pool) >= min_n:
                out[i] = pool[
                    rng.choice(len(pool), size=min(window, len(pool)), replace=False)
                ].mean()
    return out


def build_frame(base: pl.DataFrame, placebo_seed: int | None = None) -> pl.DataFrame:
    """base: game_id, game_date, season, home_team, away_team, home_pts, away_pts, n_poss, maxp (+ anything else, kept). Adds as-of pace/off/def
    per side (rolling-20, min 5, shift 1 over the team's whole history), total, poss, ppp_avg, ppp_diff, b2b, month."""
    g = base.sort(["game_date", "game_id"])
    f = lambda c: g[c].cast(pl.Float64).to_numpy()  # noqa: E731
    npos, maxp, hp, ap = f("n_poss"), f("maxp"), f("home_pts"), f("away_pts")
    mins = 48.0 + 5.0 * np.clip(maxp - 4, 0, 10)
    pt = npos / 2.0
    pace = pt * 48.0 / mins
    off_h, def_h = (
        hp / pt * 100.0,
        ap / pt * 100.0,
    )  # home: points for / against per 100 possessions
    n = g.height
    ht, at = g["home_team"].to_numpy(), g["away_team"].to_numpy()
    dates = g["game_date"].to_numpy().astype("datetime64[D]")
    feat = {k: np.full(n, np.nan) for k in FEATS}
    rest_prev = {s: np.full(n, np.datetime64("NaT"), dtype="datetime64[D]") for s in ("h", "a")}
    rng = np.random.default_rng(placebo_seed) if placebo_seed is not None else None
    teams = np.unique(np.concatenate([ht, at]))
    for t in teams:
        idx_h, idx_a = np.where(ht == t)[0], np.where(at == t)[0]
        idx = np.concatenate([idx_h, idx_a])
        side = np.concatenate([np.zeros(len(idx_h), int), np.ones(len(idx_a), int)])
        order = np.argsort(idx, kind="stable")  # g is already sorted by (date, game_id)
        idx, side = idx[order], side[order]
        p = pace[idx]
        o = np.where(side == 0, off_h[idx], def_h[idx])
        d = np.where(side == 0, def_h[idx], off_h[idx])
        rp, ro, rd = (rolling_prior(v, rng=rng) for v in (p, o, d))
        for s, tag in ((0, "h"), (1, "a")):
            m = side == s
            feat[f"pace_{tag}"][idx[m]] = rp[m]
            feat[f"off_{tag}"][idx[m]] = ro[m]
            feat[f"def_{tag}"][idx[m]] = rd[m]
            prev = np.concatenate([[np.datetime64("NaT")], dates[idx][:-1]])
            rest_prev[tag][idx[m]] = prev[m]
    b2b = np.zeros(n, bool)
    for tag in ("h", "a"):
        pv = rest_prev[tag]
        ok = ~np.isnat(pv)
        b2b |= ok & ((dates - pv) == np.timedelta64(1, "D"))
    ya = hp + ap
    gm = g["game_date"].dt
    return g.with_columns(
        *(pl.Series(k, v) for k, v in feat.items()),
        pl.Series("total", ya),
        pl.Series("poss", npos),
        pl.Series("ppp_avg", ya / npos * 100.0),
        pl.Series("ppp_diff", (hp - ap) / pt * 100.0),
        pl.Series("b2b", b2b),
        (gm.year().cast(pl.Int64) * 12 + gm.month().cast(pl.Int64) - 1).alias("month"),
    ).with_columns(pl.col(c).fill_nan(None) for c in FEATS)


def shuffle_features(df: pl.DataFrame, seed: int) -> pl.DataFrame:
    """Non-inert shuffle: one joint permutation of the six as-of columns across games within each calendar month."""
    rng = np.random.default_rng(seed)
    month = df["month"].to_numpy()
    idx = np.arange(df.height)
    for m in np.unique(month):
        w = np.where(month == m)[0]
        idx[w] = w[rng.permutation(len(w))]
    return df.with_columns(pl.col(c).gather(pl.Series(idx)).alias(c) for c in FEATS)


# ------------------------------------------------------------------------------------------------ distributions and scores
def x_of(df: dict[str, np.ndarray], lg: float, hca: float) -> np.ndarray:
    pi = (df["pace_h"] + df["pace_a"]) / 2.0
    ppp_h = df["off_h"] + df["def_a"] - lg + hca / 2.0  # lg + (off_h - lg) + (def_a - lg) + hca/2
    ppp_a = df["off_a"] + df["def_h"] - lg - hca / 2.0
    return pi * (ppp_h + ppp_a) / 100.0


def support(y: np.ndarray, line: np.ndarray) -> tuple[int, int]:
    return int(np.floor(min(y.min(), line.min()))) - PAD, int(
        np.ceil(max(y.max(), line.max()))
    ) + PAD


def crps_int(y: np.ndarray, mu: np.ndarray, sig: np.ndarray, lo: int, hi: int) -> np.ndarray:
    """Integer-grid CRPS = sum_k (F(k) - 1[y <= k])^2 for the continuity-corrected Normal; F(k) = Phi((k + 0.5 - mu)/sigma)."""
    k = np.arange(lo, hi + 1, dtype=float)[None, :]
    f = ndtr((k + 0.5 - mu[:, None]) / sig[:, None])
    return ((f - (k >= y[:, None])) ** 2).sum(axis=1)


def p_over_at(point: np.ndarray, mu: np.ndarray, sig: np.ndarray) -> np.ndarray:
    """P(Y > point) / (P(Y > point) + P(Y < point)): pushes (integer points) are conditioned away."""
    fl, ce = np.floor(point), np.ceil(point)
    up = 1.0 - ndtr((fl + 0.5 - mu) / sig)
    dn = ndtr((ce - 1.0 + 0.5 - mu) / sig)
    return up / (up + dn)


def walk_forward(
    df: pl.DataFrame,
    ycol: str,
    linecol: str,
    min_prior: int = MIN_PRIOR,
    keep: tuple[str, ...] = (),
) -> tuple[pl.DataFrame, list[tuple[int, int]]]:
    """Monthly walk-forward. For month X fit on valid games of months strictly before X (>= min_prior, else skipped and listed)."""
    cols = {
        c: df[c].cast(pl.Float64).to_numpy() for c in [*FEATS, ycol, linecol, "ppp_avg", "ppp_diff"]
    }
    y, line = cols[ycol], cols[linecol]
    month = df["month"].to_numpy()
    valid = np.all([np.isfinite(v) for v in cols.values()], axis=0)
    lo, hi = support(y[valid], line[valid]) if valid.any() else (0, 1)
    n = df.height
    res = {k: np.full(n, np.nan) for k in ("mu_P", "sig_P", "sig_M", "w", "mu_B", "sig_B")}
    scored = np.zeros(n, bool)
    skipped: list[tuple[int, int]] = []
    for m in np.unique(month[valid]):
        cur, prior = valid & (month == m), valid & (month < m)
        if prior.sum() < min_prior:
            skipped.append((int(m), int(cur.sum())))
            continue
        lg, hca = cols["ppp_avg"][prior].mean(), cols["ppp_diff"][prior].mean()
        x = x_of({k: cols[k] for k in FEATS}, lg, hca)
        a = np.column_stack([np.ones(prior.sum()), x[prior]])
        coef, *_ = np.linalg.lstsq(a, y[prior], rcond=None)
        mu_all = coef[0] + coef[1] * x
        sig_p = float(np.std(y[prior] - mu_all[prior], ddof=2))
        sig_m = float(np.std(y[prior] - line[prior], ddof=1))
        best_w, best_c, best_s = 0.0, np.inf, sig_m
        for w in W_GRID:
            mean_w = line[prior] + w * (mu_all[prior] - line[prior])
            s = max(float(np.std(y[prior] - mean_w, ddof=1)), 1.0)
            c = float(crps_int(y[prior], mean_w, np.full(prior.sum(), s), lo, hi).mean())
            if c < best_c - 1e-12:
                best_w, best_c, best_s = float(w), c, s
        res["mu_P"][cur], res["sig_P"][cur], res["sig_M"][cur] = (
            mu_all[cur],
            max(sig_p, 1.0),
            max(sig_m, 1.0),
        )
        res["w"][cur], res["sig_B"][cur] = best_w, best_s
        res["mu_B"][cur] = line[cur] + best_w * (mu_all[cur] - line[cur])
        scored |= cur
    out = df.filter(pl.Series(scored)).select("game_id", "season", "month", *keep)
    sc = {k: v[scored] for k, v in res.items()}
    out = out.with_columns(
        pl.Series("y", y[scored]),
        pl.Series("line", line[scored]),
        *(pl.Series(k, v) for k, v in sc.items()),
    )
    out = out.with_columns(
        pl.Series("lo", np.full(out.height, lo)), pl.Series("hi", np.full(out.height, hi))
    )
    return out, skipped


def score_arms(r: pl.DataFrame) -> dict[str, np.ndarray]:
    y, line = r["y"].to_numpy(), r["line"].to_numpy()
    lo, hi = int(r["lo"][0]), int(r["hi"][0])
    arms = {
        "P": (r["mu_P"], r["sig_P"]),
        "M": (r["line"], r["sig_M"]),
        "B": (r["mu_B"], r["sig_B"]),
    }
    out: dict[str, np.ndarray] = {}
    keep = y != line
    for a, (mu, sg) in arms.items():
        mu, sg = mu.to_numpy(), sg.to_numpy()
        out[f"crps_{a}"] = crps_int(y, mu, sg, lo, hi)
        p = np.clip(p_over_at(line, mu, sg), 1e-6, 1 - 1e-6)
        over = (y > line).astype(float)
        ll = -(over * np.log(p) + (1 - over) * np.log(1 - p))
        out[f"ll_{a}"] = np.where(keep, ll, np.nan)
    return out


# ------------------------------------------------------------------------------------------------ bootstrap
def boot_draws(
    gids: np.ndarray,
    nums: dict[str, np.ndarray],
    fn: Callable[[dict[str, np.ndarray]], np.ndarray],
    seed: int = SEED,
    n_boot: int = N_BOOT,
) -> np.ndarray:
    """Game-clustered bootstrap: per-game sums, multinomial resample of games, ``fn`` on the resampled sums (vectorised over draws)."""
    u, inv = np.unique(gids, return_inverse=True)
    s = {
        k: np.bincount(inv, weights=np.nan_to_num(np.asarray(v, float)), minlength=len(u))
        for k, v in nums.items()
    }
    pk = np.random.default_rng(seed).integers(0, len(u), (n_boot, len(u)))
    with np.errstate(divide="ignore", invalid="ignore"):
        return fn({k: v[pk].sum(axis=1) for k, v in s.items()})


def ci(draws: np.ndarray) -> list[float]:
    return [float(np.nanquantile(draws, 0.025)), float(np.nanquantile(draws, 0.975))]


def diff_stat(d: np.ndarray, g: np.ndarray) -> dict[str, Any]:
    ok = ~np.isnan(d)
    d, g = d[ok], g[ok]
    dr = boot_draws(g, {"s": d, "n": np.ones(len(d))}, lambda s: s["s"] / s["n"])
    return {
        "mean": float(d.mean()),
        "ci": ci(dr),
        "n": int(len(d)),
        "p_one_sided": float((np.sum(dr >= 0) + 1) / (len(dr) + 1)),
    }


def to_dec(price: np.ndarray) -> np.ndarray:
    return np.where(price > 0, 1.0 + price / 100.0, 1.0 + 100.0 / -price)


def decision_score(r: pl.DataFrame) -> dict[str, Any]:
    """Bet the larger-EV side of the DraftKings T-60 total at B's probability, EV >= 5%, one unit; compare with the market-lean side for the same count."""
    d = r.filter(
        pl.col("dk_point").is_not_null()
        & pl.col("dk_po").is_not_null()
        & pl.col("dk_pu").is_not_null()
        & (pl.col("y") != pl.col("dk_point"))
    )
    if d.height == 0:
        return {"n_bets": 0, "status": "insufficient sample", "roi": None}
    pt, y = d["dk_point"].to_numpy(), d["y"].to_numpy()
    do, du = (
        to_dec(d["dk_po"].to_numpy().astype(float)),
        to_dec(d["dk_pu"].to_numpy().astype(float)),
    )
    p = p_over_at(pt, d["mu_B"].to_numpy(), d["sig_B"].to_numpy())
    ev_o, ev_u = p * do - 1.0, (1.0 - p) * du - 1.0
    over_side = ev_o >= ev_u
    ev = np.where(over_side, ev_o, ev_u)
    bet = ev >= EV_MIN
    won_over = y > pt
    prof_b = np.where(
        over_side, np.where(won_over, do - 1, -1.0), np.where(~won_over, du - 1, -1.0)
    )
    prof_b = np.where(bet, prof_b, 0.0)
    io, iu = 1 / do, 1 / du
    pm = io / (io + iu)  # de-vigged market P(over)
    lean_ok = np.abs(pm - 0.5) > 1e-9
    nb = int(bet.sum())
    key = np.where(lean_ok, -np.abs(pm - 0.5), np.inf)
    sel = np.zeros(len(d), bool)
    sel[np.argsort(key, kind="stable")[:nb]] = True
    sel &= lean_ok
    lean_over = pm > 0.5
    prof_l = np.where(
        lean_over, np.where(won_over, do - 1, -1.0), np.where(~won_over, du - 1, -1.0)
    )
    prof_l = np.where(sel, prof_l, 0.0)
    g = d["game_id"].to_numpy()
    nums = {"pb": prof_b, "nb": bet.astype(float), "pl": prof_l, "nl": sel.astype(float)}
    dr_b = boot_draws(g, nums, lambda s: s["pb"] / s["nb"])
    dr_d = boot_draws(g, nums, lambda s: s["pb"] / s["nb"] - s["pl"] / s["nl"])
    roi = float(prof_b.sum() / max(nb, 1))
    return {
        "n_eligible": int(d.height),
        "n_bets": nb,
        "status": "ok" if nb >= MIN_BETS else "insufficient sample",
        "roi": roi if nb else None,
        "roi_ci": ci(dr_b) if nb else None,
        "hit_rate": float(np.mean(np.where(over_side, won_over, ~won_over)[bet])) if nb else None,
        "lean_n": int(sel.sum()),
        "lean_roi": float(prof_l.sum() / max(int(sel.sum()), 1)),
        "roi_minus_lean": float(roi - prof_l.sum() / max(int(sel.sum()), 1)) if nb else None,
        "roi_minus_lean_ci": ci(dr_d) if nb else None,
        "p_roi_le_0": float((np.sum(dr_b <= 0) + 1) / (len(dr_b) + 1)) if nb else 1.0,
        "p_diff_le_0": float((np.sum(dr_d <= 0) + 1) / (len(dr_d) + 1)) if nb else 1.0,
    }


def season_report(r: pl.DataFrame, season: int) -> dict[str, Any]:
    s = r.filter(pl.col("season") == season)
    if s.height == 0:
        return {"n": 0}
    sc = score_arms(s)
    g = s["game_id"].to_numpy()
    return {
        "n": int(s.height),
        "w_mean": float(s["w"].mean()),
        "crps": {a: float(sc[f"crps_{a}"].mean()) for a in "PMB"},
        "crps_B_minus_M": diff_stat(sc["crps_B"] - sc["crps_M"], g),
        "crps_P_minus_M": diff_stat(sc["crps_P"] - sc["crps_M"], g),
        "logloss_B_minus_M": diff_stat(sc["ll_B"] - sc["ll_M"], g),
        "logloss": {a: float(np.nanmean(sc[f"ll_{a}"])) for a in "PMB"},
        "decision": decision_score(s),
    }


# ------------------------------------------------------------------------------------------------ verdict
def primary_pass(rep: dict[str, Any]) -> dict[str, Any]:
    c, lg_, d = rep["crps_B_minus_M"], rep["logloss_B_minus_M"], rep["decision"]
    p_dec = (
        max(d.get("p_roi_le_0", 1.0), d.get("p_diff_le_0", 1.0))
        if d.get("n_bets", 0) >= MIN_BETS
        else 1.0
    )
    adj = bh_adjust(np.array([c["p_one_sided"], lg_["p_one_sided"], p_dec]))
    ok_c = c["mean"] <= FLOOR_CRPS and c["ci"][1] < 0 and adj[0] < 0.05
    ok_l = lg_["mean"] <= FLOOR_LL and lg_["ci"][1] < 0 and adj[1] < 0.05
    ok_d = bool(
        d.get("n_bets", 0) >= MIN_BETS
        and d["roi"] >= FLOOR_ROI
        and d["roi_ci"][0] > 0
        and d["roi_minus_lean_ci"][0] > 0
        and adj[2] < 0.05
    )
    return {
        "crps": bool(ok_c),
        "logloss": bool(ok_l),
        "decision": ok_d,
        "bh_adjusted_p": [float(a) for a in adj],
        "p_raw": [c["p_one_sided"], lg_["p_one_sided"], p_dec],
    }


def select_sign(rep: dict[str, Any]) -> bool:
    d = rep.get("decision", {})
    return bool(
        rep.get("n", 0) > 0
        and rep["crps_B_minus_M"]["mean"] < 0
        and rep["logloss_B_minus_M"]["mean"] < 0
        and d.get("roi") is not None
        and d["roi"] >= 0
    )


def verdict(prim: dict[str, Any], sel_ok: bool, guards_clean: bool) -> str:
    if not prim["crps"]:
        return "fails_crps"
    if not prim["logloss"]:
        return "fails_logloss"
    if not prim["decision"]:
        return "fails_decision_guard"
    if not (sel_ok and guards_clean):
        return "fails_guard"
    return "passes"


# ------------------------------------------------------------------------------------------------ pipeline and guards
def run_pipeline(
    base: pl.DataFrame,
    placebo_seed: int | None = None,
    shuffle_seed: int | None = None,
    min_prior: int = MIN_PRIOR,
) -> tuple[pl.DataFrame, pl.DataFrame, list[tuple[int, int]]]:
    fr = build_frame(base, placebo_seed)
    if shuffle_seed is not None:
        fr = shuffle_features(fr, shuffle_seed)
    keep = tuple(c for c in ("dk_point", "dk_po", "dk_pu", "fallback", "b2b") if c in fr.columns)
    res, skipped = walk_forward(
        fr.with_columns(pl.col("total").alias("y_total")), "y_total", "line", min_prior, keep
    )
    return fr, res, skipped


def planted_future(base: pl.DataFrame, cut: Any, min_prior: int = MIN_PRIOR) -> dict[str, Any]:
    """Inflate scores and possessions from ``cut`` (first of a month) on; everything for earlier games must be unchanged, later ones must move."""
    infl = base.with_columns(
        *(
            pl.when(pl.col("game_date") >= cut).then(pl.col(c) + k).otherwise(pl.col(c)).alias(c)
            for c, k in (("home_pts", 100), ("away_pts", 100), ("n_poss", 60))
        )
    )
    f0, r0, _ = run_pipeline(base, min_prior=min_prior)
    f1, r1, _ = run_pipeline(infl, min_prior=min_prior)
    before = f0["game_date"] < cut
    diffs = []
    for c in (*FEATS, "b2b"):
        a, b = (
            f0.filter(before)[c].cast(pl.Float64).fill_null(-1e18).to_numpy(),
            f1.filter(before)[c].cast(pl.Float64).fill_null(-1e18).to_numpy(),
        )
        diffs.append(float(np.max(np.abs(a - b))) if len(a) else 0.0)
    fd = max(diffs)
    j = r0.join(r1, on="game_id", suffix="_p", how="full")
    jb = j.filter(pl.col("game_id").is_in(f0.filter(before)["game_id"]))
    fc = 0.0
    for c in ("mu_P", "sig_P", "sig_M", "w", "mu_B", "sig_B"):
        a, b = jb[c].fill_null(-1e18).to_numpy(), jb[f"{c}_p"].fill_null(-1e18).to_numpy()
        fc = max(fc, float(np.max(np.abs(a - b))) if len(a) else 0.0)
    after = f0.filter(~before)
    moved_feat = int(
        np.sum(
            np.abs(
                f0.filter(~before)["pace_h"].fill_null(0).to_numpy()
                - f1.filter(~before)["pace_h"].fill_null(0).to_numpy()
            )
            > 0
        )
    )
    ja = j.filter(
        pl.col("game_id").is_in(after["game_id"])
        & pl.col("mu_P").is_not_null()
        & pl.col("mu_P_p").is_not_null()
    )
    moved_fc = int((np.abs(ja["mu_P"].to_numpy() - ja["mu_P_p"].to_numpy()) > 0).sum())
    return {
        "max_feature_diff_before": fd,
        "max_forecast_diff_before": fc,
        "n_before": int(before.sum()),
        "later_feature_rows_moved": moved_feat,
        "later_forecasts_moved": moved_fc,
        "pass": bool(fd == 0.0 and fc == 0.0 and moved_feat > 0 and moved_fc > 0),
    }


def guard_placebo_shuffle(
    base: pl.DataFrame, rep_main: dict[str, Any], season: int, min_prior: int = MIN_PRIOR
) -> dict[str, Any]:
    _, rp, _ = run_pipeline(base, placebo_seed=SEED, min_prior=min_prior)
    _, rs, _ = run_pipeline(base, shuffle_seed=SEED, min_prior=min_prior)
    pl_rep, sh_rep = season_report(rp, season), season_report(rs, season)
    out: dict[str, Any] = {"placebo": pl_rep, "shuffle": sh_rep}
    if pl_rep.get("n", 0) == 0 or sh_rep.get("n", 0) == 0:
        out["pass"] = False
        return out
    placebo_ok = pl_rep["crps_B_minus_M"]["mean"] > FLOOR_CRPS
    worse = sh_rep["crps"]["P"] - rep_main["crps"]["P"]
    shuffle_ok = worse >= SHUFFLE_WORSE and sh_rep["crps_B_minus_M"]["mean"] > FLOOR_CRPS
    out |= {
        "placebo_pass": bool(placebo_ok),
        "shuffle_P_worse_by": float(worse),
        "shuffle_pass": bool(shuffle_ok),
        "pass": bool(placebo_ok and shuffle_ok),
    }
    return out


def missingness(
    base: pl.DataFrame, fr: pl.DataFrame, res: pl.DataFrame, skipped: list[tuple[int, int]]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    sk_month = {m for m, _ in skipped}
    f = fr.with_columns(pl.col("month").is_in(list(sk_month)).alias("_burn"))
    feat_null = pl.any_horizontal(*(pl.col(c).is_null() for c in FEATS))
    sc = set(res["game_id"].to_list())
    for s in (SELECT_SEASON, REPORT_SEASON):
        x = f.filter(pl.col("season") == s)
        out[str(s)] = {
            "games": x.height,
            "no_possessions": int(x["n_poss"].is_null().sum()),
            "no_asof_features": int(x.filter(feat_null).height),
            "no_line": int(x["line"].is_null().sum()),
            "burn_in_skipped": int(
                x.filter(pl.col("_burn") & pl.col("line").is_not_null() & ~feat_null).height
            ),
            "scored": int(x.filter(pl.col("game_id").is_in(sc)).height),
        }
    r = res.filter(pl.col("season") == REPORT_SEASON)
    flag = False
    if r.height and "fallback" in r.columns:
        sc_ = score_arms(r)
        d = sc_["crps_B"] - sc_["crps_M"]
        fb = r["fallback"].fill_null(True).to_numpy()
        tot = d.sum()
        cls = {}
        for name, m in (("pinnacle", ~fb), ("consensus_fallback", fb)):
            cls[name] = {
                "n": int(m.sum()),
                "mean": float(d[m].mean()) if m.any() else None,
                "sum": float(d[m].sum()),
            }
        for a, b in (("pinnacle", "consensus_fallback"), ("consensus_fallback", "pinnacle")):
            if (
                tot < 0
                and cls[a]["sum"] <= 0.9 * tot
                and cls[b]["n"] >= 100
                and cls[b]["mean"] is not None
                and cls[b]["mean"] >= 0
            ):
                flag = True
        out["gain_by_line_source_2024"] = cls
    out["concentration_flag"] = flag
    return out


def slices(r: pl.DataFrame, fr: pl.DataFrame) -> dict[str, Any]:
    s = r.filter(pl.col("season") == REPORT_SEASON).join(
        fr.select("game_id", "game_date", "pace_h", "pace_a"), on="game_id", how="left"
    )
    if s.height == 0:
        return {}
    sc = score_arms(s)
    dc, dl = sc["crps_B"] - sc["crps_M"], sc["ll_B"] - sc["ll_M"]
    g = s["game_id"].to_numpy()
    pi = ((s["pace_h"] + s["pace_a"]) / 2).to_numpy()
    line = s["line"].to_numpy()
    med_date = s["game_date"].sort()[s.height // 2]
    masks: dict[str, np.ndarray] = {
        "season_first_half": (s["game_date"] <= med_date).to_numpy(),
        "season_second_half": (s["game_date"] > med_date).to_numpy(),
    }
    for nm, v in (("pace", pi), ("line", line)):
        q1, q2 = np.quantile(v, [1 / 3, 2 / 3])
        masks |= {
            f"{nm}_tercile_low": v <= q1,
            f"{nm}_tercile_mid": (v > q1) & (v <= q2),
            f"{nm}_tercile_high": v > q2,
        }
    if "b2b" in s.columns:
        b = s["b2b"].fill_null(False).to_numpy()
        masks |= {"b2b_either": b, "no_b2b": ~b}
    if "fallback" in s.columns:
        fb = s["fallback"].fill_null(True).to_numpy()
        masks |= {"pinnacle_line": ~fb, "consensus_fallback_line": fb}
    return {
        k: {
            "crps_B_minus_M": diff_stat(dc[m], g[m]) if m.sum() > 20 else None,
            "logloss_B_minus_M": diff_stat(dl[m], g[m]) if m.sum() > 20 else None,
            "n": int(m.sum()),
        }
        for k, m in masks.items()
    }


def period_totals(fr: pl.DataFrame, min_prior: int = MIN_PRIOR) -> dict[str, Any]:
    out: dict[str, Any] = {}
    ps = []
    for t in PER:
        if f"t_{t}" not in fr.columns or f"line_{t}" not in fr.columns:
            continue
        res, _ = walk_forward(fr, f"t_{t}", f"line_{t}", min_prior)
        rep = season_report(
            res.with_columns(
                pl.lit(None, pl.Float64).alias("dk_point"),
                pl.lit(None, pl.Float64).alias("dk_po"),
                pl.lit(None, pl.Float64).alias("dk_pu"),
            ),
            REPORT_SEASON,
        )
        out[t] = {
            "n": rep.get("n", 0),
            "w_mean": rep.get("w_mean"),
            "crps_B_minus_M": rep.get("crps_B_minus_M"),
            "crps": rep.get("crps"),
        }
        ps.append((t, rep["crps_B_minus_M"]["p_one_sided"] if rep.get("n") else 1.0))
    if ps:
        adj = bh_adjust(np.array([p for _, p in ps]))
        for (t, _), a in zip(ps, adj, strict=True):
            out[t]["bh_adjusted_p"] = float(a)
        sup = [
            t
            for (t, _), a in zip(ps, adj, strict=True)
            if a < 0.05 and out[t]["crps_B_minus_M"]["ci"][1] < 0
        ]
        out["descriptive_support"] = sup
    return out


def run_all(base: pl.DataFrame, min_prior: int = MIN_PRIOR, cut: Any = None) -> dict[str, Any]:
    fr, res, skipped = run_pipeline(base, min_prior=min_prior)
    _log(f"main walk-forward: scored {res.height}, burn-in months skipped {skipped}")
    sel, rep = season_report(res, SELECT_SEASON), season_report(res, REPORT_SEASON)
    prim = (
        primary_pass(rep) if rep.get("n") else {"crps": False, "logloss": False, "decision": False}
    )
    out: dict[str, Any] = {
        "rule": ID,
        "select_2023": sel,
        "report_2024": rep,
        "primary": prim,
        "skipped_months": skipped,
    }
    _log(f"primary {prim}")
    gm = missingness(base, fr, res, skipped)
    out["missingness"] = gm
    out["slices_2024"] = slices(res, fr)
    out["planted_future"] = (
        planted_future(base, cut, min_prior)
        if cut is not None
        else {"pass": False, "note": "no cut date"}
    )
    _log(f"planted-future {out['planted_future']}")
    out["placebo_shuffle"] = (
        guard_placebo_shuffle(base, rep, REPORT_SEASON, min_prior)
        if rep.get("n")
        else {"pass": False}
    )
    out["periods"] = period_totals(fr, min_prior)
    guards = bool(
        out["planted_future"]["pass"]
        and out["placebo_shuffle"]["pass"]
        and not gm["concentration_flag"]
    )
    out["guards_clean"] = guards
    out["select_sign_ok"] = select_sign(sel)
    out["verdict"] = verdict(prim, out["select_sign_ok"], guards) if rep.get("n") else "fails_guard"
    if out["verdict"] != "passes":
        out["headline"] = (
            "the_line_already_has_the_pace / defer to the market"
            if out["verdict"] != "fails_guard"
            else "guard failed; no claim"
        )
    return out


# ------------------------------------------------------------------------------------------------ real data
def load_base() -> pl.DataFrame:
    import duckdb

    from research.eval import confusion_report as CR
    from research.eval import pace_periods_b as PB

    con = duckdb.connect(str(ROOT / "nba.duckdb"), read_only=True)
    g = con.execute(
        "select game_id, game_date, season, home_team, away_team, home_pts, away_pts from games where season <= 2024"
    ).pl()
    p = con.execute(
        "select p.game_id, count(*) n_poss, max(p.period) maxp from possessions p join games g using(game_id) where g.season <= 2024 group by 1"
    ).pl()
    con.close()
    assert g["season"].max() <= MAX_SEASON
    base = g.join(p, on="game_id", how="left")
    mk = PB.totals_market("totals").select("game_id", "line", "p_over_mkt", "fallback", "n_books")
    base = base.join(mk, on="game_id", how="left").join(dk_totals(), on="game_id", how="left")
    pb = pl.read_parquet(CR.PERIODS_CACHE)
    ok = (
        pl.col("h_fin").is_not_null()
        & (pl.col("h_fin") == pl.col("home_pts"))
        & (pl.col("a_fin") == pl.col("away_pts"))
    )
    base = base.join(pb, on="game_id", how="left", suffix="_pb").with_columns(ok.alias("pbp_ok"))
    for q in range(1, 5):
        prev = (pl.col(f"h_c{q - 1}") + pl.col(f"a_c{q - 1}")) if q > 1 else pl.lit(0)
        base = base.with_columns(
            pl.when(pl.col("pbp_ok"))
            .then(pl.col(f"h_c{q}") + pl.col(f"a_c{q}") - prev)
            .alias(f"t_q{q}")
        )
    base = base.with_columns(
        (pl.col("t_q1") + pl.col("t_q2")).alias("t_h1"),
        (pl.col("t_q3") + pl.col("t_q4")).alias("t_h2"),
    )
    for t in PER:
        m = PB.totals_market(f"totals_{t}").select("game_id", pl.col("line").alias(f"line_{t}"))
        base = base.join(m, on="game_id", how="left")
    assert base["season"].max() <= MAX_SEASON
    return base


def dk_totals() -> pl.DataFrame:
    """DraftKings T-60 full-game total: the over/under pair whose de-vigged price is closest to even (the main line)."""
    import duckdb

    o = duckdb.connect(str(ROOT / "data/odds/odds_history.duckdb"), read_only=True)
    d = o.execute(
        "select game_id, side, point, price_american from odds_history where market='totals' and snapshot_kind='t60' and book='draftkings' and season<=2024 and side in ('over','under')"
    ).pl()
    o.close()
    ov = d.filter(pl.col("side") == "over").select(
        "game_id", "point", pl.col("price_american").alias("dk_po")
    )
    un = d.filter(pl.col("side") == "under").select(
        "game_id", "point", pl.col("price_american").alias("dk_pu")
    )
    j = ov.join(un, on=["game_id", "point"])

    def imp(c: str) -> pl.Expr:
        return (
            pl.when(pl.col(c) < 0)
            .then(-pl.col(c) / (-pl.col(c) + 100.0))
            .otherwise(100.0 / (pl.col(c) + 100.0))
        )

    j = (
        j.with_columns(((imp("dk_po") / (imp("dk_po") + imp("dk_pu"))) - 0.5).abs().alias("dist"))
        .sort("dist")
        .group_by("game_id", maintain_order=True)
        .first()
    )
    return j.select("game_id", pl.col("point").alias("dk_point"), "dk_po", "dk_pu")


def write_report(out: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "results.json").write_text(json.dumps(out, indent=1, default=float))
    r, s = out["report_2024"], out["select_2023"]
    lines = [
        f"# PACE_TOTALS result (verdict: {out['verdict']})",
        "",
        f"n 2024 scored = {r.get('n')}, n 2023 scored = {s.get('n')}",
        "",
    ]
    for nm, rep in (("2023 (select)", s), ("2024 (report)", r)):
        if rep.get("n"):
            lines.append(
                f"- {nm}: CRPS B-M {rep['crps_B_minus_M']['mean']:.4f} CI {rep['crps_B_minus_M']['ci']}; logloss B-M {rep['logloss_B_minus_M']['mean']:.5f} CI {rep['logloss_B_minus_M']['ci']}; decision {rep['decision'].get('status')} n_bets {rep['decision'].get('n_bets')} ROI {rep['decision'].get('roi')}"
            )
    lines += [
        "",
        f"primary: {out['primary']}",
        f"guards_clean: {out['guards_clean']}; select_sign_ok: {out['select_sign_ok']}",
        f"periods descriptive_support: {out['periods'].get('descriptive_support')}",
    ]
    (ROOT / "reports/prereg_pace_totals.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["selftest", "check", "run"])
    ap.add_argument("--sha")
    a = ap.parse_args()
    if a.cmd == "selftest":
        from tests.eval.test_pace_totals_eval import synth_base

        b = synth_base()
        out = run_all(b, min_prior=150, cut=__import__("datetime").date(2024, 2, 1))
        print(
            json.dumps({k: out[k] for k in ("verdict", "guards_clean", "primary")}, default=float)
        )
        return
    require_frozen(a.sha)
    if a.cmd == "check":
        b = load_base()
        print(
            b.group_by("season")
            .agg(
                pl.len(),
                pl.col("line").is_not_null().sum().alias("line"),
                pl.col("n_poss").is_not_null().sum().alias("poss"),
                pl.col("dk_point").is_not_null().sum().alias("dk"),
            )
            .sort("season")
        )
        return
    with heavy_lock():
        t0 = time.time()
        b = load_base()
        _log(f"loaded {b.height} games")
        out = run_all(b, cut=__import__("datetime").date(2024, 2, 1))
        write_report(out)
        _log(f"verdict {out['verdict']} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
