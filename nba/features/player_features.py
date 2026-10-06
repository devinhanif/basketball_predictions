"""Player-level, strictly as-of feature builders.

Two things live here:

1. A **career-possession proxy** (:func:`build_player_career_poss_proxy`),
   used to replace the games-played placeholder cold-start bucket in
   ``nba/eval/slices.py`` with CLAUDE.md's real definition: "a game is in
   the cold-start bucket if >=1 projected starter has <500 career
   possessions." The possessions table is empty (the play-by-play parser
   is a separate, not-yet-built milestone owned by the data engineer), so
   the box-score possession formula in CLAUDE.md (``FGA + 0.44*FTA + TOV -
   OREB``) can't be computed either -- ``player_game_stats`` in this schema
   has no ``fga``/``fta``/``oreb`` columns. **PROXY, clearly flagged**: we
   use a minutes-based estimate instead (CLAUDE.md explicitly allows this:
   "or a minutes-based proxy"): a player is credited with
   ``minutes * POSSESSIONS_PER_MINUTE`` team possessions for every game
   they played in, which approximates "possessions the player was on the
   court for" using a league-average pace constant. Replace with a true
   possession count once the PBP parser lands and ``possessions`` is
   populated.

2. ``log1p_n_poss`` (:func:`log1p_n_poss`), the trust-in-rate feature
   CLAUDE.md requires every model to receive: "Models receive
   ``log(1+n_poss)`` as a feature so they can learn how much to trust a
   rate."

No-leakage: both builders use the same strictly-prior window-frame
discipline as ``nba.features.team_features`` (``ROWS BETWEEN UNBOUNDED
PRECEDING AND 1 PRECEDING``), partitioned by player instead of team.
"""

from __future__ import annotations

import duckdb
import numpy as np
import polars as pl

from nba.coldstart.config import CAREER_POSS_COLD_START_THRESHOLD

#: League-average team possessions per minute of game clock (roughly 100
#: possessions per team per 48 minutes). A player on the court the whole
#: game is credited with this many team possessions for that game. This is
#: an explicit approximation pending the play-by-play parser -- see module
#: docstring.
POSSESSIONS_PER_MINUTE = 100.0 / 48.0

__all__ = [
    "CAREER_POSS_COLD_START_THRESHOLD",
    "POSSESSIONS_PER_MINUTE",
    "build_game_cold_start_flags",
    "build_player_career_poss_proxy",
    "log1p_n_poss",
]

_PLAYER_CAREER_POSS_SQL = f"""
SELECT
    pgs.game_id,
    pgs.player_id,
    pgs.team_id,
    pgs.starter,
    g.game_date AS as_of,
    g.game_date,
    pgs.minutes,
    -- strictly-prior: this game's own minutes never contribute to its own
    -- career_poss_proxy_prior (same discipline as team_features.py).
    COALESCE(
        SUM(pgs.minutes * {POSSESSIONS_PER_MINUTE}) OVER (
            PARTITION BY pgs.player_id ORDER BY g.game_date, pgs.game_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ),
        0.0
    ) AS career_poss_proxy_prior
FROM player_game_stats pgs
JOIN games g USING (game_id)
ORDER BY g.game_date, pgs.game_id, pgs.player_id
"""


def log1p_n_poss(n_poss: np.ndarray | float) -> np.ndarray:
    """``log(1 + n_poss)``, the trust-in-rate feature required by CLAUDE.md."""
    n = np.asarray(n_poss, dtype=float)
    return np.asarray(np.log1p(np.clip(n, 0.0, None)), dtype=float)


def build_player_career_poss_proxy(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """One row per (game, player): as-of career-possession proxy.

    ``career_poss_proxy_prior`` only sums games with strictly earlier
    ``(game_date, game_id)`` ordering for that player -- a brand-new player
    (or one appearing for the first time in this dataset) gets 0, which is
    the correct "no observed history yet" value, not a leak.
    """
    con.execute(_PLAYER_CAREER_POSS_SQL)
    columns = [d[0] for d in con.description]
    rows = con.fetchall()
    schema = {
        "game_id": pl.Utf8,
        "player_id": pl.Int64,
        "team_id": pl.Int64,
        "starter": pl.Boolean,
        "as_of": pl.Date,
        "game_date": pl.Date,
        "minutes": pl.Float64,
        "career_poss_proxy_prior": pl.Float64,
    }
    return pl.DataFrame(rows, schema={c: schema[c] for c in columns}, orient="row")


def build_game_cold_start_flags(
    con: duckdb.DuckDBPyConnection,
    threshold: float = CAREER_POSS_COLD_START_THRESHOLD,
) -> pl.DataFrame:
    """One row per ``game_id``: ``any_starter_cold_start`` per CLAUDE.md.

    True iff at least one player flagged ``starter=True`` in that game (on
    either team) has ``career_poss_proxy_prior < threshold``. This uses the
    box score's own ``starter`` flag purely to define a backtest *reporting*
    bucket after the fact -- it is never fed to a model as a prediction-time
    feature (CLAUDE.md risk #3: never use actual box-score lineups at
    prediction time), only used retrospectively to slice walk-forward
    metrics by cold-start-ness of the game that already happened.
    """
    per_player = build_player_career_poss_proxy(con)
    starters = per_player.filter(pl.col("starter"))
    if starters.height == 0:
        return pl.DataFrame(schema={"game_id": pl.Utf8, "any_starter_cold_start": pl.Boolean})
    flagged = starters.group_by("game_id").agg(
        (pl.col("career_poss_proxy_prior") < threshold).any().alias("any_starter_cold_start")
    )
    return flagged.sort("game_id")
