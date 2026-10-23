"""Readers for each pre-tip source. Every read is as-of before tip; nothing here reads season 2025.

All lines are keyed ``(game_id, player_id, stat, k)`` with the value ``P(stat >= k)``. A Kalshi
"k+" market is the sportsbook over at ``k - 0.5``.
"""

from __future__ import annotations

import glob
import gzip
import json
import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from nba.forward.scorecard import SEASON_START, STATS
from nba.parlay.top_source import p_over

NY = ZoneInfo("America/New_York")
PROD = "props_context_residual"
LLM = "llm_shadow"
KALSHI_SERIES = ("KXNBAPTS", "KXNBAREB", "KXNBAAST", "KXNBA3PT")
KALSHI_WORD = {"points": "pts", "rebounds": "reb", "assists": "ast", "threes": "fg3m"}
TITLE_RE = re.compile(r"^(.+?): (\d+)\+ (points|rebounds|assists|threes)$")
STAT_SYNONYM = {**{s: s for s in STATS}, **KALSHI_WORD, "3pt": "fg3m", "threes_made": "fg3m"}
WINDOW = timedelta(minutes=10)

Key = tuple[str, int, str, int]


@dataclass(frozen=True)
class Game:
    game_id: str
    tip: datetime  # timezone-aware UTC
    et_date: date
    teams: tuple[int, int]
    settled: bool


def nz(s: str) -> str:
    """Name key (STAR_FLOOR_PAPER): NFKD, ASCII, lowercase letters only."""
    return re.sub(
        r"[^a-z]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    )


def half_line_k(line: float | None) -> int | None:
    """k for a sportsbook x.5 line (over L == stat >= k, k = L + 0.5); None for whole lines."""
    if line is None or abs(line - math.floor(line) - 0.5) > 1e-9:
        return None
    return int(math.ceil(line))


def load_games(con: Any, since: date, until: date) -> list[Game]:
    """Games with forward predictions whose America/New_York tip date is in [since, until]."""
    rows = con.execute(
        """SELECT p.game_id, any_value(p.tipoff), any_value(g.home_team), any_value(g.away_team),
                  bool_or(g.home_pts IS NOT NULL AND g.away_pts IS NOT NULL)
           FROM forward_predictions p JOIN games g USING (game_id)
           WHERE g.game_date >= ? GROUP BY p.game_id ORDER BY 2, 1""",
        [SEASON_START],
    ).fetchall()
    out = []
    for gid, tip, h, a, done in rows:
        tip_utc = tip.replace(tzinfo=UTC)
        d = tip_utc.astimezone(NY).date()
        if since <= d <= until:
            out.append(Game(gid, tip_utc, d, (int(h), int(a)), bool(done)))
    return out


def load_box(con: Any, game_ids: list[str]) -> dict[tuple[str, int], dict[str, Any]]:
    """Box score rows for these (season 2026) games: minutes and the four stats."""
    if not game_ids:
        return {}
    marks = ",".join("?" for _ in game_ids)
    raw = con.execute(
        f"""SELECT s.game_id, s.player_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m
            FROM player_game_stats s JOIN games g USING (game_id)
            WHERE s.game_id IN ({marks}) AND g.game_date >= ?""",  # noqa: S608
        [*game_ids, SEASON_START],
    ).fetchall()
    return {
        (g, int(p)): {"minutes": m, "pts": a, "reb": b, "ast": c, "fg3m": d}
        for g, p, m, a, b, c, d in raw
    }


def load_forward(
    con: Any, game_ids: list[str], model: str
) -> dict[tuple[str, int, str], dict[str, Any]]:
    """Latest pre-tip prediction per (game, player, stat) for ``model`` (made_at < tipoff)."""
    if not game_ids:
        return {}
    gm = ",".join("?" for _ in game_ids)
    sm = ",".join("?" for _ in STATS)
    raw = con.execute(
        f"""SELECT game_id, player_id, target, CAST(prediction AS VARCHAR) FROM (
              SELECT *, row_number() OVER (PARTITION BY game_id, target, player_id
                                           ORDER BY made_at DESC, run_id DESC) rn
              FROM forward_predictions
              WHERE model_name = ? AND game_id IN ({gm}) AND target IN ({sm})
                AND player_id IS NOT NULL AND made_at < tipoff) WHERE rn = 1""",  # noqa: S608
        [model, *game_ids, *STATS],
    ).fetchall()
    out: dict[tuple[str, int, str], dict[str, Any]] = {}
    for gid, pid, tgt, pj in raw:
        d = json.loads(pj) if isinstance(pj, str) else pj
        if isinstance(d, dict):
            out[(gid, int(pid), tgt)] = d
    return out


def model_p(pred: dict[str, Any], k: int) -> float | None:
    """P(stat >= k) from the production row's integer-grid counts (None if the row has none)."""
    return p_over(pred, k - 0.5)


def llm_p(pred: dict[str, Any]) -> tuple[int, float] | None:
    """(k, P(stat >= k)) from an llm_shadow row (its p_over is for the book's x.5 line)."""
    k = half_line_k(_f(pred.get("line")))
    p = _f(pred.get("p_over"))
    return None if k is None or p is None or not 0.0 <= p <= 1.0 else (k, p)


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _snap_time(path: str) -> datetime:
    return datetime.strptime(Path(path).name[:15], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)


def kalshi_lines(
    game: Game,
    root: Path,
    name_map: dict[str, set[int]],
    membership: dict[int, set[int]],
    _cache: dict[str, Any] | None = None,
) -> tuple[dict[Key, dict[str, float]], dict[str, int]]:
    """Kalshi mid per line from the last snapshot in [tip - 10 min, tip) of each series.

    Matching rule (STAR_FLOOR_PAPER): the date token of ``event_ticker`` (YYMONDD) equals the
    game's America/New_York date AND the player's team is one of the game's two teams; status
    ``active``; title ``<Name>: <k>+ <stat>``; 0 < bid < ask < 1. Zero or several name matches drop
    the line, and every drop is counted.
    """
    cache = _cache if _cache is not None else {}
    drops: dict[str, int] = defaultdict(int)
    out: dict[Key, dict[str, float]] = {}
    token = game.et_date.strftime("%y%b%d").upper()
    for series in KALSHI_SERIES:
        files = sorted(
            glob.glob(
                str(
                    root
                    / f"live_{game.et_date.isoformat()}"
                    / "kalshi"
                    / f"{series}_p0"
                    / "*.json.gz"
                )
            )
        )
        pre = [f for f in files if game.tip - WINDOW <= _snap_time(f) < game.tip]
        if not pre:
            drops["no_snapshot_in_window"] += 1
            continue
        if pre[-1] not in cache:
            try:
                with gzip.open(pre[-1], "rt") as fh:
                    cache[pre[-1]] = json.load(fh)
            except (OSError, ValueError):
                drops["unreadable_snapshot"] += 1
                continue
        for m in cache[pre[-1]].get("markets", []):
            ev = str(m.get("event_ticker", "")).split("-")
            if len(ev) < 2 or ev[1][:7] != token:
                continue
            mm = TITLE_RE.match(m.get("title") or "")
            if not mm or m.get("status") != "active":
                continue
            try:
                bid, ask = (
                    Decimal(str(m.get("yes_bid_dollars"))),
                    Decimal(str(m.get("yes_ask_dollars"))),
                )
            except InvalidOperation:
                drops["bad_price"] += 1
                continue
            if not (0 < bid < ask < 1):
                drops["bad_price"] += 1
                continue
            pids = name_map.get(nz(mm.group(1)), set())
            if len(pids) != 1:
                drops["name_not_in_this_game" if not pids else "name_ambiguous"] += 1
                continue
            pid = next(iter(pids))
            if not (membership.get(pid, set()) & set(game.teams)):
                drops["not_on_game_team"] += 1
                continue
            key = (game.game_id, pid, KALSHI_WORD[mm.group(3)], int(mm.group(2)))
            out[key] = {"mid": float((bid + ask) / 2), "bid": float(bid), "ask": float(ask)}
    return out, dict(drops)


def load_reads(
    path: Path,
    games: dict[str, Game],
    names_by_game: dict[str, dict[str, set[int]]],
) -> tuple[dict[Key, dict[str, Any]], dict[str, int]]:
    """Devin's reads (append-only JSONL). Only reads stamped before the game's tip are scored.

    ``p_or_call`` is a number (0-1, or a percent up to 100) = the probability that the stated
    ``side`` wins, or a call word (yes/over/no/under). ``side`` yes/over means stat >= k; no/under
    means stat < k. Several reads of one line: the latest before tip stands.
    """
    drops: dict[str, int] = defaultdict(int)
    best: dict[Key, tuple[datetime, dict[str, Any]]] = {}
    if not path.exists():
        return {}, dict(drops)
    for raw in path.read_text().splitlines():
        if not raw.strip():
            continue
        try:
            r = json.loads(raw)
            ts = datetime.fromisoformat(str(r["ts_utc"]).replace("Z", "+00:00"))
            ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
            gid, stat, k = str(r["game_id"]), STAT_SYNONYM[str(r["stat"]).lower()], int(r["k"])
            side = str(r["side"]).strip().lower()
            if side not in ("yes", "over", "no", "under"):
                raise ValueError(side)
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            drops["malformed"] += 1
            continue
        g = games.get(gid)
        if g is None:
            drops["game_not_in_range"] += 1
            continue
        if ts >= g.tip:
            drops["after_tip_rejected"] += 1
            continue
        pids = names_by_game.get(gid, {}).get(nz(str(r.get("player", ""))), set())
        if len(pids) != 1:
            drops["name_not_in_this_game" if not pids else "name_ambiguous"] += 1
            continue
        up = side in ("yes", "over")
        v = r.get("p_or_call")
        kind, p_side = "", 0.0
        pv = _f(v)
        if pv is not None and not isinstance(v, bool):
            pv = pv / 100.0 if pv > 1.0 else pv
            if not 0.0 <= pv <= 1.0:
                drops["malformed"] += 1
                continue
            kind, p_side = "p", pv
        elif str(v).strip().lower() in ("yes", "over", "no", "under"):
            kind, p_side = "call", 1.0 if str(v).strip().lower() in ("yes", "over") else 0.0
            up = True  # a call word is read as the direction on stat >= k
        else:
            drops["no_probability_or_call"] += 1
            continue
        p_ge = p_side if up else 1.0 - p_side
        key = (gid, next(iter(pids)), stat, k)
        if key not in best or ts > best[key][0]:
            best[key] = (
                ts,
                {"p": p_ge, "kind": kind, "price_seen": r.get("price_seen"), "ts": ts.isoformat()},
            )
    return {k: v for k, (_, v) in best.items()}, dict(drops)
