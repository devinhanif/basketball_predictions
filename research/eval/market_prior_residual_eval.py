# ruff: noqa: E501
"""MARKET_PRIOR_RESIDUAL harness (rule: docs/prereg/MARKET_PRIOR_RESIDUAL.md; constants and verdict in ..._rule.py).

    M="-m research.eval.market_prior_residual_eval"
    .venv/bin/python -W ignore $M run --sha <64 hex>      # the scored run; REFUSES while the doc says DRAFT, or without the freeze commit

The label-free gate is ``research/eval/market_prior_residual_gate.py``. This module refuses any season > 2024 (``assert_seasons``),
opens ``nba.duckdb`` read-only through ``own_tag_eval.load_study`` (season <= 2024, asserted) and never touches season 2025.

Arms (rule section "Arms"): M = Pinnacle t60 de-vigged P(over) at the line; R = LightGBM with offset logit(p_M) on production's as-of
feature frame (it never sees production's own P(over)); PL = R with the production-feature block given to another leg of the same
(date, stat), averaged over K draws; A0 = production's stored P(over) (comparator).
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from research.eval import market_prior_residual_rule as rule

OUT_DIR = Path("reports/prereg_market_prior_residual")
PAIRS_REL = "data/ops/model_clv_pairs/pairs_{season}.parquet"
LOCK = Path("data/ops/heavy.lock")
KEYS = ["game_id", "player_id"]
LEG_COLS = [
    "game_id", "player_id", "stat", "book", "snapshot_kind", "snapshot_ts", "tipoff_utc", "line",
    "price_over", "price_under", "p_over_mult", "p_model", "_dnp", "status", "season", "game_date",
]  # fmt: skip
PINNED_SHA256 = {  # LINE_CALIBRATION's pins; the same frames (rule section "Data")
    2023: "050dba3e250d43d6d33eec836cd840397b168896b6017725b00d2bfd238b2569",
    2024: "560a3808d7d97110902471895e36a94a3f41d5ed88cb435e147d47f367dfdc5c",
}


def _log(msg: str) -> None:
    print(f"[mpr {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main_checkout() -> Path:
    """Root of the main checkout (git common dir's parent): untracked data lives there when running from a worktree."""
    import subprocess

    out = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        cwd=rule.ROOT,
        check=False,
    ).stdout.strip()
    return Path(out).parent if out else rule.ROOT


def resolve(rel: str) -> Path:
    for base in (rule.ROOT, main_checkout()):
        if (base / rel).exists():
            return base / rel
    raise FileNotFoundError(rel)


# --------------------------------------------------------------------------- locks


class Lock:
    """``mkdir data/ops/heavy.lock`` (retry every 120 s); writes pid and purpose; removes only a lock holding our own pid."""

    def __init__(self, purpose: str, retry_s: float = 120.0) -> None:
        self.purpose, self.retry_s, self.held = purpose, retry_s, False

    def __enter__(self) -> Lock:
        while True:
            try:
                LOCK.mkdir()
                (LOCK / "owner").write_text(f"{os.getpid()} {self.purpose}\n")
                self.held = True
                return self
            except FileExistsError:
                _log("heavy.lock held; retry in 120 s")
                time.sleep(self.retry_s)

    def __exit__(self, *exc: object) -> None:
        if self.held:
            try:
                if (LOCK / "owner").read_text().split()[0] == str(os.getpid()):
                    (LOCK / "owner").unlink()
                    LOCK.rmdir()
            except OSError:
                pass


# --------------------------------------------------------------------------- legs


def load_legs(
    seasons: tuple[int, ...] = (rule.SELECT_SEASON, rule.REPORT_SEASON),
    book: str = rule.BOOK,
    with_labels: bool = True,
    pairs_path: str = PAIRS_REL,
    min_lead: float = rule.MIN_LEAD_MIN,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Legs of ``book`` at snapshot t60: stats of the family, played, scored (x.5 lines, no push), known-at hard filter applied.

    Returns (legs, counts); ``counts`` has the rows before and after each filter per season. ``with_labels=False`` never selects
    the label columns (the gate's guarantee)."""
    rule.assert_seasons(seasons)
    cols = [*LEG_COLS, *(["over", "y"] if with_labels else [])]
    frames: list[pl.DataFrame] = []
    counts: dict[str, Any] = {}
    for s in seasons:
        d = pl.read_parquet(resolve(pairs_path.format(season=s)), columns=cols)
        assert int(d["season"].max()) <= rule.MAX_SEASON  # type: ignore[arg-type]
        n0 = d.height
        d = d.filter(
            (pl.col("book") == book)
            & (pl.col("snapshot_kind") == rule.SNAPSHOT_KIND)
            & pl.col("stat").is_in(list(rule.STATS))
        )
        n1 = d.height
        d = d.filter(~pl.col("_dnp") & (pl.col("status") == 0))
        n2 = d.height
        keep = rule.known_at_keep(d["snapshot_ts"].to_numpy(), d["tipoff_utc"].to_numpy(), min_lead)
        n3 = int(keep.sum())
        d = d.filter(pl.Series(keep))
        counts[str(s)] = {
            "rows": n0,
            "book_kind_stats": n1,
            "played_scored": n2,
            "after_known_at": n3,
            "dropped_known_at": n2 - n3,
        }
        frames.append(d)
    legs = pl.concat(frames).sort(["game_date", "game_id", "stat", "player_id"])
    assert (
        legs.select(pl.struct(["game_id", "player_id", "stat"]).is_duplicated().any()).item()
        is False
    ), "duplicate legs"
    return legs, counts


# --------------------------------------------------------------------------- features


def load_features(db_path: str | None = None) -> pl.DataFrame:
    """Production's as-of feature frame (the frame ``own_tag_eval.load_study`` builds), read-only, season <= 2024.

    Differs from ``load_study`` in one way: tip lookups are limited to schedule files of season <= 2024
    (``max_season``), so no holdout tip row is materialised; the own_tag columns are not built."""
    import yaml

    from nba.eval.context_residual_eval import load_inputs
    from nba.eval.lower_tail_eval import attach_realised
    from nba.props.context_residual import (
        ContextResidualConfig,
        build_features,
        flagged_from_availability,
    )

    db = db_path or str(resolve("nba.duckdb"))
    elo = {
        k: float(v)
        for k, v in yaml.safe_load(Path("configs/mov_elo_tuned.yaml").read_text()).items()
    }
    cfg = ContextResidualConfig(
        **yaml.safe_load(Path("configs/context_residual.yaml").read_text()).get("model", {})
    )
    games, pgs, static, avail = load_inputs(db, None)
    assert int(games["season"].max()) <= rule.MAX_SEASON  # type: ignore[arg-type]
    flagged, _ = flagged_from_availability(avail, games, cfg.report, max_season=rule.MAX_SEASON)
    feats = attach_realised(build_features(games, pgs, static, flagged, elo), games, pgs)
    assert int(feats["season"].max()) <= rule.MAX_SEASON  # type: ignore[arg-type]
    return feats


def generic_feature_names() -> list[str]:
    from nba.props.context_residual import COMMON_FEATURES, PROP_STATS

    return [
        *COMMON_FEATURES,
        *[f"m10_{s}" for s in PROP_STATS],
        "m5_own", "m40_own", "m100_own", "sd10_own", "role_gap_own", "rate_own", "opp_allow_own", "vac_own",
        "m_rec", "s_rec", "stat_code",
    ]  # fmt: skip


MARKET_FEATURES = ["line", "logit_pm", "line_minus_m", "line_z"]


def build_feature_frame(
    legs: pl.DataFrame, feats: pl.DataFrame
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Attach production's as-of features to each leg (pooled over stats with generic own-stat names). Counts the misses."""
    from nba.props.context_residual import stat_feature_names, stat_frame

    parts: list[pl.DataFrame] = []
    miss: dict[str, int] = {}
    for code, stat in enumerate(rule.STATS):
        sf = stat_frame(feats, stat)
        names = stat_feature_names(stat)
        ren = {
            f"{p}_{stat}": f"{p}_own"
            for p in ("m5", "m40", "m100", "sd10", "role_gap", "rate", "opp_allow", "vac")
        }
        keep = [*KEYS, *dict.fromkeys(names), "m", "s"]
        sf = (
            sf.select([c for c in keep if c in sf.columns])
            .rename(ren)
            .rename({"m": "m_rec", "s": "s_rec"})
        )
        lg = legs.filter(pl.col("stat") == stat)
        j = lg.join(sf, on=KEYS, how="left")
        miss[stat] = int(j["m_rec"].is_null().sum())
        parts.append(j.with_columns(pl.lit(code).alias("stat_code")))
    out = pl.concat(parts, how="diagonal").sort(["game_date", "game_id", "stat", "player_id"])
    assert out.height == legs.height, "feature join changed the row count"
    return out, {"misses_by_stat": miss, "misses": int(sum(miss.values())), "legs": legs.height}


def market_columns(
    line: np.ndarray, p_m: np.ndarray, m_rec: np.ndarray, s_rec: np.ndarray
) -> np.ndarray:
    lm = rule.logit(p_m)
    d = line - m_rec
    return np.column_stack([line, lm, d, d / np.maximum(s_rec, 0.5)])


def design_matrix(frame: pl.DataFrame, perm: np.ndarray | None = None) -> np.ndarray:
    """[production block | market block]. ``perm`` (placebo) re-assigns the production block, row i receiving row perm[i]'s block;
    the mixed market columns are re-derived from the permuted recency mean/std and the row's own line and price."""
    names = generic_feature_names()
    P = frame.select([pl.col(c).cast(pl.Float64) for c in names]).to_numpy()
    line = frame["line"].to_numpy().astype(float)
    p_m = frame["p_over_mult"].to_numpy().astype(float)
    ix = names.index
    if perm is not None:
        P = P[perm]
    mk = market_columns(line, p_m, P[:, ix("m_rec")], P[:, ix("s_rec")])
    return np.column_stack([P, mk])


# --------------------------------------------------------------------------- walk-forward fits


def month_starts(dates: np.ndarray) -> list[np.datetime64]:
    ms = np.unique(np.asarray(dates, dtype="datetime64[M]"))
    return [m.astype("datetime64[D]") for m in ms]


def fit_predict_block(
    X_tr: np.ndarray,
    y_tr: np.ndarray,
    off_tr: np.ndarray,
    w_tr: np.ndarray,
    X_te: np.ndarray,
    off_te: np.ndarray,
) -> np.ndarray:
    """LightGBM with a fixed offset (init_score = logit p_M): returns logit p_R = offset + raw score of the trees."""
    import lightgbm as lgb

    mdl = lgb.LGBMClassifier(**rule.LGB_PARAMS)
    mdl.fit(X_tr, y_tr, init_score=off_tr, sample_weight=w_tr)
    return np.asarray(off_te + np.asarray(mdl.predict(X_te, raw_score=True), dtype=float))


def walk_forward(
    frame: pl.DataFrame,
    y: np.ndarray,
    test_season: int,
    warm_seasons: tuple[int, ...] = (),
    perm: np.ndarray | None = None,
    n_min: int | None = None,
    X: np.ndarray | None = None,
    verbose: bool = False,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Expanding month-block fits. For each calendar month of ``test_season`` the model is fit on every leg strictly earlier (the
    warm seasons plus earlier months of the test season), time-weighted, and scored on the month. Returns (scored mask over frame
    rows, logit p_R for scored rows (NaN elsewhere), info). A month with fewer than ``n_min`` prior legs is not scored."""
    rule.assert_seasons((test_season, *warm_seasons))
    n_min = rule.N_MIN_TRAIN if n_min is None else n_min
    season = frame["season"].to_numpy()
    dates = frame["game_date"].to_numpy().astype("datetime64[D]")
    p_m = frame["p_over_mult"].to_numpy().astype(float)
    off = rule.logit(p_m)
    Xm = design_matrix(frame, perm) if X is None else X
    pool = np.isin(season, (test_season, *warm_seasons))
    out = np.full(len(frame), np.nan)
    scored = np.zeros(len(frame), dtype=bool)
    info: dict[str, Any] = {"blocks": [], "n_min": n_min}
    for start in month_starts(dates[season == test_season]):
        end = (start.astype("datetime64[M]") + np.timedelta64(1, "M")).astype("datetime64[D]")
        te = (season == test_season) & (dates >= start) & (dates < end)
        tr = pool & (dates < start)
        rec = {"start": str(start), "n_train": int(tr.sum()), "n_test": int(te.sum())}
        if tr.sum() < n_min or not te.any():
            rec["scored"] = False
            info["blocks"].append(rec)
            continue
        t0 = time.time()
        w = rule.time_weights(dates[tr], start)
        out[te] = fit_predict_block(Xm[tr], y[tr], off[tr], w, Xm[te], off[te])
        scored |= te
        rec["scored"] = True
        info["blocks"].append(rec)
        if verbose:
            _log(
                f"  block {start} train {int(tr.sum())} test {int(te.sum())} ({time.time() - t0:.1f}s)"
            )
    return scored, out, info


def h2_walk_forward(
    frame: pl.DataFrame,
    y: np.ndarray,
    test_season: int,
    warm_seasons: tuple[int, ...],
    scored: np.ndarray,
) -> np.ndarray:
    """Reference arm H2W (the collapse check): ODDS_HISTORY H2 / MARKET_SHRINK B1, a w-only blend of finished forecasts,
    ``logit p = logit p_M + w (logit p_A0 - logit p_M)``, w in {0, 0.05, .., 1} fit per stat on every earlier leg (unweighted, as H2)
    and frozen for the month; w = 0 (the market) for a stat-month with fewer than 2,000 earlier legs. Returns p on ``scored`` rows."""
    season = frame["season"].to_numpy()
    dates = frame["game_date"].to_numpy().astype("datetime64[D]")
    stat = frame["stat"].to_numpy()
    lm = rule.logit(frame["p_over_mult"].to_numpy().astype(float))
    la = rule.logit(
        np.clip(frame["p_model"].to_numpy().astype(float), rule.MODEL_CLIP, 1 - rule.MODEL_CLIP)
    )
    out = np.full(len(frame), np.nan)
    grid = np.linspace(0.0, 1.0, 21)
    pool = np.isin(season, (test_season, *warm_seasons))
    for start in month_starts(dates[season == test_season]):
        end = (start.astype("datetime64[M]") + np.timedelta64(1, "M")).astype("datetime64[D]")
        te = (season == test_season) & (dates >= start) & (dates < end) & scored
        for st in rule.STATS:
            tr = pool & (dates < start) & (stat == st)
            t = te & (stat == st)
            if not t.any():
                continue
            w = 0.0
            if tr.sum() >= 2000:
                losses = [
                    rule.log_loss_vec(rule.sigmoid(lm[tr] + g * (la[tr] - lm[tr])), y[tr]).mean()
                    for g in grid
                ]
                w = float(grid[int(np.argmin(losses))])
            out[t] = rule.sigmoid(lm[t] + w * (la[t] - lm[t]))
    return np.asarray(out[scored])


# --------------------------------------------------------------------------- scoring


def slice_masks(d: pl.DataFrame) -> dict[str, np.ndarray]:
    st = d["starter10"].to_numpy()
    n_out = d["n_out_rot"].to_numpy()
    tg = d["team_game_no"].to_numpy()
    out = {
        "starter": st >= 0.5,
        "bench": st < 0.5,
        "first15": tg <= 15,
        "rest_of_season": tg > 15,
        "teammate_out": n_out >= 1,
        "no_teammate_out": n_out < 1,
    }
    if "min10" in d.columns:
        m10 = d["min10"].to_numpy()
        out |= {
            "min10_lt15": m10 < 15,
            "min10_15_25": (m10 >= 15) & (m10 < 25),
            "min10_ge25": m10 >= 25,
        }
    hr = d["has_report"].to_numpy()
    out |= {"has_report": hr == 1, "no_report": hr == 0}
    return out


def beats_p(c: dict[str, Any]) -> float:
    """One-sided p that the contrast is below zero (R beats the comparator), worst case over the decisive clusterings.

    ``Boot`` returns (1 + #{boot <= 0}) / (B + 1), the p for 'above zero'; with no ties #{boot >= 0} = B - #{boot <= 0}."""
    out = 0.0
    for k in c["decisive"]:
        p, n_b = c["by_clustering"][k]["p"], rule.N_BOOT
        out = max(out, 1.0 - p + 1.0 / (n_b + 1))
    return min(out, 1.0)


def load_a0_current(frame: pl.DataFrame) -> np.ndarray | None:
    """Today's-vintage A0 P(over) at each leg's line from the GAME_MARKET_CONTEXT cache of A0 integer quantile grids (None if absent).

    The stored ``p_model`` (ODDS_HISTORY vintage, 2026-10-10) differs from today's production frame for about half of the 2024 legs
    (118-row backfill); the same-vintage comparator for R (whose features are today's) is this one."""
    out = np.full(frame.height, np.nan)
    try:
        base = resolve("data/ops/game_market_context_cache/arm_pts_A0.npz").parent
    except FileNotFoundError:
        return None
    for stat in rule.STATS:
        z = np.load(base / f"arm_{stat}_A0.npz")
        meta = (
            pl.read_parquet(base / f"arm_{stat}_A0.parquet")
            .select("game_id", "player_id", "season")
            .with_columns(pl.int_range(pl.len()).alias("_i"))
        )
        idx = np.flatnonzero(frame["stat"].to_numpy() == stat)
        sub = (
            frame[idx]
            .select("game_id", "player_id", "season", "line")
            .join(meta, on=["game_id", "player_id", "season"], how="left")
        )
        ok = sub["_i"].is_not_null().to_numpy()
        qi = z["qi"][sub["_i"].fill_null(0).to_numpy()]
        k = np.floor(sub["line"].to_numpy()) + 1
        p = (qi >= k[:, None]).mean(axis=1)
        out[idx[ok]] = p[ok]
    return out


def contrast(
    v: np.ndarray, d: pl.DataFrame, clusters: dict[str, np.ndarray], mask: np.ndarray | None = None
) -> dict[str, Any]:
    """Mean of ``v`` with cluster-bootstrap CIs under each clustering and the binding envelope. n and cluster counts reported."""
    m = np.ones(len(v), dtype=bool) if mask is None else mask
    cis: dict[str, dict[str, Any]] = {}
    for k in rule.CLUSTERINGS:
        codes = np.unique(clusters[k][m], return_inverse=True)[1]
        cis[k] = rule.cluster_ci(v[m], codes) | {"clusters": rule.n_clusters(codes)}
    dec = rule.decisive_clusterings(cis["week"]["clusters"])
    env = rule.envelope(cis, dec)
    return {"n": int(m.sum()), "by_clustering": cis, "decisive": list(dec), "binding": env}


def score_stage(
    d: pl.DataFrame,
    y: np.ndarray,
    p_r: np.ndarray,
    p_pl: np.ndarray | None,
    with_secondary: bool = True,
    p_h2: np.ndarray | None = None,
) -> dict[str, Any]:
    """Everything the rule scores for one stage on the scored rows ``d`` (already restricted to scored months)."""
    if d.height == 0:
        raise RuntimeError("no scored rows: every month is under the minimum training size")
    p_m = d["p_over_mult"].to_numpy().astype(float)
    p_a0 = np.clip(d["p_model"].to_numpy().astype(float), rule.MODEL_CLIP, 1 - rule.MODEL_CLIP)
    clusters = {k: rule.cluster_codes(d, k) for k in rule.CLUSTERINGS}
    ll_m, ll_r, ll_a = (
        rule.log_loss_vec(p_m, y),
        rule.log_loss_vec(p_r, y),
        rule.log_loss_vec(p_a0, y),
    )
    res: dict[str, Any] = {
        "n": int(len(y)),
        "clusters": {k: rule.n_clusters(c) for k, c in clusters.items()},
        "ll": {"M": float(ll_m.mean()), "R": float(ll_r.mean()), "A0": float(ll_a.mean())},
        "a_R_minus_M": contrast(ll_r - ll_m, d, clusters),
    }
    dec = res["a_R_minus_M"]["decisive"]
    dis: dict[str, dict[str, Any]] = {}
    for k in rule.CLUSTERINGS:
        dis[k] = rule.disagreement_boot(y, p_m, p_r, clusters[k])
    res["b_disagreement"] = {
        "by_clustering": dis,
        "decisive": dec,
        "binding": _dis_envelope(dis, dec),
    }
    if p_pl is not None:
        ll_pl = (
            rule.log_loss_vec(p_pl, y[None, :]).mean(axis=0)
            if p_pl.ndim == 2
            else rule.log_loss_vec(p_pl, y)
        )
        res["c_R_minus_PL"] = contrast(ll_r - ll_pl, d, clusters)
        res["ll"]["PL"] = float(ll_pl.mean())
        if p_pl.ndim == 2:
            per_draw = rule.log_loss_vec(p_pl, y[None, :]).mean(axis=1)
            res["placebo_null"] = {
                "K": int(p_pl.shape[0]),
                "mean_ll": float(per_draw.mean()),
                "sd_ll": float(per_draw.std(ddof=1)) if p_pl.shape[0] > 1 else None,
            }
    if with_secondary:
        res["secondary"] = secondary(
            d, y, p_m, p_r, p_a0, ll_m, ll_r, ll_a, clusters, load_a0_current(d), p_h2
        )
    return res


def _dis_envelope(dis: dict[str, dict[str, Any]], dec: list[str]) -> dict[str, Any]:
    return {
        "coef": dis[dec[0]]["coef"],
        "lo": min(dis[k]["lo"] for k in dec),
        "hi": max(dis[k]["hi"] for k in dec),
        "p": max(dis[k]["p"] for k in dec),
    }


def secondary(
    d: pl.DataFrame,
    y: np.ndarray,
    p_m: np.ndarray,
    p_r: np.ndarray,
    p_a0: np.ndarray,
    ll_m: np.ndarray,
    ll_r: np.ndarray,
    ll_a: np.ndarray,
    clusters: dict[str, np.ndarray],
    a0_cur: np.ndarray | None = None,
    p_h2: np.ndarray | None = None,
) -> dict[str, Any]:
    """Labels only, no pass role. BH over the declared family: per-stat R-M (4) and per-stat disagreement coefficient (4)."""
    out: dict[str, Any] = {}
    stat = d["stat"].to_numpy()
    out["brier_R_minus_M"] = contrast(rule.brier_vec(p_r, y) - rule.brier_vec(p_m, y), d, clusters)
    out["R_minus_A0_stored"] = contrast(ll_r - ll_a, d, clusters)
    if a0_cur is not None and np.isfinite(a0_cur).all():
        ll_c = rule.log_loss_vec(np.clip(a0_cur, rule.MODEL_CLIP, 1 - rule.MODEL_CLIP), y)
        out["R_minus_A0_current_vintage"] = contrast(ll_r - ll_c, d, clusters)
        out["A0_current_minus_M"] = contrast(ll_c - ll_m, d, clusters)
    dis_a0 = {k: rule.disagreement_boot(y, p_m, p_a0, clusters[k]) for k in rule.CLUSTERINGS}
    out["disagreement_A0_stored"] = {
        "by_clustering": dis_a0,
        "note": "H2's information in the b-test form: coefficient on logit(p_A0) - logit(p_M), no fit beyond the regression",
    }
    if p_h2 is not None:
        ll_h = rule.log_loss_vec(p_h2, y)
        out["H2W_minus_M"] = contrast(ll_h - ll_m, d, clusters)
        out["R_minus_H2W"] = contrast(ll_r - ll_h, d, clusters)
    fam: list[tuple[str, float]] = []
    per: dict[str, Any] = {}
    for s in rule.STATS:
        m = stat == s
        if m.sum() < 200:
            per[s] = {"n": int(m.sum()), "note": "too few legs"}
            continue
        c = contrast(ll_r - ll_m, d, clusters, m)
        sub = {k: np.unique(v[m], return_inverse=True)[1] for k, v in clusters.items()}
        dis = {k: rule.disagreement_boot(y[m], p_m[m], p_r[m], sub[k]) for k in rule.CLUSTERINGS}
        dec = c["decisive"]
        per[s] = {
            "R_minus_M": c,
            "disagreement": {"by_clustering": dis, "binding": _dis_envelope(dis, dec)},
        }
        fam.append((f"{s}:R_minus_M", beats_p(c)))
        fam.append((f"{s}:disagreement", _dis_envelope(dis, dec)["p"]))
    adj = rule.bh_adjust([p for _, p in fam]) if fam else []
    out["per_stat"] = per
    out["bh_family"] = [{"test": n, "p": p, "bh": a} for (n, p), a in zip(fam, adj, strict=True)]
    sl: dict[str, Any] = {}
    for name, m in slice_masks(d).items():
        if m.sum() >= 300:
            sl[name] = contrast(ll_r - ll_m, d, clusters, m)
        else:
            sl[name] = {"n": int(m.sum()), "note": "n < 300: not scored"}
    out["slices_R_minus_M"] = sl
    return out


# --------------------------------------------------------------------------- the run


def run_stages(
    legs: pl.DataFrame,
    frame: pl.DataFrame,
    y_all: np.ndarray,
    k_placebo: int,
    verbose: bool = True,
    with_secondary: bool = True,
) -> dict[str, Any]:
    """Select (2023, no warm start) then report (2024, 2023 as warm start): R, K placebo draws, scoring. ``y_all`` aligned to frame."""
    out: dict[str, Any] = {}
    groups = np.array(
        [
            f"{a}|{b}"
            for a, b in zip(
                frame["game_date"].cast(pl.Utf8).to_list(), frame["stat"].to_list(), strict=True
            )
        ]
    )
    for stage, season, warm in (
        ("select", rule.SELECT_SEASON, ()),
        ("report", rule.REPORT_SEASON, (rule.SELECT_SEASON,)),
    ):
        _log(f"{stage}: season {season}, R")
        scored, lr, info = walk_forward(frame, y_all, season, warm, verbose=verbose)
        p_r = rule.sigmoid(lr[scored])
        pl_draws = np.empty((k_placebo, int(scored.sum())))
        for k in range(k_placebo):
            perm = rule.within_group_derangement(groups, 1000 + k)
            sc_k, lp, _ = walk_forward(frame, y_all, season, warm, perm=perm)
            assert (sc_k == scored).all()
            pl_draws[k] = rule.sigmoid(lp[scored])
            if (k + 1) % 5 == 0:
                _log(f"{stage}: placebo draw {k + 1}/{k_placebo}")
        d = frame.filter(pl.Series(scored))
        p_h2 = h2_walk_forward(frame, y_all, season, warm, scored) if with_secondary else None
        res = score_stage(
            d, y_all[scored], p_r, pl_draws if k_placebo else None, with_secondary, p_h2
        )
        res["walk_forward"] = info
        out[stage] = res
    return out


def knockout_columns() -> list[int]:
    """Column indices kept by the report-column knockout R_NR (production block + market block minus REPORT_COLUMNS)."""
    names = [*generic_feature_names(), *MARKET_FEATURES]
    return [i for i, n in enumerate(names) if n not in rule.REPORT_COLUMNS]


def knockout_and_dk(
    frame: pl.DataFrame, y_all: np.ndarray, dk_frame: pl.DataFrame | None, dk_y: np.ndarray | None
) -> dict[str, Any]:
    """Secondary, label only: R without the report columns (pooled R_NR minus M), and R on DraftKings legs (direction)."""
    out: dict[str, Any] = {}
    keep = knockout_columns()
    for stage, season, warm in (
        ("select", rule.SELECT_SEASON, ()),
        ("report", rule.REPORT_SEASON, (rule.SELECT_SEASON,)),
    ):
        X = design_matrix(frame)[:, keep]
        scored, lr, _ = walk_forward(frame, y_all, season, warm, X=X)
        d = frame.filter(pl.Series(scored))
        y = y_all[scored]
        v = rule.log_loss_vec(rule.sigmoid(lr[scored]), y) - rule.log_loss_vec(
            d["p_over_mult"].to_numpy().astype(float), y
        )
        cl = {k: rule.cluster_codes(d, k) for k in rule.CLUSTERINGS}
        out.setdefault("R_NR_minus_M", {})[stage] = contrast(v, d, cl)
        if dk_frame is not None and dk_y is not None:
            sc, lr2, _ = walk_forward(dk_frame, dk_y, season, warm)
            d2 = dk_frame.filter(pl.Series(sc))
            y2 = dk_y[sc]
            v2 = rule.log_loss_vec(rule.sigmoid(lr2[sc]), y2) - rule.log_loss_vec(
                d2["p_over_mult"].to_numpy().astype(float), y2
            )
            cl2 = {k: rule.cluster_codes(d2, k) for k in rule.CLUSTERINGS}
            out.setdefault("DK_R_minus_M", {})[stage] = contrast(v2, d2, cl2)
    return out


def decide(res: dict[str, Any]) -> dict[str, Any]:
    sel, rep = res["select"], res["report"]
    s1 = rule.stage1_sign(sel["b_disagreement"]["binding"]["coef"])
    a = rep["a_R_minus_M"]["binding"]
    a = {"point": a["point"], "hi": a["hi"], "lo": a["lo"]}
    b = rep["b_disagreement"]["binding"]
    c = rep["c_R_minus_PL"]["binding"]
    return rule.verdict(
        a, {"coef": b["coef"], "lo": b["lo"]}, {"point": c["point"], "hi": c["hi"]}, s1
    )


def run(sha: str, out_dir: Path = OUT_DIR) -> dict[str, Any]:
    """The scored run. Refuses while DRAFT, without the freeze commit, with a drifted harness, or for any season > 2024."""
    rule.require_frozen(sha)
    pins = rule.require_harness_pinned(sha)
    rule.assert_seasons((rule.SELECT_SEASON, rule.REPORT_SEASON))
    out_dir.mkdir(parents=True, exist_ok=True)
    with Lock("MARKET_PRIOR_RESIDUAL run"):
        legs, counts = load_legs()
        for s, want in PINNED_SHA256.items():
            got = rule.file_sha256(resolve(PAIRS_REL.format(season=s)))
            if got != want:
                raise RuntimeError(f"pairs_{s}.parquet sha256 {got[:8]} != pinned {want[:8]}")
        feats = load_features()
        frame, fcount = build_feature_frame(legs, feats)
        y = frame["over"].to_numpy().astype(float)
        res = run_stages(legs, frame, y, rule.K_PLACEBO)
        dk_legs, _ = load_legs(book="draftkings")
        dk_frame, _ = build_feature_frame(dk_legs, feats)
        res["knockouts"] = knockout_and_dk(
            frame, y, dk_frame, dk_frame["over"].to_numpy().astype(float)
        )
        res["verdict"] = decide(res)
        res["legs_counts"], res["feature_alignment"], res["harness_pin"], res["rule_sha256"] = (
            counts,
            fcount,
            pins,
            sha,
        )
        (out_dir / "results.json").write_text(json.dumps(res, indent=1, default=float))
        _log(f"verdict {res['verdict']['word']}")
        return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--sha", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        run(a.sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
