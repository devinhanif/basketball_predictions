# ruff: noqa: E501
"""Shot-type counts for SHOT_PROFILE / DEFENSE_CONTEXT (docs/prereg/ADVANCED_RATES.md, DRAFT).

Source: ``data/pbp`` field-goal events (``is_field_goal = 1``): ``shot_distance`` and ``sub_type``
are present on 100% of 2022-24 field goals; counts reconcile to the box FGA in 100% of team-games.
The x/y ``shots`` table (``game_event_id == action_number``, joined on ``is_field_goal = 1`` rows
only, because block rows share the action number) is a pre-declared SWAP-IN for the buckets only
if its coverage gate passes before the rule is frozen.

Buckets (distance in feet, ``shot_value`` 2 or 3):

* ``rim``       distance <= 4
* ``short_mid`` 2-pointer, 5 <= distance <= 13
* ``long_mid``  2-pointer, distance >= 14
* ``corner3``   3-pointer, distance <= 22 (the corner line is 22 ft; every other 3 is longer)
* ``atb3``      3-pointer, distance >= 23 (above the break and heaves)

Sub-types from ``sub_type`` text (first match wins, case-insensitive): putback ('Putback ...' and
'Tip ...'; PLAY_TYPE only), dunk, hook, floater
('Floating'), stepback ('Step Back'), pullup ('Pullup' / 'Pull-Up'), layup ('Layup' incl. finger
roll, tip, putback), jump (any other jump/bank/fadeaway shot).
"""

from __future__ import annotations

import duckdb
import polars as pl

BUCKETS: tuple[str, ...] = ("rim", "short_mid", "long_mid", "corner3", "atb3")
SUBTYPES: tuple[str, ...] = (
    "putback", "dunk", "hook", "floater", "stepback", "pullup", "layup", "jump",
)  # fmt: skip
#: SHOT_PROFILE columns; ``putback`` lives in PLAY_TYPE only (one home per column, no double count).
SHOT_PROFILE_SUBTYPES: tuple[str, ...] = tuple(x for x in SUBTYPES if x != "putback")
FAST_SECONDS = 7.0  # existing fast-possession measure: possession length <= 7 s

BUCKET_CASE = """CASE
  WHEN shot_value = 3 AND shot_distance <= 22 THEN 'corner3'
  WHEN shot_value = 3 THEN 'atb3'
  WHEN shot_distance <= 4 THEN 'rim'
  WHEN shot_distance <= 13 THEN 'short_mid'
  ELSE 'long_mid' END"""

SUBTYPE_CASE = """CASE
  WHEN lower(sub_type) LIKE '%putback%' OR lower(sub_type) LIKE 'tip %' THEN 'putback'
  WHEN lower(sub_type) LIKE '%dunk%' THEN 'dunk'
  WHEN lower(sub_type) LIKE '%hook%' THEN 'hook'
  WHEN lower(sub_type) LIKE '%floating%' THEN 'floater'
  WHEN lower(sub_type) LIKE '%step back%' THEN 'stepback'
  WHEN lower(sub_type) LIKE '%pullup%' OR lower(sub_type) LIKE '%pull-up%' THEN 'pullup'
  WHEN lower(sub_type) LIKE '%layup%' THEN 'layup'
  ELSE 'jump' END"""


def shot_bucket(shot_value: int, shot_distance: int) -> str:
    """Python mirror of ``BUCKET_CASE`` (tested equal)."""
    if shot_value == 3:
        return "corner3" if shot_distance <= 22 else "atb3"
    if shot_distance <= 4:
        return "rim"
    return "short_mid" if shot_distance <= 13 else "long_mid"


def shot_subtype(sub_type: str) -> str:
    """Python mirror of ``SUBTYPE_CASE`` (tested equal)."""
    t = sub_type.lower()
    if "putback" in t or t.startswith("tip "):
        return "putback"
    for key, name in (
        ("dunk", "dunk"),
        ("hook", "hook"),
        ("floating", "floater"),
        ("step back", "stepback"),
        ("pullup", "pullup"),
        ("pull-up", "pullup"),
        ("layup", "layup"),
    ):
        if key in t:
            return name
    return "jump"


def _wide(prefix: str, names: tuple[str, ...], made: bool) -> str:
    cols = [f"SUM(CASE WHEN {prefix} = '{n}' THEN 1 ELSE 0 END)::INTEGER AS att_{n}" for n in names]
    if made:
        cols += [
            f"SUM(CASE WHEN {prefix} = '{n}' AND made THEN 1 ELSE 0 END)::INTEGER AS made_{n}"
            for n in names
        ]
    return ", ".join(cols)


def _fg_cte(pbp: str) -> str:
    return (
        f"WITH fg AS (SELECT game_id, team_id, player_id, ({BUCKET_CASE}) AS bucket, "
        f"({SUBTYPE_CASE}) AS subtype, (shot_result = 'Made') AS made FROM {pbp} "
        f"WHERE is_field_goal = 1 AND team_id IS NOT NULL AND shot_distance IS NOT NULL)"
    )


def player_shot_counts(con: duckdb.DuckDBPyConnection, pbp: str = "pbp") -> pl.DataFrame:
    """One row per (game_id, player_id): attempts and makes per bucket, attempts per sub-type."""
    sql = (
        f"{_fg_cte(pbp)} SELECT game_id, player_id, {_wide('bucket', BUCKETS, True)}, "
        f"{_wide('subtype', SUBTYPES, False)} FROM fg WHERE player_id > 0 GROUP BY 1, 2"
    )
    return con.execute(sql).pl()


def team_allowed_counts(con: duckdb.DuckDBPyConnection, pbp: str = "pbp") -> pl.DataFrame:
    """One row per (game_id, def_team_id): attempts and makes ALLOWED per bucket (the shooter's
    team is the other side of ``games``)."""
    sql = (
        f"{_fg_cte(pbp)} SELECT fg.game_id, "
        "CASE WHEN fg.team_id = g.home_team THEN g.away_team ELSE g.home_team END AS def_team_id, "
        f"{_wide('bucket', BUCKETS, True)} FROM fg JOIN games g USING (game_id) GROUP BY 1, 2"
    )
    return con.execute(sql).pl()


def play_type_counts(con: duckdb.DuckDBPyConnection, pbp: str = "pbp") -> pl.DataFrame:
    """One row per (game_id, player_id) for PLAY_TYPE.

    From ``possessions`` (final-shot possessions: ``shooter_id`` set, outcome FGM2/FGM3/FGA_miss;
    TOV rows carry ``shooter_id`` only 10% of the time, so turnovers are NOT used here):
    ``fsp`` final-shot possessions, ``tr_fsp`` of them with length <= 7 s, ``tr_pts`` their points,
    ``mk`` makes, ``ast_mk`` assisted makes. From PBP: ``pb_att`` / ``pb_made`` putback and tip
    attempts / makes. Possession length is ``clock_start - clock_end`` (seconds, same period).
    """
    key = ["game_id", "player_id"]
    poss = con.execute(
        "SELECT game_id, shooter_id AS player_id, COUNT(*)::INTEGER AS fsp, "
        f"SUM(CASE WHEN clock_start - clock_end <= {FAST_SECONDS} THEN 1 ELSE 0 END)::INTEGER AS tr_fsp, "
        f"SUM(CASE WHEN clock_start - clock_end <= {FAST_SECONDS} THEN COALESCE(pts, 0) ELSE 0 END)::INTEGER AS tr_pts, "
        "SUM(CASE WHEN outcome IN ('FGM2','FGM3') THEN 1 ELSE 0 END)::INTEGER AS mk, "
        "SUM(CASE WHEN outcome IN ('FGM2','FGM3') AND assister_id IS NOT NULL THEN 1 ELSE 0 END)::INTEGER AS ast_mk "
        "FROM possessions WHERE shooter_id IS NOT NULL AND outcome IN ('FGM2','FGM3','FGA_miss') "
        "GROUP BY 1, 2"
    ).pl()
    pb = con.execute(
        f"{_fg_cte(pbp)} SELECT game_id, player_id, COUNT(*)::INTEGER AS pb_att, "
        "SUM(CASE WHEN made THEN 1 ELSE 0 END)::INTEGER AS pb_made FROM fg "
        "WHERE player_id > 0 AND subtype = 'putback' GROUP BY 1, 2"
    ).pl()
    return poss.join(pb, on=key, how="full", coalesce=True).with_columns(
        [
            pl.col(c).fill_null(0)
            for c in ("fsp", "tr_fsp", "tr_pts", "mk", "ast_mk", "pb_att", "pb_made")
        ]
    )


def team_allowed_fast(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """One row per (game_id, def_team_id): possessions the defence faced and how many were fast
    (<= 7 s); the opponent's as-of transition rate ALLOWED is the ratio of the prior sums."""
    return con.execute(
        "SELECT game_id, def_team AS def_team_id, COUNT(*)::INTEGER AS faced, "
        f"SUM(CASE WHEN clock_start - clock_end <= {FAST_SECONDS} THEN 1 ELSE 0 END)::INTEGER AS faced_fast "
        "FROM possessions GROUP BY 1, 2"
    ).pl()
