# ruff: noqa: E501
"""Score FULL against production, nightly: the live log that confirms or refutes RECENT_ROLE_FULL.

Eligibility (same as ``forward_scores_elig`` and the P(play) shadow): the latest shadow row per
(game, player, stat) with ``made_at <= tipoff - 60 min``. Two scores, both on integer support
(``ceil(q - 0.5)``, the house map), lower is better:

* conditional, played rows only: ``crps_prod`` / ``crps_full`` = 2 x mean pinball over the 19 stored taus
  (``nba.daily.lt_live.integer_crps``), the FORWARD_PREREG pairing;
* joint (unconditional), every player in a final box, ``y_uncond`` = the stat if he played else 0: the
  forecast distribution is ``(1 - p) delta_0 + p/19 sum_i delta_{q_i}`` (the 19 stored quantiles as equal-mass
  atoms) and the score is its exact CRPS. ``crps_joint_prod`` = production p_play x production grid;
  ``crps_joint_full`` = P(play) R (``forward_pplay_shadow.p_play_r``, latest eligible row) x FULL grid, i.e. what
  production would serve with both shadows adopted; ``crps_joint_rprod`` = R x production grid (R alone).
  The joint score uses the atoms, not the pinball form, so joint and conditional CRPS are not comparable in
  level, only within a column pair.

Idempotent; a player absent from a final box is ``not_in_box`` (unscored).
"""

from __future__ import annotations

import json
from datetime import datetime

import duckdb
import numpy as np

from nba.daily.full_shadow.store import ensure_tables
from nba.daily.lt_live import integer_crps
from nba.daily.pplay_shadow.store import ensure_tables as ensure_pplay_tables
from nba.props.context_residual import to_integer_support

LEAD_MINUTES = 60
STAT_COLUMN = {"pts": "pts", "reb": "reb", "ast": "ast", "fg3m": "fg3m"}

_SQL = """
WITH elig AS (
    SELECT *, row_number() OVER (PARTITION BY game_id, player_id, stat ORDER BY made_at DESC, run_id DESC) rn
    FROM forward_full_shadow WHERE made_at <= tipoff - ? * INTERVAL 1 MINUTE
), pp AS (
    SELECT game_id, player_id, p_play_r,
           row_number() OVER (PARTITION BY game_id, player_id ORDER BY made_at DESC, run_id DESC) rn
    FROM forward_pplay_shadow WHERE made_at <= tipoff - ? * INTERVAL 1 MINUTE
)
SELECT e.game_id, g.game_date, g.season, e.player_id, e.stat, e.run_id, e.made_at, e.tipoff,
       CAST(e.q_grid AS VARCHAR), CAST(e.q_grid_prod AS VARCHAR), e.p_play_prod, e.in_prod_log,
       pp.p_play_r,
       (SELECT count(*) FROM player_game_stats b WHERE b.game_id = e.game_id) AS n_box,
       (SELECT max(CASE WHEN b.minutes > 0 THEN 1.0 ELSE 0.0 END) FROM player_game_stats b
         WHERE b.game_id = e.game_id AND b.player_id = e.player_id) AS played,
       (SELECT max(CASE e.stat WHEN 'pts' THEN b.pts WHEN 'reb' THEN b.reb WHEN 'ast' THEN b.ast
                               WHEN 'fg3m' THEN b.fg3m END) FROM player_game_stats b
         WHERE b.game_id = e.game_id AND b.player_id = e.player_id) AS val
FROM elig e JOIN games g USING (game_id)
LEFT JOIN pp ON pp.game_id = e.game_id AND pp.player_id = e.player_id AND pp.rn = 1
WHERE e.rn = 1 AND g.home_pts > 0 AND g.away_pts > 0
  AND NOT EXISTS (SELECT 1 FROM forward_full_scores s
                  WHERE s.game_id = e.game_id AND s.player_id = e.player_id AND s.stat = e.stat)
"""


def mixture_crps(grid: list[float] | np.ndarray, p: float, y: float) -> float:
    """Exact CRPS of ``(1 - p) delta_0 + p/K sum_k delta_{x_k}`` (x = the integer-mapped grid) against y."""
    x = to_integer_support(np.asarray(grid, dtype=float))
    atoms = np.concatenate([[0.0], x])
    w = np.concatenate([[1.0 - p], np.full(len(x), p / len(x))])
    term1 = float(np.sum(w * np.abs(atoms - y)))
    term2 = 0.5 * float(w @ np.abs(atoms[:, None] - atoms[None, :]) @ w)
    return term1 - term2


def settle_full_shadow(con: duckdb.DuckDBPyConnection, scored_at: datetime) -> dict[str, int]:
    ensure_tables(con)
    ensure_pplay_tables(
        con
    )  # the join below reads P(play) R; an empty table when that shadow never ran
    out: list[tuple[object, ...]] = []
    n = n_nobox = 0
    for (gid, gdate, season, pid, stat, run_id, made_at, tip, qf, qp, p_prod, logged, p_r, n_box,
         played, val) in con.execute(_SQL, [LEAD_MINUTES, LEAD_MINUTES]).fetchall():  # fmt: skip
        if not n_box:
            continue  # box score not ingested yet: stay unsettled
        head = (scored_at, gid, gdate, season, pid, stat, run_id, made_at, tip)
        if played is None or (played == 1.0 and val is None):
            out.append(
                (
                    *head,
                    None,
                    None,
                    None,
                    None,
                    p_prod,
                    p_r,
                    None,
                    None,
                    None,
                    None,
                    logged,
                    "not_in_box",
                )
            )
            n_nobox += 1
            continue
        gf, gp = json.loads(qf), json.loads(qp)
        did = bool(played)
        y = float(val) if did else None
        yu = float(val) if did else 0.0
        c_prod = integer_crps(gp, y) if did and y is not None else None
        c_full = integer_crps(gf, y) if did and y is not None else None
        j_prod = mixture_crps(gp, float(p_prod), yu)
        j_full = None if p_r is None else mixture_crps(gf, float(p_r), yu)
        j_rprod = None if p_r is None else mixture_crps(gp, float(p_r), yu)
        out.append(
            (
                *head,
                did,
                y,
                c_prod,
                c_full,
                p_prod,
                p_r,
                yu,
                j_prod,
                j_full,
                j_rprod,
                logged,
                "scored",
            )
        )
        n += 1
    if out:
        con.executemany(
            "INSERT INTO forward_full_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            out,
        )
    return {"full_scored": n, "full_not_in_box": n_nobox}
