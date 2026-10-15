"""Live inputs for ``python -m nba.parlay top``: stored forecasts and pre-tip book quotes -> legs.

Read-only: ``nba.duckdb`` and the capture store ``market_asof.duckdb`` are opened by the caller with
``read_only=True``; nothing is written and nothing is fetched. The app export's primary source is
the AVG shadow row when a fresh pre-tip row exists, else the production
``props_context_residual`` row.
The CAL map was fitted on PRODUCTION P(over), so every leg's ``p_raw`` / ``p_cal`` come from the
production row (``model_source`` production); a fresh AVG row's P(over) is carried as ``p_avg_raw``
for display and never ranks; no production row, no leg. ``choose_primary`` repeats the rules of
``nba.export.best_model.mark_primary`` (that module may not be imported from the parlay package: the
live-path layering test forbids it) and ``tests/parlay/test_top_source.py`` pins the two together.

Quotes follow ``nba.export.reader.prop_lines`` but keep only each book's newest snapshot per
market and player (a withdrawn line is never selectable): main-line markets only,
``received_at`` strictly
before tip and before the vendor start, and not later than the forecast row paired with them.
Alternate ladders are not used. A leg with no CAL map for its book, no stored starter rate, a whole
number line, or a P(play) below ``MIN_P_PLAY`` is dropped and counted, never guessed.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import numpy as np

from nba.explain import names
from nba.parlay import line_cal as lc
from nba.parlay.top_parlays import LegCand, make_leg
from nba.props import full_support
from nba.props.config import THRESHOLDS

ET = ZoneInfo("America/New_York")
HOLDOUT_SEASON = 2025
PROPS_MODEL = "props_context_residual"
AVG_MODEL = "props_avg_live2"
AVG_TABLE = "forward_avg_shadow"
HEARTBEAT_TABLE = "forward_avg_heartbeat"
LINES_TABLE = "live_book_odds"
STATS = ("pts", "reb", "ast", "fg3m")
MIN_P_PLAY = 0.90  # a leg on a player under this is not offered (a sit voids or loses the leg)
MARKET_STAT = {
    "player_points": "pts",
    "player_rebounds": "reb",
    "player_assists": "ast",
    "player_threes": "fg3m",
}
Q_IDX = {"q25": 4, "q50": 9, "q75": 14}  # the 19-point grid is tau = 0.05 .. 0.95
N_PMF, N_Q = 199, 19


class SourceError(ValueError):
    """The request cannot be served (holdout season, unreadable store)."""


def slate_season(d: date) -> int:
    return d.year if d.month >= 8 else d.year - 1


def et_date(ts: datetime) -> date:
    return ts.replace(tzinfo=UTC).astimezone(ET).date()


def _jloads(v: Any) -> dict[str, Any]:
    out = json.loads(v) if isinstance(v, str) else v
    return out if isinstance(out, dict) else {}


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


# ------------------------------------------------------------------ primary source (port)


def avg_freshness(
    *,
    made_at: datetime,
    installed_at: datetime | None,
    fit_through: date | None,
    month_block: str | None,
    stale_flag: bool | None,
    game_date: date,
) -> tuple[str, str | None]:
    if stale_flag:
        return "stale", "writer_flag"
    if month_block != game_date.strftime("%Y-%m"):
        return "stale", "month_block_mismatch"
    if installed_at is None or installed_at > made_at:
        return "stale", "installed_after_made"
    if fit_through is None or fit_through >= game_date.replace(day=1):
        return "stale", "fit_through_not_before_month"
    return "fresh", None


def _latest(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return max(rows, key=lambda p: (p["made_at"], p["run_id"])) if rows else None


def _behind(avg: dict[str, Any], prod: dict[str, Any] | None, ok_runs: set[str]) -> bool:
    return prod is not None and avg["made_at"] < prod["made_at"] and prod["run_id"] not in ok_runs


def _inputs_changed(
    avg: dict[str, Any], rows: list[dict[str, Any]], prod: dict[str, Any] | None
) -> bool:
    src = avg.get("a0_source")
    ref = (
        _latest([r for r in rows if r["model_name"] == src and r["model_name"] != AVG_MODEL])
        if src
        else prod
    )
    a0, cur = avg.get("a0_q19"), (ref or {}).get("q_grid")
    if a0 is None or cur is None or len(a0) != len(cur):
        return ref is not None or a0 is not None
    return not np.allclose(np.asarray(a0, float), np.asarray(cur, float), atol=1e-6, rtol=0)


def choose_primary(
    gid: str, rows: list[dict[str, Any]], ok_runs: set[str]
) -> tuple[dict[str, Any] | None, str, str | None]:
    """(chosen row, source, detail) for one (game, player, stat); the rules of
    ``nba.export.best_model.mark_primary``. ``rows`` hold production, AVG and any other model's
    pre-tip rows with ``model_name``, ``comparison_only``, ``run_id``, ``made_at`` and
    ``freshness``."""
    prod = _latest([r for r in rows if r["model_name"] == PROPS_MODEL and not r["comparison_only"]])
    avg = _latest([r for r in rows if r["model_name"] == AVG_MODEL])
    if gid.startswith(("004", "005")):
        return prod, "postseason", "AVG flat in the postseason (T479)"
    if not gid.startswith("002"):
        return prod, "non_regular_season", "AVG serves regular-season (002) games only"
    if avg is None:
        return prod, "fallback_missing", "no AVG row before tip"
    if avg["freshness"] == "fresh" and not _behind(avg, prod, ok_runs):
        if _inputs_changed(avg, rows, prod):
            return prod, "fallback_stale", "avg_inputs_changed"
        return avg, "avg", None
    if avg["freshness"] == "fresh":
        return prod, "fallback_stale", "avg_run_behind_production"
    return prod, "fallback_stale", f"bundle stale: {avg['freshness_reason']}"


def avg_prediction(stat: str, pmf: list[float], q19: list[float]) -> dict[str, Any]:
    """The production prop-row shape from an AVG row (conditional on playing)."""
    s = np.asarray(pmf, dtype=float)
    thr = THRESHOLDS[stat]
    return {
        "mean": float(s.mean()),
        "q_grid": [float(v) for v in q19],
        "p_ge": {str(t): float(np.mean(s > t - 0.5)) for t in thr},
        "p_ge_full": full_support.from_samples(s, stat),
    }


def p_over(pred: dict[str, Any], line: float) -> float | None:
    """P(stat > line) for a x.5 line from the stored ``p_ge_full`` counts; None for whole lines."""
    if abs(line - math.floor(line) - 0.5) > 1e-9:
        return None
    full = pred.get("p_ge_full") or {}
    c, n = full.get("c"), full.get("n")
    if not c or not n:
        return None
    k = int(math.ceil(line))
    return 1.0 if k <= 0 else (float(c[k - 1]) / float(n) if k <= len(c) else 0.0)


def quartiles(row: dict[str, Any]) -> tuple[float, float, float] | None:
    """(q25, q50, q75) of the row's continuous forecast: the 199 sorted AVG samples at the harness
    grid positions, or the 19-point grid (tau = 0.25 / 0.5 / 0.75 are grid points)."""
    if row.get("pmf") is not None:
        return lc.sorted_grid_quantiles(np.asarray(row["pmf"], float))
    g = row["pred"].get("q_grid")
    if not g or len(g) != N_Q:
        return None
    return float(g[Q_IDX["q25"]]), float(g[Q_IDX["q50"]]), float(g[Q_IDX["q75"]])


# ----------------------------------------------------------------------------- loader


@dataclass
class Loaded:
    legs: list[LegCand] = field(default_factory=list)
    made_at: datetime | None = None
    no_slate: bool = False
    dropped: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    sources: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    notes: list[str] = field(default_factory=list)


def _table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()
    return bool(row and row[0])


def _prop_rows(
    con: duckdb.DuckDBPyConnection, lo: datetime, hi: datetime, slate: date, now: datetime | None
) -> tuple[dict[tuple[str, int, str], list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    marks = ",".join("?" for _ in STATS)
    raw = con.execute(
        f"""SELECT p.run_id, p.made_at, p.game_id, p.tipoff, p.model_name, p.target, p.player_id,
                   CAST(p.prediction AS VARCHAR), g.game_date, g.home_team, g.away_team
            FROM forward_predictions p LEFT JOIN games g ON g.game_id = p.game_id
            WHERE p.tipoff >= ? AND p.tipoff < ? AND p.target IN ({marks})
              AND p.player_id > 0 AND p.game_id NOT LIKE '001%' AND p.game_id NOT LIKE '003%'
            ORDER BY p.game_id, p.model_name, p.target, p.player_id, p.made_at, p.run_id""",  # noqa: S608
        [lo, hi, *STATS],
    ).fetchall()
    groups: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    games: dict[str, dict[str, Any]] = {}
    for run_id, made_at, gid, tip, model, stat, pid, pj, gdate, home, away in raw:
        if made_at >= tip or (now is not None and made_at > now):
            continue
        if (gdate or et_date(tip)) != slate:
            continue
        pred = _jloads(pj)
        gm = games.setdefault(gid, {"tipoff": tip, "home": home, "away": away})
        gm["tipoff"] = max(gm["tipoff"], tip)
        groups[(gid, int(pid), str(stat))].append({
            "run_id": run_id, "made_at": made_at, "tipoff": tip, "model_name": model,
            "comparison_only": bool(pred.get("comparison_only", False)), "pred": pred,
            "q_grid": pred.get("q_grid"), "freshness": "n/a", "freshness_reason": None,
        })  # fmt: skip
    return groups, games


def _avg_rows(
    con: duckdb.DuckDBPyConnection,
    lo: datetime,
    hi: datetime,
    slate: date,
    now: datetime | None,
    groups: dict[tuple[str, int, str], list[dict[str, Any]]],
) -> int:
    if not _table_exists(con, AVG_TABLE):
        return 0
    raw = con.execute(
        f"""SELECT a.made_at, a.run_id, a.game_id, a.player_id, a.stat, a.tipoff_utc, a.fit_through,
                   a.month_block, a.installed_at, a.stale_model, CAST(a.avg_pmf199 AS VARCHAR),
                   CAST(a.avg_q19 AS VARCHAR), CAST(a.a0_q19 AS VARCHAR), a.a0_source
            FROM {AVG_TABLE} a
            WHERE a.tipoff_utc >= ? AND a.tipoff_utc < ? AND a.game_id NOT LIKE '001%'
              AND a.game_id NOT LIKE '003%'
            ORDER BY a.game_id, a.player_id, a.stat, a.made_at, a.run_id""",  # noqa: S608
        [lo, hi],
    ).fetchall()
    bad = 0
    for (
        made_at,
        run_id,
        gid,
        pid,
        stat,
        tip,
        fit_through,
        mblock,
        inst,
        stale,
        pj,
        qj,
        aj,
        src,
    ) in raw:
        if made_at >= tip or (now is not None and made_at > now) or stat not in STATS:
            continue
        if et_date(tip) != slate:
            continue
        pmf, q19 = _valid(pj, N_PMF), _valid(qj, N_Q)
        if pmf is None or q19 is None:
            bad += 1
            continue
        fresh, why = avg_freshness(
            made_at=made_at, installed_at=inst, fit_through=fit_through, month_block=mblock,
            stale_flag=stale, game_date=slate,
        )  # fmt: skip
        groups[(str(gid), int(pid), str(stat))].append({
            "run_id": run_id, "made_at": made_at, "tipoff": tip, "model_name": AVG_MODEL,
            "comparison_only": True, "pmf": pmf, "q_grid": q19,
            "pred": avg_prediction(str(stat), pmf, q19), "a0_q19": _valid(aj, N_Q),
            "a0_source": src, "freshness": fresh, "freshness_reason": why,
        })  # fmt: skip
    return bad


def _ok_runs(con: duckdb.DuckDBPyConnection) -> set[str]:
    if not _table_exists(con, HEARTBEAT_TABLE):
        return set()
    return {
        str(r[0])
        for r in con.execute(f"SELECT run_id FROM {HEARTBEAT_TABLE} WHERE status = 'ok'").fetchall()  # noqa: S608
    }


def _teams(con: duckdb.DuckDBPyConnection, gids: list[str], slate: date) -> dict[int, int]:
    rows = con.execute(
        "SELECT s.player_id, arg_max(s.team_id, g.game_date) FROM player_game_stats s "
        "JOIN games g USING (game_id) WHERE g.game_date < ? GROUP BY s.player_id",
        [slate],
    ).fetchall()
    return {int(p): int(t) for p, t in rows if t is not None}


def _out_names(
    con: duckdb.DuckDBPyConnection, gids: list[str], made_at: dict[str, datetime]
) -> dict[str, list[int]]:
    """Players whose newest official-report status (as of the forecast) is ``out``, per game."""
    if not gids or not _table_exists(con, "player_availability"):
        return {}
    marks = ",".join("?" for _ in gids)
    rows = con.execute(
        f"SELECT game_id, player_id, status, as_of FROM player_availability "  # noqa: S608
        f"WHERE game_id IN ({marks}) ORDER BY as_of",
        gids,
    ).fetchall()
    latest: dict[tuple[str, int], str] = {}
    for gid, pid, status, as_of in rows:
        if as_of <= made_at.get(gid, datetime.max):
            latest[(gid, int(pid))] = str(status)
    out: dict[str, list[int]] = defaultdict(list)
    for (gid, pid), status in sorted(latest.items()):
        if status == "out":
            out[gid].append(pid)
    return out


def load_legs(
    nba: duckdb.DuckDBPyConnection,
    odds: duckdb.DuckDBPyConnection | None,
    slate: date,
    *,
    now: datetime | None,
    calmap: lc.CalMap,
) -> Loaded:
    """Every priced pre-tip leg of the slate. ``now`` (naive UTC) is the run clock: forecasts and
    quotes stamped after it are not read."""
    season = slate_season(slate)
    if season == HOLDOUT_SEASON:
        raise SourceError(
            f"{slate}: season {HOLDOUT_SEASON} is the frozen holdout and is never loaded"
        )
    out = Loaded(made_at=now)
    lo = datetime.combine(slate, datetime.min.time())
    hi = lo + timedelta(days=2)
    groups, games = _prop_rows(nba, lo, hi, slate, now)
    if not groups:
        out.no_slate = True
        out.notes.append(f"no pre-tip prop forecasts for {slate} in forward_predictions")
        return out
    bad_avg = _avg_rows(nba, lo, hi, slate, now, groups)
    if bad_avg:
        out.dropped["avg_row_unreadable"] += bad_avg
    ok = _ok_runs(nba)
    chosen: dict[tuple[str, int, str], tuple[dict[str, Any], dict[str, Any], str]] = {}
    for key in sorted(groups):
        row, source, _detail = choose_primary(key[0], groups[key], ok)
        prod = _latest(
            [r for r in groups[key] if r["model_name"] == PROPS_MODEL and not r["comparison_only"]]
        )
        if (
            prod is None or row is None
        ):  # p_cal needs the production P(over); no production row, no leg
            out.dropped["no_production_row"] += 1
            continue
        chosen[key] = (row, prod, source)
        out.sources["avg_shown" if row["model_name"] == AVG_MODEL else "production_only"] += 1
    if odds is None or not _table_exists(odds, LINES_TABLE):
        out.notes.append("no capture store or no live_book_odds table: no priced legs")
        return out
    gids = sorted({k[0] for k in chosen})
    pids = sorted({k[1] for k in chosen})
    team_by_player = _teams(nba, gids, slate)
    out_ids = _out_names(nba, gids, {k[0]: v[1]["made_at"] for k, v in chosen.items()})
    gm, pm, mm = (",".join("?" for _ in xs) for xs in (gids, pids, MARKET_STAT))
    quotes = odds.execute(
        f"""SELECT game_id, player_id, market, book, side, point, price_american, received_at,
                   player_name, tipoff
            FROM {LINES_TABLE}
            WHERE game_id IN ({gm}) AND player_id IN ({pm}) AND market IN ({mm})
              AND received_at < tipoff AND received_at < commence_time
              AND point IS NOT NULL AND price_american IS NOT NULL AND side IN ('over', 'under')
            ORDER BY received_at, snapshot_kind""",  # noqa: S608
        [*gids, *pids, *MARKET_STAT],
    ).fetchall()
    by_player: dict[tuple[str, int], list[tuple[Any, ...]]] = defaultdict(list)
    by_game: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for q in quotes:
        by_player[(q[0], int(q[1]))].append(q)
        by_game[q[0]].append(q)
    for key in sorted(chosen):
        gid, pid, stat = key
        row, prod, source = chosen[key]
        pp = prod["pred"]
        p_play, srate = pp.get("p_play"), pp.get("starter_rate")
        qs = quartiles(prod)
        if qs is None:
            out.dropped["no_quartiles"] += 1
            continue
        team_id = team_by_player.get(pid)
        gmeta = games[gid]
        if team_id not in (gmeta["home"], gmeta["away"]):
            out.dropped["team_not_in_game"] += 1
            continue
        opp_id = gmeta["away"] if team_id == gmeta["home"] else gmeta["home"]
        usable = [
            q
            for q in by_player.get((gid, pid), [])
            if MARKET_STAT[q[2]] == stat
            and q[7] <= prod["made_at"]
            and (now is None or q[7] <= now)
        ]
        # only the newest snapshot of each (book, market, game): a line the book has since moved off
        # must not stay selectable at its old price (the export shows every line; we choose)
        # keyed on (book, market, GAME): if the newest board drops this player (market pulled),
        # his last quote must not stay selectable
        latest_snap: dict[tuple[str, str], datetime] = {}
        for gq in by_game.get(gid, []):
            if (
                MARKET_STAT[gq[2]] == stat
                and gq[7] <= prod["made_at"]
                and (now is None or gq[7] <= now)
            ):
                latest_snap[(gq[3], gq[2])] = max(latest_snap.get((gq[3], gq[2]), gq[7]), gq[7])
        newest: dict[tuple[str, str, float, str], tuple[Any, ...]] = {}
        for q in usable:  # ascending received_at
            if q[7] == latest_snap[(q[3], q[2])]:
                newest[(q[3], q[2], float(q[5]), q[4])] = q
        if not newest:
            continue
        if p_play is None or p_play < MIN_P_PLAY:
            out.dropped["p_play_below_floor_or_missing"] += len(newest)
            continue
        if srate is None:
            out.dropped["no_starter_rate"] += len(newest)
            continue
        teammates = [
            names.player_name(o)
            for o in out_ids.get(gid, [])
            if o != pid and team_by_player.get(o) == team_id
        ]
        rel_mean = pp.get("mean_recency")
        for (book, _market, line, side), q in sorted(newest.items()):
            if not calmap.has(book, stat):
                out.dropped["no_cal_map_for_book"] += 1
                continue
            po = p_over(prod["pred"], line)
            if po is None:
                out.dropped["whole_number_line_or_no_pmf"] += 1
                continue
            pc = calmap.apply(
                book, stat, po, line, qs[1], (qs[2] - qs[0]) / lc.IQR_TO_SD, float(srate)
            )
            pa = p_over(row["pred"], line) if row["model_name"] == AVG_MODEL else None
            out.legs.append(
                make_leg(
                    p_over_raw=po,
                    p_over_cal=pc,
                    p_avg_over=pa,
                    side=side,
                    game_id=gid,
                    tipoff=row["tipoff"],
                    player_id=pid,
                    player_name=names.player_name(pid, {pid: str(q[8])} if q[8] else None),
                    team_id=int(team_id or 0),
                    opp_id=int(opp_id),
                    team=names.team_abbr(team_id),
                    opp=names.team_abbr(opp_id),
                    stat=stat,
                    line=line,
                    book=book,
                    price_american=int(q[6]),
                    price_at=q[7],
                    model_source="production",
                    drivers={
                        "mean": round(float(pp["mean"]), 2),
                        "recent_avg": None if rel_mean is None else round(float(rel_mean), 2),
                        "minutes": None
                        if pp.get("proj_minutes") is None
                        else round(float(pp["proj_minutes"]), 1),
                        "minutes_kind": "projected",
                        "p_play": round(float(p_play), 3),
                        "teammates_out_names": teammates[:2],
                        "primary_source": source,
                    },
                )
            )
    return out
