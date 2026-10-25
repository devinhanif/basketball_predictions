"""Polymarket public gamma API: GET only, no key, no auth scope (docs.polymarket.com, 2026-10-25).

Endpoint used (documented): ``GET /events/slug/<slug>``, one per slate game. Nothing else:
no trading endpoint of any kind is referenced anywhere in nba/jarvis (a test greps for them).

The response shape is parsed defensively: ``outcomes`` and ``clobTokenIds`` arrive as JSON-encoded
strings or lists; ``bestBid``/``bestAsk`` are for the FIRST outcome, and the second outcome's prices
are their complements. A market without a usable bid/ask is dropped, never filled from
``outcomePrices`` (a last price is not a tradable one). Outcomes are matched to our teams by
nickname; an unmatched name is returned in ``unmatched`` and never guessed.

The shape was checked against one live GET per slate game on 2026-10-25 (public, no key):
``outcomes`` is a JSON string of team nicknames, ``bestBid``/``bestAsk`` are for the first outcome,
``gameStartTime`` is ``YYYY-MM-DD HH:MM:SS+00``, ``sportsMarketType`` is ``moneyline`` for the
winner market. ``tests/jarvis/fixtures/polymarket_gamma_nba.json`` is a trimmed copy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import httpx

from nba.jarvis.config import JarvisConfig
from nba.jarvis.models import Game, Quote
from nba.jarvis.sources import team_tables, utcnow


class _Getter(Protocol):
    def get(self, url: str, **kw: Any) -> Any: ...


@dataclass
class PolyResult:
    quotes: list[Quote] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    n_events: int = 0
    error: str | None = None


def _jlist(v: Any) -> list[Any]:
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            out = json.loads(v)
        except json.JSONDecodeError:
            return []
        return out if isinstance(out, list) else []
    return []


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if 0.0 <= x <= 1.0 else None


def _start(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(UTC).replace(tzinfo=None)


def event_slugs(game: Game, slate: date) -> list[str]:
    """Candidate event slugs for a game: ``nba-<away>-<home>-<YYYY-MM-DD>`` (seen on the live API,
    e.g. ``nba-bkn-cle-2026-10-24``), both team orders, in case the venue lists it the other way."""
    a, h = game.away_abbr.lower(), game.home_abbr.lower()
    return [f"nba-{a}-{h}-{slate.isoformat()}", f"nba-{h}-{a}-{slate.isoformat()}"]


def fetch_events(
    cfg: JarvisConfig, games: list[Game], slate: date, client: _Getter | None = None
) -> tuple[list[dict[str, Any]], str | None]:
    """``GET /events/slug/<slug>`` for each slate game (documented; no key, no auth). The tag
    listing is dominated by futures, so games are fetched by slug; a 404 means "not listed yet"."""
    pm = cfg.polymarket
    if not pm.get("enabled", True):
        return [], "polymarket disabled in config"
    own = client is None
    http: Any = client or httpx.Client(timeout=float(pm["timeout_s"]))
    base = str(pm["gamma_base"]).rstrip("/")
    events: list[dict[str, Any]] = []
    err: str | None = None
    try:
        for g in games:
            for slug in event_slugs(g, slate):
                try:
                    r = http.get(f"{base}/events/slug/{slug}")
                    if r.status_code == 404:
                        continue
                    r.raise_for_status()
                    body = r.json()
                except (httpx.HTTPError, ValueError) as exc:
                    err = type(exc).__name__
                    continue
                if isinstance(body, dict) and body.get("markets"):
                    events.append(body)
                    break
    finally:
        if own:
            http.close()
    if not events and err:
        return [], err
    return events, None


def parse_events(
    cfg: JarvisConfig, events: list[dict[str, Any]], games: list[Game], now: datetime | None = None
) -> PolyResult:
    """Moneyline markets matched to slate games (start within 4 h of our real tip)."""
    _, _, names = team_tables()
    nick = {tid: n.lower() for tid, n in names.items()}
    res = PolyResult(n_events=len(events))
    stamp = now or utcnow()

    def team_of(outcome: str) -> int | None:
        o = outcome.strip().lower()
        hits = [t for t, full in nick.items() if o and (full == o or full.endswith(" " + o))]
        return hits[0] if len(hits) == 1 else None

    for ev in events:
        for m in ev.get("markets") or []:
            if str(m.get("sportsMarketType", "")).lower() != "moneyline":
                continue
            outs = [str(x) for x in _jlist(m.get("outcomes"))]
            if len(outs) != 2:
                continue
            t0, t1 = team_of(outs[0]), team_of(outs[1])
            if t0 is None or t1 is None:
                res.unmatched.append(" vs ".join(outs))
                continue
            start = _start(m.get("gameStartTime") or ev.get("startTime") or ev.get("gameStartTime"))
            game = next(
                (
                    g
                    for g in games
                    if {g.away_team, g.home_team} == {t0, t1}
                    and (
                        start is None
                        or g.tipoff is None
                        or abs(start - g.tipoff) <= timedelta(hours=4)
                    )
                ),
                None,
            )
            if game is None:
                continue
            bid, ask = _f(m.get("bestBid")), _f(m.get("bestAsk"))
            if bid is None or ask is None or not 0.0 < ask < 1.0 or bid > ask:
                continue
            slug = str(m.get("slug") or m.get("conditionId") or ev.get("slug") or "market")
            for i, (tid, ob, oa) in enumerate(((t0, bid, ask), (t1, 1.0 - ask, 1.0 - bid))):
                ab = game.home_abbr if tid == game.home_team else game.away_abbr
                res.quotes.append(
                    Quote(
                        "polymarket",
                        f"POLY:{slug}:{i}",
                        game.game_id,
                        "game_win",
                        stamp,
                        ob if ob > 0 else None,
                        oa if 0 < oa < 1 else None,
                        team_id=tid,
                        series="polymarket_moneyline",
                        label=f"{ab} to win",
                    )
                )
    return res


def load_polymarket_quotes(
    cfg: JarvisConfig,
    games: list[Game],
    slate: date,
    now: datetime | None = None,
    client: _Getter | None = None,
) -> PolyResult:
    events, err = fetch_events(cfg, games, slate, client)
    if err:
        return PolyResult(error=err)
    return parse_events(cfg, events, games, now)
