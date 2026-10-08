"""Depth-chart rank + minutes-ceiling hazard (cold-start method 3/6 support,
CLAUDE.md "Cold start modeling" -- "unseen lineups" / "low-minute players").

Two signals, one row per ``(game_id, player_id)`` that appears in
``player_game_stats``:

``depth_chart_rank``
    1 = the teammate with the HIGHEST trailing as-of average minutes on
    this player's team as of this game (2 = next, ...). "Team" is this
    game's own ``player_game_stats.team_id`` (the roster that actually
    suited up for this game); "trailing average minutes" is each
    player's OWN prior-games history, league-wide (not filtered to the
    current team), since a trade carries a player's established role
    forward as the best available proxy for their depth-chart slot even
    immediately post-trade -- cold-start method 3 ("traded players") is
    the explicit, separate mechanism for correcting usage/role once more
    team-specific data exists; this feature intentionally stays simple.
    A player with zero prior games (debut) has trailing average 0.0 by
    documented default, so newcomers rank at the bottom of the chart
    among that game's teammates unless every teammate is equally new, in
    which case ties break on ascending ``player_id`` for determinism.

``minutes_ceiling_hazard``
    A hazard-style ``P(minutes capped / role reduced this game) in
    [0, 1]``, built from three as-of, interpretable pressure signals
    (documented formula, NOT fit/learned -- so it has no leakage surface
    and no tuning risk):

        hazard = clip(0.5 * cv_norm + 0.4 * depth_pressure + 0.1 * b2b, 0, 1)

    - ``cv_norm``: trailing-minutes volatility, the coefficient of
      variation of the player's own prior-games minutes
      (``std_minutes_prior / avg_minutes_prior``), clipped to ``[0, 2]``
      and rescaled to ``[0, 1]`` by dividing by 2. A player whose minutes
      swing wildly game to game is read as more likely to have a capped
      role on any given night than a steady-minutes player.
    - ``depth_pressure``: how closely the next-lower-ranked teammate's
      trailing average trails this player's own -- ``clip(1 - (avg_i -
      avg_below) / (avg_i + eps), 0, 1)``. A near-equal backup pushing on
      a player's minutes is read as rotation crowding; the lowest-ranked
      player on the chart (nobody below them) gets ``depth_pressure =
      0.0`` by definition.
    - ``b2b``: 1.0 on a back-to-back (``nba.features.team_features``'s
      as-of ``rest_days <= 1`` definition, reused unchanged), else 0.0 --
      the mechanistically obvious rest-load pressure on a minutes cap.

    Weights (0.5 / 0.4 / 0.1) are a documented, fixed prior, not tuned on
    any backtest -- consistent with CLAUDE.md's "assume leakage, audit
    as-of logic" caution: a learned hazard model is a natural rung-2
    follow-up once this ships and an ml-engineer A/Bs it against the
    actual realized minutes-drop rate, but that is out of scope here.

    ``NEW_PLAYER_HAZARD_DEFAULT`` (0.5, i.e. maximally uncertain) is used
    whenever a player has zero prior games -- their minutes trajectory is
    completely unknown, so the formula above (which needs a trailing
    average to even be defined) is skipped entirely rather than silently
    evaluating to a spuriously confident number from an all-zero history.

As-of discipline: every trailing statistic uses
``ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING`` ordered by
``(game_date, game_id)`` per player -- the same window convention as
``nba.features.team_features`` / ``nba.features.player_possession_
features`` / ``nba.props.minutes``. The current game's own minutes never
contribute to its own row. See
``tests/preprocess/test_depth_chart.py::test_planted_future_game_no_leakage``
for the proof.
"""

from __future__ import annotations

import duckdb
import numpy as np
import polars as pl

from nba.features.team_features import build_team_game_features

#: Documented default for a player's trailing average/std minutes with zero
#: prior games (debut) -- see module docstring. Ranks newcomers at the
#: bottom of the depth chart by construction.
DEFAULT_AVG_MINUTES_PRIOR = 0.0
DEFAULT_STD_MINUTES_PRIOR = 0.0

#: Hazard formula weights (fixed, documented prior -- see module docstring).
HAZARD_WEIGHT_CV = 0.5
HAZARD_WEIGHT_DEPTH_PRESSURE = 0.4
HAZARD_WEIGHT_B2B = 0.1

#: Maximally-uncertain hazard assigned to a player with zero prior games
#: (no trailing average exists to compute cv_norm/depth_pressure from).
NEW_PLAYER_HAZARD_DEFAULT = 0.5

#: cv (coefficient of variation) clip ceiling before rescaling to [0, 1].
_CV_CLIP_MAX = 2.0
_EPS = 1e-6

OUTPUT_COLUMNS: list[str] = ["game_id", "player_id", "depth_chart_rank", "minutes_ceiling_hazard"]

_BASE_SQL = """
WITH per_player AS (
    SELECT
        pgs.game_id,
        pgs.player_id,
        pgs.team_id,
        g.game_date,
        g.season,
        COALESCE(pgs.minutes, 0.0) AS minutes
    FROM player_game_stats pgs
    JOIN games g USING (game_id)
)
SELECT
    game_id,
    player_id,
    team_id,
    game_date,
    season,
    COUNT(*) OVER (
        PARTITION BY player_id ORDER BY game_date, game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS n_games_prior,
    AVG(minutes) OVER (
        PARTITION BY player_id ORDER BY game_date, game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS avg_minutes_prior,
    STDDEV_SAMP(minutes) OVER (
        PARTITION BY player_id ORDER BY game_date, game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS std_minutes_prior
FROM per_player
ORDER BY game_date, game_id, player_id
"""


def _query_base(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    con.execute(_BASE_SQL)
    columns = [d[0] for d in con.description]
    rows = con.fetchall()
    schema = {
        "game_id": pl.Utf8,
        "player_id": pl.Int64,
        "team_id": pl.Int64,
        "game_date": pl.Date,
        "season": pl.Int64,
        "n_games_prior": pl.Int64,
        "avg_minutes_prior": pl.Float64,
        "std_minutes_prior": pl.Float64,
    }
    return pl.DataFrame(rows, schema={c: schema[c] for c in columns}, orient="row")


def build_depth_chart_features(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """One row per ``(game_id, player_id)`` in ``player_game_stats``.

    Returns exactly the columns in :data:`OUTPUT_COLUMNS`:
    ``game_id`` (str), ``player_id`` (int), ``depth_chart_rank`` (int, 1 =
    highest trailing as-of minutes on that game's team), and
    ``minutes_ceiling_hazard`` (float in ``[0, 1]``). See module docstring
    for the exact as-of window and hazard formula.
    """
    base = _query_base(con)
    if base.height == 0:
        return pl.DataFrame(
            schema={
                "game_id": pl.Utf8,
                "player_id": pl.Int64,
                "depth_chart_rank": pl.Int64,
                "minutes_ceiling_hazard": pl.Float64,
            }
        )

    base = base.with_columns(
        [
            pl.col("n_games_prior").fill_null(0),
            pl.col("avg_minutes_prior").fill_null(DEFAULT_AVG_MINUTES_PRIOR),
            pl.col("std_minutes_prior").fill_null(DEFAULT_STD_MINUTES_PRIOR),
        ]
    )

    # Rest/back-to-back context, reused unchanged from nba.features.team_features
    # (same as-of discipline, see that module's own docstring).
    context = build_team_game_features(con).select(["game_id", "team_id", "b2b"])
    base = base.join(context, on=["game_id", "team_id"], how="left").with_columns(
        pl.col("b2b").fill_null(False)
    )

    # Sort within each (game_id, team_id) roster by descending trailing
    # average minutes, tie-broken by ascending player_id for determinism,
    # so depth_chart_rank and the "next teammate below" lookup are both a
    # straightforward row-order operation within the group.
    base = base.sort(
        ["game_id", "team_id", "avg_minutes_prior", "player_id"],
        descending=[
            False,
            False,
            True,
            False,
        ],
    )

    base = base.with_columns(
        depth_chart_rank=pl.int_range(1, pl.len() + 1).over(["game_id", "team_id"])
    )
    # The next-lower-ranked teammate's trailing average, within the same
    # (game_id, team_id) group, given the sort order above. Null (no one
    # below -- the lowest-ranked player on the chart) -> depth_pressure 0.
    base = base.with_columns(
        avg_minutes_below=pl.col("avg_minutes_prior").shift(-1).over(["game_id", "team_id"])
    )

    n_games_prior = base.get_column("n_games_prior").to_numpy()
    avg_minutes_prior = base.get_column("avg_minutes_prior").to_numpy()
    std_minutes_prior = base.get_column("std_minutes_prior").to_numpy()
    avg_minutes_below = base.get_column("avg_minutes_below").to_numpy()
    b2b = base.get_column("b2b").cast(pl.Float64).to_numpy()

    cv = std_minutes_prior / np.maximum(avg_minutes_prior, _EPS)
    cv_norm = np.clip(cv, 0.0, _CV_CLIP_MAX) / _CV_CLIP_MAX

    has_below = ~np.isnan(avg_minutes_below)
    gap = avg_minutes_prior - np.nan_to_num(avg_minutes_below)
    gap_ratio = np.where(
        has_below,
        gap / np.maximum(avg_minutes_prior, _EPS),
        1.0,  # placeholder; overwritten to 0 pressure below for no-one-below rows
    )
    depth_pressure = np.where(has_below, np.clip(1.0 - gap_ratio, 0.0, 1.0), 0.0)

    hazard = (
        HAZARD_WEIGHT_CV * cv_norm
        + HAZARD_WEIGHT_DEPTH_PRESSURE * depth_pressure
        + HAZARD_WEIGHT_B2B * b2b
    )
    hazard = np.clip(hazard, 0.0, 1.0)
    # Zero prior games -> no trailing average exists to drive the formula
    # above; use the documented maximally-uncertain default instead (see
    # module docstring), overriding whatever the (degenerate, all-zero)
    # inputs would otherwise have produced.
    hazard = np.where(n_games_prior == 0, NEW_PLAYER_HAZARD_DEFAULT, hazard)

    out = base.select(["game_id", "player_id"]).with_columns(
        depth_chart_rank=base.get_column("depth_chart_rank"),
        minutes_ceiling_hazard=pl.Series(hazard, dtype=pl.Float64),
    )
    return out.sort(["game_id", "player_id"])
