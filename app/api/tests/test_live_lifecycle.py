"""Server-side lifecycle of the live stream: a stale signal when the capture stops growing, a clean
end when it stays stopped or the stream reaches its cap, and the viewer slot released each time."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
from live_harness import DATE, EVENT, FIXTURE, SseClient, body_dir, copy_fixture, land, start_server

from parlayhitters_api import live as L
from parlayhitters_api import live_routes
from parlayhitters_api.main import create_app

SRC = FIXTURE / f"live_{DATE}" / "espn_summary" / EVENT
NAMES = sorted(p.name for p in SRC.iterdir())  # pre, 2 early, mid, final


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    copy_fixture(tmp_path)
    monkeypatch.setenv(L.LIVE_ENV, str(tmp_path))
    monkeypatch.setenv(L.CHECKPOINT_ENV, str(tmp_path / "none.json"))
    d = body_dir(tmp_path)
    shutil.rmtree(d)
    land(SRC / NAMES[1], d, "20261110T002113Z")  # one in-progress body; nothing else lands
    return tmp_path


def _released(app: object, timeout: float = 10.0) -> bool:
    hub = app.state.live_hub  # type: ignore[attr-defined]
    end = time.time() + timeout
    while time.time() < end:
        if hub.n_subs == 0 and hub.games == {}:
            return True
        time.sleep(0.05)
    return False


def test_a_capture_that_stops_growing_gets_stale_then_end(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live_routes, "HEARTBEAT_S", 0.2)
    monkeypatch.setattr(live_routes, "STALE_AFTER_S", 0.5)
    monkeypatch.setattr(live_routes, "IDLE_CLOSE_S", 1.5)
    app = create_app()
    srv = start_server(app)
    try:
        c = SseClient(srv.port, f"/api/live/{EVENT}/stream").start()
        assert c.wait(lambda ev: any(e.kind == "stale" for e in ev), timeout=5)
        assert c.wait(lambda ev: any(e.kind == "end" for e in ev), timeout=5)
        kinds = [e.kind for e in c.snapshot()]
        assert kinds[0] == "snapshot" and kinds.count("stale") == 1 and kinds[-1] == "end"
        end = c.snapshot()[-1].data
        assert end is not None and end["reason"] == "capture_stopped" and end["idle_s"] >= 1.5
        deadline = time.time() + 5
        while not c.closed and time.time() < deadline:
            time.sleep(0.02)
        assert c.closed, "the server closes the stream after end"
        assert any(x == ": hb" for x in c.comments), "heartbeats keep flowing before stale"
        assert _released(app)
    finally:
        srv.stop()


def test_a_new_body_clears_stale_and_keeps_the_stream_open(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live_routes, "HEARTBEAT_S", 0.2)
    monkeypatch.setattr(live_routes, "STALE_AFTER_S", 0.6)
    monkeypatch.setattr(live_routes, "IDLE_CLOSE_S", 60.0)
    app = create_app()
    srv = start_server(app)
    try:
        c = SseClient(srv.port, f"/api/live/{EVENT}/stream").start()
        assert c.wait(lambda ev: any(e.kind == "stale" for e in ev), timeout=5)
        land(SRC / NAMES[2], body_dir(root), "20261110T004116Z")
        assert c.wait(lambda ev: any(e.kind == "delta" for e in ev), timeout=5)
        # stale again only after another quiet spell, and never an end while bodies may come
        assert c.wait(lambda ev: [e.kind for e in ev].count("stale") == 2, timeout=5)
        assert not any(e.kind == "end" for e in c.snapshot()) and not c.closed
        c.close()
        assert _released(app)
    finally:
        srv.stop()


def test_a_stream_ends_at_its_cap_even_while_bodies_land(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live_routes, "HEARTBEAT_S", 0.2)
    monkeypatch.setattr(live_routes, "STREAM_MAX_S", 1.0)
    app = create_app()
    srv = start_server(app)
    try:
        c = SseClient(srv.port, f"/api/live/{EVENT}/stream").start()
        assert c.wait(lambda ev: len(ev) >= 1)
        land(SRC / NAMES[2], body_dir(root), "20261110T004116Z")
        assert c.wait(lambda ev: any(e.kind == "end" for e in ev), timeout=5)
        end = c.snapshot()[-1].data
        assert end is not None and end["reason"] == "max_duration"
        assert _released(app)
    finally:
        srv.stop()


def test_the_bounds_are_sane() -> None:
    assert L.HEARTBEAT_S < L.STALE_AFTER_S < L.IDLE_CLOSE_S < L.STREAM_MAX_S
    # a whole game (about 2.5 h) fits inside one stream
    assert L.STREAM_MAX_S >= 3 * 3600
