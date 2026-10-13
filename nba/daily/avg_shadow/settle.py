# ruff: noqa: E501
"""Settle the AVG shadow: score the latest row per (game, player, stat) with ``made_at <= tipoff - 60 min`` against the box score.

All arms are scored from the 19 stored quantiles on the integer grid (``nba.daily.lt_live.integer_crps``, 2 x mean pinball; conditional on
playing). Idempotent; a player absent from a final box is ``not_in_box`` (unscored). Writes only ``forward_avg_scores``.
"""

from __future__ import annotations

import json
from datetime import datetime

import duckdb

from nba.daily.avg_shadow.store import ensure_tables
from nba.daily.lt_live import integer_crps

LEAD_MINUTES = 60

_SQL = """
WITH elig AS (
    SELECT *, row_number() OVER (PARTITION BY game_id, player_id, stat ORDER BY made_at DESC, run_id DESC) rn
    FROM forward_avg_shadow WHERE made_at <= tipoff_utc - ? * INTERVAL 1 MINUTE
)
SELECT e.game_id, g.game_date, g.season, e.player_id, e.stat, e.run_id, e.made_at, e.tipoff_utc, e.stale_model,
       CAST(e.avg_q19 AS VARCHAR), CAST(e.full_q19 AS VARCHAR), CAST(e.a0_q19 AS VARCHAR),
       CAST(e.seqc_q19 AS VARCHAR), CAST(e.seq_q19 AS VARCHAR),
       (SELECT count(*) FROM player_game_stats b WHERE b.game_id = e.game_id) AS n_box,
       (SELECT max(CASE WHEN b.minutes > 0 THEN 1.0 ELSE 0.0 END) FROM player_game_stats b
         WHERE b.game_id = e.game_id AND b.player_id = e.player_id) AS played,
       (SELECT max(CASE e.stat WHEN 'pts' THEN b.pts WHEN 'reb' THEN b.reb WHEN 'ast' THEN b.ast
                               WHEN 'fg3m' THEN b.fg3m END) FROM player_game_stats b
         WHERE b.game_id = e.game_id AND b.player_id = e.player_id) AS val
FROM elig e JOIN games g USING (game_id)
WHERE e.rn = 1 AND g.home_pts > 0 AND g.away_pts > 0
  AND NOT EXISTS (SELECT 1 FROM forward_avg_scores s
                  WHERE s.game_id = e.game_id AND s.player_id = e.player_id AND s.stat = e.stat)
"""


def settle_avg_shadow(con: duckdb.DuckDBPyConnection, scored_at: datetime) -> dict[str, int]:
    ensure_tables(con)
    out: list[tuple[object, ...]] = []
    n = n_nobox = 0
    for (gid, gdate, season, pid, stat, run_id, made_at, tip, stale, qa, qf, q0, qs, qq, n_box, played, val) in con.execute(
        _SQL, [LEAD_MINUTES]
    ).fetchall():  # fmt: skip
        if not n_box:
            continue  # box score not ingested yet: stay unsettled
        head = (scored_at, gid, gdate, season, pid, stat, run_id, made_at, tip)
        if played is None or (played == 1.0 and val is None):
            out.append((*head, None, None, None, None, None, None, None, stale, "not_in_box"))
            n_nobox += 1
            continue
        did = bool(played)
        y = float(val) if did else None
        c = [
            integer_crps(json.loads(q), y) if did and y is not None else None
            for q in (qa, qf, q0, qs, qq)
        ]
        out.append((*head, did, y, *c, stale, "scored"))
        n += 1
    if out:
        con.executemany(
            "INSERT INTO forward_avg_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", out
        )
    return {"avg_scored": n, "avg_not_in_box": n_nobox}
