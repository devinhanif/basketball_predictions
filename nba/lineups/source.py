"""NBA daily-lineups feed: fetch (one GET per poll) and parse.

Source: ``https://stats.nba.com/js/data/leaders/00_daily_lineups_YYYYMMDD.json``, a static
file served from Akamai NetStorage (not the metered stats API). One file per Eastern game
date holds EVERY game of that day::

    {"games": [{"gameId", "gameStatus", "gameStatusText",
                "homeTeam": {"teamId", "teamAbbreviation", "players": [...]},
                "awayTeam": {...}}]}

Each player carries ``lineupStatus`` ('Expected' = projected five, 'Confirmed' = official),
``position`` (non-empty only for the five starters), ``rosterStatus`` ('Active' or
'Inactive') and ``timestamp`` (the source's own clock, naive US-Eastern). An 'Expected'
team lists just the projected five; a 'Confirmed' team lists its whole active roster.
cdn.nba.com returned 403 (Akamai Access Denied) from this machine, so it is not used.

The ``timestamp`` is the source's publication time for that team's block; it is stored next
to our own ``fetched_at`` so announcement lead times can be measured.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

ET = ZoneInfo("America/New_York")
URL_TEMPLATE = "https://stats.nba.com/js/data/leaders/00_daily_lineups_{ymd}.json"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.nba.com/",
}
CONFIRMED = "Confirmed"
EXPECTED = "Expected"
_TIP_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*([ap])m\s+ET\s*$", re.IGNORECASE)


def lineups_url(game_date: date) -> str:
    return URL_TEMPLATE.format(ymd=game_date.strftime("%Y%m%d"))


@dataclass(frozen=True)
class RawFetch:
    """One HTTP response. ``fetched_at`` is naive UTC, stamped right after the response."""

    url: str
    status: int
    body: bytes
    fetched_at: datetime
    last_modified: datetime | None = None  # naive UTC, HTTP Last-Modified
    etag: str | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()


@dataclass(frozen=True)
class LineupRow:
    game_id: str
    game_status: int
    game_status_text: str
    team_id: int
    team_abbr: str
    is_home: bool
    player_id: int
    player_name: str
    lineup_status: str
    position: str
    announced_starter: bool
    roster_status: str
    source_ts: datetime | None  # naive UTC, converted from the feed's Eastern clock


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def fetch_daily_lineups(game_date: date, *, client: httpx.Client | None = None) -> RawFetch:
    """One read-only GET. Never raises on HTTP status (the caller records it)."""
    url = lineups_url(game_date)
    owns = client is None
    c = client if client is not None else httpx.Client(timeout=20.0)
    try:
        r = c.get(url, headers=HEADERS)
    finally:
        if owns:
            c.close()
    lm: datetime | None = None
    if r.headers.get("last-modified"):
        try:
            lm = parsedate_to_datetime(r.headers["last-modified"]).astimezone(UTC)
            lm = lm.replace(tzinfo=None)
        except (TypeError, ValueError):
            lm = None
    return RawFetch(url, r.status_code, bytes(r.content), utc_now(), lm, r.headers.get("etag"))


def _et_to_utc(text: object) -> datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        d = datetime.fromisoformat(text)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=ET)
    return d.astimezone(UTC).replace(tzinfo=None)


def tip_from_status_text(text: str, game_date: date) -> datetime | None:
    """'8:00 pm ET' (a not-yet-started game) -> naive-UTC tip-off on ``game_date`` (ET date).
    Returns None for any other status text (live, final, ...)."""
    m = _TIP_RE.match(text)
    if m is None:
        return None
    hour, minute, ap = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    hour = hour % 12 + (12 if ap == "p" else 0)
    local = datetime(game_date.year, game_date.month, game_date.day, hour, minute, tzinfo=ET)
    return local.astimezone(UTC).replace(tzinfo=None)


def parse_daily_lineups(body: bytes | str | dict[str, Any]) -> list[LineupRow]:
    """Flatten the feed into one row per (game, team, player). Raises ValueError if the
    payload is not the expected shape (so a garbage/HTML 200 is never stored as data)."""
    try:
        doc = json.loads(body) if isinstance(body, bytes | str) else body
        games = doc["games"]
        if not isinstance(games, list):
            raise TypeError("games is not a list")
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"unexpected daily-lineups payload: {exc!r}") from exc
    rows: list[LineupRow] = []
    for g in games:
        for side, is_home in (("homeTeam", True), ("awayTeam", False)):
            if not isinstance(g, dict) or side not in g:
                raise ValueError("unexpected daily-lineups payload: malformed game")
            team = g[side]
            for p in team.get("players", []):
                pos = str(p.get("position") or "").strip()
                rows.append(
                    LineupRow(
                        game_id=str(g["gameId"]),
                        game_status=int(g.get("gameStatus", 0)),
                        game_status_text=str(g.get("gameStatusText", "")).strip(),
                        team_id=int(team["teamId"]),
                        team_abbr=str(team.get("teamAbbreviation", "")),
                        is_home=is_home,
                        player_id=int(p["personId"]),
                        player_name=str(p.get("playerName", "")),
                        lineup_status=str(p.get("lineupStatus", "")),
                        position=pos,
                        announced_starter=bool(pos),
                        roster_status=str(p.get("rosterStatus", "")),
                        source_ts=_et_to_utc(p.get("timestamp")),
                    )
                )
    return rows
