# ruff: noqa: E501
"""Settle the COMBO_STOCKS shadow (rule 5): score the latest row per (game, player, target) with ``made_at <= tipoff - 60 min`` against the box.

The metric is the exact integer CRPS of the stored 199-point grid, ``2 * mean over the 199 taus of the pinball loss`` (the midpoint rule is exact for
a 199-atom pmf), the same function for every arm (S, RECENT, INDEP). Conditional on playing: a player with minutes 0 is ``played = false`` and has
no CRPS; a player absent from a final box is ``not_in_box`` (unscored). Idempotent; writes only ``forward_combo_scores``.
"""

from __future__ import annotations

import json
from datetime import datetime

import duckdb
import numpy as np

from nba.daily.combo_shadow.store import ensure_tables
from nba.props.context_residual import CRPS_TAUS

LEAD_MINUTES = 60
_TAU = np.asarray(CRPS_TAUS, dtype=float)

#: the box quantity of each target (``y``); the combos are sums of the same box columns
_Y = {
    "blk": "b.blk", "stl": "b.stl", "pra": "b.pts + b.reb + b.ast", "pr": "b.pts + b.reb",
    "pa": "b.pts + b.ast", "ra": "b.reb + b.ast", "bs": "b.blk + b.stl",
}  # fmt: skip
_Y_CASE = "CASE e.target " + " ".join(f"WHEN '{t}' THEN {expr}" for t, expr in _Y.items()) + " END"

_SQL = f"""
WITH elig AS (
    SELECT *, row_number() OVER (PARTITION BY game_id, player_id, target ORDER BY made_at DESC, run_id DESC) rn
    FROM forward_combo_shadow WHERE made_at <= tipoff_utc - ? * INTERVAL 1 MINUTE
)
SELECT e.game_id, g.game_date, g.season, e.player_id, e.target, e.run_id, e.made_at, e.tipoff_utc,
       CAST(e.s_pmf199 AS VARCHAR), CAST(e.recent_pmf199 AS VARCHAR), CAST(e.indep_pmf199 AS VARCHAR),
       (SELECT count(*) FROM player_game_stats x WHERE x.game_id = e.game_id) AS n_box,
       (SELECT max(CASE WHEN b.minutes > 0 THEN 1.0 ELSE 0.0 END) FROM player_game_stats b
         WHERE b.game_id = e.game_id AND b.player_id = e.player_id) AS played,
       (SELECT max({_Y_CASE}) FROM player_game_stats b
         WHERE b.game_id = e.game_id AND b.player_id = e.player_id) AS val
FROM elig e JOIN games g USING (game_id)
WHERE e.rn = 1 AND g.home_pts > 0 AND g.away_pts > 0
  AND NOT EXISTS (SELECT 1 FROM forward_combo_scores s
                  WHERE s.game_id = e.game_id AND s.player_id = e.player_id AND s.target = e.target)
"""


def integer_crps_grid(grid: list[float] | np.ndarray, y: float) -> float:
    """Exact integer CRPS of an equal-mass 199-point grid: ``2 * mean_tau max(tau (y - q), (tau - 1) (y - q))``."""
    q = np.asarray(grid, dtype=float)
    d = y - q
    return float(2.0 * np.mean(np.maximum(_TAU * d, (_TAU - 1.0) * d)))


def crps_rows(q: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Per-row integer CRPS of ``(n, 199)`` grids against ``y`` (vectorised ``integer_crps_grid``)."""
    d = y[:, None] - q
    return np.asarray(2.0 * np.mean(np.maximum(_TAU * d, (_TAU - 1.0) * d), axis=1))


def settle_combo_shadow(con: duckdb.DuckDBPyConnection, scored_at: datetime) -> dict[str, int]:
    ensure_tables(con)
    out: list[tuple[object, ...]] = []
    n = n_nobox = 0
    for (
        gid,
        gdate,
        season,
        pid,
        target,
        run_id,
        made_at,
        tip,
        qs,
        qr,
        qi,
        n_box,
        played,
        val,
    ) in con.execute(_SQL, [LEAD_MINUTES]).fetchall():
        if not n_box:
            continue  # box score not ingested yet: stay unsettled
        head = (scored_at, gid, gdate, season, pid, target, run_id, made_at, tip)
        if played is None or (played == 1.0 and val is None):
            out.append((*head, None, None, None, None, None, None, None, "not_in_box"))
            n_nobox += 1
            continue
        did = bool(played)
        y = float(val) if did else None
        c_s: float | None = None
        c_r: float | None = None
        c_i: float | None = None
        if did and y is not None:
            c_s = integer_crps_grid(json.loads(qs), y)
            c_r = integer_crps_grid(json.loads(qr), y)
            c_i = integer_crps_grid(json.loads(qi), y) if qi is not None else None
        out.append((*head, did, y, c_s, c_r, c_i, None, None, "scored"))
        n += 1
    if out:
        con.executemany(
            "INSERT INTO forward_combo_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", out
        )
    return {"combo_scored": n, "combo_not_in_box": n_nobox}
