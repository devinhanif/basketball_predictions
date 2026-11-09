# ruff: noqa: E501
"""A paid call must not become invisible: the store is reopened with the capture's budget, and the caps
count the ledger's request_id lines, not only the capture log."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from typing import Any

import duckdb
import pytest

from nba.odds import live_close as lc
from nba.pulls.config import PullsConfig
from nba.pulls.store import PullStore
from tests.odds.test_live_close import FakeHttp
from tests.pulls.conftest import at, file_request, run_tick

NOW = at(60)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"calls": [], "fail_after_paid": False}
    real = lc.open_store

    def open_store(path: Any, retries: int = 6, wait_s: float = 5.0) -> duckdb.DuckDBPyConnection:
        state["calls"].append((retries, wait_s))
        if state["fail_after_paid"] and (retries, wait_s) == (6, 5.0):
            raise duckdb.IOException("Could not set lock: another writer holds the store")
        return real(path, retries=1, wait_s=0.0)

    monkeypatch.setattr(lc, "open_store", open_store)
    return state


def test_the_reopen_after_a_paid_call_uses_the_capture_budget(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore, spy: dict[str, Any]
) -> None:
    file_request(store, NOW)
    assert run_tick(NOW, pcfg, lcfg, store).statuses["done"] == 1
    calls = spy["calls"]
    assert (pcfg.store_retries, pcfg.store_wait_s) in calls  # before any vendor call: short budget
    assert calls[-1] == (6, 5.0) == (pcfg.reopen_retries, pcfg.reopen_wait_s)  # after: 6 x 5 s


def test_a_paid_call_whose_store_never_came_back_is_failed_and_counted(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore, spy: dict[str, Any]
) -> None:
    spy["fail_after_paid"] = True
    rid = file_request(store, NOW)
    rep = run_tick(NOW, pcfg, lcfg, store)
    (r,) = store.list_requests()
    assert r.status == "failed" and "paid call not stored" in r.detail and rep.degraded
    jsonl = next(lcfg.ledger_dir.glob("the_odds_api_live_*.jsonl"))
    paid = [
        x for x in map(json.loads, jsonl.read_text().splitlines()) if x["endpoint"] == "event_odds"
    ]
    assert len(paid) == 1 and paid[0]["request_id"] == rid and paid[0]["cost"] == 14
    # no capture-log row exists for the spend, yet the next request for the game sees it
    spy["fail_after_paid"] = False
    one = replace(pcfg, ondemand_max_per_game=1)
    later = NOW + timedelta(minutes=40)
    file_request(store, later)
    http = FakeHttp()
    rep2 = run_tick(later, one, lcfg, store, http)
    assert rep2.statuses["refused_budget"] == 1 and http.calls == []
    assert "per-game cap" in store.list_requests()[0].detail


def test_the_daily_credit_cap_counts_ledger_lines_with_a_request_id(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore, spy: dict[str, Any]
) -> None:
    spy["fail_after_paid"] = True
    file_request(store, NOW)
    run_tick(NOW, pcfg, lcfg, store)  # 14 credits paid, nothing stored
    spy["fail_after_paid"] = False
    capped = replace(pcfg, ondemand_daily_credit_cap=14)
    later = NOW + timedelta(minutes=40)
    file_request(store, later)
    http = FakeHttp()
    rep = run_tick(later, capped, lcfg, store, http)
    assert rep.statuses["refused_budget"] == 1 and http.calls == []
    assert "credit cap" in store.list_requests()[0].detail
