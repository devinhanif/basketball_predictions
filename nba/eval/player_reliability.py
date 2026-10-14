"""Per-player "model reliability" table (DESCRIPTIVE; no claim, no rule, not an edge).

For each (player, stat, season) it reports how well the stored production walk-forward
forecasts (``props_context_residual`` v2 real-tip OOF, 2023 and 2024) fit that player:

* ``skill``: 1 - CRPS(model) / CRPS(recent-form baseline), both on the SAME integer
  support and the SAME 19-point tau grid, same rows (empirical-Bayes shrunk);
* ``coverage``: share of games whose integer outcome falls in the stored 80% interval
  ``[q10, q90]`` (empirical-Bayes shrunk toward the stat's pooled coverage);
* ``minutes_cv``: sd / mean of the player's minutes over the scored games.

Tiers are defined here, in code and in docs/research/player_reliability_2026-10-14.md,
BEFORE any player was inspected (no player name is ever printed or stored):

* low    : n < 30, or shrunk coverage outside [0.70, 0.90];
* high   : n >= 50, shrunk skill in the top third of the (stat, season) cells with n >= 30,
           and shrunk coverage in [0.75, 0.85];
* medium : everything else.

Read-only on the DuckDB (``season <= 2024`` in SQL); never loads season 2025.

    uv run python -m nba.eval.player_reliability --db /path/nba.duckdb
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
from scipy.stats import spearmanr

from nba.eval.context_residual_eval import crps_from_quantiles
from nba.props.context_residual import PROP_STATS, QUANTILE_TAUS, to_integer_support

MAX_SEASON = 2024
SEASONS: tuple[int, ...] = (2023, 2024)
TAUS = np.asarray(QUANTILE_TAUS, dtype=float)  # 0.05 .. 0.95, the stored q_grid
LO_I, HI_I = int(np.argmin(np.abs(TAUS - 0.10))), int(np.argmin(np.abs(TAUS - 0.90)))
BASE_WINDOW = 20  # prior played games feeding the baseline
BASE_HALFLIFE = 10.0  # same half-life as production's recency average
MIN_PRIOR = 5  # production's MIN_PRIOR_PLAYED
#: Neutral-site games restored on 2026-10-11 (docs/DATA_CHANGELOG.md, F2); the stored
#: 2024 OOF (n 27,583 per stat) predates them, so the baseline history excludes them too.
NEUTRAL_RESTORED_2024: tuple[str, ...] = (
    "0022400147",
    "0022400621",
    "0022400633",
    "0022401229",
    "0022401230",
)
# Tier rule (frozen in code before any player was inspected).
N_LOW, N_HIGH = 30, 50
COV_LOW_BAND, COV_HIGH_BAND = (0.70, 0.90), (0.75, 0.85)
TOP_THIRD = 2.0 / 3.0
N_BOOT = 1000
OOF_DEFAULT = (
    "/Users/devin/Downloads/nba-prediction/reports/context_residual_v2_realtip/"
    "oof_context_residual.parquet"
)
DB_DEFAULT = "/Users/devin/Downloads/nba-prediction/nba.duckdb"
TIERS: tuple[str, ...] = ("low", "medium", "high")


# --------------------------------------------------------------------------- baseline


def weighted_pmf_quantiles(
    vals: np.ndarray, weights: np.ndarray, taus: np.ndarray = TAUS
) -> np.ndarray:
    """Quantiles (smallest value with weighted CDF >= tau) of a weighted empirical pmf.

    ``vals`` / ``weights`` are (m, w); ``weights`` is 0 for padding. Integer valued, so
    the baseline is natively on the integer support. Rows with zero total weight -> NaN.
    """
    v = np.where(weights > 0, vals, np.inf)
    order = np.argsort(v, axis=1, kind="stable")
    vs = np.take_along_axis(v, order, axis=1)
    ws = np.take_along_axis(weights, order, axis=1)
    tot = ws.sum(axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        cdf = np.cumsum(ws, axis=1) / tot
    out = np.full((vals.shape[0], len(taus)), np.nan)
    for j, t in enumerate(taus):
        idx = (cdf >= t - 1e-12).argmax(axis=1)
        out[:, j] = vs[np.arange(len(vs)), idx]
    out[(tot[:, 0] <= 0), :] = np.nan
    return out


def _key(dates: pl.Series, gids: pl.Series) -> np.ndarray:
    d = dates.cast(pl.Int32).to_numpy().astype(np.int64)
    g = np.array([int(x) for x in gids.to_list()], dtype=np.int64)
    return np.asarray(d * 10**10 + g)


def baseline_quantiles(
    played: pl.DataFrame, rows: pl.DataFrame, stat: str
) -> tuple[np.ndarray, np.ndarray]:
    """As-of recent-form baseline quantile grid for each row of ``rows``.

    ``played`` = played player-games (player_id, game_id, game_date, <stat>), the history.
    For a row, the window is that player's previous ``BASE_WINDOW`` played games strictly
    before (game_date, game_id), weights ``0.5 ** (k / BASE_HALFLIFE)`` (k = games ago - 1).
    Returns (quantiles (m, 19), n_prior (m,)). Only prior games enter: a planted future
    value changes nothing (test).
    """
    hist = played.sort(["player_id", "game_date", "game_id"])
    pid = hist["player_id"].to_numpy()
    val = hist[stat].to_numpy().astype(float)
    # one sortable integer key per (game_date, game_id)
    hkey = _key(hist["game_date"], hist["game_id"])
    qkey = _key(rows["game_date"], rows["game_id"])
    qpid = rows["player_id"].to_numpy()
    uniq, first = np.unique(pid, return_index=True)
    ends = np.append(first[1:], len(pid))
    span = {int(u): (int(a), int(b)) for u, a, b in zip(uniq, first, ends, strict=True)}
    m = len(rows)
    vals = np.zeros((m, BASE_WINDOW))
    wts = np.zeros((m, BASE_WINDOW))
    n_prior = np.zeros(m, dtype=int)
    wk = 0.5 ** (np.arange(BASE_WINDOW) / BASE_HALFLIFE)
    for i in range(m):
        a_b = span.get(int(qpid[i]))
        if a_b is None:
            continue
        a, b = a_b
        pos = a + int(np.searchsorted(hkey[a:b], qkey[i], side="left"))  # rows < query
        k = min(pos - a, BASE_WINDOW)
        n_prior[i] = pos - a
        if k:
            vals[i, :k] = val[pos - k : pos][::-1]  # most recent first
            wts[i, :k] = wk[:k]
    return weighted_pmf_quantiles(vals, wts), n_prior


# --------------------------------------------------------------------------- metrics


def interval_coverage(y: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """1.0 where the integer outcome lies in the STORED 80% interval ``[q10, q90]``.

    The stored production quantiles are conformal-calibrated for exactly this naive
    event (pooled 0.799/0.801/0.800/0.799 in reports/context_residual_v2_realtip/report.txt).
    A "continuity-corrected" fraction of the unit cell is NOT used: production quantiles are
    clipped at 0 with a point mass there, so cell-splitting would count a certain 0 as half
    covered (first attempt: pooled fg3m coverage 0.63; replaced before any tier was read).
    The integer-rounded interval over-covers (INTEGER_QUANTILES caveat) and is not used."""
    return ((y >= lo) & (y <= hi)).astype(float)


def eb_shrink(
    est: np.ndarray, var: np.ndarray, min_n_mask: np.ndarray
) -> tuple[np.ndarray, float, float]:
    """Normal-normal empirical Bayes (DerSimonian-Laird tau^2). Returns (shrunk, mu, tau2).

    ``est``/``var``: per-cell estimate and its sampling variance; tau^2 and mu are fitted
    on the cells in ``min_n_mask``; every cell is shrunk toward mu with weight
    B = tau2 / (tau2 + var)."""
    e, v = est[min_n_mask], np.maximum(var[min_n_mask], 1e-12)
    w = 1.0 / v
    mu0 = float((w * e).sum() / w.sum())
    q = float((w * (e - mu0) ** 2).sum())
    denom = float(w.sum() - (w**2).sum() / w.sum())
    tau2 = max(0.0, (q - (len(e) - 1)) / denom) if denom > 0 else 0.0
    ws = 1.0 / (v + tau2)
    mu = float((ws * e).sum() / ws.sum())
    b = tau2 / (tau2 + np.maximum(var, 1e-12)) if tau2 > 0 else np.zeros_like(est)
    return mu + b * (est - mu), mu, tau2


def assign_tier(
    n: np.ndarray, skill: np.ndarray, cov: np.ndarray, skill_cut: float, tau2_skill: float
) -> np.ndarray:
    """The frozen tier rule (see module docstring)."""
    low = (n < N_LOW) | (cov < COV_LOW_BAND[0]) | (cov > COV_LOW_BAND[1])
    high = (
        (n >= N_HIGH)
        & (skill >= skill_cut)
        & (tau2_skill > 0)
        & (cov >= COV_HIGH_BAND[0])
        & (cov <= COV_HIGH_BAND[1])
        & ~low
    )
    return np.where(low, "low", np.where(high, "high", "medium"))


def build_cells(df: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Cell table for ONE (stat, season) from per-row frame with columns
    player_id, m_crps, b_crps, cov, minutes."""
    g = (
        df.group_by("player_id")
        .agg(
            pl.len().alias("n"),
            pl.col("m_crps").sum().alias("sm"),
            pl.col("b_crps").sum().alias("sb"),
            pl.col("cov").mean().alias("coverage_raw"),
            pl.col("minutes").mean().alias("min_mean"),
            pl.col("minutes").std().alias("min_sd"),
            pl.col("m_crps").implode().alias("m_list"),
            pl.col("b_crps").implode().alias("b_list"),
        )
        .sort("player_id")
    )
    n = g["n"].to_numpy().astype(float)
    sm, sb = g["sm"].to_numpy(), g["sb"].to_numpy()
    # a baseline with zero CRPS (e.g. a player who never makes a 3) leaves skill undefined:
    # no information, so it is shrunk to the stat mean (infinite sampling variance)
    valid = sb > 1e-9
    ratio = np.where(valid, sm / np.where(valid, sb, 1.0), np.nan)
    skill_raw = 1.0 - ratio
    var_skill = np.full(len(g), np.inf)
    for i, (ml, bl) in enumerate(zip(g["m_list"].to_list(), g["b_list"].to_list(), strict=True)):
        if not valid[i]:
            continue
        mm, bb = np.asarray(ml), np.asarray(bl)
        r = mm - ratio[i] * bb
        k = len(mm)
        var_skill[i] = (k / max(k - 1, 1)) * float((r**2).sum()) / float(bb.sum() ** 2)
    cov_raw = g["coverage_raw"].to_numpy()
    cbar = float(df["cov"].mean())  # type: ignore[arg-type]
    var_cov = cbar * (1.0 - cbar) / n
    fit_mask = n >= 10
    skill_shr, mu_s, tau2_s = eb_shrink(
        np.where(valid, skill_raw, 0.0), var_skill, fit_mask & valid
    )
    cov_shr, mu_c, tau2_c = eb_shrink(cov_raw, var_cov, fit_mask)
    elig = n >= N_LOW
    cut = float(np.quantile(skill_shr[elig], TOP_THIRD)) if elig.any() else float("nan")
    tier = assign_tier(n, skill_shr, cov_shr, cut, tau2_s)
    cv = g["min_sd"].to_numpy() / g["min_mean"].to_numpy()
    out = pl.DataFrame(
        {
            "player_id": g["player_id"].to_numpy(),
            "tier": tier,
            "n": n.astype(int),
            "skill": skill_shr,
            "coverage": cov_shr,
            "minutes_cv": cv,
            "skill_raw": skill_raw,
            "coverage_raw": cov_raw,
        }
    )
    meta: dict[str, Any] = {
        "skill_mu": mu_s,
        "skill_tau": float(np.sqrt(tau2_s)),
        "skill_cut_top_third": cut,
        "cov_mu": mu_c,
        "cov_tau": float(np.sqrt(tau2_c)),
        "pooled_skill": float(1.0 - df["m_crps"].to_numpy().sum() / df["b_crps"].to_numpy().sum()),
        "pooled_cov": cbar,
        "cells": len(g),
        "rows": df.height,
        "mean_shrink_skill": float(np.mean(tau2_s / (tau2_s + var_skill)) if tau2_s > 0 else 0.0),
        "cells_skill_undefined": int((~valid).sum()),
    }
    return out, meta


# --------------------------------------------------------------------------- stability


def kappa(a: np.ndarray, b: np.ndarray) -> float:
    cats = list(TIERS)
    ia, ib = np.array([cats.index(x) for x in a]), np.array([cats.index(x) for x in b])
    po = float((ia == ib).mean())
    pa = np.array([(ia == c).mean() for c in range(3)])
    pb = np.array([(ib == c).mean() for c in range(3)])
    pe = float((pa * pb).sum())
    return (po - pe) / (1.0 - pe) if pe < 1 else float("nan")


def cluster_boot(
    pids: np.ndarray, fn: Any, n_boot: int = N_BOOT, seed: int = 0
) -> tuple[float, float, float]:
    """Player-clustered bootstrap CI of ``fn(index_array)``; point, lo, hi."""
    rng = np.random.default_rng(seed)
    uniq, inv = np.unique(pids, return_inverse=True)
    groups = [np.flatnonzero(inv == i) for i in range(len(uniq))]
    point = fn(np.arange(len(pids)))
    vals = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(uniq), len(uniq))
        idx = np.concatenate([groups[i] for i in pick])
        vals.append(fn(idx))
    arr = np.asarray(vals, dtype=float)
    return float(point), float(np.nanpercentile(arr, 2.5)), float(np.nanpercentile(arr, 97.5))


def _rho(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman rho on the pairs where both values are finite (NaN otherwise)."""
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 10:
        return float("nan")
    return float(spearmanr(x[ok], y[ok])[0])


def stability(cells: pl.DataFrame) -> dict[str, Any]:
    """2023 -> 2024 persistence from the stacked (stat, season) cell table.

    Rank correlations use percentile ranks WITHIN (stat, season) so that pooling the four
    stats does not manufacture persistence from stat-level differences."""
    cells = cells.with_columns(
        pl.col("skill").rank().over("stat", "season").alias("rk_skill"),
        pl.col("skill_raw").fill_nan(None).rank().over("stat", "season").alias("rk_skill_raw"),
        (pl.col("coverage_raw") - 0.8).abs().rank().over("stat", "season").alias("rk_cov_dev"),
    )
    a = cells.filter(pl.col("season") == 2023)
    b = cells.filter(pl.col("season") == 2024)
    j = a.join(b, on=["player_id", "stat"], suffix="_24")
    res: dict[str, Any] = {"n_joined_cells": j.height}

    def block(sub: pl.DataFrame, label: str) -> None:
        if sub.height < 20:
            res[label] = {"n": sub.height}
            return
        t0, t1 = sub["tier"].to_numpy(), sub["tier_24"].to_numpy()
        pid = sub["player_id"].to_numpy()
        same = cluster_boot(pid, lambda ix: float((t0[ix] == t1[ix]).mean()))
        kap = cluster_boot(pid, lambda ix: kappa(t0[ix], t1[ix]))

        def rho(col: str, only_pts: bool = False) -> tuple[float, float, float]:
            d = sub.filter(pl.col("stat") == sub["stat"][0]) if only_pts else sub
            x, y = d[col].to_numpy(), d[col + "_24"].to_numpy()
            return cluster_boot(d["player_id"].to_numpy(), lambda ix: _rho(x[ix], y[ix]))

        rho_s, rho_r, rho_c = rho("rk_skill"), rho("rk_skill_raw"), rho("rk_cov_dev")
        rho_m = rho("minutes_cv", only_pts=True)  # player-level: one stat's rows
        base = {
            t: float((t1 == t).mean()) for t in TIERS
        }  # unconditional 2024 tier shares in this subset
        cond = {
            t: (float((t1[t0 == t] == t).mean()) if (t0 == t).any() else float("nan"))
            for t in TIERS
        }
        res[label] = {
            "n": sub.height,
            "players": int(len(np.unique(pid))),
            "same_tier": same,
            "kappa": kap,
            "spearman_skill_shrunk": rho_s,
            "spearman_skill_raw": rho_r,
            "spearman_abs_cov_dev_raw": rho_c,
            "spearman_minutes_cv": rho_m,
            "p_tier24_given_tier23": cond,
            "p_tier24_unconditional": base,
            "n_by_tier23": {t: int((t0 == t).sum()) for t in TIERS},
            "transition": {
                f"{x}->{y}": int(((t0 == x) & (t1 == y)).sum()) for x in TIERS for y in TIERS
            },
        }

    block(j, "all_cells")
    both30 = j.filter((pl.col("n") >= N_LOW) & (pl.col("n_24") >= N_LOW))
    block(both30, "n30_both_seasons")
    for s in PROP_STATS:
        block(both30.filter(pl.col("stat") == s), f"n30_both_{s}")
    return res


# --------------------------------------------------------------------------- pipeline


def load_played(db: str) -> pl.DataFrame:
    con = duckdb.connect(db, read_only=True)
    try:
        return con.execute(
            "SELECT s.game_id, s.player_id, g.game_date, g.season, s.minutes, "
            "s.pts, s.reb, s.ast, s.fg3m FROM player_game_stats s JOIN games g USING (game_id) "
            f"WHERE g.season <= {MAX_SEASON} AND s.minutes > 0"
        ).pl()
    finally:
        con.close()


def score_rows(oof: pl.DataFrame, played: pl.DataFrame, stat: str) -> pl.DataFrame:
    """Per-row model/baseline integer CRPS and coverage for one stat."""
    hist = played.filter(~pl.col("game_id").is_in(list(NEUTRAL_RESTORED_2024)))
    rows = (
        oof.filter(pl.col("target") == stat)
        .join(played.select(["game_id", "player_id", "minutes", stat]), on=["game_id", "player_id"])
        .rename({stat: "y"})
        .sort(["player_id", "game_date", "game_id"])
    )
    qc = np.asarray(rows["q_grid"].to_list(), dtype=float)
    y = rows["y"].to_numpy().astype(float)
    qm = to_integer_support(qc)
    qb, n_prior = baseline_quantiles(hist, rows, stat)
    ok = (n_prior >= MIN_PRIOR) & ~np.isnan(qb).any(axis=1)
    qb = to_integer_support(np.where(np.isnan(qb), 0.0, qb))
    out = rows.select(["player_id", "game_id", "season", "minutes"]).with_columns(
        pl.Series("m_crps", crps_from_quantiles(qm, y, TAUS)),
        pl.Series("b_crps", crps_from_quantiles(qb, y, TAUS)),
        pl.Series("cov", interval_coverage(y, qc[:, LO_I], qc[:, HI_I])),
        pl.Series("ok", ok),
        pl.lit(stat).alias("stat"),
    )
    return out


def run(db: str, oof_path: str, out_dir: Path) -> dict[str, Any]:
    oof = pl.read_parquet(oof_path).filter(pl.col("season") <= MAX_SEASON)
    assert int(oof["season"].to_numpy().max()) <= MAX_SEASON
    played = load_played(db)
    out_dir.mkdir(parents=True, exist_ok=True)
    cells: list[pl.DataFrame] = []
    meta: dict[str, dict[str, Any]] = {}
    for stat in PROP_STATS:
        sc = score_rows(oof, played, stat)
        meta[stat] = {"rows_oof": sc.height, "rows_dropped_no_baseline": int((~sc["ok"]).sum())}
        sc = sc.filter(pl.col("ok"))
        print(f"[{stat}] scored rows {sc.height}", flush=True)
        for season in SEASONS:
            sub = sc.filter(pl.col("season") == season)
            tbl, m = build_cells(sub)
            meta[stat][str(season)] = m
            cells.append(
                tbl.with_columns(pl.lit(stat).alias("stat"), pl.lit(season).alias("season"))
            )
            print(
                f"[{stat}] {season} cells {tbl.height} tau_skill {m['skill_tau']:.4f}", flush=True
            )
    allc = pl.concat(cells).select(
        "player_id",
        "stat",
        "tier",
        "n",
        "skill",
        "coverage",
        "minutes_cv",
        "season",
        "skill_raw",
        "coverage_raw",
    )
    for season in SEASONS:
        allc.filter(pl.col("season") == season).write_parquet(
            out_dir / f"reliability_{season}.parquet"
        )
    stab = stability(allc)

    def share(sub: pl.DataFrame) -> dict[str, float]:
        t = sub["tier"].to_numpy()
        return {k: float((t == k).mean()) for k in TIERS}

    shares = {
        str(s): {
            st: share(allc.filter((pl.col("season") == s) & (pl.col("stat") == st)))
            for st in PROP_STATS
        }
        for s in SEASONS
    }
    shares_all = {str(s): share(allc.filter(pl.col("season") == s)) for s in SEASONS}
    # share among cells with n >= 30 (tier not decided by games played)
    shares30 = {
        str(s): share(allc.filter((pl.col("season") == s) & (pl.col("n") >= N_LOW)))
        for s in SEASONS
    }
    # descriptive links
    d24 = allc.filter(pl.col("season") == 2024)
    corr = {}
    for st in PROP_STATS:
        sub = d24.filter((pl.col("stat") == st) & (pl.col("n") >= N_LOW))
        corr[st] = {
            "spearman_minutes_cv_vs_skill_raw": _rho(
                sub["minutes_cv"].to_numpy(), sub["skill_raw"].to_numpy()
            )
        }
    result = {
        "meta": meta,
        "stability": stab,
        "shares_by_stat": shares,
        "shares_all": shares_all,
        "shares_n30": shares30,
        "minutes_cv_vs_skill_2024": corr,
        "cells_by_season": {str(s): allc.filter(pl.col("season") == s).height for s in SEASONS},
    }
    (out_dir / "reliability_summary.json").write_text(json.dumps(result, indent=1, default=float))
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DB_DEFAULT)
    ap.add_argument("--oof", default=OOF_DEFAULT)
    ap.add_argument("--out", default="reports/player_reliability")
    a = ap.parse_args(argv)
    run(a.db, a.oof, Path(a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
