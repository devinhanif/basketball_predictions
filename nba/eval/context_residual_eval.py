"""Walk-forward evaluation of the context-residual props candidate.

PRE-REGISTERED DECISION RULE (written before the first real-data run, 2026-10-08)
-------------------------------------------------------------------------------
* Candidate: ``nba.props.context_residual`` (recency mean + LightGBM residual and
  scale heads, split-conformal standardized quantiles).
* Baseline: the forward default, the played-only recency-weighted average
  (half-life 10 games) as a Normal(mean, recency std).
* Rows: PLAYED rows only (minutes > 0) with >= 5 prior played games, season
  2023 and 2024 (2022 = warm-up history and training). Season 2025 is never
  loaded: the loader filters ``season <= 2024`` in SQL.
* Walk-forward: calendar-month blocks; each block is predicted by models fit on
  games dated strictly before the block; the newest 15% of that training window
  (cut on a date boundary) is the held-out conformal calibration window.
* Primary metric: per-row CRPS (199-quantile pinball form, as
  ``nba.props.metrics``), paired difference candidate - baseline, 95% bootstrap CI
  clustered by game_id (2000 resamples).
* KEEP a stat iff ALL hold: (1) mean CRPS delta <= -0.005 (floor); (2) the
  clustered 95% CI excludes 0 (upper bound < 0); (3) the clustered-bootstrap
  two-sided p-value survives Benjamini-Hochberg across the 4 stats at q = 0.05;
  (4) no slice (teammate-out report status, minutes-change bucket, first-15 vs
  rest team games, starter vs bench; n >= 300) has a CRPS delta > +0.01.
* Diagnostics only (not part of the rule): MAE of the predictive mean, mean bias
  with clustered CI, 80% interval coverage, and the same CRPS comparison against
  ``recency_conformal`` (recency mean/std given the same conformal quantile
  shape) to separate calibration gains from context-feature gains.
* The rule is not changed after seeing results; a negative result is reported
  as such.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import yaml
from scipy.stats import norm

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
from nba.props.metrics import (
    _clustered_boot_means,
    interval_coverage,
    mean_bias_ci,
    paired_score_delta_ci,
)

MODEL_NAME = "context_residual"
HOLDOUT_SEASON = 2025
MAX_SEASON = 2024  # season 2025 is the frozen holdout: never loaded
TEST_SEASONS: tuple[int, ...] = (2023, 2024)
CRPS_FLOOR = 0.005
SLICE_MAX_REGRESSION = 0.01
SLICE_MIN_N = 300
N_BOOT = 2000


# --------------------------------------------------------------------------- scoring


def crps_from_quantiles(q: np.ndarray, y: np.ndarray, taus: np.ndarray = CRPS_TAUS) -> np.ndarray:
    """CRPS ~= 2 * mean_tau pinball(y, q_tau) per row (same form as nba.props.metrics)."""
    d = y[:, None] - q
    pin = np.where(d >= 0, taus[None, :] * d, (taus[None, :] - 1.0) * d)
    return np.asarray(2.0 * pin.mean(axis=1))


def normal_quantiles(m: np.ndarray, s: np.ndarray, taus: np.ndarray) -> np.ndarray:
    return np.asarray(m[:, None] + s[:, None] * norm.ppf(taus)[None, :])


def bh_adjust(p: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values (monotone step-up)."""
    p = np.asarray(p, dtype=float)
    n = len(p)
    order = np.argsort(p)
    adj = np.empty(n)
    run = 1.0
    for rank, idx in zip(range(n, 0, -1), order[::-1], strict=True):
        run = min(run, p[idx] * n / rank)
        adj[idx] = run
    return adj


def boot_p_value(
    delta: np.ndarray, cluster_ids: np.ndarray, n_boot: int = N_BOOT, seed: int = 0
) -> float:
    """Two-sided clustered-bootstrap p-value for mean(delta) == 0."""
    b = _clustered_boot_means(delta, cluster_ids, n_boot, np.random.default_rng(seed))
    p = 2.0 * min(float(np.mean(b >= 0.0)), float(np.mean(b <= 0.0)))
    return float(min(max(p, 1.0 / (n_boot + 1)), 1.0))


# --------------------------------------------------------------------------- data


def load_inputs(
    db_path: str, injury_db: str | None, max_season: int = MAX_SEASON
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """(games, player_game_stats, players_static, availability); read-only; season <= 2024."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        games = con.execute(
            "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
            f"FROM games WHERE season <= {max_season}"
        ).pl()
        pgs = con.execute(
            "SELECT s.game_id, s.player_id, s.team_id, s.minutes, s.pts, s.reb, "
            "s.ast, s.fg3m, s.starter "
            f"FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season <= {max_season}"
        ).pl()
        static = con.execute("SELECT player_id, position FROM players_static").pl()
    finally:
        con.close()
    icon = duckdb.connect(injury_db or db_path, read_only=True)
    try:
        avail = icon.execute(
            "SELECT game_id, player_id, status, as_of, source FROM player_availability "
            "WHERE game_id IS NOT NULL"
        ).pl()
    finally:
        icon.close()
    return games, pgs, static, avail


# --------------------------------------------------------------------------- slices


def add_slice_columns(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        pl.when(pl.col("has_report") == 0)
        .then(pl.lit("no_report"))
        .when(pl.col("n_out_rot") > 0)
        .then(pl.lit("teammate_out"))
        .otherwise(pl.lit("report_none_out"))
        .alias("sl_tm_out"),
        pl.when(pl.col("min_gap") < -3.0)
        .then(pl.lit("minutes_down"))
        .when(pl.col("min_gap") > 3.0)
        .then(pl.lit("minutes_up"))
        .otherwise(pl.lit("minutes_flat"))
        .alias("sl_min_change"),
        pl.when(pl.col("team_game_no") < 15)
        .then(pl.lit("first15"))
        .otherwise(pl.lit("rest"))
        .alias("sl_phase"),
        pl.when(pl.col("starter10") >= 0.5)
        .then(pl.lit("starter"))
        .otherwise(pl.lit("bench"))
        .alias("sl_role"),
    )


SLICE_COLS = ("sl_tm_out", "sl_min_change", "sl_phase", "sl_role")


# --------------------------------------------------------------------------- walk-forward


def month_blocks(test: pl.DataFrame) -> list[tuple[dt.date, dt.date]]:
    """[start, end) calendar-month blocks covering the test rows."""
    months = sorted({(d.year, d.month) for d in test["game_date"].to_list()})
    out = []
    for y, m in months:
        start = dt.date(y, m, 1)
        end = dt.date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        out.append((start, end))
    return out


def walk_forward_stat(
    feats: pl.DataFrame, stat: str, cfg: ContextResidualConfig, test_seasons: tuple[int, ...]
) -> tuple[pl.DataFrame, dict[str, float]]:
    """Predict every test row from models fit strictly before its month block.

    Returns the test frame with prediction columns and averaged gain importances."""
    sf = stat_frame(feats, stat)
    names = stat_feature_names(stat)
    test_all = sf.filter(pl.col("season").is_in(list(test_seasons)))
    parts: list[pl.DataFrame] = []
    imps: list[dict[str, float]] = []
    for start, end in month_blocks(test_all):
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(test_seasons))
        )
        train = sf.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train.height < 1500:
            continue
        model = ContextResidualModel(stat, cfg, names).fit(train)
        mean, q199 = model.predict(block, CRPS_TAUS)
        _, q19 = model.predict(block, np.array(QUANTILE_TAUS))
        bmean, bq199 = model.predict_baseline_cal(block, CRPS_TAUS)
        data_through = train["game_date"].max()
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
        ).with_columns(
            pl.Series("mean", mean),
            pl.Series("crps", crps_from_quantiles(q199, block["y"].to_numpy())),
            pl.Series("q10", q19[:, 1]),
            pl.Series("q90", q19[:, 17]),
            pl.Series("q_grid", [list(map(float, r)) for r in q19], dtype=pl.List(pl.Float64)),
            pl.Series("crps_cal", crps_from_quantiles(bq199, block["y"].to_numpy())),
            pl.lit(data_through).alias("made_with_data_through"),
        )
        parts.append(out)
        imps.append(model.importance_)
    if not parts:
        return pl.DataFrame(), {}
    res = pl.concat(parts)
    bq = normal_quantiles(res["m"].to_numpy(), res["s"].to_numpy(), CRPS_TAUS)
    bq10, bq90 = (
        res["m"].to_numpy() + res["s"].to_numpy() * norm.ppf(0.1),
        res["m"].to_numpy() + res["s"].to_numpy() * norm.ppf(0.9),
    )
    res = res.with_columns(
        pl.Series("crps_base", crps_from_quantiles(bq, res["y"].to_numpy())),
        pl.Series("bq10", bq10),
        pl.Series("bq90", bq90),
    )
    imp = {n: float(np.mean([d[n] for d in imps])) for n in imps[0]}
    return add_slice_columns(res), imp


def summarize_stat(res: pl.DataFrame, n_boot: int = N_BOOT) -> dict[str, Any]:
    y = res["y"].to_numpy()
    gid = res["game_id"].to_numpy()
    crps, base, cal = (
        res["crps"].to_numpy(),
        res["crps_base"].to_numpy(),
        res["crps_cal"].to_numpy(),
    )
    d = crps - base
    ci = paired_score_delta_ci(crps, base, n_boot=n_boot, cluster_ids=gid)
    ci_cal = paired_score_delta_ci(crps, cal, n_boot=n_boot, cluster_ids=gid)
    bias_c = mean_bias_ci(res["mean"].to_numpy(), y, n_boot=n_boot, cluster_ids=gid)
    bias_b = mean_bias_ci(res["m"].to_numpy(), y, n_boot=n_boot, cluster_ids=gid)
    out: dict[str, Any] = {
        "n": int(res.height),
        "n_games": int(len(np.unique(gid))),
        "crps_cand": float(crps.mean()),
        "crps_base": float(base.mean()),
        "crps_delta": [ci.point, ci.lo, ci.hi],
        "crps_delta_vs_recency_conformal": [ci_cal.point, ci_cal.lo, ci_cal.hi],
        "p_value": boot_p_value(d, gid, n_boot),
        "p_value_vs_recency_conformal": boot_p_value(crps - cal, gid, n_boot),
        "mae_cand": float(np.abs(res["mean"].to_numpy() - y).mean()),
        "mae_base": float(np.abs(res["m"].to_numpy() - y).mean()),
        "bias_cand": [bias_c.point, bias_c.lo, bias_c.hi],
        "bias_base": [bias_b.point, bias_b.lo, bias_b.hi],
        "cov80_cand": interval_coverage(res["q10"].to_numpy(), res["q90"].to_numpy(), y),
        "cov80_base": interval_coverage(res["bq10"].to_numpy(), res["bq90"].to_numpy(), y),
    }
    slices: list[dict[str, Any]] = []
    for col in SLICE_COLS:
        for val in sorted(res[col].unique().to_list()):
            sub = res.filter(pl.col(col) == val)
            if sub.height < 50:
                continue
            sd = sub["crps"].to_numpy() - sub["crps_base"].to_numpy()
            sci = paired_score_delta_ci(
                sub["crps"].to_numpy(),
                sub["crps_base"].to_numpy(),
                n_boot=500,
                cluster_ids=sub["game_id"].to_numpy(),
            )
            slices.append(
                {
                    "slice": f"{col}={val}",
                    "n": int(sub.height),
                    "delta": float(sd.mean()),
                    "lo": sci.lo,
                    "hi": sci.hi,
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
    """The pre-registered keep rule (see module docstring)."""
    stats = list(summaries)
    adj = bh_adjust(np.array([float(summaries[s]["p_value"]) for s in stats]))
    verdict: dict[str, dict[str, Any]] = {}
    for s, q in zip(stats, adj, strict=True):
        pt, _lo, hi = summaries[s]["crps_delta"]
        checks = {
            "floor": pt <= -CRPS_FLOOR,
            "ci_excludes_0": hi < 0,
            "bh_q": bool(q <= 0.05),
            "no_slice_regression": not summaries[s]["slice_regressions"],
        }
        verdict[s] = {"bh_adj_p": float(q), "checks": checks, "keep": all(checks.values())}
    return verdict


def run_eval_frames(
    games: pl.DataFrame,
    pgs: pl.DataFrame,
    static: pl.DataFrame,
    avail: pl.DataFrame,
    elo_params: dict[str, float],
    cfg: ContextResidualConfig | None = None,
    test_seasons: tuple[int, ...] = TEST_SEASONS,
    stats: tuple[str, ...] = PROP_STATS,
    n_boot: int = N_BOOT,
    max_season: int = MAX_SEASON,
) -> dict[str, Any]:
    cfg = cfg or ContextResidualConfig()
    max_season_seen = games["season"].max()
    if max_season_seen is not None and int(max_season_seen) > max_season:  # type: ignore[arg-type]
        raise ValueError("season > 2024 present: frozen holdout must not be loaded")
    flagged, _info = flagged_from_availability(avail, games, cfg.report)
    feats = build_features(games, pgs, static, flagged, elo_params)
    summaries: dict[str, dict[str, Any]] = {}
    importances: dict[str, dict[str, float]] = {}
    oof: list[pl.DataFrame] = []
    for stat in stats:
        res, imp = walk_forward_stat(feats, stat, cfg, test_seasons)
        if res.is_empty():
            continue
        summaries[stat] = summarize_stat(res, n_boot)
        importances[stat] = dict(sorted(imp.items(), key=lambda kv: -kv[1])[:12])
        oof.append(
            res.select(
                pl.lit(MODEL_NAME).alias("model"),
                (pl.col("game_date").dt.year() * 100 + pl.col("game_date").dt.month()).alias(
                    "fold_id"
                ),
                "game_id",
                "player_id",
                pl.lit(stat).alias("target"),
                "game_date",
                "season",
                "mean",
                "q_grid",
                "made_with_data_through",
            )
        )
    return {
        "summaries": summaries,
        "verdict": apply_rule(summaries) if summaries else {},
        "importances": importances,
        "oof": pl.concat(oof) if oof else pl.DataFrame(),
        "report_coverage": {
            "games_with_report": len(flagged),
            "share_test_rows_with_report": float(
                feats.filter(pl.col("season").is_in(list(test_seasons)))["has_report"].mean()  # type: ignore[arg-type]
                or 0.0
            ),
        },
    }


def format_report(result: dict[str, Any]) -> str:
    lines = [
        "stat | n | CRPS cand | CRPS base | delta [95% CI] | p(BH) | MAE c/b | "
        "bias c/b | cov80 c/b | keep"
    ]
    sm: dict[str, dict[str, Any]] = result["summaries"]
    vd: dict[str, dict[str, Any]] = result["verdict"]
    for s, r in sm.items():
        d, bc, bb = r["crps_delta"], r["bias_cand"], r["bias_base"]
        lines.append(
            f"{s} | {r['n']} | {r['crps_cand']:.4f} | {r['crps_base']:.4f} | "
            f"{d[0]:+.4f} [{d[1]:+.4f},{d[2]:+.4f}] | {vd[s]['bh_adj_p']:.3g} | "
            f"{r['mae_cand']:.3f}/{r['mae_base']:.3f} | {bc[0]:+.3f}[{bc[1]:+.2f},{bc[2]:+.2f}]/"
            f"{bb[0]:+.3f} | {r['cov80_cand']:.3f}/{r['cov80_base']:.3f} | {vd[s]['keep']}"
        )
    for s, r in sm.items():
        dc = r["crps_delta_vs_recency_conformal"]
        lines.append(f"[{s}] vs recency_conformal: {dc[0]:+.4f} [{dc[1]:+.4f},{dc[2]:+.4f}]")
        for sl in r["slices"]:
            lines.append(
                f"  [{s}] {sl['slice']:<28} n={sl['n']:<6} d={sl['delta']:+.4f} "
                f"[{sl['lo']:+.4f},{sl['hi']:+.4f}]"
            )
        lines.append(f"  [{s}] checks: {vd[s]['checks']}  regress: {r['slice_regressions']}")
    return "\n".join(lines)


def run(
    db_path: str = "nba.duckdb",
    injury_db: str | None = None,
    elo_config: str = "configs/mov_elo_tuned.yaml",
    config_path: str = "configs/context_residual.yaml",
    out_dir: str = "reports/context_residual",
    write_store: bool = False,
    tip_source: str = "proxy19",
) -> dict[str, Any]:
    """Real-data entrypoint (read-only DBs; never writes them).

    ``tip_source`` "proxy19" (default, production) or "real" (scheduled tip-off
    gating of report snapshots; see ``nba.features.game_tipoff``)."""
    elo_params = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    cfg_raw = yaml.safe_load(Path(config_path).read_text())
    cfg = ContextResidualConfig(**cfg_raw.get("model", {}))
    if tip_source != "proxy19":
        cfg = dataclasses.replace(
            cfg, report=dataclasses.replace(cfg.report, tip_source=tip_source)
        )
    games, pgs, static, avail = load_inputs(db_path, injury_db)
    result = run_eval_frames(games, pgs, static, avail, elo_params, cfg)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    oof = result.pop("oof")
    assert isinstance(oof, pl.DataFrame)
    if not oof.is_empty():
        oof.write_parquet(out / "oof_context_residual.parquet")
        if write_store:
            from nba.stack.oof import write_oof

            write_oof(oof.to_pandas(), MODEL_NAME, "v1")
    (out / "results.json").write_text(json.dumps(result, indent=2, default=str))
    text = format_report(result)
    (out / "report.txt").write_text(text)
    print(text)
    return result


def run_holdout(
    db_path: str = "nba.duckdb",
    injury_db: str | None = None,
    elo_config: str = "configs/mov_elo_tuned.yaml",
    config_path: str = "configs/context_residual.yaml",
    out_path: str = "reports/context_residual/holdout_2025.json",
) -> dict[str, Any]:
    """Confirmatory season-2025 touch (docs/HOLDOUT_ACCESS_LOG.md; run ONCE).

    Frozen procedure: same config/features; month blocks over 2025 only are
    scored, each trained on all played rows strictly before the block. Primary:
    paired CRPS delta vs recency_conformal, game-clustered, BH over the 4 stats.
    """
    elo_params = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    cfg = ContextResidualConfig(**yaml.safe_load(Path(config_path).read_text()).get("model", {}))
    games, pgs, static, avail = load_inputs(db_path, injury_db, max_season=HOLDOUT_SEASON)
    res = run_eval_frames(
        games, pgs, static, avail, elo_params, cfg, test_seasons=(HOLDOUT_SEASON,),
        max_season=HOLDOUT_SEASON,
    )  # fmt: skip
    res.pop("oof")
    sm = res["summaries"]
    stats = list(sm)
    adj = bh_adjust(np.array([float(sm[s]["p_value_vs_recency_conformal"]) for s in stats]))
    res["holdout_verdict"] = {
        s: {
            "bh_adj_p": float(q),
            "delta_vs_recency_conformal": sm[s]["crps_delta_vs_recency_conformal"],
            "confirmed": bool(sm[s]["crps_delta_vs_recency_conformal"][2] < 0 and q <= 0.05),
        }
        for s, q in zip(stats, adj, strict=True)
    }
    res.pop("verdict", None)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2, default=str))
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    ap.add_argument("--db-path", default="nba.duckdb")
    ap.add_argument("--injury-db", default=None, help="default: player_availability in --db-path")
    ap.add_argument("--write-oof", action="store_true", help="also write nba.stack.oof store")
    ap.add_argument("--config", default="configs/context_residual.yaml")
    ap.add_argument("--out-dir", default="reports/context_residual")
    ap.add_argument("--confirmatory-holdout", type=int, default=None)
    ap.add_argument("--i-have-preregistered", action="store_true")
    ap.add_argument("--tip-source", choices=("proxy19", "real"), default="proxy19")
    a = ap.parse_args(argv)
    if a.confirmatory_holdout is not None or a.i_have_preregistered:
        if a.confirmatory_holdout != HOLDOUT_SEASON or not a.i_have_preregistered:
            ap.error("holdout needs BOTH --confirmatory-holdout 2025 and --i-have-preregistered")
        res = run_holdout(a.db_path, a.injury_db, config_path=a.config)
        print(
            json.dumps(
                {"summaries": res["summaries"], "verdict": res["holdout_verdict"]}, default=str
            )
        )
        return 0
    run(
        a.db_path,
        a.injury_db,
        config_path=a.config,
        out_dir=a.out_dir,
        write_store=a.write_oof,
        tip_source=a.tip_source,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
