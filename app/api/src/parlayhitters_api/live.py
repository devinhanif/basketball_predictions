"""Live pane and parlay tracker feeds: public, read-only, cached, and never from the browser.

Two public sources, both fetched here with a short timeout and a 20 s cache:
  * ESPN's unofficial NBA scoreboard and game summary (no key).
  * Kalshi's public market data (no key, no login, no account).
Nothing here places an order, reads an account, or holds a credential. A public deployment of
either feed is a licence decision for the maintainer, not made here.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from .forecast import forecast_probs

Json = dict[str, Any]

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
KALSHI_MARKETS = "https://api.elections.kalshi.com/trade-api/v2/markets"
TTL_S = 20.0
STALE_OK_S = 300.0
TIMEOUT_S = 8.0
MAX_TICKERS = 25
TICKER_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
EVENT_RE = re.compile(r"^[0-9]{3,20}$")
STATS = ("pts", "reb", "ast", "fg3m")


class SourceDown(Exception):
    """The upstream feed did not answer or answered nonsense and no recent copy exists."""


def http_get(url: str) -> Json:
    """One GET with a short timeout. Tests replace this; nothing else touches the network."""
    if not url.startswith(("https://site.api.espn.com/", "https://api.elections.kalshi.com/")):
        raise SourceDown("refused: not an allowed host")
    req = urllib.request.Request(  # noqa: S310 - host allow-listed just above
        url, headers={"Accept": "application/json", "User-Agent": "parlayhitters-readonly/0.1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310
            data = json.loads(resp.read(8_000_000))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        raise SourceDown(type(e).__name__) from e
    if not isinstance(data, dict):
        raise SourceDown("unexpected response shape")
    return data


_cache: dict[str, tuple[float, Json]] = {}


def clear_cache() -> None:
    _cache.clear()


def cached(
    key: str, url: str, fetch: Callable[[str], Json] | None = None, now: float | None = None
) -> tuple[Json, bool, float]:
    """(payload, stale, age_s). A fresh copy within TTL_S; else fetch; if the fetch fails, a copy up
    to STALE_OK_S old is served marked stale; else SourceDown."""
    t = time.monotonic() if now is None else now
    hit = _cache.get(key)
    if hit and t - hit[0] < TTL_S:
        return hit[1], False, t - hit[0]
    try:
        data = (fetch or http_get)(url)
    except SourceDown:
        if hit and t - hit[0] < STALE_OK_S:
            return hit[1], True, t - hit[0]
        raise
    _cache[key] = (t, data)
    return data, False, 0.0


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # noqa: PLR0124 - NaN check


def _int(v: Any) -> int | None:
    f = _num(v)
    return None if f is None else int(f)


def norm_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", "", s.replace("-", " "))
    parts = [p for p in s.split() if p not in {"jr", "sr", "ii", "iii", "iv"}]
    return " ".join(parts)


def et_date(iso_utc: str) -> str | None:
    try:
        d = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(ZoneInfo("America/New_York")).date().isoformat()


# ----------------------------------------------------------------------------- ESPN


def _status(s: Json) -> Json:
    t = s.get("type") or {}
    state = t.get("state")
    return {
        "state": state if state in ("pre", "in", "post") else "pre",
        "period": _int(s.get("period")) or 0,
        "clock": str(s.get("displayClock") or ""),
        "detail": str(t.get("shortDetail") or t.get("detail") or ""),
        "completed": bool(t.get("completed")),
    }


def _competitor(c: Json) -> Json:
    team = c.get("team") or {}
    return {
        "side": c.get("homeAway") if c.get("homeAway") in ("home", "away") else "home",
        "abbr": str(team.get("abbreviation") or ""),
        "name": str(team.get("displayName") or ""),
        "score": _int(c.get("score")),
    }


def parse_scoreboard(raw: Json) -> list[Json]:
    out: list[Json] = []
    for ev in raw.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        teams = [_competitor(c) for c in comp.get("competitors") or []]
        home = next((t for t in teams if t["side"] == "home"), None)
        away = next((t for t in teams if t["side"] == "away"), None)
        if not ev.get("id") or home is None or away is None:
            continue
        start = str(ev.get("date") or "")
        out.append(
            {
                "event_id": str(ev["id"]),
                "start_utc": start,
                "et_date": et_date(start),
                "status": _status(ev.get("status") or comp.get("status") or {}),
                "home": home,
                "away": away,
            }
        )
    return out


LABEL_KEYS = {"MIN": "min", "PTS": "pts", "REB": "reb", "AST": "ast", "PF": "pf", "+/-": "pm"}


def _made(v: str) -> int | None:
    m = re.match(r"^(\d+)-\d+$", v.strip())
    return int(m.group(1)) if m else None


def parse_summary(raw: Json) -> Json:
    """Teams, status, and one box-score row per player who has a line."""
    header = (raw.get("header") or {}).get("competitions") or [{}]
    comp = header[0]
    teams = [_competitor(c) for c in comp.get("competitors") or []]
    home = next((t for t in teams if t["side"] == "home"), None)
    away = next((t for t in teams if t["side"] == "away"), None)
    players: list[Json] = []
    for block in (raw.get("boxscore") or {}).get("players") or []:
        tabbr = str((block.get("team") or {}).get("abbreviation") or "")
        for grp in block.get("statistics") or []:
            labels = [str(x) for x in grp.get("labels") or grp.get("names") or []]
            for a in grp.get("athletes") or []:
                ath = a.get("athlete") or {}
                vals = [str(x) for x in a.get("stats") or []]
                row: Json = {
                    "player_id": str(ath.get("id") or ""),
                    "name": str(ath.get("displayName") or ""),
                    "team": tabbr,
                    "starter": bool(a.get("starter")),
                    "dnp": bool(a.get("didNotPlay")) or not vals,
                    "min": None, "pts": None, "reb": None, "ast": None,  # noqa: E501
                    "fg3m": None, "pf": None, "pm": None,
                }  # fmt: skip
                for lab, v in zip(labels, vals, strict=False):
                    if lab in LABEL_KEYS:
                        row[LABEL_KEYS[lab]] = v if lab in ("MIN", "+/-") else _int(v)
                    elif lab == "3PT":
                        row["fg3m"] = _made(v)
                if row["name"]:
                    players.append(row)
    return {
        "event_id": str((raw.get("header") or {}).get("id") or ""),
        "status": _status(comp.get("status") or {}),
        "home": home,
        "away": away,
        "players": players,
        "box_available": bool(players),
    }


def forecasts_for(slate: Json | None) -> dict[str, Json]:
    """Pre-game forecast means by normalised player name, from the export slate of that date."""
    out: dict[str, Json] = {}
    if not slate:
        return out
    for g in slate.get("games") or []:
        for pl in g.get("players") or []:
            props: Json = {}
            for st in STATS:
                p = (pl.get("props") or {}).get(st) or {}
                mean = (p.get("forecast") or {}).get("mean")
                if mean is not None:
                    props[st] = {"mean": float(mean), "line": p.get("line")}
            if props:
                out[norm_name(str(pl["name"]))] = props
    return out


def attach_forecasts(summary: Json, fc: dict[str, Json]) -> None:
    for p in summary["players"]:
        p["forecast"] = fc.get(norm_name(p["name"]))


# ----------------------------------------------------------------------------- Kalshi


def _price(m: Json, key: str) -> float | None:
    """Dollars 0..1. Kalshi serves either `<key>_dollars` (string) or `<key>` in cents."""
    d = _num(m.get(f"{key}_dollars"))
    if d is not None:
        return d
    c = _num(m.get(key))
    return None if c is None else c / 100.0


def parse_kalshi(raw: Json, asked: list[str]) -> list[Json]:
    found: dict[str, Json] = {}
    for m in raw.get("markets") or []:
        t = str(m.get("ticker") or "")
        found[t] = {
            "ticker": t,
            "title": str(m.get("title") or m.get("yes_sub_title") or ""),
            "status": str(m.get("status") or ""),
            "result": str(m.get("result") or "") or None,
            "yes_bid": _price(m, "yes_bid"),
            "yes_ask": _price(m, "yes_ask"),
            "no_bid": _price(m, "no_bid"),
            "no_ask": _price(m, "no_ask"),
            "last": _price(m, "last_price"),
            "found": True,
        }
    return [
        found.get(t)
        or {
            "ticker": t,
            "title": "",
            "status": "",
            "result": None,
            "yes_bid": None,
            "yes_ask": None,
            "no_bid": None,
            "no_ask": None,
            "last": None,
            "found": False,
        }  # fmt: skip
        for t in asked
    ]


def stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ----------------------------------------------------------------------------- model probabilities


def _none(reason: str) -> Json:
    return {"p": None, "source": None, "reason": reason}


def model_prob(
    slate: Json | None,
    kind: str,
    player: str = "",
    stat: str = "pts",
    line: float = 0.0,
    side: str = "over",
    team: str = "",
) -> Json:
    """The pre-game model's probability for one leg, or why there is none.

    A player leg "N or more" is P(count >= N) = P(over N - 0.5); "fewer than N" is P(under N - 0.5).
    A win leg uses the pre-game win probability of the game the team plays in. There is no in-game
    model, spreads and totals have none, and nothing is made up when the name or team is not found.
    """
    if not slate:
        return _none("no pre-game forecasts are loaded for this date")
    if kind == "player":
        want = norm_name(player)
        for g in slate.get("games") or []:
            for pl in g.get("players") or []:
                if norm_name(str(pl["name"])) != want:
                    continue
                prop = (pl.get("props") or {}).get(stat)
                if not prop:
                    return _none(f"no {stat} forecast for {pl['name']}")
                lp = forecast_probs(prop["forecast"], line - 0.5)
                p = lp.p_over if side == "over" else lp.p_under
                return {
                    "p": float(p),
                    "source": "pre-game model forecast ("
                    + str(prop["forecast"].get("stamp"))
                    + ")",
                    "reason": None,
                }
        return _none("player not found in that day's forecasts")
    if kind == "win":
        for g in slate.get("games") or []:
            wp = g.get("win_probability") or {}
            mh = wp.get("model_home")
            if mh is None:
                continue
            if team == (g.get("home") or {}).get("abbr"):
                return {
                    "p": float(mh),
                    "source": f"pre-game win model ({wp.get('model_name')})",
                    "reason": None,
                }
            if team == (g.get("away") or {}).get("abbr"):
                return {
                    "p": 1.0 - float(mh),
                    "source": f"pre-game win model ({wp.get('model_name')})",
                    "reason": None,
                }
        return _none("team not found in that day's win forecasts")
    return _none("the model does not forecast spreads or totals")
