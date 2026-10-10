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

import datetime as dt
from collections import defaultdict, deque
from dataclasses import dataclass

import duckdb
import numpy as np
import polars as pl

from nba.coldstart.archetypes import (
    ArchetypeAssignmentResult,
    assign_archetypes,
    build_player_archetypes,
    build_player_feature_frame_cached,
    build_player_feature_source,
)
from nba.props.config import (
    DEFAULT_LOOKBACK_GAMES as DEFAULT_LOOKBACK_GAMES,
)
from nba.props.config import (
    OpponentAdjustmentConfig as OpponentAdjustmentConfig,
)

#: Stats this module can produce a defense factor for -- matches
#: ``nba.props.config.TARGET_STATS``.
OPPONENT_STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")


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


# ---------------------------------------------------------------------------
# Archetype-vs-archetype opponent factor (docs/NEXT_OPTIONS.md §3 Option A).
# ---------------------------------------------------------------------------
#
# Mechanistically identical to the team-level factor above
# (``opp_allowed_<stat> / league_avg_<stat>_allowed``), except the "allowed"
# quantity is broken out by the SHOOTING player's archetype cluster
# (:mod:`nba.coldstart.archetypes`, same k as ``docs/RESULTS_2026-10-08.md``
# §2/§3) instead of pooled across every opponent: "Team Y allows 1.08x
# league-average rebounds specifically to big-man-archetype players."
#
# As-of / leakage discipline for the archetype LABEL itself (the one new
# wrinkle vs. the team-level factor, which has no clustering step at all):
#
#   1. A single archetype-clustering model is fit ONCE, via
#      :func:`nba.coldstart.archetypes.build_player_archetypes`, as of the
#      EARLIEST ``game_date`` in the ``games`` table (:func:`_earliest_game_date`).
#      As of that date there are zero strictly-prior games for anyone, so
#      the fit uses only ``players_static`` physical attributes (height,
#      weight, position, age) plus the documented league-wide tendency
#      defaults -- never any game OUTCOME, at any date. This is what makes
#      it safe to reuse the exact same frozen ``scaler``/``kmeans`` to label
#      every later game: the model itself never saw a result, so scoring a
#      later row with it cannot leak that row's own game (or anyone else's)
#      into the label.
#   2. Every row is then LABELED (not re-clustered) via
#      :func:`nba.coldstart.archetypes.assign_archetypes` against a feature
#      vector built strictly as of THAT row's own ``game_date`` (its own
#      prior tendencies only, see ``build_player_feature_frame``) -- this is
#      what keeps the label responsive to role change/aging over a career
#      while the cluster geometry (and therefore label *integers*) stays
#      comparable across the whole history, unlike
#      :func:`nba.coldstart.archetypes.build_archetype_history`'s
#      independent per-snapshot refits.
#   3. The per-(opponent, archetype) rolling "allowed" average is then a
#      pure ``ROWS BETWEEN lookback PRECEDING AND 1 PRECEDING`` window over
#      games strictly before the one being adjusted, identical in spirit to
#      the team-level SQL window above -- see
#      ``tests/props/test_opponent.py``'s
#      ``test_archetype_factor_no_leakage_future_game_does_not_change_factor``.
#
# Perf history (see docs/PERF_ARCHETYPE_2026-10-08.md): step 2 above used to
# call ``build_player_feature_frame`` (which itself re-ran
# ``build_player_shot_rates``/``build_player_reb_ast_rates`` -- each a
# full-history SQL window-function query over the WHOLE DB) once per
# UNIQUE ``game_date`` across the full history. That meant 2 full-history
# re-scans per distinct date instead of per run -- on a synthetic 400-game/
# 80-date DB this alone was ~8s of an ~11s total, and scales linearly with
# the number of distinct dates (hundreds on the real multi-season DB).
# Fixed by building a :class:`~nba.coldstart.archetypes.PlayerFeatureSource`
# ONCE (:func:`build_player_feature_source`, the 2 full-history queries run
# a single time) and reusing it for every date via
# :func:`build_player_feature_frame_cached` (no further DuckDB round-trips)
# -- see :func:`_assign_archetypes_over_dates` below. Numerically identical
# to the old per-date path (same filter/groupby logic, just not re-run from
# a cold SQL query each time); proven by
# ``tests/props/test_opponent.py::test_cached_feature_source_matches_per_date_query``.

#: Stat column names pulled straight from ``player_game_stats`` for the
#: raw per-player-game rows this builder works from.
_RAW_STAT_COLUMNS: tuple[str, ...] = OPPONENT_STATS


def _earliest_game_date(con: duckdb.DuckDBPyConnection) -> str | None:
    row = con.execute("SELECT MIN(game_date) FROM games").fetchone()
    if row is None or row[0] is None:
        return None
    return str(row[0])


def _all_player_game_rows(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """One row per ``player_game_stats`` row across the WHOLE DB (not just
    the eval target): ``game_id, player_id, team_id, game_date`` plus every
    ``_RAW_STAT_COLUMNS`` value (DNP-NULL coalesced to 0, same convention
    as ``nba.props.run._target_frame``)."""
    con.execute(
        """
        SELECT pgs.game_id, pgs.player_id, pgs.team_id, g.game_date,
               COALESCE(pgs.pts, 0) AS pts, COALESCE(pgs.reb, 0) AS reb,
               COALESCE(pgs.ast, 0) AS ast, COALESCE(pgs.fg3m, 0) AS fg3m
        FROM player_game_stats pgs
        JOIN games g USING (game_id)
        """
    )
    columns = [d[0] for d in con.description]
    rows = con.fetchall()
    schema: dict[str, type[pl.DataType]] = {
        "game_id": pl.Utf8,
        "player_id": pl.Int64,
        "team_id": pl.Int64,
        "game_date": pl.Date,
        "pts": pl.Int64,
        "reb": pl.Int64,
        "ast": pl.Int64,
        "fg3m": pl.Int64,
    }
    return pl.DataFrame(rows, schema={c: schema[c] for c in columns}, orient="row")


def _opp_team_map(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """``(game_id, team_id) -> opp_team_id`` for every team-game, via a
    self-join on ``player_game_stats`` (two teams per game)."""
    con.execute(
        """
        SELECT DISTINCT pgs.game_id, pgs.team_id, pgs2.team_id AS opp_team_id
        FROM player_game_stats pgs
        JOIN player_game_stats pgs2
          ON pgs2.game_id = pgs.game_id AND pgs2.team_id <> pgs.team_id
        """
    )
    columns = [d[0] for d in con.description]
    rows = con.fetchall()
    schema = {"game_id": pl.Utf8, "team_id": pl.Int64, "opp_team_id": pl.Int64}
    return pl.DataFrame(rows, schema={c: schema[c] for c in columns}, orient="row")


def _assign_archetypes_over_dates(
    con: duckdb.DuckDBPyConnection, model: ArchetypeAssignmentResult, dates: list[dt.date]
) -> pl.DataFrame:
    """Label every player with a feature frame at each distinct date in
    ``dates`` using the already-fit ``model`` (never refit) -- see module
    docstring point 2. Returns ``(player_id, game_date[Date], archetype)``.

    Builds the date-independent :class:`~nba.coldstart.archetypes.PlayerFeatureSource`
    (the two full-history SQL queries) ONCE, outside the loop over dates --
    see this module's perf-history comment above
    :func:`build_archetype_opponent_features` for why that mattered.
    """
    source = build_player_feature_source(con)
    frames: list[pl.DataFrame] = []
    for d in sorted(set(dates)):
        feature_frame = build_player_feature_frame_cached(source, str(d))
        labels = assign_archetypes(model, feature_frame)
        if labels.height:
            frames.append(labels.with_columns(pl.lit(d).alias("game_date")))
    if not frames:
        return pl.DataFrame(
            {"player_id": [], "game_date": [], "archetype": []},
            schema={"player_id": pl.Int64, "game_date": pl.Date, "archetype": pl.Int64},
        )
    return pl.concat(frames, how="vertical")


def _rolling_archetype_cell_table(
    history_labeled: pl.DataFrame, lookback_games: int
) -> pl.DataFrame:
    """Per-``(opp_team_id, archetype, game_id, game_date)`` as-of rolling
    "allowed to this archetype" average, plus the league-wide pooled
    equivalent (across every opponent) -- the archetype-level analog of
    ``_OPPONENT_FEATURES_SQL``'s ``ROWS BETWEEN {lookback} PRECEDING AND 1
    PRECEDING`` / ``UNBOUNDED PRECEDING AND 1 PRECEDING`` windows. Computed
    in Python (not SQL) because the archetype label itself comes from a
    scikit-learn model, not a SQL-expressible quantity.

    ``history_labeled`` must have one row per player-game with columns
    ``opp_team_id, archetype, game_id, game_date`` plus every
    ``_RAW_STAT_COLUMNS`` value -- already-summed per player, not yet
    aggregated to the cell level (this function does that aggregation).
    """
    cell = (
        history_labeled.group_by(["opp_team_id", "archetype", "game_id", "game_date"])
        .agg([pl.col(s).sum().alias(s) for s in _RAW_STAT_COLUMNS])
        .sort(["opp_team_id", "archetype", "game_date", "game_id"])
    )
    n = cell.height
    if n == 0:
        schema = {
            "opp_team_id": pl.Int64,
            "archetype": pl.Int64,
            "game_id": pl.Utf8,
            "game_date": pl.Date,
            "cell_games_played_prior": pl.Int64,
            **{f"cell_allowed_{s}_prior": pl.Float64 for s in _RAW_STAT_COLUMNS},
            **{f"league_allowed_{s}_prior": pl.Float64 for s in _RAW_STAT_COLUMNS},
        }
        return pl.DataFrame(schema=schema)

    opp_arr = cell.get_column("opp_team_id").to_numpy()
    arch_arr = cell.get_column("archetype").to_numpy()
    stat_arrs = {s: cell.get_column(s).to_numpy().astype(float) for s in _RAW_STAT_COLUMNS}

    # Per-(opp_team_id, archetype) cell: bounded trailing window, current
    # row excluded (same "PRECEDING AND 1 PRECEDING" semantics as the SQL
    # team-level builder) -- a single left-to-right pass per row, O(n)
    # overall, since each row touches its own group's deque exactly twice.
    cell_prior_n = np.zeros(n, dtype=np.int64)
    cell_prior_mean = {s: np.full(n, np.nan) for s in _RAW_STAT_COLUMNS}
    windows: dict[tuple[int, int], dict[str, deque[float]]] = {}
    group_n: dict[tuple[int, int], int] = defaultdict(int)
    for i in range(n):
        key = (int(opp_arr[i]), int(arch_arr[i]))
        dq = windows.setdefault(key, {s: deque(maxlen=lookback_games) for s in _RAW_STAT_COLUMNS})
        prior_n = group_n[key]
        cell_prior_n[i] = prior_n
        if prior_n > 0:
            for s in _RAW_STAT_COLUMNS:
                cell_prior_mean[s][i] = float(np.mean(dq[s]))
        for s in _RAW_STAT_COLUMNS:
            dq[s].append(stat_arrs[s][i])
        group_n[key] += 1

    cell = cell.with_columns(
        pl.Series("cell_games_played_prior", cell_prior_n),
        *[pl.Series(f"cell_allowed_{s}_prior", cell_prior_mean[s]) for s in _RAW_STAT_COLUMNS],
    )

    # League-wide pooled equivalent: same "allowed to this archetype"
    # quantity, pooled across every opponent team, expanding (unbounded)
    # window -- same tie-break convention (game_date, game_id) as the
    # team-level league average, with opp_team_id as the final tie-break
    # for determinism.
    league = cell.sort(["archetype", "game_date", "game_id", "opp_team_id"])
    arch_arr2 = league.get_column("archetype").to_numpy()
    stat_arrs2 = {s: league.get_column(s).to_numpy().astype(float) for s in _RAW_STAT_COLUMNS}
    n2 = league.height
    league_prior_mean = {s: np.full(n2, np.nan) for s in _RAW_STAT_COLUMNS}
    running_sum: dict[int, dict[str, float]] = {}
    running_n: dict[int, int] = defaultdict(int)
    for i in range(n2):
        a = int(arch_arr2[i])
        sums = running_sum.setdefault(a, {s: 0.0 for s in _RAW_STAT_COLUMNS})
        rn = running_n[a]
        if rn > 0:
            for s in _RAW_STAT_COLUMNS:
                league_prior_mean[s][i] = sums[s] / rn
        for s in _RAW_STAT_COLUMNS:
            sums[s] += stat_arrs2[s][i]
        running_n[a] += 1
    league = league.with_columns(
        *[pl.Series(f"league_allowed_{s}_prior", league_prior_mean[s]) for s in _RAW_STAT_COLUMNS]
    )

    league_cols = [
        "opp_team_id",
        "archetype",
        "game_id",
        "game_date",
        *[f"league_allowed_{s}_prior" for s in _RAW_STAT_COLUMNS],
    ]
    return cell.join(
        league.select(league_cols),
        on=["opp_team_id", "archetype", "game_id", "game_date"],
        how="left",
    )


def build_archetype_opponent_features(
    con: duckdb.DuckDBPyConnection,
    target: pl.DataFrame,
    k: int = 6,
    seed: int = 0,
    lookback_games: int = DEFAULT_LOOKBACK_GAMES,
) -> pl.DataFrame:
    """Row-aligned (same order as ``target``) archetype-vs-opponent as-of
    features: ``cell_games_played_prior`` plus
    ``cell_allowed_<stat>_prior`` / ``league_allowed_<stat>_prior`` for
    every stat in :data:`OPPONENT_STATS` -- the archetype-level analog of
    :func:`build_opponent_pace_features`. ``target`` must have
    ``game_id, player_id, team_id, game_date`` columns (same contract as
    every other ``nba.props.run`` builder). Feed the result to
    :func:`compute_archetype_opponent_factors`. See module docstring for
    the as-of/leakage argument and the real-DB cost note.

    Empty/neutral-schema frame (every row present, factor columns all
    null) when there are no games in the DB at all or the archetype model
    degenerates to ``k=0`` (no ``players_static`` rows) -- callers see an
    all-null frame which :func:`compute_archetype_opponent_factors` turns
    into the neutral ``1.0`` factor via its own guard, never a crash.
    """
    stat_cols = [f"cell_allowed_{s}_prior" for s in _RAW_STAT_COLUMNS] + [
        f"league_allowed_{s}_prior" for s in _RAW_STAT_COLUMNS
    ]
    empty_result = target.select(["game_id", "player_id", "team_id"]).with_columns(
        pl.lit(None, dtype=pl.Int64).alias("cell_games_played_prior"),
        *[pl.lit(None, dtype=pl.Float64).alias(c) for c in stat_cols],
    )

    ref_date = _earliest_game_date(con)
    if ref_date is None:
        return empty_result
    model = build_player_archetypes(con, ref_date, k=k, seed=seed)
    if model.k == 0:
        return empty_result

    history_rows = _all_player_game_rows(con)
    opp_map = _opp_team_map(con)
    if history_rows.height == 0 or opp_map.height == 0:
        return empty_result
    history_rows = history_rows.join(opp_map, on=["game_id", "team_id"], how="inner")

    all_dates = (
        history_rows.get_column("game_date").to_list() + target.get_column("game_date").to_list()
    )
    labels_all = _assign_archetypes_over_dates(con, model, all_dates)
    if labels_all.height == 0:
        return empty_result

    history_labeled = history_rows.join(labels_all, on=["player_id", "game_date"], how="inner")
    if history_labeled.height == 0:
        return empty_result
    cell_table = _rolling_archetype_cell_table(history_labeled, lookback_games)

    target_small = (
        target.select(["game_id", "player_id", "team_id", "game_date"])
        .join(opp_map, on=["game_id", "team_id"], how="left")
        .join(labels_all, on=["player_id", "game_date"], how="left")
    )
    aligned = target_small.join(
        cell_table.select(
            [
                "opp_team_id",
                "archetype",
                "game_id",
                "game_date",
                "cell_games_played_prior",
                *stat_cols,
            ]
        ),
        on=["opp_team_id", "archetype", "game_id", "game_date"],
        how="left",
    )
    return aligned.select(
        ["game_id", "player_id", "team_id", "cell_games_played_prior", *stat_cols]
    )


def compute_archetype_opponent_factors(
    features: pl.DataFrame, stat: str, config: OpponentAdjustmentConfig
) -> OpponentFactors:
    """Row-aligned archetype-vs-opponent multiplier for ``stat`` -- the
    archetype-level analog of :func:`compute_opponent_factors`. ``features``
    must be the output of :func:`build_archetype_opponent_features`
    (already aligned 1:1 to the target frame). A row whose
    ``cell_games_played_prior < config.min_games_for_archetype_factor`` (the
    STRICTER per-cell guard -- see ``OpponentAdjustmentConfig`` docstring)
    or with a missing/zero league denominator gets the neutral factor
    ``1.0`` rather than a value fit on a thin per-archetype cell. No pace
    factor here (``pace_factor`` is left at the neutral 1.0; the archetype
    split is specifically about who-gets-scored-on-how-much, not pace) --
    ``combined == def_factor``, so :func:`apply_opponent_adjustment` is
    reused unmodified.
    """
    if stat not in OPPONENT_STATS:
        raise ValueError(
            f"compute_archetype_opponent_factors only supports {OPPONENT_STATS}, got {stat!r}"
        )

    n_cell = features.select("cell_games_played_prior").to_series().fill_null(0).to_numpy()
    enough_history = n_cell >= config.min_games_for_archetype_factor

    cell_allowed = (
        features.select(f"cell_allowed_{stat}_prior")
        .to_series()
        .fill_null(0.0)
        .fill_nan(0.0)
        .to_numpy()
    )
    league_allowed = (
        features.select(f"league_allowed_{stat}_prior")
        .to_series()
        .fill_null(0.0)
        .fill_nan(0.0)
        .to_numpy()
    )
    def_factor = np.ones_like(cell_allowed, dtype=float)
    valid = enough_history & (league_allowed > 1e-9)
    def_factor[valid] = cell_allowed[valid] / league_allowed[valid]
    def_factor = np.clip(def_factor, config.def_factor_clip_lo, config.def_factor_clip_hi)
    def_factor[~valid] = 1.0

    pace_factor = np.ones_like(def_factor)
    return OpponentFactors(def_factor=def_factor, pace_factor=pace_factor, combined=def_factor)


# ---------------------------------------------------------------------------
# Maintainer entry point (real 4-season DB; NOT run by this agent -- see
# CLAUDE.md "no multi-minute jobs" / this module's cost note above).
#
#   from nba.db.connect import connect
#   from nba.props.config import DEFAULT_CONFIG
#   from nba.props.run import run_props_experiment  # whatever the current
#   #   entry point is named in nba/props/run.py at HEAD
#   con = connect("nba.duckdb", read_only=True)
#   cfg_baseline = DEFAULT_CONFIG  # enabled=False, use_archetype_factor=False
#   cfg_team_level = ...  # opponent_adjustment.enabled=True (the already-
#   #   failed version, for the required same-window comparison)
#   cfg_archetype = ...  # opponent_adjustment.use_archetype_factor=True
#   # run all three, paired per-player-game bootstrap CRPS on pts/reb/ast,
#   # sliced by cold-start bucket, with a required separate check that
#   # rebounds CRPS does not regress vs. cfg_baseline (CLAUDE.md's "a model
#   # that wins overall but loses on cold-start games is not done" +
#   # docs/NEXT_OPTIONS.md §3's explicit rebounds-regression gate).
# ---------------------------------------------------------------------------
