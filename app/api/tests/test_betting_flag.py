"""The betting switch on the API (features.py, the betting package): read at app creation, off by
default; with it off /api/betting/* is the ordinary typed 404 and nothing else changes; with it on
one GET route answers and the service stays read-only. Hostile input on the new paths in both
states."""

from __future__ import annotations

import json
import random
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from conftest import seal
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from hostile_gen import BETTING_ROUTES, FIXED_BAD, random_values

from parlayhitters_api import features
from parlayhitters_api.errors import REGISTRY
from parlayhitters_api.main import create_app, reset_store

FIX = Path(__file__).resolve().parents[2] / "fixtures"
KEYS = {"ok", "code", "text", "field", "request_id", "detail"}
STATUS_BODY = {
    "enabled": True,
    "note": "analysis only; no orders are placed; this service is read-only",
}


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_EXPORT_DIR", raising=False)
    monkeypatch.delenv("PH_PRIVATE", raising=False)
    monkeypatch.setenv("APP_LIVE_DIR", str(FIX / "live"))
    monkeypatch.setenv("APP_DESK_SHARED_FILE", str(FIX / "desk_fixture_shared.json"))
    monkeypatch.setenv("APP_CHECKPOINT_SNAPSHOT", str(FIX / "no_checkpoint_snapshot.json"))


def _client(monkeypatch: pytest.MonkeyPatch, betting: bool) -> Iterator[TestClient]:
    _env(monkeypatch)
    reset_store()
    with TestClient(create_app(betting=betting), raise_server_exceptions=False) as c:
        yield c
    reset_store()


@pytest.fixture
def off_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    yield from _client(monkeypatch, False)


@pytest.fixture
def on_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    yield from _client(monkeypatch, True)


def _paths(betting: bool | None) -> set[str]:
    return {r.path for r in create_app(betting=betting).routes if isinstance(r, APIRoute)}


def _typed(r: Any, status: int, code: str) -> None:
    body = r.json()
    assert r.status_code == status, (r.status_code, body)
    assert set(body) == KEYS and body["ok"] is False and body["code"] == code, body
    assert REGISTRY[code]["status"] == status
    assert body["request_id"] == r.headers.get("x-request-id")


# ---------------------------------------------------------------- reading the switch


@pytest.mark.parametrize(
    ("value", "on"),
    [
        (None, False),
        ("1", True),
        ("0", False),
        ("", False),
        ("true", False),
        ("yes", False),
        (" 1", False),
        ("1 ", False),
        ("01", False),
    ],
)
def test_the_switch_is_on_only_for_exactly_1(
    monkeypatch: pytest.MonkeyPatch, value: str | None, on: bool
) -> None:
    if value is None:
        monkeypatch.delenv(features.BETTING_ENV, raising=False)
    else:
        monkeypatch.setenv(features.BETTING_ENV, value)
    assert features.betting_enabled() is on
    assert ("/api/betting/status" in _paths(None)) is on


def test_the_switch_is_read_at_app_creation_not_per_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env(monkeypatch)
    monkeypatch.setenv(features.BETTING_ENV, "1")
    reset_store()
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        monkeypatch.delenv(features.BETTING_ENV)
        assert c.get("/api/betting/status").json() == STATUS_BODY
    monkeypatch.delenv(features.BETTING_ENV, raising=False)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        monkeypatch.setenv(features.BETTING_ENV, "1")
        _typed(c.get("/api/betting/status"), 404, "not_found")
    reset_store()


def test_the_name_is_the_documented_one() -> None:
    assert features.BETTING_ENV == "PH_BETTING"


# ---------------------------------------------------------------- switch OFF: nothing changes


def test_off_adds_no_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(features.BETTING_ENV, raising=False)
    off = _paths(False)
    assert off == _paths(None)
    assert not any(p.startswith("/api/betting") for p in off)
    assert _paths(True) - off == {"/api/betting/status"}


def test_off_betting_paths_are_the_ordinary_typed_404(off_client: TestClient) -> None:
    for path in ("/api/betting/status", "/api/betting", "/api/betting/", "/api/betting/slip"):
        _typed(off_client.get(path), 404, "not_found")
    # The same answer as any unknown path, so the OFF service does not even hint at the module.
    a = off_client.get("/api/betting/status").json()
    b = off_client.get("/api/no-such-path").json()
    assert {k: v for k, v in a.items() if k != "request_id"} == {
        k: v for k, v in b.items() if k != "request_id"
    }
    _typed(off_client.post("/api/betting/status"), 404, "not_found")


def test_off_openapi_has_no_betting_path(off_client: TestClient) -> None:
    spec = off_client.get("/api/openapi.json").json()
    assert not [p for p in spec["paths"] if "betting" in p]


def test_meta_and_health_gain_nothing_in_either_state(off_client: TestClient) -> None:
    off_meta = set(off_client.get("/api/meta").json())
    off_health = set(off_client.get("/api/health").json())
    with TestClient(create_app(betting=True), raise_server_exceptions=False) as on:
        assert set(on.get("/api/meta").json()) == off_meta
        assert set(on.get("/api/health").json()) == off_health


# ---------------------------------------------------------------- switch ON: one GET route


def test_on_status_route(on_client: TestClient) -> None:
    r = on_client.get("/api/betting/status")
    assert r.status_code == 200
    assert r.json() == STATUS_BODY
    assert r.headers["x-content-type-options"] == "nosniff"


def test_on_every_route_is_still_get_only() -> None:
    for r in create_app(betting=True).routes:
        if isinstance(r, APIRoute):
            assert r.methods == {"GET"}, (r.path, r.methods)


def test_on_writes_are_refused_typed(on_client: TestClient) -> None:
    for method in ("post", "put", "patch", "delete"):
        _typed(getattr(on_client, method)("/api/betting/status"), 405, "method_not_allowed")
    pre = on_client.options(
        "/api/betting/status",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert pre.status_code == 400


def test_on_status_refuses_with_a_refused_export(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails closed: with the export refused (sha mismatch) the betting route is 503 like every
    store route (tests/test_route_contracts.py walks them all in an ON run)."""
    seal(export_dir)
    m = json.loads((export_dir / "manifest.json").read_text())
    m["files"]["parlay_of_day.json"]["sha256"] = "0" * 64
    (export_dir / "manifest.json").write_text(json.dumps(m))
    monkeypatch.setenv("APP_EXPORT_DIR", str(export_dir))
    reset_store()
    with TestClient(create_app(betting=True), raise_server_exceptions=False) as c:
        _typed(c.get("/api/betting/status"), 503, "export_refused")
    reset_store()


def test_on_unregistered_betting_paths_are_typed_404(on_client: TestClient) -> None:
    for path in ("/api/betting", "/api/betting/slip", "/api/betting/status/x"):
        _typed(on_client.get(path), 404, "not_found")


# ---------------------------------------------------------------- hostile input, both states


def _hostile_values() -> list[str]:
    return list(FIXED_BAD) + random_values(random.Random(20261117), 60)  # noqa: S311 - a seeded test draw


@pytest.mark.parametrize("betting", [False, True])
def test_hostile_paths_and_queries_never_crash(
    monkeypatch: pytest.MonkeyPatch, betting: bool
) -> None:
    _env(monkeypatch)
    reset_store()
    problems: list[str] = []
    n = 0
    with TestClient(create_app(betting=betting), raise_server_exceptions=False) as c:
        for template, _, _ in BETTING_ROUTES:
            for v in _hostile_values():
                for url in (
                    f"{template}/{quote(v, safe='')}",
                    f"/api/betting/{quote(v, safe='')}",
                    f"{template}?{quote(v, safe='')}=1",
                    f"{template}?zz={quote(v, safe='')}",
                ):
                    n += 1
                    r = c.get(url)
                    try:
                        body = r.json()
                    except ValueError:
                        problems.append(f"non-JSON {r.status_code} {url[:100]}")
                        continue
                    if r.status_code >= 500:
                        problems.append(f"crash {r.status_code} {url[:100]}")
                    elif r.status_code >= 400:
                        if not (isinstance(body, dict) and set(body) == KEYS):
                            problems.append(f"untyped {r.status_code} {url[:100]}")
                        elif REGISTRY[body["code"]]["status"] != r.status_code:
                            problems.append(f"code/status {body['code']} {url[:100]}")
                    elif not betting or body != STATUS_BODY:
                        problems.append(f"unexpected {r.status_code} {url[:100]}")
    reset_store()
    assert n > 400
    assert not problems, problems[:10]
