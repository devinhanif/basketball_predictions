"""Orchestrates the rung-0..2 walk-forward backtest end to end.

train (per fold) -> predict (held-out date) -> aggregate out-of-fold
predictions -> metrics + slices + paired bootstrap ladder comparisons ->
report.md -> registry logging. Entry point: ``python -m nba.eval``.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

import duckdb
import numpy as np
import polars as pl

from nba.eval.metrics import (
    ConfidenceInterval,
    PairedBootstrapComparison,
    accuracy,
    bootstrap_ci,
    brier_score,
    calibration_curve,
    log_loss,
    paired_bootstrap_compare,
)
from nba.eval.slices import (
    cold_start_bucket_slice,
    favorite_underdog_slices,
    rest_slices,
    season_phase_slices,
)
from nba.eval.walkforward import (
    make_walk_forward_folds,
    split_frozen_holdout,
    walk_forward_data_sufficiency_note,
)
from nba.features.player_features import build_game_cold_start_flags
from nba.features.team_features import build_matchup_features
from nba.models.base import RungModel
from nba.models.rung0_baselines import EloBaseline, HomeCourtBaseline
from nba.models.rung1_logistic import LogisticRung
from nba.models.rung2_gbm import LightGBMRung

#: Ladder order: each entry is compared against the *previous* entry (per
#: CLAUDE.md "a rung is kept only if it beats the previous on walk-forward
#: log loss"). The two rung-0 baselines are compared against each other
#: first; rung 1 is compared against whichever rung-0 baseline had the
#: lower walk-forward log loss; rung 2 against rung 1.
RungFactory = Callable[[int], RungModel]

RUNG_SPECS: list[tuple[str, int, str, RungFactory]] = [
    ("rung0_home_court", 0, "home_court_baseline", lambda seed: HomeCourtBaseline(seed=seed)),
    ("rung0_elo", 0, "elo_baseline", lambda seed: EloBaseline(seed=seed)),
    ("rung1_logistic", 1, "logistic_regression", lambda seed: LogisticRung(seed=seed)),
    ("rung2_lightgbm", 2, "lightgbm", lambda seed: LightGBMRung(seed=seed)),
]


@dataclass
class RungMetricBundle:
    name: str
    rung: int
    model_method: str
    log_loss: ConfidenceInterval
    brier: ConfidenceInterval
    accuracy_point: float
    ece: float
    n_games: int
    config: dict[str, object]
    slice_metrics: dict[str, dict[str, object]] = field(default_factory=dict)
    holdout: dict[str, object] | None = None


@dataclass
class ExperimentResult:
    oof: pl.DataFrame
    rungs: list[RungMetricBundle]
    ladder_comparisons: list[PairedBootstrapComparison]
    holdout_note: str
    walk_forward_note: str
    n_folds: int
    seed: int
    game_date_min: dt.date | None
    game_date_max: dt.date | None


def _run_walk_forward(
    tunable_df: pl.DataFrame, seed: int, min_train_games: int, n_boot: int
) -> tuple[pl.DataFrame, list[RungMetricBundle], dict[str, RungModel]]:
    folds = make_walk_forward_folds(tunable_df, min_train_games=min_train_games)

    oof_rows: list[dict[str, object]] = []
    last_models: dict[str, RungModel] = {}

    for fold in folds:
        y_train = fold.train_df.select("y").to_series().to_numpy()
        for name, _rung, _method, factory in RUNG_SPECS:
            model = factory(seed)
            model.fit(fold.train_df, y_train)
            last_models[name] = model
            preds = model.predict(fold.test_df)
            for (game_id, as_of, y), p in zip(
                fold.test_df.select(["game_id", "as_of", "y"]).iter_rows(), preds, strict=True
            ):
                oof_rows.append(
                    {
                        "fold_index": fold.fold_index,
                        "game_id": game_id,
                        "as_of": as_of,
                        "y": y,
                        "model_name": name,
                        "p": float(p),
                    }
                )

    if not oof_rows:
        oof_long = pl.DataFrame(
            schema={
                "fold_index": pl.Int64,
                "game_id": pl.Utf8,
                "as_of": pl.Date,
                "y": pl.Int64,
                "model_name": pl.Utf8,
                "p": pl.Float64,
            }
        )
    else:
        oof_long = pl.DataFrame(oof_rows)

    oof_wide = (
        oof_long.pivot(on="model_name", index=["fold_index", "game_id", "as_of", "y"], values="p")
        if oof_long.height
        else oof_long
    )

    bundles: list[RungMetricBundle] = []
    for name, rung, method, _factory in RUNG_SPECS:
        if oof_long.height == 0 or name not in oof_long.get_column("model_name").unique().to_list():
            p = np.array([])
            y = np.array([])
        else:
            rows = oof_long.filter(pl.col("model_name") == name)
            y = rows.select("y").to_series().to_numpy().astype(float)
            p = rows.select("p").to_series().to_numpy().astype(float)
        bundle = _build_metric_bundle(name, rung, method, y, p, tunable_df, oof_wide, seed, n_boot)
        if name in last_models:
            bundle.config = last_models[name].get_config()
        bundles.append(bundle)

    return oof_wide, bundles, last_models


def _build_metric_bundle(
    name: str,
    rung: int,
    method: str,
    y: np.ndarray,
    p: np.ndarray,
    tunable_df: pl.DataFrame,
    oof_wide: pl.DataFrame,
    seed: int,
    n_boot: int,
) -> RungMetricBundle:
    ll_ci = bootstrap_ci(
        np.array([log_loss(y[i : i + 1], p[i : i + 1]) for i in range(len(y))])
        if len(y)
        else np.array([]),
        stat_fn=np.mean,
        n_boot=n_boot,
        seed=seed,
    )
    brier_ci = bootstrap_ci(
        np.array([brier_score(y[i : i + 1], p[i : i + 1]) for i in range(len(y))])
        if len(y)
        else np.array([]),
        stat_fn=np.mean,
        n_boot=n_boot,
        seed=seed,
    )
    cal = calibration_curve(y, p) if len(y) else calibration_curve(np.array([]), np.array([]))

    slice_metrics: dict[str, dict[str, object]] = {}
    if len(y) and oof_wide.height and name in oof_wide.columns:
        merge_keys = oof_wide.select(["fold_index", "game_id", "as_of", "y", name]).rename(
            {name: "p"}
        )
        slice_df = merge_keys.join(
            tunable_df.select(
                [
                    "game_id",
                    "home_b2b",
                    "away_b2b",
                    "games_played_prior_min",
                    "any_starter_cold_start",
                ]
            ),
            on="game_id",
            how="left",
        )
        slice_defs: dict[str, np.ndarray] = {}
        slice_defs.update(rest_slices(slice_df))
        slice_defs.update(season_phase_slices(slice_df))
        slice_defs.update(cold_start_bucket_slice(slice_df))
        p_slice = slice_df.select("p").to_series().to_numpy()
        slice_defs.update(favorite_underdog_slices(p_slice))
        y_slice = slice_df.select("y").to_series().to_numpy().astype(float)
        for slice_name, mask in slice_defs.items():
            n_slice = int(mask.sum())
            if n_slice == 0:
                slice_metrics[slice_name] = {"n": 0, "note": "no games in this slice"}
                continue
            slice_metrics[slice_name] = {
                "n": n_slice,
                "log_loss": log_loss(y_slice[mask], p_slice[mask]),
                "brier": brier_score(y_slice[mask], p_slice[mask]),
            }

    return RungMetricBundle(
        name=name,
        rung=rung,
        model_method=method,
        log_loss=ll_ci,
        brier=brier_ci,
        accuracy_point=accuracy(y, p) if len(y) else float("nan"),
        ece=cal.ece,
        n_games=len(y),
        config={},
        slice_metrics=slice_metrics,
    )


def _ladder_comparisons(
    oof_wide: pl.DataFrame, seed: int, n_boot: int
) -> list[PairedBootstrapComparison]:
    comparisons: list[PairedBootstrapComparison] = []
    if oof_wide.height == 0:
        return comparisons
    pairs = [
        ("rung0_elo", "rung0_home_court"),
        ("rung1_logistic", "rung0_elo"),
        ("rung2_lightgbm", "rung1_logistic"),
    ]
    for a_name, b_name in pairs:
        if a_name not in oof_wide.columns or b_name not in oof_wide.columns:
            continue
        rows = oof_wide.select(["y", a_name, b_name]).drop_nulls()
        if rows.height == 0:
            continue
        y = rows.select("y").to_series().to_numpy().astype(float)
        p_a = rows.select(a_name).to_series().to_numpy().astype(float)
        p_b = rows.select(b_name).to_series().to_numpy().astype(float)
        comparisons.append(
            paired_bootstrap_compare(
                y, p_a, p_b, log_loss, f"{a_name}_vs_{b_name}_log_loss", n_boot=n_boot, seed=seed
            )
        )
    return comparisons


def _evaluate_holdout(
    holdout_df: pl.DataFrame, tunable_df: pl.DataFrame, seed: int
) -> dict[str, dict[str, object]]:
    """Confirmatory-only: fit each rung on *all* tunable data, score once on the frozen holdout.

    Never used to pick hyperparameters or compare rungs for the "kept"
    decision -- that happens entirely on the walk-forward folds above.
    """
    if holdout_df.height == 0:
        return {}
    y_train = tunable_df.select("y").to_series().to_numpy()
    y_holdout = holdout_df.select("y").to_series().to_numpy().astype(float)
    out: dict[str, dict[str, object]] = {}
    for name, _rung, _method, factory in RUNG_SPECS:
        model = factory(seed)
        model.fit(tunable_df, y_train)
        p = model.predict(holdout_df)
        out[name] = {
            "n": len(y_holdout),
            "log_loss": log_loss(y_holdout, p),
            "brier": brier_score(y_holdout, p),
        }
    return out


def run_experiment(
    con: duckdb.DuckDBPyConnection,
    seed: int = 0,
    holdout_season: int | None = None,
    min_train_games: int = 1,
    n_boot: int = 2000,
) -> ExperimentResult:
    start = time.monotonic()
    matchup_df = build_matchup_features(con)
    cold_start_flags = build_game_cold_start_flags(con)
    if cold_start_flags.height:
        matchup_df = matchup_df.join(cold_start_flags, on="game_id", how="left")
    else:
        matchup_df = matchup_df.with_columns(any_starter_cold_start=pl.lit(None, dtype=pl.Boolean))
    holdout_split = split_frozen_holdout(matchup_df, holdout_season)

    folds = make_walk_forward_folds(holdout_split.tunable_df, min_train_games=min_train_games)
    n_dates = holdout_split.tunable_df.select("game_date").unique().height
    wf_note = walk_forward_data_sufficiency_note(n_dates)

    oof_wide, bundles, _models = _run_walk_forward(
        holdout_split.tunable_df, seed=seed, min_train_games=min_train_games, n_boot=n_boot
    )

    holdout_metrics = _evaluate_holdout(holdout_split.holdout_df, holdout_split.tunable_df, seed)
    for bundle in bundles:
        bundle.holdout = holdout_metrics.get(bundle.name)

    ladder = _ladder_comparisons(oof_wide, seed=seed, n_boot=n_boot)

    dates = matchup_df.select("game_date").to_series()
    game_date_min = cast("dt.date | None", dates.min()) if matchup_df.height else None
    game_date_max = cast("dt.date | None", dates.max()) if matchup_df.height else None

    _ = time.monotonic() - start
    return ExperimentResult(
        oof=oof_wide,
        rungs=bundles,
        ladder_comparisons=ladder,
        holdout_note=holdout_split.note,
        walk_forward_note=wf_note,
        n_folds=len(folds),
        seed=seed,
        game_date_min=game_date_min,
        game_date_max=game_date_max,
    )
