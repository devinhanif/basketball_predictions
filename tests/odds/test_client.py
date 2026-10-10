"""Client behaviour: key handling, budget, raw-first cache, bounded retry. No network."""

from __future__ import annotations

import gzip
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from nba.odds.theoddsapi import (
    KEY_ENV,
    BudgetLedger,
    OddsBudgetExceeded,
    OddsConfig,
    OddsHTTPError,
    OddsKeyMissingError,
    OddsUnreachable,
    TheOddsApiClient,
    load_config,
    params_hash,
)
from tests.odds.conftest import TEST_KEY, FakeHttp, FakeResponse, probe_body

OK = {"success": True, "source": "cache", "data": []}


def test_missing_key_raises_clear_error(
    cfg: OddsConfig, monkeypatch: pytest.MonkeyPatch, http: FakeHttp
) -> None:
    monkeypatch.delenv(KEY_ENV, raising=False)
    with pytest.raises(OddsKeyMissingError, match="ODDS_API_KEY"):
        TheOddsApiClient(cfg)
    monkeypatch.setenv(KEY_ENV, "   ")
    with pytest.raises(OddsKeyMissingError):
        TheOddsApiClient(cfg)
    assert http.calls == []


def test_key_is_header_only_and_never_in_repr_logs_exceptions_or_raw_files(
    client: TheOddsApiClient, http: FakeHttp, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    http.script = [FakeResponse(200, OK)]
    r = client.odds(commence_from="2026-10-20T04:00:00Z", commence_to="2026-10-21T04:00:00Z")
    call = http.calls[0]
    assert call["headers"] == {"x-api-key": TEST_KEY}
    assert TEST_KEY not in json.dumps(call["params"]) and TEST_KEY not in call["url"]
    assert call["params"]["markets"] == "h2h,spreads,totals"
    assert call["params"]["oddsFormat"] == "american"
    assert call["url"] == "https://api.theoddsapi.com/odds/"
    assert TEST_KEY not in repr(client) and TEST_KEY not in str(client)
    assert TEST_KEY not in repr(client._key) and TEST_KEY not in str(client._key)
    assert r.path is not None
    with gzip.open(r.path, "rt") as fh:
        assert TEST_KEY not in fh.read()
    # error paths: the key is scrubbed even if the server echoes it
    http.script = [FakeResponse(403, f"forbidden for {TEST_KEY}")]
    with pytest.raises(OddsHTTPError) as ei:
        client.props()
    assert TEST_KEY not in str(ei.value) and ei.value.status == 403
    assert TEST_KEY not in caplog.text


def test_raw_written_before_parse_with_documented_name(
    client: TheOddsApiClient, http: FakeHttp, cfg: OddsConfig
) -> None:
    http.script = [FakeResponse(200, OK)]
    r = client.props()
    assert r.path is not None and not r.from_cache
    q = {
        "sport_key": "basketball_nba",
        "markets": "player_points,player_rebounds,player_assists,player_threes",
        "regions": "us",
    }
    assert r.path.parent == cfg.raw_dir / "2026-10-20"
    assert r.path.name == f"props_{params_hash(q)}_20261020T200005Z.json.gz"
    assert client.load_raw(r.path).body == OK
    assert client.ledger.server_remaining(client.now()) == 6000


def test_same_request_inside_reuse_window_costs_nothing(
    cfg: OddsConfig, monkeypatch: pytest.MonkeyPatch, http: FakeHttp
) -> None:
    monkeypatch.setenv(KEY_ENV, TEST_KEY)
    t = [datetime(2026, 10, 20, 20, 0, 5, tzinfo=UTC)]
    c = TheOddsApiClient(cfg, clock=lambda: t[0], sleep=lambda _s: None)
    http.script = [FakeResponse(200, OK), FakeResponse(200, OK)]
    a = c.props()
    t[0] += timedelta(seconds=30)
    b = c.props()
    assert b.from_cache and b.fetched_at == a.fetched_at and len(http.calls) == 1
    t[0] += timedelta(seconds=120)  # outside the 60 s window: a real second request
    c.props()
    assert len(http.calls) == 2 and c.ledger.used(t[0]) == 2


def test_daily_budget_refuses_beyond_ceiling(
    cfg: OddsConfig, clock: object, monkeypatch: pytest.MonkeyPatch, http: FakeHttp
) -> None:
    monkeypatch.setenv(KEY_ENV, TEST_KEY)
    c = TheOddsApiClient(
        replace(cfg, daily_budget=2, reuse_window_s=0.0),
        clock=clock,  # type: ignore[arg-type]
        sleep=lambda _s: None,
    )
    http.script = [FakeResponse(200, OK), FakeResponse(200, OK)]
    c.props()
    c.props()
    with pytest.raises(OddsBudgetExceeded, match="2/2"):
        c.props()
    assert len(http.calls) == 2  # the refused call never reached the network


def test_budget_counts_every_attempt_and_resets_next_utc_day(tmp_path: Path) -> None:
    led = BudgetLedger(tmp_path, ceiling=3)
    d1 = datetime(2026, 10, 20, 23, 59, tzinfo=UTC)
    for _ in range(3):
        led.charge(d1)
    with pytest.raises(OddsBudgetExceeded):
        led.charge(d1)
    led.charge(d1 + timedelta(minutes=2))  # midnight UTC rolled over
    assert led.used(d1) == 3 and led.used(d1 + timedelta(minutes=2)) == 1


def test_respects_server_reported_remaining(tmp_path: Path) -> None:
    led = BudgetLedger(tmp_path, ceiling=2000, reserve_remaining=50)
    now = datetime(2026, 10, 20, 20, tzinfo=UTC)
    led.note_remaining(now, 49)
    with pytest.raises(OddsBudgetExceeded, match="remaining"):
        led.charge(now)
    led.note_remaining(now, 50)
    assert led.charge(now) == 1


def test_retries_5xx_then_succeeds_and_counts_attempts(
    client: TheOddsApiClient, http: FakeHttp
) -> None:
    http.script = [FakeResponse(503, "busy"), httpx.ConnectError("boom"), FakeResponse(200, OK)]
    r = client.props()
    assert r.status == 200 and len(http.calls) == 3 and client.ledger.used(client.now()) == 3


def test_bounded_retry_then_unreachable(client: TheOddsApiClient, http: FakeHttp) -> None:
    http.script = [httpx.ConnectError("a"), httpx.ReadTimeout("b"), httpx.ConnectError("c")]
    with pytest.raises(OddsUnreachable):
        client.props()
    assert len(http.calls) == client.config.max_attempts == 3
    assert not list(client.config.raw_dir.glob("**/*.json.gz"))  # failures are not cached as data


def test_429_stops_without_retry(client: TheOddsApiClient, http: FakeHttp) -> None:
    http.script = [FakeResponse(429, "quota")]
    with pytest.raises(OddsBudgetExceeded):
        client.props()
    assert len(http.calls) == 1


def test_non_json_200_is_http_error(client: TheOddsApiClient, http: FakeHttp) -> None:
    http.script = [FakeResponse(200, "<html>")]
    with pytest.raises(OddsHTTPError):
        client.props()


def test_me_is_never_served_from_cache(client: TheOddsApiClient, http: FakeHttp) -> None:
    me = probe_body("me")
    http.script = [FakeResponse(200, me), FakeResponse(200, me)]
    client.me()
    client.me()
    assert len(http.calls) == 2 and http.calls[0]["url"].endswith("/me/")


def test_only_known_endpoints_callable(client: TheOddsApiClient) -> None:
    with pytest.raises(ValueError):
        client.get("orders")


def test_config_file_loads_and_defaults_ceiling_is_our_own() -> None:
    cfg = load_config()
    assert cfg.daily_budget == 2000 < 6667
    assert cfg.base_url == "https://api.theoddsapi.com"
    assert cfg.prop_markets == (
        "player_points",
        "player_rebounds",
        "player_assists",
        "player_threes",
    )
    assert cfg.game_markets == ("h2h", "spreads", "totals")
