"""Live play-by-play: parsing, newest-body choice, deltas, routes, SSE over a real local server, the
latency of a landed body, the checkpoint gate, and independence from the export store."""

from __future__ import annotations

import asyncio
import gzip
import json
import re
import shutil
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient
from live_harness import (
    DATE,
    EVENT,
    FIXTURE,
    SseClient,
    body_dir,
    copy_fixture,
    land,
    percentile,
    replay_latency,
    start_server,
)

from parlayhitters_api import live as L
from parlayhitters_api import live_routes
from parlayhitters_api.main import create_app

SRC = FIXTURE / f"live_{DATE}" / "espn_summary" / EVENT
NAMES = sorted(p.name for p in SRC.iterdir())  # pre, 2 early, mid, final


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    copy_fixture(tmp_path)
    monkeypatch.setenv(L.LIVE_ENV, str(tmp_path))
    monkeypatch.setenv(L.CHECKPOINT_ENV, str(tmp_path / "none.duckdb"))
    return tmp_path


def state_of(name: str) -> L.GameState:
    stamp = name.removesuffix(".json.gz")
    ref = L.BodyRef(SRC / name, "capture", stamp, 0)
    return L.load_state(ref, DATE, EVENT)


# ------------------------------------------------------------------------------ parsing


def test_parse_mid_game_body() -> None:
    gs = state_of(NAMES[3])
    assert gs.state == "in"
    assert (gs.away["abbr"], gs.home["abbr"]) == ("LAL", "ATL")
    assert (gs.away["score"], gs.home["score"]) == (86, 76)
    assert gs.status["period"] == 3 and gs.status["clock"] == "3:39"
    assert gs.status["elapsed_min"] == pytest.approx(24 + (12 * 60 - 219) / 60, abs=0.01)
    assert len(gs.plays) > 300
    assert gs.last_play_at is not None and gs.play_lag_s is not None and gs.play_lag_s >= 0
    rows = [r for r in gs.box.values() if r["team"] == "LAL"]
    assert rows and {"pts", "reb", "ast", "fg3m", "pf", "min", "name"} <= set(rows[0])
    assert sum(r["pts"] for r in rows) == 86  # the box adds up to the score


def test_parse_pre_game_and_final() -> None:
    pre = state_of(NAMES[0])
    assert pre.state == "pre" and not pre.plays and pre.status["elapsed_min"] is None
    fin = state_of(NAMES[-1])
    assert fin.state == "post"


def test_bad_body_is_an_error_not_a_crash(tmp_path: Path) -> None:
    p = tmp_path / "20261110T000000Z.json.gz"
    p.write_bytes(gzip.compress(b'{"nope": 1}'))
    with pytest.raises(L.BodyError):
        L.load_state(L.BodyRef(p, "capture", "20261110T000000Z", 0), DATE, EVENT)
    p.write_bytes(b"not gzip")
    with pytest.raises(L.BodyError):
        L.load_state(L.BodyRef(p, "capture", "20261110T000000Z", 0), DATE, EVENT)


def test_elapsed_minutes_overtime() -> None:
    assert L.elapsed_minutes(2, "12:00") == 12
    assert L.elapsed_minutes(4, "0:00") == 48
    assert L.elapsed_minutes(5, "2:30") == 50.5


# ------------------------------------------------------------------------------ newest body


def test_newest_across_capture_and_fresh(root: Path) -> None:
    ref = L.newest_body(root, DATE, EVENT)
    assert ref is not None and ref.source == "capture" and ref.stamp == "20261110T023731Z"
    fresh = root / f"live_{DATE}" / "fresh" / "espn_summary_fresh" / EVENT
    land(SRC / NAMES[3], fresh, "20261110T023800Z")  # fetched later: wins
    ref = L.newest_body(root, DATE, EVENT)
    assert ref is not None and ref.source == "fresh"
    land(SRC / NAMES[3], fresh, "20261110T020000Z")  # older fresh does not win
    assert L.newest_body(root, DATE, EVENT) is not None
    land(SRC / NAMES[2], fresh, "20261110T023731Z")  # same second: fresh wins the tie
    tie = L.newest_body(root, DATE, EVENT)
    assert tie is not None and tie.stamp == "20261110T023800Z"  # still the newest of all


def test_tmp_files_are_ignored(root: Path) -> None:
    d = body_dir(root)
    (d / "20261110T030000Z.json.gz.tmp").write_bytes(b"half")
    ref = L.newest_body(root, DATE, EVENT)
    assert ref is not None and ref.stamp == "20261110T023731Z"


# ------------------------------------------------------------------------------ deltas


def test_delta_has_only_what_changed() -> None:
    a, b = state_of(NAMES[1]), state_of(NAMES[2])
    now = datetime.now(UTC)
    d = L.delta_payload(a, b, 1, now)
    assert d["changed"] is True
    new_plays = {p["n"] for p in d["plays"]}
    assert new_plays == set(b.plays) - set(a.plays)
    assert all(a.plays.get(n) != b.plays[n] for n in new_plays)
    changed_box = {r["id"] for r in d["box"]}
    assert changed_box <= set(b.box)
    assert len(changed_box) < len(b.box)  # not the whole box
    assert d["status"] == b.status
    again = L.delta_payload(b, b, 2, now)
    assert again["changed"] is False and "plays" not in again and "box" not in again
    assert len(json.dumps(again)) < 400  # an unchanged body costs a few hundred bytes
    snap = L.snapshot_payload(b, 1, now)
    assert len(snap["plays"]) == min(L.SNAPSHOT_PLAYS, len(b.plays))
    assert snap["n_plays"] == len(b.plays)
    assert len(json.dumps(d)) < len(json.dumps(snap))


def test_plays_before_pages_backwards() -> None:
    gs = state_of(NAMES[3])
    page = L.plays_before(gs, 200, 50)
    assert len(page) == 50 and page[-1]["n"] < 200
    assert [p["n"] for p in page] == sorted(p["n"] for p in page)


# ------------------------------------------------------------------------------ routes


def test_games_on_lists_games_with_age(root: Path) -> None:
    c = TestClient(create_app())
    r = c.get(f"/api/live/{DATE}")
    assert r.status_code == 200
    j = r.json()
    assert j["attribution"] == "Play-by-play: ESPN. Not affiliated."
    assert "Delayed feed" in j["delay_note"]
    ids = [g["game_id"] for g in j["games"]]
    assert ids == [EVENT, "401909955"]
    g = j["games"][0]
    assert g["state"] == "post" and g["fetched_at"] == "2026-11-10T02:37:31+00:00"
    assert g["age_s"] > 0 and (g["away"]["abbr"], g["home"]["abbr"]) == ("LAL", "ATL")
    assert "plays" not in g
    assert j["games"][1]["state"] == "pre"


def test_unknown_date_and_bad_ids(root: Path) -> None:
    c = TestClient(create_app())
    j = c.get("/api/live/2026-01-01").json()
    assert j["games"] == [] and "No live capture" in j["note"]
    assert c.get("/api/live/notadate").status_code == 404
    assert c.get("/api/live/abc/snapshot").status_code == 404
    assert c.get("/api/live/../etc/stream").status_code == 404
    assert c.get("/api/live/123456789/stream").status_code == 404
    assert c.get("/api/live/123456789/snapshot").status_code == 404


def test_snapshot_and_plays_routes(root: Path) -> None:
    c = TestClient(create_app())
    s = c.get(f"/api/live/{EVENT}/snapshot").json()
    assert s["state"] == "post" and len(s["plays"]) == L.SNAPSHOT_PLAYS and s["n_plays"] > 400
    assert s["attribution"].startswith("Play-by-play: ESPN")
    older = c.get(f"/api/live/{EVENT}/plays", params={"before": s["first_n"], "limit": 25}).json()
    assert len(older["plays"]) == 25 and older["more"] is True
    assert max(p["n"] for p in older["plays"]) < s["first_n"]
    assert c.get(f"/api/live/{EVENT}/plays", params={"limit": 0}).status_code == 422


def test_live_routes_do_not_need_the_store(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import parlayhitters_api.main as m

    def boom() -> None:
        raise AssertionError("the live routes must not load the export store")

    monkeypatch.setattr(m, "load_store", boom)
    m.reset_store()
    c = TestClient(create_app())
    assert c.get(f"/api/live/{DATE}").status_code == 200
    assert c.get(f"/api/live/{EVENT}/snapshot").status_code == 200
    assert c.get(f"/api/live/{EVENT}/checkpoint").status_code == 200


def test_live_module_does_not_import_nba() -> None:
    for f in ("live.py", "live_routes.py"):
        text = (Path(live_routes.__file__).parent / f).read_text()
        assert not re.search(r"^\s*(from|import)\s+nba\b", text, re.M), f
        assert "import httpx" not in text and "import requests" not in text and "urllib" not in text


# ------------------------------------------------------------------------------ checkpoint gate


def make_checkpoint_db(path: Path, event: str) -> None:
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE checkpoint_scored (checkpoint INTEGER, e_used DOUBLE, "
        "model_support VARCHAR, period INTEGER, clock VARCHAR, known_at TIMESTAMP, "
        "player_name VARCHAR, stat VARCHAR, k INTEGER, p_model DOUBLE, cur INTEGER, "
        "minutes INTEGER, fouls INTEGER, event_id VARCHAR, mode VARCHAR, "
        "ask_c INTEGER, edge_model DOUBLE)"
    )
    for ck, p in ((20, 0.4), (25, 0.5)):
        con.execute(
            "INSERT INTO checkpoint_scored VALUES (?, 25.1, 'trained', 3, '3:39', "
            "'2026-11-10 01:40:00', 'Luka Doncic', 'pts', 30, ?, 22, 25, 1, ?, 'live', 55, 0.07)",
            [ck, p, event],
        )
    con.execute(
        "INSERT INTO checkpoint_scored VALUES (25, 25.1, 'trained', 3, '3:39', "
        "'2026-11-10 01:40:00', 'Replay Guy', 'pts', 10, 0.9, 5, 20, 0, ?, 'replay', 55, 0.07)",
        [event],
    )
    con.close()


def test_checkpoint_absent_final_and_present(
    root: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    c = TestClient(create_app())
    j = c.get(f"/api/live/{EVENT}/checkpoint").json()
    assert j["available"] is False and j["label"].startswith("UNPROVEN: no live record")
    db = tmp_path_factory.mktemp("ck") / "ingame_checkpoints.duckdb"
    make_checkpoint_db(db, EVENT)
    r = L.read_checkpoint(EVENT, "in", db)
    assert r["available"] is True and r["checkpoint"] == 25  # the newest checkpoint only
    assert [x["player"] for x in r["rows"]] == ["Luka Doncic"]  # live rows only, not replay
    assert r["label"] == "UNPROVEN: no live record; most nights the market already knows this."
    text = json.dumps(r)
    assert not re.search(r"ask|bid|edge|price|fee", text)
    assert L.read_checkpoint(EVENT, "post", db)["available"] is False
    assert L.read_checkpoint("999999999", "in", db)["available"] is False
    empty = db.with_name("empty.duckdb")
    duckdb.connect(str(empty)).close()
    assert L.read_checkpoint(EVENT, "in", empty)["available"] is False  # no table: quiet, not a 500


# ------------------------------------------------------------------------------ SSE, end to end


def test_stream_snapshot_deltas_final_and_close(root: Path) -> None:
    d = body_dir(root)
    shutil.rmtree(d)
    land(SRC / NAMES[1], d, "20261110T002113Z")
    srv = start_server(create_app())
    try:
        c = SseClient(srv.port, f"/api/live/{EVENT}/stream").start()
        assert c.status == 200
        assert c.wait(lambda ev: len(ev) >= 1)
        first = c.snapshot()[0]
        assert first.kind == "snapshot" and first.data is not None
        assert first.data["state"] == "in" and first.data["meta"]["fetched_at"].startswith(
            "2026-11-10T00:21"
        )
        assert first.data["attribution"] == "Play-by-play: ESPN. Not affiliated."
        seq0 = first.data["seq"]
        land(SRC / NAMES[2], d, "20261110T004116Z")
        assert c.wait(lambda ev: any(e.kind == "delta" for e in ev))
        delta = next(e for e in c.snapshot() if e.kind == "delta")
        assert delta.data is not None and delta.data["seq"] == seq0 + 1 and delta.data["plays"]
        assert delta.data["meta"]["age_s"] >= 0 and "server_now" in delta.data["meta"]
        land(SRC / NAMES[4], d, "20261110T023731Z")  # final
        assert c.wait(lambda ev: any(e.kind == "final" for e in ev))
        deadline = time.time() + 5
        while not c.closed and time.time() < deadline:
            time.sleep(0.02)
        assert c.closed  # the server closes at final
        # a late viewer gets the snapshot and the final marker, then the stream ends
        late = SseClient(srv.port, f"/api/live/{EVENT}/stream").start()
        assert late.wait(lambda ev: any(e.kind == "final" for e in ev))
        assert [e.kind for e in late.snapshot()][:2] == ["snapshot", "final"]
    finally:
        srv.stop()


def test_heartbeat_and_one_watcher_for_many_clients(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live_routes, "HEARTBEAT_S", 0.3)
    d = body_dir(root)
    shutil.rmtree(d)
    land(SRC / NAMES[1], d, "20261110T002113Z")
    app = create_app()
    srv = start_server(app)
    try:
        clients = [SseClient(srv.port, f"/api/live/{EVENT}/stream").start() for _ in range(5)]
        for c in clients:
            assert c.wait(lambda ev: len(ev) >= 1)
        time.sleep(0.8)
        assert all(any(x == ": hb" for x in c.comments) for c in clients)
        land(SRC / NAMES[2], d, "20261110T004116Z")
        for c in clients:
            assert c.wait(lambda ev: any(e.kind == "delta" for e in ev))
        for c in clients:
            c.close()
    finally:
        srv.stop()


def test_hub_one_watcher_task_and_cleanup(root: Path) -> None:
    async def run() -> None:
        hub = L.LiveHub(root, 0.05)
        d = body_dir(root)
        shutil.rmtree(d)
        land(SRC / NAMES[1], d, "20261110T002113Z")
        subs = [await hub.subscribe(EVENT) for _ in range(3)]
        gw = hub.games[EVENT]
        assert len(gw.subs) == 3 and hub.n_subs == 3
        assert gw.task is not None  # one task, however many viewers
        land(SRC / NAMES[2], d, "20261110T004116Z")
        for sub, _first, _final in subs:
            msg = await asyncio.wait_for(sub.queue.get(), 2)
            assert msg is not None and msg.startswith("id: 1\nevent: delta")
        for sub, _f, _fin in subs:
            hub.unsubscribe(EVENT, sub)
        await asyncio.sleep(0.2)
        assert EVENT not in hub.games and hub.n_subs == 0  # the watcher stopped by itself

    asyncio.run(run())


def test_slow_subscriber_is_dropped_not_buffered(root: Path) -> None:
    async def run() -> None:
        hub = L.LiveHub(root, 0.05)
        d = body_dir(root)
        shutil.rmtree(d)
        land(SRC / NAMES[1], d, "20261110T002113Z")
        sub, _first, _final = await hub.subscribe(EVENT)
        gw = hub.games[EVENT]
        for i in range(L.QUEUE_MAX + 5):  # nobody reads
            hub._broadcast(gw, f"m{i}")
        assert sub.dead and sub not in gw.subs
        assert sub.queue.qsize() <= 1  # cleared and ended with the close marker
        assert sub.queue.get_nowait() is None
        hub.unsubscribe(EVENT, sub)

    asyncio.run(run())


def test_viewer_cap_gives_503(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(L, "MAX_SUBS_PER_GAME", 1)

    async def run() -> None:
        hub = L.LiveHub(root, 0.05)
        await hub.subscribe(EVENT)
        with pytest.raises(L.HubFull):
            await hub.subscribe(EVENT)

    d = body_dir(root)
    shutil.rmtree(d)
    land(SRC / NAMES[1], d, "20261110T002113Z")
    asyncio.run(run())


def test_parse_cache_is_bounded(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(L, "PARSE_CACHE_MAX", 2)
    L._parse_cache.clear()
    for n in NAMES[:4]:
        ref = L.BodyRef(SRC / n, "capture", n.removesuffix(".json.gz"), 0)
        L._cached_load(ref, DATE, EVENT)
    assert len(L._parse_cache) == 2


# ------------------------------------------------------------------------------ latency


def test_landed_body_reaches_the_client_quickly(root: Path) -> None:
    lat = replay_latency(root, create_app(), n=16)
    p50, p95 = statistics.median(lat), percentile(lat, 0.95)
    # the watcher polls every 0.2 s, so a body waits 0 to 0.2 s and then costs one parse
    assert p50 < 0.3 and p95 < 0.6, (p50, p95, max(lat))
