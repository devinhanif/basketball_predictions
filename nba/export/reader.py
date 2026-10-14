"""Read-only extract functions: plain dicts from an already-open read-only ``nba.duckdb``.

Nothing here writes, fits, loads a model, or touches the network. Every query is bounded to the
allowed seasons in SQL (see ``guard``); a final assertion re-checks the rows. Timestamps are
naive UTC exactly as stored (``forward_predictions.made_at`` / ``tipoff``); ``game_date`` is the
Eastern date. A forecast row is exported only if ``made_at <= tipoff`` (never a post-tip row) and
carries ``lead_minutes = tipoff - made_at`` so the consumer can apply its own cutoff
(the scoring rule is ``made_at <= tipoff - 60 min``; the T-30 refresh lands after it).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from nba.export import guard

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
WIN_TARGET = "win_prob_home"
NO_PLAYER = -1
STAMPS_TABLE = "forward_model_stamps"


@dataclass(frozen=True)
class Window:
    """Which slice of the forward log to export: Eastern game dates in
    ``[date - lookback_days, date]`` for the allowed ``seasons``."""

    day: date
    lookback_days: int = 7
    seasons: tuple[int, ...] = (guard.LIVE_SEASON,)

    @property
    def first(self) -> date:
        return self.day - timedelta(days=self.lookback_days)

    def tip_bounds_utc(self) -> tuple[datetime, datetime]:
        """Coarse naive-UTC bounds on tipoff (the exact test is the Eastern date)."""
        lo = datetime.combine(self.first, datetime.min.time())
        hi = datetime.combine(self.day, datetime.min.time()) + timedelta(days=2)
        return lo, hi


def open_read_only(path: str | Path, *, max_wait_s: float = 30.0) -> duckdb.DuckDBPyConnection:
    """The only way this package opens ``nba.duckdb``: ``read_only=True``, bounded lock retry."""
    from nba.db.connect import connect_with_retry

    return connect_with_retry(path, read_only=True, max_wait_s=max_wait_s)


def table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()
    return bool(row and row[0])


def et_date(ts: datetime) -> date:
    """Eastern calendar date of a naive-UTC timestamp."""
    return ts.replace(tzinfo=UTC).astimezone(ET).date()


def _assert_allowed(seasons: set[int], allowed: tuple[int, ...]) -> None:
    bad = {s for s in seasons if s not in allowed or s == guard.HOLDOUT_SEASON}
    if bad:  # pragma: no cover - the SQL filter makes this unreachable; kept as the belt
        raise guard.ExportRefused(f"rows from seasons {sorted(bad)} reached the export")


def _jloads(v: Any) -> dict[str, Any]:
    out = json.loads(v) if isinstance(v, str) else v
    return out if isinstance(out, dict) else {}


# --------------------------------------------------------------------------- model stamps


def model_stamps(
    con: duckdb.DuckDBPyConnection, run_ids: set[str]
) -> dict[tuple[str, str], dict[str, Any]]:
    """``forward_model_stamps`` keyed by (run_id, model_name); empty when the table is absent
    (the live DB gets it at the first run that carries the stamp writer)."""
    if not run_ids or not table_exists(con, STAMPS_TABLE):
        return {}
    marks = ",".join("?" for _ in run_ids)
    rows = con.execute(
        f"SELECT run_id, model_name, version, made_at, weights_sha256, weights_scope, "
        f"data_version, fit_through, n_train, git_sha, git_dirty, stamped_at "
        f"FROM {STAMPS_TABLE} WHERE run_id IN ({marks})",
        sorted(run_ids),
    ).fetchall()
    cols = (
        "run_id", "model_name", "version", "made_at", "weights_sha256", "weights_scope",
        "data_version", "fit_through", "n_train", "git_sha", "git_dirty", "stamped_at",
    )  # fmt: skip
    return {(r[0], r[1]): dict(zip(cols, r, strict=True)) for r in rows}


# ------------------------------------------------------------------------ forward predictions


@dataclass(frozen=True)
class ForwardBundle:
    """Forward predictions for a window, as plain dicts."""

    win_probs: list[dict[str, Any]]
    prop_grids: list[dict[str, Any]]
    stamps: list[dict[str, Any]]
    games: list[dict[str, Any]]
    excluded_post_tip: int
    excluded_unresolved_season: int


def _flatten_stamp(stamp: dict[str, Any] | None) -> dict[str, Any]:
    keys = ("weights_sha256", "weights_scope", "data_version", "fit_through", "n_train",
            "git_sha", "git_dirty", "stamped_at")  # fmt: skip
    return {f"stamp_{k}": (stamp[k] if stamp else None) for k in keys}


def forward_predictions(con: duckdb.DuckDBPyConnection, window: Window) -> ForwardBundle:
    """Win probabilities and per-player-stat forecast grids as the daily run stored them.

    Win rows: ``p_home`` plus the stamped teams. Prop rows: mean/std, ``q_grid`` (19 integer-support
    quantile levels as stored), ``p_ge`` / ``p_ge_full`` survival tables and ``p_play`` /
    ``proj_minutes``. The verbatim ``prediction`` JSON rides along. ``is_latest`` marks the newest
    pre-tip row per (game, model, target, player); ``stamp_*`` is the model stamp of the run.
    """
    lo, hi = window.tip_bounds_utc()
    marks = ",".join("?" for _ in window.seasons)
    sql = f"""
        SELECT p.run_id, p.made_at, p.game_id, p.tipoff, p.model_name, p.version, p.target,
               p.player_id, CAST(p.prediction AS VARCHAR) AS prediction,
               g.game_date, g.home_team, g.away_team,
               {guard.season_sql("g", "p.game_id")} AS season, g.home_pts, g.away_pts
        FROM forward_predictions p LEFT JOIN games g ON g.game_id = p.game_id
        WHERE p.tipoff >= ? AND p.tipoff < ?
          AND p.game_id NOT LIKE '001%' AND p.game_id NOT LIKE '003%'
          AND {guard.season_sql("g", "p.game_id")} IN ({marks})
        ORDER BY p.game_id, p.model_name, p.target, p.player_id, p.made_at, p.run_id
    """  # noqa: S608 - identifiers are constants
    raw = con.execute(sql, [lo, hi, *window.seasons]).fetchall()
    total_in_bounds = con.execute(
        "SELECT count(*) FROM forward_predictions WHERE tipoff >= ? AND tipoff < ? "
        "AND game_id NOT LIKE '001%' AND game_id NOT LIKE '003%'",
        [lo, hi],
    ).fetchone()
    excluded_unresolved = int(total_in_bounds[0]) - len(raw) if total_in_bounds else 0

    kept: list[tuple[Any, ...]] = []
    post_tip = 0
    for r in raw:
        made_at, tipoff = r[1], r[3]
        if made_at > tipoff:
            post_tip += 1
            continue
        gdate = r[9] or et_date(tipoff)
        if not (window.first <= gdate <= window.day):
            continue
        kept.append(r)
    _assert_allowed({int(r[12]) for r in kept}, window.seasons)

    stamps = model_stamps(con, {r[0] for r in kept})
    latest: dict[tuple[str, str, str, int], tuple[datetime, str]] = {}
    for r in kept:
        k = (r[2], r[4], r[6], int(r[7]))
        if k not in latest or (r[1], r[0]) > latest[k]:
            latest[k] = (r[1], r[0])

    win: list[dict[str, Any]] = []
    props: list[dict[str, Any]] = []
    games: dict[str, dict[str, Any]] = {}
    for r in kept:
        run_id, made_at, game_id, tipoff, model, version, target, pid, pred_s = r[:9]
        pred = _jloads(pred_s)
        base: dict[str, Any] = {
            "run_id": run_id, "made_at": made_at, "game_id": game_id, "tipoff": tipoff,
            "model_name": model, "version": version, "player_id": int(pid),
            "lead_minutes": (tipoff - made_at).total_seconds() / 60.0,
            "is_latest": latest[(game_id, model, target, int(pid))] == (made_at, run_id),
            "comparison_only": bool(pred.get("comparison_only", False)),
            "prediction_json": pred_s,
            **_flatten_stamp(stamps.get((run_id, model))),
        }  # fmt: skip
        g = games.setdefault(
            game_id,
            {"game_id": game_id, "season": int(r[12]), "game_date": r[9] or et_date(tipoff),
             "tipoff": tipoff, "home_team": r[10], "away_team": r[11],
             "final": bool(r[13] and r[14]), "home_pts": r[13] or None,
             "away_pts": r[14] or None},
        )  # fmt: skip
        g["tipoff"] = max(g["tipoff"], tipoff)
        if target == WIN_TARGET:
            g["home_team"] = g["home_team"] if g["home_team"] is not None else pred.get("home_team")
            g["away_team"] = g["away_team"] if g["away_team"] is not None else pred.get("away_team")
            win.append(base | {
                "p_home": pred.get("p_home"), "home_team": pred.get("home_team"),
                "away_team": pred.get("away_team"), "is_primary": pred.get("primary"),
                "fallback_reason": pred.get("fallback_reason"),
            })  # fmt: skip
        else:
            full = pred.get("p_ge_full") or {}
            props.append(base | {
                "stat": target, "mean": pred.get("mean"), "std": pred.get("std"),
                "p_play": pred.get("p_play"), "proj_minutes": pred.get("proj_minutes"),
                "q10": pred.get("q10"), "q50": pred.get("q50"), "q90": pred.get("q90"),
                "q_grid": pred.get("q_grid"), "p_ge": pred.get("p_ge"),
                "p_ge_full_counts": full.get("c"), "p_ge_full_n": full.get("n"),
            })  # fmt: skip
    used = {(w["run_id"], w["model_name"]) for w in win} | {
        (p["run_id"], p["model_name"]) for p in props
    }
    return ForwardBundle(
        win_probs=win,
        prop_grids=props,
        stamps=[v for k, v in sorted(stamps.items()) if k in used],
        games=sorted(games.values(), key=lambda g: (g["game_date"], g["game_id"])),
        excluded_post_tip=post_tip,
        excluded_unresolved_season=excluded_unresolved,
    )


# ------------------------------------------------------------------------------- settled


def settled_scores(con: duckdb.DuckDBPyConnection, window: Window) -> list[dict[str, Any]]:
    """``forward_scores`` rows (scored and dnp) for the allowed seasons up to ``window.day``.
    All of the season, not just the lookback: calibration pages need the whole log."""
    marks = ",".join("?" for _ in window.seasons)
    cols = ("scored_at", "game_id", "game_date", "season", "model_name", "version", "target",
            "player_id", "made_at", "y", "pred", "log_loss", "brier", "crps", "status")  # fmt: skip
    rows = con.execute(
        f"SELECT {', '.join('s.' + c for c in cols)} FROM forward_scores s "
        f"WHERE s.season IN ({marks}) AND s.game_date <= ? "
        f"AND s.game_id NOT LIKE '001%' AND s.game_id NOT LIKE '003%' "
        f"ORDER BY s.game_date, s.game_id, s.model_name, s.target, s.player_id, s.made_at",
        [*window.seasons, window.day],
    ).fetchall()
    out = [dict(zip(cols, r, strict=True)) for r in rows]
    _assert_allowed({int(r["season"]) for r in out}, window.seasons)
    return out


def box_scores(con: duckdb.DuckDBPyConnection, window: Window) -> list[dict[str, Any]]:
    """Actuals for final games in the allowed seasons (player_game_stats), for leg settlement.
    DNP rows are explicit: NULL minutes, zero stats (the table's own convention). ``stl``, ``blk``
    and ``tov`` ride along as stored: NULL when the box score lacked them, never zero-filled."""
    marks = ",".join("?" for _ in window.seasons)
    cols = ("game_id", "player_id", "team_id", "minutes", "starter", "pts", "reb", "ast", "fg3m",
            "stl", "blk", "tov")  # fmt: skip
    rows = con.execute(
        f"SELECT {', '.join('b.' + c for c in cols)}, g.game_date, g.season "
        f"FROM player_game_stats b JOIN games g ON g.game_id = b.game_id "
        f"WHERE g.season IN ({marks}) AND g.game_date <= ? AND g.home_pts IS NOT NULL "
        f"ORDER BY g.game_date, b.game_id, b.player_id",
        [*window.seasons, window.day],
    ).fetchall()
    out = [dict(zip((*cols, "game_date", "season"), r, strict=True)) for r in rows]
    _assert_allowed({int(r["season"]) for r in out}, window.seasons)
    return out


# ------------------------------------------------------------------------------- team ids


def player_teams(
    con: duckdb.DuckDBPyConnection,
    props: list[dict[str, Any]],
    games: list[dict[str, Any]],
    window: Window,
) -> dict[tuple[str, int], tuple[int, str]]:
    """``(game_id, player_id) -> (team_id, source)`` for the players of the exported prop rows.

    ``box``: the player's own box-score row in that game. ``prior_game``: the team on the player's
    latest EARLIER game (``game_date`` strictly before) in the allowed seasons, accepted only when
    it is one of the game's two teams. Seasons outside the window are never read (the 2025
    holdout holds last season's rosters); a player with neither stays absent (NULL, never
    guessed)."""
    pairs = {(str(p["game_id"]), int(p["player_id"])) for p in props}
    if not pairs:
        return {}
    gmeta = {g["game_id"]: g for g in games}
    ids = sorted({pid for _, pid in pairs})
    gids = sorted({gid for gid, _ in pairs})
    smarks = ",".join("?" for _ in window.seasons)
    pmarks = ",".join("?" for _ in ids)
    gmarks = ",".join("?" for _ in gids)
    out: dict[tuple[str, int], tuple[int, str]] = {}
    rows = con.execute(
        f"SELECT b.game_id, b.player_id, b.team_id FROM player_game_stats b "
        f"JOIN games g ON g.game_id = b.game_id "
        f"WHERE b.game_id IN ({gmarks}) AND b.player_id IN ({pmarks}) AND g.season IN ({smarks}) "
        f"AND b.team_id IS NOT NULL",
        [*gids, *ids, *window.seasons],
    ).fetchall()
    for gid, pid, tid in rows:
        if (gid, int(pid)) in pairs:
            out[(gid, int(pid))] = (int(tid), "box")
    todo = pairs - set(out)
    if not todo:
        return out
    hist: dict[int, list[tuple[date, int]]] = defaultdict(list)
    for pid, gd, tid in con.execute(
        f"SELECT b.player_id, g.game_date, b.team_id FROM player_game_stats b "
        f"JOIN games g ON g.game_id = b.game_id "
        f"WHERE b.player_id IN ({pmarks}) AND g.season IN ({smarks}) AND g.game_date <= ? "
        f"AND b.team_id IS NOT NULL ORDER BY g.game_date, b.game_id",
        [*ids, *window.seasons, window.day],
    ).fetchall():
        hist[int(pid)].append((gd, int(tid)))
    for gid, pid in sorted(todo):
        g = gmeta.get(gid)
        if g is None:
            continue
        pair = {g.get("home_team"), g.get("away_team")} - {None}
        for gd, tid in reversed(hist.get(pid, [])):
            if gd < g["game_date"] and tid in pair:
                out[(gid, pid)] = (tid, "prior_game")
                break
    return out


# ------------------------------------------------------------------------------------ lines

LINES_TABLE = "live_book_odds"
#: the vendor's player markets for the four forecast stats (``nba.daily.predict.PROP_STATS``),
#: main and alternate (milestone) ladders -> (stat, is_alternate). Combos / blocks / steals have
#: no forecast to attach to.
LINE_MARKETS: dict[str, tuple[str, bool]] = {
    "player_points": ("pts", False), "player_points_alternate": ("pts", True),
    "player_rebounds": ("reb", False), "player_rebounds_alternate": ("reb", True),
    "player_assists": ("ast", False), "player_assists_alternate": ("ast", True),
    "player_threes": ("fg3m", False), "player_threes_alternate": ("fg3m", True),
}  # fmt: skip
#: Polymarket Unders priced beyond this (more negative) are a no-bid placeholder, not a price
#: (docs/CLOSING_LINE_CAPTURE.md, "The us_ex region": every recorded player_points Under <= -3000).
POLYMARKET_UNDER_FLOOR = -1000
UNUSABLE_PM_UNDER = "polymarket_under_placeholder"


@dataclass(frozen=True)
class LinesBundle:
    """Pre-tip quotes per forward prop prediction, plus what was left out."""

    rows: list[dict[str, Any]]
    excluded_post_tip: int  # quotes for these players stamped at/after tip or the vendor start
    status: str  # ok | no_table | absent | locked | not_requested


def open_odds_read_only(
    path: str | Path, *, max_wait_s: float = 20.0
) -> duckdb.DuckDBPyConnection | None:
    """``market_asof.duckdb`` read-only; None when the file is absent. A lock that outlasts the
    retry raises ``duckdb.IOException`` (the capture owns that file; the caller records it)."""
    from nba.db.connect import connect_with_retry

    if not Path(path).exists():
        return None
    return connect_with_retry(path, read_only=True, max_wait_s=max_wait_s)


def unusable_reason(book: str, side: str | None, price: int | None) -> str | None:
    """Why a quote must not be read as a price, or None. Polymarket (any ``polymarket*`` key)
    Unders beyond -1000 are a placeholder with no bid behind them."""
    if (
        book.startswith("polymarket")
        and side == "under"
        and price is not None
        and price < POLYMARKET_UNDER_FLOOR
    ):
        return UNUSABLE_PM_UNDER
    return None


def prop_lines(
    odds: duckdb.DuckDBPyConnection | None,
    props: list[dict[str, Any]],
    window: Window,
) -> LinesBundle:
    """For each forward prop prediction (run, game, player, stat), the book quotes knowable when
    it was made: ``received_at <= made_at`` (our clock after the response, naive UTC) and strictly
    before the tip. The newest quote per (book, market, line, side) wins; main and alternate ladders
    are separate markets. Nothing is de-vigged; a Polymarket Under beyond -1000 is kept but flagged
    ``unusable``. ``player_id`` is the capture's name resolution (NULL = unresolved, not exported).
    Only games of the exported prop rows are read, so a game outside the window's seasons never
    appears; the row set is re-asserted against the allowed seasons."""
    if odds is None:
        return LinesBundle([], 0, "absent")
    if not table_exists(odds, LINES_TABLE):
        return LinesBundle([], 0, "no_table")
    _assert_allowed(
        {s for p in props if (s := guard.season_of_game_id(str(p["game_id"]))) is not None},
        window.seasons,
    )
    preds: dict[tuple[str, str, int, str], tuple[datetime, datetime]] = {}
    for p in props:  # models in one run share that run's quotes
        preds[(p["run_id"], str(p["game_id"]), int(p["player_id"]), str(p["stat"]))] = (
            p["made_at"],
            p["tipoff"],
        )
    if not preds:
        return LinesBundle([], 0, "ok")
    stat_of = {m: v[0] for m, v in LINE_MARKETS.items()}
    gids = sorted({k[1] for k in preds})
    pids = sorted({k[2] for k in preds})
    gm, pm, mm = (",".join("?" for _ in xs) for xs in (gids, pids, stat_of))
    where = (
        f"game_id IN ({gm}) AND player_id IN ({pm}) AND market IN ({mm})"
        f" AND game_id NOT LIKE '001%' AND game_id NOT LIKE '003%'"
    )
    params = [*gids, *pids, *stat_of]
    post = odds.execute(
        f"SELECT count(*) FROM {LINES_TABLE} WHERE {where} "  # noqa: S608
        f"AND NOT (received_at < tipoff AND received_at < commence_time)",
        params,
    ).fetchone()
    raw = odds.execute(
        f"SELECT game_id, player_id, market, book, side, point, price_american, implied_prob_raw, "  # noqa: S608
        f"received_at, snapshot_kind, last_update FROM {LINES_TABLE} "
        f"WHERE {where} AND received_at < tipoff AND received_at < commence_time "
        f"AND point IS NOT NULL AND price_american IS NOT NULL AND side IN ('over', 'under') "
        f"ORDER BY received_at, snapshot_kind",
        params,
    ).fetchall()
    by_player: dict[tuple[str, int], list[tuple[Any, ...]]] = defaultdict(list)
    for r in raw:
        by_player[(r[0], int(r[1]))].append(r)
    out: list[dict[str, Any]] = []
    for (run_id, gid, pid, stat), (made_at, tipoff) in sorted(preds.items()):
        newest: dict[tuple[str, str, float, str], tuple[Any, ...]] = {}
        for r in by_player.get((gid, pid), []):  # ascending received_at: the last one wins
            if stat_of[r[2]] == stat and r[8] <= made_at and r[8] < tipoff:
                newest[(r[3], r[2], float(r[5]), r[4])] = r
        for (book, market, point, side), r in sorted(newest.items()):
            reason = unusable_reason(book, side, int(r[6]))
            out.append({
                "run_id": run_id, "made_at": made_at, "game_id": gid, "player_id": pid,
                "stat": stat, "book": book, "market": market,
                "is_alternate": LINE_MARKETS[market][1], "line": point, "side": side,
                "price_american": int(r[6]), "implied_prob_raw": r[7], "snapshot_ts": r[8],
                "snapshot_kind": r[9], "last_update": r[10],
                "minutes_before_tip": (tipoff - r[8]).total_seconds() / 60.0,
                "unusable": reason is not None, "unusable_reason": reason,
            })  # fmt: skip
    return LinesBundle(out, int(post[0]) if post else 0, "ok")


# ------------------------------------------------------------------------------ explainer


def explainer(con: duckdb.DuckDBPyConnection, game_id: str, window: Window) -> dict[str, Any]:
    """Stored facts behind one game: header, the official injury report as the model saw it
    (latest snapshot with ``as_of <= tip - 60`` ET, via ``nba.explain.data.load_report``), the
    pre-tip win and prop rows, and, once final, the box score. Nothing is recomputed."""
    from nba.explain.data import load_box, load_report

    if not guard.game_id_allowed(game_id):
        raise guard.ExportRefused(f"{game_id}: preseason / all-star games are not exported")
    sid = guard.season_of_game_id(game_id)
    row = con.execute(
        "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
        "FROM games WHERE game_id = ?",
        [game_id],
    ).fetchone()
    season = int(row[2]) if row else sid
    if season is None or season not in window.seasons or season == guard.HOLDOUT_SEASON:
        raise guard.ExportRefused(f"{game_id}: season {season} is not in the export window")
    tip = con.execute(
        "SELECT max(tipoff) FROM forward_predictions WHERE game_id = ?", [game_id]
    ).fetchone()
    tip_utc = tip[0] if tip and tip[0] is not None else None
    gdate = row[1] if row else (et_date(tip_utc) if tip_utc else None)
    one = Window(gdate or window.day, 0, window.seasons)
    fwd = forward_predictions(con, one)
    report = load_report(con, game_id, tip_utc, gdate) if (tip_utc and gdate) else None
    final = bool(row and row[5] and row[6])
    box = load_box(con, game_id) if final else {}
    return {
        "game": {
            "game_id": game_id,
            "season": season,
            "game_date": gdate,
            "tipoff": tip_utc,
            "home_team": row[3] if row else None,
            "away_team": row[4] if row else None,
            "final": final,
            "home_pts": int(row[5]) if final and row else None,
            "away_pts": int(row[6]) if final and row else None,
        },  # fmt: skip
        "injury_report": {
            "as_of_et": report.as_of_et if report else None,
            "lines": [
                {
                    "player_id": ln.player_id,
                    "team_id": ln.team_id,
                    "status": ln.status,
                    "reason": ln.reason,
                }
                for ln in (report.lines if report else [])
            ],
        },  # fmt: skip
        "win_probs": [w for w in fwd.win_probs if w["game_id"] == game_id],
        "prop_grids": [p for p in fwd.prop_grids if p["game_id"] == game_id],
        "box": [
            {
                "player_id": b.player_id,
                "team_id": b.team_id,
                "minutes": b.minutes,
                "starter": b.starter,
                **b.stats,
            }
            for b in sorted(box.values(), key=lambda b: b.player_id)
        ],  # fmt: skip
    }


def explainer_rows(
    con: duckdb.DuckDBPyConnection, games: list[dict[str, Any]], window: Window
) -> list[dict[str, Any]]:
    """Injury-report lines for each exported game, flat (one row per player line)."""
    from nba.explain.data import load_report

    out: list[dict[str, Any]] = []
    if not table_exists(con, "player_availability"):
        return out
    by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for g in games:
        rep = load_report(con, g["game_id"], g["tipoff"], g["game_date"])
        for ln in rep.lines:
            by_game[g["game_id"]].append(
                {"game_id": g["game_id"], "report_as_of_et": rep.as_of_et,
                 "player_id": ln.player_id, "team_id": ln.team_id, "status": ln.status,
                 "reason": ln.reason}
            )  # fmt: skip
    for gid in sorted(by_game):
        out.extend(by_game[gid])
    return out
