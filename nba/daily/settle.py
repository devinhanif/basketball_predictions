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
from nba.props import full_support as fsup
from nba.props.forward import QUANTILE_TAUS
from nba.truth.metrics import brier_score, log_loss

WIN_TARGET = "win_prob_home"


def normal_crps(mean: float, std: float, y: float) -> float:
    """Closed-form CRPS of N(mean, std^2) at ``y``."""
    s = max(std, MIN_STD)
    z = (y - mean) / s
    return float(s * (z * (2 * norm.cdf(z) - 1) + 2 * norm.pdf(z) - 1 / math.sqrt(math.pi)))


def quantile_crps(taus: list[float], quantiles: list[float], y: float) -> float:
    """Empirical CRPS from a stored quantile grid: ``2 * mean_tau pinball_tau``
    (CRPS = 2 * integral of pinball loss over tau; the grid truncates tails
    below/above its end taus, a small downward bias for wide misses)."""
    t = np.asarray(taus, dtype=float)
    q = np.asarray(quantiles, dtype=float)
    d = y - q
    return float(2.0 * np.mean(np.maximum(t * d, (t - 1.0) * d)))


def prediction_crps(pred: dict[str, object], y: float) -> float:
    """CRPS of a stored prop prediction: empirical from ``q_grid`` when present
    (routed sim / recency model), else the closed-form normal from mean/std."""
    grid = pred.get("q_grid")
    if isinstance(grid, list) and len(grid) == len(QUANTILE_TAUS):
        return quantile_crps(list(QUANTILE_TAUS), [float(v) for v in grid], y)
    return normal_crps(float(pred["mean"]), float(pred["std"]), y)  # type: ignore[arg-type]


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
        mean = float(pred["mean"])
        if box[1] is None:
            rows.append((scored_at, gid, gdate, season, mname, ver, tgt, pid, made_at,
                         None, mean, None, None, None, "dnp"))  # fmt: skip
            n_dnp += 1
        else:
            y = float(box[1])
            rows.append((scored_at, gid, gdate, season, mname, ver, tgt, pid, made_at,
                         y, mean, None, None, prediction_crps(pred, y), "scored"))  # fmt: skip
            n_prop += 1
    if rows:
        con.executemany("INSERT INTO forward_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    settle_eligible_pending(con, scored_at)
    return {"win_scored": n_win, "prop_scored": n_prop, "prop_dnp": n_dnp}


def _col(target: str) -> str:
    if target not in {"pts", "reb", "ast", "fg3m"}:
        raise ValueError(f"unknown prop target {target!r}")
    return target


# ---------------------------------------------------------------------------------------------
# Pre-registered scoring rows (docs/FORWARD_PREREG_2026_27.md rules 3-4): the prediction used for
# each (game, model, target, player) is the latest one with made_at <= tip-off minus the model's
# lead (60 min; the T-30 arm: 30 min), NOT the latest overall that ``settle_pending`` scores.
# Stored beside (never instead of) ``forward_scores``: integer-support CRPS (RPS) plus the pinball19
# CRPS, the data the checkpoint scorer (``nba.daily.checkpoint``) pairs arms with.
# ---------------------------------------------------------------------------------------------

#: Models scored on the eligibility rule: production, comparators and the shadow arms.
PREREG_MODELS: tuple[str, ...] = (
    "props_context_residual",
    "props_context_residual_t30",
    "props_context_residual_int",
    "props_context_residual_lt",
    "rung0_injury_elo",
    "rung0_mov_elo",
)
T30_MODEL = "props_context_residual_t30"
LEAD_MINUTES: dict[str, int] = {T30_MODEL: 30}
DEFAULT_LEAD_MINUTES = 60

ELIG_DDL = """
CREATE TABLE IF NOT EXISTS forward_scores_elig (
    scored_at TIMESTAMP,
    game_id VARCHAR,
    game_date DATE,
    season INT,
    model_name VARCHAR,
    version VARCHAR,
    target VARCHAR,
    player_id INT,
    run_id VARCHAR,
    made_at TIMESTAMP,
    tipoff TIMESTAMP,
    lead_min INT,            -- lead: made_at <= tipoff - lead (T-30 arm: snapshot < tipoff - lead)
    y DOUBLE,
    pred DOUBLE,
    q10 DOUBLE,
    q90 DOUBLE,
    rps DOUBLE,              -- integer-support CRPS from p_ge_full
    pinball19 DOUBLE,        -- continuous 19-quantile CRPS (secondary)
    log_loss DOUBLE,         -- win rows only
    brier DOUBLE,
    pit_lo DOUBLE,           -- F(y-1), F(y): continuity-corrected PIT interval
    pit_hi DOUBLE,
    starter_rate DOUBLE,
    snapshot_fetched_at TIMESTAMP,   -- T-30 arm: lineup snapshot used
    status VARCHAR           -- 'scored' | 'dnp' | 'unscorable'
);
"""

_SELECT_ELIGIBLE = """
WITH elig AS (
    SELECT *, row_number() OVER (
        PARTITION BY game_id, model_name, target, player_id
        ORDER BY made_at DESC, run_id DESC) rn
    FROM forward_predictions
    WHERE model_name IN ({models})
      AND CASE WHEN model_name = ?
               -- T-30 arm: the information cutoff is the lineup snapshot, not made_at
               THEN made_at < tipoff AND TRY_CAST(
                        json_extract_string(prediction, '$.snapshot_fetched_at') AS TIMESTAMP)
                    < tipoff - ? * INTERVAL 1 MINUTE
               ELSE made_at <= tipoff - ? * INTERVAL 1 MINUTE END
),
box AS (
    SELECT game_id, player_id,
           max(CASE WHEN minutes > 0 THEN pts END) AS pts,
           max(CASE WHEN minutes > 0 THEN reb END) AS reb,
           max(CASE WHEN minutes > 0 THEN ast END) AS ast,
           max(CASE WHEN minutes > 0 THEN fg3m END) AS fg3m
    FROM player_game_stats GROUP BY game_id, player_id
),
has_box AS (SELECT DISTINCT game_id FROM player_game_stats)
SELECT l.game_id, l.model_name, l.version, l.target, l.player_id, l.run_id, l.made_at, l.tipoff,
       l.prediction, g.game_date, g.season, g.home_pts, g.away_pts,
       b.pts, b.reb, b.ast, b.fg3m, h.game_id IS NOT NULL AS has_box
FROM elig l JOIN games g USING (game_id)
LEFT JOIN box b ON b.game_id = l.game_id AND b.player_id = l.player_id
LEFT JOIN has_box h ON h.game_id = l.game_id
WHERE l.rn = 1 AND g.home_pts > 0 AND g.away_pts > 0
  AND NOT EXISTS (SELECT 1 FROM forward_scores_elig s
      WHERE s.game_id = l.game_id AND s.model_name = l.model_name
        AND s.target = l.target AND s.player_id = l.player_id)
"""


def settle_eligible_pending(con: duckdb.DuckDBPyConnection, scored_at: datetime) -> dict[str, int]:
    """Append a ``forward_scores_elig`` row per eligible-prediction key whose game is complete.

    Idempotent. Props whose stored ``p_ge_full`` is missing or does not reach 99.9% of the mass
    are written with ``status = 'unscorable'`` (counted by the checkpoint, never dropped)."""
    con.execute(ELIG_DDL)
    sql = _SELECT_ELIGIBLE.format(models=",".join("?" for _ in PREREG_MODELS))
    params: list[object] = [
        *PREREG_MODELS,
        T30_MODEL,
        LEAD_MINUTES[T30_MODEL],
        DEFAULT_LEAD_MINUTES,
    ]
    pending = con.execute(sql, params).fetchall()
    out_rows: list[tuple[object, ...]] = []
    counts = {"scored": 0, "dnp": 0, "unscorable": 0}
    for (gid, mname, ver, tgt, pid, run_id, made_at, tip, pred_json, gdate, season, hp, ap,
         b_pts, b_reb, b_ast, b_fg3m, has_box) in pending:  # fmt: skip
        pred = json.loads(pred_json)
        lead = LEAD_MINUTES.get(mname, DEFAULT_LEAD_MINUTES)
        head = (scored_at, gid, gdate, season, mname, ver, tgt, pid, run_id, made_at, tip, lead)
        snap = pred.get("snapshot_fetched_at")
        snap_ts = datetime.fromisoformat(snap) if isinstance(snap, str) else None
        if tgt == WIN_TARGET:
            y = 1.0 if hp > ap else 0.0
            p = float(pred["p_home"])
            ya, pa = np.array([y]), np.array([p])
            out_rows.append(
                (*head, y, p, None, None, None, None, float(log_loss(ya, pa)),
                 float(brier_score(ya, pa)), None, None, None, snap_ts, "scored")
            )  # fmt: skip
            counts["scored"] += 1
            continue
        if pid == NO_PLAYER or not has_box:
            continue  # box score not ingested yet: stay unsettled
        yv = {"pts": b_pts, "reb": b_reb, "ast": b_ast, "fg3m": b_fg3m}[tgt]
        mean = float(pred["mean"])
        starter_rate = pred.get("starter_rate")
        sr = float(starter_rate) if starter_rate is not None else None
        if yv is None:
            out_rows.append(
                (*head, None, mean, None, None, None, None, None, None, None, None, sr,
                 snap_ts, "dnp")
            )  # fmt: skip
            counts["dnp"] += 1
            continue
        y = float(yv)
        surv = fsup.survival(pred.get(fsup.FIELD))
        grid = pred.get("q_grid")
        pin = (
            quantile_crps(list(QUANTILE_TAUS), [float(v) for v in grid], y)
            if isinstance(grid, list) and len(grid) == len(QUANTILE_TAUS)
            else None
        )
        if surv is not None and fsup.is_complete(surv):
            rps: float | None = fsup.crps_int_surv(surv, int(y))
            lo, hi = fsup.pit_bounds(surv, int(y))
            status = "scored"
        else:
            rps, lo, hi, status = None, None, None, "unscorable"
        counts[status] += 1
        out_rows.append(
            (*head, y, mean, _f(pred.get("q10")), _f(pred.get("q90")), rps, pin, None, None,
             lo, hi, sr, snap_ts, status)
        )  # fmt: skip
    if out_rows:
        marks = ",".join("?" * 25)
        con.executemany(f"INSERT INTO forward_scores_elig VALUES ({marks})", out_rows)
    return counts


def _f(v: object) -> float | None:
    return None if v is None else float(v)  # type: ignore[arg-type]
