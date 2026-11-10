"""A real uvicorn server on a free local port plus a stdlib SSE client, for the live tests and the
latency measurement. Never touches the ports of a running server (it binds port 0 on 127.0.0.1)."""

from __future__ import annotations

import http.client
import json
import random
import shutil
import socket
import statistics
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import uvicorn

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "live"
EVENT = "401909952"
DATE = "2026-11-09"


def body_dir(root: Path, event: str = EVENT, date: str = DATE) -> Path:
    return root / f"live_{date}" / "espn_summary" / event


def copy_fixture(root: Path) -> None:
    shutil.copytree(FIXTURE, root, dirs_exist_ok=True)


def land(src: Path, dest_dir: Path, stamp: str) -> float:
    """Land a body the way the capture does (tmp file, then rename); returns time.time() after."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / f"{stamp}.json.gz.tmp"
    tmp.write_bytes(src.read_bytes())
    tmp.rename(dest_dir / f"{stamp}.json.gz")
    return time.time()


@dataclass
class Server:
    port: int
    server: uvicorn.Server
    thread: threading.Thread

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def start_server(app: Any) -> Server:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    cfg = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="off",
        timeout_graceful_shutdown=1,
    )
    server = uvicorn.Server(cfg)
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign,attr-defined,unused-ignore]
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    return Server(port, server, t)


@dataclass
class Event:
    kind: str
    data: dict[str, Any] | None
    at: float  # time.time() when the blank line ending the event arrived


@dataclass
class SseClient:
    port: int
    path: str
    events: list[Event] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    closed: bool = False
    status: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _thread: threading.Thread | None = None
    _conn: http.client.HTTPConnection | None = None

    def start(self) -> SseClient:
        self._conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        self._conn.request("GET", self.path, headers={"Accept": "text/event-stream"})
        resp = self._conn.getresponse()
        self.status = resp.status
        self._thread = threading.Thread(target=self._read, args=(resp,), daemon=True)
        self._thread.start()
        return self

    def _read(self, resp: http.client.HTTPResponse) -> None:
        kind, data = "message", ""
        try:
            while True:
                line = resp.readline()
                if not line:
                    break
                text = line.decode().rstrip("\r\n")
                if text == "":
                    if data:
                        with self._lock:
                            self.events.append(Event(kind, json.loads(data), time.time()))
                    kind, data = "message", ""
                elif text.startswith(":"):
                    with self._lock:
                        self.comments.append(text)
                elif text.startswith("event:"):
                    kind = text[6:].strip()
                elif text.startswith("data:"):
                    data += text[5:].strip()
        except (OSError, http.client.HTTPException, AttributeError, ValueError):
            pass
        self.closed = True

    def wait(self, pred: Callable[[list[Event]], bool], timeout: float = 10.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            with self._lock:
                if pred(self.events):
                    return True
            time.sleep(0.005)
        return False

    def snapshot(self) -> list[Event]:
        with self._lock:
            return list(self.events)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()


def replay_latency(root: Path, app: Any, n: int = 40, seed: int = 7) -> list[float]:
    """Seconds from a body landing on disk to its delta being received, over ``n`` landings of
    recorded in-progress bodies at random phases against the watcher (default 0.2 s poll)."""
    d = body_dir(root)
    shutil.rmtree(d, ignore_errors=True)
    names = sorted(p.name for p in (FIXTURE / f"live_{DATE}" / "espn_summary" / EVENT).iterdir())
    bodies = [
        FIXTURE / f"live_{DATE}" / "espn_summary" / EVENT / x for x in names[1:4]
    ]  # in progress
    land(bodies[0], d, "20261110T010000Z")
    srv = start_server(app)
    try:
        c = SseClient(srv.port, f"/api/live/{EVENT}/stream").start()
        assert c.wait(lambda ev: any(e.kind == "snapshot" for e in ev))
        rng = random.Random(seed)  # noqa: S311  (test timing jitter)
        out: list[float] = []
        for i in range(n):
            time.sleep(rng.uniform(0.2, 0.9))
            stamp = f"20261110T01{(i + 1) // 60:02d}{(i + 1) % 60:02d}Z"
            t_land = land(bodies[(i + 1) % 3], d, stamp)
            want = i + 1

            def got(ev: list[Event], w: int = want) -> bool:
                return sum(e.kind == "delta" for e in ev) >= w

            assert c.wait(got, 5.0)
            deltas = [e for e in c.snapshot() if e.kind == "delta"]
            out.append(deltas[i].at - t_land)
        c.close()
        return out
    finally:
        srv.stop()


def percentile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, round(q * (len(s) - 1)))]


if __name__ == "__main__":
    import tempfile

    from parlayhitters_api import live as L
    from parlayhitters_api.main import create_app

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        copy_fixture(root)
        import os

        os.environ[L.LIVE_ENV] = str(root)
        lat = replay_latency(root, create_app(), n=60)
        print(
            f"n={len(lat)} p50={statistics.median(lat) * 1000:.0f} ms "
            f"p95={percentile(lat, 0.95) * 1000:.0f} ms max={max(lat) * 1000:.0f} ms"
        )
