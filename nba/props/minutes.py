"""Minutes model (CLAUDE.md "Minutes model first").

"Most prop error comes from minutes uncertainty (injuries, blowouts,
rest). Model P(minutes) with a separate head, including a DNP/low-minute
branch." Implemented as a hurdle model:

    P(minutes = 0)      = 1 - p_play
    minutes | plays > 0  ~ Normal(mu, sigma) truncated to [0, max_minutes]

-- see ``nba.props.distributions.MinutesHurdleDist`` for the distribution
object this module produces.

CRITICAL NO-LEAKAGE RULE (CLAUDE.md risk #3): :func:`build_minutes_features`
returns **no** column derived from the target game's own minutes -- every
``*_prior`` column is a window aggregate over strictly earlier
``(game_date, game_id)`` rows for that player (same discipline as
``nba.features.team_features``). :func:`predict_minutes` only ever reads
that feature frame, never ``player_game_stats.minutes`` for the row being
predicted. ``tests/props/test_minutes_no_leakage.py`` proves this by
planting a wild future box score and asserting the earlier prediction is
byte-for-byte unchanged, plus a structural check that the feature frame
has no raw ``minutes`` column at all.

MIS-CENTERING FIX (real 4-season validation: steady, high-usage players
under-predicted ~-4.35 pts): the rolling windows below are bounded to the
trailing ``lookback_games`` games (default 150, see
``MinutesModelConfig.lookback_games``), not the player's full career.
Reproduced synthetically: a player whose role grows from bench minutes to
a steady ~28ppg starter role is undershot by ~10 points per game on an
*unbounded* career-to-date average, because years-old low-minute games
never drop out of the window. A bounded trailing window tracks the
current, durable role while still giving ample-history players (the
common case once the window is near-full) essentially the same
shrinkage-stabilized estimate as before.

GAME-CONTEXT WIRING (``MinutesModelConfig.use_game_context``, motivation:
rest/travel/tanking plausibly affect WHO PLAYS and HOW MUCH more than they
affect game win probability, which is where the win-prob rungs found no
edge over Elo from the same signals): when the flag is on,
:func:`build_minutes_features` left-joins the as-of team/game-context
columns (``GAME_CONTEXT_FEATURE_COLUMNS`` below -- rest/b2b/travel from
``nba.features.team_features``, tanking incentive/standings/national TV
from ``nba.features.game_context``) onto the per-player-game frame by
``(game_id, team_id)``. Those builders already guarantee their own
as-of-ness (see their module docstrings); joining by the *same* game's
``team_id`` introduces no new leakage path, since nothing about the
target game's own outcome is read.

:func:`predict_minutes` then applies a small, fixed-effect additive
adjustment (``MinutesModelConfig.*_adjust`` knobs) to ``p_play``/``mu``
built only from the three signals CLAUDE.md's motivation calls out as
mechanistically plausible here: back-to-backs, travel miles, and tanking
incentive. The adjustment is a pure per-row function of that row's own
(already as-of) context columns -- never fit from, or dependent on, any
other row -- so it cannot reintroduce the kind of future-row leakage a
global regression fit would risk; see
``tests/props/test_minutes_game_context.py`` for the planted-future-game
no-leakage proof (same pattern as
``tests/props/test_minutes_no_leakage.py``). The maintainer runs the real
4-season A/B on whether this helps; nothing here is tuned on a backtest.

LEARNED GAME-CONTEXT FIT (``MinutesModelConfig.use_learned_game_context``,
``docs/NEXT_OPTIONS.md`` §1 Option A): the hand-set adjustment above
regressed on the real 4-season backtest (DNP log loss 0.3881 -> 0.3908,
minutes MAE 6.185 -> 6.222) -- it fixed an aggregate bias but with the
wrong per-signal sign/magnitude, because the constants were guessed, not
fit. :func:`fit_learned_game_context` replaces them with a small ridge
regression of the *same* hurdle's own residuals
(``actual_played - p_play_base`` for the DNP head, ``actual_minutes -
mu_base`` for played rows) on ``GAME_CONTEXT_FEATURE_COLUMNS`` (plus any
``extra_features`` columns joined on ``(game_id, player_id)`` -- e.g. a
sibling depth-chart-hazard feature; this module never imports that
producer, it only accepts whatever frame is handed to it), fit **only** on
the chronologically-earlier ``learned_context_cal_frac`` split of the rows
passed in (:func:`nba.props.conformal.chronological_split`, the same
never-random discipline as ``DispersionConfig``/``ConformalConfig``). The
fitted :class:`LearnedGameContextParams` is a plain data object (coefficient
dict + intercept per head); :func:`predict_minutes` only ever *applies* it
as a pure per-row linear function of that row's own as-of columns -- same
no-cross-row-dependence argument as the hand-set adjustment, so the
planted-future-game no-leakage test still applies verbatim to this path
(see ``tests/props/test_minutes_learned_game_context.py``). Below
``learned_context_min_cal_n`` calibration rows the fit is explicitly
skipped (all-zero coefficients, noted) rather than fit to noise. Honest
framing per CLAUDE.md risk #6: this is **retrying an already-documented
failure** with a learned fit instead of a guess; if it still can't beat
the ``use_game_context=False`` null on the real 4-season A/B, that is
itself useful evidence the rest/b2b/travel/tanking signals carry no
*minutes* signal beyond the shrinkage baseline (mirrors the win-prob
rungs' identical finding for the same raw signals vs. Elo).

GARBAGE-TIME BRANCH (``MinutesModelConfig.use_garbage_time``,
``docs/NEXT_OPTIONS.md`` §1 Option B): a 35-point blowout and a 2-point
nail-biter currently get the same ``mu``/``sigma`` from history alone.
:func:`fit_garbage_time_params` learns a single "garbage time" mean minutes
(shrunk toward ``default_mu`` by the usual empirical-Bayes pseudo-count,
``garbage_time_k``) from calibration-split rows where a caller-supplied,
OPTIONAL pre-game ``projected_margin`` (absolute point-spread -- this
module never computes it; CLAUDE.md risk #3 means it must come from the
team-level sim/Elo, as-of, never the game's own final score) exceeds
``garbage_time_margin_threshold``. :func:`predict_minutes` then blends
``mu`` toward that learned mean and inflates ``sigma`` by a weight that
ramps linearly from 0 (at the threshold) to 1 (``garbage_time_margin_scale``
points past it) as ``|projected_margin|`` grows -- never from the target
game's own outcome, only from the caller-supplied pre-game projection, so
the same no-leakage argument holds as long as the caller's own
``projected_margin`` is itself as-of (not this module's responsibility to
verify, but tested here with a synthetic input per the task).
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import polars as pl
from sklearn.linear_model import Ridge

from nba.coldstart.shrinkage import shrink_rate
from nba.features.game_context import build_national_tv_features, build_standings_features
from nba.features.team_features import build_team_game_features
from nba.props.config import MinutesModelConfig
from nba.props.conformal import chronological_split
from nba.props.distributions import MinutesHurdleDist
from nba.truth.metrics import log_loss

#: Identifier / as-of columns always present; everything else is a
#: strictly-prior rolling aggregate. No raw ``minutes`` column on purpose.
MINUTES_FEATURE_COLUMNS: list[str] = [
    "games_played_prior",
    "n_played_prior",
    "play_rate_prior",
    "avg_minutes_given_played_prior",
    "std_minutes_given_played_prior",
    "starter_rate_prior",
]

#: Default trailing-games window, mirrors ``MinutesModelConfig.lookback_games``
#: (kept as a literal default here too so ``build_minutes_features(con)`` --
#: used throughout the existing tests -- keeps working unchanged).
DEFAULT_LOOKBACK_GAMES = 150

#: As-of team/game-context columns joined onto the minutes feature frame
#: when ``use_game_context`` is True -- see module docstring. Documented
#: fill-in defaults (used only if a (game_id, team_id) somehow has no
#: context row, which should not happen on real data since every one of
#: these builders is derived from the same ``games`` table) mirror
#: ``nba.features.team_features.FEATURE_DEFAULTS`` /
#: ``nba.features.game_context``'s own no-history fallbacks.
GAME_CONTEXT_FEATURE_COLUMNS: list[str] = [
    "rest_days",
    "b2b",
    "travel_miles_asof",
    "tanking_incentive",
    "is_national_tv",
    "games_into_season",
    "in_playoff_pos",
    "in_playin",
]

_GAME_CONTEXT_FILL_DEFAULTS: dict[str, object] = {
    "rest_days": 7.0,
    "b2b": False,
    "travel_miles_asof": 0.0,
    "tanking_incentive": 0.0,
    "is_national_tv": False,
    "games_into_season": 0,
    "in_playoff_pos": False,
    "in_playin": False,
}

_MINUTES_FEATURES_SQL = """
WITH per_player AS (
    SELECT
        pgs.game_id,
        pgs.player_id,
        pgs.team_id,
        g.game_date,
        g.season,
        pgs.minutes,
        CASE WHEN pgs.minutes > 0 THEN 1.0 ELSE 0.0 END AS played,
        CASE WHEN pgs.starter THEN 1.0 ELSE 0.0 END AS started
    FROM player_game_stats pgs
    JOIN games g USING (game_id)
)
SELECT
    game_id,
    player_id,
    team_id,
    game_date AS as_of,
    game_date,
    season,
    COUNT(*) OVER (
        PARTITION BY player_id ORDER BY game_date, game_id
        ROWS BETWEEN {lookback} PRECEDING AND 1 PRECEDING
    ) AS games_played_prior,
    COALESCE(
        SUM(played) OVER (
            PARTITION BY player_id ORDER BY game_date, game_id
            ROWS BETWEEN {lookback} PRECEDING AND 1 PRECEDING
        ),
        0.0
    ) AS n_played_prior,
    AVG(played) OVER (
        PARTITION BY player_id ORDER BY game_date, game_id
        ROWS BETWEEN {lookback} PRECEDING AND 1 PRECEDING
    ) AS play_rate_prior,
    AVG(CASE WHEN played = 1.0 THEN minutes END) OVER (
        PARTITION BY player_id ORDER BY game_date, game_id
        ROWS BETWEEN {lookback} PRECEDING AND 1 PRECEDING
    ) AS avg_minutes_given_played_prior,
    STDDEV_SAMP(CASE WHEN played = 1.0 THEN minutes END) OVER (
        PARTITION BY player_id ORDER BY game_date, game_id
        ROWS BETWEEN {lookback} PRECEDING AND 1 PRECEDING
    ) AS std_minutes_given_played_prior,
    AVG(started) OVER (
        PARTITION BY player_id ORDER BY game_date, game_id
        ROWS BETWEEN {lookback} PRECEDING AND 1 PRECEDING
    ) AS starter_rate_prior
FROM per_player
ORDER BY game_date, game_id, player_id
"""


def _build_minutes_game_context(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """One row per (game_id, team_id): the as-of context columns joined
    onto the minutes feature frame -- see module docstring.

    Reuses ``nba.features.team_features``/``nba.features.game_context``
    unchanged (this module owns no feature-building logic of its own for
    these columns, just the join). Both source builders key on
    ``(game_id, team_id)``, matching the minutes frame's own key.
    """
    team_feats = build_team_game_features(con).select(
        ["game_id", "team_id", "rest_days", "b2b", "travel_miles_asof"]
    )
    standings = build_standings_features(con).select(
        [
            "game_id",
            "team_id",
            "games_into_season",
            "in_playoff_pos",
            "in_playin",
            "tanking_incentive",
        ]
    )
    national_tv = build_national_tv_features(con)
    merged = team_feats.join(standings, on=["game_id", "team_id"], how="left")
    merged = merged.join(national_tv, on="game_id", how="left")
    return merged.with_columns(
        [pl.col(c).fill_null(v) for c, v in _GAME_CONTEXT_FILL_DEFAULTS.items()]
    )


def build_minutes_features(
    con: duckdb.DuckDBPyConnection,
    lookback_games: int = DEFAULT_LOOKBACK_GAMES,
    use_game_context: bool = True,
) -> pl.DataFrame:
    """One row per (game, player): strictly as-of minutes-history features.

    Deliberately excludes the target game's own ``minutes`` -- see module
    docstring. Every ``*_prior`` column is NULL on a player's first
    appearance (handled by :func:`predict_minutes` via shrinkage toward
    the league default, never by fabricating a non-null value here).

    ``lookback_games`` bounds the rolling window to the trailing N games
    (default 150, ``MinutesModelConfig.lookback_games``) rather than a
    player's entire career-to-date. An unbounded window lets a durable
    role change (bench -> starter, a jump in minutes) stay diluted by
    years-old, no-longer-representative games indefinitely, which is the
    documented mechanism behind the star-undershoot bias this fixes --
    see the module docstring and ``nba.props.stat_models`` for the
    points-side half of the same fix.

    ``use_game_context`` (mirrors ``MinutesModelConfig.use_game_context``)
    left-joins ``GAME_CONTEXT_FEATURE_COLUMNS`` onto the result by
    ``(game_id, team_id)`` when True; those columns are simply absent from
    the returned frame when False, so a caller can tell at a glance which
    mode produced a given frame.
    """
    con.execute(_MINUTES_FEATURES_SQL.format(lookback=int(lookback_games)))
    columns = [d[0] for d in con.description]
    rows = con.fetchall()
    schema = {
        "game_id": pl.Utf8,
        "player_id": pl.Int64,
        "team_id": pl.Int64,
        "as_of": pl.Date,
        "game_date": pl.Date,
        "season": pl.Int64,
        "games_played_prior": pl.Int64,
        "n_played_prior": pl.Float64,
        "play_rate_prior": pl.Float64,
        "avg_minutes_given_played_prior": pl.Float64,
        "std_minutes_given_played_prior": pl.Float64,
        "starter_rate_prior": pl.Float64,
    }
    df = pl.DataFrame(rows, schema={c: schema[c] for c in columns}, orient="row")
    if not use_game_context:
        return df
    context = _build_minutes_game_context(con)
    return df.join(context, on=["game_id", "team_id"], how="left").with_columns(
        [pl.col(c).fill_null(v) for c, v in _GAME_CONTEXT_FILL_DEFAULTS.items()]
    )


@dataclass
class LearnedGameContextParams:
    """Fitted output of :func:`fit_learned_game_context` -- a plain data
    object (no behavior beyond what's applied in :func:`predict_minutes`),
    same "params object produced by an offline fit, applied as a pure
    per-row function" pattern as ``nba.props.dispersion.DispersionCalibration``.

    ``feature_columns`` is the exact candidate-predictor list used at fit
    time (``GAME_CONTEXT_FEATURE_COLUMNS`` plus any ``extra_features``
    columns that were present) -- :func:`predict_minutes` looks up each by
    name in the features frame it's given and treats a missing column as
    0 (documented fallback; see module docstring).
    """

    feature_columns: list[str]
    p_play_coef: dict[str, float]
    p_play_intercept: float
    mu_coef: dict[str, float]
    mu_intercept: float
    n_cal: int
    n_cal_played: int
    note: str = ""


def _design_matrix(df: pl.DataFrame, cols: list[str]) -> np.ndarray:
    """Float, null-filled design matrix for ``cols`` (booleans cast to
    0.0/1.0) -- shared by fit and apply so both see identical encodings."""
    if not cols:
        return np.zeros((df.height, 0))
    arrs = []
    for c in cols:
        s = df.select(c).to_series()
        s = s.cast(pl.Float64) if s.dtype == pl.Boolean else s.cast(pl.Float64, strict=False)
        arrs.append(s.fill_null(0.0).to_numpy())
    return np.column_stack(arrs)


def fit_learned_game_context(
    features: pl.DataFrame,
    actual_played: np.ndarray,
    actual_minutes: np.ndarray,
    config: MinutesModelConfig | None = None,
    extra_features: pl.DataFrame | None = None,
) -> LearnedGameContextParams:
    """Fit :class:`LearnedGameContextParams` by ridge regression of this
    hurdle's own residuals on ``GAME_CONTEXT_FEATURE_COLUMNS`` -- see module
    docstring "LEARNED GAME-CONTEXT FIT". ``features`` must be the frame
    returned by :func:`build_minutes_features(..., use_game_context=True)`
    (needs both the context columns and ``game_date``/the base shrinkage
    inputs); ``actual_played``/``actual_minutes`` are the same-order target
    arrays (see :func:`fetch_actual_minutes`).

    ``extra_features`` (OPTIONAL) is left-joined onto ``features`` on
    ``(game_id, player_id)`` before the fit; any of its non-key columns are
    added as additional candidate predictors alongside
    ``GAME_CONTEXT_FEATURE_COLUMNS``. This module never imports the
    producer of such a frame (e.g. a depth-chart/hazard feature module) --
    it only accepts whatever is handed to it, so a caller can feed any
    additional as-of columns without a code change here.

    Fit is restricted to the chronologically-earlier
    ``learned_context_cal_frac`` split of the ROWS PASSED IN
    (:func:`chronological_split`, never random) -- the caller is
    responsible for passing an as-of-safe frame; this function adds no new
    leakage path on top of that because the fitted coefficients are a
    single global object applied identically (and additively) to every row
    at predict time, never looking at any other row's target.
    """
    cfg = config or MinutesModelConfig()
    df = features
    if extra_features is not None:
        extra_cols = [c for c in extra_features.columns if c not in ("game_id", "player_id")]
        df = df.join(extra_features, on=["game_id", "player_id"], how="left")
    else:
        extra_cols = []

    candidate_cols = [c for c in GAME_CONTEXT_FEATURE_COLUMNS if c in df.columns] + [
        c for c in extra_cols if c in df.columns
    ]

    dates = df.select("game_date").to_series().to_numpy()
    cal_mask, _test_mask = chronological_split(dates, cfg.learned_context_cal_frac)
    n_cal = int(cal_mask.sum())
    if n_cal < cfg.learned_context_min_cal_n:
        note = (
            f"only {n_cal} calibration-split player-games "
            f"(<{cfg.learned_context_min_cal_n}); learned game-context fit "
            "left as a no-op (all-zero coefficients) rather than fit to noise."
        )
        zeros = {c: 0.0 for c in candidate_cols}
        return LearnedGameContextParams(
            candidate_cols, zeros, 0.0, dict(zeros), 0.0, n_cal, 0, note
        )

    played = np.asarray(actual_played, dtype=float)
    minutes = np.asarray(actual_minutes, dtype=float)

    n_games = df.select("games_played_prior").to_series().fill_null(0).to_numpy()
    n_played = df.select("n_played_prior").to_series().fill_null(0.0).to_numpy()
    play_rate_obs = df.select("play_rate_prior").to_series().fill_null(0.0).to_numpy()
    mu_obs = df.select("avg_minutes_given_played_prior").to_series().fill_null(0.0).to_numpy()
    p_play_base = shrink_rate(play_rate_obs, n_games, cfg.default_p_play, cfg.k_play)
    mu_base = shrink_rate(mu_obs, n_played, cfg.default_mu, cfg.k_mu)

    x_full = _design_matrix(df, candidate_cols)
    x_cal = x_full[cal_mask]

    ridge_p = Ridge(alpha=cfg.learned_context_ridge_alpha)
    ridge_p.fit(x_cal, (played - p_play_base)[cal_mask])
    p_play_coef = dict(zip(candidate_cols, ridge_p.coef_.tolist(), strict=True))
    p_play_intercept = float(ridge_p.intercept_)

    played_cal_mask = cal_mask & (played > 0.0)
    n_cal_played = int(played_cal_mask.sum())
    if n_cal_played >= cfg.learned_context_min_cal_n:
        ridge_mu = Ridge(alpha=cfg.learned_context_ridge_alpha)
        ridge_mu.fit(x_full[played_cal_mask], (minutes - mu_base)[played_cal_mask])
        mu_coef = dict(zip(candidate_cols, ridge_mu.coef_.tolist(), strict=True))
        mu_intercept = float(ridge_mu.intercept_)
        note = ""
    else:
        mu_coef = {c: 0.0 for c in candidate_cols}
        mu_intercept = 0.0
        note = (
            f"only {n_cal_played} played calibration rows "
            f"(<{cfg.learned_context_min_cal_n}); mu head left as a no-op."
        )

    return LearnedGameContextParams(
        candidate_cols,
        p_play_coef,
        p_play_intercept,
        mu_coef,
        mu_intercept,
        n_cal,
        n_cal_played,
        note,
    )


def _apply_learned_context(
    features: pl.DataFrame, params: LearnedGameContextParams
) -> tuple[np.ndarray, np.ndarray]:
    """Pure per-row application of a fitted :class:`LearnedGameContextParams`
    -- never reads any other row, so it cannot reintroduce leakage (same
    argument as the hand-set adjustment; see module docstring)."""
    n = features.height
    p_adj = np.full(n, params.p_play_intercept, dtype=float)
    mu_adj = np.full(n, params.mu_intercept, dtype=float)
    if not params.feature_columns:
        return p_adj, mu_adj
    present = [c for c in params.feature_columns if c in features.columns]
    missing = [c for c in params.feature_columns if c not in features.columns]
    if present:
        x = _design_matrix(features, present)
        p_coef = np.array([params.p_play_coef.get(c, 0.0) for c in present])
        mu_coef = np.array([params.mu_coef.get(c, 0.0) for c in present])
        p_adj += x @ p_coef
        mu_adj += x @ mu_coef
    # ``missing`` columns (e.g. an extra_features column fit on but not
    # joined onto this particular predict-time frame) contribute 0 --
    # documented fallback, see LearnedGameContextParams docstring.
    del missing
    return p_adj, mu_adj


@dataclass
class GarbageTimeParams:
    """Fitted output of :func:`fit_garbage_time_params` -- the learned
    "garbage time" mean minutes blended into :func:`predict_minutes`'s
    ``mu`` once ``|projected_margin|`` is large; see module docstring
    "GARBAGE-TIME BRANCH"."""

    garbage_time_mu: float
    n_cal: int
    note: str = ""


def fit_garbage_time_params(
    features: pl.DataFrame,
    actual_played: np.ndarray,
    actual_minutes: np.ndarray,
    projected_margin: np.ndarray,
    config: MinutesModelConfig | None = None,
) -> GarbageTimeParams:
    """Learn a single garbage-time mean minutes (empirical-Bayes-shrunk
    toward ``default_mu``) from the chronologically-earlier
    ``garbage_time_cal_frac`` split of rows where the caller-supplied,
    OPTIONAL ``projected_margin`` (same order as ``features``) exceeds
    ``garbage_time_margin_threshold`` and the player actually played.

    ``projected_margin`` is NOT computed here -- CLAUDE.md risk #3 means it
    must be an as-of pre-game projection (team-level sim/Elo output) from
    the caller; this function only consumes it.
    """
    cfg = config or MinutesModelConfig()
    dates = features.select("game_date").to_series().to_numpy()
    cal_mask, _test_mask = chronological_split(dates, cfg.garbage_time_cal_frac)
    margin = np.abs(np.asarray(projected_margin, dtype=float))
    played = np.asarray(actual_played, dtype=float) > 0.0
    blowout_cal = cal_mask & played & (margin >= cfg.garbage_time_margin_threshold)
    n_cal = int(blowout_cal.sum())
    if n_cal < cfg.garbage_time_min_cal_n:
        note = (
            f"only {n_cal} blowout calibration player-games "
            f"(<{cfg.garbage_time_min_cal_n}); garbage_time_mu left at the "
            "no-op default_mu rather than fit to noise."
        )
        return GarbageTimeParams(cfg.default_mu, n_cal, note)

    minutes = np.asarray(actual_minutes, dtype=float)
    obs_mean = float(minutes[blowout_cal].mean())
    gt_mu = float(
        shrink_rate(np.array([obs_mean]), np.array([n_cal]), cfg.default_mu, cfg.garbage_time_k)[0]
    )
    return GarbageTimeParams(gt_mu, n_cal, "")


def predict_minutes(
    features: pl.DataFrame,
    config: MinutesModelConfig | None = None,
    k_multiplier: np.ndarray | None = None,
    learned_context: LearnedGameContextParams | None = None,
    projected_margin: np.ndarray | None = None,
    garbage_time: GarbageTimeParams | None = None,
) -> list[MinutesHurdleDist]:
    """Build one :class:`MinutesHurdleDist` per row of ``features``.

    Empirical-Bayes shrinkage (``nba.coldstart.shrinkage.shrink_rate``)
    blends each player's as-of observed rate toward the league default as
    ``n -> 0`` and toward the observed rate as ``n -> infinity`` -- the
    same discipline CLAUDE.md requires for every ``player_rates`` column.

    ``k_multiplier`` (optional, one per row, default all-ones) scales every
    pseudo-count up for rows flagged by role-change detection
    (``nba.props.role_change``), temporarily raising how much weight the
    shrinkage posterior gives to the prior right after a detected trade,
    injury return, or starter change -- CLAUDE.md: "On a flagged change,
    cold-start logic temporarily raises prior weight."

    ``learned_context`` (optional :class:`LearnedGameContextParams`, from
    :func:`fit_learned_game_context`) is applied instead of the hand-set
    ``use_game_context`` adjustment when ``cfg.use_learned_game_context``
    is True -- see module docstring "LEARNED GAME-CONTEXT FIT". Silently a
    no-op if the flag is on but no fitted params are supplied (so a caller
    can toggle the flag without crashing an already-running pipeline).

    ``projected_margin``/``garbage_time`` (both optional, the latter from
    :func:`fit_garbage_time_params`) are applied when
    ``cfg.use_garbage_time`` is True -- see module docstring "GARBAGE-TIME
    BRANCH". Also a no-op if the flag is on but either argument is missing.
    """
    cfg = config or MinutesModelConfig()
    n_games = features.select("games_played_prior").to_series().fill_null(0).to_numpy()
    n_played = features.select("n_played_prior").to_series().fill_null(0.0).to_numpy()
    play_rate_obs = features.select("play_rate_prior").to_series().fill_null(0.0).to_numpy()
    mu_obs = features.select("avg_minutes_given_played_prior").to_series().fill_null(0.0).to_numpy()
    sigma_obs = (
        features.select("std_minutes_given_played_prior").to_series().fill_null(0.0).to_numpy()
    )
    k_mult = (
        np.ones(features.height) if k_multiplier is None else np.asarray(k_multiplier, dtype=float)
    )

    p_play = shrink_rate(play_rate_obs, n_games, cfg.default_p_play, cfg.k_play * k_mult)
    mu = shrink_rate(mu_obs, n_played, cfg.default_mu, cfg.k_mu * k_mult)
    sigma = shrink_rate(sigma_obs, n_played, cfg.default_sigma, cfg.k_sigma * k_mult)
    sigma = np.clip(sigma, cfg.min_sigma, None)

    context_cols = ("b2b", "tanking_incentive", "travel_miles_asof")
    if cfg.use_learned_game_context and learned_context is not None:
        p_adj, mu_adj = _apply_learned_context(features, learned_context)
        if cfg.learned_context_max_n_played is not None:
            # Cold-start gate: the learned fit helps thin-history players and
            # slightly hurts established ones (real-DB A/B 2026-10-08), so apply
            # the adjustment only where n_played_prior is below the threshold.
            gate = (n_played < cfg.learned_context_max_n_played).astype(float)
            p_adj = p_adj * gate
            mu_adj = mu_adj * gate
        p_play = p_play + p_adj
        mu = mu + mu_adj
    elif cfg.use_game_context and all(c in features.columns for c in context_cols):
        b2b = features.select("b2b").to_series().fill_null(False).cast(pl.Float64).to_numpy()
        tanking = features.select("tanking_incentive").to_series().fill_null(0.0).to_numpy()
        travel_1000mi = (
            features.select("travel_miles_asof").to_series().fill_null(0.0).to_numpy() / 1000.0
        )
        p_play = p_play + (
            b2b * cfg.b2b_p_play_adjust
            + tanking * cfg.tanking_p_play_adjust
            + travel_1000mi * cfg.travel_p_play_adjust_per_1000mi
        )
        mu = mu + (
            b2b * cfg.b2b_mu_adjust
            + tanking * cfg.tanking_mu_adjust
            + travel_1000mi * cfg.travel_mu_adjust_per_1000mi
        )

    if cfg.use_garbage_time and garbage_time is not None and projected_margin is not None:
        margin = np.abs(np.asarray(projected_margin, dtype=float))
        scale = max(cfg.garbage_time_margin_scale, 1e-6)
        w = np.clip((margin - cfg.garbage_time_margin_threshold) / scale, 0.0, 1.0)
        mu = (1.0 - w) * mu + w * garbage_time.garbage_time_mu
        sigma = sigma * (1.0 + w * (cfg.garbage_time_sigma_inflate - 1.0))

    p_play = np.clip(p_play, 0.02, 0.99)
    mu = np.clip(mu, 0.0, cfg.max_minutes)

    return [
        MinutesHurdleDist(
            p_play=float(p_play[i]),
            mu=float(mu[i]),
            sigma=float(sigma[i]),
            max_minutes=cfg.max_minutes,
        )
        for i in range(features.height)
    ]


def fetch_actual_minutes(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Actual box-score minutes keyed by ``(game_id, player_id)`` --
    **evaluation only**. Never fed into :func:`build_minutes_features` /
    :func:`predict_minutes` (those deliberately never read this column --
    see module docstring and ``tests/props/test_minutes_no_leakage.py``);
    used solely by :func:`evaluate_minutes_model` to score the model's own
    predictions against the true outcome, the same role ``_target_frame``
    plays for the stat distributions in ``nba.props.run``.

    DNP rows store NULL ``minutes``; coalesced to ``0.0`` here -- a DNP
    really is 0 minutes played, not a missing value (same discipline as
    ``nba.props.run._target_frame``'s identical DNP-coalesce note).
    """
    con.execute(
        "SELECT game_id, player_id, COALESCE(minutes, 0.0) AS minutes FROM player_game_stats"
    )
    columns = [d[0] for d in con.description]
    rows = con.fetchall()
    schema = {"game_id": pl.Utf8, "player_id": pl.Int64, "minutes": pl.Float64}
    return pl.DataFrame(rows, schema={c: schema[c] for c in columns}, orient="row")


@dataclass
class MinutesEval:
    """Direct minutes-model evaluation (independent of the downstream stat
    distributions), so the game-context A/B (``MinutesModelConfig.
    use_game_context``) can be measured where CLAUDE.md's motivation
    actually expects it to show up.

    ``dnp_log_loss``: binary log loss of predicted ``P(plays)`` against
    actual played/DNP, over every row (``dnp_n``).
    ``minutes_mae`` / ``minutes_bias``: mean absolute error / mean
    (predicted - actual) of predicted ``E[minutes | plays]`` (the hurdle's
    ``mu``), scored only over rows where the player actually played
    (``minutes_n``) -- a DNP row has no "minutes given played" outcome to
    score against, so it is scored on the DNP head only, never here.
    """

    n: int
    dnp_log_loss: float
    dnp_n: int
    minutes_mae: float
    minutes_bias: float
    minutes_n: int
    used_game_context: bool


def evaluate_minutes_model(
    dists: list[MinutesHurdleDist],
    actual_minutes: np.ndarray,
    used_game_context: bool = True,
) -> MinutesEval:
    """Score ``dists`` (one ``MinutesHurdleDist`` per row, same order as
    ``actual_minutes``) against the true box-score outcome.

    A row with ``actual_minutes > 0`` is "played"; its ``minutes`` value
    is scored on the minutes-MAE/bias head. A row with ``actual_minutes
    == 0`` (DNP) is scored only on the DNP log loss head -- see class
    docstring.
    """
    actual = np.asarray(actual_minutes, dtype=float)
    n = len(actual)
    if n == 0:
        return MinutesEval(
            n=0,
            dnp_log_loss=float("nan"),
            dnp_n=0,
            minutes_mae=float("nan"),
            minutes_bias=float("nan"),
            minutes_n=0,
            used_game_context=used_game_context,
        )

    played = actual > 0.0
    played_indicator = played.astype(float)
    p_play_pred = np.array([d.p_play for d in dists], dtype=float)
    dnp_ll = float(log_loss(played_indicator, p_play_pred))

    mu_pred = np.array([d.mu for d in dists], dtype=float)
    minutes_n = int(played.sum())
    if minutes_n > 0:
        err = mu_pred[played] - actual[played]
        minutes_mae = float(np.mean(np.abs(err)))
        minutes_bias = float(np.mean(err))
    else:
        minutes_mae = float("nan")
        minutes_bias = float("nan")

    return MinutesEval(
        n=n,
        dnp_log_loss=dnp_ll,
        dnp_n=n,
        minutes_mae=minutes_mae,
        minutes_bias=minutes_bias,
        minutes_n=minutes_n,
        used_game_context=used_game_context,
    )
