"""Frozen live scoring rule for the pts lower-tail SHADOW (docs/LOWER_TAIL.md, "Live rule").

Paired CRPS, ``props_context_residual_lt`` minus ``props_context_residual`` (pts only), on
integer support for BOTH arms: each stored 19-quantile grid is mapped with ``ceil(q - 0.5)``
(docs/BEST_PRACTICES.md) and scored with the empirical CRPS ``2 * mean pinball``. Only rows
where both arms were logged for the same game-player and the player actually played
(minutes > 0) count; DNPs are excluded exactly as in the pre-registered conditional-on-playing
design. The 95% interval is a percentile bootstrap resampling whole game DATES (same-night
correlation), 2000 resamples, seed 0. Read-only; the constants below are frozen with the doc
before opening night and must not be edited after the first lt row is logged.

CLI: ``uv run python -m nba.daily.lt_live [--season 2026]`` (opens the DB read-only).
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

# ---- frozen live rule (docs/LOWER_TAIL.md) ----
MIN_PLAYER_GAMES = 500
MIN_DATES = 14
EFFECT_FLOOR = -0.005  # absolute integer-support CRPS units; point estimate must be <= this
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
    latest prediction per (game, model, player)."""
    return con.execute(
        """
        WITH latest AS (
            SELECT *, row_number() OVER (
                PARTITION BY game_id, model_name, target, player_id
                ORDER BY made_at DESC, run_id DESC) rn
            FROM forward_predictions
            WHERE target = 'pts' AND player_id <> -1 AND model_name IN (?, ?))
        SELECT g.game_date, a.game_id, a.player_id,
               a.prediction ->> '$.q_grid', b.prediction ->> '$.q_grid', s.pts
        FROM latest a JOIN latest b
          ON a.game_id = b.game_id AND a.player_id = b.player_id AND b.rn = 1
        JOIN games g ON g.game_id = a.game_id
        JOIN player_game_stats s ON s.game_id = a.game_id AND s.player_id = a.player_id
        WHERE a.rn = 1 AND a.model_name = ? AND b.model_name = ?
          AND g.season = ? AND g.home_pts > 0 AND g.away_pts > 0 AND s.minutes > 0
        ORDER BY g.game_date, a.game_id, a.player_id
        """,
        [
            CONTEXT_PROPS_MODEL_NAME,
            CONTEXT_LT_PROPS_MODEL_NAME,
            CONTEXT_PROPS_MODEL_NAME,
            CONTEXT_LT_PROPS_MODEL_NAME,
            season,
        ],
    ).fetchall()


def evaluate_pairs(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    """Apply the frozen rule to :func:`paired_rows` output."""
    d = np.array(
        [
            integer_crps(json.loads(r[4]), float(r[5]))
            - integer_crps(json.loads(r[3]), float(r[5]))
            for r in rows
        ]
    )
    dates = np.array([str(r[0]) for r in rows])
    ci = cluster_bootstrap_mean(d, dates, N_BOOT, SEED)
    enough = ci.n >= MIN_PLAYER_GAMES and ci.n_clusters >= MIN_DATES
    if not enough:
        verdict = "insufficient_n_no_claim"
    elif ci.point <= EFFECT_FLOOR and ci.hi < 0:
        verdict = "lt_better"
    elif ci.lo > 0:
        verdict = "lt_worse"
    else:
        verdict = "no_claim"
    return {
        "n_player_games": ci.n,
        "n_dates": ci.n_clusters,
        "delta_crps": ci.point,
        "ci95": [ci.lo, ci.hi],
        "min_player_games": MIN_PLAYER_GAMES,
        "min_dates": MIN_DATES,
        "effect_floor": EFFECT_FLOOR,
        "verdict": verdict,
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
