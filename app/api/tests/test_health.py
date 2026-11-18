"""/api/health keeps its two keys and adds the data's age and the manifest's own freshness facts."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api.export_store import ExportStore
from parlayhitters_api.health import health_detail
from parlayhitters_api.main import create_app, reset_store


def test_fixture_health_has_no_age(client: TestClient) -> None:
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["mode"] == "fixture_replay"
    assert h["data_age_s"] is None and h["export"] is None and h["data_made_at"]


def test_export_health_copies_the_manifest(export_client: TestClient, export_dir: Path) -> None:
    m = json.loads((export_dir / "manifest.json").read_text())
    h = export_client.get("/api/health").json()
    assert h["status"] == "ok" and h["mode"] == "export"
    assert h["data_made_at"] == m["made_at"] and h["data_version"] == m["data_version"]
    assert h["data_age_s"] is not None and h["data_age_s"] > 0
    e = h["export"]
    assert e["schema_version"] == m["schema_version"] and e["row_counts"] == m["row_counts"]
    assert e["lines"]["status"] == m["lines"]["status"]
    assert len(e["checks_passed"]) == 4


def test_age_is_now_minus_made_at(export_dir: Path) -> None:
    s = ExportStore(export_dir)
    made = datetime.fromisoformat(s.made_at.replace("Z", "+00:00"))
    d = health_detail(s, now=made + timedelta(seconds=90))
    assert d["data_age_s"] == 90.0


def test_a_refused_export_makes_health_a_typed_503(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (export_dir / "parlay_of_day.json").write_text("{}")  # no longer matches its sha256
    monkeypatch.setenv("APP_EXPORT_DIR", str(export_dir))
    reset_store()
    with TestClient(create_app()) as c:
        r = c.get("/api/health")
    reset_store()
    assert r.status_code == 503
    b = r.json()
    assert b["code"] == "export_refused" and "parlay_of_day.json" in b["detail"]


def test_naive_made_at_has_no_age() -> None:
    class S:
        mode = "export"
        manifest = {"made_at": "2024-11-12T10:00:00"}

    assert health_detail(S(), now=datetime.now(UTC))["data_age_s"] is None
