"""Read-only view of a live capture directory (``data/raw/live_<date>/``), as of a given time.

Raw files are write-once gzip JSON named ``<UTC %Y%m%dT%H%M%S>Z[_n].json.gz``. Nothing here writes.
"""

from __future__ import annotations

import gzip
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nba.live import kalshi_pull as kp
from nba.live.capture import ESPN_TO_KALSHI
from nba.ride.winprob import parse_clock

_STAMP = re.compile(r"^(\d{8}T\d{6})Z")


def file_ts(path: Path) -> datetime | None:
    m = _STAMP.match(path.name)
    if m is None:
        return None
    return datetime.strptime(m[1], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)


def read_json(path: Path) -> Any:
    try:
        with gzip.open(path, "rb") as f:
            return json.loads(f.read())
    except (OSError, ValueError, EOFError):
        return None  # a half-written or corrupt file is skipped, never fatal


@dataclass(frozen=True)
class Play:
    period: int
    clock_secs: float
    home: int
    away: int
    wallclock: datetime | None
    scoring: bool
    team_id: str


@dataclass(frozen=True)
class GameSnap:
    event_id: str
    away: str  # Kalshi abbreviations
    home: str
    state: str  # pre | in | post
    period: int
    clock_secs: float
    home_score: int
    away_score: int
    espn_home_wp: float | None
    file_time: datetime
    plays: tuple[Play, ...]
    home_team_id: str = ""

    @property
    def margin(self) -> int:
        return self.home_score - self.away_score


def _int(x: Any, default: int = 0) -> int:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return default


def parse_summary(body: dict[str, Any], event_id: str, ts: datetime) -> GameSnap | None:
    try:
        comp = body["header"]["competitions"][0]
        status = comp["status"]
        teams = {c["homeAway"]: c for c in comp["competitors"]}
        away = ESPN_TO_KALSHI.get(
            teams["away"]["team"]["abbreviation"], teams["away"]["team"]["abbreviation"]
        )
        home = ESPN_TO_KALSHI.get(
            teams["home"]["team"]["abbreviation"], teams["home"]["team"]["abbreviation"]
        )
        state = str(status["type"]["state"])
        period = _int(status.get("period"))
        clock = parse_clock(str(status.get("displayClock", "0")))
        hs, as_ = _int(teams["home"].get("score")), _int(teams["away"].get("score"))
        home_id = str(teams["home"]["team"].get("id", ""))
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    plays: list[Play] = []
    play_ids: list[int] = []
    for p in body.get("plays") or []:
        try:
            wc = p.get("wallclock")
            plays.append(
                Play(
                    _int(p["period"]["number"]),
                    parse_clock(str(p["clock"]["displayValue"])),
                    _int(p.get("homeScore")),
                    _int(p.get("awayScore")),
                    datetime.fromisoformat(wc.replace("Z", "+00:00")) if wc else None,
                    bool(p.get("scoringPlay")),
                    str((p.get("team") or {}).get("id", "")),
                )
            )
            play_ids.append(_int(p["id"]))
        except (KeyError, TypeError, ValueError):
            continue
    wp = None
    if play_ids:
        last = max(play_ids)
        best = -1
        for w in body.get("winprobability") or []:  # entries after the last play are post-game junk
            pid = _int(w.get("playId"), -1)
            if best < pid <= last and w.get("homeWinPercentage") is not None:
                best, wp = pid, float(w["homeWinPercentage"])
    return GameSnap(
        event_id, away, home, state, period, clock, hs, as_, wp, ts, tuple(plays), home_id
    )


@dataclass(frozen=True)
class Quote:
    ticker: str
    bid: float | None
    ask: float | None
    bid_size: float | None
    ask_size: float | None
    source: str  # book | market
    at: datetime


def book_quote(ticker: str, book: dict[str, Any], at: datetime) -> Quote | None:
    """YES bid = best YES bid; YES ask = 1 - best NO bid (taker buys YES against NO bidders)."""
    fp = book.get("orderbook_fp") or {}

    def levels(key: str) -> list[tuple[float, float]]:
        out = []
        for lv in fp.get(key) or []:
            try:
                out.append((float(lv[0]), float(lv[1])))
            except (TypeError, ValueError, IndexError):
                continue
        return out

    yes, no = levels("yes_dollars"), levels("no_dollars")
    if not yes and not no:
        return None
    by, bn = (max(yes) if yes else None), (max(no) if no else None)
    return Quote(
        ticker,
        by[0] if by else None,
        round(1.0 - bn[0], 4) if bn else None,
        by[1] if by else None,
        bn[1] if bn else None,
        "book",
        at,
    )


def market_quote(m: dict[str, Any], at: datetime) -> Quote:
    return Quote(
        str(m.get("ticker", "")),
        kp.to_f(m.get("yes_bid_dollars")),
        kp.to_f(m.get("yes_ask_dollars")),
        kp.to_f(m.get("yes_bid_size_fp")),
        kp.to_f(m.get("yes_ask_size_fp")),
        "market",
        at,
    )


class CaptureView:
    def __init__(self, root: Path) -> None:
        self.root = root

    def files(self, source: str, key: str) -> list[tuple[datetime, Path]]:
        d = self.root / source / key
        if not d.is_dir():
            return []
        out = [(t, p) for p in d.glob("*.json.gz") if (t := file_ts(p)) is not None]
        return sorted(out)

    def latest(
        self, source: str, key: str, now: datetime, back: int = 1
    ) -> list[tuple[datetime, Path]]:
        fs = [x for x in self.files(source, key) if x[0] <= now]
        return fs[-back:][::-1]

    def event_ids(self) -> list[str]:
        d = self.root / "espn_summary"
        return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.is_dir() else []

    def snap(self, event_id: str, now: datetime) -> GameSnap | None:
        for ts, path in self.latest("espn_summary", event_id, now, back=3):
            body = read_json(path)
            if isinstance(body, dict):
                s = parse_summary(body, event_id, ts)
                if s is not None:
                    return s
        return None

    def heartbeat_age_s(self, now: datetime, *, replay: bool) -> float | None:
        """Seconds since the capture last proved it was alive. Live: heartbeat.json ``last_tick_ts``
        with status running. Replay: age of the newest captured file of any source."""
        if not replay:
            try:
                hb = json.loads((self.root / "heartbeat.json").read_text())
                if hb.get("status") != "running":
                    return None
                last = datetime.fromisoformat(str(hb["last_tick_ts"]))
                return (now - last).total_seconds()
            except (OSError, ValueError, KeyError):
                return None
        newest: datetime | None = None
        for src in ("espn_scoreboard", "espn_summary", "kalshi"):
            d = self.root / src
            if not d.is_dir():
                continue
            for sub in d.iterdir():
                fs = self.latest(src, sub.name, now)
                if fs and (newest is None or fs[0][0] > newest):
                    newest = fs[0][0]
        return None if newest is None else (now - newest).total_seconds()

    def game_market_ticker(
        self, snap: GameSnap, team: str, now: datetime
    ) -> tuple[str, dict[str, Any], datetime] | None:
        """(ticker, market, file time) of the KXNBAGAME YES market for ``team`` in this game."""
        d = self.root / "kalshi"
        if not d.is_dir():
            return None
        for sub in sorted(d.glob("KXNBAGAME_p*")):
            for ts, path in self.latest("kalshi", sub.name, now, back=2):
                body = read_json(path)
                for m in (body or {}).get("markets", []) if isinstance(body, dict) else []:
                    t = str(m.get("ticker", ""))
                    if (
                        kp.event_teams(str(m.get("event_ticker", ""))) == (snap.away, snap.home)
                        and t.endswith("-" + team)
                        and "KXNBAGAME" in t
                        and kp.event_date(str(m.get("event_ticker", ""))) is not None
                    ):
                        return t, m, ts
        return None

    def quote(
        self, ticker: str, market: dict[str, Any], market_ts: datetime, now: datetime, date_key: str
    ) -> Quote:
        """Order-book quote if a book newer than the listing holds the ticker, else the listing."""
        for ts, path in self.latest("kalshi_orderbook", date_key, now, back=3):
            if ts < market_ts:
                break
            body = read_json(path)
            ent = (body or {}).get(ticker) if isinstance(body, dict) else None
            if isinstance(ent, dict) and isinstance(ent.get("orderbook"), dict):
                q = book_quote(ticker, ent["orderbook"], ts)
                if q is not None and q.ask is not None:
                    return q
        return market_quote(market, market_ts)
