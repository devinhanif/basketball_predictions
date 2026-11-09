"""Pull-request store: dedupe, cooldown, statuses, readers on a missing file."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from nba.pulls.config import PullsConfig, load_pulls_config
from nba.pulls.store import PullStore, request_pull
from tests.odds.test_live_close import SLATE
from tests.pulls.conftest import G1, at, file_request

NOW = at(120)
NEED = NOW.replace(tzinfo=None)


def req(store: PullStore, game: str, now, reason: str = "r", cooldown_min: float = 0.0):  # type: ignore[no-untyped-def]
    return store.request(
        source="pinnacle", scope_game_id=game, slate_date=SLATE, need_by_utc=NEED,
        max_age_min=30, requester="jarvis", reason=reason, now=now, cooldown_min=cooldown_min,
    )  # fmt: skip


def test_readers_on_a_missing_store_are_empty(tmp_path: Path) -> None:
    st = PullStore(tmp_path / "none" / "pulls.duckdb")
    assert st.open_requests() == [] and st.list_requests() == []
    assert not (tmp_path / "none").exists()  # a reader never creates anything


def test_one_open_request_per_source_and_game(store: PullStore) -> None:
    a = req(store, G1, NOW, "r1")
    b = req(store, G1, NOW + timedelta(minutes=2), "r2")
    c = req(store, "G2", NOW, "r3")
    assert a.created and not b.created and c.created
    assert b.request.request_id == a.request.request_id
    assert {r.scope_game_id for r in store.open_requests()} == {G1, "G2"}
    assert len(store.list_requests()) == 2


def test_columns_and_naive_utc(store: PullStore) -> None:
    rid = file_request(store, NOW)
    (r,) = store.open_requests()
    assert r.request_id == rid and r.status == "open" and r.source == "pinnacle"
    assert (r.requester, r.reason, r.max_age_min, r.slate_date) == ("jarvis", "test", 30.0, SLATE)
    assert r.created_at_utc.tzinfo is None and r.created_at_utc == NEED


def test_done_allows_a_new_request_and_cooldown_blocks_after_a_refusal(store: PullStore) -> None:
    first = req(store, G1, NOW)
    store.set_status(first.request.request_id, "done", NOW, "ok")
    nxt = req(store, G1, NOW + timedelta(minutes=5), "again", cooldown_min=10)
    assert nxt.created  # done is not a cooldown status: a new stale reference gets a new request
    store.set_status(nxt.request.request_id, "refused_budget", NOW + timedelta(minutes=6), "cap")
    again = req(store, G1, NOW + timedelta(minutes=8), "x", cooldown_min=10)
    assert not again.created and again.request.status == "refused_budget"
    later = req(store, G1, NOW + timedelta(minutes=17), "x", cooldown_min=10)
    assert later.created


def test_unknown_status_is_rejected(store: PullStore) -> None:
    rid = file_request(store, NOW)
    with pytest.raises(ValueError):
        store.set_status(rid, "weird", NOW)


def test_request_pull_api_uses_config_defaults(pcfg: PullsConfig) -> None:
    kw = dict(source="pinnacle", scope_game_id=G1, slate_date=SLATE, requester="jarvis",
              reason="why", now=NOW, cfg=pcfg)  # fmt: skip
    res = request_pull(**kw)  # type: ignore[arg-type]
    r = res.request
    assert res.created and r.max_age_min == pcfg.pinnacle_alert_max_age_min
    assert r.need_by_utc == NEED + timedelta(minutes=pcfg.request_need_by_min)
    again = request_pull(**kw)  # type: ignore[arg-type]
    assert not again.created and again.request.request_id == r.request_id


def test_shipped_config_loads_and_matches_the_spec() -> None:
    c = load_pulls_config()
    assert c.enabled and c.pinnacle_alert_max_age_min == 30
    assert c.requests_db.name == "pulls.duckdb" and c.requests_db.parent.name == "ops"
    assert c.wd_warn_pinnacle_age_min == 60 and c.wd_window_h == 3
