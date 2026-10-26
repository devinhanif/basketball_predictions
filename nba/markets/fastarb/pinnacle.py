"""Pinnacle core lines (h2h, spreads, totals) through the existing the-odds-api client and ledger.

One call is 3 markets x 1 bookmaker group = 3 credits (``bookmakers=pinnacle``, no ``regions=``; the
vendor bills up to 10 books as one region). The call goes through ``LiveClient._request``, so the
daily ledger (``daily_max_credits``), the server-remaining floor and key scrubbing of the capture
apply unchanged: this module shares ``data/odds/budget/the_odds_api_live_<ET date>.json`` with the
live capture, so its spend counts against the same guard. The key comes from the environment (the
existing client reads it) and is never stored.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from nba.markets.fastarb.props import GameKey, PropKey, is_half_point
from nba.markets.implied import devig_two_way
from nba.odds.live_close import LiveClient, LiveConfig
from nba.odds.the_odds_api import EventRef, parse_events, parse_ts
from nba.odds.theoddsapi import NameResolver


class FastPinnacleClient(LiveClient):
    """``LiveClient`` plus one method: Pinnacle core lines for an event."""

    def core_odds(self, event_id: str, fa: Mapping[str, Any]) -> tuple[Any, int]:
        p = fa["pinnacle"]
        params = {
            "markets": ",".join(p["markets"]),
            "oddsFormat": str(p["odds_format"]),
            "dateFormat": "iso",
            "bookmakers": str(p["bookmaker"]),
        }
        expected = int(p["credits_per_call"])
        url = f"/sports/{self.config.sport_key}/events/{event_id}/odds"
        body, headers, _ = self._request(url, params, expected)
        cost = self._book("fastarb_core", headers, expected, event_id=event_id)
        return body, cost


def make_client(cfg: LiveConfig, environ: Mapping[str, str]) -> FastPinnacleClient:
    return FastPinnacleClient(cfg, environ=environ)


def match_events(
    body: Any, games: list[tuple[GameKey, datetime]], resolver: NameResolver, window_min: float
) -> tuple[dict[GameKey, str], int]:
    """game -> vendor event id by teams AND commence time; unmatched are counted, never guessed."""
    events: list[EventRef] = parse_events({"data": body})
    out: dict[GameKey, str] = {}
    unmatched = 0
    for g, tip in games:
        hits = [
            e
            for e in events
            if resolver.team_id(e.home_team) == g.home
            and resolver.team_id(e.away_team) == g.away
            and abs((e.commence_time - tip).total_seconds()) <= window_min * 60
        ]
        if len(hits) == 1:
            out[g] = hits[0].event_id
        else:
            unmatched += 1
    return out, unmatched


def pinnacle_probs(
    body: Any, game: GameKey, resolver: NameResolver, bookmaker: str = "pinnacle"
) -> tuple[dict[PropKey, float], dict[PropKey, str], dict[str, int]]:
    """Proportional no-vig P(YES) per canonical proposition, Pinnacle's own last_update per prop.

    Half-point lines only (a whole-number line has a push the two-way devig does not model)."""
    probs: dict[PropKey, float] = {}
    updated: dict[PropKey, str] = {}
    skipped: dict[str, int] = {}
    bms = body.get("bookmakers") if isinstance(body, dict) else None
    book = next((b for b in bms or [] if b.get("key") == bookmaker), None)
    if book is None:
        skipped["no_pinnacle"] = 1
        return probs, updated, skipped
    for m in book.get("markets") or []:
        key = m.get("key")
        outs = m.get("outcomes") or []
        upd = str(m.get("last_update") or book.get("last_update") or "")
        if len(outs) != 2:
            skipped[f"{key}:not_two_way"] = skipped.get(f"{key}:not_two_way", 0) + 1
            continue
        dec = {
            str(o.get("name")): (float(o["price"]), o.get("point")) for o in outs if o.get("price")
        }
        if len(dec) != 2:
            continue
        if key == "h2h":
            home = next((n for n in dec if resolver.team_id(n) == game.home), None)
            away = next((n for n in dec if resolver.team_id(n) == game.away), None)
            if home is None or away is None:
                skipped["h2h:team"] = skipped.get("h2h:team", 0) + 1
                continue
            p = devig_two_way(1.0 / dec[home][0], 1.0 / dec[away][0])
            if p is not None:
                probs[PropKey(game, "margin", 0.0)] = p
                updated[PropKey(game, "margin", 0.0)] = upd
        elif key == "spreads":
            home = next((n for n in dec if resolver.team_id(n) == game.home), None)
            away = next((n for n in dec if resolver.team_id(n) == game.away), None)
            if home is None or away is None or dec[home][1] is None:
                skipped["spreads:team"] = skipped.get("spreads:team", 0) + 1
                continue
            hp = float(dec[home][1])
            if not is_half_point(hp):
                skipped["spreads:whole_line"] = skipped.get("spreads:whole_line", 0) + 1
                continue
            p = devig_two_way(1.0 / dec[home][0], 1.0 / dec[away][0])
            if p is not None:  # home covers hp  <=>  home margin > -hp
                probs[PropKey(game, "margin", -hp)] = p
                updated[PropKey(game, "margin", -hp)] = upd
        elif key == "totals":
            over = next((n for n in dec if n.lower() == "over"), None)
            under = next((n for n in dec if n.lower() == "under"), None)
            if over is None or under is None or dec[over][1] is None:
                continue
            pt = float(dec[over][1])
            if not is_half_point(pt):
                skipped["totals:whole_line"] = skipped.get("totals:whole_line", 0) + 1
                continue
            p = devig_two_way(1.0 / dec[over][0], 1.0 / dec[under][0])
            if p is not None:
                probs[PropKey(game, "total", pt)] = p
                updated[PropKey(game, "total", pt)] = upd
    return probs, updated, skipped


def parse_update(ts: str) -> datetime | None:
    return parse_ts(ts)
