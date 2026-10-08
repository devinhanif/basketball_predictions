"""Cold-start method 4: season-level time-decay carryover for returning players.

CLAUDE.md cold-start method 4 ("Time-decay carryover for returning
players"): "Blend last season's rates with a regression-to-the-mean term
and an age curve. Tune decay and carryover weights by backtest." This
module generalizes ``nba.coldstart.carryover`` (a single-prior-season,
pure-math blend) to **multiple** prior seasons with exponential decay by
season gap, and wires it to a strictly as-of DuckDB builder so it can
actually sit in front of the existing empirical-Bayes shrinkage
(``nba.coldstart.shrinkage.shrink_rate``) the possession feature builders
already use.

Motivating gap (NEXT_SESSION.md, "Known data gaps"): as-of features
currently pool all prior seasons with EQUAL weight, so a season-opener
game (e.g. a star coming off an All-NBA prior season) gets no extra credit
for "last season was great" -- the model falls back almost entirely to the
archetype/position prior, producing underconfident predictions like
"0.1 pts / actual 35" for a known star's first game of a new season. This
module's whole purpose is to let last season (and, decayed further, the
seasons before that) carry weight into the new season's early, low-`n`
games, without ever looking at data from the current season's future.

NO-LEAKAGE CONTRACT (read before calling):

1. The "current-season observed" component for a game row is a cumulative
   sum over STRICTLY EARLIER games in the SAME season for that player
   (``ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING``, partitioned by
   ``(player_id, season)``, ordered by ``(game_date, game_id)``) -- the
   identical window discipline as
   ``nba.features.player_possession_features``.
2. The "prior-season" components are FULL-season totals from seasons
   strictly less than the current row's season (``season_totals.season <
   cur.season``). Because ``games.season`` is a monotonically increasing
   integer keyed to calendar time in this schema, any game in a strictly
   earlier season is guaranteed to have occurred entirely before any game
   in a later season -- so using the *complete* prior season's totals
   (not just games before some date) is still strictly as-of and commits
   no leakage, unlike using a partial/current season's future games.
3. The archetype/position prior is passed in by the caller (this module
   does not compute it -- see CLAUDE.md method 2 / ``nba.coldstart.
   archetypes``, owned by another agent). Callers must ensure whatever
   they pass was itself computed as-of.
4. See ``tests/features/test_time_decay.py::test_future_game_excluded_from_as_of_window``
   for the planted-future-game proof that a strictly later game in the
   current season never leaks into ``cur_num``/``cur_den``/``cur_n``.

Everything in this module is pure-function / SQL-window based -- no
fitted state, no scaler, nothing to leak across a train/test boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import polars as pl

from nba.coldstart.carryover import carryover_blend
from nba.coldstart.shrinkage import shrink_rate


@dataclass(frozen=True)
class TimeDecayConfig:
    """Tunable knobs for the season-level carryover blend.

    All four defaults below are reasonable starting points, NOT backtested
    values -- CLAUDE.md cold-start method 4 explicitly calls for tuning
    decay/carryover weights by walk-forward backtest (mirroring
    ``nba.coldstart.shrinkage.tune_pseudo_count``'s pattern); that tuning
    pass has not been run yet. Treat every field here as "tune by
    backtest, not yet tuned."
    """

    #: Seasons for a prior season's weight to halve. 1.5 means last season
    #: carries ~2x the weight of two seasons ago, ~4x three seasons ago.
    half_life_seasons: float = 1.5
    #: Regression-to-the-mean weight (``nba.coldstart.carryover.
    #: carryover_blend``'s ``decay_weight``) applied when blending the
    #: decayed prior-season rate with the archetype/position prior -- 0
    #: trusts the decayed prior-season rate fully, 1 regresses fully to
    #: the archetype prior.
    carryover_decay_weight: float = 0.3
    #: Empirical-Bayes pseudo-count (``nba.coldstart.shrinkage.shrink_rate``'s
    #: ``k``) for shrinking the current season's observed rate toward the
    #: carryover component as current-season ``n`` grows.
    k: float = 150.0
    peak_age: float = 27.0
    age_curve_width: float = 9.0


def season_decay_weights(season_gaps: np.ndarray | float, half_life_seasons: float) -> np.ndarray:
    """Exponential decay weight for a prior season, ``0.5 ** (gap / half_life)``.

    ``season_gaps`` of 1 means "last season" (weight 1.0 by construction
    regardless of half-life); larger gaps decay toward 0. Monotonically
    decreasing in ``season_gaps`` for any positive half-life.
    """
    gaps = np.asarray(season_gaps, dtype=float)
    hl = max(float(half_life_seasons), 1e-6)
    return np.asarray(np.power(0.5, (gaps - 1.0) / hl), dtype=float)


def decayed_prior_season_rate(
    prior_rates: np.ndarray,
    prior_ns: np.ndarray,
    season_gaps: np.ndarray,
    half_life_seasons: float,
) -> tuple[float, float]:
    """Blend multiple prior seasons' rates into one decayed "effective last
    season" rate, weighting each season by ``sample_size x exponential
    season-decay``.

    Returns ``(decayed_rate, effective_n)``. ``effective_n`` is the
    decay-weighted pseudo-sample-size, used downstream only as a signal of
    "how much decayed history exists" (e.g. to decide whether to fall back
    to the archetype prior entirely); it is NOT the real possession count.
    With zero prior seasons (``len == 0``) or all-zero sample sizes,
    returns ``(nan, 0.0)`` so the caller can detect "no history" and fall
    back to the archetype prior outright.
    """
    rates = np.asarray(prior_rates, dtype=float)
    ns = np.asarray(prior_ns, dtype=float)
    gaps = np.asarray(season_gaps, dtype=float)
    if rates.size == 0:
        return float("nan"), 0.0
    decay = season_decay_weights(gaps, half_life_seasons)
    weights = decay * ns
    total_w = float(np.sum(weights))
    if total_w <= 0:
        return float("nan"), 0.0
    decayed_rate = float(np.sum(weights * rates) / total_w)
    return decayed_rate, total_w


def blend_as_of_rate(
    cur_obs_rate: float,
    cur_n: float,
    prior_rates: np.ndarray,
    prior_ns: np.ndarray,
    season_gaps: np.ndarray,
    archetype_prior: float,
    config: TimeDecayConfig,
    age: float | None = None,
) -> float:
    """The core as-of blend: current-season observed rate, shrunk toward a
    carryover component that itself blends exponentially-decayed prior
    seasons with the archetype/position prior and an age-curve adjustment.

    Contract: every argument must already be computed strictly as-of (see
    module docstring points 1-3) -- this function does no date filtering
    itself, it only blends numbers the caller has already made safe.

    Limiting behavior (see tests/features/test_time_decay.py):
    - As ``cur_n -> infinity``, the result converges to ``cur_obs_rate``
      (empirical-Bayes shrinkage dominates with enough current-season data).
    - As ``cur_n -> 0`` (season opener / cold start), the result converges
      to the carryover component: the decayed prior-season rate blended
      with the archetype prior (and age curve), NOT the archetype prior
      alone -- this is exactly the fix for the Tatum-style season-opener
      underprediction.
    - With zero prior-season history too, the carryover component itself
      collapses to ``archetype_prior`` (true rookie / no-history case).
    """
    decayed_rate, effective_n = decayed_prior_season_rate(
        prior_rates, prior_ns, season_gaps, config.half_life_seasons
    )
    if effective_n <= 0.0 or np.isnan(decayed_rate):
        carryover_component = float(archetype_prior)
    else:
        carryover_component = float(
            carryover_blend(
                decayed_rate,
                archetype_prior,
                config.carryover_decay_weight,
                age=age,
                peak_age=config.peak_age,
                age_curve_width=config.age_curve_width,
            )
        )
    posterior = shrink_rate(cur_obs_rate, cur_n, carryover_component, config.k)
    return float(posterior)


_WINDOW = "ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING"

_CURRENT_SEASON_SQL = f"""
WITH per_game AS (
    SELECT
        pgs.game_id, pgs.player_id, pgs.team_id, g.game_date, g.season,
        {{numerator_col}} AS num, {{denominator_col}} AS den
    FROM player_game_stats pgs
    JOIN games g USING (game_id)
),
season_totals AS (
    SELECT player_id, season, SUM(num) AS season_num, SUM(den) AS season_den,
           COUNT(*) AS season_games
    FROM per_game
    GROUP BY 1, 2
),
cur AS (
    SELECT
        game_id, player_id, team_id, game_date, season,
        SUM(num) OVER (
            PARTITION BY player_id, season ORDER BY game_date, game_id {_WINDOW}
        ) AS cur_num,
        SUM(den) OVER (
            PARTITION BY player_id, season ORDER BY game_date, game_id {_WINDOW}
        ) AS cur_den,
        COUNT(*) OVER (
            PARTITION BY player_id, season ORDER BY game_date, game_id {_WINDOW}
        ) AS cur_n
    FROM per_game
)
SELECT
    c.game_id, c.player_id, c.team_id, c.game_date, c.season,
    COALESCE(c.cur_num, 0.0) AS cur_num,
    COALESCE(c.cur_den, 0.0) AS cur_den,
    COALESCE(c.cur_n, 0) AS cur_n,
    COALESCE(
        LIST(st.season_num ORDER BY st.season) FILTER (WHERE st.season IS NOT NULL),
        []
    ) AS prior_nums,
    COALESCE(
        LIST(st.season_den ORDER BY st.season) FILTER (WHERE st.season IS NOT NULL),
        []
    ) AS prior_dens,
    COALESCE(
        LIST(st.season_games ORDER BY st.season) FILTER (WHERE st.season IS NOT NULL),
        []
    ) AS prior_games,
    COALESCE(
        LIST(c.season - st.season ORDER BY st.season) FILTER (WHERE st.season IS NOT NULL),
        []
    ) AS season_gaps
FROM cur c
LEFT JOIN season_totals st ON st.player_id = c.player_id AND st.season < c.season
GROUP BY c.game_id, c.player_id, c.team_id, c.game_date, c.season,
         c.cur_num, c.cur_den, c.cur_n
ORDER BY c.game_date, c.game_id, c.player_id
"""

TIME_DECAY_RATE_COLUMNS: list[str] = ["rate_cur_obs", "rate_carryover_blended", "n_cur"]


def build_player_season_decayed_rates(
    con: duckdb.DuckDBPyConnection,
    numerator_col: str,
    denominator_col: str,
    archetype_prior: float | pl.DataFrame,
    config: TimeDecayConfig | None = None,
    age_as_of: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """One row per (game_id, player_id): strictly as-of, time-decay-carryover
    blended rate for an arbitrary ``numerator_col / denominator_col`` ratio
    read from ``player_game_stats`` (e.g. ``'pts'`` / ``'1'`` for points per
    game, or ``'reb'`` / ``'minutes'`` for a per-minute rebound rate).

    ``archetype_prior``: either a single float (applied league-wide, the
    simplest case), or a ``pl.DataFrame`` with columns ``player_id`` and
    ``prior`` for a per-player (e.g. position/archetype-group) prior. This
    module never computes the archetype prior itself -- CLAUDE.md method 2
    / ``nba.coldstart.archetypes`` (owned elsewhere) is the source; this
    is a required input, per the data-innovator task boundary.

    ``age_as_of``: optional ``pl.DataFrame`` with columns ``game_id``,
    ``player_id``, ``age_years`` (age strictly as of that game's date --
    caller's responsibility to compute from ``players_static.birth_date``
    without leaking). If omitted, the age-curve adjustment is skipped
    (equivalent to ``age=None`` -- "no age information available" rather
    than a silent assumption).

    See module docstring for the full no-leakage contract.
    """
    config = config or TimeDecayConfig()
    sql = _CURRENT_SEASON_SQL.format(numerator_col=numerator_col, denominator_col=denominator_col)
    raw = con.execute(sql).pl()

    if raw.height == 0:
        return raw.with_columns(
            [pl.lit(None, dtype=pl.Float64).alias(c) for c in TIME_DECAY_RATE_COLUMNS]
        )

    if isinstance(archetype_prior, pl.DataFrame):
        raw = raw.join(archetype_prior.select(["player_id", "prior"]), on="player_id", how="left")
        raw = raw.with_columns(pl.col("prior").fill_null(0.0))
    else:
        raw = raw.with_columns(pl.lit(float(archetype_prior)).alias("prior"))

    if age_as_of is not None:
        raw = raw.join(
            age_as_of.select(["game_id", "player_id", "age_years"]),
            on=["game_id", "player_id"],
            how="left",
        )
    else:
        raw = raw.with_columns(pl.lit(None, dtype=pl.Float64).alias("age_years"))

    out_rates = np.empty(raw.height, dtype=float)
    out_cur_obs = np.empty(raw.height, dtype=float)
    rows = raw.to_dicts()
    for i, row in enumerate(rows):
        cur_den = float(row["cur_den"])
        cur_num = float(row["cur_num"])
        cur_obs = cur_num / cur_den if cur_den > 0 else 0.0
        out_cur_obs[i] = cur_obs

        prior_nums = np.asarray(row["prior_nums"], dtype=float)
        prior_dens = np.asarray(row["prior_dens"], dtype=float)
        prior_games = np.asarray(row["prior_games"], dtype=float)
        season_gaps = np.asarray(row["season_gaps"], dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            safe_dens = np.where(prior_dens == 0, 1, prior_dens)
            prior_rates = np.where(prior_dens > 0, prior_nums / safe_dens, 0.0)

        age = row["age_years"]
        out_rates[i] = blend_as_of_rate(
            cur_obs_rate=cur_obs,
            cur_n=cur_den,
            prior_rates=prior_rates,
            prior_ns=prior_games,
            season_gaps=season_gaps,
            archetype_prior=float(row["prior"]),
            config=config,
            age=float(age) if age is not None else None,
        )

    base_cols = ["game_id", "player_id", "team_id", "game_date", "season", "cur_n"]
    return raw.select(base_cols).with_columns(
        [
            pl.Series("rate_cur_obs", out_cur_obs),
            pl.Series("rate_carryover_blended", out_rates),
            pl.col("cur_n").alias("n_cur"),
        ]
    )
