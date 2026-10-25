from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from nba.jarvis.config import JarvisConfig
from nba.jarvis.models import Game
from nba.jarvis.polymarket import event_slugs, fetch_events, load_polymarket_quotes, parse_events

FIX = Path(__file__).parent / "fixtures" / "polymarket_gamma_nba.json"
LAL, UTA, IND, BKN = 1610612747, 1610612762, 1610612754, 1610612751
GAMES = [
    Game("A", LAL, UTA, "LAL", "UTA", datetime(2026, 10, 25, 21, 0)),
    Game("B", IND, BKN, "IND", "BKN", datetime(2026, 10, 25, 22, 0)),
]
SLATE = date(2026, 10, 25)


class Resp:
    def __init__(self, code: int, body: Any) -> None:
        self.status_code, self._b = code, body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("x", request=None, response=None)  # type: ignore[arg-type]

    def json(self) -> Any:
        return self._b


class FakeClient:
    """GET-only fake; records URLs. A test fails if anything else is called."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.by_slug = {e["slug"]: e for e in events}
        self.urls: list[str] = []

    def get(self, url: str, **kw: Any) -> Resp:
        self.urls.append(url)
        slug = url.rsplit("/", 1)[-1]
        return Resp(200, self.by_slug[slug]) if slug in self.by_slug else Resp(404, None)


def events() -> list[dict[str, Any]]:
    return json.loads(FIX.read_text())  # type: ignore[no-any-return]


def test_parse_real_shape_into_two_quotes_per_game(cfg: JarvisConfig) -> None:
    res = parse_events(cfg, events(), [GAMES[0]])
    assert len(res.quotes) == 2 and not res.unmatched
    lal = next(q for q in res.quotes if q.team_id == LAL)
    uta = next(q for q in res.quotes if q.team_id == UTA)
    assert (lal.yes_bid, lal.yes_ask) == (0.68, 0.69)
    assert round(uta.yes_bid, 2) == 0.31 and round(uta.yes_ask, 2) == 0.32  # complements
    assert lal.venue == "polymarket" and not lal.indicative


def test_nickname_match_is_exact_not_substring(cfg: JarvisConfig) -> None:
    # "Nets" must not match "Charlotte Hornets"; both fixture games resolve to one team each
    res = parse_events(cfg, events(), GAMES)
    assert not res.unmatched
    assert {q.team_id for q in res.quotes} == {LAL, UTA, IND, BKN}


def test_unmatched_names_are_listed_never_guessed(cfg: JarvisConfig) -> None:
    ev = events()
    ev[0]["markets"][0]["outcomes"] = json.dumps(["Quokkas", "Jazz"])
    res = parse_events(cfg, ev, GAMES)
    assert res.unmatched == ["Quokkas vs Jazz"]
    assert all(q.game_id != "A" for q in res.quotes)


def test_market_without_bid_ask_is_dropped_not_filled_from_last(cfg: JarvisConfig) -> None:
    ev = events()
    ev[0]["markets"][0]["bestBid"] = None
    res = parse_events(cfg, ev, [GAMES[0]])
    assert res.quotes == []


def test_only_moneyline_markets_are_read(cfg: JarvisConfig) -> None:
    ev = events()
    ev[0]["markets"][0]["sportsMarketType"] = "totals"
    assert parse_events(cfg, ev, [GAMES[0]]).quotes == []


def test_fetch_is_get_only_by_slug_and_tolerates_404(cfg: JarvisConfig) -> None:
    fake = FakeClient(events())
    res = load_polymarket_quotes(cfg, GAMES, SLATE, datetime(2026, 10, 25, 20, 0), fake)
    assert res.error is None and len(res.quotes) == 4
    assert all("/events/slug/nba-" in u for u in fake.urls)
    assert event_slugs(GAMES[0], SLATE)[0] == "nba-lal-uta-2026-10-25"
    # a game the venue has not listed: 404 on both orderings, no error
    other = [Game("C", 1610612738, 1610612737, "BOS", "ATL", datetime(2026, 10, 25, 23, 0))]
    res2 = load_polymarket_quotes(cfg, other, SLATE, None, FakeClient(events()))
    assert res2.quotes == [] and res2.error is None


def test_disabled_in_config_makes_no_call(cfg: JarvisConfig) -> None:
    off = type(cfg)(**{**cfg.__dict__, "polymarket": {**cfg.polymarket, "enabled": False}})
    fake = FakeClient(events())
    ev, err = fetch_events(off, GAMES, SLATE, fake)
    assert ev == [] and err and fake.urls == []
