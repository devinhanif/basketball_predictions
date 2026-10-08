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

No-leakage discipline (mirrors ``nba.features.player_possession_features``
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

This module is deliberately SQL-first (CLAUDE.md "prefer SQL in DuckDB for
feature building"): the join is one query against ``possessions``/``games``
plus the team-ratings table registered as a view, so the local export
script (``nba/models/colab/export_training_data.py``) can run the *exact
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
#: ``nba.features.player_possession_features.ZONES`` (no corner-3/above-
#: break-3 split in this schema -- see that module's docstring).
ZONE_CLASSES: list[str] = ["rim", "mid", "above3"]

#: Outcomes that constitute a shot attempt (the ``zone``/``make`` heads are
#: only meaningful -- and only trained -- on these rows).
SHOT_ATTEMPT_OUTCOMES: list[str] = ["FGM2", "FGM3", "FGA_miss"]

#: Outcomes counted as a made shot for the ``make`` head's binary label.
MADE_OUTCOMES: list[str] = ["FGM2", "FGM3"]

#: Numeric (non-categorical) feature columns consumed by the trunk of
#: ``nba.models.rung4_stepheads.StepHeadsNet``. Order matters -- it is the
#: tensor column order used by both the local CPU smoke test and the Colab
#: training notebook (kept in sync by hand; see that notebook's docstring).
POSSESSION_STEP_FEATURE_COLUMNS: list[str] = [
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

#: Label columns the training frame carries alongside the features.
POSSESSION_STEP_LABEL_COLUMNS: list[str] = [
    "outcome",
    "shot_zone",
    "made_shot",
    "oreb",
    "duration_s",
]

_JOIN_SQL = """
WITH poss AS (
    SELECT p.*, g.game_date, g.season
    FROM possessions p
    JOIN games g ON g.game_id = p.game_id
)
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
    poss.outcome,
    poss.shot_zone,
    poss.oreb,
    poss.fta,
    poss.pts
FROM poss
JOIN team_ratings off ON off.game_id = poss.game_id AND off.team_id = poss.off_team
JOIN team_ratings def_r ON def_r.game_id = poss.game_id AND def_r.team_id = poss.def_team
ORDER BY poss.game_date, poss.game_id, poss.poss_idx
"""


def possession_step_join_sql() -> str:
    """The SQL this module runs, exposed so the export script can ``COPY`` it directly.

    Callers must first register ``nba.features.possession_features.
    build_team_possession_rates(con)``'s output as a view named
    ``team_ratings`` on ``con`` (e.g. ``con.register("team_ratings", df)``)
    -- see :func:`build_possession_step_training_frame` for the reference
    usage and ``nba/models/colab/export_training_data.py`` for the
    streaming-``COPY`` usage that never materializes the join in Python.
    """
    return _JOIN_SQL


def build_possession_step_training_frame(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """One row per possession: as-of features + the five step-head labels.

    Small-data convenience path (fixture tests, CPU smoke tests): runs the
    join fully in Python/polars. For the real multi-season DB, use
    ``nba/models/colab/export_training_data.py`` instead, which issues the
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
