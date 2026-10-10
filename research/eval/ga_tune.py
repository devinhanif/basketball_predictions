"""Genetic-algorithm hyperparameter tuner for ``MovEloBaseline``.

Uses ``scipy.optimize.differential_evolution`` -- a real genetic algorithm
(population of candidate parameter vectors, mutation via differential
vectors, crossover, selection each generation), not a grid/random search.

**Anti-overfit core (read this before changing anything here):** the GA's
objective function is *pooled walk-forward out-of-fold (OOF) log loss*,
computed only over whatever ``tunable_df``/``tunable_seasons`` the caller
passes in. This module never reads ``holdout_season`` games into the
objective -- :func:`tune_mov_elo_ga` only ever touches the frozen holdout
to *carve it out* (via ``nba.truth.walkforward.split_frozen_holdout``), the
same no-leakage helper the rung-0..2 harness in ``research/eval/run.py`` uses.
If a future edit adds a holdout read inside :func:`objective` or
:func:`walk_forward_oof_log_loss`, ``tests/ml/test_ga_tuner.py`` has a
test that constructs a holdout row with a planted, out-of-range team id
and asserts the tuner never evaluates on it -- it will fail loudly.

Maintainer workflow for the REAL (non-fixture) tuning run::

    uv run python -m research.eval.ga_tune \\
        --db-path nba.duckdb \\
        --holdout-season 2025 \\
        --seed 0 --maxiter 40 --popsize 15

This prints the best hyperparameters and the pooled walk-forward OOF log
loss they achieved on every season *except* 2025 (the frozen holdout).
Add ``--evaluate-holdout`` to also run a strictly confirmatory (never
tuned) sequential walk-forward of the tuned model through the holdout
season, exactly once, and print its log loss/Brier alongside -- do not
re-tune afterward based on that number.
"""

from __future__ import annotations

import argparse

import duckdb
import numpy as np
import polars as pl
from scipy.optimize import differential_evolution

from nba.db.connect import connect
from nba.features.team_features import build_matchup_features
from nba.models.rung0_baselines import MovEloBaseline
from nba.truth.metrics import brier_score, log_loss
from nba.truth.walkforward import Fold, make_walk_forward_folds, split_frozen_holdout

#: Order of the parameter vector ``differential_evolution`` optimizes over.
PARAM_NAMES: tuple[str, ...] = (
    "k_factor",
    "home_advantage_elo",
    "season_carryover",
    "mov_c",
    "mov_div",
)

#: Sensible default search bounds per CLAUDE.md's cold-start/architecture
#: guidance -- wide enough to let the GA find a real optimum, narrow
#: enough to stay physically sane for an NBA Elo rating.
DEFAULT_BOUNDS: dict[str, tuple[float, float]] = {
    "k_factor": (5.0, 60.0),
    "home_advantage_elo": (0.0, 150.0),
    "season_carryover": (0.5, 1.0),
    "mov_c": (1.0, 5.0),
    "mov_div": (0.0005, 0.005),
}

#: Returned when a candidate's walk-forward produces no folds/finite loss
#: (e.g. degenerate synthetic data) -- a large-but-finite penalty so the
#: GA still has a gradient to move away from instead of crashing on NaN.
_INFEASIBLE_PENALTY = 1.0e6


def params_from_vector(x: np.ndarray) -> dict[str, float]:
    """Map a raw ``differential_evolution`` candidate vector to named params."""
    return {name: float(v) for name, v in zip(PARAM_NAMES, x, strict=True)}


def _vector_from_params(params: dict[str, float]) -> list[float]:
    return [params[name] for name in PARAM_NAMES]


def walk_forward_oof_log_loss(
    tunable_df: pl.DataFrame,
    params: dict[str, float],
    seed: int,
    min_train_games: int = 1,
) -> float:
    """Pooled walk-forward out-of-fold log loss for MOV-Elo on ``tunable_df`` only.

    This is the GA's objective function's inner evaluation. It has no
    concept of a holdout season at all -- by construction, it only ever
    sees whatever rows ``tunable_df`` contains. Callers (see
    :func:`tune_mov_elo_ga`) are responsible for having already removed
    the frozen holdout season before this function is ever called.
    """
    folds: list[Fold] = make_walk_forward_folds(tunable_df, min_train_games=min_train_games)
    if not folds:
        return float("nan")
    ys: list[np.ndarray] = []
    ps: list[np.ndarray] = []
    for fold in folds:
        y_train = fold.train_df.select("y").to_series().to_numpy()
        model = MovEloBaseline(seed=seed, **params)
        model.fit(fold.train_df, y_train)
        preds = model.predict(fold.test_df)
        ys.append(fold.test_df.select("y").to_series().to_numpy().astype(float))
        ps.append(preds)
    y_all = np.concatenate(ys)
    p_all = np.concatenate(ps)
    if y_all.size == 0:
        return float("nan")
    return float(log_loss(y_all, p_all))


def tune_mov_elo_ga(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    tunable_df: pl.DataFrame | None = None,
    tunable_seasons: list[int] | None = None,
    holdout_season: int | None = None,
    seed: int = 0,
    bounds: dict[str, tuple[float, float]] | None = None,
    maxiter: int = 15,
    popsize: int = 10,
    min_train_games: int = 1,
) -> tuple[dict[str, float], float]:
    """Genetic-algorithm search for MOV-Elo hyperparameters.

    Objective: pooled walk-forward OOF log loss on the TUNABLE seasons
    only (never the frozen holdout -- see module docstring).

    Exactly one of ``tunable_df`` or ``con`` must be usable to produce
    the tunable dataset:

    - If ``tunable_df`` is passed directly (e.g. a tiny synthetic frame
      in tests), it is used as-is -- the caller is asserting it has
      *already* excluded the holdout.
    - Otherwise ``con`` is read via ``build_matchup_features`` and
      ``nba.truth.walkforward.split_frozen_holdout(matchup_df,
      holdout_season)`` is applied, so the holdout season's rows are
      dropped *before* a single objective evaluation happens -- the GA
      literally never receives them.

    ``tunable_seasons`` (optional) further restricts the tunable data to
    a specific season list -- useful to shrink a real multi-season
    backtest to a faster subset while developing, still strictly
    excluding the holdout season either way.

    Returns ``(best_params, best_oof_log_loss)``. ``differential_evolution``
    is called with ``seed=seed`` and ``workers=1`` (no multiprocessing,
    for determinism) so repeated calls with the same inputs are bit-for-bit
    reproducible -- see ``tests/ml/test_ga_tuner.py::test_ga_tuner_is_deterministic``.
    """
    if tunable_df is None:
        if con is None:
            raise ValueError("either `con` or `tunable_df` must be provided")
        matchup_df = build_matchup_features(con)
        split = split_frozen_holdout(matchup_df, holdout_season)
        tunable_df = split.tunable_df

    if tunable_seasons is not None and "season" in tunable_df.columns:
        tunable_df = tunable_df.filter(pl.col("season").is_in(tunable_seasons))

    resolved_bounds = dict(DEFAULT_BOUNDS)
    if bounds:
        resolved_bounds.update(bounds)
    bounds_list = [resolved_bounds[name] for name in PARAM_NAMES]

    frozen_tunable_df = tunable_df

    def objective(x: np.ndarray) -> float:
        params = params_from_vector(x)
        loss = walk_forward_oof_log_loss(
            frozen_tunable_df, params, seed=seed, min_train_games=min_train_games
        )
        return loss if np.isfinite(loss) else _INFEASIBLE_PENALTY

    result = differential_evolution(
        objective,
        bounds_list,
        seed=seed,
        maxiter=maxiter,
        popsize=popsize,
        polish=False,
        updating="deferred",
        workers=1,
        init="latinhypercube",
    )
    best_params = params_from_vector(np.asarray(result.x))
    return best_params, float(result.fun)


def evaluate_mov_elo_on_holdout(
    con: duckdb.DuckDBPyConnection,
    params: dict[str, float],
    holdout_season: int,
    seed: int = 0,
) -> dict[str, object]:
    """Strictly confirmatory: score tuned MOV-Elo on the frozen holdout once.

    Mirrors ``research.eval.run._evaluate_holdout``'s sequential-refit pattern
    (ratings must be warm-started on tunable history and updated through
    each earlier holdout game, not predicted from a single stale fit) but
    specialized to one model with caller-supplied params, since the GA
    tuner's whole point is to evaluate parameter sets ``run_experiment``'s
    fixed ``RUNG_SPECS`` never sees. Call this *after* :func:`tune_mov_elo_ga`
    has already picked ``params`` from tunable-only data -- never inside
    the objective, and never more than once per tuning run (re-tuning
    based on this number defeats the point of a frozen holdout).
    """
    matchup_df = build_matchup_features(con)
    split = split_frozen_holdout(matchup_df, holdout_season)
    if split.holdout_df.height == 0:
        return {"n": 0, "note": split.note}

    combined = pl.concat([split.tunable_df, split.holdout_df], how="vertical")
    holdout_dates = set(split.holdout_df.select("game_date").unique().to_series().to_list())
    all_folds = make_walk_forward_folds(combined, min_train_games=1)
    folds = [f for f in all_folds if f.test_date in holdout_dates]

    p_by_game: dict[str, float] = {}
    for fold in folds:
        model = MovEloBaseline(seed=seed, **params)
        y_train = fold.train_df.select("y").to_series().to_numpy()
        model.fit(fold.train_df, y_train)
        preds = model.predict(fold.test_df)
        for game_id, p in zip(
            fold.test_df.select("game_id").to_series().to_list(), preds, strict=True
        ):
            p_by_game[game_id] = float(p)

    ordered_holdout = split.holdout_df.sort(["game_date", "game_id"])
    ids = ordered_holdout.select("game_id").to_series().to_list()
    y = ordered_holdout.select("y").to_series().to_numpy().astype(float)
    p = np.array([p_by_game[g] for g in ids], dtype=float)
    return {
        "n": len(y),
        "log_loss": log_loss(y, p),
        "brier": brier_score(y, p),
        "params": params,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m research.eval.ga_tune",
        description=(
            "Genetic-algorithm (differential evolution) tuner for MOV-Elo. "
            "Objective is walk-forward OOF log loss on tunable seasons only; "
            "the holdout season is never touched by tuning."
        ),
    )
    parser.add_argument("--db-path", default="nba.duckdb")
    parser.add_argument("--holdout-season", type=int, default=2025)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--maxiter", type=int, default=15)
    parser.add_argument("--popsize", type=int, default=10)
    parser.add_argument("--min-train-games", type=int, default=1)
    parser.add_argument(
        "--evaluate-holdout",
        action="store_true",
        help="after tuning, run ONE confirmatory eval of the tuned params on the frozen holdout",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    # Read-only: tuning only ever reads game/feature data, never writes
    # (no --register path here; this module never touches nba.duckdb's
    # schema or rows).
    con = connect(args.db_path, read_only=True)
    try:
        best_params, best_oof_log_loss = tune_mov_elo_ga(
            con,
            holdout_season=args.holdout_season,
            seed=args.seed,
            maxiter=args.maxiter,
            popsize=args.popsize,
            min_train_games=args.min_train_games,
        )
        print("best_params:", best_params)
        print("best_walk_forward_oof_log_loss (tunable seasons only):", best_oof_log_loss)
        if args.evaluate_holdout:
            holdout_result = evaluate_mov_elo_on_holdout(
                con, best_params, args.holdout_season, seed=args.seed
            )
            print(f"confirmatory holdout (season={args.holdout_season}) result:", holdout_result)
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
