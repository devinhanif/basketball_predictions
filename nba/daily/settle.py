"""Settle forward predictions against completed games / box scores."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta

import duckdb
import numpy as np
from scipy.stats import norm

from nba.daily.predict import MIN_STD
from nba.daily.store import NO_PLAYER, ensure_tables
from nba.props import full_support as fsup
from nba.props.forward import QUANTILE_TAUS
from nba.truth.metrics import brier_score, log_loss

WIN_TARGET = "win_prob_home"
#: LLM shadow rows carry p_over, not a predictive mean/std: settled by their own companion scorer
#: (docs/LLM_SHADOW.md), never by this path.
SHADOW_LLM_MODEL = "llm_shadow"
#: A game is settled only this long after its scheduled tip. The games feed returns in-progress
#: games with live points (2026-10-20: BOS@DET stored 25-23 an hour after tip and was scored on
#: it); scores are written once per key, so a partial result would stand for good.
SETTLE_AFTER_TIP_HOURS = 4
#: ...and a winner row is graded only when the game has a box score behind its final score: at
#: least this many player_game_stats rows with minutes > 0 (a real game has 18-26; the games feed
#: stored PHI@NYK at 63-62 an hour after the last pretip run, and points alone are not a result).
MIN_PLAYED_ROWS = 10

#: SQL fragment: the game has >= MIN_PLAYED_ROWS played box rows. ``{g}`` = game-id column.
_HAS_BOX_SQL = (
    "(SELECT count(*) FROM player_game_stats b WHERE b.game_id = {g} AND b.minutes > 0) >= "
    + str(MIN_PLAYED_ROWS)
)
#: SQL fragment: the box score agrees with the game score (``g`` = the games row): there are box
#: rows, and each team's summed points (a DNP row's NULL counts as 0) equal the game's points.
#: Closes the in-progress box cached for good (fetch_cached never re-pulls) and a partial games
#: feed score after the box is final (OKC@SAS 44-65, 2026-10-21). Applies to every target.
_BOX_AGREES_SQL = (
    "(SELECT count(*) > 0"
    " AND coalesce(sum(CASE WHEN b.team_id = g.home_team THEN coalesce(b.pts, 0) END), 0)"
    " = g.home_pts"
    " AND coalesce(sum(CASE WHEN b.team_id = g.away_team THEN coalesce(b.pts, 0) END), 0)"
    " = g.away_pts FROM player_game_stats b WHERE b.game_id = g.game_id)"
)
#: A refused key whose tip was at least this long ago should long be final and gradable: alert.
STALE_REFUSAL_HOURS = 24
#: SQL fragment: the key was withdrawn (misplaced player, docs/DAILY_PIPELINE.md "Withdrawals").
_WITHDRAWN_SQL = (
    "EXISTS (SELECT 1 FROM forward_withdrawals w WHERE w.game_id = {a}.game_id "
    "AND w.model_name = {a}.model_name AND w.target = {a}.target AND w.player_id = {a}.player_id)"
)


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
            FROM forward_predictions
            WHERE model_name <> ?)
        SELECT l.game_id, l.model_name, l.version, l.target, l.player_id, l.made_at,
               l.prediction, g.game_date, g.season, g.home_pts, g.away_pts
        FROM latest l JOIN games g USING (game_id)
        WHERE l.rn = 1 AND g.home_pts > 0 AND g.away_pts > 0
          AND (l.target <> ? OR {has_box})
          AND {agrees}
          AND NOT {withdrawn}
          AND l.tipoff + ? * INTERVAL 1 HOUR <= ?
          AND NOT EXISTS (SELECT 1 FROM forward_scores s
              WHERE s.game_id = l.game_id AND s.model_name = l.model_name
                AND s.target = l.target AND s.player_id = l.player_id)
        """.format(  # noqa: S608 - fragments are module constants
            has_box=_HAS_BOX_SQL.format(g="l.game_id"),
            agrees=_BOX_AGREES_SQL,
            withdrawn=_WITHDRAWN_SQL.format(a="l"),
        ),
        [SHADOW_LLM_MODEL, WIN_TARGET, SETTLE_AFTER_TIP_HOURS, scored_at],
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
    out = {"win_scored": n_win, "prop_scored": n_prop, "prop_dnp": n_dnp}
    out.update(refusal_counts(refused_games(con, scored_at), scored_at))
    return out


REFUSAL_REASONS = ("no_box", "thin_box", "box_mismatch")

_REFUSED_SQL = """
WITH keys AS (
    SELECT DISTINCT p.game_id, p.target = ? AS is_win, p.tipoff
    FROM forward_predictions p
    WHERE p.model_name <> ?
      AND (NOT EXISTS (SELECT 1 FROM forward_scores s
               WHERE s.game_id = p.game_id AND s.model_name = p.model_name
                 AND s.target = p.target AND s.player_id = p.player_id)
           OR (p.model_name IN ({models}) AND NOT EXISTS (SELECT 1 FROM forward_scores_elig e
               WHERE e.game_id = p.game_id AND e.model_name = p.model_name
                 AND e.target = p.target AND e.player_id = p.player_id)))
      AND NOT {withdrawn}
),
bx AS (
    SELECT b.game_id, count(*) AS n_rows, count(*) FILTER (WHERE b.minutes > 0) AS n_played,
           coalesce(sum(CASE WHEN b.team_id = g.home_team THEN coalesce(b.pts, 0) END), 0) AS hs,
           coalesce(sum(CASE WHEN b.team_id = g.away_team THEN coalesce(b.pts, 0) END), 0) AS aws
    FROM player_game_stats b JOIN games g USING (game_id) GROUP BY b.game_id
)
SELECT k.game_id, min(k.tipoff) AS tipoff, bool_or(k.is_win) AS has_win,
       coalesce(any_value(x.n_rows), 0) AS n_rows, coalesce(any_value(x.n_played), 0) AS n_played,
       any_value(x.hs = g.home_pts AND x.aws = g.away_pts) AS agrees
FROM keys k JOIN games g USING (game_id) LEFT JOIN bx x USING (game_id)
WHERE g.home_pts > 0 AND g.away_pts > 0 AND k.tipoff + ? * INTERVAL 1 HOUR <= ?
GROUP BY k.game_id ORDER BY k.game_id
"""


def refused_games(con: duckdb.DuckDBPyConnection, now: datetime) -> list[tuple[str, str, datetime]]:
    """(game_id, reason, tipoff) for each game with an unscored key that the box guards refuse.

    Reason, in order: ``no_box`` (no player_game_stats rows), ``thin_box`` (a winner key and fewer
    than MIN_PLAYED_ROWS played rows), ``box_mismatch`` (a team's summed box points differ from
    the game score). Only games past the 4 h guard with a final-looking score are listed; a
    refused key is never written and stays pending for the next settle."""
    ensure_tables(con)
    con.execute(ELIG_DDL)
    sql = _REFUSED_SQL.format(  # noqa: S608 - fragments are module constants
        models=",".join("?" for _ in PREREG_MODELS), withdrawn=_WITHDRAWN_SQL.format(a="p")
    )
    rows = con.execute(
        sql, [WIN_TARGET, SHADOW_LLM_MODEL, *PREREG_MODELS, SETTLE_AFTER_TIP_HOURS, now]
    ).fetchall()
    out: list[tuple[str, str, datetime]] = []
    for gid, tip, has_win, n_rows, n_played, agrees in rows:
        if n_rows == 0:
            reason = "no_box"
        elif has_win and n_played < MIN_PLAYED_ROWS:
            reason = "thin_box"
        elif not agrees:
            reason = "box_mismatch"
        else:
            continue
        out.append((gid, reason, tip))
    return out


def refusal_counts(refused: list[tuple[str, str, datetime]], now: datetime) -> dict[str, int]:
    """Distinct refused games per reason, plus ``refused_stale`` (tipped >= 24 h before ``now``)."""
    counts = {f"refused_{r}": sum(1 for _, x, _ in refused if x == r) for r in REFUSAL_REASONS}
    cut = now - timedelta(hours=STALE_REFUSAL_HOURS)
    counts["refused_stale"] = sum(1 for _, _, tip in refused if tip <= cut)
    return counts


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
    "props_context_residual_t15",
    "props_context_residual_int",
    "props_context_residual_lt",
    "rung0_injury_elo",
    "rung0_mov_elo",
)
T30_MODEL = "props_context_residual_t30"
T15_MODEL = "props_context_residual_t15"
LEAD_MINUTES: dict[str, int] = {T30_MODEL: 30, T15_MODEL: 15}
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
               WHEN model_name = ?
               -- T-15 arm (docs/prereg/FORWARD_T15_2026_27.md rule 5): same, with a 15 min lead
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
  AND (l.target <> ? OR {has_box})
  AND {agrees}
  AND l.tipoff + ? * INTERVAL 1 HOUR <= ?
  AND NOT EXISTS (SELECT 1 FROM forward_scores_elig s
      WHERE s.game_id = l.game_id AND s.model_name = l.model_name
        AND s.target = l.target AND s.player_id = l.player_id)
"""


def settle_eligible_pending(con: duckdb.DuckDBPyConnection, scored_at: datetime) -> dict[str, int]:
    """Append a ``forward_scores_elig`` row per eligible-prediction key whose game is complete.

    Idempotent. Props whose stored ``p_ge_full`` is missing or does not reach 99.9% of the mass
    are written with ``status = 'unscorable'`` (counted by the checkpoint, never dropped)."""
    ensure_tables(con)
    con.execute(ELIG_DDL)
    sql = _SELECT_ELIGIBLE.format(
        models=",".join("?" for _ in PREREG_MODELS),
        has_box=_HAS_BOX_SQL.format(g="l.game_id"),
        agrees=_BOX_AGREES_SQL,
    )
    params: list[object] = [
        *PREREG_MODELS,
        T30_MODEL,
        LEAD_MINUTES[T30_MODEL],
        T15_MODEL,
        LEAD_MINUTES[T15_MODEL],
        DEFAULT_LEAD_MINUTES,
        WIN_TARGET,
        SETTLE_AFTER_TIP_HOURS,
        scored_at,
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
    counts.update(refusal_counts(refused_games(con, scored_at), scored_at))
    return counts


def _f(v: object) -> float | None:
    return None if v is None else float(v)  # type: ignore[arg-type]
