"""Played-only, strictly as-of season-average baselines.

DNP rows in ``player_game_stats`` carry ``minutes`` NULL and all-zero stats
(~18.6% of rows, docs/DNP_AUDIT_2026-10-08.md). The historical backtest
baseline (``nba.props.baselines``) averages those zeros in, which makes it a
different estimand (unconditional on playing) from a sim whose minutes are
conditional on playing. These builders use only played games
(``minutes > 0``) for numerators AND denominators, so P(play) is handled once
(by the minutes model), mirroring the forward path
(``nba.props.forward.recency_weighted_dist``).

As-of discipline: a target row's baseline uses only played games strictly
before it in ``(game_date, game_id)`` order -- the same
``1 PRECEDING`` window frame as every other as-of feature; the target game's
own stat never contributes (see tests/features/test_played_baseline.py).
"""

from __future__ import annotations

from bisect import bisect_left

import duckdb
import numpy as np
import polars as pl

from nba.props.distributions import NormalDist
from nba.props.forward import recency_weighted_dist

#: Half-life in played games for the recency-weighted fair baseline; must
#: equal the value forward.py uses for its season-average (checked in tests).
FORWARD_RECENCY_HALFLIFE_GAMES = 10.0

_PLAYED = "pgs.minutes IS NOT NULL AND pgs.minutes > 0"
_WINDOW = (
    "PARTITION BY pgs.player_id ORDER BY g.game_date, pgs.game_id "
    "ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING"
)


def build_played_baseline_features(con: duckdb.DuckDBPyConnection, stat: str) -> pl.DataFrame:
    """Same columns as ``nba.props.baselines.build_baseline_features`` for the
    season-average family (``games_played_prior``, ``season_avg_prior``,
    ``season_std_prior``), computed over PLAYED prior games only. One row per
    ``player_game_stats`` row (DNP target rows included)."""
    if stat not in {"pts", "reb", "ast", "fg3m"}:
        raise ValueError(f"unsupported stat {stat!r}")
    val = f"CASE WHEN {_PLAYED} THEN pgs.{stat} END"
    con.execute(
        f"""
        SELECT pgs.game_id, pgs.player_id, g.game_date,
               COUNT({val}) OVER ({_WINDOW}) AS games_played_prior,
               AVG({val}) OVER ({_WINDOW}) AS season_avg_prior,
               STDDEV_SAMP({val}) OVER ({_WINDOW}) AS season_std_prior
        FROM player_game_stats pgs JOIN games g USING (game_id)
        ORDER BY g.game_date, pgs.game_id, pgs.player_id
        """
    )
    schema = {
        "game_id": pl.Utf8,
        "player_id": pl.Int64,
        "game_date": pl.Date,
        "games_played_prior": pl.Int64,
        "season_avg_prior": pl.Float64,
        "season_std_prior": pl.Float64,
    }
    cols = [d[0] for d in con.description]
    return pl.DataFrame(con.fetchall(), schema={c: schema[c] for c in cols}, orient="row")


def recency_played_baseline(
    con: duckdb.DuckDBPyConnection,
    stat: str,
    keys: pl.DataFrame,
    halflife_games: float = FORWARD_RECENCY_HALFLIFE_GAMES,
) -> list[NormalDist]:
    """Fair season-average per ``keys`` row (``game_id``, ``player_id``):
    forward.py's exponentially recency-weighted Normal over the player's
    PLAYED games strictly before the target game (career-to-date, like the
    forward path), via the very same ``recency_weighted_dist``."""
    if stat not in {"pts", "reb", "ast", "fg3m"}:
        raise ValueError(f"unsupported stat {stat!r}")
    rows = con.execute(
        f"""
        SELECT pgs.player_id, g.game_date, pgs.game_id, pgs.{stat}
        FROM player_game_stats pgs JOIN games g USING (game_id)
        WHERE pgs.minutes IS NOT NULL AND pgs.minutes > 0 AND pgs.{stat} IS NOT NULL
        ORDER BY pgs.player_id, g.game_date, pgs.game_id
        """
    ).fetchall()
    order: dict[int, list[tuple[object, str]]] = {}
    vals: dict[int, list[float]] = {}
    for pid, d, gid, v in rows:
        order.setdefault(int(pid), []).append((d, str(gid)))
        vals.setdefault(int(pid), []).append(float(v))
    dates = {
        str(r[0]): r[1] for r in con.execute("SELECT game_id, game_date FROM games").fetchall()
    }
    out: list[NormalDist] = []
    for gid, pid in zip(keys["game_id"].to_list(), keys["player_id"].to_list(), strict=True):
        key = (dates[str(gid)], str(gid))
        o = order.get(int(pid), [])
        k = bisect_left(o, key)  # played games strictly before the target
        series = np.asarray(vals.get(int(pid), [])[:k], dtype=float)
        out.append(recency_weighted_dist(series, stat, halflife_games))
    return out
