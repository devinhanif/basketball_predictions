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
from dataclasses import dataclass, field
from typing import cast

import duckdb
import numpy as np
import polars as pl

from nba.props.baselines import (
    build_baseline_features,
    last10_average_baseline,
    season_average_baseline,
)
from nba.props.coherence import (
    CoherenceCheck,
    build_team_total_features,
    check_coherence,
    reconcile_group,
    team_total_forecast,
)
from nba.props.combos import (
    COMBO_THRESHOLDS,
    COMBOS,
    build_combo_distributions,
    combo_moments,
    estimate_within_player_correlation,
)
from nba.props.config import TARGET_STATS, THRESHOLDS, PropsConfig
from nba.props.conformal import ConformalResult, evaluate_conformal
from nba.props.distributions import Distribution, batch_minutes_mean_var, batch_p_ge, batch_ppf
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
from nba.props.role_change import build_role_change_features
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
from nba.props.volatility import (
    VolatilityBucketResult,
    assign_buckets,
    build_volatility_features,
    evaluate_volatility_buckets,
    quantile_buckets,
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
    conformal: ConformalResult | None = None
    volatility_buckets: list[VolatilityBucketResult] = field(default_factory=list)


@dataclass
class ComboStatResult:
    stat: str
    n: int
    dist_family: str
    mean_bias: ConfidenceInterval
    coverage: float
    pooled_ece: float
    crps_point: float
    correlation_used: dict[str, float]
    note: str


@dataclass
class RoleChangeSummary:
    n_rows: int
    n_flagged: int
    flagged_examples: list[dict[str, object]]  # (game_id, player_id, games_since_change)


@dataclass
class PropsExperimentResult:
    stats: list[StatResult]
    predictions: pl.DataFrame
    seed: int
    n_boot: int
    game_date_min: dt.date | None
    game_date_max: dt.date | None
    combo_stats: list[ComboStatResult] = field(default_factory=list)
    combo_predictions: pl.DataFrame = field(default_factory=pl.DataFrame)
    coherence_checks: list[CoherenceCheck] = field(default_factory=list)
    role_change_summary: RoleChangeSummary | None = None


def _target_frame(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    con.execute(
        """
        SELECT pgs.game_id, pgs.player_id, pgs.team_id, g.game_date, g.season,
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
        "team_id": pl.Int64,
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

    # Role-change detection (CUSUM on minutes) runs once, up front, so its
    # cold-start boost (nba.props.role_change) can feed every downstream
    # shrinkage call -- minutes model included.
    role_change_feats = build_role_change_features(con, cfg.role_change)

    minutes_features = build_minutes_features(con)
    minutes_role_aligned = minutes_features.select(["game_id", "player_id"]).join(
        role_change_feats.select(["game_id", "player_id", "k_multiplier"]),
        on=["game_id", "player_id"],
        how="left",
    )
    minutes_k_mult = (
        minutes_role_aligned.select("k_multiplier").to_series().fill_null(1.0).to_numpy()
    )
    minutes_dists = predict_minutes(minutes_features, cfg.minutes, k_multiplier=minutes_k_mult)
    minutes_key = minutes_features.select(["game_id", "player_id"]).with_columns(
        _minutes_row_idx=pl.int_range(pl.len())
    )
    # `batch_minutes_mean_var` replaces one scipy.stats.truncnorm call *per
    # player-game* (via `.mean()`/`.var()`) with two vectorized calls over
    # every row's parameters at once -- a measurable cost at 100k+ rows.
    batch_mv = batch_minutes_mean_var(minutes_dists)
    if batch_mv is not None:
        minutes_mean_all, minutes_var_all = batch_mv
    else:
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

    role_aligned = _align(target, role_change_feats)
    role_k_multiplier = role_aligned.select("k_multiplier").to_series().fill_null(1.0).to_numpy()

    # Volatility-bucket slicing (CLAUDE.md's "model's edge concentrates in
    # high-volatility players" hypothesis). When ``cv_variant == "minutes"``
    # every stat shares one as-of minutes-CV frame -- computed once here,
    # not re-queried per stat -- per nba.props.volatility module docstring.
    minutes_cv_aligned: np.ndarray | None = None
    if cfg.volatility.enabled and cfg.volatility.cv_variant == "minutes":
        minutes_vol_feats = build_volatility_features(
            con, "minutes", cfg.volatility.lookback_games, cfg.volatility.min_games_for_cv
        )
        minutes_cv_aligned = _align(target, minutes_vol_feats).select("cv").to_series().to_numpy()

    pred_rows: list[dict[str, object]] = []
    stat_results: list[StatResult] = []
    coherence_checks: list[CoherenceCheck] = []
    component_means: dict[str, np.ndarray] = {}
    component_vars: dict[str, np.ndarray] = {}

    for stat in TARGET_STATS:
        y = target.select(stat).to_series().to_numpy().astype(float)
        thresholds = THRESHOLDS[stat]

        if stat == "pts":
            feats = build_points_features(con)
            feats_aligned = _align(target, feats)
            moments = points_moments(
                feats_aligned, minutes_mean, minutes_var, cfg.points, k_multiplier=role_k_multiplier
            )
            dists: list[Distribution] = list(build_points_distributions(moments))
            zero_inflated = False
            component_means[stat] = np.array(moments.mean, dtype=float)
            component_vars[stat] = np.array(moments.var, dtype=float)
        else:
            feats = build_count_stat_features(con, stat)
            feats_aligned = _align(target, feats)
            count_cfg = cfg.count_stats[stat]
            moments_c = count_stat_moments(
                feats_aligned, minutes_mean, minutes_var, count_cfg, k_multiplier=role_k_multiplier
            )
            base_dists = build_negbin_distributions(moments_c)
            pi = fit_zero_inflation(y, base_dists, cfg.zero_inflation)
            zero_inflated = bool(np.any(pi > 0))
            if zero_inflated:
                dists = list(build_zinb_distributions(base_dists, pi))
            else:
                dists = list(base_dists)
            if stat in ("reb", "ast"):
                component_means[stat] = np.array(moments_c.mean, dtype=float)
                component_vars[stat] = np.array(moments_c.var, dtype=float)

        coherence_check: CoherenceCheck | None = None
        if cfg.coherence.enabled:
            team_feats = _align_team(target, build_team_total_features(con, stat))
            team_totals_arr = team_total_forecast(team_feats, stat, cfg.coherence.k_team_games)
            dists, coherence_check = _reconcile_to_team_totals(target, dists, team_totals_arr, stat)
            coherence_checks.append(coherence_check)

        pred_mean = np.array([d.mean() for d in dists])
        q10, q50, q90 = _quantiles(dists)

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

        conformal_result: ConformalResult | None = None
        if cfg.conformal.enabled:
            dates_arr = target.select("game_date").to_series().to_numpy()
            conformal_result = evaluate_conformal(
                q10,
                q90,
                y,
                dates_arr,
                stat,
                alpha=cfg.conformal.alpha,
                cal_frac=cfg.conformal.cal_frac,
            )

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

        volatility_buckets: list[VolatilityBucketResult] = []
        if cfg.volatility.enabled:
            if cfg.volatility.cv_variant == "minutes":
                assert minutes_cv_aligned is not None
                cv_arr = minutes_cv_aligned
            else:
                stat_vol_feats = build_volatility_features(
                    con, stat, cfg.volatility.lookback_games, cfg.volatility.min_games_for_cv
                )
                cv_arr = _align(target, stat_vol_feats).select("cv").to_series().to_numpy()
            bucket_specs = (
                quantile_buckets(cv_arr)
                if cfg.volatility.use_quantile_cutoffs
                else cfg.volatility.buckets
            )
            bucket_labels = assign_buckets(cv_arr, bucket_specs)
            volatility_buckets = evaluate_volatility_buckets(
                stat=stat,
                cv_variant=cfg.volatility.cv_variant,
                bucket_labels=bucket_labels,
                pred_mean=pred_mean,
                q10=q10,
                q90=q90,
                y=y,
                crps_model=crps_model,
                crps_season=crps_season,
                crps_last10=crps_last10,
                ll_model=ll_model,
                ll_season=ll_season,
                ll_last10=ll_last10,
                buckets=bucket_specs,
                min_bucket_n=cfg.volatility.min_bucket_n,
                n_boot=n_boot,
                seed=seed,
            )

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
                conformal=conformal_result,
                volatility_buckets=volatility_buckets,
            )
        )

        # Precompute each threshold's P(stat >= th) for every row in one
        # vectorized call (`threshold_log_loss_and_calibration` above
        # already paid this cost once; reusing it here avoids a *second*
        # pass of one scipy call per (row, threshold) pair just to build
        # the `prop_predictions.p_ge` JSON blob).
        p_ge_by_threshold: dict[int, np.ndarray] = {}
        for th in thresholds:
            arr = batch_p_ge(dists, float(th))
            p_ge_by_threshold[th] = (
                arr if arr is not None else np.array([d.p_ge(float(th)) for d in dists])
            )

        for i in range(n):
            p_ge = {str(th): float(p_ge_by_threshold[th][i]) for th in thresholds}
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

    combo_results, combo_pred_rows = _build_combo_results(
        con, cfg, target, component_means, component_vars, n_boot=n_boot, seed=seed
    )
    combo_predictions = pl.DataFrame(combo_pred_rows) if combo_pred_rows else pl.DataFrame()

    n_flagged = int(np.sum(role_k_multiplier > 1.0 + 1e-9))
    flagged_idx = np.where(role_k_multiplier > 1.0 + 1e-9)[0][:5]
    flagged_examples = [
        {
            "game_id": target[int(i), "game_id"],
            "player_id": target[int(i), "player_id"],
            "k_multiplier": float(role_k_multiplier[int(i)]),
        }
        for i in flagged_idx
    ]
    role_change_summary = RoleChangeSummary(
        n_rows=target.height, n_flagged=n_flagged, flagged_examples=flagged_examples
    )

    dates = target.select("game_date").to_series()
    return PropsExperimentResult(
        stats=stat_results,
        predictions=predictions,
        seed=seed,
        n_boot=n_boot,
        game_date_min=cast("dt.date | None", dates.min()),
        game_date_max=cast("dt.date | None", dates.max()),
        combo_stats=combo_results,
        combo_predictions=combo_predictions,
        coherence_checks=coherence_checks,
        role_change_summary=role_change_summary,
    )


_Q_TAUS = np.array([0.10, 0.50, 0.90])


def _quantiles(dists: list[Distribution]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """q10/q50/q90 for every distribution -- one vectorized ``batch_ppf``
    call instead of three ``ppf()`` calls per row (falls back to the
    per-element loop for any family ``batch_ppf`` doesn't recognize)."""
    qs = batch_ppf(dists, _Q_TAUS)
    if qs is not None:
        return qs[:, 0], qs[:, 1], qs[:, 2]
    q10 = np.array([d.ppf(0.10) for d in dists])
    q50 = np.array([d.ppf(0.50) for d in dists])
    q90 = np.array([d.ppf(0.90) for d in dists])
    return q10, q50, q90


def _align_team(target: pl.DataFrame, feats: pl.DataFrame) -> pl.DataFrame:
    """Like :func:`_align` but keyed on (game_id, team_id) -- for team-level
    (not player-level) as-of features, e.g. ``nba.props.coherence``."""
    return target.select(["game_id", "team_id"]).join(feats, on=["game_id", "team_id"], how="left")


def _reconcile_to_team_totals(
    target: pl.DataFrame, dists: list[Distribution], team_totals: np.ndarray, stat: str
) -> tuple[list[Distribution], CoherenceCheck]:
    """Forecast-proportions reconciliation (``nba.props.coherence``) applied
    group-by-group over (game_id, team_id) -- see that module's docstring."""
    df_idx = target.select(["game_id", "team_id"]).with_columns(_row_idx=pl.int_range(pl.len()))
    reconciled = list(dists)
    group_sums: list[np.ndarray] = []
    group_totals: list[float] = []
    for _, group in df_idx.group_by(["game_id", "team_id"], maintain_order=True):
        idx_arr = group.select("_row_idx").to_series().to_numpy().astype(int)
        group_dists = [dists[i] for i in idx_arr]
        total = float(team_totals[idx_arr[0]])
        rescaled, _scale = reconcile_group(group_dists, total)
        for local_i, global_i in enumerate(idx_arr):
            reconciled[global_i] = rescaled[local_i]
        group_sums.append(np.array([d.mean() for d in rescaled]))
        group_totals.append(total)
    check = check_coherence(group_sums, group_totals, stat=stat)
    return reconciled, check


def _build_combo_results(
    con: duckdb.DuckDBPyConnection,
    cfg: PropsConfig,
    target: pl.DataFrame,
    component_means: dict[str, np.ndarray],
    component_vars: dict[str, np.ndarray],
    n_boot: int,
    seed: int,
) -> tuple[list[ComboStatResult], list[dict[str, object]]]:
    """PRA / P+R / P+A / R+A distributions -- see ``nba.props.combos`` module
    docstring for the correlation method and its stated assumption."""
    if not cfg.combos.enabled or not all(s in component_means for s in ("pts", "reb", "ast")):
        return [], []

    correlation = estimate_within_player_correlation(con, cfg.combos.min_pairs_for_corr)
    y_by_stat = {
        s: target.select(s).to_series().to_numpy().astype(float) for s in ("pts", "reb", "ast")
    }

    results: list[ComboStatResult] = []
    pred_rows: list[dict[str, object]] = []
    for combo_name, components in COMBOS.items():
        moments_combo = combo_moments(component_means, component_vars, components, correlation)
        combo_dists: list[Distribution] = list(build_combo_distributions(moments_combo))
        y_combo: np.ndarray = np.sum(np.stack([y_by_stat[s] for s in components]), axis=0)
        thresholds_combo = COMBO_THRESHOLDS[combo_name]

        pred_mean_c = np.array([d.mean() for d in combo_dists])
        q10_c, q50_c, q90_c = _quantiles(combo_dists)

        n_c = len(y_combo)
        bias_ci_c = mean_bias_ci(pred_mean_c, y_combo, n_boot=n_boot, seed=seed)
        coverage_c = interval_coverage(q10_c, q90_c, y_combo)
        threshold_cal_c = threshold_log_loss_and_calibration(combo_dists, y_combo, thresholds_combo)
        pooled_ece_c = pooled_threshold_ece(threshold_cal_c)
        crps_c = crps_array(combo_dists, y_combo)
        note_c = (
            f"only {n_c} player-games (<{MIN_RELIABLE_N}); NOT statistically meaningful at this "
            "sample size -- same caveat as every base-stat metric in this report."
            if n_c < MIN_RELIABLE_N
            else ""
        )
        corr_used = {
            f"{a}_{b}": float(correlation.get((a, b), correlation.get((b, a), 0.0)))
            for a, b in (("pts", "reb"), ("pts", "ast"), ("reb", "ast"))
        }
        results.append(
            ComboStatResult(
                stat=combo_name,
                n=n_c,
                dist_family=combo_dists[0].family if combo_dists else "none",
                mean_bias=bias_ci_c,
                coverage=coverage_c,
                pooled_ece=pooled_ece_c,
                crps_point=float(np.mean(crps_c)) if len(crps_c) else float("nan"),
                correlation_used=corr_used,
                note=note_c,
            )
        )
        p_ge_by_threshold_c: dict[int, np.ndarray] = {}
        for th in thresholds_combo:
            arr = batch_p_ge(combo_dists, float(th))
            p_ge_by_threshold_c[th] = (
                arr if arr is not None else np.array([d.p_ge(float(th)) for d in combo_dists])
            )

        for i in range(n_c):
            p_ge = {str(th): float(p_ge_by_threshold_c[th][i]) for th in thresholds_combo}
            pred_rows.append(
                {
                    "game_id": target[i, "game_id"],
                    "player_id": target[i, "player_id"],
                    "stat": combo_name,
                    "mean": float(pred_mean_c[i]),
                    "dist_family": combo_dists[i].family,
                    "dist_params": json.dumps(combo_dists[i].params()),
                    "p_ge": json.dumps(p_ge),
                    "q10": float(q10_c[i]),
                    "q50": float(q50_c[i]),
                    "q90": float(q90_c[i]),
                }
            )
    return results, pred_rows


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
