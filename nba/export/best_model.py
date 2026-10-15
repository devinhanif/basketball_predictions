"""Best-evidenced model per stat for the app (DECISIONS 2026-10-14 21:30), read-only.

The frozen scoring is untouched: this module only READS the shadow tables the daily job writes
(``forward_avg_shadow``, AVG_SHADOW_LIVE_2; ``forward_pplay_shadow``, P(play) arm R) and decides,
per (game, player, stat), which stored row the app shows. Nothing is written to ``nba.duckdb``.

Primary order for a prop (one ``is_primary`` row per (game, player, stat)):

1. postseason (game id ``004`` playoffs, ``005`` play-in): production ``props_context_residual``
   (AVG is flat to worse there, T479)                                   -> ``postseason``
2. an AVG row made strictly before tip whose bundle is fresh            -> ``avg``
3. an AVG row whose bundle is stale                                     -> ``fallback_stale``
4. no AVG row (or an unreadable one) before tip                         -> ``fallback_missing``
   (2-4 fall back to the latest pre-tip production row; if there is none, no primary is marked)

Stale bundle, from the AVG rule's refit schedule (docs/prereg/AVG_SHADOW_LIVE_2.md, "Refit
staleness": monthly warm refit, installed by 12:00 CT on the 1st). A row is fresh iff ALL hold:
the writer did not flag it (``stale_model``); ``month_block`` equals the calendar month of the
game date (ET); ``installed_at <= made_at``; ``fit_through`` is strictly before the first day of
that month. Anything else is stale and ``freshness_reason`` names the first failing test.

P(play): the latest pre-tip ``forward_pplay_shadow`` row of the (game, player); R when its
``p_play_r`` is not NULL (``p_play_source = r``), else the production value
(``production``, ``p_play_detail`` carries R's ``r_reason`` or ``no_r_row``). An older R is never
reused when the newest row has none (a player ruled out later must not keep an old R).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from typing import Any

import duckdb
import numpy as np

from nba.export import guard
from nba.props import full_support
from nba.props.config import THRESHOLDS

AVG_MODEL_NAME = "props_avg_live2"
AVG_VERSION = "avg-live2"
AVG_TABLE = "forward_avg_shadow"
PPLAY_TABLE = "forward_pplay_shadow"
PRODUCTION_PROP_MODEL = "props_context_residual"
POSTSEASON_PREFIXES = ("004", "005")
N_PMF = 199
N_Q = 19

SRC_AVG = "avg"
SRC_MISSING = "fallback_missing"
SRC_STALE = "fallback_stale"
SRC_POSTSEASON = "postseason"


def is_postseason(game_id: str) -> bool:
    return game_id.startswith(POSTSEASON_PREFIXES)


def avg_freshness(
    *,
    made_at: datetime,
    installed_at: datetime | None,
    fit_through: date | None,
    month_block: str | None,
    stale_flag: bool | None,
    game_date: date,
) -> tuple[str, str | None]:
    """``("fresh", None)`` or ``("stale", reason)`` by the module docstring's four tests."""
    if stale_flag:
        return "stale", "writer_flag"
    if month_block != game_date.strftime("%Y-%m"):
        return "stale", "month_block_mismatch"
    if installed_at is None or installed_at > made_at:
        return "stale", "installed_after_made"
    if fit_through is None or fit_through >= game_date.replace(day=1):
        return "stale", "fit_through_not_before_month"
    return "fresh", None


def avg_to_prediction(stat: str, pmf: list[float], q19: list[float]) -> dict[str, Any]:
    """The production prop-row shape from an AVG row: the 199 equal-mass values play the part of
    the context model's quantile samples (``nba.props.forward._ctx_row``), so ``p_ge`` and
    ``p_ge_full`` are the same functions of the same kind of object; ``q_grid`` is the scored
    19-quantile object. Conditional on playing, like production."""
    s = np.asarray(pmf, dtype=float)
    thr = THRESHOLDS[stat]
    return {
        "mean": float(s.mean()), "std": float(np.std(s)),
        "q10": float(q19[1]), "q50": float(q19[9]), "q90": float(q19[17]),
        "q_grid": [float(v) for v in q19],
        "p_ge": {str(t): float(np.mean(s > t - 0.5)) for t in thr},
        "p_ge_full": full_support.from_samples(s, stat),
    }  # fmt: skip


def _table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()
    return bool(row and row[0])


def _valid(vals: Any, n: int) -> list[float] | None:
    try:
        out = json.loads(vals) if isinstance(vals, str) else vals
        if (
            isinstance(out, list)
            and len(out) == n
            and all(isinstance(v, int | float) and np.isfinite(v) for v in out)
        ):
            return [float(v) for v in out]
    except (TypeError, ValueError):
        pass
    return None


def avg_rows(
    con: duckdb.DuckDBPyConnection,
    lo: datetime,
    hi: datetime,
    seasons: tuple[int, ...],
    first: date,
    last: date,
    et_date: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int]:
    """Pre-tip AVG shadow rows as prop-grid dicts, plus the games they belong to.

    Returns ``(rows, games, n_post_tip, n_unreadable)``. A row with ``made_at >= tipoff_utc`` is
    never exported (the writer refuses them; this is the second lock). A row whose pmf or
    quantiles do not parse (not 199 / 19 finite numbers) is dropped and counted, so the stat falls
    back instead of showing a broken distribution."""
    if not _table_exists(con, AVG_TABLE):
        return [], [], 0, 0
    marks = ",".join("?" for _ in seasons)
    sql = f"""
        SELECT a.made_at, a.run_id, a.game_id, a.player_id, a.stat, a.tipoff_utc, a.model_id,
               a.fit_through, a.month_block, a.installed_at, a.stale_model,
               CAST(a.avg_pmf199 AS VARCHAR), CAST(a.avg_q19 AS VARCHAR),
               g.game_date, g.home_team, g.away_team,
               {guard.season_sql("g", "a.game_id")} AS season, g.home_pts, g.away_pts
        FROM {AVG_TABLE} a LEFT JOIN games g ON g.game_id = a.game_id
        WHERE a.tipoff_utc >= ? AND a.tipoff_utc < ?
          AND a.game_id NOT LIKE '001%' AND a.game_id NOT LIKE '003%'
          AND {guard.season_sql("g", "a.game_id")} IN ({marks})
        ORDER BY a.game_id, a.player_id, a.stat, a.made_at, a.run_id
    """  # noqa: S608 - identifiers are constants
    raw = con.execute(sql, [lo, hi, *seasons]).fetchall()
    rows: list[dict[str, Any]] = []
    games: dict[str, dict[str, Any]] = {}
    post_tip = bad = 0
    latest: dict[tuple[str, int, str], tuple[datetime, str]] = {}
    for r in raw:
        made_at, tip = r[0], r[5]
        gdate = r[13] or et_date(tip)
        if made_at >= tip:
            post_tip += 1
            continue
        if not (first <= gdate <= last) or r[4] not in THRESHOLDS:
            continue
        pmf, q19 = _valid(r[11], N_PMF), _valid(r[12], N_Q)
        if pmf is None or q19 is None:
            bad += 1
            continue
        stat, gid, pid = str(r[4]), str(r[2]), int(r[3])
        fresh, why = avg_freshness(
            made_at=made_at, installed_at=r[9], fit_through=r[7], month_block=r[8],
            stale_flag=r[10], game_date=gdate,
        )  # fmt: skip
        pred = avg_to_prediction(stat, pmf, q19)
        key = (gid, pid, stat)
        if key not in latest or (made_at, r[1]) > latest[key]:
            latest[key] = (made_at, r[1])
        rows.append({
            "run_id": r[1], "made_at": made_at, "game_id": gid, "tipoff": tip,
            "model_name": AVG_MODEL_NAME, "version": AVG_VERSION, "player_id": pid,
            "stat": stat, "lead_minutes": (tip - made_at).total_seconds() / 60.0,
            "comparison_only": False, "prediction_json": json.dumps(pred, sort_keys=True),
            "mean": pred["mean"], "std": pred["std"], "p_play": None, "proj_minutes": None,
            "q10": pred["q10"], "q50": pred["q50"], "q90": pred["q90"],
            "q_grid": pred["q_grid"], "p_ge": pred["p_ge"],
            "p_ge_full_counts": pred["p_ge_full"]["c"], "p_ge_full_n": pred["p_ge_full"]["n"],
            "stamp_weights_sha256": r[6], "stamp_weights_scope": "avg weights bundle (model_id)",
            "stamp_data_version": None, "stamp_fit_through": r[7], "stamp_n_train": None,
            "stamp_git_sha": None, "stamp_git_dirty": None, "stamp_stamped_at": r[9],
            "source_table": AVG_TABLE, "bundle_id": r[6], "bundle_made_at": r[9],
            "bundle_month": r[8], "freshness": fresh, "freshness_reason": why,
        })  # fmt: skip
        games.setdefault(
            gid,
            {"game_id": gid, "season": int(r[16]), "game_date": gdate, "tipoff": tip,
             "home_team": r[14], "away_team": r[15], "final": bool(r[17] and r[18]),
             "home_pts": r[17] or None, "away_pts": r[18] or None},
        )  # fmt: skip
    for row in rows:
        row["is_latest"] = latest[(row["game_id"], row["player_id"], row["stat"])] == (
            row["made_at"], row["run_id"],
        )  # fmt: skip
    return rows, list(games.values()), post_tip, bad


def pplay_rows(
    con: duckdb.DuckDBPyConnection,
    lo: datetime,
    hi: datetime,
    seasons: tuple[int, ...],
    first: date,
    last: date,
    et_date: Any,
) -> list[dict[str, Any]]:
    """Latest pre-tip ``forward_pplay_shadow`` row per (game, player), with the primary choice."""
    if not _table_exists(con, PPLAY_TABLE):
        return []
    marks = ",".join("?" for _ in seasons)
    sql = f"""
        SELECT p.run_id, p.made_at, p.game_id, p.tipoff, p.player_id, p.team_id, p.model_version,
               p.p_play_prod, p.p_play_r, p.r_reason, p.proj_minutes, p.train_through, p.n_train,
               g.game_date
        FROM {PPLAY_TABLE} p LEFT JOIN games g ON g.game_id = p.game_id
        WHERE p.tipoff >= ? AND p.tipoff < ?
          AND p.game_id NOT LIKE '001%' AND p.game_id NOT LIKE '003%'
          AND {guard.season_sql("g", "p.game_id")} IN ({marks})
        ORDER BY p.game_id, p.player_id, p.made_at, p.run_id
    """  # noqa: S608 - identifiers are constants
    best: dict[tuple[str, int], tuple[Any, ...]] = {}
    for r in con.execute(sql, [lo, hi, *seasons]).fetchall():
        made_at, tip = r[1], r[3]
        gdate = r[13] or et_date(tip)
        if made_at >= tip or not (first <= gdate <= last):
            continue
        best[(str(r[2]), int(r[4]))] = r  # ascending order: the newest pre-tip row wins
    out: list[dict[str, Any]] = []
    for (gid, pid), r in sorted(best.items()):
        r_val = r[8]
        use_r = r_val is not None and np.isfinite(float(r_val))
        prod = r[7]
        out.append({
            "game_id": gid, "player_id": pid, "team_id": r[5], "run_id": r[0],
            "made_at": r[1], "tipoff": r[3],
            "lead_minutes": (r[3] - r[1]).total_seconds() / 60.0,
            "p_play_primary": float(r_val) if use_r else (None if prod is None else float(prod)),
            "p_play_source": "r" if use_r else "production",
            "p_play_detail": None if use_r else (r[9] or "no_r_row"),
            "p_play_r": None if r_val is None else float(r_val),
            "p_play_prod": None if prod is None else float(prod),
            "model_version": r[6], "proj_minutes": r[10], "r_train_through": r[11],
            "r_n_train": r[12],
        })  # fmt: skip
    return out


def _latest(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return max(rows, key=lambda p: (p["made_at"], p["run_id"])) if rows else None


def mark_primary(props: list[dict[str, Any]], pplay: list[dict[str, Any]]) -> None:
    """Fill ``is_primary`` / ``primary_source`` / ``primary_detail`` and ``p_play_primary`` /
    ``p_play_source`` on every prop row, in place. Rows are only read, never invented."""
    pp = {(p["game_id"], p["player_id"]): p for p in pplay}
    prod_minutes: dict[tuple[str, int], float] = {}
    groups: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for p in props:
        p.setdefault("source_table", "forward_predictions")
        p.setdefault("bundle_id", p.get("stamp_weights_sha256"))
        p.setdefault("bundle_made_at", p.get("stamp_stamped_at"))
        p.setdefault("bundle_month", None)
        p.setdefault("freshness", "n/a")
        p.setdefault("freshness_reason", None)
        p["is_primary"], p["primary_source"], p["primary_detail"] = False, None, None
        groups[(p["game_id"], p["player_id"], p["stat"])].append(p)
    for (gid, pid, _stat), rows in groups.items():
        prod = _latest(
            [
                r
                for r in rows
                if r["model_name"] == PRODUCTION_PROP_MODEL and not r["comparison_only"]
            ]
        )
        avg = _latest([r for r in rows if r["model_name"] == AVG_MODEL_NAME])
        chosen: dict[str, Any] | None
        if is_postseason(gid):
            chosen, source, detail = prod, SRC_POSTSEASON, "AVG flat in the postseason (T479)"
        elif avg is None:
            chosen, source, detail = prod, SRC_MISSING, "no AVG row before tip"
        elif avg["freshness"] == "fresh":
            chosen, source, detail = avg, SRC_AVG, None
        else:
            chosen, source = prod, SRC_STALE
            detail = f"bundle stale: {avg['freshness_reason']}"
        if chosen is not None:
            chosen["is_primary"], chosen["primary_source"] = True, source
            chosen["primary_detail"] = detail
        if prod is not None and prod.get("proj_minutes") is not None:
            prod_minutes[(gid, pid)] = prod["proj_minutes"]
    for p in props:
        key = (p["game_id"], p["player_id"])
        row = pp.get(key)
        if row is not None and row["p_play_primary"] is not None:
            p["p_play_primary"], p["p_play_source"] = row["p_play_primary"], row["p_play_source"]
        else:  # no shadow row: the row's own stored value is the production number
            p["p_play_primary"] = p.get("p_play")
            p["p_play_source"] = "production" if p.get("p_play") is not None else None
        if p["model_name"] == AVG_MODEL_NAME:  # AVG has no P(play) of its own
            p["p_play"] = p["p_play_primary"]
            p["proj_minutes"] = prod_minutes.get(key, row["proj_minutes"] if row else None)
