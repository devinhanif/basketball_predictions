"""Polymarket US public gateway: GET only, no key, no auth scope (docs.polymarket.us, 2026-10-25).

polymarket.com (gamma / CLOB) is not tradable by US persons and is NOT used here. The US venue
has its own public gateway: ``GET {gateway}/v1/events/slug/nba-<away>-<home>-<YYYY-MM-DD>``, one
per slate game. This is a minimal mirror of the venue client in
``nba/markets/fastarb/venues.py`` (branch agent/market-fast-arb, 7ce3be0, not merged when this was
written); once it merges this module should import ``us_quotes`` from there. No trading endpoint
of any kind is referenced anywhere in nba/jarvis (a test greps for them).

Only the full-game winner market is read (``basketball_team_full_game_winner``). Each market side
is a tradable instrument with its own ask in ``quote``; the other side's bid is ``1 - ask``. A
market whose own ``feeCoefficient`` differs from the verified fee in configs/jarvis.yaml is
skipped and counted (the check the fast-arb poller also makes). An unmatched team is listed, never
guessed. Book depth is not fetched (``/v1/markets/<slug>/book`` is v2): sizes are unknown, so a
stake larger than the displayed level may not fill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import httpx

from nba.jarvis.config import JarvisConfig
from nba.jarvis.models import Game, Quote
from nba.jarvis.sources import team_tables, utcnow

US_WINNER = "basketball_team_full_game_winner"
VENUE = "polymarket"


class _Getter(Protocol):
    def get(self, url: str, **kw: Any) -> Any: ...


@dataclass
class PolyResult:
    quotes: list[Quote] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    n_events: int = 0
    error: str | None = None


def _f(v: Any) -> float | None:
    if isinstance(v, dict):
        v = v.get("value")
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if 0.0 <= x <= 1.0 else None


def _start(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.replace(tzinfo=None) if d.tzinfo is None else d.astimezone(UTC).replace(tzinfo=None)


def event_slug(game: Game, slate: date) -> str:
    """``nba-<away>-<home>-<date>`` (seen on the live gateway, e.g. nba-det-phi-2026-10-25)."""
    return f"nba-{game.away_abbr.lower()}-{game.home_abbr.lower()}-{slate.isoformat()}"


def fetch_events(
    cfg: JarvisConfig, games: list[Game], slate: date, client: _Getter | None = None
) -> tuple[list[dict[str, Any]], str | None]:
    """One GET per slate game; a 404 means "not listed yet". Returns (events, error)."""
    pm = cfg.polymarket
    if not pm.get("enabled", True):
        return [], "polymarket disabled in config"
    own = client is None
    http: Any = client or httpx.Client(timeout=float(pm["timeout_s"]))
    base = str(pm["gateway_base"]).rstrip("/")
    events: list[dict[str, Any]] = []
    err: str | None = None
    try:
        for g in games:
            try:
                r = http.get(f"{base}/v1/events/slug/{event_slug(g, slate)}")
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                body = r.json()
            except (httpx.HTTPError, ValueError) as exc:
                err = type(exc).__name__
                continue
            ev = body.get("event", body) if isinstance(body, dict) else None
            if isinstance(ev, dict) and ev.get("markets"):
                events.append(ev)
    finally:
        if own:
            http.close()
    if not events and err:
        return [], err
    return events, None


def parse_events(
    cfg: JarvisConfig, events: list[dict[str, Any]], games: list[Game], now: datetime | None = None
) -> PolyResult:
    abbr, _, _ = team_tables()
    res = PolyResult(n_events=len(events))
    stamp = now or utcnow()
    want = cfg.fee(VENUE).coefficient

    def skip(why: str) -> None:
        res.skipped[why] = res.skipped.get(why, 0) + 1

    for ev in events:
        id_to_team: dict[int, int] = {}
        for t in ev.get("teams") or []:
            tid = abbr.get(str(t.get("abbreviation", "")).upper())
            if tid is not None and t.get("id") is not None:
                id_to_team[int(t["id"])] = tid
        for m in ev.get("markets") or []:
            if m.get("sportsMarketType") != US_WINNER:
                continue
            if m.get("status") != "MARKET_STATUS_OPEN" or m.get("closed"):
                skip("not_open")
                continue
            fc = _f(m.get("feeCoefficient"))
            if fc is None or abs(fc - want) > 1e-9:
                skip("fee_mismatch")  # the venue's own coefficient is not the verified one
                continue
            sides = [s for s in m.get("marketSides") or [] if s.get("tradable", True)]
            teams = [
                id_to_team.get(int(s["teamId"])) if s.get("teamId") is not None else None
                for s in sides
            ]
            if len(sides) != 2 or None in teams:
                res.unmatched.append(str(m.get("question", m.get("slug"))))
                continue
            game = next((g for g in games if {g.away_team, g.home_team} == set(teams)), None)
            start = _start(ev.get("startTime"))
            if game is None or (
                start is not None
                and game.tipoff is not None
                and abs(start - game.tipoff) > timedelta(hours=4)
            ):
                continue
            asks = [_f(s.get("quote")) for s in sides]
            if any(a is None or not 0.0 < a < 1.0 for a in asks):
                skip("no_price")
                continue
            slug = str(m.get("slug", "market"))
            for i in range(2):
                tid = teams[i]
                other = asks[1 - i]
                bid = None if other is None else 1.0 - other  # the other side's ask is 1 - this bid
                ab = game.home_abbr if tid == game.home_team else game.away_abbr
                res.quotes.append(
                    Quote(VENUE, f"POLYUS:{slug}:{i}", game.game_id, "game_win", stamp,
                          bid if bid and bid > 0 else None, asks[i], team_id=tid,
                          series="polymarket_us_winner", label=f"{ab} to win")
                )  # fmt: skip
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
