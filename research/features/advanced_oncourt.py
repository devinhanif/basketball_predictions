# ruff: noqa: E501
"""On-court denominators for ADVANCED_RATES (docs/prereg/ADVANCED_RATES.md, DRAFT).

Everything here is post-game truth about a game that has ALREADY been played; it becomes a
pre-game feature only through ``research.features.advanced_rates`` (strictly earlier games).

Two sources, each reconciled to the box score on the real data (gate numbers in the prereg):

* PBP events x ``stints`` for FGA / FGM / FTA / TOV. The ``possessions`` table collapses
  second-chance shots into one trip and undercounts team FGA by about 13 per game, so it is NOT
  used for shot-type denominators. Event time rule (R1): an event at clock ``sec`` (seconds left
  in the period) belongs to the stint with ``end_clock <= sec < start_clock``.
* ``possessions`` x lineups at possession start for possession counts and rebound availability
  (``n_oreb`` / ``n_dreb`` reconcile to the box in 99.6-99.97% of team-games).

Pure SQL over a DuckDB connection; the caller owns the connection (read-only on ``nba.duckdb``).
"""

from __future__ import annotations

import duckdb
import polars as pl

#: Registered by ``register_events``: one row per own-team event with a kind.
EVENTS_SQL = """
CREATE OR REPLACE TEMP TABLE adv_events AS
SELECT game_id, team_id, period,
       regexp_extract(clock, 'PT(\\d+)M', 1)::INTEGER * 60
         + regexp_extract(clock, 'M([\\d.]+)S', 1)::DOUBLE AS sec,
       (is_field_goal = 1)::INTEGER AS fga,
       (is_field_goal = 1 AND shot_result = 'Made')::INTEGER AS fgm,
       (is_field_goal = 1 AND shot_value = 3)::INTEGER AS fg3a,
       (action_type = 'Free Throw')::INTEGER AS fta,
       (action_type = 'Turnover')::INTEGER AS tov
FROM {pbp}
WHERE team_id IS NOT NULL
  AND (is_field_goal = 1 OR action_type IN ('Free Throw', 'Turnover'))
"""

ONCOURT_EVENTS_SQL = """
SELECT e.game_id, u.p AS player_id,
       SUM(e.fga)::INTEGER AS oc_fga, SUM(e.fgm)::INTEGER AS oc_fgm, SUM(e.fg3a)::INTEGER AS oc_fg3a,
       SUM(e.fta)::INTEGER AS oc_fta, SUM(e.tov)::INTEGER AS oc_tov
FROM adv_events e
JOIN stints s ON s.game_id = e.game_id AND s.team_id = e.team_id AND s.period = e.period
             AND s.start_clock > e.sec AND e.sec >= s.end_clock,
     UNNEST(s.players::INTEGER[]) AS u(p)
GROUP BY 1, 2
"""

ONCOURT_MINUTES_SQL = """
SELECT s.game_id, u.p AS player_id, SUM(s.start_clock - s.end_clock) / 60.0 AS oc_min
FROM stints s, UNNEST(s.players::INTEGER[]) AS u(p)
GROUP BY 1, 2
"""

#: Possession-based: p on offence -> own OREB + opponent DREB available; p on defence -> own DREB
#: + opponent OREB. ``oc_poss_off`` / ``oc_poss_def`` are possession counts while on court.
ONCOURT_POSS_SQL = """
WITH o AS (
  SELECT game_id, u.p AS player_id, COUNT(*) AS oc_poss_off,
         SUM(n_oreb) AS off_oreb, SUM(n_dreb) AS off_opp_dreb
  FROM possessions, UNNEST(off_players::INTEGER[]) AS u(p) GROUP BY 1, 2),
d AS (
  SELECT game_id, u.p AS player_id, COUNT(*) AS oc_poss_def,
         SUM(n_dreb) AS def_dreb, SUM(n_oreb) AS def_opp_oreb
  FROM possessions, UNNEST(def_players::INTEGER[]) AS u(p) GROUP BY 1, 2)
SELECT COALESCE(o.game_id, d.game_id) AS game_id, COALESCE(o.player_id, d.player_id) AS player_id,
       COALESCE(oc_poss_off, 0)::INTEGER AS oc_poss_off, COALESCE(oc_poss_def, 0)::INTEGER AS oc_poss_def,
       COALESCE(off_oreb, 0)::INTEGER AS oc_oreb, COALESCE(off_opp_dreb, 0)::INTEGER AS oc_opp_dreb,
       COALESCE(def_dreb, 0)::INTEGER AS oc_dreb, COALESCE(def_opp_oreb, 0)::INTEGER AS oc_opp_oreb
FROM o FULL OUTER JOIN d USING (game_id, player_id)
"""


def register_events(con: duckdb.DuckDBPyConnection, pbp: str = "pbp") -> None:
    """Create the temp table ``adv_events`` from a relation/view ``pbp`` (columns of data/pbp)."""
    con.execute(EVENTS_SQL.format(pbp=pbp))


def build_oncourt(con: duckdb.DuckDBPyConnection, pbp: str = "pbp") -> pl.DataFrame:
    """One row per (game_id, player_id) with every on-court denominator (NULL-free counts).

    Rows exist for every player who appears in any stint or lineup; a played player with no
    row is an explicit gap the caller must flag, never fill with zero.
    """
    register_events(con, pbp)
    ev = con.execute(ONCOURT_EVENTS_SQL).pl()
    mn = con.execute(ONCOURT_MINUTES_SQL).pl()
    ps = con.execute(ONCOURT_POSS_SQL).pl()
    key = ["game_id", "player_id"]
    out = mn.join(ev, on=key, how="full", coalesce=True).join(ps, on=key, how="full", coalesce=True)
    return out.with_columns(
        [pl.col(c).fill_null(0) for c in out.columns if c not in key and c != "oc_min"]
        + [pl.col("oc_min").fill_null(0.0)]
    )
