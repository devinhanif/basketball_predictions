# ruff: noqa: E501
"""Score both P(play) columns once the box score is in; the live log that confirms or refutes R.

Rule (same eligibility as ``forward_scores_elig``): the latest shadow row per (game, player) with
``made_at <= tipoff - 60 min``; label ``y = 1`` when the player has a box row with minutes > 0, ``0`` for a
listed DNP, and no score (status ``not_in_box``) for a player absent from a game's box. Idempotent."""

from __future__ import annotations

from datetime import datetime

import duckdb
import numpy as np

from nba.daily.pplay_shadow.store import ensure_tables
from nba.truth.metrics import log_loss

LEAD_MINUTES = 60

_SQL = """
WITH elig AS (
    SELECT *, row_number() OVER (PARTITION BY game_id, player_id ORDER BY made_at DESC, run_id DESC) rn
    FROM forward_pplay_shadow WHERE made_at <= tipoff - ? * INTERVAL 1 MINUTE
)
SELECT e.game_id, g.game_date, g.season, e.player_id, e.run_id, e.made_at, e.tipoff,
       e.p_play_prod, e.p_play_r, e.in_prod_log,
       (SELECT count(*) FROM player_game_stats b WHERE b.game_id = e.game_id) AS n_box,
       (SELECT max(CASE WHEN b.minutes > 0 THEN 1.0 ELSE 0.0 END) FROM player_game_stats b
         WHERE b.game_id = e.game_id AND b.player_id = e.player_id) AS played
FROM elig e JOIN games g USING (game_id)
WHERE e.rn = 1 AND g.home_pts > 0 AND g.away_pts > 0
  AND NOT EXISTS (SELECT 1 FROM forward_pplay_scores s
                  WHERE s.game_id = e.game_id AND s.player_id = e.player_id)
"""


def settle_pplay_shadow(con: duckdb.DuckDBPyConnection, scored_at: datetime) -> dict[str, int]:
    ensure_tables(con)
    out: list[tuple[object, ...]] = []
    n = n_nobox = 0
    for (gid, gdate, season, pid, run_id, made_at, tip, p_prod, p_r, logged, n_box,
         played) in con.execute(_SQL, [LEAD_MINUTES]).fetchall():  # fmt: skip
        if not n_box:
            continue  # box score not ingested yet: stay unsettled
        head = (scored_at, gid, gdate, season, pid, run_id, made_at, tip, p_prod, p_r)
        if played is None:
            out.append((*head, None, None, None, logged, "not_in_box"))
            n_nobox += 1
            continue
        y = np.array([float(played)])
        ll_prod = float(log_loss(y, np.array([p_prod])))
        ll_r = None if p_r is None else float(log_loss(y, np.array([p_r])))
        out.append((*head, float(played), ll_prod, ll_r, logged, "scored"))
        n += 1
    if out:
        con.executemany(
            "INSERT INTO forward_pplay_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", out
        )
    return {"pplay_scored": n, "pplay_not_in_box": n_nobox}
