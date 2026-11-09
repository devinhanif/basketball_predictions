# ruff: noqa: E501
"""Review fixes on the tick: no write lock across a vendor call, the close window, an unreadable slate,
the monthly plan rule, the server floor, and the slate retry. Fake vendor, tmp stores."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.odds import live_close as lc
from nba.pulls.config import PullsConfig
from nba.pulls.orchestrator import tick
from nba.pulls.store import PullStore
from tests.odds.test_live_close import ENV_ON, RES, FakeHttp
from tests.pulls.conftest import GAME, at, file_request, market_rows, run_tick

NOW = at(60)


def _is_open(con: duckdb.DuckDBPyConnection) -> bool:
    try:
        con.execute("SELECT 1")
    except duckdb.Error:
        return False
    return True


def test_the_market_store_is_closed_during_every_vendor_call(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[duckdb.DuckDBPyConnection] = []
    real = lc.open_store

    def spy(*a: Any, **k: Any) -> duckdb.DuckDBPyConnection:
        con = real(*a, **k)
        opened.append(con)
        return con

    monkeypatch.setattr(lc, "open_store", spy)

    class Probe(FakeHttp):
        def __init__(self) -> None:
            super().__init__()
            self.open_during: list[int] = []

        def __call__(self, url: str, **kw: Any) -> Any:
            self.open_during.append(sum(1 for c in opened if _is_open(c)))
            return super().__call__(url, **kw)

    file_request(store, NOW)
    http = Probe()
    rep = run_tick(NOW, pcfg, lcfg, store, http)
    assert rep.statuses["done"] == 1
    assert len(http.open_during) == 2 and http.open_during == [0, 0]  # events list, event odds
    assert len(opened) >= 2  # reopened after each call for the write
    assert market_rows(lcfg, "SELECT DISTINCT snapshot_kind FROM live_book_odds") == [
        ("ondemand1",)
    ]
    assert not any(_is_open(c) for c in opened)


def test_a_game_in_its_close_window_leaves_the_request_open_with_no_call(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore
) -> None:
    soon = lc.LiveGame(
        "G2", NOW.replace(tzinfo=None) + timedelta(minutes=9), GAME.home_team, GAME.away_team
    )
    file_request(store, NOW, "G1", need_by_min=60)
    http = FakeHttp()
    rep = run_tick(NOW, pcfg, lcfg, store, http, games=[GAME, soon])
    assert http.calls == [] and not rep.statuses
    assert store.list_requests()[0].status == "open"  # not failed, not expired
    assert any("within 10 min of tip" in m for m in rep.messages)
    # twelve minutes later G2 has tipped, and the request is served
    later = NOW + timedelta(minutes=12)
    rep2 = run_tick(later, pcfg, lcfg, store, FakeHttp(), games=[GAME, soon])
    assert rep2.statuses["done"] == 1


def test_the_close_window_edges(pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore) -> None:
    file_request(store, at(11), need_by_min=60)
    http = FakeHttp()
    assert run_tick(at(11), pcfg, lcfg, store, http).statuses["done"] == 1  # 11 min out: served
    file_request(store, at(10), need_by_min=60)
    http2 = FakeHttp()
    assert run_tick(at(10), pcfg, lcfg, store, http2).statuses["done"] == 0  # exactly 10: waits
    assert http2.calls == []


def test_an_unreadable_slate_leaves_requests_open_not_failed(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore
) -> None:
    file_request(store, NOW)

    def locked(_d: Any) -> list[lc.LiveGame]:
        raise duckdb.IOException("Could not set lock on lineups.duckdb")

    rep = tick(
        NOW, cfg=pcfg, live_cfg=lcfg, store=store, environ=ENV_ON, games_fn=locked, resolver=RES,
        sleep=lambda _s: None, http_get=FakeHttp(), clock=lambda: NOW,
    )  # fmt: skip
    assert store.list_requests()[0].status == "open" and not rep.degraded
    assert any("requests stay open" in m for m in rep.messages)


def test_the_slate_read_is_retried_while_lineups_is_written(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    def flaky(*_a: Any, **_k: Any) -> list[lc.LiveGame]:
        calls["n"] += 1
        if calls["n"] < 3:
            raise duckdb.IOException("Could not set lock")
        return [GAME]

    monkeypatch.setattr(lc, "read_slate", flaky)
    naps: list[float] = []
    file_request(store, NOW)
    rep = tick(
        NOW, cfg=replace(pcfg, store_retries=3, store_wait_s=0.5), live_cfg=lcfg, store=store,
        environ=ENV_ON, resolver=RES, sleep=naps.append, http_get=FakeHttp(), clock=lambda: NOW,
    )  # fmt: skip
    assert calls["n"] == 3 and naps == [0.5, 0.5] and rep.statuses["done"] == 1


def test_the_monthly_plan_rule_refuses_on_demand(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore
) -> None:
    file_request(store, NOW)
    tight = replace(lcfg, plan=replace(lcfg.plan, monthly_credits=1000))
    http = FakeHttp()
    rep = run_tick(NOW, pcfg, tight, store, http)
    assert rep.statuses["refused_budget"] == 1 and http.calls == []
    assert "monthly plan rule" in store.list_requests()[0].detail


def test_the_shipped_plan_has_room_for_the_schedule_plus_on_demand() -> None:
    from nba.pulls.config import load_pulls_config
    from nba.pulls.orchestrator import monthly_plan_ok

    ok, why = monthly_plan_ok(lc.load_live_config(), load_pulls_config())
    assert ok, why


def _ledger(lcfg: lc.LiveConfig, remaining: int) -> None:
    lcfg.ledger_dir.mkdir(parents=True, exist_ok=True)
    path = lcfg.ledger_dir / f"the_odds_api_live_{NOW.astimezone(lc.ET).date().isoformat()}.json"
    path.write_text(json.dumps({"spent": 0, "n_calls": 0, "server_remaining": remaining}))


def test_the_server_floor_is_wired_into_the_tick(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore
) -> None:
    file_request(store, NOW)
    # floor 100,000; call 14 + reserve 14: 100,028 is allowed, 100,027 is not
    _ledger(lcfg, 100_027)
    http = FakeHttp()
    rep = run_tick(NOW, pcfg, lcfg, store, http)
    assert rep.statuses["refused_budget"] == 1 and http.calls == []
    assert "server remaining" in store.list_requests()[0].detail
    file_request(store, NOW)
    _ledger(lcfg, 100_028)
    assert run_tick(NOW, pcfg, lcfg, store, FakeHttp()).statuses["done"] == 1


def test_an_injected_client_is_used_as_is(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore, tmp_path: Path
) -> None:
    file_request(store, NOW)
    http = FakeHttp()
    cli = lc.LiveClient(lcfg, environ=ENV_ON, sleep=lambda _s: None, http_get=http, day=NOW.date())
    rep = tick(
        NOW, cfg=pcfg, live_cfg=lcfg, store=store, environ=ENV_ON,
        games_fn=lambda _d: [GAME], client=cli, resolver=RES, clock=lambda: NOW,
    )  # fmt: skip
    assert rep.statuses["done"] == 1 and http.n_odds == 1
