"""Descriptive monitor for the pts lower-tail SHADOW (docs/LOWER_TAIL.md, "Live shadow").

The BINDING scoring rule is section 4/5 of docs/FORWARD_PREREG_2026_27.md (frozen before any
2026-27 row existed): paired integer-support pts CRPS vs production, comparator from the SAME
``run_id``, floor -0.005, looks L30/L60/L120/END, no efficacy claim before 120 distinct dates and
10,000 paired player-games, alpha spent over the looks. This module does NOT decide anything: it
computes the paired delta with a date-clustered 95% interval so the shadow can be watched while it
accumulates, and reports whether the minimum sample for an efficacy look has been reached.

Pairing: both arms' stored 19-quantile grids mapped with ``ceil(q - 0.5)`` (BEST_PRACTICES), scored
with the empirical CRPS (``2 * mean pinball``); played rows only (minutes > 0); for each game-player
the latest ``run_id`` that logged BOTH arms. Percentile bootstrap resampling whole game dates
(2000 resamples, seed 0). Read-only.

CLI: ``uv run python -m nba.daily.lt_live [--season 2026]``.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import duckdb
import numpy as np

from nba.daily.predict import CONTEXT_LT_PROPS_MODEL_NAME, CONTEXT_PROPS_MODEL_NAME
from nba.daily.report import cluster_bootstrap_mean
from nba.props.context_residual import to_integer_support
from nba.props.forward import QUANTILE_TAUS

# minimum sample for an efficacy look (docs/FORWARD_PREREG_2026_27.md section 5, "LT pts")
MIN_PLAYER_GAMES = 10_000
MIN_DATES = 120
FLOOR = -0.005
N_BOOT = 2000
SEED = 0


def integer_crps(grid: list[float], y: float) -> float:
    """CRPS of a stored 19-grid after the integer-support map."""
    q = to_integer_support(np.asarray(grid, dtype=float))
    t = np.asarray(QUANTILE_TAUS, dtype=float)
    d = y - q
    return float(2.0 * np.mean(np.maximum(t * d, (t - 1.0) * d)))


def paired_rows(con: duckdb.DuckDBPyConnection, season: int = 2026) -> list[tuple[Any, ...]]:
    """(game_date, game_id, player_id, grid_primary, grid_lt, y) for settled, played pts rows;
    both arms from the same run, the latest run that has both."""
    return con.execute(
        """
        WITH pairs AS (
            SELECT a.game_id, a.player_id, a.run_id, a.made_at,
                   a.prediction ->> '$.q_grid' AS ga, b.prediction ->> '$.q_grid' AS gb,
                   row_number() OVER (PARTITION BY a.game_id, a.player_id
                                      ORDER BY a.made_at DESC, a.run_id DESC) rn
            FROM forward_predictions a JOIN forward_predictions b
              ON a.run_id = b.run_id AND a.game_id = b.game_id AND a.player_id = b.player_id
                 AND a.target = b.target
            WHERE a.target = 'pts' AND a.player_id <> -1
              AND a.model_name = ? AND b.model_name = ? AND a.made_at < a.tipoff)
        SELECT g.game_date, p.game_id, p.player_id, p.ga, p.gb, s.pts
        FROM pairs p JOIN games g ON g.game_id = p.game_id
        JOIN player_game_stats s ON s.game_id = p.game_id AND s.player_id = p.player_id
        WHERE p.rn = 1 AND g.season = ? AND g.home_pts > 0 AND g.away_pts > 0 AND s.minutes > 0
        ORDER BY g.game_date, p.game_id, p.player_id
        """,
        [CONTEXT_PROPS_MODEL_NAME, CONTEXT_LT_PROPS_MODEL_NAME, season],
    ).fetchall()


def evaluate_pairs(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    """Paired delta (lt - production) on :func:`paired_rows` output; descriptive only."""
    d = np.array(
        [
            integer_crps(json.loads(r[4]), float(r[5]))
            - integer_crps(json.loads(r[3]), float(r[5]))
            for r in rows
        ]
    )
    dates = np.array([str(r[0]) for r in rows])
    ci = cluster_bootstrap_mean(d, dates, N_BOOT, SEED)
    eligible = ci.n >= MIN_PLAYER_GAMES and ci.n_clusters >= MIN_DATES
    return {
        "n_player_games": ci.n,
        "n_dates": ci.n_clusters,
        "delta_crps": ci.point,
        "ci95": [ci.lo, ci.hi],
        "min_player_games": MIN_PLAYER_GAMES,
        "min_dates": MIN_DATES,
        "floor": FLOOR,
        "efficacy_look_eligible": eligible,
        "status": "descriptive_monitor_see_FORWARD_PREREG_2026_27"
        if eligible
        else "keep_shadowing_below_minimum_sample",
    }


def evaluate(con: duckdb.DuckDBPyConnection, season: int = 2026) -> dict[str, Any]:
    return evaluate_pairs(paired_rows(con, season))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    p.add_argument("--db-path", default="nba.duckdb")
    p.add_argument("--season", type=int, default=2026)
    args = p.parse_args(argv)
    con = duckdb.connect(args.db_path, read_only=True)
    print(json.dumps(evaluate(con, args.season), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
