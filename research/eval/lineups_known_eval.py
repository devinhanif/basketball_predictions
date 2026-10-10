"""T-30 ("lineups known") vs T-60 value of confirmed starters for props.

PRE-REGISTERED in docs/LINEUPS_KNOWN.md before any result was computed. This module
only implements that document; the rule is not changed after seeing results.

LEAK WARNING: arm B uses tonight's starters (``nba.props.lineup_features``), which are
not known at the production T-60 prediction time. This is a different prediction time,
never a production feature set. Season 2025 is never loaded (SQL filter season <= 2024).

* Arm A: production context-residual at T-60 (walk-forward month blocks, conformal
  calibration on the newest 15% of the training window).
* Arm B: the same code, seed, rows and calibration with the five ``t30_*`` features.
* Arm Bs (sensitivity, descriptive): arm B with a synthetic late-scratch process applied to
  the T-30 proxy (``SCRATCH_PROB``) in train and test.
* P(play): production minutes-model P(play) -> walk-forward logistic recalibration
  (A_cal) vs the same logistic plus the T-30 features (B).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import yaml
from sklearn.linear_model import LogisticRegression

from nba.eval.context_residual_eval import (
    CRPS_FLOOR,
    MAX_SEASON,
    N_BOOT,
    SLICE_MAX_REGRESSION,
    SLICE_MIN_N,
    add_slice_columns,
    bh_adjust,
    boot_p_value,
    crps_from_quantiles,
    load_inputs,
    month_blocks,
)
from nba.props.context_residual import (
    CRPS_TAUS,
    PROP_STATS,
    QUANTILE_TAUS,
    ContextResidualConfig,
    ContextResidualModel,
    build_features,
    flagged_from_availability,
    stat_feature_names,
    stat_frame,
)
from nba.props.lineup_features import T30_FEATURES, build_lineup_features
from nba.props.metrics import interval_coverage, mean_bias_ci, paired_score_delta_ci

SCRATCH_PROB = 0.05
TEST_SEASONS: tuple[int, ...] = (2023, 2024)
REPORT_SEASON = 2024
COV_TOL = 0.03
BIAS_MAX = 0.5
BIAS_WORSEN_MAX = 0.05


# --------------------------------------------------------------------------- slices


def add_t30_slices(df: pl.DataFrame) -> pl.DataFrame:
    df = add_slice_columns(df)
    return df.with_columns(
        pl.when(pl.col("t30_starter") >= 0.5)
        .then(pl.lit("starter_tonight"))
        .otherwise(pl.lit("bench_tonight"))
        .alias("sl_start_tonight"),
        pl.when(pl.col("t30_start_delta").is_null())
        .then(pl.lit("unknown"))
        .when(pl.col("t30_start_delta") > 0)
        .then(pl.lit("promoted"))
        .when(pl.col("t30_start_delta") < 0)
        .then(pl.lit("demoted"))
        .otherwise(pl.lit("unchanged"))
        .alias("sl_status"),
    )


SLICE_COLS = ("sl_tm_out", "sl_min_change", "sl_phase", "sl_role", "sl_start_tonight", "sl_status")


# --------------------------------------------------------------------------- walk-forward


def walk_forward_arms(
    feats: pl.DataFrame,
    stat: str,
    cfg: ContextResidualConfig,
    test_seasons: tuple[int, ...],
    feats_scratch: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, dict[str, float]]:
    """Predict every test row with arms A and B (and Bs) from models fit strictly before
    its month block. All arms see the same rows and the same conformal split."""
    sf = stat_frame(feats, stat)
    sfs = stat_frame(feats_scratch, stat) if feats_scratch is not None else None
    names_a = stat_feature_names(stat)
    names_b = stat_feature_names(stat, lineups_known=True)
    test_all = sf.filter(pl.col("season").is_in(list(test_seasons)))
    parts: list[pl.DataFrame] = []
    imps: list[dict[str, float]] = []
    for start, end in month_blocks(test_all):
        in_test = pl.col("season").is_in(list(test_seasons))
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end) & in_test)
        train = sf.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train.height < 1500:
            continue
        y = block["y"].to_numpy()
        out = block.select(
            "game_id",
            "player_id",
            "game_date",
            "season",
            "y",
            "m",
            "s",
            "has_report",
            "n_out_rot",
            "min_gap",
            "team_game_no",
            "starter10",
            "t30_starter",
            "t30_start_delta",
        )
        arms: list[tuple[str, ContextResidualModel, pl.DataFrame]] = [
            ("a", ContextResidualModel(stat, cfg, names_a).fit(train), block),
            ("b", ContextResidualModel(stat, cfg, names_b).fit(train), block),
        ]
        if sfs is not None:
            tr_s = sfs.filter(pl.col("game_date") < start).sort(
                ["game_date", "game_id", "player_id"]
            )
            bl_s = sfs.filter(
                (pl.col("game_date") >= start) & (pl.col("game_date") < end) & in_test
            )
            arms.append(("s", ContextResidualModel(stat, cfg, names_b).fit(tr_s), bl_s))
        cols: list[pl.Series] = []
        for tag, model, data in arms:
            mean, q199 = model.predict(data, CRPS_TAUS)
            _, q19 = model.predict(data, np.array(QUANTILE_TAUS))
            cols += [
                pl.Series(f"mean_{tag}", mean),
                pl.Series(f"crps_{tag}", crps_from_quantiles(q199, y)),
                pl.Series(f"q10_{tag}", q19[:, 1]),
                pl.Series(f"q90_{tag}", q19[:, 17]),
            ]
            if tag == "b":
                imps.append(model.importance_)
        parts.append(out.with_columns(cols))
    if not parts:
        return pl.DataFrame(), {}
    imp = {n: float(np.mean([d[n] for d in imps])) for n in imps[0] if n in T30_FEATURES}
    return add_t30_slices(pl.concat(parts)), imp


# --------------------------------------------------------------------------- summaries


def _delta_block(res: pl.DataFrame, a: str, b: str, n_boot: int) -> dict[str, Any]:
    gid = res["game_id"].to_numpy()
    ca, cb = res[f"crps_{a}"].to_numpy(), res[f"crps_{b}"].to_numpy()
    ci = paired_score_delta_ci(cb, ca, n_boot=n_boot, cluster_ids=gid)
    return {
        "delta": [ci.point, ci.lo, ci.hi],
        "p_value": boot_p_value(cb - ca, gid, n_boot),
    }


def summarize(res: pl.DataFrame, n_boot: int = N_BOOT) -> dict[str, Any]:
    """Arm B minus A (negative = lineups-known helps)."""
    y = res["y"].to_numpy()
    gid = res["game_id"].to_numpy()
    out: dict[str, Any] = {"n": int(res.height), "n_games": int(len(np.unique(gid)))}
    out.update(_delta_block(res, "a", "b", n_boot))
    out["crps_a"], out["crps_b"] = float(res["crps_a"].mean()), float(res["crps_b"].mean())  # type: ignore[arg-type]
    for tag in ("a", "b"):
        bc = mean_bias_ci(res[f"mean_{tag}"].to_numpy(), y, n_boot=n_boot, cluster_ids=gid)
        out[f"bias_{tag}"] = [bc.point, bc.lo, bc.hi]
        out[f"cov80_{tag}"] = interval_coverage(
            res[f"q10_{tag}"].to_numpy(), res[f"q90_{tag}"].to_numpy(), y
        )
        out[f"mae_{tag}"] = float(np.abs(res[f"mean_{tag}"].to_numpy() - y).mean())
    if "crps_s" in res.columns:
        sens = _delta_block(res, "a", "s", n_boot)
        out["sensitivity_scratch"] = {"scratch_prob": SCRATCH_PROB, **sens}
    slices: list[dict[str, Any]] = []
    for col in SLICE_COLS:
        for val in sorted(res[col].unique().to_list()):
            sub = res.filter(pl.col(col) == val)
            if sub.height < 50:
                continue
            sd = _delta_block(sub, "a", "b", 500)
            slices.append(
                {
                    "slice": f"{col}={val}",
                    "n": int(sub.height),
                    "delta": sd["delta"][0],
                    "lo": sd["delta"][1],
                    "hi": sd["delta"][2],
                }
            )
    ch = res.filter(pl.col("sl_status").is_in(["promoted", "demoted"]))
    if ch.height >= 50:
        sd = _delta_block(ch, "a", "b", 500)
        slices.append(
            {
                "slice": "sl_status=changed(promoted+demoted)",
                "n": int(ch.height),
                "delta": sd["delta"][0],
                "lo": sd["delta"][1],
                "hi": sd["delta"][2],
            }
        )
    out["slices"] = slices
    out["slice_regressions"] = [
        s["slice"]
        for s in slices
        if int(s["n"]) >= SLICE_MIN_N and float(s["delta"]) > SLICE_MAX_REGRESSION
    ]
    return out


def apply_rule(summaries: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The pre-registered per-stat rule (docs/LINEUPS_KNOWN.md)."""
    stats = list(summaries)
    adj = bh_adjust(np.array([float(summaries[s]["p_value"]) for s in stats]))
    verdict: dict[str, dict[str, Any]] = {}
    for s, q in zip(stats, adj, strict=True):
        r = summaries[s]
        pt, _lo, hi = r["delta"]
        checks = {
            "floor": pt <= -CRPS_FLOOR,
            "ci_excludes_0": hi < 0,
            "bh_q": bool(q <= 0.05),
            "cov80_within_tol": abs(r["cov80_b"] - 0.80) <= COV_TOL,
            "bias_abs_le_0.5": abs(r["bias_b"][0]) <= BIAS_MAX,
            "bias_not_worse": abs(r["bias_b"][0]) - abs(r["bias_a"][0]) <= BIAS_WORSEN_MAX,
            "no_slice_regression": not r["slice_regressions"],
        }
        verdict[s] = {"bh_adj_p": float(q), "checks": checks, "adds_value": all(checks.values())}
    return verdict


# --------------------------------------------------------------------------- P(play)


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-3, 1 - 1e-3)
    return np.asarray(np.log(p / (1.0 - p)))


def build_pplay_frame(games: pl.DataFrame, pgs: pl.DataFrame, t30: pl.DataFrame) -> pl.DataFrame:
    """One row per box-score row with production raw P(play) (default minutes config,
    no game context), the played label, and the T-30 columns. The minutes features are
    the production SQL run on an in-memory copy holding only the rows passed in."""
    from nba.props.minutes import build_minutes_features, predict_minutes

    con = duckdb.connect(":memory:")
    try:
        g = games.with_columns(pl.col("game_date").cast(pl.Date))
        con.register("_g", g.to_arrow())
        con.register(
            "_p", pgs.select("game_id", "player_id", "team_id", "minutes", "starter").to_arrow()
        )
        con.execute("CREATE TABLE games AS SELECT * FROM _g")
        con.execute("CREATE TABLE player_game_stats AS SELECT * FROM _p")
        mf = build_minutes_features(con, use_game_context=False)
    finally:
        con.close()
    dists = predict_minutes(mf)
    lab = pgs.select(
        "game_id", "player_id", (pl.col("minutes").fill_null(0.0) > 0.0).alias("played")
    )
    return (
        mf.select(
            "game_id",
            "player_id",
            "game_date",
            "season",
            "games_played_prior",
            "starter_rate_prior",
        )
        .with_columns(pl.Series("p_raw", [d.p_play for d in dists], dtype=pl.Float64))
        .join(lab, on=["game_id", "player_id"])
        .join(t30.drop("team_id"), on=["game_id", "player_id"], how="left")
    )


def _design(df: pl.DataFrame, with_t30: bool, role_prior: bool = False) -> np.ndarray:
    cols = [_logit(df["p_raw"].to_numpy())]
    if role_prior:  # exploratory control A2 (not pre-registered): adds the T-60 starter rate
        cols.append(df["starter_rate_prior"].fill_null(0.0).to_numpy().astype(float))
    if with_t30:
        for c in T30_FEATURES:
            v = df[c].fill_null(0.0).to_numpy().astype(float)
            cols.append(v / 10.0 if c == "t30_vac_starter_min" else v)
    return np.column_stack(cols)


def _logloss(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.asarray(-(y * np.log(p) + (1 - y) * np.log(1 - p)))


def pplay_walk_forward(
    frame: pl.DataFrame, test_seasons: tuple[int, ...], n_boot: int = N_BOOT
) -> dict[str, Any]:
    """Month-block walk-forward logistic recalibration, A_cal vs B (fixed C=1.0, no tuning)."""
    from nba.props.forward import P_PLAY_PLATT

    pop = frame.filter((pl.col("games_played_prior") >= 5) & (pl.col("p_raw") >= 0.5))
    test_all = pop.filter(pl.col("season").is_in(list(test_seasons)))
    parts: list[pl.DataFrame] = []
    for start, end in month_blocks(test_all):
        block = pop.filter(
            (pl.col("game_date") >= start)
            & (pl.col("game_date") < end)
            & pl.col("season").is_in(list(test_seasons))
        )
        train = pop.filter(pl.col("game_date") < start)
        if block.is_empty() or train.height < 1500:
            continue
        yt = train["played"].cast(pl.Int8).to_numpy()
        ya = block["played"].cast(pl.Float64).to_numpy()
        pa = LogisticRegression(C=1.0, max_iter=1000).fit(_design(train, False), yt)
        pb = LogisticRegression(C=1.0, max_iter=1000).fit(_design(train, True), yt)
        pa2 = LogisticRegression(C=1.0, max_iter=1000).fit(_design(train, False, True), yt)
        pa_, pb_ = P_PLAY_PLATT
        prod = 1.0 / (1.0 + np.exp(-(pa_ + pb_ * _logit(block["p_raw"].to_numpy()))))
        parts.append(
            block.select(
                "game_id", "player_id", "season", "t30_starter", "t30_start_delta"
            ).with_columns(
                pl.Series("y", ya),
                pl.Series("ll_a", _logloss(ya, pa.predict_proba(_design(block, False))[:, 1])),
                pl.Series("ll_b", _logloss(ya, pb.predict_proba(_design(block, True))[:, 1])),
                pl.Series("ll_prod", _logloss(ya, prod)),
                pl.Series(
                    "ll_a2", _logloss(ya, pa2.predict_proba(_design(block, False, True))[:, 1])
                ),
            )
        )
    res = pl.concat(parts)
    out: dict[str, Any] = {}
    for season in (*test_seasons, None):
        sub = res if season is None else res.filter(pl.col("season") == season)
        key = "all" if season is None else str(season)
        out[key] = _pplay_summary(sub, n_boot)
    return out


def _pplay_summary(res: pl.DataFrame, n_boot: int) -> dict[str, Any]:
    def one(sub: pl.DataFrame) -> dict[str, Any]:
        gid = sub["game_id"].to_numpy()
        ci = paired_score_delta_ci(
            sub["ll_b"].to_numpy(), sub["ll_a"].to_numpy(), n_boot=n_boot, cluster_ids=gid
        )
        ci2 = paired_score_delta_ci(
            sub["ll_b"].to_numpy(), sub["ll_a2"].to_numpy(), n_boot=n_boot, cluster_ids=gid
        )
        return {
            "n": int(sub.height),
            "base_rate": float(sub["y"].mean()),  # type: ignore[arg-type]
            "ll_prod_platt": float(sub["ll_prod"].mean()),  # type: ignore[arg-type]
            "ll_a_cal": float(sub["ll_a"].mean()),  # type: ignore[arg-type]
            "ll_b": float(sub["ll_b"].mean()),  # type: ignore[arg-type]
            "delta_b_minus_a": [ci.point, ci.lo, ci.hi],
            "p_value": boot_p_value(sub["ll_b"].to_numpy() - sub["ll_a"].to_numpy(), gid, n_boot),
            "exploratory_ll_a2_role_prior": float(sub["ll_a2"].mean()),  # type: ignore[arg-type]
            "exploratory_delta_b_minus_a2": [ci2.point, ci2.lo, ci2.hi],
        }

    out = {"overall": one(res)}
    bench = res.filter(pl.col("t30_starter") < 0.5)
    if bench.height >= 50:
        out["bench_tonight"] = one(bench)
    return out


# --------------------------------------------------------------------------- proxy bias bound


def starter_proxy_bounds(pgs: pl.DataFrame) -> dict[str, Any]:
    """Descriptive bounds on how optimistic the box-score starter proxy is."""
    st = pgs.filter(pl.col("starter").fill_null(False))
    per_team = (
        pgs.group_by(["game_id", "team_id"])
        .agg(pl.col("starter").fill_null(False).cast(pl.Int8).sum().alias("n"))
        .filter(pl.col("n") > 0)
    )
    m = st["minutes"]
    n = max(st.height, 1)
    return {
        "n_starter_rows": int(st.height),
        "share_starters_null_minutes": float(m.is_null().sum() / n),
        "share_starters_lt10min": float(((m < 10) | m.is_null()).sum() / n),
        "share_starters_lt5min": float(((m < 5) | m.is_null()).sum() / n),
        "n_team_games": int(per_team.height),
        "share_team_games_not_5_starters": float(
            (per_team["n"] != 5).sum() / max(per_team.height, 1)
        ),
    }


# --------------------------------------------------------------------------- run


def run(
    db_path: str = "nba.duckdb",
    elo_config: str = "configs/mov_elo_tuned.yaml",
    config_path: str = "configs/context_residual.yaml",
    out_dir: str = "reports/lineups_known",
    n_jobs: int = 2,
    stats: tuple[str, ...] = PROP_STATS,
    n_boot: int = N_BOOT,
    sensitivity: bool = True,
) -> dict[str, Any]:
    """Real-data entrypoint (read-only DB). Resumable: per-stat frames are cached in out_dir."""
    elo_params = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    cfg = ContextResidualConfig(**yaml.safe_load(Path(config_path).read_text()).get("model", {}))
    cfg = replace(cfg, n_jobs=n_jobs)
    games, pgs, static, avail = load_inputs(db_path, None)
    smax = games["season"].max()
    if smax is not None and int(smax) > MAX_SEASON:  # type: ignore[arg-type]
        raise ValueError("season > 2024 present: frozen holdout must not be loaded")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    flagged, _ = flagged_from_availability(avail, games, cfg.report)
    feats = build_features(games, pgs, static, flagged, elo_params, lineups_known=True)
    feats_s = None
    if sensitivity:
        t30s = build_lineup_features(games, pgs, scratch_prob=SCRATCH_PROB, seed=0).drop("team_id")
        feats_s = feats.drop(list(T30_FEATURES)).join(t30s, on=["game_id", "player_id"], how="left")
    result: dict[str, Any] = {
        "started": dt.datetime.now(dt.UTC).isoformat(),
        "seed": cfg.seed,
        "proxy_bounds": starter_proxy_bounds(
            pgs.join(games.select("game_id", "season"), on="game_id")
        ),
        "summaries": {},
        "importances": {},
    }
    frames: dict[str, pl.DataFrame] = {}
    for stat in stats:
        cache = out / f"rows_{stat}.parquet"
        if cache.exists():
            res = pl.read_parquet(cache)
            imp: dict[str, float] = json.loads((out / f"imp_{stat}.json").read_text())
        else:
            res, imp = walk_forward_arms(feats, stat, cfg, TEST_SEASONS, feats_s)
            res.write_parquet(cache)
            (out / f"imp_{stat}.json").write_text(json.dumps(imp))
        frames[stat] = res
        result["importances"][stat] = imp
        print(f"[{stat}] walk-forward done n={res.height}", flush=True)
    for season in (REPORT_SEASON, 2023):
        sm = {s: summarize(f.filter(pl.col("season") == season), n_boot) for s, f in frames.items()}
        result[f"season_{season}"] = {"summaries": sm, "verdict": apply_rule(sm)}
    t30 = build_lineup_features(games, pgs)
    pf = build_pplay_frame(games, pgs, t30)
    result["p_play"] = pplay_walk_forward(pf, TEST_SEASONS, n_boot)
    (out / "results.json").write_text(json.dumps(result, indent=2, default=str))
    print(format_report(result), flush=True)
    return result


def format_report(result: dict[str, Any]) -> str:
    lines = [
        "T-30 (B) minus T-60 (A) CRPS; negative = lineups-known helps",
        "season stat | n | CRPS A | CRPS B | delta [95% CI] | BH p | cov80 A/B | bias A/B | "
        "adds_value",
    ]
    for season in (REPORT_SEASON, 2023):
        blk = result.get(f"season_{season}")
        if not blk:
            continue
        for s, r in blk["summaries"].items():
            v = blk["verdict"][s]
            d = r["delta"]
            lines.append(
                f"{season} {s} | {r['n']} | {r['crps_a']:.4f} | {r['crps_b']:.4f} | "
                f"{d[0]:+.4f} [{d[1]:+.4f},{d[2]:+.4f}] | {v['bh_adj_p']:.3g} | "
                f"{r['cov80_a']:.3f}/{r['cov80_b']:.3f} | "
                f"{r['bias_a'][0]:+.3f}/{r['bias_b'][0]:+.3f} | "
                f"{v['adds_value']} {[k for k, ok in v['checks'].items() if not ok]}"
            )
            sens = r.get("sensitivity_scratch")
            if sens:
                sd = sens["delta"]
                lines.append(
                    f"   sensitivity scratch_prob={sens['scratch_prob']}: "
                    f"{sd[0]:+.4f} [{sd[1]:+.4f},{sd[2]:+.4f}]"
                )
            if season == REPORT_SEASON:
                for sl in r["slices"]:
                    lines.append(
                        f"   [{s}] {sl['slice']:<40} n={sl['n']:<6} d={sl['delta']:+.4f} "
                        f"[{sl['lo']:+.4f},{sl['hi']:+.4f}]"
                    )
    lines.append(f"P(play): {json.dumps(result.get('p_play'), default=str)}")
    lines.append(f"proxy bounds: {json.dumps(result.get('proxy_bounds'))}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    ap.add_argument("--db-path", default="nba.duckdb")
    ap.add_argument("--out-dir", default="reports/lineups_known")
    ap.add_argument("--n-jobs", type=int, default=2)
    ap.add_argument("--no-sensitivity", action="store_true")
    a = ap.parse_args(argv)
    run(a.db_path, out_dir=a.out_dir, n_jobs=a.n_jobs, sensitivity=not a.no_sensitivity)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
