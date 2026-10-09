"""Descriptive check of the integer-support quantile transform on stored OOF predictions.

Pre-registration: docs/INTEGER_QUANTILES.md. Reads the production context_residual OOF file
(19-quantile grid, the grid the daily settler scores) and the played-row labels from
``player_game_stats`` through a short-lived ``read_only`` connection, then compares
production-as-is against ``ceil(q - 0.5)`` on the same rows. Season 2025 is refused.

    uv run python -m nba.eval.integer_quantiles_eval --season 2023
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.eval.metrics import calibration_curve, log_loss
from nba.props.config import THRESHOLDS
from nba.props.context_residual import QUANTILE_TAUS, to_integer_support
from nba.props.metrics import (
    interval_coverage,
    mean_bias_ci,
    paired_score_delta_ci,
)

OOF_PATH = Path("reports/context_residual/oof_context_residual.parquet")
STATS = ("reb", "ast", "fg3m", "pts")
EPS = 0.005


def grid_crps(q: np.ndarray, y: np.ndarray, taus: np.ndarray) -> np.ndarray:
    d = y[:, None] - q
    pin = np.where(d >= 0, taus[None, :] * d, (taus[None, :] - 1.0) * d)
    return np.asarray(2.0 * pin.mean(axis=1))


def p_ge_grid(q: np.ndarray, n: int) -> np.ndarray:
    return np.asarray((q > n - 0.5).mean(axis=1))


def _ci(delta: np.ndarray, gid: np.ndarray, n_boot: int) -> dict[str, float]:
    c = paired_score_delta_ci(delta, np.zeros_like(delta), n_boot=n_boot, cluster_ids=gid)
    return {"point": c.point, "lo": c.lo, "hi": c.hi}


def evaluate(
    oof: pl.DataFrame, labels: pl.DataFrame, season: int, n_boot: int = 2000
) -> dict[str, Any]:
    if season >= 2025:
        raise ValueError("season 2025 is the frozen holdout: not evaluated here")
    taus = np.array(QUANTILE_TAUS)
    out: dict[str, Any] = {}
    for stat in STATS:
        d = (
            oof.filter((pl.col("target") == stat) & (pl.col("season") == season))
            .join(
                labels.select(["game_id", "player_id", pl.col(stat).alias("y")]),
                on=["game_id", "player_id"],
                how="inner",
            )
            .sort(["game_id", "player_id"])
        )
        q = np.array(d["q_grid"].to_list(), dtype=float)
        y = d["y"].to_numpy().astype(float)
        gid = d["game_id"].to_numpy()
        qi = to_integer_support(q)
        c0, c1 = grid_crps(q, y, taus), grid_crps(qi, y, taus)
        mean = d["mean"].to_numpy()
        ll0, ll1, ece0, ece1, dmax = [], [], [], [], 0.0
        for n in THRESHOLDS[stat]:
            ind = (y >= n).astype(float)
            p0, p1 = np.clip(p_ge_grid(q, n), EPS, 1 - EPS), np.clip(p_ge_grid(qi, n), EPS, 1 - EPS)
            dmax = max(dmax, float(np.abs(p_ge_grid(q, n) - p_ge_grid(qi, n)).max()))
            ll0.append(log_loss(ind, p0))
            ll1.append(log_loss(ind, p1))
            ece0.append(calibration_curve(ind, p0).ece)
            ece1.append(calibration_curve(ind, p1).ece)
        b = mean_bias_ci(mean, y, n_boot=n_boot, cluster_ids=gid)
        out[stat] = {
            "n": int(len(y)),
            "n_games": int(len(np.unique(gid))),
            "crps_asis": float(c0.mean()),
            "crps_int": float(c1.mean()),
            "crps_delta": _ci(c1 - c0, gid, n_boot),
            "max_abs_dP_ge": dmax,
            "tll_asis": float(np.mean(ll0)),
            "tll_int": float(np.mean(ll1)),
            "tll_delta": float(np.mean(ll1) - np.mean(ll0)),
            "ece_asis": float(np.mean(ece0)),
            "ece_int": float(np.mean(ece1)),
            "ece_delta": float(np.mean(ece1) - np.mean(ece0)),
            "bias": {"point": b.point, "lo": b.lo, "hi": b.hi},
            "cov80_naive_asis": interval_coverage(q[:, 1], q[:, 17], y),
            "cov80_naive_int": interval_coverage(qi[:, 1], qi[:, 17], y),
            "q_int_is_integer": bool((qi == np.round(qi)).all()),
        }
    return out


def load_labels(db_path: str) -> pl.DataFrame:
    con = duckdb.connect(db_path, read_only=True)
    try:
        return con.execute(
            "SELECT game_id, player_id, pts, reb, ast, fg3m FROM player_game_stats "
            "WHERE minutes > 0"
        ).pl()
    finally:
        con.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--season", type=int, default=2023)
    ap.add_argument("--db-path", default="nba.duckdb")
    ap.add_argument("--oof", default=str(OOF_PATH))
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    res = evaluate(pl.read_parquet(a.oof), load_labels(a.db_path), a.season, a.n_boot)
    txt = json.dumps(res, indent=1, sort_keys=True)
    if a.out:
        Path(a.out).write_text(txt)
    print(txt)


if __name__ == "__main__":
    main()
