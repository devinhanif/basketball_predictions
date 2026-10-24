# ruff: noqa: E501
"""MARKET_ANCHORED_PROPS core (rule: docs/prereg/MARKET_ANCHORED_PROPS.md; DRAFT until a hand that can commit freezes it).

Nested logistic arms on Pinnacle t60 main-line legs, fitted walk-forward by calendar month:

    M   logit p = m                                              (Pinnacle de-vigged P(over), the market alone)
    MR  logit p = X_mr beta                                      (market-only recalibration: no model input)
    MM  logit p = X_mr beta + delta * (g0 + g_b bench + g_t tmout + g_d disp)   (market plus model disagreement)
    PL  MM refit with delta given to another leg of the same (game_date, stat)   (placebo, mean of K draws)

Components (per-leg log loss, paired): C_mkt = MR - M, C_mod = MM - MR (the claim), C_pl = MM - PL, C_total = MM - M.

Nothing here reads a label unless the caller passes one: ``load_legs`` never selects ``over`` or ``y``. Season 2025 is refused.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from research.eval import market_prior_residual_eval as mpr
from research.eval import market_prior_residual_rule as rule
from research.eval.model_clv_eval import Boot

STATS: tuple[str, ...] = rule.STATS
MARKET_OF = {
    "pts": "player_points",
    "reb": "player_rebounds",
    "ast": "player_assists",
    "fg3m": "player_threes",
}
HI_LINE = {
    "pts": 24.5,
    "reb": 9.5,
    "ast": 6.5,
    "fg3m": 2.5,
}  # "high line" cut, fixed before any outcome
DCLIP = 3.0  # |delta| clipped here (logit units)
U1CLIP = 1.0
TMOUT_MIN = (
    3  # heavy depletion: n_out_rot >= 3 (31% of legs; >= 1 is 83%, not a discriminating flag)
)
LAM = 50.0  # ridge on every coefficient but the stat intercepts, centred on the starting point (market identity; zero for added terms)
N_MIN_TRAIN = 3000  # prior pooled legs before a month is scored (MARKET_PRIOR_RESIDUAL N_MIN_TRAIN)
HALF_LIFE_DAYS = 365.0
K_PLACEBO = 50
INFO_FLOOR = -0.001
BET_FLOOR = -0.003
WORSE_FLOOR = 0.0005  # MM must be this much worse than M (with CI above 0) to override: the null overfit cost is +0.0002
MODEL_CLIP = rule.MODEL_CLIP
ODDS_DB = "data/odds/odds_history.duckdb"
EXTRAS_REL = "data/ops/market_anchored_props/extras_{season}.parquet"
EXTRAS_PINNED_SHA256: dict[
    int, str
] = {}  # filled by the freeze commit (rule text); the run refuses a mismatch
K1_SELECT_LIMIT = 0.0010  # kill: 2023 C_total point estimate above this -> KILLED_K1
PAIRS_COLS = [
    "game_id", "player_id", "stat", "book", "snapshot_kind", "snapshot_ts", "tipoff_utc", "line",
    "p_over_mult", "p_model", "_dnp", "status", "season", "game_date", "starter10", "n_out_rot",
]  # fmt: skip
MR_COLS = (
    *(f"icpt_{s}" for s in STATS),
    *(f"m_{s}" for s in STATS),
    "hi",
    "u1",
    "miss_u1",
    "blowout",
    "bench",
    "tmout",
    "disp10",
)
MM_COLS = (*(f"delta_{s}" for s in STATS), "delta_bench", "delta_tmout", "delta_disp")


def _log(msg: str) -> None:
    print(f"[map {time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------------------------------- inputs


def odds_extras(season: int, db_path: str | None = None) -> pl.DataFrame:
    """Label-free market-only extras per (game, player, stat, line): soft-book mean/sd of the de-vigged logit at the
    SAME line as Pinnacle's, and the game's absolute spread. Read-only DuckDB, season asserted <= 2024."""
    rule.assert_seasons([season])
    path = db_path or str(mpr.resolve(ODDS_DB))
    mk = ",".join(f"'{v}'" for v in MARKET_OF.values())
    con = duckdb.connect(path, read_only=True)
    try:
        q = f"""
        with q as (
          select game_id, player_id, market, book, point,
                 max(case when side='over'  then price_american end) po,
                 max(case when side='under' then price_american end) pu
          from odds_history
          where season = {int(season)} and snapshot_kind = 't60' and market in ({mk}) and player_id is not null
          group by 1,2,3,4,5),
        d as (
          select game_id, player_id, market, book, point,
                 case when po < 0 then -po/(-po+100.0) else 100.0/(po+100.0) end as ro,
                 case when pu < 0 then -pu/(-pu+100.0) else 100.0/(pu+100.0) end as ru
          from q where po is not null and pu is not null),
        soft as (
          select game_id, player_id, market, point,
                 avg(ln(ro/ru)) as u_mean, stddev_samp(ln(ro/ru)) as u_sd, count(*) as n_soft
          from d where book <> 'pinnacle' group by 1,2,3,4)
        select game_id, player_id, market, point as line, u_mean, u_sd, n_soft from soft
        """
        soft = con.execute(q).pl()
        sp = con.execute(
            f"select game_id, median(abs(point)) as spread_abs from odds_history "
            f"where season = {int(season)} and snapshot_kind = 't60' and market = 'spreads' and side = 'home' group by 1"
        ).pl()
    finally:
        con.close()
    inv = {v: k for k, v in MARKET_OF.items()}
    soft = soft.with_columns(
        pl.col("market").replace_strict(inv, return_dtype=pl.String).alias("stat")
    ).drop("market")
    return soft.join(sp, on="game_id", how="left")


def build_extras(
    out_dir: str, db_path: str, seasons: tuple[int, ...] = (2023, 2024)
) -> dict[str, str]:
    """Write the market-side extras (u_mean, u_sd, n_soft, spread_abs per game, player, stat, line) to one sorted parquet per
    season and return their sha256. The rule pins these files; the run reads them and never opens the live odds database."""
    import os

    os.makedirs(out_dir, exist_ok=True)
    out: dict[str, str] = {}
    for s in seasons:
        ex = odds_extras(s, db_path).sort(["game_id", "player_id", "stat", "line"])
        path = os.path.join(out_dir, f"extras_{s}.parquet")
        ex.write_parquet(path, compression="zstd", statistics=False)
        out[str(s)] = rule.file_sha256(Path(path))
    return out


def load_legs(
    seasons: tuple[int, ...] = (2023, 2024),
    pairs_path: str = mpr.PAIRS_REL,
    extras_path: str = EXTRAS_REL,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Pinnacle t60 legs (the MARKET_PRIOR_RESIDUAL / LINE_CALIBRATION definition) plus the market-only extras.
    Never selects a label column."""
    rule.assert_seasons(seasons)
    frames: list[pl.DataFrame] = []
    counts: dict[str, Any] = {}
    for s in seasons:
        d = pl.read_parquet(mpr.resolve(pairs_path.format(season=s)), columns=PAIRS_COLS)
        assert int(d["season"].max()) <= rule.MAX_SEASON  # type: ignore[arg-type]
        n0 = d.height
        d = d.filter(
            (pl.col("book") == rule.BOOK)
            & (pl.col("snapshot_kind") == rule.SNAPSHOT_KIND)
            & pl.col("stat").is_in(list(STATS))
            & ~pl.col("_dnp")
            & (pl.col("status") == 0)
        )
        n1 = d.height
        keep = rule.known_at_keep(d["snapshot_ts"].to_numpy(), d["tipoff_utc"].to_numpy())
        d = d.filter(pl.Series(keep))
        n2 = d.height
        d = d.filter(((pl.col("line") % 1.0) - 0.5).abs() < 1e-9)
        n3 = d.height
        ex_file = mpr.resolve(extras_path.format(season=s))
        if s in EXTRAS_PINNED_SHA256 and rule.file_sha256(ex_file) != EXTRAS_PINNED_SHA256[s]:
            raise RuntimeError(f"extras frame for {s} differs from its pin: refusing to run")
        ex = pl.read_parquet(ex_file)
        d = d.join(ex, on=["game_id", "player_id", "stat", "line"], how="left")
        counts[str(s)] = {
            "rows": n0,
            "played_pinnacle_t60": n1,
            "after_known_at": n2,
            "half_point": n3,
        }
        frames.append(d)
    legs = pl.concat(frames).sort(["game_date", "game_id", "stat", "player_id"])
    assert (
        legs.select(pl.struct(["game_id", "player_id", "stat"]).is_duplicated().any()).item()
        is False
    )
    return legs, counts


def to_arrays(legs: pl.DataFrame) -> dict[str, np.ndarray]:
    """Numeric frame for the arms. Only market-side and as-of model columns; no label."""
    stat = legs["stat"].to_numpy()
    stat_i = np.array([STATS.index(s) for s in stat], dtype=np.int64)
    line = legs["line"].to_numpy().astype(float)
    m = rule.logit(legs["p_over_mult"].to_numpy())
    pm = np.clip(legs["p_model"].to_numpy().astype(float), MODEL_CLIP, 1.0 - MODEL_CLIP)
    a = np.log(pm / (1.0 - pm))
    delta = np.clip(a - m, -DCLIP, DCLIP)
    hi = (line >= np.array([HI_LINE[s] for s in stat])).astype(float)
    u_mean = legs["u_mean"].to_numpy().astype(float)
    n_soft = np.nan_to_num(legs["n_soft"].to_numpy().astype(float), nan=0.0)
    ok1 = np.isfinite(u_mean) & (n_soft >= 3)
    u1 = np.where(ok1, np.clip(np.nan_to_num(u_mean) - m, -U1CLIP, U1CLIP), 0.0)
    u_sd = legs["u_sd"].to_numpy().astype(float)
    disp10 = np.where(ok1 & np.isfinite(u_sd), 10.0 * u_sd, 0.0)
    sp = legs["spread_abs"].to_numpy().astype(float)
    bl = np.where(np.isfinite(sp), sp / 10.0, 0.0)
    starter = np.nan_to_num(legs["starter10"].to_numpy().astype(float), nan=1.0)
    nout = np.nan_to_num(legs["n_out_rot"].to_numpy().astype(float), nan=0.0)
    dates = legs["game_date"].to_numpy().astype("datetime64[D]")
    gid = legs["game_id"].to_numpy()
    ps = np.array(
        [
            f"{p}-{s}"
            for p, s in zip(legs["player_id"].to_list(), legs["season"].to_list(), strict=True)
        ]
    )
    iso = (dates.astype("datetime64[W]") - np.datetime64("1970-01-05", "W")).astype(int)
    return {
        "stat_i": stat_i, "m": m, "a": a, "delta": delta, "hi": hi, "u1": u1, "miss_u1": (~ok1).astype(float),
        "disp10": disp10, "bl": bl, "bench": (starter < 0.5).astype(float), "tmout": (nout >= TMOUT_MIN).astype(float),
        "season": legs["season"].to_numpy().astype(int), "date": dates,
        "month": dates.astype("datetime64[M]"), "game_idx": np.unique(gid, return_inverse=True)[1],
        "ps_idx": np.unique(ps, return_inverse=True)[1], "week_idx": np.unique(iso, return_inverse=True)[1],
        "line": line, "p_model": pm,
    }  # fmt: skip


def _onehot(A: dict[str, np.ndarray], idx: np.ndarray) -> np.ndarray:
    return np.asarray(A["stat_i"][idx][:, None] == np.arange(len(STATS))[None, :], dtype=float)


def design_b0(A: dict[str, np.ndarray], idx: np.ndarray) -> np.ndarray:
    """8 columns: stat intercepts (4) and a stat-specific slope on the market logit (4): pure re-scaling of the price."""
    oh = _onehot(A, idx)
    return np.column_stack([oh, oh * A["m"][idx][:, None]])


def _market_cov(A: dict[str, np.ndarray], idx: np.ndarray) -> np.ndarray:
    return np.column_stack(
        [
            A["hi"][idx],
            A["u1"][idx],
            A["miss_u1"][idx],
            A["bl"][idx],
            A["bench"][idx],
            A["tmout"][idx],
            A["disp10"][idx],
        ]
    )


def _model_cols(A: dict[str, np.ndarray], idx: np.ndarray, delta: np.ndarray | None) -> np.ndarray:
    d = A["delta"][idx] if delta is None else delta
    oh = _onehot(A, idx)
    return np.column_stack(
        [oh * d[:, None], d * A["bench"][idx], d * A["tmout"][idx], d * A["disp10"][idx]]
    )


def design_mr(A: dict[str, np.ndarray], idx: np.ndarray) -> np.ndarray:
    """12 columns: B0 plus the market-side covariates (hi, u1, miss_u1, blowout). No model input."""
    return np.column_stack([design_b0(A, idx), _market_cov(A, idx)])


def design_md(
    A: dict[str, np.ndarray], idx: np.ndarray, delta: np.ndarray | None = None
) -> np.ndarray:
    """7 columns, fitted on the offset m (no re-scaling, no market covariates): the model credited first."""
    return _model_cols(A, idx, delta)


def design_mm(
    A: dict[str, np.ndarray], idx: np.ndarray, delta: np.ndarray | None = None
) -> np.ndarray:
    """19 columns: MR plus the model terms."""
    return np.column_stack([design_mr(A, idx), _model_cols(A, idx, delta)])


BETA0_B0 = np.concatenate([np.zeros(4), np.ones(4)])
BETA0_MR = np.concatenate([BETA0_B0, np.zeros(7)])
BETA0_MD = np.zeros(7)
BETA0_MM = np.concatenate([BETA0_MR, np.zeros(7)])
UNPENALISED = np.arange(4)  # the stat intercepts


# ------------------------------------------------------------------------------------------- fitting


def sigmoid(z: np.ndarray) -> np.ndarray:
    return np.asarray(1.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0))))


def fit_logit(
    X: np.ndarray,
    y: np.ndarray,
    w: np.ndarray,
    beta0: np.ndarray,
    lam: float | None = None,
    iters: int = 40,
    free: np.ndarray = UNPENALISED,
    offset: np.ndarray | None = None,
) -> np.ndarray:
    """Weighted logistic regression, ridge ``lam`` on every coefficient toward ``beta0`` except the ``free`` ones, Newton."""
    k = X.shape[1]
    pen = (LAM if lam is None else lam) * np.ones(k)
    pen[free] = 0.0
    beta = beta0.astype(float).copy()
    off = np.zeros(len(y)) if offset is None else offset
    for _ in range(iters):
        p = sigmoid(off + X @ beta)
        g = X.T @ (w * (p - y)) + pen * (beta - beta0)
        H = (X.T * (w * p * (1.0 - p))) @ X + np.diag(pen) + 1e-9 * np.eye(k)
        step = np.linalg.solve(H, g)
        beta -= step
        if float(np.max(np.abs(step))) < 1e-8:
            break
    return beta


def ll_vec(logit_p: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Per-leg log loss from a logit (numerically stable)."""
    z = np.clip(logit_p, -30.0, 30.0)
    return np.asarray(np.logaddexp(0.0, z) - y * z)


def derangements(A: dict[str, np.ndarray], k: int, seed: int = 1000) -> np.ndarray:
    """K index arrays: within each (game_date, stat) group, a cyclic shift of a random order (no leg keeps its own row);
    singleton groups are pooled per stat and shifted the same way."""
    n = len(A["m"])
    key = A["date"].astype("datetime64[D]").astype(np.int64) * 8 + A["stat_i"]
    out = np.empty((k, n), dtype=np.int64)
    uniq, inv = np.unique(key, return_inverse=True)
    groups = [np.flatnonzero(inv == g) for g in range(len(uniq))]
    big = [g for g in groups if len(g) > 1]
    single = [g[0] for g in groups if len(g) == 1]
    for j in range(k):
        rng = np.random.default_rng(seed + j)
        perm = np.arange(n)
        for g in big:
            o = g[rng.permutation(len(g))]
            perm[o] = np.roll(o, 1)
        if single:
            s = np.array(single)
            for st in range(len(STATS)):
                ss = s[A["stat_i"][s] == st]
                if len(ss) > 1:
                    o = ss[rng.permutation(len(ss))]
                    perm[o] = np.roll(o, 1)
        out[j] = perm
    return out


def walk_forward(
    A: dict[str, np.ndarray],
    y: np.ndarray,
    score_season: int,
    train_seasons: tuple[int, ...],
    perms: np.ndarray | None = None,
    n_min: int = N_MIN_TRAIN,
    log: bool = False,
) -> dict[str, Any]:
    """Monthly expanding walk-forward, one pooled fit per month (stat-specific terms inside the design). A month of
    ``score_season`` is scored with parameters fitted on legs of ``train_seasons`` strictly before that month (weights
    0.5 ** (age/365)); a month with fewer than ``n_min`` prior legs is not scored."""
    n = len(y)
    out = {
        k: np.full(n, np.nan)
        for k in ("ll_m", "ll_b0", "ll_mr", "ll_md", "ll_mm", "ll_pl", "lp_mr", "lp_mm")
    }
    pl_pooled: list[list[float]] = [[] for _ in range(0 if perms is None else len(perms))]
    scored = np.zeros(n, dtype=bool)
    coefs: list[dict[str, Any]] = []
    months = np.unique(A["month"][A["season"] == score_season])
    for mo in months:
        mo_start = mo.astype("datetime64[D]")
        sc = np.flatnonzero((A["season"] == score_season) & (A["month"] == mo))
        tr = np.flatnonzero(np.isin(A["season"], train_seasons) & (A["date"] < mo_start))
        if len(sc) == 0 or len(tr) < n_min:
            continue
        age = (mo_start - A["date"][tr]).astype(float)
        w = 0.5 ** (age / HALF_LIFE_DAYS)
        b_mr = fit_logit(design_mr(A, tr), y[tr], w, BETA0_MR)
        b_mm = fit_logit(design_mm(A, tr), y[tr], w, BETA0_MM)
        b_b0 = fit_logit(design_b0(A, tr), y[tr], w, BETA0_B0)
        b_md = fit_logit(
            design_md(A, tr), y[tr], w, BETA0_MD, free=np.array([], dtype=int), offset=A["m"][tr]
        )
        out["ll_m"][sc] = ll_vec(A["m"][sc], y[sc])
        out["ll_b0"][sc] = ll_vec(design_b0(A, sc) @ b_b0, y[sc])
        out["ll_md"][sc] = ll_vec(A["m"][sc] + design_md(A, sc) @ b_md, y[sc])
        out["lp_mr"][sc] = design_mr(A, sc) @ b_mr
        out["lp_mm"][sc] = design_mm(A, sc) @ b_mm
        out["ll_mr"][sc] = ll_vec(out["lp_mr"][sc], y[sc])
        out["ll_mm"][sc] = ll_vec(out["lp_mm"][sc], y[sc])
        scored[sc] = True
        coefs.append(
            {
                "month": str(mo),
                "n_train": len(tr),
                "beta_mr": b_mr.tolist(),
                "beta_mm": b_mm.tolist(),
            }
        )
        if perms is not None:
            acc = np.zeros(len(sc))
            for j in range(len(perms)):
                dl = A["delta"][perms[j]]
                b_pl = fit_logit(design_mm(A, tr, dl[tr]), y[tr], w, BETA0_MM)
                l_j = ll_vec(design_mm(A, sc, dl[sc]) @ b_pl, y[sc])
                acc += l_j
                pl_pooled[j].extend(l_j.tolist())
            out["ll_pl"][sc] = acc / len(perms)
        if log:
            _log(f"season {score_season} month {mo} done")
    return {
        **out,
        "scored": scored,
        "coefs": coefs,
        "pl_draw_means": [float(np.mean(v)) if v else float("nan") for v in pl_pooled],
    }


# ------------------------------------------------------------------------------------------- inference


def make_boots(
    A: dict[str, np.ndarray], mask: np.ndarray, b: int = rule.N_BOOT, seed: int = rule.SEED
) -> dict[str, Boot]:
    boots: dict[str, Boot] = {}
    for name, key in (("game", "game_idx"), ("player_season", "ps_idx"), ("week", "week_idx")):
        _, gi = np.unique(A[key][mask], return_inverse=True)
        boots[name] = Boot(gi, int(gi.max()) + 1, seed=seed, B=b)
    return boots


def contrast(boots: dict[str, Boot], v: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
    """Mean of ``v`` over ``mask`` (``mask`` is the same one the boots were built on) with the envelope of the 3 clusterings."""
    vv = v[mask]
    ones = np.ones(len(vv), dtype=bool)
    per = {k: bt.mean(vv, ones) for k, bt in boots.items()}
    los = [c["lo"] for c in per.values() if c["lo"] is not None]
    his = [c["hi"] for c in per.values() if c["hi"] is not None]
    return {
        "mean": float(np.mean(vv)), "n": int(len(vv)), "lo": float(min(los)), "hi": float(max(his)), "per_clustering": per,
    }  # fmt: skip


def gain_p(boots: dict[str, Boot], v: np.ndarray, mask: np.ndarray) -> float:
    """One-sided p for "the mean of the per-leg loss difference ``v`` is below 0" (a gain is negative):
    max over the three clusterings of (1 + #{b : bootstrap mean >= 0}) / (B + 1). ``Boot.mean(...)["p"]`` is the opposite
    tail ((1 + #{bm <= 0}) / (B + 1)) and must not be used for these contrasts."""
    vv = v[mask]
    ones = np.ones(len(vv), dtype=bool)
    ps = []
    for bt in boots.values():
        sums, cnt = bt._sc(vv, ones)
        den = bt.W @ cnt
        bm = (bt.W @ sums) / np.where(den > 0, den, np.nan)
        bm = bm[np.isfinite(bm)]
        ps.append((1 + float(np.sum(bm >= 0))) / (len(bm) + 1))
    return max(ps)


def decompose(A: dict[str, np.ndarray], wf: dict[str, Any], b: int = rule.N_BOOT) -> dict[str, Any]:
    """The four components on the scored rows of one walk-forward result."""
    mask = wf["scored"]
    boots = make_boots(A, mask, b=b)
    comps = {
        "C_cal": wf["ll_b0"]
        - wf["ll_m"],  # pure re-scaling of the price (stat intercept and slope)
        "C_mktx": wf["ll_mr"] - wf["ll_b0"],  # market-side covariates beyond the re-scaling
        "C_mkt": wf["ll_mr"]
        - wf["ll_m"],  # = C_cal + C_mktx: everything the market alone can explain
        "C_mod": wf["ll_mm"] - wf["ll_mr"],  # the claim: model terms given the recalibrated market
        "C_mod_first": wf["ll_md"]
        - wf["ll_m"],  # model terms on the raw price (model credited first)
        "C_mkt_after": wf["ll_mm"]
        - wf["ll_md"],  # market side given the model first (the market word's leakage guard)
        "C_total": wf["ll_mm"] - wf["ll_m"],
    }
    if not np.all(np.isnan(wf["ll_pl"][mask])):
        comps["C_pl"] = wf["ll_mm"] - wf["ll_pl"]
    res: dict[str, Any] = {k: contrast(boots, v, mask) for k, v in comps.items()}
    res["n_scored"] = int(mask.sum())
    return res


def run_words(
    select_c_total_mean: float, invalid_reason: str | None = None
) -> dict[str, str] | None:
    """Run-level words that replace the verdict: NO_RESULT_INVALID_RUN (planted future, leg reproduction, pin or extras-pin
    failure) first, then KILLED_K1 (2023 C_total above K1_SELECT_LIMIT; 2024 is not scored). None when the run proceeds."""
    if invalid_reason is not None:
        return {"model": "NONE", "market": "NONE", "override": "NO_RESULT_INVALID_RUN"}
    if select_c_total_mean > K1_SELECT_LIMIT:
        return {"model": "NONE", "market": "NONE", "override": "KILLED_K1"}
    return None


def verdict(report: dict[str, Any], select: dict[str, Any]) -> dict[str, str]:
    """Rule section 5. A model word (C_mod = MM - MR: the model credited AFTER the recalibrated market) and a market word
    (C_mkt = MR - M detected, and kept only if C_mkt_after = MM - MD, the market side credited AFTER the model, is also
    below 0: otherwise MODEL_EXPLAINS), plus the override WORSE_THAN_MARKET (MM worse than M by
    at least WORSE_FLOOR with the binding CI above 0). ``report`` and ``select`` are ``decompose`` outputs for 2024 (primary) and
    2023 (sign condition). Detection is any size with the binding CI below 0; the floors say how large."""
    c_mod, c_mkt, c_aft, c_tot, c_pl = (
        report["C_mod"],
        report["C_mkt"],
        report["C_mkt_after"],
        report["C_total"],
        report["C_pl"],
    )
    if c_tot["lo"] > 0 and c_tot["mean"] >= WORSE_FLOOR:
        return {"model": "NONE", "market": "NONE", "override": "WORSE_THAN_MARKET"}
    mod_det = c_mod["hi"] < 0
    pl_det = c_pl["hi"] < 0
    sel_mod = select["C_mod"]["mean"] < 0
    sel_mkt = select["C_mkt"]["mean"] < 0
    if not mod_det:
        model = "NONE"
    elif not pl_det:
        model = "PLACEBO_EXPLAINS"
    elif not sel_mod:
        model = "FAIL_SELECT_SIGN"
    elif c_mod["mean"] <= BET_FLOOR and c_pl["mean"] <= BET_FLOOR:
        model = "BET_FLOOR_PASS"
    elif c_mod["mean"] <= INFO_FLOOR and c_pl["mean"] <= INFO_FLOOR:
        model = "INFO"
    else:
        model = "DETECTED_BELOW_INFO_FLOOR"
    if c_mkt["hi"] >= 0:
        market = "NONE"
    elif c_aft["mean"] >= 0:
        market = "MODEL_EXPLAINS"  # the market component vanishes when the model terms go first
    elif not sel_mkt:
        market = "FAIL_SELECT_SIGN"
    elif c_mkt["mean"] <= INFO_FLOOR and c_aft["hi"] < 0:
        market = "MISCALIBRATION_INFO"
    else:
        market = "MISCALIBRATION_DETECTED_BELOW_INFO_FLOOR"
    return {"model": model, "market": market, "override": ""}


# ------------------------------------------------------------------------------------------- synthetic worlds


def oracle_kl(p_true: np.ndarray, p_m: np.ndarray) -> float:
    """E[LL(M) - LL(true)] = mean KL(p_true || p_m): the best any arm could gain on this world."""
    pt = np.clip(p_true, 1e-12, 1 - 1e-12)
    pm = np.clip(p_m, 1e-12, 1 - 1e-12)
    return float(np.mean(pt * np.log(pt / pm) + (1 - pt) * np.log((1 - pt) / (1 - pm))))


def world_eta(A: dict[str, np.ndarray], kind: str, gamma: float = 0.0) -> np.ndarray:
    """Logit of the planted outcome probability. ``mkt``: market mis-calibration only (slope 0.92, over bias, soft-book
    pull, high-line shift) with the model inert; ``mod``: market plus ``gamma`` times the model disagreement."""
    eta = A["m"].copy()
    if kind in ("mkt", "both"):
        eta = -0.06 + 0.85 * A["m"] + 0.40 * A["u1"] - 0.08 * A["hi"]
    if kind in ("mod", "both"):
        eta = eta + gamma * A["delta"]
    return eta


OFF_SPAN_KINDS = ("bench", "tmout", "disp", "curv", "line")


def off_span_z(A: dict[str, np.ndarray], kind: str) -> np.ndarray:
    """Direction of a market error that MR cannot express as a main effect of its own columns: over-bias on bench legs,
    on heavy-depletion legs, in book dispersion (centred), curvature in m, and a within-stat standardised line level."""
    if kind == "bench":
        return -A["bench"]
    if kind == "tmout":
        return -A["tmout"]
    if kind == "disp":
        ok = A["miss_u1"] == 0
        return np.asarray(-(A["disp10"] - float(A["disp10"][ok].mean())) * ok)
    if kind == "curv":
        return -A["m"] * np.abs(A["m"])
    if kind == "line":
        z = np.zeros(len(A["m"]))
        for si in range(len(STATS)):
            k = A["stat_i"] == si
            z[k] = (A["line"][k] - A["line"][k].mean()) / A["line"][k].std()
        return -z
    raise ValueError(kind)


def draw_labels(eta: np.ndarray, seed: int) -> np.ndarray:
    return (np.random.default_rng(seed).random(len(eta)) < sigmoid(eta)).astype(float)
