"""Orchestrates the props pipeline end to end (CLAUDE.md Phase 2, milestone 7).

minutes model -> per-stat frequency-severity distributions -> metrics
(CRPS, threshold log loss, calibration, mean-bias CI, 80% coverage) ->
paired-bootstrap comparison against season-average / last-10-average
baselines -> ``prop_predictions`` rows + ``report.md``.

Design note on "walk-forward": unlike the win-probability rungs in
``nba/eval/run.py`` (which retrain model parameters per fold), nothing
here is fit from data in the ML sense -- every feature is already a
strictly-prior SQL window aggregate (see ``nba.props.minutes``,
``nba.props.stat_models``, ``nba.props.baselines``), and shrinkage
pseudo-counts are fixed config, not tuned per fold. So every row's
prediction is already "as of" that game's tip-off by construction; no
separate walk-forward fold loop is needed to avoid leakage here. The
no-leakage guarantee is instead proven structurally (see
``tests/props/test_minutes_no_leakage.py``).
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import cast

import duckdb
import numpy as np
import polars as pl

from nba.props.baselines import (
    build_baseline_features,
    last10_average_baseline,
    season_average_baseline,
)
from nba.props.config import TARGET_STATS, THRESHOLDS, PropsConfig
from nba.props.distributions import Distribution
from nba.props.metrics import (
    ConfidenceInterval,
    ThresholdCalibration,
    avg_threshold_log_loss_per_game,
    crps_array,
    interval_coverage,
    mean_bias_ci,
    paired_score_delta_ci,
    pooled_threshold_ece,
    threshold_log_loss_and_calibration,
)
from nba.props.minutes import build_minutes_features, predict_minutes
from nba.props.stat_models import (
    build_count_stat_features,
    build_negbin_distributions,
    build_points_distributions,
    build_points_features,
    build_zinb_distributions,
    count_stat_moments,
    fit_zero_inflation,
    points_moments,
)

#: Below this many player-games, bias/coverage CIs are reported but
#: flagged as not statistically meaningful (CLAUDE.md milestone 7, point
#: 4: "the fixture ... must say so explicitly").
MIN_RELIABLE_N = 500


@dataclass
class StatResult:
    stat: str
    n: int
    dist_family: str
    mean_bias: ConfidenceInterval
    coverage: float
    threshold_calibration: list[ThresholdCalibration]
    pooled_ece: float
    crps_point: float
    crps_vs_season_avg: ConfidenceInterval
    crps_vs_last10_avg: ConfidenceInterval
    log_loss_vs_season_avg: ConfidenceInterval
    log_loss_vs_last10_avg: ConfidenceInterval
    zero_inflation_detected: bool
    data_sufficiency_note: str
    baseline_note: str


@dataclass
class PropsExperimentResult:
    stats: list[StatResult]
    predictions: pl.DataFrame
    seed: int
    n_boot: int
    game_date_min: dt.date | None
    game_date_max: dt.date | None


def _target_frame(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    con.execute(
        """
        SELECT pgs.game_id, pgs.player_id, g.game_date, g.season,
               pgs.pts, pgs.reb, pgs.ast, pgs.fg3m
        FROM player_game_stats pgs
        JOIN games g USING (game_id)
        ORDER BY g.game_date, pgs.game_id, pgs.player_id
        """
    )
    columns = [d[0] for d in con.description]
    rows = con.fetchall()
    schema = {
        "game_id": pl.Utf8,
        "player_id": pl.Int64,
        "game_date": pl.Date,
        "season": pl.Int64,
        "pts": pl.Int64,
        "reb": pl.Int64,
        "ast": pl.Int64,
        "fg3m": pl.Int64,
    }
    return pl.DataFrame(rows, schema={c: schema[c] for c in columns}, orient="row")


def run_props_experiment(
    con: duckdb.DuckDBPyConnection,
    config: PropsConfig | None = None,
    seed: int = 0,
    n_boot: int = 500,
) -> PropsExperimentResult:
    cfg = config or PropsConfig()
    target = _target_frame(con)
    if target.height == 0:
        return PropsExperimentResult(
            stats=[],
            predictions=target,
            seed=seed,
            n_boot=n_boot,
            game_date_min=None,
            game_date_max=None,
        )

    minutes_features = build_minutes_features(con)
    minutes_dists = predict_minutes(minutes_features, cfg.minutes)
    minutes_key = minutes_features.select(["game_id", "player_id"]).with_columns(
        _minutes_row_idx=pl.int_range(pl.len())
    )
    minutes_mean_all = np.array([d.mean() for d in minutes_dists])
    minutes_var_all = np.array([d.var() for d in minutes_dists])

    # Align minutes moments onto `target`'s row order by (game_id, player_id).
    joined = target.join(minutes_key, on=["game_id", "player_id"], how="left")
    idx = joined.select("_minutes_row_idx").to_series().to_numpy()
    if np.isnan(idx).any():
        raise RuntimeError("minutes features missing rows present in player_game_stats")
    idx = idx.astype(int)
    minutes_mean = minutes_mean_all[idx]
    minutes_var = minutes_var_all[idx]

    pred_rows: list[dict[str, object]] = []
    stat_results: list[StatResult] = []

    for stat in TARGET_STATS:
        y = target.select(stat).to_series().to_numpy().astype(float)
        thresholds = THRESHOLDS[stat]

        if stat == "pts":
            feats = build_points_features(con)
            feats_aligned = _align(target, feats)
            moments = points_moments(feats_aligned, minutes_mean, minutes_var, cfg.points)
            dists: list[Distribution] = list(build_points_distributions(moments))
            zero_inflated = False
        else:
            feats = build_count_stat_features(con, stat)
            feats_aligned = _align(target, feats)
            count_cfg = cfg.count_stats[stat]
            moments_c = count_stat_moments(feats_aligned, minutes_mean, minutes_var, count_cfg)
            base_dists = build_negbin_distributions(moments_c)
            pi = fit_zero_inflation(y, base_dists, cfg.zero_inflation)
            zero_inflated = bool(np.any(pi > 0))
            if zero_inflated:
                dists = list(build_zinb_distributions(base_dists, pi))
            else:
                dists = list(base_dists)

        pred_mean = np.array([d.mean() for d in dists])
        q10 = np.array([d.ppf(0.10) for d in dists])
        q50 = np.array([d.ppf(0.50) for d in dists])
        q90 = np.array([d.ppf(0.90) for d in dists])

        n = len(y)
        note = (
            f"only {n} player-games (<{MIN_RELIABLE_N}); mean-bias CI and coverage are "
            "NOT statistically meaningful at this sample size -- treat as a smoke test, "
            "not a success/failure verdict (CLAUDE.md milestone 7, point 4)."
            if n < MIN_RELIABLE_N
            else ""
        )

        bias_ci = mean_bias_ci(pred_mean, y, n_boot=n_boot, seed=seed)
        coverage = interval_coverage(q10, q90, y)
        threshold_cal = threshold_log_loss_and_calibration(dists, y, thresholds)
        pooled_ece = pooled_threshold_ece(threshold_cal)
        crps_model = crps_array(dists, y)

        baseline_feats_season = _align(target, build_baseline_features(con, stat))
        season_dists = season_average_baseline(baseline_feats_season, stat)
        last10_dists = last10_average_baseline(baseline_feats_season, stat)
        games_played_prior = baseline_feats_season.select("games_played_prior").to_series()
        no_history_frac = float(cast("float", (games_played_prior.fill_null(0) == 0).mean()))
        baseline_note = (
            f"{no_history_frac:.0%} of rows have zero prior games for this player, so the "
            "season-avg/last-10 baselines default to 0 on those rows -- any 'beats baseline' "
            "result below is a fixture artifact (every player's first game in this tiny "
            "dataset), not evidence of real skill. Needs a real multi-game dataset to mean "
            "anything."
            if no_history_frac > 0.5
            else ""
        )
        crps_season = crps_array(list(season_dists), y)
        crps_last10 = crps_array(list(last10_dists), y)
        ll_model = avg_threshold_log_loss_per_game(dists, y, thresholds)
        ll_season = avg_threshold_log_loss_per_game(list(season_dists), y, thresholds)
        ll_last10 = avg_threshold_log_loss_per_game(list(last10_dists), y, thresholds)

        crps_vs_season = paired_score_delta_ci(crps_model, crps_season, n_boot=n_boot, seed=seed)
        crps_vs_last10 = paired_score_delta_ci(crps_model, crps_last10, n_boot=n_boot, seed=seed)
        ll_vs_season = paired_score_delta_ci(ll_model, ll_season, n_boot=n_boot, seed=seed)
        ll_vs_last10 = paired_score_delta_ci(ll_model, ll_last10, n_boot=n_boot, seed=seed)

        stat_results.append(
            StatResult(
                stat=stat,
                n=n,
                dist_family=dists[0].family if dists else "none",
                mean_bias=bias_ci,
                coverage=coverage,
                threshold_calibration=threshold_cal,
                pooled_ece=pooled_ece,
                crps_point=float(np.mean(crps_model)) if len(crps_model) else float("nan"),
                crps_vs_season_avg=crps_vs_season,
                crps_vs_last10_avg=crps_vs_last10,
                log_loss_vs_season_avg=ll_vs_season,
                log_loss_vs_last10_avg=ll_vs_last10,
                zero_inflation_detected=zero_inflated,
                data_sufficiency_note=note,
                baseline_note=baseline_note,
            )
        )

        for i in range(n):
            p_ge = {str(th): float(dists[i].p_ge(float(th))) for th in thresholds}
            pred_rows.append(
                {
                    "game_id": target[i, "game_id"],
                    "player_id": target[i, "player_id"],
                    "stat": stat,
                    "mean": float(pred_mean[i]),
                    "dist_family": dists[i].family,
                    "dist_params": json.dumps(dists[i].params()),
                    "p_ge": json.dumps(p_ge),
                    "q10": float(q10[i]),
                    "q50": float(q50[i]),
                    "q90": float(q90[i]),
                }
            )

    predictions = pl.DataFrame(pred_rows) if pred_rows else pl.DataFrame()
    dates = target.select("game_date").to_series()
    return PropsExperimentResult(
        stats=stat_results,
        predictions=predictions,
        seed=seed,
        n_boot=n_boot,
        game_date_min=cast("dt.date | None", dates.min()),
        game_date_max=cast("dt.date | None", dates.max()),
    )


def write_prop_predictions(
    con: duckdb.DuckDBPyConnection, predictions: pl.DataFrame, run_id: str
) -> None:
    """Insert ``predictions`` into ``prop_predictions`` (CLAUDE.md output contract).

    Stamps ``run_id`` and ``made_at`` at write time (not carried on the
    in-memory :class:`PropsExperimentResult` so that two identically-seeded
    runs produce byte-identical ``predictions`` frames for the determinism
    test -- a wall-clock timestamp would otherwise differ run to run).
    """
    from nba.ingest.cache import insert_rows  # local import: keep this module DB-free by default

    if predictions.height == 0:
        return
    made_at = dt.datetime.now(dt.UTC)
    rows = predictions.with_columns(run_id=pl.lit(run_id), made_at=pl.lit(made_at))
    columns = [
        "run_id",
        "game_id",
        "player_id",
        "stat",
        "mean",
        "dist_family",
        "dist_params",
        "p_ge",
        "q10",
        "q50",
        "q90",
        "made_at",
    ]
    insert_rows(con, "prop_predictions", columns, rows)


def _align(target: pl.DataFrame, feats: pl.DataFrame) -> pl.DataFrame:
    """Left-join ``feats`` onto ``target``'s (game_id, player_id, game_date) row
    order so every stat's as-of features line up 1:1 with ``target``'s rows,
    regardless of each query's own internal ordering."""
    key_cols = [c for c in ("game_id", "player_id") if c in feats.columns]
    return target.select(["game_id", "player_id"]).join(feats, on=key_cols, how="left")
