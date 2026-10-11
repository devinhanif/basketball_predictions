"""Assemble the as-of JSON payload for one game at request time ``t``.

Allowed inputs (docs/LLM_SHADOW.md): facts-store rows with ``known_at <= t``; production's own
forward rows with ``made_at <= t`` (mean, P(over) at the DK line); the T-60 DK / Pinnacle lines from
``live_book_odds`` with ``received_at <= t``; team context from completed games strictly before the
game date; each player's trailing-10 box lines strictly before the game date. Nothing from the web.

Every section carries its ``known_at``; :func:`assert_as_of` walks the finished payload and raises
:class:`AsOfViolation` on any stamp after ``t``. ``t`` must be at least 60 minutes before tip.
Ordering is deterministic and the payload's sha256 is over its canonical JSON.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import numpy as np

from nba.facts import query as facts_query
from nba.props import full_support as fsup
from nba.shadow_llm.config import ShadowConfig

ET = ZoneInfo("America/New_York")
SCHEMA = "llm_shadow_payload_v1"
LEAD_MINUTES = 60

PROD_PROPS_MODEL = "props_context_residual"
WIN_MODEL = "rung0_injury_elo"
MOV_MODEL = "rung0_mov_elo"
WIN_TARGET = "win_prob_home"

#: odds-api market -> forward_predictions target (existing prop naming; the scorer reads it as is)
MARKET_STAT = {
    "player_points": "pts",
    "player_rebounds": "reb",
    "player_assists": "ast",
    "player_threes": "fg3m",
}
STAT_ORDER = ("pts", "reb", "ast", "fg3m")
PRIMARY_BOOK = "draftkings"
SECOND_BOOK = "pinnacle"
#: game markets sent as POINT values only: market -> the side whose point is shown
GAME_MARKET_SIDE = {"spreads": "home", "totals": "over"}
TRAILING_N = 10
#: rough chars-per-token for compact JSON of numbers and short words (an estimate, not a count)
CHARS_PER_TOKEN = 3.0


class AsOfViolation(ValueError):
    """A payload field is stamped after the request time (or the request itself is too late)."""


class PayloadRefused(ValueError):
    """The payload cannot be built (reason in ``code``); nothing is sent, the game may retry."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class Payload:
    data: dict[str, Any]
    text: str
    sha256: str
    legs: list[dict[str, Any]]
    est_tokens: int


def naive_utc(t: datetime) -> datetime:
    return t.astimezone(UTC).replace(tzinfo=None) if t.tzinfo else t


def iso(t: datetime | None) -> str | None:
    return None if t is None else naive_utc(t).isoformat(timespec="seconds")


def et_date(tipoff: datetime) -> date:
    return naive_utc(tipoff).replace(tzinfo=UTC).astimezone(ET).date()


def _clean(v: Any, nd: int = 4) -> Any:
    if isinstance(v, float | np.floating):
        f = float(v)
        return None if not math.isfinite(f) else round(f, nd)
    if isinstance(v, np.integer):
        return int(v)
    return v


def canonical(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)


def p_over_from_row(pred: dict[str, Any], line: float) -> tuple[float, str]:
    """P(stat > line) from a stored production row: exact from ``p_ge_full`` when present
    (P(Y >= floor(line) + 1)), else a normal approximation from mean/std (labelled)."""
    k = math.floor(line) + 1
    surv = fsup.survival(pred.get(fsup.FIELD))
    if surv is not None and fsup.is_complete(surv):
        return (float(surv[k - 1]) if k - 1 < surv.size else 0.0), "p_ge_full"
    from scipy.stats import norm

    std = max(float(pred.get("std") or 0.0), 0.5)
    return float(norm.sf((k - 0.5 - float(pred["mean"])) / std)), "normal_approx"


# ----------------------------------------------------------------------------------------------
# The as-of rule
# ----------------------------------------------------------------------------------------------


def _parse(ts: Any) -> datetime:
    return naive_utc(ts) if isinstance(ts, datetime) else naive_utc(datetime.fromisoformat(ts))


def assert_as_of(node: Any, t: datetime, path: str = "$") -> int:
    """Raise :class:`AsOfViolation` for any ``known_at`` (scalar, or a ``known_at`` column of a
    ``{"columns", "rows"}`` table) later than ``t``. Returns how many stamps were checked."""
    t = naive_utc(t)
    n = 0
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "known_at" and v is not None:
                n += 1
                if _parse(v) > t:
                    raise AsOfViolation(f"{path}.known_at={v} is after t={iso(t)}")
            else:
                n += assert_as_of(v, t, f"{path}.{k}")
        cols, rows = node.get("columns"), node.get("rows")
        if isinstance(cols, list) and isinstance(rows, list) and "known_at" in cols:
            i = cols.index("known_at")
            for j, row in enumerate(rows):
                if row[i] is not None:
                    n += 1
                    if _parse(row[i]) > t:
                        raise AsOfViolation(
                            f"{path}.rows[{j}].known_at={row[i]} is after t={iso(t)}"
                        )
    elif isinstance(node, list):
        for i, v in enumerate(node):
            n += assert_as_of(v, t, f"{path}[{i}]")
    return n


# ----------------------------------------------------------------------------------------------
# Sections
# ----------------------------------------------------------------------------------------------


def _production_rows(
    con: duckdb.DuckDBPyConnection, game_id: str, t: datetime
) -> list[tuple[str, str, int, str, datetime, dict[str, Any]]]:
    """Latest row per (model, target, player) with made_at <= t (the scorer's eligibility rule)."""
    rows = con.execute(
        """
        SELECT model_name, target, player_id, run_id, made_at, prediction FROM (
            SELECT *, row_number() OVER (
                PARTITION BY model_name, target, player_id ORDER BY made_at DESC, run_id DESC) rn
            FROM forward_predictions
            WHERE game_id = ? AND made_at <= ? AND model_name IN (?, ?, ?))
        WHERE rn = 1 ORDER BY model_name, target, player_id
        """,
        [game_id, t, PROD_PROPS_MODEL, WIN_MODEL, MOV_MODEL],
    ).fetchall()
    return [(str(m), str(tg), int(p), str(r), a, json.loads(j)) for m, tg, p, r, a, j in rows]


def _win_section(rows: list[tuple[str, str, int, str, datetime, dict[str, Any]]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for model, target, _pid, run_id, made_at, pred in rows:
        if target != WIN_TARGET:
            continue
        if model == WIN_MODEL:
            out["injury_elo"] = {
                "p_home": _clean(pred.get("p_home")),
                "p_mov_elo": _clean(pred.get("p_mov_elo")),
                "d_out": _clean(pred.get("d_out")),
                "d_doubt": _clean(pred.get("d_doubt")),
                "fallback_reason": pred.get("fallback_reason"),
                "report_as_of_et": pred.get("report_as_of_et"),
                "home_team": pred.get("home_team"),
                "away_team": pred.get("away_team"),
                "run_id": run_id,
                "known_at": iso(made_at),
            }
        elif model == MOV_MODEL:
            out["mov_elo"] = {
                "p_home": _clean(pred.get("p_home")),
                "run_id": run_id,
                "known_at": iso(made_at),
            }
    return out


def _lines(
    odds: duckdb.DuckDBPyConnection | None, game_id: str, t: datetime
) -> tuple[dict[tuple[str, str, int], dict[str, Any]], list[list[Any]], datetime | None]:
    """Main prop line per (book, stat, player) and spread/total POINTS (T-60, received <= t).

    Prices and implied probabilities are used only to pick the main line (the point whose over is
    closest to even) and are never copied into the payload: the model must not be able to copy the
    market."""
    if odds is None:
        return {}, [], None
    try:
        rows = odds.execute(
            """
            SELECT book, market, player_id, player_name, side, point, implied_prob,
                   implied_prob_raw, received_at
            FROM live_book_odds
            WHERE game_id = ? AND snapshot_kind = 't60' AND received_at <= ?
              AND book IN (?, ?)
            ORDER BY book, market, player_id, point, side
            """,
            [game_id, t, PRIMARY_BOOK, SECOND_BOOK],
        ).fetchall()
    except duckdb.CatalogException:
        return {}, [], None
    latest = max((r[8] for r in rows), default=None)
    by: dict[tuple[str, str, int], dict[float, dict[str, Any]]] = defaultdict(dict)
    game_rows: list[list[Any]] = []
    for book, market, pid, pname, side, point, ip, ipr, rec in rows:
        if market in GAME_MARKET_SIDE:
            if side == GAME_MARKET_SIDE[market] and point is not None:
                game_rows.append([book, market, side, _clean(point), iso(rec)])
            continue
        stat = MARKET_STAT.get(market)
        if stat is None or pid is None or point is None or side not in ("over", "under"):
            continue
        cell = by[(book, stat, int(pid))].setdefault(float(point), {"name": pname})
        cell[side] = {"p": ip if ip is not None else ipr, "rec": rec}
    mains: dict[tuple[str, str, int], dict[str, Any]] = {}
    for key, pts in by.items():
        best: tuple[float, float] | None = None
        for point, cell in sorted(pts.items()):
            if "over" not in cell:
                continue
            p = cell["over"]["p"]
            dist = abs(float(p) - 0.5) if p is not None else 1.0
            if best is None or dist < best[0]:
                best = (dist, point)
        if best is None:
            continue
        cell = pts[best[1]]
        mains[key] = {
            "line": best[1],
            "name": cell["name"],
            "known_at": iso(cell["over"]["rec"]),
        }
    return mains, game_rows, latest


def _last10(
    con: duckdb.DuckDBPyConnection,
    player_ids: list[int],
    game_day: date,
    max_season: int | None,
) -> dict[int, list[list[Any]]]:
    """Trailing-10 played games per player, game_date strictly before ``game_day``."""
    if not player_ids:
        return {}
    marks = ",".join("?" for _ in player_ids)
    season_clause = "AND g.season <= ?" if max_season is not None else ""
    params: list[Any] = [*player_ids, game_day]
    if max_season is not None:
        params.append(max_season)
    params.append(TRAILING_N)
    rows = con.execute(
        f"""
        SELECT player_id, team_id, game_date, minutes, pts, reb, ast, fg3m, starter FROM (
            SELECT s.player_id, s.team_id, g.game_date, s.minutes, s.pts, s.reb, s.ast, s.fg3m,
                   s.starter, row_number() OVER (
                       PARTITION BY s.player_id ORDER BY g.game_date DESC, s.game_id DESC) rn
            FROM player_game_stats s JOIN games g USING (game_id)
            WHERE s.player_id IN ({marks}) AND s.minutes > 0 AND g.game_date < ?
              AND g.home_pts > 0 {season_clause})
        WHERE rn <= ? ORDER BY player_id, game_date DESC
        """,  # noqa: S608
        params,
    ).fetchall()
    out: dict[int, list[list[Any]]] = defaultdict(list)
    for pid, team, gd, mins, pts, reb, ast, fg3m, starter in rows:
        out[int(pid)].append(
            [
                str(gd),
                team,
                _clean(mins, 1),
                pts,
                reb,
                ast,
                fg3m,
                None if starter is None else int(starter),
            ]
        )
    return out


def _next_day_start_utc(d: date) -> datetime:
    """Start of the ET day after ``d`` (naive UTC): the date-rule bound on a box line's known_at."""
    nxt = datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=ET)
    return nxt.astimezone(UTC).replace(tzinfo=None)


def _team_context(
    con: duckdb.DuckDBPyConnection, team_ids: list[int], game_day: date, max_season: int | None
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    season_clause = "AND g.season <= ?" if max_season is not None else ""
    for tid in team_ids:
        params: list[Any] = [tid, tid, game_day]
        if max_season is not None:
            params.append(max_season)
        games = con.execute(
            f"""
            SELECT g.game_id, g.game_date FROM games g
            WHERE (g.home_team = ? OR g.away_team = ?) AND g.game_date < ? AND g.home_pts > 0
              {season_clause}
            ORDER BY g.game_date DESC, g.game_id DESC LIMIT {TRAILING_N}
            """,  # noqa: S608
            params,
        ).fetchall()
        if not games:
            out[str(tid)] = {
                "n_games": 0,
                "known_at": iso(_next_day_start_utc(game_day - timedelta(days=1))),
            }
            continue
        last_date = games[0][1]
        entry: dict[str, Any] = {
            "n_games": len(games),
            "rest_days": (game_day - last_date).days,
            "back_to_back": int((game_day - last_date).days == 1),
            "known_at": iso(_next_day_start_utc(last_date)),
        }
        ids = [g[0] for g in games]
        try:
            marks = ",".join("?" for _ in ids)
            row = con.execute(
                f"SELECT avg(pace), avg(off_rating), avg(def_rating), avg(net_rating), count(*) "  # noqa: S608
                f"FROM team_game_advanced WHERE team_id = ? AND game_id IN ({marks})",
                [tid, *ids],
            ).fetchone()
        except duckdb.CatalogException:
            row = None
        if row and row[4]:
            entry.update(
                trailing10_pace=_clean(row[0], 2),
                trailing10_off_rating=_clean(row[1], 2),
                trailing10_def_rating=_clean(row[2], 2),
                trailing10_net_rating=_clean(row[3], 2),
                trailing10_adv_n=int(row[4]),
            )
        out[str(tid)] = entry
    return out


def _facts_section(
    facts: duckdb.DuckDBPyConnection, game_id: str, t: datetime
) -> list[dict[str, Any]]:
    """As-of fact rows as compact dicts (None and default-confidence fields omitted), each with
    its own ``known_at``."""
    try:
        df = facts_query.as_of(facts, game_id, t)
    except facts_query.PostTipError as exc:
        raise PayloadRefused("facts_post_tip_or_no_tip", str(exc)) from None
    except duckdb.Error as exc:
        raise PayloadRefused("facts_unreadable", type(exc).__name__) from None
    out: list[dict[str, Any]] = []
    gent = game_id if game_id.startswith("game:") else f"game:{game_id}"
    for r in df.sort(["known_at", "fact_id"]).iter_rows(named=True):
        if r["predicate"] == "played_minutes" and r["object"] == gent:
            raise AsOfViolation(
                f"{game_id}: a played_minutes fact for this game is in the as-of set"
            )
        row: dict[str, Any] = {
            "subject": r["subject"],
            "predicate": r["predicate"],
            "known_at": iso(r["known_at"]),
        }
        if r["object"] is not None and r["object"] != gent:
            row["object"] = r["object"]
        for k in ("value", "value_text", "source"):
            if r[k] is not None:
                row[k] = _clean(r[k])
        if r["confidence"] is not None and r["confidence"] < 1.0:
            row["confidence"] = _clean(r["confidence"], 2)
        out.append(row)
    return out


def _injury_report_facts(
    nba_con: duckdb.DuckDBPyConnection,
    facts_con: duckdb.DuckDBPyConnection,
    game_id: str,
    tipoff: datetime,
    t: datetime,
    game_day: date,
    max_season: int | None,
) -> list[dict[str, Any]]:
    """The latest official injury-report snapshot for THIS game with ``as_of <= min(t, tip-60)``,
    chosen by production's own serving rule (``serve_pretip_flagged``, naive US-Eastern clock),
    one fact per listed player of BOTH teams. A game with no qualifying snapshot gets one explicit
    ``no_report`` fact, never "nobody out"."""
    from nba.daily.predict import injury_report_rows
    from nba.features.injury_report import OFFICIAL_REPORT_SOURCE, ReportTriggerConfig
    from nba.features.injury_report import serve_pretip_flagged as serve

    def to_et(x: datetime) -> datetime:
        return x.replace(tzinfo=UTC).astimezone(ET).replace(tzinfo=None)

    no_report = [
        {
            "subject": f"game:{game_id}",
            "predicate": "injury_report",
            "value_text": "no_report",
            "source": OFFICIAL_REPORT_SOURCE,
            "known_at": iso(t),
        }
    ]
    try:
        rows = injury_report_rows(nba_con, [game_id], game_dates={game_id: game_day})
        cfg = ReportTriggerConfig()
        _flagged, used = serve(rows, {game_id: to_et(tipoff)}, to_et(t), cfg)
        if game_id not in used:
            return no_report
        snap = used[game_id]
        got = nba_con.execute(
            "SELECT player_id, status, reason, CAST(as_of AS TIMESTAMP) FROM player_availability "
            "WHERE game_id = ? AND source = ? AND player_id IS NOT NULL "
            "AND CAST(as_of AS TIMESTAMP) = ? ORDER BY player_id, status",
            [game_id, OFFICIAL_REPORT_SOURCE, snap],
        ).fetchall()
    except duckdb.CatalogException:
        return no_report
    if not got:
        return no_report
    pids = sorted({int(r[0]) for r in got})
    marks = ",".join("?" for _ in pids)
    sc = "AND g.season <= ?" if max_season is not None else ""
    team_rows = nba_con.execute(
        f"""
        SELECT player_id, team_id FROM (
            SELECT s.player_id, s.team_id, row_number() OVER (
                PARTITION BY s.player_id ORDER BY g.game_date DESC, s.game_id DESC) rn
            FROM player_game_stats s JOIN games g USING (game_id)
            WHERE s.player_id IN ({marks}) AND g.game_date < ? {sc}) WHERE rn = 1
        """,  # noqa: S608
        [*pids, game_day, *([max_season] if max_season is not None else [])],
    ).fetchall()
    team = {int(a): b for a, b in team_rows}
    names: dict[int, str] = {}
    try:
        for pid, nm in facts_con.execute(
            f"SELECT entity_id, canonical_name FROM entities WHERE entity_id IN ({marks})",  # noqa: S608
            [f"player:{p}" for p in pids],
        ).fetchall():
            names[int(str(pid).split(":")[1])] = nm
    except duckdb.Error:
        pass
    known = iso(snap.replace(tzinfo=ET).astimezone(UTC).replace(tzinfo=None))
    out: list[dict[str, Any]] = []
    for pid, status, reason, _asof in got:
        out.append(
            {
                "subject": f"player:{int(pid)}",
                "predicate": "injury_report_status",
                "player_id": int(pid),
                "player_name": names.get(int(pid)),
                "team": team.get(int(pid)),
                "status": str(status).strip().capitalize(),
                "reason": reason,
                "source": OFFICIAL_REPORT_SOURCE,
                "known_at": known,
            }
        )
    return out


# ----------------------------------------------------------------------------------------------
# Build
# ----------------------------------------------------------------------------------------------


def _tip_of(con: duckdb.DuckDBPyConnection, game_id: str) -> datetime:
    row = con.execute(
        "SELECT max(tipoff) FROM forward_predictions WHERE game_id = ? AND model_name = ?",
        [game_id, WIN_MODEL],
    ).fetchone()
    if not row or row[0] is None:
        raise PayloadRefused("no_production_rows", f"{game_id}: no {WIN_MODEL} row")
    return naive_utc(row[0])


def _ro(path: Path | None) -> duckdb.DuckDBPyConnection | None:
    if path is None or not Path(path).exists():
        return None
    try:
        return duckdb.connect(str(path), read_only=True)
    except duckdb.Error:
        return None


def build_payload(
    game_id: str,
    *,
    now: datetime,
    paths: Mapping[str, Path],
    cfg: ShadowConfig | None = None,
    max_season: int | None = None,
) -> dict[str, Any]:
    """The as-of payload dict for ``game_id`` at request time ``now`` (naive UTC).

    ``paths`` keys: ``nba`` (box scores), ``forward`` (forward_predictions; usually the same
    file), ``facts``, ``market`` (live_book_odds). Keys: ``facts`` (list of dicts, each with
    ``known_at``) and ``legs`` (``player_id, stat, line, p_over_prod, ...``). Raises
    :class:`AsOfViolation` when ``now`` is later than tip - 60 min, :class:`PayloadRefused` when
    something needed is missing. Connections are read-only and closed before returning."""
    cfg = cfg or ShadowConfig()
    same = Path(paths["nba"]) == Path(paths["forward"])
    nba = _ro(paths["nba"])
    fwd = nba if same else _ro(paths["forward"])
    facts = _ro(paths.get("facts"))
    odds = _ro(paths.get("market"))
    try:
        if nba is None or fwd is None:
            raise PayloadRefused("db_unavailable", "nba/forward database cannot be opened")
        if facts is None:
            raise PayloadRefused("facts_unavailable", "no facts store")
        tip = _tip_of(fwd, game_id)
        return build_from_connections(
            nba,
            fwd,
            facts,
            odds,
            game_id=game_id,
            tipoff=tip,
            t=now,
            cfg=cfg,
            max_season=max_season,
        ).data
    finally:
        for c in {id(x): x for x in (nba, fwd, facts, odds) if x is not None}.values():
            c.close()


def build_from_connections(
    nba_con: duckdb.DuckDBPyConnection,
    fwd_con: duckdb.DuckDBPyConnection,
    facts_con: duckdb.DuckDBPyConnection,
    odds_con: duckdb.DuckDBPyConnection | None,
    *,
    game_id: str,
    tipoff: datetime,
    t: datetime,
    cfg: ShadowConfig,
    max_season: int | None = None,
) -> Payload:
    """The payload for ``game_id`` as known at ``t``. ``max_season`` caps the seasons read for box
    lines and team context (dry runs and estimates only; the live path passes None)."""
    t, tipoff = naive_utc(t), naive_utc(tipoff)
    if t > tipoff - timedelta(minutes=LEAD_MINUTES):
        raise AsOfViolation(
            f"{game_id}: t={iso(t)} is after tip-{LEAD_MINUTES} min "
            f"({iso(tipoff - timedelta(minutes=LEAD_MINUTES))})"
        )
    game_day = et_date(tipoff)
    prod = _production_rows(fwd_con, game_id, t)
    win = _win_section(prod)
    if "injury_elo" not in win:
        raise PayloadRefused("no_production_rows", f"{game_id}: no injury-Elo row made by {iso(t)}")
    prop_rows: dict[tuple[str, int], tuple[str, datetime, dict[str, Any]]] = {
        (tg, pid): (run_id, made_at, pred)
        for model, tg, pid, run_id, made_at, pred in prod
        if model == PROD_PROPS_MODEL and tg in STAT_ORDER
    }
    mains, game_lines, odds_known = _lines(odds_con, game_id, t)

    legs: list[dict[str, Any]] = []
    for (book, stat, pid), line in sorted(mains.items()):
        if book != PRIMARY_BOOK or (stat, pid) not in prop_rows:
            continue
        pt = float(line["line"])
        if pt == math.floor(pt):  # integer lines (push possible) are not sent
            continue
        run_id, made_at, pred = prop_rows[(stat, pid)]
        p_over, src = p_over_from_row(pred, pt)
        pin = mains.get((SECOND_BOOK, stat, pid))
        pin_pt = None if pin is None else float(pin["line"])
        legs.append(
            {
                "player_id": pid,
                "stat": stat,
                "line": _clean(pt),
                "book": PRIMARY_BOOK,
                "pinnacle_line": _clean(pin_pt) if pin_pt is not None else None,
                "p_over_prod": _clean(p_over),
                "p_over_source": src,
                "mean_prod": _clean(pred.get("mean")),
                "std_prod": _clean(pred.get("std")),
                "proj_minutes": _clean(pred.get("proj_minutes"), 1),
                "p_play": _clean(pred.get("p_play")),
                "prod_run_id": run_id,
                "prod_made_at": iso(made_at),
                "known_at": max(str(line["known_at"]), str(iso(made_at))),
                "_name": line["name"],
            }
        )
    if not legs:
        raise PayloadRefused(
            "no_legs", f"{game_id}: no non-integer DK prop line with a production row by {iso(t)}"
        )
    # rank by projected minutes; keep the top legs; the input cap drops more (lowest minutes first)
    legs.sort(
        key=lambda L: (-(L["proj_minutes"] or 0.0), L["player_id"], STAT_ORDER.index(L["stat"]))
    )
    ranked = legs[: cfg.max_legs_per_game]
    inj = win["injury_elo"]
    team_ids = [int(x) for x in (inj.get("home_team"), inj.get("away_team")) if x]
    all_facts = _facts_section(facts_con, game_id, t)
    report_facts = _injury_report_facts(
        nba_con, facts_con, game_id, tipoff, t, game_day, max_season
    )
    team_ctx = _team_context(nba_con, team_ids, game_day, max_season)
    n_keep = len(ranked)
    while True:
        kept = sorted(
            (dict(L) for L in ranked[:n_keep]),
            key=lambda L: (L["player_id"], STAT_ORDER.index(L["stat"])),
        )
        pids = sorted({int(L["player_id"]) for L in kept})
        names = {int(L["player_id"]): L.pop("_name") for L in kept}
        last10 = _last10(nba_con, pids, game_day, max_season)
        players: dict[str, Any] = {}
        for pid in pids:
            lines10 = last10.get(pid, [])
            players[str(pid)] = {
                "name": names.get(pid),
                "last10": lines10,
                "known_at": iso(_next_day_start_utc(date.fromisoformat(lines10[0][0])))
                if lines10
                else None,
            }
        wanted = {
            f"game:{game_id}",
            *(f"team:{x}" for x in team_ids),
            *(f"player:{x}" for x in pids),
        }
        data: dict[str, Any] = {
            "schema": SCHEMA,
            "game": {
                "game_id": game_id,
                "tip_utc": iso(tipoff),
                "et_date": game_day.isoformat(),
                "as_of_t_utc": iso(t),
                "minutes_to_tip": int((tipoff - t).total_seconds() // 60),
                "home_team": inj.get("home_team"),
                "away_team": inj.get("away_team"),
                "n_legs_dropped_for_input_cap": len(ranked) - n_keep,
                "last10_columns": [
                    "date",
                    "team_id",
                    "min",
                    "pts",
                    "reb",
                    "ast",
                    "fg3m",
                    "starter",
                ],
                "known_at": iso(t),
            },
            "production_game": win,
            "team_context": team_ctx,
            "game_lines": {
                "columns": ["book", "market", "side", "point", "known_at"],
                "rows": sorted(game_lines, key=lambda r: [str(x) for x in r]),
                "known_at": iso(odds_known),
            },
            "facts": [f for f in all_facts if f["subject"] in wanted] + report_facts,
            "players": players,
            "legs": kept,
        }
        text = canonical(data)
        est = int(len(text) / CHARS_PER_TOKEN) + 1
        if est <= cfg.input_token_cap or n_keep <= 1:
            break
        n_keep = max(1, n_keep - 2)
    if est > cfg.input_token_cap:
        raise PayloadRefused("input_cap", f"{game_id}: ~{est} tokens > cap {cfg.input_token_cap}")
    n = assert_as_of(data, t)
    if n == 0:  # pragma: no cover - defensive: a payload with no stamps would be unauditable
        raise AsOfViolation(f"{game_id}: payload carries no known_at stamps")
    return Payload(
        data=data,
        text=text,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        legs=kept,
        est_tokens=est,
    )


def payload_sha256(data: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(data).encode()).hexdigest()
