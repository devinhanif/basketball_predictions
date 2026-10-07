"""Opponent defense + pace adjustment for player stat projections.

Motivation (CLAUDE.md Phase 2 "beats naive baselines" ask): a raw
season-average ignores *who the player is about to play*. A player facing
a fast, weak-defense team should project higher (more possessions, easier
buckets) than the same player vs. a slow, elite-defense team -- mechanistic
signal a season-average baseline structurally cannot use. This module adds
that signal as a pure, as-of multiplier on top of the existing
frequency-severity projections in ``nba.props.stat_models``.

As-of definitions (``games`` + ``player_game_stats`` only -- no new data,
same proxy-data constraint as ``nba.features.team_features``):

    team_totals(game, team)      = SUM(pts|reb|ast|fg3m) from player_game_stats
    opp_allowed_<stat>(team)     = rolling as-of avg of team_totals.<stat> for
                                    the OPPONENT in every one of this team's
                                    prior games -- i.e. how much of <stat> this
                                    team concedes on defense.
    league_avg_<stat>_allowed    = rolling as-of avg of the same quantity,
                                    pooled across the whole league (every
                                    team-game row strictly before this one in
                                    (game_date, game_id, team_id) order -- same
                                    tie-break convention as
                                    ``nba.features.team_features``).
    opp_def_factor_<stat>        = opp_allowed_<stat>(opponent) / league_avg_<stat>_allowed
                                    -- >1 means the opponent allows more than
                                    league-average (weak defense, projection
                                    scales UP); <1 means a strong defense
                                    (scales DOWN).
    pace_proxy(team)             = rolling as-of avg of (team_pts + opp_pts)
                                    across a team's games -- same box-score
                                    pace proxy as
                                    ``nba.features.team_features.pace_proxy_prior``.
    pace_factor                  = pace_proxy(opponent) / league_avg_pace_proxy
                                    -- a fast opponent (high combined scoring
                                    history) scales every projection UP a
                                    little (more possessions to go around),
                                    independent of the stat-specific defense
                                    factor above.

Both factors are a pure function of the OPPONENT's own as-of history (never
the target game's own result, never the player's own team's offense) --
strictly-prior rolling windows, same discipline as every other as-of
builder in this package. See
``tests/props/test_opponent.py::test_no_leakage_future_game_does_not_change_factor``
for the planted-future-game proof.

Applied as ``adjusted_mean = raw_mean * clip(opp_def_factor_stat * pace_factor)``
and ``adjusted_var = raw_var * clip(...)**2`` (scaling variance by the
factor squared keeps the coefficient of variation -- the native dispersion
shape already fit by ``nba.props.stat_models`` -- unchanged; see
:func:`apply_opponent_adjustment`). Behind
``PropsConfig.opponent_adjustment.enabled`` (CLAUDE.md flag ask); when a
row has too little opponent history (``min_games_for_factor``) the factor
is forced to the neutral ``1.0`` rather than fit on noise, same
honesty-guard pattern as ``nba.props.dispersion``/``nba.props.conformal``.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import polars as pl

#: Stats this module can produce a defense factor for -- matches
#: ``nba.props.config.TARGET_STATS``.
OPPONENT_STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")

#: Default trailing-games window, mirrors every other as-of builder in this
#: package (``DEFAULT_LOOKBACK_GAMES`` in ``nba.props.minutes`` /
#: ``nba.props.stat_models``).
DEFAULT_LOOKBACK_GAMES = 150

_OPPONENT_FEATURES_SQL = """
WITH team_totals AS (
    SELECT game_id, team_id,
           SUM(pts) AS pts, SUM(reb) AS reb, SUM(ast) AS ast, SUM(fg3m) AS fg3m
    FROM player_game_stats
    GROUP BY game_id, team_id
),
team_games AS (
    SELECT
        g.game_id, g.game_date,
        tt.team_id, tt2.team_id AS opp_team_id,
        tt.pts AS team_pts, tt.reb AS team_reb, tt.ast AS team_ast, tt.fg3m AS team_fg3m,
        tt2.pts AS opp_pts, tt2.reb AS opp_reb, tt2.ast AS opp_ast, tt2.fg3m AS opp_fg3m
    FROM games g
    JOIN team_totals tt ON tt.game_id = g.game_id
    JOIN team_totals tt2 ON tt2.game_id = g.game_id AND tt2.team_id <> tt.team_id
),
rolled AS (
    SELECT
        game_id, game_date, team_id, opp_team_id,
        COUNT(*) OVER (
            PARTITION BY team_id ORDER BY game_date, game_id
            ROWS BETWEEN {lookback} PRECEDING AND 1 PRECEDING
        ) AS team_games_played_prior,
        AVG(opp_pts) OVER (
            PARTITION BY team_id ORDER BY game_date, game_id
            ROWS BETWEEN {lookback} PRECEDING AND 1 PRECEDING
        ) AS allowed_pts_prior,
        AVG(opp_reb) OVER (
            PARTITION BY team_id ORDER BY game_date, game_id
            ROWS BETWEEN {lookback} PRECEDING AND 1 PRECEDING
        ) AS allowed_reb_prior,
        AVG(opp_ast) OVER (
            PARTITION BY team_id ORDER BY game_date, game_id
            ROWS BETWEEN {lookback} PRECEDING AND 1 PRECEDING
        ) AS allowed_ast_prior,
        AVG(opp_fg3m) OVER (
            PARTITION BY team_id ORDER BY game_date, game_id
            ROWS BETWEEN {lookback} PRECEDING AND 1 PRECEDING
        ) AS allowed_fg3m_prior,
        AVG(team_pts + opp_pts) OVER (
            PARTITION BY team_id ORDER BY game_date, game_id
            ROWS BETWEEN {lookback} PRECEDING AND 1 PRECEDING
        ) AS pace_proxy_prior,
        AVG(opp_pts) OVER (
            ORDER BY game_date, game_id, team_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS league_allowed_pts_prior,
        AVG(opp_reb) OVER (
            ORDER BY game_date, game_id, team_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS league_allowed_reb_prior,
        AVG(opp_ast) OVER (
            ORDER BY game_date, game_id, team_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS league_allowed_ast_prior,
        AVG(opp_fg3m) OVER (
            ORDER BY game_date, game_id, team_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS league_allowed_fg3m_prior,
        AVG(team_pts + opp_pts) OVER (
            ORDER BY game_date, game_id, team_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS league_pace_proxy_prior
    FROM team_games
)
SELECT
    r1.game_id,
    r1.game_date,
    r1.team_id,
    r1.opp_team_id,
    r2.team_games_played_prior AS opp_games_played_prior,
    r2.allowed_pts_prior AS opp_allowed_pts_prior,
    r2.allowed_reb_prior AS opp_allowed_reb_prior,
    r2.allowed_ast_prior AS opp_allowed_ast_prior,
    r2.allowed_fg3m_prior AS opp_allowed_fg3m_prior,
    r2.pace_proxy_prior AS opp_pace_proxy_prior,
    r1.league_allowed_pts_prior,
    r1.league_allowed_reb_prior,
    r1.league_allowed_ast_prior,
    r1.league_allowed_fg3m_prior,
    r1.league_pace_proxy_prior
FROM rolled r1
JOIN rolled r2 ON r2.game_id = r1.game_id AND r2.team_id = r1.opp_team_id
ORDER BY r1.game_date, r1.game_id, r1.team_id
"""


def build_opponent_pace_features(
    con: duckdb.DuckDBPyConnection, lookback_games: int = DEFAULT_LOOKBACK_GAMES
) -> pl.DataFrame:
    """One row per (game, team): the as-of opponent-defense + pace columns.

    ``team_id`` is the player's own team for that game; every
    ``opp_*_prior``/``league_*_prior`` column is derived strictly from rows
    with an earlier ``(game_date, game_id)`` than the current one -- see
    module docstring. Callers join this onto a player-level frame by
    ``(game_id, team_id)`` (the player's team), matching
    ``nba.props.minutes``'s existing ``(game_id, team_id)`` join convention
    for game-context features.
    """
    sql = _OPPONENT_FEATURES_SQL.format(lookback=int(lookback_games))
    con.execute(sql)
    columns = [d[0] for d in con.description]
    rows = con.fetchall()
    schema: dict[str, type[pl.DataType]] = {
        "game_id": pl.Utf8,
        "game_date": pl.Date,
        "team_id": pl.Int64,
        "opp_team_id": pl.Int64,
        "opp_games_played_prior": pl.Int64,
        "opp_allowed_pts_prior": pl.Float64,
        "opp_allowed_reb_prior": pl.Float64,
        "opp_allowed_ast_prior": pl.Float64,
        "opp_allowed_fg3m_prior": pl.Float64,
        "opp_pace_proxy_prior": pl.Float64,
        "league_allowed_pts_prior": pl.Float64,
        "league_allowed_reb_prior": pl.Float64,
        "league_allowed_ast_prior": pl.Float64,
        "league_allowed_fg3m_prior": pl.Float64,
        "league_pace_proxy_prior": pl.Float64,
    }
    return pl.DataFrame(rows, schema={c: schema[c] for c in columns}, orient="row")


@dataclass
class OpponentAdjustmentConfig:
    """``use_opponent_adjustment`` flag + tuning knobs (CLAUDE.md task ask).

    Defaulted OFF: the real 4-season A/B showed opponent adjustment slightly
    WORSENS CRPS for every stat (pts 3.670->3.676, reb 1.539->1.541, ast
    1.297->1.300, fg3m 0.743->0.745) and does not help beat the season-avg
    baseline -- the box-score-derived defense/pace factors add noise without
    improving central tendency. Kept available (flag + plumbing) for a future
    version using real defensive ratings. Set ``enabled=True`` to exercise it.
    Nothing here is tuned on any backtest.
    """

    enabled: bool = False
    lookback_games: int = DEFAULT_LOOKBACK_GAMES
    #: Below this many strictly-prior games for the OPPONENT, the defense
    #: and pace factors are forced to the neutral 1.0 (not enough history
    #: to trust the ratio) -- same honesty-guard pattern as
    #: ``ZeroInflationConfig.min_games`` / ``DispersionConfig.min_cal_n``.
    min_games_for_factor: int = 5
    #: Conservative symmetric-ish clip on the opponent-defense factor alone
    #: (applied before combining with pace) -- guards against a small
    #: sample's noisy allowed-stat average producing an extreme ratio.
    def_factor_clip_lo: float = 0.80
    def_factor_clip_hi: float = 1.25
    #: Same clip, for the pace factor alone.
    pace_factor_clip_lo: float = 0.85
    pace_factor_clip_hi: float = 1.15


@dataclass
class OpponentFactors:
    """Per-row combined multiplier, plus the raw components for diagnostics
    / testing (``combined = clip(def_factor) * clip(pace_factor)``)."""

    def_factor: np.ndarray
    pace_factor: np.ndarray
    combined: np.ndarray


_LEAGUE_COL = {
    "pts": "league_allowed_pts_prior",
    "reb": "league_allowed_reb_prior",
    "ast": "league_allowed_ast_prior",
    "fg3m": "league_allowed_fg3m_prior",
}
_OPP_COL = {
    "pts": "opp_allowed_pts_prior",
    "reb": "opp_allowed_reb_prior",
    "ast": "opp_allowed_ast_prior",
    "fg3m": "opp_allowed_fg3m_prior",
}


def compute_opponent_factors(
    features: pl.DataFrame, stat: str, config: OpponentAdjustmentConfig
) -> OpponentFactors:
    """Row-aligned opponent-defense x pace multiplier for ``stat``.

    ``features`` must already be aligned 1:1 (same row order) to the
    player-level target frame -- see ``nba.props.run._align`` for the join
    helper every other as-of feature builder in this package uses the same
    way. A row with insufficient opponent history (``opp_games_played_prior
    < config.min_games_for_factor``) or a missing/zero league denominator
    gets the neutral factor ``1.0`` rather than a value fit on noise.
    """
    if stat not in OPPONENT_STATS:
        raise ValueError(f"compute_opponent_factors only supports {OPPONENT_STATS}, got {stat!r}")

    n_opp = features.select("opp_games_played_prior").to_series().fill_null(0).to_numpy()
    enough_history = n_opp >= config.min_games_for_factor

    opp_allowed = features.select(_OPP_COL[stat]).to_series().fill_null(0.0).to_numpy()
    league_allowed = features.select(_LEAGUE_COL[stat]).to_series().fill_null(0.0).to_numpy()
    def_factor = np.ones_like(opp_allowed, dtype=float)
    valid_def = enough_history & (league_allowed > 1e-9)
    def_factor[valid_def] = opp_allowed[valid_def] / league_allowed[valid_def]
    def_factor = np.clip(def_factor, config.def_factor_clip_lo, config.def_factor_clip_hi)
    def_factor[~valid_def] = 1.0

    opp_pace = features.select("opp_pace_proxy_prior").to_series().fill_null(0.0).to_numpy()
    league_pace = features.select("league_pace_proxy_prior").to_series().fill_null(0.0).to_numpy()
    pace_factor = np.ones_like(opp_pace, dtype=float)
    valid_pace = enough_history & (league_pace > 1e-9)
    pace_factor[valid_pace] = opp_pace[valid_pace] / league_pace[valid_pace]
    pace_factor = np.clip(pace_factor, config.pace_factor_clip_lo, config.pace_factor_clip_hi)
    pace_factor[~valid_pace] = 1.0

    combined = def_factor * pace_factor
    return OpponentFactors(def_factor=def_factor, pace_factor=pace_factor, combined=combined)


def apply_opponent_adjustment(
    mean: np.ndarray, var: np.ndarray, factors: OpponentFactors
) -> tuple[np.ndarray, np.ndarray]:
    """Scale ``mean`` by ``factors.combined`` and ``var`` by its square.

    Scaling variance by the factor squared holds the coefficient of
    variation fixed -- i.e. this only moves WHERE the distribution is
    centered, not its relative (already-fit) shape/dispersion, consistent
    with CLAUDE.md's "Keep variance/dispersion handling consistent" ask.
    Applied *before* the native-dispersion calibration / conformal /
    coherence steps in ``nba.props.run`` so every downstream step sees an
    already opponent-adjusted moment pair, same as every other
    ``k_multiplier``-style adjustment in this package.
    """
    adj_mean = mean * factors.combined
    adj_var = var * (factors.combined**2)
    return adj_mean, adj_var
