"""Player-level, strictly as-of possession rates for rung-3 player attribution.

CLAUDE.md architecture ladder, rung 3 ("Full-game distributions, player
lines") -- this module supplies the per-player as-of rates
``nba.sim.player_attribution`` consumes to turn the existing team-level
possession sim into per-player points: usage/shot-share, shot-zone mix,
per-zone make probability, and free-throw-trip rate / FT%. Scope is
**points only** this milestone (CLAUDE.md: "shooter -> shot zone ->
make/miss -> free throws"); rebounds/assists are a later milestone.

No-leakage: every ``*_prior`` column is a cumulative window aggregate over
strictly earlier ``(game_date, game_id)`` rows for that player (or, for the
position-level prior, that position), ``ROWS BETWEEN UNBOUNDED PRECEDING
AND 1 PRECEDING`` -- the identical discipline as
``nba.features.team_features`` / ``nba.features.possession_features``. The
current game's own shots/minutes never contribute to its own feature row.
See ``tests/ml/test_player_possession_features_no_leakage.py`` for the
planted-future-game proof.

DATA REALITY CHECK (both documented limitations, not bugs):

1. ``possessions.off_players``/``def_players`` (the actual 5-man lineup on
   court) is **always NULL** in the real ingested DB today (the lineup
   parser is a separate, not-yet-built milestone) -- confirmed by directly
   querying ``nba.duckdb`` read-only. "Fraction of team shot attempts while
   on court" therefore cannot be computed possession-by-possession; this
   module uses the documented proxy CLAUDE.md allows elsewhere for the same
   reason (see ``nba.features.player_features`` module docstring): a
   player's shot-share denominator is their *team's* total field-goal
   attempts in every game that player actually appeared in (per
   ``player_game_stats``), not a true on-court-only possession count.
   Replace with a true on-court denominator once the lineup parser lands.
2. ``players_static.position`` is **empty** in the real ingested DB today
   (that ingest hasn't run yet). The position-level prior below degrades
   gracefully to a single ``"UNK"`` bucket -- i.e. a league-wide prior --
   when no position data exists, which is mathematically identical to a
   true position prior with one position. This is intentional graceful
   degradation (same spirit as ``nba.coldstart.shrinkage.tune_pseudo_count``
   degrading to a documented default with too little data), not a silent
   wrong answer.

Shrinkage: every observed rate is blended toward its prior via
``nba.coldstart.shrinkage.shrink_rate`` (method 1, "ship first") -- reused
unchanged, not re-implemented. Rates are blended in two stages: a player's
own as-of observed rate is shrunk toward the position-level as-of prior,
pseudo-count-weighted by the SAME method; if a position has zero prior
observations too (a brand-new position bucket, or no position data), the
position "prior" itself is just the hardcoded league-wide constant below,
so the player's rate still shrinks to something reasonable rather than
being NULL or a divide-by-zero.

League-wide empirical constants below (documented, like
``nba.sim.possession_model.BASE_OUTCOME_PROBS`` / ``nba.features.
possession_features.LEAGUE_AVG_PPP_DEFAULT``), computed once from the full
historical ``possessions``/``player_game_stats`` tables (2022-23 through
2025-26 seasons, ~797k shot attempts with a non-null ``shooter_id``):

    zone_mix:    rim 0.3422, mid 0.2566, above3 0.4012
    zone_fg_pct: rim 0.7212, mid 0.5172, above3 0.4247
    league FTA per FGA (ft_trip_rate prior): 0.2989
    league FT%:  0.7822

NOTE on "3-point share within above3": this schema's ``shot_zone`` has only
three buckets (``rim``/``mid``/``above3``) with no corner-3/above-break-3
split, and every ``above3`` attempt is already a 3-point attempt (``mid``/
``rim`` are exclusively 2-point zones in this data -- verified: ``FGM3``
never co-occurs with ``shot_zone`` in (``rim``, ``mid``)). "3-point share
within above3" therefore collapses to 1.0 identically for every player and
carries no information; ``zone_mix_above3`` (how often a player's shots ARE
an ``above3`` attempt in the first place) is the feature that actually
varies by player and is what this module reports instead.
"""

from __future__ import annotations

import duckdb
import numpy as np
import polars as pl

from nba.coldstart.shrinkage import shrink_rate

#: League-wide empirical zone-mix / zone-efficiency constants -- see module
#: docstring for provenance. Used as the last-resort fallback prior when a
#: position bucket itself has zero as-of observations (e.g. the very first
#: player ever seen at a brand-new position, or no position data at all).
LEAGUE_ZONE_MIX_DEFAULT: dict[str, float] = {"rim": 0.3422, "mid": 0.2566, "above3": 0.4012}
LEAGUE_ZONE_FG_PCT_DEFAULT: dict[str, float] = {"rim": 0.7212, "mid": 0.5172, "above3": 0.4247}
LEAGUE_SHOT_SHARE_DEFAULT = 0.2  # ~1/5 of team FGA, a league-average rotation player
LEAGUE_FT_TRIP_RATE_DEFAULT = 0.2989  # FTA per FGA
LEAGUE_FT_PCT_DEFAULT = 0.7822

#: Pseudo-counts (empirical-Bayes shrinkage, method 1). Shot-share and
#: ft-trip-rate mirror ``nba.coldstart.config.DEFAULT_PSEUDO_COUNTS``'s
#: "usage"/"foul_draw" values (150) and ft_pct mirrors its "ft_pct" (100);
#: duplicated here (rather than imported) because this module only needs
#: the numeric defaults, not the shared mutable config dataclass, and
#: zone-mix/zone-fg-pct have no existing entry in that dict to reuse.
SHOT_SHARE_PSEUDO_COUNT = 150.0
ZONE_MIX_PSEUDO_COUNT = 150.0
ZONE_FG_PCT_PSEUDO_COUNT = 100.0
FT_TRIP_RATE_PSEUDO_COUNT = 150.0
FT_PCT_PSEUDO_COUNT = 100.0

ZONES: list[str] = ["rim", "mid", "above3"]

#: Columns this module produces, one row per (game_id, player_id) -- see
#: :func:`build_player_shot_rates`.
PLAYER_SHOT_RATE_COLUMNS: list[str] = [
    "shot_share_prior",
    "zone_mix_rim_prior",
    "zone_mix_mid_prior",
    "zone_mix_above3_prior",
    "zone_fg_pct_rim_prior",
    "zone_fg_pct_mid_prior",
    "zone_fg_pct_above3_prior",
    "ft_trip_rate_prior",
    "ft_pct_prior",
    "n_fga_prior",
]

_WINDOW = "ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING"

_PLAYER_SHOT_RATES_SQL = f"""
WITH shots AS (
    SELECT game_id, off_team AS team_id, shooter_id AS player_id, shot_zone, outcome
    FROM possessions
    WHERE shooter_id IS NOT NULL
),
shot_agg AS (
    SELECT
        game_id, team_id, player_id,
        COUNT(*) AS n_fga,
        SUM(CASE WHEN shot_zone = 'rim' THEN 1 ELSE 0 END) AS n_rim_fga,
        SUM(CASE WHEN shot_zone = 'rim' AND outcome = 'FGM2' THEN 1 ELSE 0 END) AS n_rim_made,
        SUM(CASE WHEN shot_zone = 'mid' THEN 1 ELSE 0 END) AS n_mid_fga,
        SUM(CASE WHEN shot_zone = 'mid' AND outcome = 'FGM2' THEN 1 ELSE 0 END) AS n_mid_made,
        SUM(CASE WHEN shot_zone = 'above3' THEN 1 ELSE 0 END) AS n_above3_fga,
        SUM(CASE WHEN shot_zone = 'above3' AND outcome = 'FGM3' THEN 1 ELSE 0 END) AS n_above3_made
    FROM shots
    GROUP BY 1, 2, 3
),
team_fga AS (
    -- Team's total FGA for the game -- the shot-share denominator proxy
    -- (see module docstring point 1: no real on-court lineup data yet).
    SELECT game_id, off_team AS team_id, COUNT(*) AS team_n_fga
    FROM possessions
    WHERE outcome IN ('FGM2', 'FGM3', 'FGA_miss')
    GROUP BY 1, 2
),
per_game AS (
    SELECT
        pgs.game_id,
        pgs.player_id,
        pgs.team_id,
        g.game_date,
        g.season,
        COALESCE(ps.position, 'UNK') AS position,
        COALESCE(sa.n_fga, 0) AS n_fga,
        COALESCE(sa.n_rim_fga, 0) AS n_rim_fga,
        COALESCE(sa.n_rim_made, 0) AS n_rim_made,
        COALESCE(sa.n_mid_fga, 0) AS n_mid_fga,
        COALESCE(sa.n_mid_made, 0) AS n_mid_made,
        COALESCE(sa.n_above3_fga, 0) AS n_above3_fga,
        COALESCE(sa.n_above3_made, 0) AS n_above3_made,
        COALESCE(tf.team_n_fga, 0) AS team_n_fga,
        COALESCE(pgs.fta, 0) AS fta,
        COALESCE(pgs.ftm, 0) AS ftm
    FROM player_game_stats pgs
    JOIN games g USING (game_id)
    LEFT JOIN players_static ps ON ps.player_id = pgs.player_id
    LEFT JOIN shot_agg sa ON sa.game_id = pgs.game_id AND sa.player_id = pgs.player_id
    LEFT JOIN team_fga tf ON tf.game_id = pgs.game_id AND tf.team_id = pgs.team_id
),
player_prior AS (
    SELECT
        game_id, player_id, team_id, game_date, season, position,
        SUM(n_fga) OVER (PARTITION BY player_id ORDER BY game_date, game_id {_WINDOW})
            AS n_fga_prior,
        SUM(n_rim_fga) OVER (PARTITION BY player_id ORDER BY game_date, game_id {_WINDOW})
            AS n_rim_fga_prior,
        SUM(n_rim_made) OVER (PARTITION BY player_id ORDER BY game_date, game_id {_WINDOW})
            AS n_rim_made_prior,
        SUM(n_mid_fga) OVER (PARTITION BY player_id ORDER BY game_date, game_id {_WINDOW})
            AS n_mid_fga_prior,
        SUM(n_mid_made) OVER (PARTITION BY player_id ORDER BY game_date, game_id {_WINDOW})
            AS n_mid_made_prior,
        SUM(n_above3_fga) OVER (PARTITION BY player_id ORDER BY game_date, game_id {_WINDOW})
            AS n_above3_fga_prior,
        SUM(n_above3_made) OVER (PARTITION BY player_id ORDER BY game_date, game_id {_WINDOW})
            AS n_above3_made_prior,
        SUM(team_n_fga) OVER (PARTITION BY player_id ORDER BY game_date, game_id {_WINDOW})
            AS team_n_fga_prior,
        SUM(fta) OVER (PARTITION BY player_id ORDER BY game_date, game_id {_WINDOW})
            AS fta_prior,
        SUM(ftm) OVER (PARTITION BY player_id ORDER BY game_date, game_id {_WINDOW})
            AS ftm_prior
    FROM per_game
),
position_prior AS (
    -- Pooled as-of prior across every player sharing a position (degrades
    -- to one league-wide "UNK" bucket when no position data exists -- see
    -- module docstring point 2). Ordered by (game_date, game_id, player_id)
    -- for a deterministic total order within the position partition, same
    -- convention as nba.features.possession_features's league prior.
    SELECT
        game_id, player_id, position,
        SUM(n_fga) OVER (PARTITION BY position ORDER BY game_date, game_id, player_id {_WINDOW})
            AS pos_n_fga_prior,
        SUM(n_rim_fga) OVER (
            PARTITION BY position ORDER BY game_date, game_id, player_id {_WINDOW}
        ) AS pos_n_rim_fga_prior,
        SUM(n_rim_made) OVER (
            PARTITION BY position ORDER BY game_date, game_id, player_id {_WINDOW}
        ) AS pos_n_rim_made_prior,
        SUM(n_mid_fga) OVER (
            PARTITION BY position ORDER BY game_date, game_id, player_id {_WINDOW}
        ) AS pos_n_mid_fga_prior,
        SUM(n_mid_made) OVER (
            PARTITION BY position ORDER BY game_date, game_id, player_id {_WINDOW}
        ) AS pos_n_mid_made_prior,
        SUM(n_above3_fga) OVER (
            PARTITION BY position ORDER BY game_date, game_id, player_id {_WINDOW}
        ) AS pos_n_above3_fga_prior,
        SUM(n_above3_made) OVER (
            PARTITION BY position ORDER BY game_date, game_id, player_id {_WINDOW}
        ) AS pos_n_above3_made_prior,
        SUM(team_n_fga) OVER (
            PARTITION BY position ORDER BY game_date, game_id, player_id {_WINDOW}
        ) AS pos_team_n_fga_prior,
        SUM(fta) OVER (PARTITION BY position ORDER BY game_date, game_id, player_id {_WINDOW})
            AS pos_fta_prior,
        SUM(ftm) OVER (PARTITION BY position ORDER BY game_date, game_id, player_id {_WINDOW})
            AS pos_ftm_prior
    FROM per_game
)
SELECT
    pp.game_id, pp.player_id, pp.team_id, pp.game_date, pp.season,
    pp.n_fga_prior, pp.n_rim_fga_prior, pp.n_rim_made_prior,
    pp.n_mid_fga_prior, pp.n_mid_made_prior,
    pp.n_above3_fga_prior, pp.n_above3_made_prior,
    pp.team_n_fga_prior, pp.fta_prior, pp.ftm_prior,
    pos.pos_n_fga_prior, pos.pos_n_rim_fga_prior, pos.pos_n_rim_made_prior,
    pos.pos_n_mid_fga_prior, pos.pos_n_mid_made_prior,
    pos.pos_n_above3_fga_prior, pos.pos_n_above3_made_prior,
    pos.pos_team_n_fga_prior, pos.pos_fta_prior, pos.pos_ftm_prior
FROM player_prior pp
JOIN position_prior pos USING (game_id, player_id)
ORDER BY pp.game_date, pp.game_id, pp.player_id
"""


def _safe_ratio(num: pl.Expr, den: pl.Expr, default: float) -> pl.Expr:
    return pl.when(den > 0).then(num / den).otherwise(pl.lit(default))


def build_player_shot_rates(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """One row per (game, player): strictly as-of, shrinkage-blended shot rates.

    See module docstring for the exact no-leakage window discipline, the
    two documented data-reality proxies (no on-court lineup data, no
    position data yet), and the shrinkage design (player rate -> position
    prior -> hardcoded league constant, via
    ``nba.coldstart.shrinkage.shrink_rate`` at each stage).
    """
    con.execute(_PLAYER_SHOT_RATES_SQL)
    columns = [d[0] for d in con.description]
    rows = con.fetchall()
    int_cols = {
        "n_fga_prior",
        "n_rim_fga_prior",
        "n_rim_made_prior",
        "n_mid_fga_prior",
        "n_mid_made_prior",
        "n_above3_fga_prior",
        "n_above3_made_prior",
        "team_n_fga_prior",
        "fta_prior",
        "ftm_prior",
        "pos_n_fga_prior",
        "pos_n_rim_fga_prior",
        "pos_n_rim_made_prior",
        "pos_n_mid_fga_prior",
        "pos_n_mid_made_prior",
        "pos_n_above3_fga_prior",
        "pos_n_above3_made_prior",
        "pos_team_n_fga_prior",
        "pos_fta_prior",
        "pos_ftm_prior",
    }
    schema = {
        "game_id": pl.Utf8,
        "player_id": pl.Int64,
        "team_id": pl.Int64,
        "game_date": pl.Date,
        "season": pl.Int64,
    }
    for c in int_cols:
        schema[c] = pl.Float64
    df = pl.DataFrame(rows, schema={c: schema[c] for c in columns}, orient="row")
    if df.height == 0:
        return df.with_columns(
            [pl.lit(None, dtype=pl.Float64).alias(c) for c in PLAYER_SHOT_RATE_COLUMNS]
        )
    df = df.with_columns([pl.col(c).fill_null(0.0) for c in int_cols])

    # Stage 1: position-level prior rates (fall back to the hardcoded
    # league constant when the position bucket itself has 0 observations).
    pos_shot_share = _safe_ratio(
        pl.col("pos_n_fga_prior"), pl.col("pos_team_n_fga_prior"), LEAGUE_SHOT_SHARE_DEFAULT
    )
    pos_zone_mix = {
        z: _safe_ratio(
            pl.col(f"pos_n_{z}_fga_prior"), pl.col("pos_n_fga_prior"), LEAGUE_ZONE_MIX_DEFAULT[z]
        )
        for z in ZONES
    }
    pos_zone_fg_pct = {
        z: _safe_ratio(
            pl.col(f"pos_n_{z}_made_prior"),
            pl.col(f"pos_n_{z}_fga_prior"),
            LEAGUE_ZONE_FG_PCT_DEFAULT[z],
        )
        for z in ZONES
    }
    pos_ft_trip_rate = _safe_ratio(
        pl.col("pos_fta_prior"), pl.col("pos_n_fga_prior"), LEAGUE_FT_TRIP_RATE_DEFAULT
    )
    pos_ft_pct = _safe_ratio(
        pl.col("pos_ftm_prior"), pl.col("pos_fta_prior"), LEAGUE_FT_PCT_DEFAULT
    )

    df = df.with_columns(
        [
            pos_shot_share.alias("_pos_shot_share"),
            pos_ft_trip_rate.alias("_pos_ft_trip_rate"),
            pos_ft_pct.alias("_pos_ft_pct"),
            *[pos_zone_mix[z].alias(f"_pos_zone_mix_{z}") for z in ZONES],
            *[pos_zone_fg_pct[z].alias(f"_pos_zone_fg_pct_{z}") for z in ZONES],
        ]
    )

    # Stage 2: player's own observed rate, shrunk toward the position prior.
    shot_share_obs = _safe_ratio(pl.col("n_fga_prior"), pl.col("team_n_fga_prior"), 0.0)
    ft_trip_rate_obs = _safe_ratio(pl.col("fta_prior"), pl.col("n_fga_prior"), 0.0)
    ft_pct_obs = _safe_ratio(pl.col("ftm_prior"), pl.col("fta_prior"), 0.0)
    zone_mix_obs = {
        z: _safe_ratio(pl.col(f"n_{z}_fga_prior"), pl.col("n_fga_prior"), 0.0) for z in ZONES
    }
    zone_fg_pct_obs = {
        z: _safe_ratio(pl.col(f"n_{z}_made_prior"), pl.col(f"n_{z}_fga_prior"), 0.0) for z in ZONES
    }

    df = df.with_columns(
        [
            shot_share_obs.alias("_shot_share_obs"),
            ft_trip_rate_obs.alias("_ft_trip_rate_obs"),
            ft_pct_obs.alias("_ft_pct_obs"),
            *[zone_mix_obs[z].alias(f"_zone_mix_obs_{z}") for z in ZONES],
            *[zone_fg_pct_obs[z].alias(f"_zone_fg_pct_obs_{z}") for z in ZONES],
        ]
    )

    team_n_fga_prior = df.get_column("team_n_fga_prior").to_numpy()
    n_fga_prior = df.get_column("n_fga_prior").to_numpy()
    fta_prior = df.get_column("fta_prior").to_numpy()

    shot_share_shrunk = shrink_rate(
        df.get_column("_shot_share_obs").to_numpy(),
        team_n_fga_prior,
        df.get_column("_pos_shot_share").to_numpy(),
        SHOT_SHARE_PSEUDO_COUNT,
    )
    ft_trip_rate_shrunk = shrink_rate(
        df.get_column("_ft_trip_rate_obs").to_numpy(),
        n_fga_prior,
        df.get_column("_pos_ft_trip_rate").to_numpy(),
        FT_TRIP_RATE_PSEUDO_COUNT,
    )
    ft_pct_shrunk = shrink_rate(
        df.get_column("_ft_pct_obs").to_numpy(),
        fta_prior,
        df.get_column("_pos_ft_pct").to_numpy(),
        FT_PCT_PSEUDO_COUNT,
    )

    zone_mix_shrunk = {}
    zone_fg_pct_shrunk = {}
    for z in ZONES:
        zone_mix_shrunk[z] = shrink_rate(
            df.get_column(f"_zone_mix_obs_{z}").to_numpy(),
            n_fga_prior,
            df.get_column(f"_pos_zone_mix_{z}").to_numpy(),
            ZONE_MIX_PSEUDO_COUNT,
        )
        zone_fga_prior_z = df.get_column(f"n_{z}_fga_prior").to_numpy()
        zone_fg_pct_shrunk[z] = shrink_rate(
            df.get_column(f"_zone_fg_pct_obs_{z}").to_numpy(),
            zone_fga_prior_z,
            df.get_column(f"_pos_zone_fg_pct_{z}").to_numpy(),
            ZONE_FG_PCT_PSEUDO_COUNT,
        )

    # Normalize the shrunk zone mix back to sum=1 (shrinkage is applied
    # independently per zone toward each zone's own prior, so the three
    # shrunk shares don't automatically sum to exactly 1 -- renormalize so
    # callers can treat this as a true categorical distribution).
    mix_rim, mix_mid, mix_above3 = (
        zone_mix_shrunk["rim"],
        zone_mix_shrunk["mid"],
        zone_mix_shrunk["above3"],
    )
    mix_sum = np.clip(mix_rim + mix_mid + mix_above3, 1e-9, None)
    mix_rim, mix_mid, mix_above3 = mix_rim / mix_sum, mix_mid / mix_sum, mix_above3 / mix_sum

    out = df.select(["game_id", "player_id", "team_id", "game_date", "season"]).with_columns(
        [
            pl.Series("shot_share_prior", np.clip(shot_share_shrunk, 0.0, 1.0)),
            pl.Series("zone_mix_rim_prior", mix_rim),
            pl.Series("zone_mix_mid_prior", mix_mid),
            pl.Series("zone_mix_above3_prior", mix_above3),
            pl.Series("zone_fg_pct_rim_prior", np.clip(zone_fg_pct_shrunk["rim"], 0.0, 1.0)),
            pl.Series("zone_fg_pct_mid_prior", np.clip(zone_fg_pct_shrunk["mid"], 0.0, 1.0)),
            pl.Series("zone_fg_pct_above3_prior", np.clip(zone_fg_pct_shrunk["above3"], 0.0, 1.0)),
            pl.Series("ft_trip_rate_prior", np.clip(ft_trip_rate_shrunk, 0.0, None)),
            pl.Series("ft_pct_prior", np.clip(ft_pct_shrunk, 0.0, 1.0)),
            pl.Series("n_fga_prior", n_fga_prior),
        ]
    )
    return out
