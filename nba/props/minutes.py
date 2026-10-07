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
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import polars as pl

from nba.coldstart.shrinkage import shrink_rate
from nba.eval.metrics import log_loss
from nba.features.game_context import build_national_tv_features, build_standings_features
from nba.features.team_features import build_team_game_features
from nba.props.config import MinutesModelConfig
from nba.props.distributions import MinutesHurdleDist

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


def predict_minutes(
    features: pl.DataFrame,
    config: MinutesModelConfig | None = None,
    k_multiplier: np.ndarray | None = None,
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
    if cfg.use_game_context and all(c in features.columns for c in context_cols):
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
