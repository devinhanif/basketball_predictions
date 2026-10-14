"""The ``us_ex`` region (Kalshi, Polymarket, Novig, ProphetX, BetOpenly) in the live capture.

The payload is a recording (``tests/fixtures/odds/the_odds_api/us_ex_live_2026-10-14.json.gz``,
2026-10-14, four regular-season events, ``regions=us_ex``): it must go through the EXISTING parser
with no new market or outcome shape, and the daily credit guard must still bound a night at three
regions. No network, no key, no ``nba.duckdb``.
"""

from __future__ import annotations

import copy
import gzip
import json
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from nba.odds import live_close as lc
from nba.odds.the_odds_api import CreditBudgetExceeded, CreditLedger, parse_event_odds
from nba.odds.theoddsapi import NameResolver
from tests.odds import test_live_close as base

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "odds"
    / "the_odds_api"
    / "us_ex_live_2026-10-14.json.gz"
)
USEX_BOOKS = {"kalshi", "novig", "polymarket", "polymarket_us"}  # prophetx/betopenly: none posted
OUTCOME_KEYS = {"name", "price", "point", "description"}  # the shapes the parser already handles
cfg = base.cfg  # the tmp-dir LiveConfig fixture from test_live_close


def recording() -> dict[str, Any]:
    with gzip.open(FIXTURE, "rt") as fh:
        out = json.load(fh)
    assert isinstance(out, dict)
    return out


def events() -> dict[str, dict[str, Any]]:
    ev = recording()["events"]
    assert isinstance(ev, dict)
    return ev


def parse(body: dict[str, Any], res: NameResolver | None = None) -> Any:
    return parse_event_odds(
        {"timestamp": "2026-10-14T17:40:00Z", "data": body},
        season=0,
        game_id="G",
        snapshot_kind="t60",
        requested_at=datetime(2026, 10, 14, 17, 41, tzinfo=UTC),
        raw_file="x",
        resolver=res or NameResolver(teams={}, players={}),
    )


# ---- the recording is key-free and in the shape the parser already knows --------------------


def test_recording_has_no_credential_and_four_events() -> None:
    text = FIXTURE.read_bytes()
    assert b"apiKey" not in gzip.decompress(text) and len(events()) == 4


def test_us_ex_payload_has_no_new_shape() -> None:
    shapes: Counter[frozenset[str]] = Counter()
    for body in events().values():
        for b in body["bookmakers"]:
            assert set(b) == {"key", "title", "markets"}  # no new book-level field
            for m in b["markets"]:
                assert m["key"] in {"h2h", "spreads", "totals", "player_points"}
                for o in m["outcomes"]:
                    assert set(o) <= OUTCOME_KEYS
                    shapes[frozenset(o)] += 1
    assert set(shapes) == {
        frozenset({"name", "price"}),
        frozenset({"name", "price", "point"}),
        frozenset({"name", "price", "point", "description"}),
    }


def test_us_ex_payload_parses_with_no_unknown_reasons() -> None:
    seen: Counter[str] = Counter()
    for body in events().values():
        pr = parse(body)
        reasons = {k: v for k, v in pr.reasons.items() if k != "unresolved_player"}
        # nothing unparsed, duplicated or bad (the empty resolver misses names, which is fine)
        assert reasons == {}
        assert pr.rows.height > 0
        seen.update(pr.books)
    assert set(seen) == USEX_BOOKS


def test_kalshi_is_a_two_way_moneyline_only() -> None:
    pr = parse(next(b for k, b in events().items() if k.startswith("26b036ff")))
    k = pr.rows.filter(pr.rows["book"] == "kalshi")
    assert sorted(k["side"].to_list()) == ["away", "home"] and set(k["market"]) == {"h2h"}
    assert k["implied_prob"].sum() == pytest.approx(1.0)  # de-vigged pair


def test_player_points_resolve_by_reviewed_alias_not_by_guess() -> None:
    body = next(b for k, b in events().items() if k.startswith("2b042dda"))
    names = {
        o["description"]
        for bk in body["bookmakers"]
        for m in bk["markets"]
        if m["key"] == "player_points"
        for o in m["outcomes"]
    }
    assert names == {"Jalen Brunson"}
    miss = parse(body)  # empty resolver: nothing is guessed
    assert miss.unresolved == Counter({"Jalen Brunson": 2})
    hit = parse(body, NameResolver(teams={}, players={"jalen brunson": 1628973}))
    assert set(hit.rows.filter(hit.rows["market"] == "player_points")["player_id"]) == {1628973}


def test_recording_shows_polymarket_under_is_a_placeholder_price() -> None:
    """Every Polymarket player_points Under in the recording is <= -3000 (a no-bid placeholder,
    not a tradable price): the de-vigged ``implied_prob`` of that pair is NOT a probability to use;
    the Over's own price is. If this stops holding, re-read the quotes before trusting them."""
    unders: list[int] = []
    for body in events().values():
        pr = parse(body)
        is_pm = (pr.rows["book"] == "polymarket") & (pr.rows["market"] == "player_points")
        pm = pr.rows.filter(is_pm)
        unders += pm.filter(pm["side"] == "under")["price_american"].to_list()
    assert unders and all(u <= -3000 for u in unders)


# ---- end to end through the live capture ----------------------------------------------------


def usex_cfg(c: lc.LiveConfig) -> lc.LiveConfig:
    return replace(c, regions=("us", "eu", "us_ex"))


def test_live_capture_stores_us_ex_books_beside_the_rest(cfg: lc.LiveConfig) -> None:
    body = copy.deepcopy(next(b for k, b in events().items() if k.startswith("2b042dda")))
    body["commence_time"] = "2026-10-20T23:30:00Z"
    resp = base.FakeResp(200, body, {"x-requests-last": "9", "x-requests-remaining": "2296000"})
    http = base.FakeHttp(odds=resp)
    c = usex_cfg(cfg)
    rep = base.run(c, http, base.at(6))
    assert rep.pulled == 1 and not rep.degraded and rep.credits == 9
    (_, params) = next(call for call in http.calls if call[0].endswith("/odds"))
    assert params["regions"] == "us,eu,us_ex"
    q = "SELECT DISTINCT book FROM live_book_odds WHERE snapshot_kind = 'close'"
    got = {r[0] for r in base.rows(c, q)}
    assert got == USEX_BOOKS
    # no parse_* reason from any new book (the shared test resolver does not know Brunson)
    assert set(rep.statuses) - {"parse_unresolved_player"} == {"ok"}
    assert base.rows(c, "SELECT status, credits FROM live_capture_log") == [("ok", 9)]


def test_expected_cost_scales_with_regions_and_the_guard_refuses_first(cfg: lc.LiveConfig) -> None:
    c = usex_cfg(cfg)
    assert c.expected_cost() == 7 * 3
    http = base.FakeHttp()
    rep = base.run(replace(c, daily_max_credits=c.expected_cost() - 1), http, base.at(6))
    assert rep.degraded and http.n_odds == 0  # refused BEFORE the request is sent
    ok = base.run(replace(c, daily_max_credits=c.expected_cost()), base.FakeHttp(), base.at(6))
    assert ok.pulled == 1 and not ok.degraded


def test_production_daily_guard_bounds_a_night_at_three_regions(tmp_path: Path) -> None:
    c = lc.load_live_config()
    assert c.regions == ("us", "eu", "us_ex")
    call = c.expected_cost()
    assert call == 267
    ledger = CreditLedger(tmp_path / "day.json", c.daily_max_credits, c.min_server_remaining)
    allowed = 0
    for _ in range(1000):  # far more calls than any night could make
        try:
            ledger.check(call)
        except CreditBudgetExceeded:
            break
        ledger.record({"cost": call})
        allowed += 1
    assert allowed == c.daily_max_credits // call == 269  # bounded, never past the cap
    assert ledger.spent() <= c.daily_max_credits
    # a 15-game night over the full 12-point schedule (180 calls) and a 16th game all fit
    assert 16 * len(c.schedule) <= allowed
    # the monthly estimate still fits the plan rule, so every market group stays on
    assert lc.estimate_credits(c, c.plan.games_per_month) <= 0.5 * c.plan.monthly_credits
    assert c.dropped_groups() == () and len(c.active_schedule()) == len(c.schedule)
