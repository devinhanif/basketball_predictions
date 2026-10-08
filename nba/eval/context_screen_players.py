"""Exploratory screen: do DRAFT POSITION and COLLEGE explain what the played-only
recency-average props baseline misses, and how strong are they as cold-start priors?

PRE-REGISTERED FAMILY (written before the first run; exploratory, BH q = 0.10)
-----------------------------------------------------------------------------
Data: real ``nba.duckdb`` opened read_only; played rows only (``minutes > 0``;
DNP rows are excluded per docs/DNP_AUDIT_2026-10-08.md); seasons 2022-2024 only
(2025 is burned and never read). Baseline = recency-weighted mean of the
player's prior played games, half-life 10 games (the ``nba.props.forward``
default), strictly as-of (row's own game and anything later never enter);
residual = actual - baseline; rows need >= 3 prior played games.

Screen A -- one OLS per (subset, stat) of residual on four standardized
pedigree predictors (ln draft pick for drafted players [undrafted = 0 after
centering]; undrafted flag; age at game; power-conference college flag) plus a
untested control log1p(prior played games). Tested coefficient = effect per
1 SD of the predictor.
  subsets (2): S1 = games-played-this-season-before-this-game <= 15 (all
               players); S2 = player in first two NBA seasons ("early career").
  stats (4):   pts, reb, ast, fg3m.
  predictors (4): ln_pick, undrafted, age, power_conf.
  => m_A = 2 x 4 x 4 = 32 tests. p-values: two-sided bootstrap, resampling
  PLAYERS (primary: pedigree is a player-level covariate, game clustering alone
  is anti-conservative); a clustered-by-GAME bootstrap is also reported and a
  test needs p<=BH threshold under BOTH.

Screen B -- cold-start first-season production of rookie cohorts. Outcomes
(4): pts/36, reb/36, ast/36, minutes per game. Walk-forward by draft class:
test class 2023 trained on 2022; test class 2024 trained on 2022+2023; pooled
out-of-sample. Ridge (alpha by 5-fold CV inside train) on ln pick, undrafted,
college group one-hot, age at first game, height, weight, position one-hot vs
a position-only (primary position) train mean. Test statistic: paired
MAE(position-only) - MAE(ridge) >0 means pedigree helps; bootstrap over
players. => m_B = 4.

m = 36 total. BH q = 0.10 over all 36. Effect floors (a survivor must also
clear these, else flagged "significant but trivial"): A per 1 SD: pts 0.20,
reb 0.10, ast 0.07, fg3m 0.03 (actual-stat units per game); B: relative MAE
improvement >= 3%.

Descriptive tables (not tested): first-season stats by pick bucket and
college group, mean residual by pick bucket.

Run:  uv run python -m nba.eval.context_screen_players --out <markdown path>
"""
# ruff: noqa: E501

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import numpy as np
import polars as pl
from sklearn.linear_model import Ridge, RidgeCV

from nba.features.player_pedigree import load_college_map, pedigree_as_of, static_pedigree

SEED = 20261008
SCREEN_SEASONS = (2022, 2023, 2024)  # 2025 is burned: never read.
HALFLIFE = 10.0
STATS = ("pts", "reb", "ast", "fg3m")
MIN_HIST = 3
EARLY_SEASON_GAMES = 15
N_BOOT = 2000
BH_Q = 0.10
FLOORS_A = {"pts": 0.20, "reb": 0.10, "ast": 0.07, "fg3m": 0.03}
FLOOR_B_REL = 0.03
PREDICTORS = ("ln_pick", "undrafted", "age", "power_conf")
PICK_BUCKETS = (
    ("1-5", 1, 5),
    ("6-14", 6, 14),
    ("15-30", 15, 30),
    ("31-60", 31, 60),
)
MIN_ROOKIE_MINUTES = 200.0
MIN_ROOKIE_GAMES = 10


# --------------------------------------------------------------------------- data


def load_played(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    ss = ",".join(str(s) for s in SCREEN_SEASONS)
    rows = con.execute(
        f"""
        SELECT s.player_id, s.game_id, g.game_date, g.season, s.minutes,
               s.pts, s.reb, s.ast, s.fg3m
        FROM player_game_stats s JOIN games g USING (game_id)
        WHERE s.minutes > 0 AND g.season IN ({ss})
          AND s.pts IS NOT NULL AND s.reb IS NOT NULL AND s.ast IS NOT NULL
          AND s.fg3m IS NOT NULL
        ORDER BY s.player_id, g.game_date, s.game_id
        """
    ).fetchall()
    return pl.DataFrame(
        rows,
        schema={
            "player_id": pl.Int64,
            "game_id": pl.Utf8,
            "game_date": pl.Date,
            "season": pl.Int64,
            "minutes": pl.Float64,
            "pts": pl.Float64,
            "reb": pl.Float64,
            "ast": pl.Float64,
            "fg3m": pl.Float64,
        },
        orient="row",
    )


def load_static(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    rows = con.execute(
        "SELECT player_id, position, height_in, weight_lb, birth_date, draft_year, "
        "draft_pick, college FROM players_static"
    ).fetchall()
    return pl.DataFrame(
        rows,
        schema={
            "player_id": pl.Int64,
            "position": pl.Utf8,
            "height_in": pl.Float64,
            "weight_lb": pl.Float64,
            "birth_date": pl.Date,
            "draft_year": pl.Int64,
            "draft_pick": pl.Int64,
            "college": pl.Utf8,
        },
        orient="row",
    )


def add_recency_baseline(played: pl.DataFrame, halflife: float = HALFLIFE) -> pl.DataFrame:
    """As-of recency-weighted baseline per stat plus prior-played counts.

    ``played`` must be sorted by (player_id, game_date, game_id). The weight of
    a prior game g games ago is 0.5 ** (g / halflife) (most recent = 1), the
    same weighting as ``nba.props.forward.recency_weighted_dist``; computed by
    an exponential recursion over *prior* rows only (the row's own value is
    added after its baseline is emitted)."""
    decay = 0.5 ** (1.0 / halflife)
    pid = played["player_id"].to_numpy()
    season = played["season"].to_numpy()
    vals = np.column_stack([played[s].to_numpy() for s in STATS])
    n = len(pid)
    base = np.full((n, len(STATS)), np.nan)
    n_hist = np.zeros(n, dtype=np.int64)
    n_season = np.zeros(n, dtype=np.int64)
    num = np.zeros(len(STATS))
    den = 0.0
    cur, cur_season, cnt, cnt_season = -1, -1, 0, 0
    for i in range(n):
        if pid[i] != cur:
            cur, cur_season, cnt, cnt_season = int(pid[i]), -1, 0, 0
            num = np.zeros(len(STATS))
            den = 0.0
        if season[i] != cur_season:
            cur_season, cnt_season = int(season[i]), 0
        n_hist[i], n_season[i] = cnt, cnt_season
        if cnt > 0:
            base[i] = num / den
        num = decay * num + vals[i]
        den = decay * den + 1.0
        cnt += 1
        cnt_season += 1
    out = played
    for j, s in enumerate(STATS):
        out = out.with_columns(
            pl.Series(f"base_{s}", base[:, j]),
        )
        out = out.with_columns((pl.col(s) - pl.col(f"base_{s}")).alias(f"res_{s}"))
    return out.with_columns(pl.Series("n_hist", n_hist), pl.Series("n_season_prior", n_season))


def add_cohorts(df: pl.DataFrame, static: pl.DataFrame) -> pl.DataFrame:
    """first_season / early_career / rookie-class flags (see module doc)."""
    first = df.group_by("player_id").agg(pl.col("season").min().alias("first_season"))
    out = df.join(first, on="player_id", how="left").join(
        static.select("player_id", "draft_year", "undrafted", "draft_pick"),
        on="player_id",
        how="left",
        suffix="_s",
    )
    drafted_early = (
        (pl.col("undrafted") == 0)
        & pl.col("draft_year").is_not_null()
        & (pl.col("season") - pl.col("draft_year")).is_between(0, 1)
    )
    undrafted_early = (
        (pl.col("undrafted") == 1)
        & (pl.col("first_season") >= 2023)
        & ((pl.col("season") - pl.col("first_season")) <= 1)
    )
    return out.with_columns((drafted_early | undrafted_early).alias("early_career")).drop(
        "draft_year", "undrafted", "draft_pick"
    )


# ------------------------------------------------------------------ bootstrap OLS


def _cluster_sums(X: np.ndarray, Y: np.ndarray, codes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort(codes, kind="stable")
    c = codes[order]
    Xo, Yo = X[order], Y[order]
    starts = np.r_[0, np.flatnonzero(np.diff(c)) + 1]
    xtx = np.add.reduceat(Xo[:, :, None] * Xo[:, None, :], starts, axis=0)
    xty = np.add.reduceat(Xo[:, :, None] * Yo[:, None, :], starts, axis=0)
    return xtx, xty


def ols_cluster_boot(
    X: np.ndarray, Y: np.ndarray, codes: np.ndarray, n_boot: int, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    """OLS beta (p, k) and bootstrap betas (n_boot, p, k) resampling clusters."""
    xtx, xty = _cluster_sums(X, Y, codes)
    g = xtx.shape[0]
    eye = 1e-9 * np.eye(X.shape[1])
    beta = np.linalg.solve(xtx.sum(0) + eye, xty.sum(0))
    boots = np.empty((n_boot, X.shape[1], Y.shape[1]))
    for b in range(n_boot):
        cnt = np.bincount(rng.integers(0, g, size=g), minlength=g).astype(float)
        boots[b] = np.linalg.solve(np.tensordot(cnt, xtx, 1) + eye, np.tensordot(cnt, xty, 1))
    return beta, boots


def boot_p(boots: np.ndarray) -> np.ndarray:
    """Two-sided bootstrap p (with +1 smoothing) of 'coefficient == 0'."""
    b = boots.shape[0]
    lo = ((boots <= 0).sum(0) + 1) / (b + 1)
    hi = ((boots >= 0).sum(0) + 1) / (b + 1)
    return np.asarray(np.minimum(1.0, 2 * np.minimum(lo, hi)))


def bh_reject(p: np.ndarray, q: float) -> np.ndarray:
    """Benjamini-Hochberg: boolean mask of rejected hypotheses at FDR q."""
    m = len(p)
    order = np.argsort(p)
    thresh = q * (np.arange(1, m + 1) / m)
    passed = p[order] <= thresh
    keep = np.zeros(m, dtype=bool)
    if passed.any():
        k = int(np.flatnonzero(passed).max())
        keep[order[: k + 1]] = True
    return keep


def cluster_mean_ci(
    vals: np.ndarray, clusters: np.ndarray, rng: np.random.Generator, n_boot: int = 1000
) -> tuple[float, float, float]:
    """Mean and 95% percentile CI, resampling clusters (players)."""
    if len(vals) == 0:
        return (float("nan"),) * 3
    _, codes = np.unique(clusters, return_inverse=True)
    g = codes.max() + 1
    s = np.bincount(codes, weights=vals, minlength=g)
    n = np.bincount(codes, minlength=g).astype(float)
    ms = np.empty(n_boot)
    for b in range(n_boot):
        cnt = np.bincount(rng.integers(0, g, size=g), minlength=g)
        ms[b] = (cnt * s).sum() / max((cnt * n).sum(), 1.0)
    return float(vals.mean()), float(np.percentile(ms, 2.5)), float(np.percentile(ms, 97.5))


# ----------------------------------------------------------------------- Screen A


def screen_a_frame(df: pl.DataFrame, static: pl.DataFrame) -> pl.DataFrame:
    d = pedigree_as_of(static, df.select("player_id", "game_date").unique())
    d = d.select("player_id", "game_date", "age", "log_pick", "undrafted", "college_group")
    out = df.join(d, on=["player_id", "game_date"], how="left")
    out = out.join(static.select("player_id", "draft_pick"), on="player_id", how="left")
    out = out.filter(
        (pl.col("n_hist") >= MIN_HIST)
        & ~((pl.col("undrafted") == 0) & pl.col("draft_pick").is_null())
        & pl.col("age").is_not_null()
    )
    return out.with_columns(
        (pl.col("college_group") == "power_conf").cast(pl.Float64).alias("power_conf")
    )


def run_screen_a(frame: pl.DataFrame, rng: np.random.Generator) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    subsets = {
        "S1_first15_of_season": frame.filter(pl.col("n_season_prior") < EARLY_SEASON_GAMES),
        "S2_first_two_seasons": frame.filter(pl.col("early_career")),
    }
    for name, sub in subsets.items():
        drafted = sub["undrafted"].to_numpy() == 0
        lp = sub["log_pick"].to_numpy()
        ln_pick = np.where(drafted, lp - lp[drafted].mean(), 0.0)
        raw = np.column_stack(
            [
                ln_pick,
                sub["undrafted"].to_numpy().astype(float),
                sub["age"].to_numpy(),
                sub["power_conf"].to_numpy(),
            ]
        )
        ctrl = np.log1p(sub["n_hist"].to_numpy().astype(float))
        z = np.column_stack([raw, ctrl])
        sd = z.std(0)
        sd[sd == 0] = 1.0
        z = (z - z.mean(0)) / sd
        X = np.column_stack([np.ones(len(z)), z])
        Y = np.column_stack([sub[f"res_{s}"].to_numpy() for s in STATS])
        for label, key in (("player", "player_id"), ("game", "game_id")):
            _, codes = np.unique(sub[key].to_numpy(), return_inverse=True)
            beta, boots = ols_cluster_boot(X, Y, codes, N_BOOT, rng)
            p = boot_p(boots)
            lo = np.percentile(boots, 2.5, axis=0)
            hi = np.percentile(boots, 97.5, axis=0)
            for si, s in enumerate(STATS):
                for pi, pname in enumerate(PREDICTORS):
                    results.append(
                        {
                            "subset": name,
                            "stat": s,
                            "pred": pname,
                            "cluster": label,
                            "n": len(sub),
                            "n_players": int(sub["player_id"].n_unique()),
                            "beta": float(beta[pi + 1, si]),
                            "lo": float(lo[pi + 1, si]),
                            "hi": float(hi[pi + 1, si]),
                            "p": float(p[pi + 1, si]),
                        }
                    )
    return results


def pick_bucket_expr() -> pl.Expr:
    branches = [(pl.col("undrafted") == 1, "undrafted")] + [
        (pl.col("draft_pick").is_between(lo, hi), lab) for lab, lo, hi in PICK_BUCKETS
    ]
    expr: pl.Expr = pl.lit("unknown_pick")
    for cond, lab in reversed(branches):
        expr = pl.when(cond).then(pl.lit(lab)).otherwise(expr)
    return expr


# ----------------------------------------------------------------------- Screen B


def rookie_table(df: pl.DataFrame, static: pl.DataFrame) -> pl.DataFrame:
    """One row per rookie: class, first-season per-36/mpg outcomes, features."""
    first = df.group_by("player_id").agg(pl.col("season").min().alias("first_season"))
    j = df.join(first, on="player_id").filter(pl.col("season") == pl.col("first_season"))
    agg = j.group_by("player_id").agg(
        pl.col("first_season").first(),
        pl.col("game_date").min().alias("debut"),
        pl.len().alias("gp"),
        pl.col("minutes").sum().alias("min_tot"),
        pl.col("pts").sum().alias("pts_tot"),
        pl.col("reb").sum().alias("reb_tot"),
        pl.col("ast").sum().alias("ast_tot"),
    )
    agg = agg.join(static, on="player_id", how="left")
    cls = (
        ((pl.col("first_season") == 2022) & (pl.col("draft_year") == 2022))
        | ((pl.col("first_season") >= 2023) & (pl.col("undrafted") == 1))
        | (
            (pl.col("first_season") >= 2023)
            & (pl.col("undrafted") == 0)
            & (pl.col("first_season") - pl.col("draft_year") <= 3)
        )
    )
    agg = agg.filter(cls)
    agg = agg.filter(pl.col("draft_pick").is_not_null() | (pl.col("undrafted") == 1))
    agg = agg.with_columns(
        (36 * pl.col("pts_tot") / pl.col("min_tot")).alias("pts36"),
        (36 * pl.col("reb_tot") / pl.col("min_tot")).alias("reb36"),
        (36 * pl.col("ast_tot") / pl.col("min_tot")).alias("ast36"),
        (pl.col("min_tot") / pl.col("gp")).alias("mpg"),
        ((pl.col("debut") - pl.col("birth_date")).dt.total_days() / 365.25).alias("age_debut"),
    )
    return agg.filter(
        (pl.col("min_tot") >= MIN_ROOKIE_MINUTES) & (pl.col("gp") >= MIN_ROOKIE_GAMES)
    ).sort("player_id")


OUTCOMES = ("pts36", "reb36", "ast36", "mpg")
COLLEGE_LEVELS = (
    "power_conf",
    "mid_major",
    "international_pro",
    "pathway",
    "high_school",
    "unknown",
)
POS_LEVELS = ("Guard", "Forward", "Center", "Unknown")


def _design(tab: pl.DataFrame, cols: str) -> np.ndarray:
    cont = [tab["age_debut"].to_numpy(), tab["height_in"].to_numpy(), tab["weight_lb"].to_numpy()]
    parts: list[np.ndarray] = []
    if "pick" in cols:
        parts += [tab["log_pick"].to_numpy(), tab["undrafted"].to_numpy().astype(float)]
    if "college" in cols:
        parts += [(tab["college_group"] == c).to_numpy().astype(float) for c in COLLEGE_LEVELS]
    if "age" in cols:
        parts += [cont[0]]
    if "body" in cols:
        parts += [cont[1], cont[2]]
    if "pos" in cols:
        parts += [(tab["position_primary"] == c).to_numpy().astype(float) for c in POS_LEVELS]
    return np.column_stack(parts)


def _impute(a: np.ndarray, ref: np.ndarray) -> np.ndarray:
    out = a.copy()
    med = np.nanmedian(ref, axis=0)
    idx = np.where(np.isnan(out))
    out[idx] = np.take(med, idx[1])
    return out


def _fit_predict(tr: pl.DataFrame, te: pl.DataFrame, y: str, cols: str, seed: int) -> np.ndarray:
    xtr, xte = _design(tr, cols), _design(te, cols)
    xte = _impute(xte, xtr)
    xtr = _impute(xtr, xtr)
    mu, sd = xtr.mean(0), xtr.std(0)
    sd[sd == 0] = 1.0
    xtr, xte = (xtr - mu) / sd, (xte - mu) / sd
    ytr = tr[y].to_numpy()
    cv = RidgeCV(alphas=(1.0, 3.0, 10.0, 30.0, 100.0, 300.0), cv=5).fit(xtr, ytr)
    return np.asarray(Ridge(alpha=float(cv.alpha_)).fit(xtr, ytr).predict(xte))


def _pos_mean_predict(tr: pl.DataFrame, te: pl.DataFrame, y: str) -> np.ndarray:
    means = tr.group_by("position_primary").agg(pl.col(y).mean().alias("m"))
    mp = dict(zip(means["position_primary"].to_list(), means["m"].to_list(), strict=True))
    glob = float(tr[y].mean())  # type: ignore[arg-type]
    return np.array([mp.get(p, glob) for p in te["position_primary"].to_list()])


def _r2(y: np.ndarray, pred: np.ndarray, ref: np.ndarray) -> float:
    return float(1 - ((y - pred) ** 2).sum() / ((y - ref) ** 2).sum())


def _d_r2(y: np.ndarray, a: np.ndarray, b: np.ndarray, ref: np.ndarray, idx: np.ndarray) -> float:
    """R2 gain of predictor ``b`` over ``a`` on rows ``idx`` (ref = train mean)."""
    num = ((y[idx] - a[idx]) ** 2).sum() - ((y[idx] - b[idx]) ** 2).sum()
    return float(num / ((y[idx] - ref[idx]) ** 2).sum())


def run_screen_b(
    rk: pl.DataFrame, rng: np.random.Generator
) -> tuple[list[dict[str, object]], dict[str, dict[str, np.ndarray]]]:
    folds = ((2023, [2022]), (2024, [2022, 2023]))
    res: list[dict[str, object]] = []
    detail: dict[str, dict[str, np.ndarray]] = {}
    for y in OUTCOMES:
        yt, pr, pp, ppk, pcl, prv, ppb = [], [], [], [], [], [], []
        for test_cls, train_cls in folds:
            tr = rk.filter(pl.col("first_season").is_in(train_cls))
            te = rk.filter(pl.col("first_season") == test_cls)
            yt.append(te[y].to_numpy())
            pr.append(_fit_predict(tr, te, y, "pick college age body pos", SEED))
            pp.append(_pos_mean_predict(tr, te, y))
            ppk.append(_fit_predict(tr, te, y, "pick", SEED))
            ppb.append(_fit_predict(tr, te, y, "body pos", SEED))
            pcl.append(_fit_predict(tr, te, y, "college", SEED))
            prv.append(np.full(te.height, float(tr[y].mean())))  # type: ignore[arg-type]
        yt_, pr_, pp_, ppk_, pcl_, prv_, ppb_ = (
            np.concatenate(v) for v in (yt, pr, pp, ppk, pcl, prv, ppb)
        )
        n = len(yt_)

        d_mae = np.abs(yt_ - pp_) - np.abs(yt_ - pr_)

        d_inc = np.abs(yt_ - ppb_) - np.abs(yt_ - pr_)
        bs_inc = np.array([d_inc[rng.integers(0, n, size=n)].mean() for _ in range(N_BOOT)])
        bs_mae = np.empty(N_BOOT)
        bs_r2 = np.empty(N_BOOT)
        for b in range(N_BOOT):
            idx = rng.integers(0, n, size=n)
            bs_mae[b] = d_mae[idx].mean()
            bs_r2[b] = _d_r2(yt_, pp_, pr_, prv_, idx)
        lo_p = ((bs_mae <= 0).sum() + 1) / (N_BOOT + 1)
        hi_p = ((bs_mae >= 0).sum() + 1) / (N_BOOT + 1)
        mae_pos = float(np.abs(yt_ - pp_).mean())
        res.append(
            {
                "outcome": y,
                "n_test": n,
                "mae_ridge": float(np.abs(yt_ - pr_).mean()),
                "mae_pos": mae_pos,
                "mae_posbody": float(np.abs(yt_ - ppb_).mean()),
                "d_inc": float(d_inc.mean()),
                "d_inc_lo": float(np.percentile(bs_inc, 2.5)),
                "d_inc_hi": float(np.percentile(bs_inc, 97.5)),
                "mae_pick_only": float(np.abs(yt_ - ppk_).mean()),
                "mae_college_only": float(np.abs(yt_ - pcl_).mean()),
                "r2_ridge": _r2(yt_, pr_, prv_),
                "r2_pos": _r2(yt_, pp_, prv_),
                "r2_pick_only": _r2(yt_, ppk_, prv_),
                "d_mae": float(d_mae.mean()),
                "d_mae_lo": float(np.percentile(bs_mae, 2.5)),
                "d_mae_hi": float(np.percentile(bs_mae, 97.5)),
                "d_r2": float(_d_r2(yt_, pp_, pr_, prv_, np.arange(n))),
                "d_r2_lo": float(np.percentile(bs_r2, 2.5)),
                "d_r2_hi": float(np.percentile(bs_r2, 97.5)),
                "rel_mae_gain": float(d_mae.mean() / mae_pos),
                "p": float(min(1.0, 2 * min(lo_p, hi_p))),
            }
        )
        detail[y] = {"y": yt_, "ridge": pr_, "pos": pp_}
    return res, detail


def describe_rookies(rk: pl.DataFrame, rng: np.random.Generator) -> list[str]:
    rk = rk.with_columns(pick_bucket_expr().alias("bucket"))
    lines: list[str] = []
    for title, col, order in (
        ("pick bucket", "bucket", [b[0] for b in PICK_BUCKETS] + ["undrafted", "unknown_pick"]),
        ("college group", "college_group", list(COLLEGE_LEVELS)),
    ):
        lines += [
            f"\n**First-season production by {title}** (mean, 95% bootstrap CI over players)\n",
            "| group | n | pts/36 | reb/36 | ast/36 | min/g |",
            "|---|---|---|---|---|---|",
        ]
        for g in order:
            sub = rk.filter(pl.col(col) == g)
            if sub.height == 0:
                continue
            cells = []
            for y in OUTCOMES:
                m, lo, hi = cluster_mean_ci(sub[y].to_numpy(), sub["player_id"].to_numpy(), rng)
                cells.append(f"{m:.1f} [{lo:.1f}, {hi:.1f}]")
            lines.append(f"| {g} | {sub.height} | " + " | ".join(cells) + " |")
    return lines


# ------------------------------------------------------------------------- report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    rng = np.random.default_rng(SEED)
    con = duckdb.connect(args.db, read_only=True)
    played = load_played(con)
    static_raw = load_static(con)
    con.close()
    assert played["season"].max() <= 2024, "2025 is burned"  # type: ignore[operator]
    static = static_pedigree(static_raw, load_college_map())
    df = add_recency_baseline(played)
    df = add_cohorts(df, static)
    out: list[str] = [f"seed={SEED}  played rows={df.height}  players={df['player_id'].n_unique()}"]

    # ---- missingness
    sr = static_raw
    out += [
        f"\n## Missingness (players_static, all {sr.height} rows)\n",
        f"- draft_year & draft_pick both NULL (treated undrafted): {int(static['undrafted'].sum())}",
        f"- draft_year set, pick NULL (unknown pick): "
        f"{int((static['draft_year'].is_not_null() & static['draft_pick'].is_null()).sum())}",
        f"- birth_date NULL: {sr['birth_date'].null_count()}; height NULL: {sr['height_in'].null_count()}; "
        f"position NULL: {sr['position'].null_count()}",
        "- college groups: "
        + ", ".join(
            f"{k}={v}" for k, v in sorted(static["college_group"].value_counts().iter_rows())
        ),
    ]

    # ---- Screen A
    fa = screen_a_frame(df, static)
    a_res = run_screen_a(fa, rng)
    prim = [r for r in a_res if r["cluster"] == "player"]
    game = {(r["subset"], r["stat"], r["pred"]): r for r in a_res if r["cluster"] == "game"}
    pvals_a = np.array([float(r["p"]) for r in prim])  # type: ignore[arg-type]
    # Screen B
    rk = rookie_table(df, static)
    b_res, _ = run_screen_b(rk, rng)
    pvals_b = np.array([float(r["p"]) for r in b_res])  # type: ignore[arg-type]
    allp = np.r_[pvals_a, pvals_b]
    # a test must also pass BH under the game-cluster p (A only)
    pg = np.array([float(game[(r["subset"], r["stat"], r["pred"])]["p"]) for r in prim])  # type: ignore[arg-type]
    rej = bh_reject(allp, BH_Q)
    rej_game = bh_reject(np.r_[pg, pvals_b], BH_Q)
    m = len(allp)
    out += [
        f"\n## Pre-registered family: m = {m} (A: {len(prim)}, B: {len(b_res)}), BH q = {BH_Q}\n",
        "\n### Screen A: residual (actual - recency avg) per 1 SD of predictor\n",
        "| subset | stat | predictor | n rows / players | beta [95% CI player-boot] | p(player) | p(game) | BH | floor | verdict |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    n_surv = 0
    for i, r in enumerate(prim):
        floor = FLOORS_A[str(r["stat"])]
        sig = bool(rej[i] and rej_game[i])
        big = abs(float(r["beta"])) >= floor  # type: ignore[arg-type]
        verdict = "SURVIVOR" if sig and big else ("sig-but-trivial" if sig else "-")
        n_surv += verdict == "SURVIVOR"
        gp = game[(r["subset"], r["stat"], r["pred"])]["p"]
        out.append(
            f"| {r['subset']} | {r['stat']} | {r['pred']} | {r['n']} / {r['n_players']} | "
            f"{float(r['beta']):+.3f} [{float(r['lo']):+.3f}, {float(r['hi']):+.3f}] | "  # type: ignore[arg-type]
            f"{float(r['p']):.3f} | {float(gp):.3f} | {'Y' if rej[i] else 'n'} | {floor} | {verdict} |"  # type: ignore[arg-type]
        )
    out += [
        f"\nRookie cohort for Screen B: n={rk.height} (train+test; classes "
        + ", ".join(f"{c}: {n}" for c, n in sorted(rk.group_by("first_season").len().iter_rows()))
        + f"; >= {MIN_ROOKIE_MINUTES:.0f} min and >= {MIN_ROOKIE_GAMES} gp)",
        "\n### Screen B: walk-forward ridge (pick+college+age+height/weight+position) vs position-only mean\n",
        "Test = classes 2023 (train 2022) and 2024 (train 2022-23), pooled. dMAE = MAE(pos-only) - MAE(ridge); positive = pedigree helps.\n",
        "| outcome | n test | MAE ridge | MAE pos-only | MAE pick-only | MAE college-only | R2 ridge | R2 pos-only | dMAE [95% CI] | rel gain | dR2 [95% CI] | p | BH | verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for j, r in enumerate(b_res):
        sig = bool(rej[len(prim) + j] and rej_game[len(prim) + j])
        big = float(r["rel_mae_gain"]) >= FLOOR_B_REL  # type: ignore[arg-type]
        verdict = "SURVIVOR" if sig and big else ("sig-but-trivial" if sig else "-")
        n_surv += verdict == "SURVIVOR"
        out.append(
            f"| {r['outcome']} | {r['n_test']} | {float(r['mae_ridge']):.2f} | {float(r['mae_pos']):.2f} | "  # type: ignore[arg-type]
            f"{float(r['mae_pick_only']):.2f} | {float(r['mae_college_only']):.2f} | "  # type: ignore[arg-type]
            f"{float(r['r2_ridge']):+.3f} | {float(r['r2_pos']):+.3f} | "  # type: ignore[arg-type]
            f"{float(r['d_mae']):+.3f} [{float(r['d_mae_lo']):+.3f}, {float(r['d_mae_hi']):+.3f}] | "  # type: ignore[arg-type]
            f"{100 * float(r['rel_mae_gain']):+.1f}% | "  # type: ignore[arg-type]
            f"{float(r['d_r2']):+.3f} [{float(r['d_r2_lo']):+.3f}, {float(r['d_r2_hi']):+.3f}] | "  # type: ignore[arg-type]
            f"{float(r['p']):.3f} | {'Y' if rej[len(prim) + j] else 'n'} | {verdict} |"  # type: ignore[arg-type]
        )
    out += [
        "\nSupplementary (untested): does PEDIGREE add over position + height/weight? "
        "dMAE = MAE(ridge on position+body only) - MAE(full ridge).\n",
        "| outcome | MAE pos+body ridge | MAE full ridge | dMAE [95% CI] |",
        "|---|---|---|---|",
    ]
    for r in b_res:
        out.append(
            f"| {r['outcome']} | {float(r['mae_posbody']):.2f} | {float(r['mae_ridge']):.2f} | "  # type: ignore[arg-type]
            f"{float(r['d_inc']):+.3f} [{float(r['d_inc_lo']):+.3f}, {float(r['d_inc_hi']):+.3f}] |"  # type: ignore[arg-type]
        )
    out.append(f"\nSurvivors (BH + floor): {n_surv} of {m}.")

    # ---- descriptive
    out += ["\n## Descriptive (not tested)\n"] + describe_rookies(rk, rng)
    out += ["\n**Mean residual (actual - recency avg) by pick bucket**\n"]
    out += [
        "| subset | bucket | n rows | pts | reb | ast | fg3m |",
        "|---|---|---|---|---|---|---|",
    ]
    fb = fa.with_columns(pick_bucket_expr().alias("bucket"))
    for name, sub in (
        ("S1_first15_of_season", fb.filter(pl.col("n_season_prior") < EARLY_SEASON_GAMES)),
        ("S2_first_two_seasons", fb.filter(pl.col("early_career"))),
    ):
        for lab in [b[0] for b in PICK_BUCKETS] + ["undrafted"]:
            s2 = sub.filter(pl.col("bucket") == lab)
            if s2.height == 0:
                continue
            cells = []
            for s in STATS:
                mm, lo, hi = cluster_mean_ci(
                    s2[f"res_{s}"].to_numpy(), s2["player_id"].to_numpy(), rng, 500
                )
                cells.append(f"{mm:+.2f} [{lo:+.2f}, {hi:+.2f}]")
            out.append(f"| {name} | {lab} | {s2.height} | " + " | ".join(cells) + " |")
    text = "\n".join(out)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n")


if __name__ == "__main__":
    main()
