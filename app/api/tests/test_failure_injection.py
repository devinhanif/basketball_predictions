"""Failure injection against a running API: the export removed or replaced mid-session, a manifest
with a wrong sha, a database or parlay file that cannot be read, and a slow store. Never a 5xx
other than the typed 503, never a stack trace, and recovery without a restart."""

from __future__ import annotations

import json
import shutil
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import EXPORT_DAY, LOOPBACK_PEER, seal
from fastapi.testclient import TestClient

from parlayhitters_api import main as m
from parlayhitters_api.export_store import ExportRefused, ExportStore

FIX = Path(__file__).resolve().parents[2] / "fixtures"
STORE_ROUTES = ("/api/meta", "/api/slates", f"/api/slates/{EXPORT_DAY}", "/api/health")


@pytest.fixture
def api(export_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_EXPORT_DIR", str(export_dir))
    monkeypatch.setenv("APP_LIVE_DIR", str(FIX / "live"))
    m.reset_store()
    with TestClient(m.create_app(), raise_server_exceptions=False, client=LOOPBACK_PEER) as c:
        yield c
    m.reset_store()


def _all_refused(c: TestClient, needle: str) -> None:
    for u in STORE_ROUTES:
        r = c.get(u)
        assert r.status_code == 503, (u, r.status_code)
        b = r.json()
        assert b["code"] == "export_refused" and needle in b["detail"], (u, b)
        assert "Traceback" not in r.text and "/Users/" not in r.text and "/private/" not in r.text


def test_wrong_sha_mid_session_is_refused_then_recovers(api: TestClient, export_dir: Path) -> None:
    assert api.get("/api/meta").json()["mode"] == "export"
    mp = export_dir / "manifest.json"
    good = mp.read_text()
    bad = json.loads(good)
    bad["files"]["export.duckdb"]["sha256"] = "f" * 64
    mp.write_text(json.dumps(bad, indent=2))  # a different size, so the API notices
    _all_refused(api, "sha256")
    # the live routes never wait on the store, so they keep answering
    assert api.get("/api/live/2026-11-09").status_code == 200
    mp.write_text(good)
    time.sleep(0.01)
    seal(export_dir)  # a fresh, correct manifest: picked up without a restart
    assert api.get("/api/meta").json()["mode"] == "export"


def test_export_removed_mid_session_falls_back_to_the_labelled_replay(
    api: TestClient, export_dir: Path, tmp_path: Path
) -> None:
    """Pinned current behaviour (see the lane report): a configured directory that loses its
    manifest serves the committed replay fixtures, labelled as such, not a 503."""
    assert api.get("/api/meta").json()["mode"] == "export"
    moved = tmp_path / "moved"
    shutil.move(str(export_dir), str(moved))
    r = api.get("/api/meta")
    assert r.status_code == 200 and r.json()["mode"] == "fixture_replay"
    assert "replay" in r.json()["mode_note"].lower()
    shutil.move(str(moved), str(export_dir))
    assert api.get("/api/meta").json()["mode"] == "export"


def test_an_unopenable_database_is_a_503_not_a_crash(api: TestClient, export_dir: Path) -> None:
    (export_dir / "export.duckdb").write_bytes(b"not a duckdb file at all" * 100)
    seal(export_dir)  # the checksum now matches the damaged file
    _all_refused(api, "export.duckdb cannot be opened")


def test_a_database_missing_a_table_is_a_503(api: TestClient, export_dir: Path) -> None:
    import duckdb

    con = duckdb.connect(str(export_dir / "export.duckdb"))
    con.execute("DROP TABLE players")
    con.execute("CHECKPOINT")
    con.close()
    seal(export_dir)
    _all_refused(api, "export.duckdb could not be read")


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '{"n_evaluated": "lots"}'])
def test_an_unreadable_parlay_file_is_a_503(
    api: TestClient, export_dir: Path, content: str
) -> None:
    (export_dir / "parlay_of_day.json").write_text(content)
    seal(export_dir)
    _all_refused(api, "parlay_of_day.json is not a readable parlay file")


def test_a_half_written_manifest_is_refused(api: TestClient, export_dir: Path) -> None:
    mp = export_dir / "manifest.json"
    mp.write_text(mp.read_text()[:40])
    _all_refused(api, "manifest.json is not readable JSON")


def test_a_slow_store_does_not_hold_up_the_live_routes(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_EXPORT_DIR", str(export_dir))
    monkeypatch.setenv("APP_LIVE_DIR", str(FIX / "live"))
    real = ExportStore
    gate = threading.Event()

    def slow(*a: Any, **k: Any) -> Any:
        gate.wait(10)
        return real(export_dir)

    monkeypatch.setattr("parlayhitters_api.store.ExportStore", slow)
    m.reset_store()
    with TestClient(m.create_app(), raise_server_exceptions=False) as c:
        got: list[int] = []
        t = threading.Thread(target=lambda: got.append(c.get("/api/meta").status_code))
        t.start()
        time.sleep(0.2)
        t0 = time.perf_counter()
        assert c.get("/api/live/2026-11-09").status_code == 200
        assert time.perf_counter() - t0 < 2.0
        gate.set()
        t.join(15)
        assert got == [200]
    m.reset_store()


def test_export_refused_is_the_only_store_failure_type() -> None:
    assert issubclass(ExportRefused, ValueError)
