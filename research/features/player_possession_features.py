"""Player-level, strictly as-of possession rates for rung-3 player attribution.

CLAUDE.md architecture ladder, rung 3 ("Full-game distributions, player
lines") -- this module supplies the per-player as-of rates
``research.sim.player_attribution`` consumes to turn the existing team-level
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

SHOT-SHARE DENOMINATOR (``use_oncourt_usage``, A/B-decided, defaults OFF):

The lineup parser has since landed -- ``possessions.off_players`` is now
populated for ~100% of possessions -- so a TRUE on-court shot-share
denominator (team FGA taken while the player was on the floor) is now
computable and is wired in behind ``use_oncourt_usage``. It was A/B'd head-
to-head against the original whole-game proxy (player FGA / team's total FGA
over games the player appeared in) on the real sim points eval (15,350
player-games): the proxy TIES season-average (CRPS delta +0.008, CI
includes 0) while on-court usage is markedly WORSE (CRPS delta +0.398, CI
excludes 0). The proxy already captures usage well; the cleaner
usage x availability decomposition over-concentrates the shot distribution
once renormalized across the on-court five. So the flag defaults OFF and the
proxy is the shipped path; the on-court machinery is retained for the
lineup-mix / usage-redistribution experiments that need per-possession
on-court data for a different reason. DOCUMENTED, not a bug.

DATA REALITY CHECK (documented limitation, not a bug):

1. ``players_static.position`` is **empty** in the real ingested DB today
   (that ingest hasn't run yet). The position-level prior below degrades
   gracefully to a single ``"UNK"`` bucket -- i.e. a league-wide prior --
   when no position data exists, which is mathematically identical to a
   true position prior with one position. This is intentional graceful
   degradation (same spirit as ``nba.features.shrinkage.tune_pseudo_count``
   degrading to a documented default with too little data), not a silent
   wrong answer.

Shrinkage: every observed rate is blended toward its prior via
``nba.features.shrinkage.shrink_rate`` (method 1, "ship first") -- reused
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

from nba.features.shrinkage import shrink_rate
from research.features.time_decay import TimeDecayConfig

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

#: ``player_game_stats`` row counts as "played" iff minutes is NOT NULL and > 0
#: (DNP rows are stored with minutes NULL and all-zero stats; see
#: docs/DNP_AUDIT_2026-10-08.md). Single definition shared by the view below,
#: the rate builders, the SB classifier and the fair season-average baseline.
PLAYED_PREDICATE = "minutes IS NOT NULL AND minutes > 0"

PLAYED_VIEW_NAME = "player_game_stats_played"

_PLAYED_VIEW_SQL = (
    f"CREATE OR REPLACE TEMP VIEW {PLAYED_VIEW_NAME} AS "
    f"SELECT * FROM player_game_stats WHERE {PLAYED_PREDICATE}"
)


def ensure_played_view(con: duckdb.DuckDBPyConnection) -> None:
    """Create the ``player_game_stats_played`` view idempotently.

    Created as a TEMP view so it also works on ``read_only=True`` connections
    (a persistent twin is declared in ``nba/db/schema.sql`` for writable DBs;
    the temp one shadows it harmlessly and is identical by construction).
    """
    con.execute(_PLAYED_VIEW_SQL)


def _gate(expr: str, played_only: bool, played_sql: str = "pgs.minutes > 0") -> str:
    """``expr`` unchanged when ``played_only`` is off (byte-identical SQL);
    otherwise zero on DNP rows so they add nothing to numerators OR
    denominators of any as-of window sum."""
    if not played_only:
        return expr
    return f"CASE WHEN {played_sql} THEN {expr} ELSE 0 END"


def _shot_rates_sql(played_only: bool = False) -> str:

    def g(expr: str) -> str:
        return _gate(expr, played_only)

    return f"""
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
    -- Team's total FGA for the game -- the whole-game shot-share denominator
    -- proxy (see module docstring "SHOT-SHARE DENOMINATOR"). Used when
    -- ``use_oncourt_usage`` is off (the A/B-selected default).
    SELECT game_id, off_team AS team_id, COUNT(*) AS team_n_fga
    FROM possessions
    WHERE outcome IN ('FGM2', 'FGM3', 'FGA_miss')
    GROUP BY 1, 2
),
oncourt_fga AS (
    -- Team FGA taken while this player was ON THE FLOOR on offense -- the
    -- TRUE on-court usage denominator, now that possessions.off_players is
    -- populated (lineup parser landed). Unnests the offensive 5-man lineup
    -- per FG-attempt possession and counts attempts per on-court player, so
    -- shot_share becomes "share of team shots while I'm on court" rather
    -- than "share of the whole game's team shots". Used when
    -- ``use_oncourt_usage`` is on. Null-lineup possessions are skipped.
    SELECT game_id, off_team AS team_id, oc.player_id AS player_id,
           COUNT(*) AS oncourt_team_fga
    FROM possessions,
         UNNEST(off_players::INTEGER[]) AS oc(player_id)
    WHERE outcome IN ('FGM2', 'FGM3', 'FGA_miss')
      AND off_players IS NOT NULL
    GROUP BY 1, 2, 3
),
per_game AS (
    SELECT
        pgs.game_id,
        pgs.player_id,
        pgs.team_id,
        g.game_date,
        g.season,
        COALESCE(ps.position, 'UNK') AS position,
        {g("COALESCE(sa.n_fga, 0)")} AS n_fga,
        {g("COALESCE(sa.n_rim_fga, 0)")} AS n_rim_fga,
        {g("COALESCE(sa.n_rim_made, 0)")} AS n_rim_made,
        {g("COALESCE(sa.n_mid_fga, 0)")} AS n_mid_fga,
        {g("COALESCE(sa.n_mid_made, 0)")} AS n_mid_made,
        {g("COALESCE(sa.n_above3_fga, 0)")} AS n_above3_fga,
        {g("COALESCE(sa.n_above3_made, 0)")} AS n_above3_made,
        {g("COALESCE(tf.team_n_fga, 0)")} AS team_n_fga,
        {g("COALESCE(oc.oncourt_team_fga, 0)")} AS oncourt_team_fga,
        {g("COALESCE(pgs.fta, 0)")} AS fta,
        {g("COALESCE(pgs.ftm, 0)")} AS ftm
    FROM player_game_stats pgs
    JOIN games g USING (game_id)
    LEFT JOIN players_static ps ON ps.player_id = pgs.player_id
    LEFT JOIN shot_agg sa ON sa.game_id = pgs.game_id AND sa.player_id = pgs.player_id
    LEFT JOIN team_fga tf ON tf.game_id = pgs.game_id AND tf.team_id = pgs.team_id
    LEFT JOIN oncourt_fga oc ON oc.game_id = pgs.game_id AND oc.player_id = pgs.player_id
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
        SUM(oncourt_team_fga) OVER (PARTITION BY player_id ORDER BY game_date, game_id {_WINDOW})
            AS oncourt_team_fga_prior,
        SUM(fta) OVER (PARTITION BY player_id ORDER BY game_date, game_id {_WINDOW})
            AS fta_prior,
        SUM(ftm) OVER (PARTITION BY player_id ORDER BY game_date, game_id {_WINDOW})
            AS ftm_prior
    FROM per_game
),
position_prior AS (
    -- Pooled as-of prior across every player sharing a position (degrades
    -- to one league-wide "UNK" bucket when no position data exists -- see
    -- module docstring point 1). Ordered by (game_date, game_id, player_id)
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
        SUM(oncourt_team_fga) OVER (
            PARTITION BY position ORDER BY game_date, game_id, player_id {_WINDOW}
        ) AS pos_oncourt_team_fga_prior,
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
    pp.team_n_fga_prior, pp.oncourt_team_fga_prior, pp.fta_prior, pp.ftm_prior,
    pos.pos_n_fga_prior, pos.pos_n_rim_fga_prior, pos.pos_n_rim_made_prior,
    pos.pos_n_mid_fga_prior, pos.pos_n_mid_made_prior,
    pos.pos_n_above3_fga_prior, pos.pos_n_above3_made_prior,
    pos.pos_team_n_fga_prior, pos.pos_oncourt_team_fga_prior, pos.pos_fta_prior, pos.pos_ftm_prior
FROM player_prior pp
JOIN position_prior pos USING (game_id, player_id)
ORDER BY pp.game_date, pp.game_id, pp.player_id
"""


_PLAYER_SHOT_RATES_SQL = _shot_rates_sql(False)

_PLAYER_COUNT_COLS = [
    "n_fga",
    "n_rim_fga",
    "n_rim_made",
    "n_mid_fga",
    "n_mid_made",
    "n_above3_fga",
    "n_above3_made",
    "team_n_fga",
    "oncourt_team_fga",
    "fta",
    "ftm",
]


def _decayed_player_prior_cte(cfg: TimeDecayConfig) -> str:
    """SQL for a ``player_prior`` CTE (same output columns as the default one)
    with season-decayed pooling.

    Per count column: ``current-season cumulative strictly before this game``
    (partition ``(player_id, season)``, the same ``1 PRECEDING`` window) plus
    ``sum_{s < season} w(s) * full_season_total(s)`` where
    ``w = 0.5 ** ((gap - 1) / half_life) * (1 - carryover_decay_weight) * age``
    and ``age`` is ``carryover.age_curve_multiplier`` evaluated at the player's
    age on the first league game date of the current season (NULL birth_date
    -> 1.0). Earlier-season totals are complete, and seasons increase with
    calendar time, so no current-or-future game ever contributes.
    """
    hl = max(float(cfg.half_life_seasons), 1e-6)
    keep = 1.0 - float(cfg.carryover_decay_weight)
    age_yrs = "date_diff('day', ps.birth_date, ss.start_date) / 365.25"
    z = f"(({age_yrs} - {float(cfg.peak_age)!r}) / {float(cfg.age_curve_width)!r})"
    age_mult = f"COALESCE(GREATEST(0.5, LEAST(1.0, 1.0 - 0.5 * {z} * {z})), 1.0)"
    tot_cols = ",\n        ".join(f"SUM({c}) AS t_{c}" for c in _PLAYER_COUNT_COLS)
    dec_cols = ",\n        ".join(f"SUM(w.wt * st.t_{c}) AS d_{c}" for c in _PLAYER_COUNT_COLS)
    cur_cols = ",\n        ".join(
        f"SUM({c}) OVER (PARTITION BY player_id, season ORDER BY game_date, game_id {_WINDOW}) "
        f"AS c_{c}"
        for c in _PLAYER_COUNT_COLS
    )
    out_cols = ",\n        ".join(
        f"COALESCE(cu.c_{c}, 0) + COALESCE(d.d_{c}, 0) AS {c}_prior" for c in _PLAYER_COUNT_COLS
    )
    return f"""player_prior AS (
    WITH season_start AS (
        SELECT season, MIN(game_date) AS start_date FROM games GROUP BY 1
    ),
    season_tot AS (
        SELECT player_id, season,
        {tot_cols}
        FROM per_game GROUP BY 1, 2
    ),
    cur_cum AS (
        SELECT game_id, player_id, team_id, game_date, season, position,
        {cur_cols}
        FROM per_game
    ),
    decayed AS (
        SELECT cp.player_id, cp.season,
        {dec_cols}
        FROM (SELECT DISTINCT player_id, season FROM per_game) cp
        JOIN season_tot st ON st.player_id = cp.player_id AND st.season < cp.season
        JOIN season_start ss ON ss.season = cp.season
        LEFT JOIN players_static ps ON ps.player_id = cp.player_id
        CROSS JOIN LATERAL (
            SELECT POWER(0.5, (cp.season - st.season - 1) / {hl!r}) * {keep!r} * {age_mult} AS wt
        ) w
        GROUP BY 1, 2
    )
    SELECT cu.game_id, cu.player_id, cu.team_id, cu.game_date, cu.season, cu.position,
        {out_cols}
    FROM cur_cum cu
    LEFT JOIN decayed d ON d.player_id = cu.player_id AND d.season = cu.season
),
"""


def _time_decay_sql(cfg: TimeDecayConfig, played_only: bool = False) -> str:
    """Default SQL with its ``player_prior`` CTE swapped for the decayed one."""
    base = _shot_rates_sql(played_only)
    start = base.index("player_prior AS (")
    end = base.index("position_prior AS (")
    return base[:start] + _decayed_player_prior_cte(cfg) + base[end:]


def _safe_ratio(num: pl.Expr, den: pl.Expr, default: float) -> pl.Expr:
    return pl.when(den > 0).then(num / den).otherwise(pl.lit(default))


def build_player_shot_rates(
    con: duckdb.DuckDBPyConnection,
    use_oncourt_usage: bool = False,
    use_time_decay: bool = False,
    time_decay_config: TimeDecayConfig | None = None,
    played_only: bool = False,
) -> pl.DataFrame:
    """One row per (game, player): strictly as-of, shrinkage-blended shot rates.

    See module docstring for the exact no-leakage window discipline, the
    position-data proxy, and the shrinkage design (player rate -> position
    prior -> hardcoded league constant, via
    ``nba.features.shrinkage.shrink_rate`` at each stage).

    ``use_oncourt_usage``: when True, the shot-share denominator is the
    team's FGA *while the player was on the floor* (from the now-populated
    ``possessions.off_players`` lineups) rather than the whole-game team FGA
    proxy. This cleanly separates on-court usage intensity from availability
    (the attribution step already multiplies by projected-minutes
    availability), the usage x minutes decomposition CLAUDE.md calls for.
    Defaults to False (the original proxy) so the choice is made by the A/B
    backtest, not by assumption.

    ``use_time_decay`` (cold-start method 4, defaults OFF so default output
    is byte-identical): replaces the equal-weight pooling of ALL prior
    seasons in the player's own counts with ``current-season-to-date counts
    + sum over strictly earlier seasons of (decay-weighted full-season
    totals)``; see :func:`_decayed_player_prior_cte` for the weights.
    ``time_decay_config`` supplies the knobs (``TimeDecayConfig()`` defaults
    if None). The position-level prior is deliberately NOT decayed.

    ``played_only`` (defaults OFF so historical results reproduce exactly):
    games where the player did not play (``minutes`` NULL/0 -- DNP rows carry
    all-zero stats AND the whole team game's FGA) are zeroed in numerators
    AND denominators of every as-of window sum, so shot-share/zone/FT rates
    are rates GIVEN the player plays. Availability is then handled once, by
    the minutes model. The target game's own row is still emitted (rosters
    need it). See docs/DNP_AUDIT_2026-10-08.md section 2.
    """
    sql = _PLAYER_SHOT_RATES_SQL
    if use_time_decay:
        sql = _time_decay_sql(time_decay_config or TimeDecayConfig(), played_only)
    elif played_only:
        sql = _shot_rates_sql(True)
    con.execute(sql)
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
        "oncourt_team_fga_prior",
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
        "pos_oncourt_team_fga_prior",
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

    # Shot-share denominator: whole-game team FGA (proxy) or on-court team
    # FGA (true usage), chosen by the flag. Everything else is identical so
    # the A/B isolates exactly this one modeling choice.
    share_den_col = "oncourt_team_fga_prior" if use_oncourt_usage else "team_n_fga_prior"
    pos_share_den_col = (
        "pos_oncourt_team_fga_prior" if use_oncourt_usage else "pos_team_n_fga_prior"
    )

    # Stage 1: position-level prior rates (fall back to the hardcoded
    # league constant when the position bucket itself has 0 observations).
    pos_shot_share = _safe_ratio(
        pl.col("pos_n_fga_prior"), pl.col(pos_share_den_col), LEAGUE_SHOT_SHARE_DEFAULT
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
    shot_share_obs = _safe_ratio(pl.col("n_fga_prior"), pl.col(share_den_col), 0.0)
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

    n_fga_prior = df.get_column("n_fga_prior").to_numpy()
    fta_prior = df.get_column("fta_prior").to_numpy()
    # Sample-size weight for the shot-share shrinkage matches its denominator:
    # observed on-court (or whole-game) team FGA the player's share is drawn
    # from -- more observed attempts -> trust the observed share more.
    share_weight = df.get_column(share_den_col).to_numpy()

    shot_share_shrunk = shrink_rate(
        df.get_column("_shot_share_obs").to_numpy(),
        share_weight,
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
