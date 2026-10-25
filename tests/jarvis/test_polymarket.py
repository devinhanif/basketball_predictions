from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from nba.jarvis import edge as E
from nba.jarvis.config import JarvisConfig
from nba.jarvis.models import Game
from nba.jarvis.polymarket import event_slug, fetch_events, load_polymarket_quotes, parse_events

# Recorded 2026-10-25 from the public Polymarket US gateway by the fast-arb branch (7ce3be0),
# filtered to DET@PHI (tip 23:30Z); mirrored here so this branch does not depend on that one.
FIX = Path(__file__).parent / "fixtures" / "polymarket_us_event_detphi.json"
DET, PHI = 1610612765, 1610612755
GAME = Game("G", DET, PHI, "DET", "PHI", datetime(2026, 10, 25, 23, 30))  # DET@PHI
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
    """GET-only fake; records URLs. Anything else would raise AttributeError."""

    def __init__(self, event: dict[str, Any]) -> None:
        self.event = event
        self.urls: list[str] = []

    def get(self, url: str, **kw: Any) -> Resp:
        self.urls.append(url)
        return Resp(200, self.event) if url.endswith(self.event["slug"]) else Resp(404, None)


def event() -> dict[str, Any]:
    return json.loads(FIX.read_text())  # type: ignore[no-any-return]


def test_fee_is_the_verified_us_object(cfg: JarvisConfig) -> None:
    f = cfg.fee("polymarket")
    assert f.verified is True and f.coefficient == 0.0695 and "polymarket.us" in f.source
    assert cfg.fee_rounding["polymarket"] == "bankers_cent"
    # banker's rounding per order: 0.0695 x 20 x .25 = 0.3475 -> 0.35; x 2 x .25 = 0.03475 -> 0.03
    assert E.order_cost(cfg, "polymarket", 0.5, 20) == pytest.approx(10.35)
    assert E.order_cost(cfg, "polymarket", 0.5, 2) == pytest.approx(1.03)
    # half-even, not half-up: 0.0695 x 4 x .5 x .5 = 0.0695 -> 0.07 ; exact ties go to the even cent
    assert E.fee_per_contract_raw(cfg, "polymarket", 0.5) == pytest.approx(0.0695 * 0.25, abs=1e-4)


def test_parse_winner_market_into_two_asks_with_complement_bids(cfg: JarvisConfig) -> None:
    res = parse_events(cfg, [event()], [GAME])
    assert len(res.quotes) == 2 and not res.unmatched and not res.skipped
    det = next(q for q in res.quotes if q.team_id == DET)
    phi = next(q for q in res.quotes if q.team_id == PHI)
    assert det.yes_ask == 0.51 and phi.yes_ask == 0.50
    assert det.yes_bid == pytest.approx(0.50) and phi.yes_bid == pytest.approx(0.49)
    assert det.venue == "polymarket" and not det.indicative


def test_only_the_winner_market_is_read(cfg: JarvisConfig) -> None:
    ev = event()
    ev["markets"] = [m for m in ev["markets"] if "winner" not in m["sportsMarketType"]]
    assert parse_events(cfg, [ev], [GAME]).quotes == []


def test_market_with_a_different_fee_coefficient_is_skipped_and_counted(cfg: JarvisConfig) -> None:
    ev = event()
    for m in ev["markets"]:
        m["feeCoefficient"] = 0.10
    res = parse_events(cfg, [ev], [GAME])
    assert res.quotes == [] and res.skipped == {"fee_mismatch": 1}


def test_closed_unmatched_and_unpriced_markets(cfg: JarvisConfig) -> None:
    ev = event()
    winner = next(m for m in ev["markets"] if "winner" in m["sportsMarketType"])
    winner["status"] = "MARKET_STATUS_CLOSED"
    assert parse_events(cfg, [ev], [GAME]).skipped == {"not_open": 1}
    ev = event()
    ev["teams"][0]["abbreviation"] = "zzz"  # an unknown team is listed, never guessed
    res = parse_events(cfg, [ev], [GAME])
    assert res.quotes == [] and len(res.unmatched) == 1
    ev = event()
    winner = next(m for m in ev["markets"] if "winner" in m["sportsMarketType"])
    winner["marketSides"][0]["quote"] = {"value": None}
    assert parse_events(cfg, [ev], [GAME]).skipped == {"no_price": 1}


def test_fetch_is_get_only_by_slug_on_the_us_gateway(cfg: JarvisConfig) -> None:
    fake = FakeClient(event())
    res = load_polymarket_quotes(cfg, [GAME], SLATE, datetime(2026, 10, 25, 20, 0), fake)
    assert res.error is None and len(res.quotes) == 2
    assert fake.urls and all(
        u.startswith("https://gateway.polymarket.us/v1/events/slug/nba-") for u in fake.urls
    )
    assert "gamma" not in "".join(fake.urls) and "polymarket.com" not in "".join(fake.urls)
    other = Game("C", 1610612738, 1610612737, "BOS", "ATL", datetime(2026, 10, 25, 23, 0))
    assert event_slug(other, SLATE) == "nba-bos-atl-2026-10-25"
    res2 = load_polymarket_quotes(cfg, [other], SLATE, None, FakeClient(event()))
    assert res2.quotes == [] and res2.error is None  # 404 = not listed yet


def test_disabled_in_config_makes_no_call(cfg: JarvisConfig) -> None:
    off = type(cfg)(**{**cfg.__dict__, "polymarket": {**cfg.polymarket, "enabled": False}})
    fake = FakeClient(event())
    ev, err = fetch_events(off, [GAME], SLATE, fake)
    assert ev == [] and err and fake.urls == []


def test_gamma_is_not_referenced_by_the_package() -> None:
    src = "\n".join(p.read_text() for p in Path("nba/jarvis").glob("*.py"))
    assert "gamma-api" not in src and "clob.polymarket" not in src
    assert "gamma" not in Path("configs/jarvis.yaml").read_text().replace(
        "polymarket.com (gamma) is not used", ""
    )
