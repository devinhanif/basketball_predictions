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
5. an AVG row older than the production row it competes with, unless the heartbeat shows the
   AVG run finished ``ok`` for that production run (write-on-change leaves old rows in place)
                                                                        -> ``fallback_stale``
                                                                           (``avg_run_behind_production``)
   (2-4 fall back to the latest pre-tip production row; if there is none, no primary is marked)

Stale bundle, from the AVG rule's refit schedule (docs/prereg/AVG_SHADOW_LIVE_2.md, "Refit
staleness": monthly warm refit, installed by 12:00 CT on the 1st). A row is fresh iff ALL hold:
the writer did not flag it (``stale_model``); ``month_block`` equals the calendar month of the
game date (ET); ``installed_at <= made_at``; ``fit_through`` is strictly before the first day of
that month. Anything else is stale and ``freshness_reason`` names the first failing test.

P(play): the latest pre-tip ``forward_pplay_shadow`` row of the (game, player); R when its
``p_play_r`` is not NULL (``p_play_source = r``), else the production value
(``production``, ``p_play_detail`` carries R's ``r_reason`` or ``no_r_row``); when the newest row
says ``out_on_report`` the value is 0.0 with ``p_play_source = out_on_report``. An older R is never
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
AVG_HEARTBEAT_TABLE = "forward_avg_heartbeat"
OUT_ON_REPORT = "out_on_report"
PPLAY_TABLE = "forward_pplay_shadow"
PRODUCTION_PROP_MODEL = "props_context_residual"
POSTSEASON_PREFIXES = ("004", "005")
N_PMF = 199
N_Q = 19

SRC_AVG = "avg"
SRC_MISSING = "fallback_missing"
SRC_STALE = "fallback_stale"
SRC_POSTSEASON = "postseason"
SRC_NON_REGULAR = "non_regular_season"
REGULAR_PREFIX = "002"


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


def _assert_seasons(seasons: set[int], allowed: tuple[int, ...]) -> None:
    bad = {x for x in seasons if x not in allowed or x == guard.HOLDOUT_SEASON}
    if bad:  # pragma: no cover - the SQL filter makes this unreachable; kept as the belt
        raise guard.ExportRefused(f"rows from seasons {sorted(bad)} reached the export")


def ok_avg_runs(con: duckdb.DuckDBPyConnection) -> set[str]:
    """Run ids whose AVG inference finished with status ``ok`` (``forward_avg_heartbeat``)."""
    if not _table_exists(con, AVG_HEARTBEAT_TABLE):
        return set()
    return {
        str(r[0])
        for r in con.execute(
            f"SELECT run_id FROM {AVG_HEARTBEAT_TABLE} WHERE status = 'ok'"  # noqa: S608
        ).fetchall()
    }


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
               CAST(a.a0_q19 AS VARCHAR), a.a0_source,
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
        gdate = r[15] or et_date(tip)
        if made_at >= tip:
            post_tip += 1
            continue
        if not (first <= gdate <= last) or r[4] not in THRESHOLDS:
            continue
        pmf, q19 = _valid(r[11], N_PMF), _valid(r[12], N_Q)
        a0 = _valid(r[13], N_Q)
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
            "comparison_only": True, "prediction_json": json.dumps(pred, sort_keys=True),
            "mean": pred["mean"], "std": pred["std"], "p_play": None, "proj_minutes": None,
            "q10": pred["q10"], "q50": pred["q50"], "q90": pred["q90"],
            "q_grid": pred["q_grid"], "p_ge": pred["p_ge"],
            "p_ge_full_counts": pred["p_ge_full"]["c"], "p_ge_full_n": pred["p_ge_full"]["n"],
            "_pmf199": pmf,  # private: the rung_cal export reads it, then drops it
            "stamp_weights_sha256": r[6], "stamp_weights_scope": "avg weights bundle (model_id)",
            "stamp_data_version": None, "stamp_fit_through": r[7], "stamp_n_train": None,
            "stamp_git_sha": None, "stamp_git_dirty": None, "stamp_stamped_at": r[9],
            "_a0_q19": a0, "_a0_source": r[14], "source_table": AVG_TABLE, "bundle_id": r[6],
            "bundle_made_at": r[9],
            "bundle_month": r[8], "freshness": fresh, "freshness_reason": why,
        })  # fmt: skip
        games.setdefault(
            gid,
            {"game_id": gid, "season": int(r[18]), "game_date": gdate, "tipoff": tip,
             "home_team": r[16], "away_team": r[17], "final": bool(r[19] and r[20]),
             "home_pts": r[19] or None, "away_pts": r[20] or None},
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
               g.game_date, {guard.season_sql("g", "p.game_id")} AS season
        FROM {PPLAY_TABLE} p LEFT JOIN games g ON g.game_id = p.game_id
        WHERE p.tipoff >= ? AND p.tipoff < ?
          AND p.game_id NOT LIKE '001%' AND p.game_id NOT LIKE '003%'
          AND {guard.season_sql("g", "p.game_id")} IN ({marks})
        ORDER BY p.game_id, p.player_id, p.made_at, p.run_id
    """  # noqa: S608 - identifiers are constants
    best: dict[tuple[str, int], tuple[Any, ...]] = {}
    raw = con.execute(sql, [lo, hi, *seasons]).fetchall()
    _assert_seasons({int(r[14]) for r in raw if r[14] is not None}, seasons)
    for r in raw:
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
        out_rep = not use_r and r[9] == OUT_ON_REPORT
        value = (
            float(r_val) if use_r else (0.0 if out_rep else (None if prod is None else float(prod)))
        )
        source = "r" if use_r else (OUT_ON_REPORT if out_rep else "production")
        out.append({
            "game_id": gid, "player_id": pid, "team_id": r[5], "run_id": r[0],
            "made_at": r[1], "tipoff": r[3],
            "lead_minutes": (r[3] - r[1]).total_seconds() / 60.0,
            "p_play_primary": value, "p_play_source": source,
            "p_play_detail": None if use_r else (r[9] or "no_r_row"),
            "p_play_r": None if r_val is None else float(r_val),
            "p_play_prod": None if prod is None else float(prod),
            "model_version": r[6], "proj_minutes": r[10], "r_train_through": r[11],
            "r_n_train": r[12],
        })  # fmt: skip
    return out


def _latest(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return max(rows, key=lambda p: (p["made_at"], p["run_id"])) if rows else None


def _behind(avg: dict[str, Any], prod: dict[str, Any] | None, ok_runs: set[str] | None) -> bool:
    """AVG row older than the production row it competes with, and no ok heartbeat for that run."""
    return (
        prod is not None
        and avg["made_at"] < prod["made_at"]
        and prod["run_id"] not in (ok_runs or ())
    )


def _inputs_changed(
    avg: dict[str, Any], rows: list[dict[str, Any]], prod: dict[str, Any] | None
) -> bool:
    """True when the A0 grid the AVG row was built from (``a0_q19``, from the row named by
    ``a0_source``) is no longer the current pre-tip production grid: production inputs moved
    after AVG was made. A missing or unreadable A0 on either side counts as changed."""
    src = avg.get("_a0_source")
    ref = (
        _latest([r for r in rows if r["model_name"] == src and r["model_name"] != AVG_MODEL_NAME])
        if src
        else prod
    )
    a0, cur = avg.get("_a0_q19"), (ref or {}).get("q_grid")
    if a0 is None or cur is None or len(a0) != len(cur):
        return ref is not None or a0 is not None
    return not np.allclose(np.asarray(a0, float), np.asarray(cur, float), atol=1e-6, rtol=0)


def reconcile_pplay(pplay: list[dict[str, Any]], props: list[dict[str, Any]]) -> int:
    """Use R only if its shadow row is not behind production: ``made_at`` >= the latest pre-tip
    production prop row's, or its stored ``p_play_prod`` equals that row's ``p_play``. Otherwise
    ``p_play_primary`` is production's value (``production``, ``r_run_behind_production``), because
    the shadow step can fail while production moves on. Mutates ``pplay``; returns the count."""
    prod: dict[tuple[str, int], dict[str, Any]] = {}
    for p in props:
        if p["model_name"] == PRODUCTION_PROP_MODEL and not p["comparison_only"]:
            k = (p["game_id"], p["player_id"])
            if k not in prod or (p["made_at"], p["run_id"]) > (
                prod[k]["made_at"],
                prod[k]["run_id"],
            ):
                prod[k] = p
    n = 0
    for row in pplay:
        cur = prod.get((row["game_id"], row["player_id"]))
        if cur is None or cur.get("p_play") is None:
            continue
        same = row["p_play_prod"] is not None and abs(row["p_play_prod"] - cur["p_play"]) < 1e-9
        if row["made_at"] >= cur["made_at"] or same:
            continue
        row["p_play_primary"], row["p_play_source"] = float(cur["p_play"]), "production"
        row["p_play_detail"] = "r_run_behind_production"
        n += 1
    return n


def mark_primary(
    props: list[dict[str, Any]], pplay: list[dict[str, Any]], ok_runs: set[str] | None = None
) -> None:
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
        p["lines_run_id"] = p["run_id"]
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
        elif not gid.startswith(REGULAR_PREFIX):
            chosen, source = prod, SRC_NON_REGULAR
            detail = "AVG serves regular-season (002) games only"
        elif avg is None:
            chosen, source, detail = prod, SRC_MISSING, "no AVG row before tip"
        elif avg["freshness"] == "fresh" and not _behind(avg, prod, ok_runs):
            if _inputs_changed(avg, rows, prod):
                chosen, source, detail = prod, SRC_STALE, "avg_inputs_changed"
            else:
                chosen, source, detail = avg, SRC_AVG, None
        elif avg["freshness"] == "fresh":
            chosen, source = prod, SRC_STALE
            detail = "avg_run_behind_production"
        else:
            chosen, source = prod, SRC_STALE
            detail = f"bundle stale: {avg['freshness_reason']}"
        if chosen is not None:
            chosen["is_primary"], chosen["primary_source"] = True, source
            if chosen["model_name"] == AVG_MODEL_NAME:
                chosen["comparison_only"] = False
                if prod is not None:  # quotes are the production counterpart's (same slate run)
                    chosen["lines_run_id"] = prod["run_id"]
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
