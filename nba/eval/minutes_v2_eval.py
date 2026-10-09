"""Minutes model v2 evaluation (pre-registration: docs/MINUTES_V2.md). Read-only, seasons <= 2024.

    uv run python -m nba.eval.minutes_v2_eval minutes   # minutes arms + OOF mv2 features
    uv run python -m nba.eval.minutes_v2_eval props     # downstream context_residual arms A0..A3
    uv run python -m nba.eval.minutes_v2_eval pplay     # secondary P(play)
    uv run python -m nba.eval.minutes_v2_eval report    # verdict + reports/minutes_v2.md

Stages checkpoint to ``reports/minutes_v2/`` (scores) and ``data/minutes_v2/`` (parquet caches,
git-ignored) and resume on rerun. ``nba.duckdb`` is opened read-only and closed after loading.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import duckdb
import lightgbm as lgb
import numpy as np
import polars as pl
import yaml

from nba.eval.context_residual_eval import (
    MAX_SEASON,
    TEST_SEASONS,
    bh_adjust,
    boot_p_value,
    crps_from_quantiles,
    load_inputs,
    month_blocks,
)
from nba.eval.lower_tail_eval import arm_scores, attach_realised
from nba.features.game_context import build_standings_features
from nba.props.context_residual import (
    CRPS_TAUS,
    PROP_STATS,
    ContextResidualConfig,
    ContextResidualModel,
    _matrix,
    _params,
    asof_elo_margin,
    build_features,
    flagged_from_availability,
    split_calibration,
    stat_feature_names,
    stat_frame,
    team_game_context,
)
from nba.props.forward import P_PLAY_PLATT
from nba.props.metrics import mean_bias_ci, paired_score_delta_ci
from nba.props.minutes import GAME_CONTEXT_FEATURE_COLUMNS, build_minutes_features, predict_minutes
from nba.props.minutes_v2 import (
    BASE_MINUTES_FEATURES,
    MIN_TRAIN,
    MV2_FEATURES,
    V2_MINUTES_FEATURES,
    MinutesV2Model,
    build_v2_features,
    recency_ratio_quantiles,
    summarize_quantiles,
    trunc_normal_quantiles,
)

OUT_DIR = Path("reports/minutes_v2")
CACHE_DIR = Path("data/minutes_v2")
REPORT_PATH = Path("reports/minutes_v2.md")
SELECT_SEASON, REPORT_SEASON = 2023, 2024
N_BOOT = 2000
MINUTES_FLOOR = -0.10
PROPS_FLOOR = -0.005
BIAS_TOL = 0.5
COVER_LO, COVER_HI = 0.75, 0.85
LOW_PIT_LO, LOW_PIT_HI = 0.08, 0.12
MIN_SLICE_N = 300
MINUTES_SLICE_TOL = 0.05
PROPS_SLICE_TOL = 0.01
MINUTES_THRESHOLDS: tuple[float, ...] = (10.0, 20.0, 30.0)
MINUTES_ARMS: tuple[str, ...] = ("P", "R", "S1", "M1", "M2")
PROPS_ARMS: tuple[str, ...] = ("A0", "A1", "A2", "A3")
EPS = 0.5 / 199
COLD_N_PRIOR = 20
ROW_META: tuple[str, ...] = (
    "n_prior",
    "starter10",
    "n_out_rot",
    "has_report",
    "team_game_no",
    "b2b",
    "ret_n",
    "min10",
    "starter_act",
)


def _savez(path: Path, data: dict[str, Any]) -> None:
    np.savez_compressed(path, **data)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- world


def load_world(
    db_path: str = "nba.duckdb",
    injury_db: str | None = None,
    elo_config: str = "configs/mov_elo_tuned.yaml",
    config_path: str = "configs/context_residual.yaml",
) -> dict[str, Any]:
    """Frames and the in-memory scratch DB (season <= 2024 only); nba.duckdb is closed on return."""
    elo_params = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    cfg = ContextResidualConfig(**yaml.safe_load(Path(config_path).read_text()).get("model", {}))
    games, pgs, static, avail = load_inputs(db_path, injury_db)
    if int(games["season"].max()) > MAX_SEASON:  # type: ignore[arg-type]
        raise ValueError("season > 2024 present: frozen holdout must not be loaded")
    con = duckdb.connect(db_path, read_only=True)
    sc = duckdb.connect(":memory:")
    try:
        pf = con.execute(
            "SELECT s.game_id, s.player_id, s.pf FROM player_game_stats s JOIN games g "
            f"USING (game_id) WHERE g.season <= {MAX_SEASON}"
        ).pl()
        static = con.execute("SELECT player_id, position, birth_date FROM players_static").pl()
        copies = {
            "games": f"SELECT * FROM games WHERE season <= {MAX_SEASON}",
            "player_game_stats": (
                "SELECT s.* FROM player_game_stats s JOIN games g USING (game_id) "
                f"WHERE g.season <= {MAX_SEASON}"
            ),
            "team_context": (
                "SELECT t.* FROM team_context t JOIN games g USING (game_id) "
                f"WHERE g.season <= {MAX_SEASON}"
            ),
            "players_static": "SELECT * FROM players_static",
        }
        for name, sql in copies.items():
            df = con.execute(sql).pl()
            sc.register("_src", df)
            sc.execute(f"CREATE TABLE {name} AS SELECT * FROM _src")
            sc.unregister("_src")
    finally:
        con.close()
    pgs = pgs.join(pf, on=["game_id", "player_id"], how="left")
    flagged, _ = flagged_from_availability(avail, games, cfg.report)
    return {
        "games": games,
        "pgs": pgs,
        "static": static,
        "flagged": flagged,
        "sc": sc,
        "elo_params": elo_params,
        "cfg": cfg,
    }


def build_frame(world: dict[str, Any]) -> pl.DataFrame:
    """Production features + V2 group + realised labels (labels are never model inputs)."""
    games, pgs, static = world["games"], world["pgs"], world["static"]
    feats = build_features(games, pgs, static, world["flagged"], world["elo_params"])
    standings = build_standings_features(world["sc"])
    v2 = build_v2_features(feats, games, pgs, static, world["flagged"], standings)
    f = feats.join(v2, on=["game_id", "player_id"], how="left")
    return attach_realised(f, games, pgs)


def production_minutes_table(sc: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Production minutes path per (game, player): shrunk trailing-150-game mu/sigma and p_play."""
    mf = build_minutes_features(sc, use_game_context=False)
    dists = predict_minutes(mf)
    return mf.select("game_id", "player_id").with_columns(
        pl.Series("p_mu", [d.mu for d in dists], dtype=pl.Float64),
        pl.Series("p_sigma", [d.sigma for d in dists], dtype=pl.Float64),
        pl.Series("p_play_raw", [d.p_play for d in dists], dtype=pl.Float64),
    )


# --------------------------------------------------------------------------- scoring


def pit_continuous(q: np.ndarray, y: np.ndarray, seed: int = 0) -> np.ndarray:
    """Randomized PIT on an equal-mass grid for a continuous outcome."""
    rng = np.random.default_rng(seed)
    k = q.shape[1]
    below = (q < y[:, None]).sum(axis=1)
    return np.asarray(np.minimum((below + rng.random(len(y))) / k, 1.0))


def _ll(p: np.ndarray, ev: np.ndarray) -> np.ndarray:
    pc = np.clip(p, EPS, 1.0 - EPS)
    return np.asarray(-np.where(ev, np.log(pc), np.log(1.0 - pc)))


def score_minutes(q: np.ndarray, y: np.ndarray) -> dict[str, np.ndarray]:
    i10, i90 = (int(np.argmin(np.abs(CRPS_TAUS - t))) for t in (0.1, 0.9))
    ll = np.mean([_ll((q >= n).mean(axis=1), y >= n) for n in MINUTES_THRESHOLDS], axis=0)
    return {
        "crps": crps_from_quantiles(q, y),
        "pit": pit_continuous(q, y),
        "mean": q.mean(axis=1),
        "q10": q[:, i10],
        "q90": q[:, i90],
        "tll": ll,
    }


# --------------------------------------------------------------------------- stage: minutes


def run_minutes(world: dict[str, Any], frame: pl.DataFrame, out_dir: Path = OUT_DIR) -> None:
    cfg: ContextResidualConfig = world["cfg"]
    prod = production_minutes_table(world["sc"])
    sf = stat_frame(frame, "pts").sort(["game_date", "game_id", "player_id"])
    sf = sf.join(prod, on=["game_id", "player_id"], how="left")
    keep_meta = [c for c in ROW_META if c in sf.columns]
    acc: dict[str, list[np.ndarray]] = {}
    mv2_parts: list[pl.DataFrame] = []

    def put(key: str, val: np.ndarray) -> None:
        acc.setdefault(key, []).append(np.asarray(val))

    blocks = month_blocks(sf)
    for bi, (start, end) in enumerate(blocks):
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end))
        train = sf.filter(pl.col("game_date") < start)
        if block.is_empty() or train.height < MIN_TRAIN:
            continue
        m2 = MinutesV2Model(list(V2_MINUTES_FEATURES), "mixture", cfg).fit(train)
        q2 = m2.quantiles(block)
        pi2 = m2.pi(block)
        mv2_parts.append(
            pl.concat(
                [block.select("game_id", "player_id"), summarize_quantiles(q2, pi2)],
                how="horizontal",
            )
        )
        msk = block["season"].is_in(list(TEST_SEASONS)).to_numpy()
        if not msk.any():
            _log(f"block {bi + 1}/{len(blocks)} {start} warm-up only")
            continue
        tst = block.filter(pl.Series(msk))
        y = tst["minutes"].cast(pl.Float64).to_numpy()
        _, cal_df = split_calibration(train, cfg)
        mu = tst["p_mu"].to_numpy()
        sg = tst["p_sigma"].to_numpy()
        if np.isnan(mu).any() or np.isnan(sg).any():
            raise ValueError("production minutes table is missing test rows")
        arms = {
            "P": trunc_normal_quantiles(mu, sg),
            "R": recency_ratio_quantiles(
                cal_df["minutes"].to_numpy(), cal_df["min10"].to_numpy(), tst["min10"].to_numpy()
            ),
            "S1": MinutesV2Model(list(V2_MINUTES_FEATURES), "single", cfg)
            .fit(train)
            .quantiles(tst),
            "M1": MinutesV2Model(list(BASE_MINUTES_FEATURES), "mixture", cfg)
            .fit(train)
            .quantiles(tst),
            "M2": q2[msk],
        }
        for arm, q in arms.items():
            for k, v in score_minutes(q, y).items():
                put(f"{arm}_{k}", v)
        put("row_gid", tst["game_id"].to_numpy())
        put("row_pid", tst["player_id"].to_numpy())
        put("row_season", tst["season"].to_numpy())
        put("row_y", y)
        put("row_pi", pi2[msk])
        put("row_blk", np.full(tst.height, bi))
        for c in keep_meta:
            put(f"row_{c}", tst[c].cast(pl.Float64).to_numpy())
        _log(f"block {bi + 1}/{len(blocks)} {start} n_test={tst.height}")
    out_dir.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _savez(out_dir / "minutes_scores.npz", {k: np.concatenate(v) for k, v in acc.items()})
    pl.concat(mv2_parts).write_parquet(CACHE_DIR / "mv2.parquet")
    _log("minutes stage done")


# --------------------------------------------------------------------------- stage: props


def permute_within_season(
    frame: pl.DataFrame, cols: tuple[str, ...], seed: int = 0
) -> pl.DataFrame:
    """Jointly permute ``cols`` across rows within each season (noise control)."""
    rng = np.random.default_rng(seed)
    arr = frame.select(list(cols)).to_numpy().copy()
    season = frame["season"].to_numpy()
    for s in np.unique(season):
        idx = np.where(season == s)[0]
        arr[idx] = arr[rng.permutation(idx)]
    return frame.with_columns([pl.Series(c, arr[:, i]) for i, c in enumerate(cols)])


def props_walk(
    frame: pl.DataFrame,
    stat: str,
    extra: tuple[str, ...],
    cfg: ContextResidualConfig,
    test_seasons: tuple[int, ...] = TEST_SEASONS,
) -> dict[str, np.ndarray]:
    """Production walk-forward (month blocks) for one stat; per-row scores for the test rows."""
    if max(test_seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    sf = stat_frame(frame, stat)
    names = stat_feature_names(stat, extra=extra)
    test_all = sf.filter(pl.col("season").is_in(list(test_seasons)))
    acc: dict[str, list[np.ndarray]] = {}
    meta = [c for c in ROW_META if c in sf.columns]
    for start, end in month_blocks(test_all):
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(test_seasons))
        )
        train = sf.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train.height < 1500:
            continue
        model = ContextResidualModel(stat, cfg, names).fit(train)
        mean, q = model.predict(block, CRPS_TAUS)
        y = block["y"].to_numpy()
        sc = arm_scores(q, y, stat)
        for k, v in sc.items():
            acc.setdefault(k, []).append(np.asarray(v))
        acc.setdefault("mean", []).append(mean)
        acc.setdefault("gid", []).append(block["game_id"].to_numpy())
        acc.setdefault("pid", []).append(block["player_id"].to_numpy())
        acc.setdefault("season", []).append(block["season"].to_numpy())
        acc.setdefault("y", []).append(y)
        for c in meta:
            acc.setdefault(f"meta_{c}", []).append(block[c].cast(pl.Float64).to_numpy())
    return {k: np.concatenate(v) for k, v in acc.items()}


def run_props(
    frame: pl.DataFrame,
    cfg: ContextResidualConfig,
    out_dir: Path = OUT_DIR,
    stats: tuple[str, ...] = PROP_STATS,
) -> None:
    mv2 = pl.read_parquet(CACHE_DIR / "mv2.parquet")
    fm = frame.join(mv2, on=["game_id", "player_id"], how="left")
    fa2 = permute_within_season(fm, MV2_FEATURES)
    fa3 = frame.with_columns(pl.col("minutes").alias("minutes_oracle"))
    arms: dict[str, tuple[pl.DataFrame, tuple[str, ...]]] = {
        "A0": (frame, ()),
        "A1": (fm, MV2_FEATURES),
        "A2": (fa2, MV2_FEATURES),
        "A3": (fa3, ("minutes_oracle",)),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    for stat in stats:
        for arm, (fr, extra) in arms.items():
            path = out_dir / f"props_{stat}_{arm}.npz"
            if path.exists():
                continue
            res = props_walk(fr, stat, extra, cfg)
            _savez(path, res)
            _log(f"props {stat} {arm} n={len(res['y'])}")


# --------------------------------------------------------------------------- stage: pplay


def ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    tot = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            tot += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(tot)


def run_pplay(world: dict[str, Any], out_dir: Path = OUT_DIR) -> None:
    cfg: ContextResidualConfig = world["cfg"]
    games, pgs, static = world["games"], world["pgs"], world["static"]
    mf = build_minutes_features(world["sc"], use_game_context=True)
    dists = predict_minutes(mf)
    p_raw = np.array([d.p_play for d in dists])
    mf = mf.with_columns(pl.Series("p_raw", p_raw))
    lab = pgs.select(
        "game_id",
        "player_id",
        (pl.col("minutes") > 0).fill_null(False).cast(pl.Float64).alias("played"),
    )
    mf = mf.join(lab, on=["game_id", "player_id"], how="inner").sort(
        ["player_id", "game_date", "game_id"]
    )
    # lagged label features over ALL of the player's earlier team-box rows (before any filtering)
    mf = mf.with_columns(
        (1.0 - pl.col("played"))
        .shift(1)
        .rolling_sum(5, min_samples=1)
        .over("player_id")
        .alias("dnp_last5"),
        pl.when(pl.col("played") == 1.0)
        .then(pl.col("game_date"))
        .otherwise(None)
        .shift(1)
        .over("player_id")
        .alias("_lp"),
    ).with_columns(pl.col("_lp").forward_fill().over("player_id").alias("_lp"))
    mf = mf.with_columns(
        (pl.col("game_date") - pl.col("_lp"))
        .dt.total_days()
        .cast(pl.Float64)
        .alias("days_since_played")
    ).drop("_lp")
    out = [(g, p) for g, ps in world["flagged"].items() for p in ps]
    out_df = pl.DataFrame(
        out, schema={"game_id": pl.Utf8, "player_id": pl.Int64}, orient="row"
    ).with_columns(pl.lit(1).alias("_out"))
    mf = (
        mf.join(out_df, on=["game_id", "player_id"], how="left")
        .filter(pl.col("_out").is_null())
        .filter(pl.col("n_played_prior") >= 5)
    )
    elo = asof_elo_margin(games, world["elo_params"])
    tctx = team_game_context(games, pgs, elo).select("game_id", "team_id", "exp_margin")
    mf = mf.join(tctx, on=["game_id", "team_id"], how="left")
    mf = mf.join(
        static.select("player_id", "birth_date").unique("player_id"), on="player_id", how="left"
    )
    mf = mf.with_columns(
        ((pl.col("game_date") - pl.col("birth_date").cast(pl.Date)).dt.total_days() / 365.25).alias(
            "age"
        )
    ).sort(["game_date", "game_id", "player_id"])
    names = [
        "games_played_prior",
        "n_played_prior",
        "play_rate_prior",
        "avg_minutes_given_played_prior",
        "std_minutes_given_played_prior",
        "starter_rate_prior",
        *GAME_CONTEXT_FEATURE_COLUMNS,
        "age",
        "exp_margin",
        "dnp_last5",
        "days_since_played",
    ]
    platt_a, platt_b = P_PLAY_PLATT
    rp = np.clip(mf["p_raw"].to_numpy(), 1e-3, 1 - 1e-3)
    mf = mf.with_columns(
        pl.Series("p_platt", 1.0 / (1.0 + np.exp(-(platt_a + platt_b * np.log(rp / (1.0 - rp))))))
    )
    test_all = mf.filter(pl.col("season").is_in(list(TEST_SEASONS)))
    acc: dict[str, list[np.ndarray]] = {}
    for start, end in month_blocks(test_all):
        block = mf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(TEST_SEASONS))
        )
        train = mf.filter(pl.col("game_date") < start)
        if block.is_empty() or train.height < MIN_TRAIN:
            continue
        clf = lgb.LGBMClassifier(**_params(cfg, "binary")).fit(  # type: ignore[arg-type]
            _matrix(train, names), train["played"].to_numpy().astype(int)
        )
        p2 = np.asarray(clf.predict_proba(_matrix(block, names)))[:, 1]
        for k, vals in {
            "gid": block["game_id"].to_numpy(),
            "season": block["season"].to_numpy(),
            "y": block["played"].to_numpy(),
            "p_raw": block["p_raw"].to_numpy(),
            "p_platt": block["p_platt"].to_numpy(),
            "p_v2": p2,
        }.items():
            acc.setdefault(k, []).append(np.asarray(vals))
    out_dir.mkdir(parents=True, exist_ok=True)
    _savez(out_dir / "pplay.npz", {k: np.concatenate(v) for k, v in acc.items()})
    _log("pplay stage done")


# --------------------------------------------------------------------------- analysis


def _ci(delta: np.ndarray, gid: np.ndarray, n_boot: int = N_BOOT) -> dict[str, float]:
    c = paired_score_delta_ci(delta, np.zeros_like(delta), n_boot=n_boot, cluster_ids=gid)
    return {"point": float(c.point), "lo": float(c.lo), "hi": float(c.hi)}


def _bias(
    mean: np.ndarray, y: np.ndarray, gid: np.ndarray, n_boot: int = N_BOOT
) -> dict[str, float]:
    c = mean_bias_ci(mean, y, n_boot=n_boot, cluster_ids=gid)
    return {"point": float(c.point), "lo": float(c.lo), "hi": float(c.hi)}


def minutes_slices(z: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    n = len(z["row_y"])
    out: dict[str, np.ndarray] = {"all": np.ones(n, dtype=bool)}
    out["cold(n_prior<20)"] = z["row_n_prior"] < COLD_N_PRIOR
    out["warm"] = z["row_n_prior"] >= COLD_N_PRIOR
    out["starter"] = z["row_starter10"] >= 0.5
    out["bench"] = z["row_starter10"] < 0.5
    out["first15"] = z["row_team_game_no"] < 15
    out["rest_of_season"] = z["row_team_game_no"] >= 15
    out["teammate_out"] = (z["row_has_report"] == 1) & (z["row_n_out_rot"] > 0)
    out["b2b"] = z["row_b2b"] > 0.5
    out["return(ret_n==0)"] = z["row_ret_n"] == 0
    return out


def analyze_minutes(out_dir: Path = OUT_DIR, n_boot: int = N_BOOT) -> dict[str, Any]:
    z = {k: v for k, v in np.load(out_dir / "minutes_scores.npz", allow_pickle=True).items()}
    sl = minutes_slices(z)
    res: dict[str, Any] = {}
    for season in (SELECT_SEASON, REPORT_SEASON):
        sm = z["row_season"] == season
        gid, y = z["row_gid"][sm], z["row_y"][sm]
        r: dict[str, Any] = {"n": int(sm.sum()), "n_games": int(len(np.unique(gid))), "arms": {}}
        for arm in MINUTES_ARMS:
            pit = z[f"{arm}_pit"][sm]
            r["arms"][arm] = {
                "crps": float(z[f"{arm}_crps"][sm].mean()),
                "tll": float(z[f"{arm}_tll"][sm].mean()),
                "bias": _bias(z[f"{arm}_mean"][sm], y, gid, n_boot),
                "cover80": float(((pit > 0.1) & (pit <= 0.9)).mean()),
                "pit_le_0.10": float((pit <= 0.10).mean()),
                "pit_ge_0.90": float((pit > 0.90).mean()),
                "interval80": float(
                    ((y >= z[f"{arm}_q10"][sm]) & (y <= z[f"{arm}_q90"][sm])).mean()
                ),
            }
        comps = {}
        for other in ("P", "R", "M1", "S1"):
            d = z["M2_crps"][sm] - z[f"{other}_crps"][sm]
            dl = z["M2_tll"][sm] - z[f"{other}_tll"][sm]
            comps[other] = {
                "d_crps": _ci(d, gid, n_boot),
                "p": boot_p_value(d, gid, n_boot),
                "d_tll": _ci(dl, gid, n_boot),
            }
        adj = bh_adjust(np.array([comps["P"]["p"], comps["R"]["p"]]))
        comps["P"]["p_bh"], comps["R"]["p_bh"] = float(adj[0]), float(adj[1])
        r["M2_vs"] = comps
        slices = {}
        for name, m in sl.items():
            mm = m & sm
            if mm.sum() == 0:
                continue
            slices[name] = {
                "n": int(mm.sum()),
                "M2_crps": float(z["M2_crps"][mm].mean()),
                "d_vs_P": float((z["M2_crps"][mm] - z["P_crps"][mm]).mean()),
                "d_vs_R": float((z["M2_crps"][mm] - z["R_crps"][mm]).mean()),
            }
        r["slices"] = slices
        pi, short = z["row_pi"][sm], z["row_y"][sm] < 0.5 * z["row_min10"][sm]
        r["pi"] = {
            "mean_pi": float(pi.mean()),
            "short_rate": float(short.mean()),
            "auc": _auc(pi, short),
        }
        res[str(season)] = r
    return res


def _auc(score: np.ndarray, label: np.ndarray) -> float:
    from scipy.stats import rankdata

    pos = label.astype(bool)
    n1, n0 = int(pos.sum()), int((~pos).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    ranks = rankdata(score)
    return float((ranks[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def minutes_gate(season_res: dict[str, Any]) -> dict[str, Any]:
    a = season_res["arms"]["M2"]
    vs = season_res["M2_vs"]
    checks = {
        "floor_vs_P": vs["P"]["d_crps"]["point"] <= MINUTES_FLOOR,
        "ci_vs_P": vs["P"]["d_crps"]["hi"] < 0,
        "bh_vs_P": vs["P"]["p_bh"] < 0.05,
        "point_vs_R": vs["R"]["d_crps"]["point"] < 0,
        "ci_vs_R": vs["R"]["d_crps"]["hi"] < 0,
        "bh_vs_R": vs["R"]["p_bh"] < 0.05,
        "bias": abs(a["bias"]["point"]) <= BIAS_TOL,
        "cover80": COVER_LO <= a["cover80"] <= COVER_HI,
        "pit_low10": LOW_PIT_LO <= a["pit_le_0.10"] <= LOW_PIT_HI,
    }
    bad = [
        n
        for n, s in season_res["slices"].items()
        if s["n"] >= MIN_SLICE_N
        and (s["d_vs_P"] > MINUTES_SLICE_TOL or s["d_vs_R"] > MINUTES_SLICE_TOL)
    ]
    checks["slices"] = not bad
    return {"checks": checks, "bad_slices": bad, "pass": all(checks.values())}


def props_slices(m: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {
        "cold(n_prior<20)": m["meta_n_prior"] < COLD_N_PRIOR,
        "starter": m["meta_starter10"] >= 0.5,
        "bench": m["meta_starter10"] < 0.5,
        "teammate_out": (m["meta_has_report"] == 1) & (m["meta_n_out_rot"] > 0),
        "first15": m["meta_team_game_no"] < 15,
    }


def analyze_props(out_dir: Path = OUT_DIR, n_boot: int = N_BOOT) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for stat in PROP_STATS:
        arms = {
            a: dict(np.load(out_dir / f"props_{stat}_{a}.npz", allow_pickle=True))
            for a in PROPS_ARMS
        }
        base = arms["A0"]
        for a in PROPS_ARMS[1:]:
            if not (
                np.array_equal(base["gid"], arms[a]["gid"])
                and np.array_equal(base["pid"], arms[a]["pid"])
            ):
                raise ValueError("props arms are not row-aligned")
        sls = props_slices(base)
        r: dict[str, Any] = {}
        for season in (SELECT_SEASON, REPORT_SEASON):
            sm = base["season"] == season
            gid, y = base["gid"][sm], base["y"][sm]
            s: dict[str, Any] = {
                "n": int(sm.sum()),
                "n_games": int(len(np.unique(gid))),
                "arms": {},
            }
            for a in PROPS_ARMS:
                pit = arms[a]["pit_int"][sm]
                s["arms"][a] = {
                    "crps_int": float(arms[a]["crps_int"][sm].mean()),
                    "crps_cont": float(arms[a]["crps_cont"][sm].mean()),
                    "tll_low": float(arms[a]["tll_low"][sm].mean()),
                    "bias": _bias(arms[a]["mean"][sm], y, gid, n_boot),
                    "cover80": float(((pit > 0.1) & (pit <= 0.9)).mean()),
                }
            for a in PROPS_ARMS[1:]:
                d = arms[a]["crps_int"][sm] - base["crps_int"][sm]
                dc = arms[a]["crps_cont"][sm] - base["crps_cont"][sm]
                dt_ = arms[a]["tll_low"][sm] - base["tll_low"][sm]
                s[f"{a}_vs_A0"] = {
                    "d_crps_int": _ci(d, gid, n_boot),
                    "p": boot_p_value(d, gid, n_boot),
                    "d_crps_cont": _ci(dc, gid, n_boot),
                    "d_tll_low": _ci(dt_, gid, n_boot),
                    "slices": {
                        name: {
                            "n": int((m & sm).sum()),
                            "d": float(d[m[sm]].mean()) if (m & sm).any() else float("nan"),
                        }
                        for name, m in sls.items()
                    },
                }
            r[str(season)] = s
        res[stat] = r
    for season in (SELECT_SEASON, REPORT_SEASON):
        adj = bh_adjust(np.array([res[st][str(season)]["A1_vs_A0"]["p"] for st in PROP_STATS]))
        for st, adj_p in zip(PROP_STATS, adj, strict=True):
            res[st][str(season)]["A1_vs_A0"]["p_bh"] = float(adj_p)
    return res


def props_gate(season_res: dict[str, Any]) -> dict[str, Any]:
    v = season_res["A1_vs_A0"]
    a1 = season_res["arms"]["A1"]
    checks = {
        "floor": v["d_crps_int"]["point"] <= PROPS_FLOOR,
        "ci_below_0": v["d_crps_int"]["hi"] < 0,
        "bh_p": v["p_bh"] < 0.05,
        "bias": abs(a1["bias"]["point"]) <= BIAS_TOL,
        "cover80": COVER_LO <= a1["cover80"] <= COVER_HI,
    }
    bad = [n for n, s in v["slices"].items() if s["n"] >= MIN_SLICE_N and s["d"] > PROPS_SLICE_TOL]
    checks["slices"] = not bad
    return {"checks": checks, "bad_slices": bad, "pass": all(checks.values())}


def analyze_pplay(out_dir: Path = OUT_DIR, n_boot: int = N_BOOT) -> dict[str, Any]:
    z = dict(np.load(out_dir / "pplay.npz", allow_pickle=True))
    res: dict[str, Any] = {}
    for season in (SELECT_SEASON, REPORT_SEASON):
        sm = z["season"] == season
        gid, y = z["gid"][sm], z["y"][sm].astype(bool)
        ll = {k: _ll(z[k][sm], y) for k in ("p_raw", "p_platt", "p_v2")}
        r: dict[str, Any] = {"n": int(sm.sum()), "played_rate": float(y.mean()), "arms": {}}
        for k in ll:
            r["arms"][k] = {
                "logloss": float(ll[k].mean()),
                "brier": float(((z[k][sm] - y) ** 2).mean()),
                "ece": ece(z[k][sm], y.astype(float)),
                "mean_p": float(z[k][sm].mean()),
            }
        r["v2_vs_raw"] = _ci(ll["p_v2"] - ll["p_raw"], gid, n_boot)
        r["v2_vs_platt"] = _ci(ll["p_v2"] - ll["p_platt"], gid, n_boot)
        res[str(season)] = r
    return res


def verdict(mins: dict[str, Any], props: dict[str, Any]) -> dict[str, Any]:
    mg = {s: minutes_gate(mins[s]) for s in (str(SELECT_SEASON), str(REPORT_SEASON))}
    pg = {
        stat: {s: props_gate(props[stat][s]) for s in (str(SELECT_SEASON), str(REPORT_SEASON))}
        for stat in PROP_STATS
    }
    minutes_pass = all(g["pass"] for g in mg.values())
    stats_pass = [st for st in PROP_STATS if all(g["pass"] for g in pg[st].values())]
    if minutes_pass and stats_pass:
        v = "PASS"
    elif minutes_pass:
        v = "MINUTES-ONLY"
    else:
        v = "FAIL"
    return {
        "verdict": v,
        "minutes_gate": mg,
        "props_gate": pg,
        "stats_passing_both_seasons": stats_pass,
    }


# --------------------------------------------------------------------------- report


def _f(x: float, nd: int = 4) -> str:
    return f"{x:+.{nd}f}"


def _ci_txt(c: dict[str, float], nd: int = 4) -> str:
    return f"{_f(c['point'], nd)} [{_f(c['lo'], nd)},{_f(c['hi'], nd)}]"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def render(
    mins: dict[str, Any], props: dict[str, Any], pplay: dict[str, Any] | None, vd: dict[str, Any]
) -> str:
    seasons = (str(SELECT_SEASON), str(REPORT_SEASON))
    out: list[str] = ["# Minutes model v2: results (docs/MINUTES_V2.md)", ""]
    sp = vd["stats_passing_both_seasons"] or "none"
    out += [f"Verdict: **{vd['verdict']}**. Stats passing both seasons: {sp}.", ""]
    out += ["## Minutes given plays (continuous CRPS in minutes; lower is better)", ""]
    rows = []
    for s in seasons:
        r = mins[s]
        for arm in MINUTES_ARMS:
            a = r["arms"][arm]
            b = a["bias"]
            rows.append(
                [
                    s,
                    f"{r['n']} ({r['n_games']})",
                    arm,
                    f"{a['crps']:.4f}",
                    f"{b['point']:+.3f} [{b['lo']:+.3f},{b['hi']:+.3f}]",
                    f"{a['cover80']:.3f}",
                    f"{a['pit_le_0.10']:.3f}",
                    f"{a['tll']:.4f}",
                ]
            )
    out += _table(
        ["season", "n (games)", "arm", "CRPS", "bias [CI]", "80% cover", "PIT<=.10", "TLL"], rows
    )
    out += [
        "",
        "M2 minus comparator (negative = M2 better); game-clustered 95% CI; BH over {P, R}:",
        "",
    ]
    rows = []
    for s in seasons:
        for o in ("P", "R", "M1", "S1"):
            c = mins[s]["M2_vs"][o]
            pb = f"{c['p']:.3f}" + (f" ({c['p_bh']:.3f})" if "p_bh" in c else "")
            rows.append([s, o, _ci_txt(c["d_crps"]), pb, _ci_txt(c["d_tll"])])
    out += _table(["season", "vs", "dCRPS [CI]", "p (BH)", "dTLL [CI]"], rows)
    out += ["", "Slices (M2 mean paired dCRPS in minutes; n / vs P / vs R):", ""]
    rows = []
    for n in mins[seasons[0]]["slices"]:
        cells = []
        for s in seasons:
            sl = mins[s]["slices"].get(n)
            cells.append(
                "-" if sl is None else f"{sl['n']} / {sl['d_vs_P']:+.3f} / {sl['d_vs_R']:+.3f}"
            )
        rows.append([n, *cells])
    out += _table(["slice", *seasons], rows)
    pis = "; ".join(
        f"{s}: AUC {mins[s]['pi']['auc']:.3f}, mean pi {mins[s]['pi']['mean_pi']:.3f} "
        f"vs observed {mins[s]['pi']['short_rate']:.3f}"
        for s in seasons
    )
    out += ["", f"Short-minutes classifier pi: {pis}", "", "Minutes gate checks:", ""]
    for s, g in vd["minutes_gate"].items():
        chk = ", ".join(f"{k}={'ok' if ok else 'FAIL'}" for k, ok in g["checks"].items())
        bad = f"; bad slices {g['bad_slices']}" if g["bad_slices"] else ""
        out.append(f"* {s}: pass={g['pass']}; {chk}{bad}")
    out += ["", "## Props (integer-support CRPS; A1 = production + mv2 features)", ""]
    rows = []
    for stat in PROP_STATS:
        for s in seasons:
            r = props[stat][s]
            v1 = r["A1_vs_A0"]
            gate = vd["props_gate"][stat][s]
            fails = ",".join(k for k, ok in gate["checks"].items() if not ok)
            rows.append(
                [
                    stat,
                    s,
                    str(r["n"]),
                    f"{r['arms']['A0']['crps_int']:.4f}",
                    _ci_txt(v1["d_crps_int"]),
                    f"{v1['p']:.3f} ({v1['p_bh']:.3f})",
                    _f(r["A2_vs_A0"]["d_crps_int"]["point"]),
                    _ci_txt(r["A3_vs_A0"]["d_crps_int"]),
                    f"{r['arms']['A1']['bias']['point']:+.3f}",
                    f"{r['arms']['A1']['cover80']:.3f}",
                    "PASS" if gate["pass"] else f"no ({fails})",
                ]
            )
    out += _table(
        [
            "stat",
            "season",
            "n",
            "A0 CRPS",
            "A1-A0 dCRPS [CI]",
            "p (BH)",
            "A2 noise",
            "A3 oracle [CI]",
            "A1 bias",
            "A1 cover80",
            "gate",
        ],
        rows,
    )
    if pplay:
        out += ["", "## Secondary: P(play) (no pass/fail)", ""]
        rows = []
        for s in seasons:
            r = pplay[s]
            for k, a in r["arms"].items():
                rows.append(
                    [
                        s,
                        str(r["n"]),
                        k,
                        f"{a['logloss']:.4f}",
                        f"{a['brier']:.4f}",
                        f"{a['ece']:.4f}",
                        f"{a['mean_p']:.3f} (played {r['played_rate']:.3f})",
                    ]
                )
            rows.append([s, "", "v2 - raw logloss", _ci_txt(r["v2_vs_raw"]), "", "", ""])
            rows.append([s, "", "v2 - platt logloss", _ci_txt(r["v2_vs_platt"]), "", "", ""])
        out += _table(["season", "n", "arm", "log loss", "Brier", "ECE", "mean p"], rows)
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["minutes", "props", "pplay", "report", "all"])
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--injury-db", default=None)
    ap.add_argument("--out", default=str(OUT_DIR))
    a = ap.parse_args(argv)
    out = Path(a.out)
    if a.stage in ("minutes", "props", "pplay", "all"):
        world = load_world(a.db, a.injury_db)
        try:
            if a.stage in ("minutes", "props", "all"):
                frame = build_frame(world)
            if a.stage in ("minutes", "all"):
                run_minutes(world, frame, out)
            if a.stage in ("props", "all"):
                run_props(frame, world["cfg"], out)
            if a.stage in ("pplay", "all"):
                run_pplay(world, out)
        finally:
            world["sc"].close()
    if a.stage in ("report", "all"):
        mins, props = analyze_minutes(out), analyze_props(out)
        pplay = analyze_pplay(out) if (out / "pplay.npz").exists() else None
        vd = verdict(mins, props)
        (out / "results.json").write_text(
            json.dumps({"minutes": mins, "props": props, "pplay": pplay, "verdict": vd}, indent=1)
        )
        REPORT_PATH.write_text(render(mins, props, pplay, vd))
        print(vd["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
