"""Naive baselines (CLAUDE.md: "beats naive baselines (season average,
last-10 average) on CRPS and log loss").

Both baselines are strictly as-of: they only average a player's *prior*
games this season / prior up-to-10 games, using the identical window-frame
discipline as every other as-of feature in this project. Each baseline is
given a Normal distribution (mean = the naive average, std = the sample
std of the same trailing window, league-default std when fewer than 2
prior games) purely so it can be scored on CRPS/log loss exactly like the
real model -- without a spread assumption a point-forecast baseline can't
be compared on a proper probabilistic score at all.
"""

from __future__ import annotations

import duckdb
import numpy as np
import polars as pl

from nba.props.distributions import NormalDist

#: Fallback std when a player has 0-1 prior games (can't compute a sample
#: std yet). One default per stat, documented and not tuned.
_DEFAULT_STD: dict[str, float] = {"pts": 7.0, "reb": 2.5, "ast": 2.0, "fg3m": 1.3}

_SEASON_WINDOW = (
    "PARTITION BY pgs.player_id ORDER BY g.game_date, pgs.game_id "
    "ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING"
)
_LAST10_WINDOW = (
    "PARTITION BY pgs.player_id ORDER BY g.game_date, pgs.game_id "
    "ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING"
)

_BASELINE_SQL = f"""
SELECT
    pgs.game_id,
    pgs.player_id,
    g.game_date,
    COUNT(*) OVER ({_SEASON_WINDOW}) AS games_played_prior,
    AVG(pgs.{{stat}}) OVER ({_SEASON_WINDOW}) AS season_avg_prior,
    STDDEV_SAMP(pgs.{{stat}}) OVER ({_SEASON_WINDOW}) AS season_std_prior,
    AVG(pgs.{{stat}}) OVER ({_LAST10_WINDOW}) AS last10_avg_prior,
    STDDEV_SAMP(pgs.{{stat}}) OVER ({_LAST10_WINDOW}) AS last10_std_prior
FROM player_game_stats pgs
JOIN games g USING (game_id)
ORDER BY g.game_date, pgs.game_id, pgs.player_id
"""


def build_baseline_features(con: duckdb.DuckDBPyConnection, stat: str) -> pl.DataFrame:
    if stat not in {"pts", "reb", "ast", "fg3m"}:
        raise ValueError(f"unsupported stat {stat!r}")
    con.execute(_BASELINE_SQL.format(stat=stat))
    columns = [d[0] for d in con.description]
    rows = con.fetchall()
    schema = {
        "game_id": pl.Utf8,
        "player_id": pl.Int64,
        "game_date": pl.Date,
        "games_played_prior": pl.Int64,
        "season_avg_prior": pl.Float64,
        "season_std_prior": pl.Float64,
        "last10_avg_prior": pl.Float64,
        "last10_std_prior": pl.Float64,
    }
    return pl.DataFrame(rows, schema={c: schema[c] for c in columns}, orient="row")


def _normal_dists(means: np.ndarray, stds: np.ndarray, default_std: float) -> list[NormalDist]:
    return [
        NormalDist(mean_=float(m), std=float(s) if s > 0 else default_std)
        for m, s in zip(means, stds, strict=True)
    ]


def season_average_baseline(features: pl.DataFrame, stat: str) -> list[NormalDist]:
    default_std = _DEFAULT_STD[stat]
    means = features.select("season_avg_prior").to_series().fill_null(0.0).to_numpy()
    stds = features.select("season_std_prior").to_series().fill_null(default_std).to_numpy()
    return _normal_dists(means, stds, default_std)


def last10_average_baseline(features: pl.DataFrame, stat: str) -> list[NormalDist]:
    default_std = _DEFAULT_STD[stat]
    means = features.select("last10_avg_prior").to_series().fill_null(0.0).to_numpy()
    stds = features.select("last10_std_prior").to_series().fill_null(default_std).to_numpy()
    return _normal_dists(means, stds, default_std)
