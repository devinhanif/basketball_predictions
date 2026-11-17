"""The error registry route, the health strip's added keys, and two sweeps over every honesty route
(a refused export is a typed 503; no payload carries a money field)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from honesty_helpers import MONEY_KEYS, a_prop, db_rows, export_api, keys_of

from parlayhitters_api.errors import REGISTRY_PATH

SRC = Path(__file__).resolve().parents[1] / "src" / "parlayhitters_api"


# --------------------------------------------------------------------------- errors, health


def test_errors_route_is_the_registry_file(client: TestClient) -> None:
    body = client.get("/api/errors").json()
    data = json.loads(REGISTRY_PATH.read_text())
    assert body["codes"] == data["codes"] and body["version"] == data["version"]
    served = {c["code"]: c for c in body["codes"]}
    used: set[str] = set()
    for f in SRC.rglob("*.py"):
        used |= set(re.findall(r'(?:ApiError|error_response)\(\s*"([a-z_]+)"', f.read_text()))
    used |= {"not_found", "method_not_allowed", "bad_request", "internal_error"}
    assert used <= set(served), used - set(served)
    assert all(served[c]["side"] == "api" and served[c]["status"] for c in used)


ADDED = {"newest_quote", "stale_marks", "strip_missing"}


def test_fixture_health_adds_keys_and_changes_none(client: TestClient) -> None:
    fx = client.get("/api/health").json()
    assert ADDED <= set(fx) and fx["newest_quote"] is None and fx["strip_missing"]
    assert {"status", "mode", "data_made_at", "data_age_s", "data_version", "export"} <= set(fx)


def test_export_health_adds_stored_facts(export_client: TestClient, export_dir: Path) -> None:
    h = export_client.get("/api/health").json()
    assert ADDED <= set(h)
    want = dict(db_rows(export_dir, "SELECT book, max(snapshot_ts) FROM prop_lines GROUP BY 1"))
    got = h["newest_quote"]["by_book"]
    assert set(got) == set(want)
    assert all(got[b] == want[b].isoformat(timespec="seconds") + "Z" for b in want)
    assert h["newest_quote"]["label"] == "newest quote in this export"
    assert h["newest_quote"]["pinnacle"] is None
    assert any(m.startswith("Pinnacle") for m in h["strip_missing"])
    assert h["stale_marks"]["primary_fallback_stale"] == 24  # manifest primary_sources


# --------------------------------------------------------------------------- safety


def test_new_routes_refuse_a_bad_export(export_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    m = json.loads((export_dir / "manifest.json").read_text())
    m["files"]["export.duckdb"]["sha256"] = "0" * 64
    (export_dir / "manifest.json").write_text(json.dumps(m))
    for c in export_api(export_dir, monkeypatch):
        for u in ("/api/record/0022400023", "/api/record/0022400023/990001/pts",
                  "/api/distribution/0022400023/990001/pts", "/api/scorecard",
                  "/api/forward-tests", "/api/parlay-diagnostics/2024-11-12"):  # fmt: skip
            r = c.get(u)
            assert r.status_code == 503 and r.json()["code"] == "export_refused", u
        assert c.get("/api/errors").status_code == 200


def test_new_routes_carry_no_money(planted: TestClient) -> None:
    gid, pid, st = a_prop(planted, final=False)
    for u in (f"/api/record/{gid}/{pid}/{st}", f"/api/record/{gid}",
              f"/api/distribution/{gid}/{pid}/{st}", "/api/scorecard", "/api/forward-tests",
              "/api/parlay-diagnostics/2024-11-12"):  # fmt: skip
        r = planted.get(u)
        assert r.status_code == 200, u
        assert not (keys_of(r.json()) & MONEY_KEYS), u
