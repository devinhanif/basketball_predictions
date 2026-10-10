"""Role-change detection via CUSUM (CLAUDE.md "borrowed from manufacturing QC":
"Bayesian online changepoint detection or CUSUM on usage/minutes to flag
trades, injury returns, and starter changes. On a flagged change, cold-start
logic temporarily raises prior weight.")

Method: a standard two-sided CUSUM on each player's minutes series,
standardized by that player's own mean/std (minutes scale varies hugely by
role, so a fixed absolute threshold would flag stars and bench players at
different effective sensitivities). ``build_role_change_features`` wraps
this in the project's as-of discipline: for the row being predicted, CUSUM
only ever sees that player's *strictly prior* games (the row's own game, and
anything after it, is invisible), so this is leakage-safe by construction
like every other feature in ``nba/props/``.

The output ``k_multiplier`` is designed to be multiplied directly into a
stat's base shrinkage pseudo-count ``k`` before calling
``nba.features.shrinkage.shrink_rate`` -- raising ``k`` pulls the posterior
rate harder toward the prior, which is exactly "temporarily raise prior
weight" from CLAUDE.md. The boost decays linearly back to 1.0 over
``decay_games`` games after the flagged change, modeling the idea that a
role change's uncertainty shrinks as fresh observations accumulate.

Performance note: the as-of wrapper below re-runs CUSUM on every prefix of
every player's game log (``O(n^2)`` per player). That is fine at this
project's current data scale (single-season backtests, hundreds of games
per player) and keeps the no-leakage argument trivial to audit; an
incremental/online CUSUM would be a straightforward optimization if a much
larger multi-season run makes this a bottleneck.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import polars as pl


@dataclass
class RoleChangeConfig:
    """CUSUM + cold-start-boost tuning (CLAUDE.md "Role change detection")."""

    #: Cumulative-deviation flag threshold, in units of the series' own std.
    threshold: float = 4.0
    #: Per-step slack (in units of std) subtracted before accumulating --
    #: the standard CUSUM "allowed deviation before it counts" parameter.
    drift: float = 0.5
    #: Multiplier applied to a stat's base shrinkage pseudo-count ``k``
    #: immediately after a flagged change (decays to 1.0 over decay_games).
    raise_factor: float = 3.0
    #: Games over which the raised weight decays back to 1x.
    decay_games: float = 8.0
    #: Minimum prior games needed before CUSUM is even attempted (avoids
    #: flagging "changes" from a 1-2 game sample with no real variance).
    min_games: int = 4


def cusum_detect(x: np.ndarray, threshold: float = 4.0, drift: float = 0.5) -> list[int]:
    """Two-sided CUSUM changepoint flags on a 1-D series.

    Standardizes by the series' own mean/std, accumulates signed deviations
    (minus ``drift`` slack) in both directions, and flags index ``i`` the
    moment either accumulator exceeds ``threshold`` -- resetting both to 0
    afterward so a second, later shift is still detectable. Degenerates to
    "no flags" for series too short or with ~zero variance (a flat series
    must never falsely flag).
    """
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 2:
        return []
    std = float(np.std(x))
    if std < 1e-9:
        return []
    mean = float(np.mean(x))
    z = (x - mean) / std
    s_hi, s_lo = 0.0, 0.0
    flags: list[int] = []
    for i in range(n):
        s_hi = max(0.0, s_hi + z[i] - drift)
        s_lo = min(0.0, s_lo + z[i] + drift)
        if s_hi > threshold or -s_lo > threshold:
            flags.append(i)
            s_hi, s_lo = 0.0, 0.0
    return flags


def k_multiplier(games_since_change: float | None, config: RoleChangeConfig | None = None) -> float:
    """Cold-start pseudo-count boost: 1.0 with no flagged change, decaying
    linearly from ``1 + raise_factor`` back to 1.0 over ``decay_games``."""
    cfg = config or RoleChangeConfig()
    if games_since_change is None or games_since_change != games_since_change:  # None or NaN
        return 1.0
    games = max(float(games_since_change), 0.0)
    decay = max(cfg.decay_games, 1e-6)
    boost = max(0.0, 1.0 - games / decay)
    return float(1.0 + cfg.raise_factor * boost)


_ROLE_CHANGE_SQL = """
SELECT pgs.game_id, pgs.player_id, g.game_date, COALESCE(pgs.minutes, 0.0) AS minutes
FROM player_game_stats pgs
JOIN games g USING (game_id)
ORDER BY pgs.player_id, g.game_date, pgs.game_id
"""
# COALESCE: DNP rows store NULL minutes (see nba/props/run.py::_target_frame
# docstring). A NaN in `minutes` would propagate through np.std/np.mean in
# `cusum_detect` and silently zero out CUSUM's ability to detect *any*
# later changepoint for that player (comparisons against NaN are always
# False) -- 0.0 is also the correct semantic value (DNP = 0 minutes
# played), consistent with every other query in this package.


def build_role_change_features(
    con: duckdb.DuckDBPyConnection, config: RoleChangeConfig | None = None
) -> pl.DataFrame:
    """One row per (game, player): as-of role-change flag + cold-start boost.

    ``games_since_change`` is NULL until the first changepoint is flagged in
    a player's prior-games-only window; ``k_multiplier`` is always a finite
    number (1.0 when nothing is flagged).
    """
    cfg = config or RoleChangeConfig()
    con.execute(_ROLE_CHANGE_SQL)
    cols = [d[0] for d in con.description]
    rows = con.fetchall()
    schema = {
        "game_id": pl.Utf8,
        "player_id": pl.Int64,
        "game_date": pl.Date,
        "minutes": pl.Float64,
    }
    df = pl.DataFrame(rows, schema={c: schema[c] for c in cols}, orient="row")

    game_ids: list[str] = []
    player_ids: list[int] = []
    game_dates: list[object] = []
    games_since: list[float | None] = []
    k_mults: list[float] = []

    for (player_id,), group in df.group_by(["player_id"], maintain_order=True):
        minutes = group.select("minutes").to_series().to_numpy()
        gids = group.select("game_id").to_series().to_list()
        dates = group.select("game_date").to_series().to_list()
        n = len(minutes)
        for i in range(n):
            game_ids.append(gids[i])
            player_ids.append(player_id)
            game_dates.append(dates[i])
            prior = minutes[:i]
            if len(prior) < cfg.min_games:
                games_since.append(None)
                k_mults.append(1.0)
                continue
            flags = cusum_detect(prior, threshold=cfg.threshold, drift=cfg.drift)
            if flags:
                since = float((i - 1) - flags[-1])
                games_since.append(since)
                k_mults.append(k_multiplier(since, cfg))
            else:
                games_since.append(None)
                k_mults.append(1.0)

    out = pl.DataFrame(
        {
            "game_id": game_ids,
            "player_id": player_ids,
            "game_date": game_dates,
            "games_since_change": games_since,
            "k_multiplier": k_mults,
        },
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "game_date": pl.Date,
            "games_since_change": pl.Float64,
            "k_multiplier": pl.Float64,
        },
    )
    return out.sort(["game_date", "game_id", "player_id"])
