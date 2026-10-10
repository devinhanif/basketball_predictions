"""As-of, possession-level training frame for the rung-4 step heads.

CLAUDE.md architecture ladder, rung 4: "PyTorch multi-head net ... feeding
the sim heads". The sim step heads this module's output trains are per
CLAUDE.md's rung-3 description of the engine: "one logistic/GBM head per
step (duration, outcome, shooter, zone, make, rebound, FT)". Per
``NEXT_SESSION.md`` item 4, the *shooter* head (which of the 5 on-court
players) is explicitly deferred -- it needs the DeepSets lineup encoder,
which a prior experiment found carried negligible signal (``lineup-mix,
R^2 ~= 0.016``) and is not on this task's critical path. This module
therefore supplies features/labels for the remaining five steps: outcome,
zone, make, rebound, duration.

No-leakage discipline (mirrors ``research.features.player_possession_features``
and ``nba.features.possession_features``):

- The two as-of team-rating blocks (``off_*_prior``/``def_*_prior``) come
  straight from ``nba.features.possession_features.build_team_possession_rates``,
  which is itself a strictly-prior cumulative window (``ROWS BETWEEN
  UNBOUNDED PRECEDING AND 1 PRECEDING``) over that team's own earlier
  games -- the current game's own possessions never contribute to its own
  row's rating.
- ``period`` / ``clock_start`` / ``score_diff`` are intra-game state that
  is fully known *before* this possession's own outcome happens (the
  parser's ``_Trip`` captures ``score_diff`` from the score entering the
  trip, before any of this trip's own points are added -- see
  ``nba/parse/possessions.py::start_trip``), so they carry no leakage of
  this possession's own outcome into its own features.
- Every label column (``outcome``, ``shot_zone``, ``made_shot``, ``oreb``,
  ``duration_s``) is this possession's own realized outcome -- a label,
  never a feature.

Player/lineup inputs (``off_pa_*`` / ``def_pa_*``): for the 5 offensive and
5 defensive players on the floor (``possessions.off_players`` /
``def_players``) each player carries a strictly-prior, shrunk as-of stat
vector (:data:`PLAYER_ASOF_STATS`), pooled across the five with
permutation-invariant mean/max/min (a "DeepSets-lite": fixed pooling, no
learned encoder). No-leakage: a player's vector for game ``g`` is a
cumulative window over that player's own earlier ``(game_date, game_id)``
rows only (``ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING``) -- the
same discipline as ``research.features.player_possession_features`` -- so
neither the current game nor any later game contributes. A player with no
history gets exactly the league constant (full shrinkage), and
``pa_logn`` = ``log1p(prior on-court offensive possessions)`` lets the net
learn how much to trust the rest (CLAUDE.md "Required behavior"). "Sum"
pooling is omitted on purpose: with a fixed 5-man lineup it is exactly
5 x mean (no extra information). Co-occurrence embeddings
(``research.preprocess.embeddings``) are NOT used: they are fit at a single
``as_of_date`` so a per-possession training frame would need a refit per
refresh date (season-granular at best) -- out of scope, see the milestone
notes.

This module is deliberately SQL-first (CLAUDE.md "prefer SQL in DuckDB for
feature building"): the join is one query against ``possessions``/``games``
plus the team-ratings table registered as a view, so the local export
script (``research/models/colab/export_training_data.py``) can run the *exact
same* query as a streaming ``COPY ... TO ... (FORMAT PARQUET)`` without
ever materializing the full result set in Python -- required on this
8GB, memory-bound box (see ``NEXT_SESSION.md``).
"""

from __future__ import annotations

import duckdb
import polars as pl

from nba.features.possession_features import build_team_possession_rates

#: Outcome classes the ``outcome`` head predicts. Order is the model's
#: class index order everywhere (training frame, net, metrics) -- do not
#: reorder without retraining. ``FT_trip`` and ``other`` are real possession
#: schema values (CLAUDE.md data schema / nba/parse/possessions.py); every
#: observed value must be one of these six or the row is dropped (see
#: ``build_possession_step_training_frame``).
OUTCOME_CLASSES: list[str] = ["FGM2", "FGM3", "FGA_miss", "TOV", "FT_trip", "other"]

#: Zone classes the ``zone`` head predicts, conditioned on the possession
#: being a shot attempt (FGM2/FGM3/FGA_miss). Matches
#: ``research.features.player_possession_features.ZONES`` (no corner-3/above-
#: break-3 split in this schema -- see that module's docstring).
ZONE_CLASSES: list[str] = ["rim", "mid", "above3"]

#: Outcomes that constitute a shot attempt (the ``zone``/``make`` heads are
#: only meaningful -- and only trained -- on these rows).
SHOT_ATTEMPT_OUTCOMES: list[str] = ["FGM2", "FGM3", "FGA_miss"]

#: Outcomes counted as a made shot for the ``make`` head's binary label.
MADE_OUTCOMES: list[str] = ["FGM2", "FGM3"]

#: Team-level + intra-game-state feature columns (the original rung-4 input).
POSSESSION_STEP_TEAM_FEATURE_COLUMNS: list[str] = [
    "period",
    "clock_start",
    "score_diff",
    "off_is_home",
    "off_off_rtg_prior",
    "off_def_rtg_prior",
    "off_pace_prior",
    "def_off_rtg_prior",
    "def_def_rtg_prior",
    "league_avg_ppp_asof",
]

#: Per-player as-of stats (one shrunk value each per player per game). The
#: value is the league constant (``PLAYER_STAT_PRIORS``) for a player with
#: no history. ``pseudo`` = shrinkage pseudo-count in the stat's own unit
#: of exposure (on-court possessions, shot attempts, or shot-attempt trips).
PLAYER_ASOF_STATS: list[str] = [
    "usage",  # own FGA per on-court offensive possession
    "zmix_rim",
    "zmix_mid",
    "zmix_above3",
    "zfg_rim",
    "zfg_mid",
    "zfg_above3",
    "tov",  # on-court team TOV per offensive possession
    "oreb",  # on-court team OREB-trips per offensive possession
    "oreb_allowed",  # opponent OREB-trips per defensive possession
    "forced_tov",  # opponent TOV per defensive possession
    "opp_make",  # opponent make rate on shot-attempt trips, defensive
    "logn",  # log1p(prior on-court offensive possessions)
]

#: League-constant priors (approximate empirical means over the 2022-26
#: possessions table; zone constants as in the player-possession module).
PLAYER_STAT_PRIORS: dict[str, float] = {
    "usage": 0.152,
    "zmix_rim": 0.3422,
    "zmix_mid": 0.2566,
    "zmix_above3": 0.4012,
    "zfg_rim": 0.7212,
    "zfg_mid": 0.5172,
    "zfg_above3": 0.4247,
    "tov": 0.1413,
    "oreb": 0.1454,
    "oreb_allowed": 0.1454,
    "forced_tov": 0.1413,
    "opp_make": 0.5496,
    "logn": 0.0,
}

PLAYER_POOL_AGGS: list[str] = ["mean", "max", "min"]
_POOL_SQL_FN = {"mean": "AVG", "max": "MAX", "min": "MIN"}


def player_pool_columns(side: str) -> list[str]:
    """Pooled lineup columns for ``side`` in {"off", "def"} (stat-major order)."""
    return [f"{side}_pa_{s}_{a}" for s in PLAYER_ASOF_STATS for a in PLAYER_POOL_AGGS]


POSSESSION_STEP_PLAYER_FEATURE_COLUMNS: list[str] = [
    *player_pool_columns("off"),
    *player_pool_columns("def"),
]

#: Neutral (league-constant) value of every feature column, used when a
#: possession has no lineup and by the game-level ``predict`` fallback.
POSSESSION_STEP_NEUTRAL: dict[str, float] = {
    f"{side}_pa_{s}_{a}": PLAYER_STAT_PRIORS[s]
    for side in ("off", "def")
    for s in PLAYER_ASOF_STATS
    for a in PLAYER_POOL_AGGS
}

#: Numeric feature columns consumed by the trunk of
#: ``research.models.rung4_stepheads.StepHeadsNet`` (team + player pooled).
#: Order matters -- it is the tensor column order used by both the local
#: runs and the Colab notebook (kept in sync by a test).
POSSESSION_STEP_FEATURE_COLUMNS: list[str] = [
    *POSSESSION_STEP_TEAM_FEATURE_COLUMNS,
    *POSSESSION_STEP_PLAYER_FEATURE_COLUMNS,
]

#: Label columns the training frame carries alongside the features.
POSSESSION_STEP_LABEL_COLUMNS: list[str] = [
    "outcome",
    "shot_zone",
    "made_shot",
    "oreb",
    "duration_s",
]

_W = "ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING"

#: (count column in per-game agg, ...) -- cumulative prior sums are built from this.
_COUNT_COLS = [
    "n_off", "n_tov", "n_oreb", "n_def", "n_forced", "n_oreb_allowed", "n_opp_fga", "n_opp_fgm",
    "n_fga", "n_rim", "n_rim_m", "n_mid", "n_mid_m", "n_a3", "n_a3_m",
]  # fmt: skip


def _shrunk(num: str, den: str, stat: str, pseudo: float) -> str:
    c = PLAYER_STAT_PRIORS[stat]
    return f"CAST(({num} + {pseudo} * {c}) / ({den} + {pseudo}) AS REAL) AS pa_{stat}"


def _player_asof_sql() -> str:
    """CTE chain ending in ``player_asof`` (game_id, player_id, pa_* stats)."""
    cum = ",\n        ".join(
        f"COALESCE(SUM({c}) OVER (PARTITION BY player_id ORDER BY game_date, game_id {_W}), 0)"
        f" AS c_{c}"
        for c in _COUNT_COLS
    )
    stats = [
        _shrunk("c_n_fga", "c_n_off", "usage", 300.0),
        _shrunk("c_n_rim", "c_n_fga", "zmix_rim", 150.0),
        _shrunk("c_n_mid", "c_n_fga", "zmix_mid", 150.0),
        _shrunk("c_n_a3", "c_n_fga", "zmix_above3", 150.0),
        _shrunk("c_n_rim_m", "c_n_rim", "zfg_rim", 100.0),
        _shrunk("c_n_mid_m", "c_n_mid", "zfg_mid", 100.0),
        _shrunk("c_n_a3_m", "c_n_a3", "zfg_above3", 100.0),
        _shrunk("c_n_tov", "c_n_off", "tov", 500.0),
        _shrunk("c_n_oreb", "c_n_off", "oreb", 500.0),
        _shrunk("c_n_oreb_allowed", "c_n_def", "oreb_allowed", 500.0),
        _shrunk("c_n_forced", "c_n_def", "forced_tov", 500.0),
        _shrunk("c_n_opp_fgm", "c_n_opp_fga", "opp_make", 400.0),
        "CAST(LN(1 + c_n_off) AS REAL) AS pa_logn",
    ]
    stats_sql = ",\n        ".join(stats)
    pg_groups = (
        ("o", ["n_off", "n_tov", "n_oreb"]),
        ("d", ["n_def", "n_forced", "n_oreb_allowed", "n_opp_fga", "n_opp_fgm"]),
        ("s", ["n_fga", "n_rim", "n_rim_m", "n_mid", "n_mid_m", "n_a3", "n_a3_m"]),
    )
    pg_select = ", ".join(
        f"COALESCE({t}.{c}, 0) AS {c}" for t, group_cols in pg_groups for c in group_cols
    )
    return f"""
off_gp AS (
    SELECT game_id, u.player_id AS player_id,
           COUNT(*) AS n_off,
           SUM((outcome = 'TOV')::INT) AS n_tov,
           SUM(COALESCE(oreb, FALSE)::INT) AS n_oreb
    FROM possessions, UNNEST(off_players::INTEGER[]) AS u(player_id)
    GROUP BY 1, 2
),
def_gp AS (
    SELECT game_id, u.player_id AS player_id,
           COUNT(*) AS n_def,
           SUM((outcome = 'TOV')::INT) AS n_forced,
           SUM(COALESCE(oreb, FALSE)::INT) AS n_oreb_allowed,
           SUM((outcome IN ('FGM2', 'FGM3', 'FGA_miss'))::INT) AS n_opp_fga,
           SUM((outcome IN ('FGM2', 'FGM3'))::INT) AS n_opp_fgm
    FROM possessions, UNNEST(def_players::INTEGER[]) AS u(player_id)
    GROUP BY 1, 2
),
shot_gp AS (
    SELECT game_id, shooter_id AS player_id,
           COUNT(*) AS n_fga,
           SUM((shot_zone = 'rim')::INT) AS n_rim,
           SUM((shot_zone = 'rim' AND outcome = 'FGM2')::INT) AS n_rim_m,
           SUM((shot_zone = 'mid')::INT) AS n_mid,
           SUM((shot_zone = 'mid' AND outcome = 'FGM2')::INT) AS n_mid_m,
           SUM((shot_zone = 'above3')::INT) AS n_a3,
           SUM((shot_zone = 'above3' AND outcome = 'FGM3')::INT) AS n_a3_m
    FROM possessions
    WHERE shooter_id IS NOT NULL AND outcome IN ('FGM2', 'FGM3', 'FGA_miss')
    GROUP BY 1, 2
),
pg_keys AS (
    SELECT game_id, player_id FROM off_gp
    UNION
    SELECT game_id, player_id FROM def_gp
),
pg AS (
    SELECT k.game_id, k.player_id, g.game_date,
           {pg_select}
    FROM pg_keys k
    JOIN games g ON g.game_id = k.game_id
    LEFT JOIN off_gp o ON o.game_id = k.game_id AND o.player_id = k.player_id
    LEFT JOIN def_gp d ON d.game_id = k.game_id AND d.player_id = k.player_id
    LEFT JOIN shot_gp s ON s.game_id = k.game_id AND s.player_id = k.player_id
),
pg_cum AS (
    SELECT game_id, player_id,
        {cum}
    FROM pg
),
player_asof AS (
    SELECT game_id, player_id,
        {stats_sql}
    FROM pg_cum
)"""


def player_asof_sql() -> str:
    """Standalone query returning ``(game_id, player_id, pa_<stat>...)`` for every player-game.

    Strictly-prior as-of values (see module docstring); also what
    ``research.sim.learned_heads`` uses to look up each player's latest vector at
    game-prediction time.
    """
    return f"WITH {_player_asof_sql().lstrip()} SELECT * FROM player_asof"


def _pool_sql(side: str, players_col: str) -> str:
    aggs = ",\n        ".join(
        f"CAST({_POOL_SQL_FN[a]}(pa.pa_{s}) AS REAL) AS {side}_pa_{s}_{a}"
        for s in PLAYER_ASOF_STATS
        for a in PLAYER_POOL_AGGS
    )
    return f"""
{side}_pool AS (
    SELECT l.game_id, l.poss_idx,
        {aggs}
    FROM (
        SELECT game_id, poss_idx, UNNEST({players_col}::INTEGER[]) AS player_id
        FROM possessions
    ) l
    JOIN player_asof pa ON pa.game_id = l.game_id AND pa.player_id = l.player_id
    GROUP BY 1, 2
)"""


def _player_select_cols() -> str:
    return ",\n    ".join(
        f"COALESCE({side}_pool.{c}, {POSSESSION_STEP_NEUTRAL[c]}) AS {c}"
        for side in ("off", "def")
        for c in player_pool_columns(side)
    )


_JOIN_SQL = f"""
WITH poss AS (
    SELECT p.*, g.game_date, g.season
    FROM possessions p
    JOIN games g ON g.game_id = p.game_id
),{_player_asof_sql()},{_pool_sql("off", "off_players")},{_pool_sql("def", "def_players")}
SELECT
    poss.game_id,
    poss.poss_idx,
    poss.game_date,
    poss.season,
    poss.period,
    poss.clock_start,
    poss.clock_end,
    poss.score_diff,
    off.is_home AS off_is_home,
    off.off_rtg_prior AS off_off_rtg_prior,
    off.def_rtg_prior AS off_def_rtg_prior,
    off.pace_prior AS off_pace_prior,
    def_r.off_rtg_prior AS def_off_rtg_prior,
    def_r.def_rtg_prior AS def_def_rtg_prior,
    off.league_avg_ppp_asof AS league_avg_ppp_asof,
    {_player_select_cols()},
    poss.outcome,
    poss.shot_zone,
    poss.oreb,
    poss.fta,
    poss.pts
FROM poss
JOIN team_ratings off ON off.game_id = poss.game_id AND off.team_id = poss.off_team
JOIN team_ratings def_r ON def_r.game_id = poss.game_id AND def_r.team_id = poss.def_team
LEFT JOIN off_pool ON off_pool.game_id = poss.game_id AND off_pool.poss_idx = poss.poss_idx
LEFT JOIN def_pool ON def_pool.game_id = poss.game_id AND def_pool.poss_idx = poss.poss_idx
ORDER BY poss.game_date, poss.game_id, poss.poss_idx
"""


def possession_step_join_sql() -> str:
    """The SQL this module runs, exposed so the export script can ``COPY`` it directly.

    Callers must first register ``nba.features.possession_features.
    build_team_possession_rates(con)``'s output as a view named
    ``team_ratings`` on ``con`` (e.g. ``con.register("team_ratings", df)``)
    -- see :func:`build_possession_step_training_frame` for the reference
    usage and ``research/models/colab/export_training_data.py`` for the
    streaming-``COPY`` usage that never materializes the join in Python.
    """
    return _JOIN_SQL


def build_possession_step_training_frame(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """One row per possession: as-of features + the five step-head labels.

    Small-data convenience path (fixture tests, CPU smoke tests): runs the
    join fully in Python/polars. For the real multi-season DB, use
    ``research/models/colab/export_training_data.py`` instead, which issues the
    identical join as a streaming ``COPY ... TO parquet`` so the full
    result never sits in Python memory at once.
    """
    team_ratings = build_team_possession_rates(con)
    if team_ratings.height == 0:
        return _empty_frame()

    con.register("team_ratings", team_ratings)
    try:
        raw = con.execute(_JOIN_SQL).pl()
    finally:
        con.unregister("team_ratings")

    if raw.height == 0:
        return _empty_frame()

    return derive_step_labels(raw)


#: Upper clamp (seconds) on the duration target -- the parser occasionally
#: emits multi-minute "possessions" across dead-ball/period boundaries.
DURATION_MAX_S = 60.0


def derive_step_labels(raw: pl.DataFrame) -> pl.DataFrame:
    """Turn the raw ``possession_step_join_sql`` output into the training frame.

    ``raw`` is exactly what the SQL join (or the exported parquet) holds:
    ``outcome``, ``shot_zone``, ``oreb``, ``clock_start``, ``clock_end`` and
    the feature columns. This adds the derived labels ``made_shot`` and
    ``duration_s``, masks ``shot_zone`` to shot attempts, casts
    ``off_is_home`` to int, and drops rows with an unrecognized outcome or a
    shot attempt with no zone (never silently train on a bad label). The
    Colab notebook carries an inline copy of this function; a test keeps
    the two in sync.
    """
    raw = raw.filter(pl.col("outcome").is_in(OUTCOME_CLASSES))
    is_shot = pl.col("outcome").is_in(SHOT_ATTEMPT_OUTCOMES)
    made = pl.col("outcome").is_in(MADE_OUTCOMES)
    # Seconds elapsed (clock counts down within a period), clamped to
    # [0, DURATION_MAX_S] -- see nba/parse/possessions.py "Known limitations".
    duration = (pl.col("clock_start") - pl.col("clock_end")).clip(0.0, DURATION_MAX_S)
    out = raw.with_columns(
        [
            pl.col("off_is_home").cast(pl.Int64),
            made.cast(pl.Int64).alias("made_shot"),
            pl.when(is_shot).then(pl.col("shot_zone")).otherwise(None).alias("shot_zone"),
            pl.col("oreb").fill_null(False),
            duration.alias("duration_s"),
        ]
    ).filter(~is_shot | pl.col("shot_zone").is_in(ZONE_CLASSES))
    keep = [
        "game_id",
        "poss_idx",
        "game_date",
        "season",
        *POSSESSION_STEP_FEATURE_COLUMNS,
        *POSSESSION_STEP_LABEL_COLUMNS,
    ]
    return out.select(keep)


def _empty_frame() -> pl.DataFrame:
    schema: dict[str, type[pl.DataType]] = {
        "game_id": pl.Utf8,
        "poss_idx": pl.Int64,
        "game_date": pl.Date,
        "season": pl.Int64,
    }
    for c in POSSESSION_STEP_FEATURE_COLUMNS:
        schema[c] = pl.Float64
    schema["outcome"] = pl.Utf8
    schema["shot_zone"] = pl.Utf8
    schema["made_shot"] = pl.Int64
    schema["oreb"] = pl.Boolean
    schema["duration_s"] = pl.Float64
    return pl.DataFrame(schema=schema)
