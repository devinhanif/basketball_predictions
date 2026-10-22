"""Screenshot scan for the parlay tracker: a Kalshi portfolio card in, an editable parlay out.

The image goes to Anthropic (Claude vision) for reading, and only when ``ANTHROPIC_API_KEY`` is set in the
environment. It is held in memory for the one request: never written to disk, never logged. The key is read
from the environment per request and never stored or echoed. Only the image and a fixed extraction prompt are
sent. The reply must be strict JSON that validates against the import schema (docs/APP_ARCHITECTURE.md) or
it is rejected. Tickers are then resolved against Kalshi's public market lists; unresolved legs stay blank.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from . import live

Json = dict[str, Any]

MODEL = "claude-sonnet-5-5"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MAX_BYTES = 5 * 1024 * 1024
TYPES = {
    "image/png": b"\x89PNG",
    "image/jpeg": b"\xff\xd8\xff",
    "image/gif": b"GIF8",
    "image/webp": b"RIFF",
}
NO_KEY = "screenshot scan needs ANTHROPIC_API_KEY; or paste the screenshot to Claude and use an import link"

KINDS = ("player", "win", "spread", "total")
STATS = ("pts", "reb", "ast", "fg3m")
SERIES = {"pts": "KXNBAPTS", "reb": "KXNBAREB", "ast": "KXNBAAST", "fg3m": "KXNBA3PT"}
TICKER_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
TEAM_RE = re.compile(r"^[A-Z0-9]{2,5}$")
ALIAS = {
    "WSH": "WAS",
    "GS": "GSW",
    "NY": "NYK",
    "SA": "SAS",
    "NO": "NOP",
    "UTAH": "UTA",
    "PHO": "PHX",
}

PROMPT = (
    "The image is a screenshot of one Kalshi parlay (combo) card. Read it and answer with ONE JSON object and "
    "nothing else, no prose, no code fence. Schema: "
    '{"name": string, "contracts": number, "paid": number (total dollars paid), '
    '"combo_price": number (dollars per contract, e.g. 22.4c is 0.224; omit if not shown), '
    '"max_payout": number (omit if not shown), "current_value": number (omit if not shown), '
    '"legs": [{"label": string (the leg text as shown), "kind": "player"|"win"|"spread"|"total", '
    '"player": string (player legs), "stat": "pts"|"reb"|"ast"|"fg3m" (player legs), '
    '"threshold": number (player: the N in "N+"; total: the line; spread: the handicap, negative if the team '
    'must win by more), "side": "over"|"under" (omit for win and spread), '
    '"team": string (win and spread legs: the 3-letter NBA abbreviation, upper case)}]}. '
    "Use only what is printed in the image. If a value is not shown, leave the key out; never guess a number."
)


def _num(v: Any, lo: float | None = None, hi: float | None = None, lo_open: bool = False) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        raise ValueError("a number was expected")
    if lo is not None and (v <= lo if lo_open else v < lo):
        raise ValueError("number out of range")
    if hi is not None and v > hi:
        raise ValueError("number out of range")
    return float(v)


def _str(v: Any, n: int, lo: int = 1) -> str:
    if not isinstance(v, str) or not lo <= len(v.strip()) <= n:
        raise ValueError("text of the wrong length")
    return v.strip()


def _only(o: Json, allowed: set[str]) -> None:
    extra = set(o) - allowed
    if extra:
        raise ValueError(f"unknown field: {sorted(extra)[0][:30]}")


def validate_leg(o: Any) -> Json:
    if not isinstance(o, dict):
        raise ValueError("a leg must be an object")
    _only(
        o, {"label", "kind", "player", "stat", "threshold", "side", "team", "ticker", "entry_price"}
    )
    kind = o.get("kind")
    if kind not in KINDS:
        raise ValueError("leg kind must be player, win, spread or total")
    out: Json = {"label": _str(o.get("label"), 120), "kind": kind}
    if kind == "player":
        out["player"] = _str(o.get("player"), 60)
        if o.get("stat") not in STATS:
            raise ValueError("player legs need a stat: pts, reb, ast or fg3m")
        out["stat"] = o["stat"]
        out["threshold"] = _num(o.get("threshold"), 0, 200, lo_open=True)
    elif kind == "total":
        out["threshold"] = _num(o.get("threshold"), 0, 1000, lo_open=True)
    elif kind == "spread":
        out["threshold"] = _num(o.get("threshold"), -100, 100)
    if kind in ("win", "spread"):
        team = o.get("team")
        if not isinstance(team, str) or not TEAM_RE.match(team):
            raise ValueError("win and spread legs need a team abbreviation like ATL")
        out["team"] = team
    elif "team" in o:
        raise ValueError("team applies to win and spread legs only")
    if kind in ("player", "total"):
        side = o.get("side", "over")
        if side not in ("over", "under"):
            raise ValueError("side must be over or under")
        out["side"] = side
    elif "side" in o:
        raise ValueError("side applies to player and total legs only")
    if o.get("ticker") is not None:
        t = o["ticker"]
        if not isinstance(t, str) or not TICKER_RE.match(t):
            raise ValueError("ticker has unusual characters")
        out["ticker"] = t
    if o.get("entry_price") is not None:
        out["entry_price"] = _num(o["entry_price"], 0, 1, lo_open=True)
    if "threshold" in o and kind == "win":
        raise ValueError("threshold does not apply to a win leg")
    return out


def validate_import(o: Any) -> Json:
    """Strict: unknown fields, wrong types and out-of-range numbers are all refused."""
    if not isinstance(o, dict):
        raise ValueError("the import must be an object")
    _only(o, {"name", "contracts", "paid", "combo_price", "max_payout", "current_value", "legs"})
    legs = o.get("legs")
    if not isinstance(legs, list) or not 1 <= len(legs) <= 12:
        raise ValueError("legs must be a list of 1 to 12")
    out: Json = {
        "name": _str(o.get("name", "Imported parlay"), 60),
        "contracts": _num(o.get("contracts"), 0, 1_000_000, lo_open=True),
        "paid": _num(o.get("paid"), 0, 1_000_000),
        "legs": [validate_leg(x) for x in legs],
    }
    if o.get("combo_price") is not None:
        out["combo_price"] = _num(o["combo_price"], 0, 1, lo_open=True)
    for k in ("max_payout", "current_value"):
        if o.get(k) is not None:
            out[k] = _num(o[k], 0, 10_000_000)
    return out


def extract_json(text: str) -> Any:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    return json.loads(t)


def sniff(content_type: str, body: bytes) -> str | None:
    """The media type if the declared type is allowed and the bytes agree with it."""
    ct = content_type.split(";")[0].strip().lower()
    magic = TYPES.get(ct)
    if magic is None or not body.startswith(magic):
        return None
    return ct


def call_claude(image: bytes, media_type: str, key: str) -> str:
    """One request to Anthropic: the image and the fixed prompt, nothing else. Returns the reply text."""
    payload = {
        "model": MODEL,
        "max_tokens": 1500,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.b64encode(image).decode(),
                        },
                    },
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
    }
    req = urllib.request.Request(  # noqa: S310 - fixed https host
        ANTHROPIC_URL,
        data=json.dumps(payload).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            body = json.loads(resp.read(2_000_000))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        raise live.SourceDown(
            type(e).__name__
        ) from None  # never carry the request into the message
    parts = body.get("content") if isinstance(body, dict) else None
    if not isinstance(parts, list):
        raise live.SourceDown("unexpected reply shape")
    return "".join(
        p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"
    )


# ----------------------------------------------------------------------------- ticker resolution


def date_tags(now: datetime | None = None) -> tuple[str, ...]:
    """Kalshi event date tags (e.g. 26OCT21) for today and yesterday, US Eastern."""
    d = (now or datetime.now(ZoneInfo("America/New_York"))).astimezone(ZoneInfo("America/New_York"))
    return tuple((d - timedelta(days=k)).strftime("%y%b%d").upper() for k in (0, 1))


def _event_tag(ticker: str) -> str:
    parts = ticker.split("-")
    return parts[1][:7] if len(parts) > 1 else ""


def resolve_ticker(leg: Json, series: dict[str, list[Json]], tags: tuple[str, ...]) -> str | None:
    """A Kalshi ticker for a player or win leg from the public market lists, or None.

    Player: the market whose title reads "<name>: <N>+ ..." with N the threshold, in a game of the given dates.
    Win: the KXNBAGAME market of that team. Spreads and totals are left for the viewer. Ambiguity gives None.
    """
    if leg.get("ticker"):
        return str(leg["ticker"])
    hits: list[str] = []
    if leg["kind"] == "player":
        want = live.norm_name(leg["player"])
        n = int(leg["threshold"]) if float(leg["threshold"]).is_integer() else None
        for m in series.get(SERIES[leg["stat"]], []):
            mt = re.match(r"^(.+?):\s*(\d+)\+", str(m.get("title") or m.get("yes_sub_title") or ""))
            if (
                mt
                and n is not None
                and int(mt.group(2)) == n
                and live.norm_name(mt.group(1)) == want
            ):
                if _event_tag(str(m["ticker"])) in tags:
                    hits.append(str(m["ticker"]))
    elif leg["kind"] == "win":
        team = leg["team"].upper()
        cands = {team, ALIAS.get(team, team)}
        for m in series.get("KXNBAGAME", []):
            tk = str(m["ticker"])
            if tk.rsplit("-", 1)[-1] in cands and _event_tag(tk) in tags:
                hits.append(tk)
    return hits[0] if len(set(hits)) == 1 else None


def resolve_all(
    spec: Json,
    fetch_series: Callable[[str], list[Json]],
    tags: tuple[str, ...],
) -> list[bool]:
    """Fill in tickers in place; returns which legs now have one."""
    needed = {
        SERIES[x["stat"]] if x["kind"] == "player" else "KXNBAGAME"
        for x in spec["legs"]
        if x["kind"] in ("player", "win") and not x.get("ticker")
    }
    lists = {s: fetch_series(s) for s in needed}
    flags: list[bool] = []
    for x in spec["legs"]:
        t = resolve_ticker(x, lists, tags)
        if t:
            x["ticker"] = t
        flags.append(bool(x.get("ticker")))
    return flags


def fetch_series(series: str) -> list[Json]:
    """Public open markets of one series (cached 20 s). A failure gives an empty list: legs stay editable."""
    out: list[Json] = []
    cursor = ""
    for _ in range(3):
        url = f"{live.KALSHI_MARKETS}?series_ticker={series}&status=open&limit=1000"
        if cursor:
            url += f"&cursor={cursor}"
        try:
            raw, _, _ = live.cached(f"Kalshi:series:{series}:{cursor}", url)
        except live.SourceDown:
            return out
        out.extend(raw.get("markets") or [])
        cursor = str(raw.get("cursor") or "")
        if not cursor or not TICKER_RE.match(cursor):
            break
    return out


def scan(
    image: bytes, media_type: str, key: str, ask: Callable[[bytes, str, str], str] | None = None
) -> Json:
    reply = (ask or call_claude)(image, media_type, key)
    try:
        spec = validate_import(extract_json(reply))
    except (ValueError, TypeError) as e:
        raise ValueError(f"the reading did not match the expected format ({e})") from None
    screen = {k: spec.pop(k) for k in ("max_payout", "current_value") if k in spec}
    flags = resolve_all(spec, fetch_series, date_tags())
    return {
        "parlay": spec,
        "screen": screen,
        "resolved": flags,
        "source": f"Claude vision ({MODEL}) read the image; tickers from Kalshi public markets",
    }


def api_key() -> str | None:
    k = os.environ.get("ANTHROPIC_API_KEY")
    return k if k else None
