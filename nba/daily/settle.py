"""Settle forward predictions against completed games / box scores."""

from __future__ import annotations

import json
import math
from datetime import datetime

import duckdb
import numpy as np
from scipy.stats import norm

from nba.daily.predict import MIN_STD
from nba.daily.store import NO_PLAYER, ensure_tables
from nba.eval.metrics import brier_score, log_loss

WIN_TARGET = "win_prob_home"


def normal_crps(mean: float, std: float, y: float) -> float:
    """Closed-form CRPS of N(mean, std^2) at ``y``."""
    s = max(std, MIN_STD)
    z = (y - mean) / s
    return float(s * (z * (2 * norm.cdf(z) - 1) + 2 * norm.pdf(z) - 1 / math.sqrt(math.pi)))


def settle_pending(con: duckdb.DuckDBPyConnection, scored_at: datetime) -> dict[str, int]:
    """Append a ``forward_scores`` row for each latest-per-key prediction whose
    outcome is now known and which has no score yet. Idempotent."""
    ensure_tables(con)
    pending = con.execute(
        """
        WITH latest AS (
            SELECT *, row_number() OVER (
                PARTITION BY game_id, model_name, target, player_id
                ORDER BY made_at DESC, run_id DESC) rn
            FROM forward_predictions)
        SELECT l.game_id, l.model_name, l.version, l.target, l.player_id, l.made_at,
               l.prediction, g.game_date, g.season, g.home_pts, g.away_pts
        FROM latest l JOIN games g USING (game_id)
        WHERE l.rn = 1 AND g.home_pts > 0 AND g.away_pts > 0
          AND NOT EXISTS (SELECT 1 FROM forward_scores s
              WHERE s.game_id = l.game_id AND s.model_name = l.model_name
                AND s.target = l.target AND s.player_id = l.player_id)
        """
    ).fetchall()
    rows: list[tuple[object, ...]] = []
    n_win = n_prop = n_dnp = 0
    for gid, mname, ver, tgt, pid, made_at, pred_json, gdate, season, hp, ap in pending:
        pred = json.loads(pred_json)
        if tgt == WIN_TARGET:
            y = 1.0 if hp > ap else 0.0
            p = float(pred["p_home"])
            ya, pa = np.array([y]), np.array([p])
            rows.append(
                (scored_at, gid, gdate, season, mname, ver, tgt, pid, made_at, y, p,
                 float(log_loss(ya, pa)), float(brier_score(ya, pa)), None, "scored")
            )  # fmt: skip
            n_win += 1
            continue
        if pid == NO_PLAYER:
            continue
        box = con.execute(
            "SELECT count(*), max(CASE WHEN player_id = ? AND minutes > 0 THEN "
            f"{_col(tgt)} END) FROM player_game_stats WHERE game_id = ?",
            [pid, gid],
        ).fetchone()
        if box is None or box[0] == 0:
            continue  # box score not ingested yet: stay unsettled
        mean, std = float(pred["mean"]), float(pred["std"])
        if box[1] is None:
            rows.append((scored_at, gid, gdate, season, mname, ver, tgt, pid, made_at,
                         None, mean, None, None, None, "dnp"))  # fmt: skip
            n_dnp += 1
        else:
            y = float(box[1])
            rows.append((scored_at, gid, gdate, season, mname, ver, tgt, pid, made_at,
                         y, mean, None, None, normal_crps(mean, std, y), "scored"))  # fmt: skip
            n_prop += 1
    if rows:
        con.executemany("INSERT INTO forward_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return {"win_scored": n_win, "prop_scored": n_prop, "prop_dnp": n_dnp}


def _col(target: str) -> str:
    if target not in {"pts", "reb", "ast", "fg3m"}:
        raise ValueError(f"unknown prop target {target!r}")
    return target
