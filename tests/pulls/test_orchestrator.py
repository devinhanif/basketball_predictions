# ruff: noqa: E501
"""The orchestrator tick on a fake vendor and tmp stores: expiry, post-tip refusal, the close reserve,
the ledger request id, and the on-demand snapshot kind. No network, no real data directory."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import duckdb
import pytest

from nba.odds import live_close as lc
from nba.pulls.config import PullsConfig
from nba.pulls.orchestrator import ondemand_allowed, tick
from nba.pulls.store import PullStore
from tests.odds.test_live_close import ENV_ON, FakeHttp, FakeResp, event_odds_body
from tests.pulls.conftest import G1, GAME, SLATE, at, file_request, market_rows, run_tick

NOW = at(60)  # an hour before tip


def statuses(store: PullStore) -> dict[str, str]:
    return {r.request_id: r.status for r in store.list_requests()}


# ---- the pure reserve rule -----------------------------------------------------------------------


def test_reserve_rule_numbers() -> None:
    kw = dict(call_cost=267, close_slots=1, server_remaining=None, server_floor=100_000)
    # 15 games not yet closed: reserve = 15 x 267 = 4,005. cap 102,000
    assert ondemand_allowed(cap=102_000, spent_today=60_000, outstanding_closes=15, **kw)[0]  # type: ignore[arg-type]
    edge = 102_000 - 267 - 15 * 267  # the largest spend that still allows one more call
    assert ondemand_allowed(cap=102_000, spent_today=edge, outstanding_closes=15, **kw)[0]  # type: ignore[arg-type]
    ok, why = ondemand_allowed(cap=102_000, spent_today=edge + 1, outstanding_closes=15, **kw)  # type: ignore[arg-type]
    assert not ok and "reserve 4005" in why
    # fewer games left, smaller reserve: the same spend is allowed once the slate has tipped
    assert ondemand_allowed(cap=102_000, spent_today=edge + 1, outstanding_closes=14, **kw)[0]  # type: ignore[arg-type]
    # the server floor is held back by the reserve too
    ok2, why2 = ondemand_allowed(
        cap=10**9, spent_today=0, call_cost=267, outstanding_closes=2, close_slots=1,
        server_remaining=100_800, server_floor=100_000,
    )  # fmt: skip
    assert not ok2 and "floor" in why2  # 100,800 - 267 - 534 < 100,000


# ---- serving a request ---------------------------------------------------------------------------


def test_request_is_served_with_an_ondemand_snapshot_and_a_ledger_request_id(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore
) -> None:
    rid = file_request(store, NOW)
    http = FakeHttp()
    rep = run_tick(NOW, pcfg, lcfg, store, http)
    assert rep.statuses["done"] == 1 and http.n_odds == 1 and not rep.degraded
    (r,) = store.list_requests()
    assert r.status == "done" and "ondemand1" in r.detail
    kinds = market_rows(lcfg, "SELECT DISTINCT snapshot_kind FROM live_book_odds")
    assert kinds == [("ondemand1",)]
    (log,) = market_rows(lcfg, "SELECT snapshot_kind, status, credits FROM live_capture_log")
    assert log == ("ondemand1", "ok", 14)
    # every ledger line the pull caused names the request
    jsonl = next(lcfg.ledger_dir.glob("the_odds_api_live_*.jsonl"))
    lines = [json.loads(x) for x in jsonl.read_text().splitlines()]
    assert lines and all(x["request_id"] == rid for x in lines)
    assert any(x["endpoint"] == "event_odds" and x["cost"] == 14 for x in lines)


def test_a_later_request_makes_ondemand2_not_a_collision(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore
) -> None:
    file_request(store, NOW)
    run_tick(NOW, pcfg, lcfg, store)
    later = NOW + timedelta(minutes=40)
    file_request(store, later)
    http = FakeHttp()
    rep = run_tick(later, pcfg, lcfg, store, http)
    assert rep.statuses["done"] == 1 and http.n_odds == 1
    assert market_rows(lcfg, "SELECT DISTINCT snapshot_kind FROM live_book_odds ORDER BY 1") == [
        ("ondemand1",),
        ("ondemand2",),
    ]


def test_no_open_request_means_no_network_no_key_no_market_store(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore
) -> None:
    http = FakeHttp()
    rep = run_tick(NOW, pcfg, lcfg, store, http, environ={"NBA_CLOSE_CAPTURE": "1"})  # no key
    assert rep.seen == 0 and not rep.disabled and http.calls == []
    assert not lcfg.out_db.exists()


def test_disabled_flag_or_env_gate_is_a_noop(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore
) -> None:
    file_request(store, NOW)
    http = FakeHttp()
    off_env = run_tick(NOW, pcfg, lcfg, store, http, environ={"THE_ODDS_API_KEY": "k"})
    assert "NBA_CLOSE_CAPTURE" in off_env.disabled and http.calls == []
    off_cfg = tick(
        NOW, cfg=replace(pcfg, enabled=False), live_cfg=lcfg, store=store, environ=ENV_ON
    )
    assert "configs/pulls.yaml" in off_cfg.disabled
    assert [r.status for r in store.list_requests()] == ["open"] and not lcfg.out_db.exists()


def test_already_fresh_reference_spends_nothing(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore
) -> None:
    file_request(store, NOW)
    run_tick(NOW - timedelta(minutes=10), pcfg, lcfg, store, FakeHttp())  # a pull 10 min ago
    for r in store.list_requests():
        store.set_status(r.request_id, "done", NOW, "")
    rid = file_request(store, NOW, max_age_min=30)
    http = FakeHttp()
    rep = run_tick(NOW, pcfg, lcfg, store, http)
    assert http.calls == [] and rep.statuses["done"] == 1
    got = {r.request_id: r for r in store.list_requests()}[rid]
    assert got.status == "done" and "already fresh" in got.detail


# ---- expiry and the known-at rule ----------------------------------------------------------------


def test_request_past_need_by_expires_without_a_call(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore
) -> None:
    file_request(store, NOW, need_by_min=5)
    http = FakeHttp()
    rep = run_tick(NOW + timedelta(minutes=6), pcfg, lcfg, store, http)
    assert rep.statuses["expired"] == 1 and http.calls == []
    (r,) = store.list_requests()
    assert r.status == "expired" and "need_by" in r.detail


@pytest.mark.parametrize("after_tip_s", [0, 1, 600])
def test_request_at_or_after_tip_is_refused_and_never_pulled(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore, after_tip_s: int
) -> None:
    file_request(store, NOW, need_by_min=600)
    http = FakeHttp()
    now = at(0) + timedelta(seconds=after_tip_s)
    rep = run_tick(now, pcfg, lcfg, store, http)
    assert rep.statuses["refused_post_tip"] == 1 and http.calls == []
    assert store.list_requests()[0].status == "refused_post_tip"
    assert not lcfg.out_db.exists()


def test_response_arriving_after_tip_is_refused_by_the_existing_check(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore
) -> None:
    now = at(1)
    file_request(store, now)
    ticks = iter(
        [now, now + timedelta(seconds=2), now + timedelta(seconds=90)]
    )  # client day, request, reply after tip
    rep = run_tick(now, pcfg, lcfg, store, FakeHttp(), clock=lambda: next(ticks))
    assert rep.statuses["refused_post_tip"] == 1
    assert market_rows(lcfg, "SELECT count(*) FROM live_book_odds") == [(0,)]


# ---- budget: the close is never starved ----------------------------------------------------------


def test_reserve_protects_the_close_of_every_remaining_game(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore
) -> None:
    g2 = lc.LiveGame("G2", GAME.tipoff + timedelta(hours=1), GAME.home_team, GAME.away_team)
    file_request(store, NOW)
    tight = replace(lcfg, daily_max_credits=30)  # call 14 + reserve 2 x 14 = 42 > 30
    http = FakeHttp()
    rep = run_tick(NOW, pcfg, tight, store, http, games=[GAME, g2])
    assert rep.statuses["refused_budget"] == 1 and http.calls == []
    r = store.list_requests()[0]
    assert r.status == "refused_budget" and "close reserve" in r.detail and "reserve 28" in r.detail


def test_a_stored_close_releases_its_reserve(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore
) -> None:
    g2 = lc.LiveGame("G2", GAME.tipoff + timedelta(hours=1), GAME.home_team, GAME.away_team)
    con = lc.open_store(lcfg.out_db)
    con.execute(
        "INSERT INTO live_capture_log VALUES ('G2','close',NULL,NULL,NULL,'e','ok',1,14,'')"
    )
    con.close()
    file_request(store, NOW)
    tight = replace(lcfg, daily_max_credits=30)  # call 14 + reserve 1 x 14 = 28 <= 30
    rep = run_tick(NOW, pcfg, tight, store, FakeHttp(), games=[GAME, g2])
    assert rep.statuses["done"] == 1


def test_spend_so_far_counts_against_the_reserve(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore
) -> None:
    file_request(store, NOW)
    cap = replace(lcfg, daily_max_credits=40)  # one call (14) + reserve (14) = 28 fits an empty day
    http = FakeHttp()
    assert run_tick(NOW, pcfg, cap, store, http).statuses["done"] == 1  # spent 14
    file_request(store, NOW + timedelta(minutes=40))  # the first pull is 40 min old: stale again
    rep = run_tick(NOW + timedelta(minutes=40), pcfg, cap, store, http)  # 14 + 14 + 14 = 42 > 40
    assert rep.statuses["refused_budget"] == 1 and http.n_odds == 1


def test_per_game_and_daily_ondemand_caps(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore
) -> None:
    one = replace(pcfg, ondemand_max_per_game=1)
    file_request(store, NOW)
    run_tick(NOW, one, lcfg, store)
    file_request(store, NOW + timedelta(minutes=40))
    http = FakeHttp()
    rep = run_tick(NOW + timedelta(minutes=40), one, lcfg, store, http)
    assert rep.statuses["refused_budget"] == 1 and http.calls == []
    assert "per-game cap" in store.list_requests()[0].detail
    credit = replace(pcfg, ondemand_daily_credit_cap=14)
    file_request(store, NOW + timedelta(minutes=80), "G1")
    rep2 = run_tick(NOW + timedelta(minutes=45), credit, lcfg, store, http)
    assert rep2.statuses["refused_budget"] >= 1 and http.calls == []


# ---- failures ------------------------------------------------------------------------------------


def test_missing_key_fails_the_request_without_leaking(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore
) -> None:
    file_request(store, NOW)
    rep = run_tick(NOW, pcfg, lcfg, store, environ={"NBA_CLOSE_CAPTURE": "1"})
    assert rep.statuses["failed"] == 1 and rep.degraded
    assert "THE_ODDS_API_KEY" in store.list_requests()[0].detail


def test_unmatched_vendor_event_fails_the_request(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore
) -> None:
    file_request(store, NOW)
    http = FakeHttp(commence="2026-10-20T20:00:00Z")  # three hours off our tip
    rep = run_tick(NOW, pcfg, lcfg, store, http)
    assert rep.statuses["failed"] == 1 and http.n_odds == 0
    assert "no unique vendor event" in store.list_requests()[0].detail


def test_pull_without_a_pinnacle_row_is_failed_not_done(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore
) -> None:
    body = event_odds_body()
    body["bookmakers"][0]["key"] = "fanduel"
    http = FakeHttp(
        FakeResp(200, body, {"x-requests-last": "14", "x-requests-remaining": "4999000"})
    )
    file_request(store, NOW)
    rep = run_tick(NOW, pcfg, lcfg, store, http)
    assert rep.statuses["failed"] == 1
    assert "no Pinnacle row" in store.list_requests()[0].detail


def test_unsupported_source_and_unknown_game_fail_loudly(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore
) -> None:
    store.request(
        source="kalshi", scope_game_id=G1, slate_date=SLATE,
        need_by_utc=NOW.replace(tzinfo=None) + timedelta(hours=1),
        max_age_min=5, requester="x", reason="p2", now=NOW,
    )  # fmt: skip
    file_request(store, NOW, "GHOST")
    rep = run_tick(NOW, pcfg, lcfg, store)
    assert rep.statuses["failed"] == 2
    details = " | ".join(r.detail for r in store.list_requests())
    assert "not supported in P1" in details and "no real tip" in details


def test_market_store_held_by_a_writer_leaves_requests_open(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    file_request(store, NOW)

    def busy(*_a: object, **_k: object) -> None:  # another process owns the single writer slot
        raise duckdb.IOException("Could not set lock on file")

    monkeypatch.setattr(lc, "open_store", busy)
    rep = run_tick(NOW, pcfg, lcfg, store, FakeHttp())
    assert any("market store busy" in m for m in rep.messages)
    assert store.list_requests()[0].status == "open"


def test_no_key_in_anything_the_tick_wrote(
    pcfg: PullsConfig, lcfg: lc.LiveConfig, store: PullStore, tmp_path: Path
) -> None:
    file_request(store, NOW)
    run_tick(NOW, pcfg, lcfg, store)
    key = ENV_ON["THE_ODDS_API_KEY"]
    for p in tmp_path.rglob("*"):
        if p.is_file() and p.suffix in {".json", ".jsonl", ".duckdb"}:
            assert key.encode() not in p.read_bytes(), p
