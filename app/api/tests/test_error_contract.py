"""The error contract shared with the web app: every refusal is
{ok: false, code, text, field, request_id, detail}, codes come from app/error_codes.json, statuses
are what they were before, and every response carries X-Request-Id."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api.errors import REGISTRY, REGISTRY_PATH, ApiError
from parlayhitters_api.main import create_app, reset_store

SRC = Path(__file__).resolve().parents[1] / "src" / "parlayhitters_api"
DATE = "2024-11-12"
GID = "0022400001"
PID = 1628369
KEYS = {"ok", "code", "text", "field", "request_id", "detail"}


@pytest.fixture
def lenient() -> Iterator[TestClient]:
    """A client that sees a 500 as the browser would, instead of re-raising it."""
    reset_store()
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c
    reset_store()


def _refusal(r: object) -> dict[str, object]:
    body = r.json()  # type: ignore[attr-defined]
    assert isinstance(body, dict)
    assert set(body) == KEYS, body
    assert body["ok"] is False
    assert body["code"] in REGISTRY
    assert isinstance(body["text"], str) and body["text"]
    assert body["request_id"] == r.headers["x-request-id"]  # type: ignore[attr-defined]
    assert r.status_code == REGISTRY[str(body["code"])]["status"]  # type: ignore[attr-defined]
    return body


def test_registry_shape_is_kept() -> None:
    data = json.loads(REGISTRY_PATH.read_text())
    assert data["version"] == 1
    seen = set()
    for c in data["codes"]:
        assert set(c) == {"code", "status", "side", "title", "what", "why", "todo"}, c
        assert c["side"] in ("api", "web")
        assert (c["status"] is None) == (c["side"] == "web")
        assert c["code"] not in seen
        seen.add(c["code"])


def test_every_code_raised_in_the_source_is_registered() -> None:
    used: set[str] = set()
    for f in SRC.rglob("*.py"):
        used |= set(re.findall(r'ApiError\(\s*"([a-z_]+)"', f.read_text()))
        used |= set(re.findall(r'error_response\(\s*"([a-z_]+)"', f.read_text()))
        assert "HTTPException(" not in f.read_text() or f.name == "errors.py", f
    assert used, "no codes found"
    assert used <= set(REGISTRY), used - set(REGISTRY)


def test_an_unknown_code_cannot_be_raised() -> None:
    with pytest.raises(ValueError, match="not in"):
        ApiError("no_such_code_anywhere")


def test_success_carries_a_request_id(client: TestClient) -> None:
    a = client.get("/api/health")
    b = client.get("/api/health")
    assert a.status_code == 200
    ra, rb = a.headers["x-request-id"], b.headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{16}", ra) and ra != rb


@pytest.mark.parametrize(
    ("url", "status", "code", "field", "detail"),
    [
        ("/api/slates/2023-01-01", 404, "unknown_slate", "date", "no such slate"),
        ("/api/games/0029999999", 404, "unknown_game", "game_id", "no such game"),
        (f"/api/card/{GID}/1/pts", 404, "no_forecast", "player_id", None),
        (f"/api/card/{GID}/{PID}/stl", 404, "unknown_stat", "stat", "unknown stat"),
        (f"/api/props/{GID}/{PID}/pts?line=-1", 422, "bad_request", "line", None),
        (f"/api/props/{GID}/abc/pts?line=1", 422, "bad_request", "player_id", None),
        (f"/api/breakdown/{GID}/{PID}/pts?side=sideways", 422, "bad_request", "side", None),
        (f"/api/breakdown/{GID}/1/pts", 404, "unknown_player", "player_id", None),
        (f"/api/track-record/guess/{GID}/{PID}/stl", 404, "no_posted_line", "stat", None),
        ("/api/track-record/guess?n=0", 422, "bad_request", "n", None),
        ("/api/top-parlays/2024-11-12", 404, "no_top_parlays", "date", None),
        ("/api/live/401909952x/snapshot", 404, "unknown_game", "game_id", "unknown game"),
        ("/api/nothing-here", 404, "not_found", None, "Not Found"),
    ],
)
def test_route_refusals_follow_the_contract(
    client: TestClient,
    url: str,
    status: int,
    code: str,
    field: str | None,
    detail: str | None,
) -> None:
    r = client.get(url)
    assert r.status_code == status
    body = _refusal(r)
    assert body["code"] == code and body["field"] == field
    if detail is not None:
        assert body["detail"] == detail


def test_details_keep_the_words_routes_used_before(client: TestClient) -> None:
    """The current web client reads `detail`; the old strings must still be there."""
    assert client.get(f"/api/card/{GID}/1/pts").json()["detail"] == (
        "no forecast for that player and stat"
    )
    assert client.get("/api/top-parlays/2024-11-12").json()["detail"] == (
        "no top parlays file for 2024-11-12"
    )
    r = client.get(f"/api/track-record/guess/{GID}/{PID}/stl")
    assert r.json()["detail"] == "no finished prop with a posted line for that player and stat"


def test_validation_refusal_does_not_echo_the_input(client: TestClient) -> None:
    junk = "<script>" + "x" * 500
    r = client.get(f"/api/props/{GID}/{PID}/pts", params={"line": junk})
    body = _refusal(r)
    assert body["field"] == "line"
    assert "script" not in r.text and "xxxx" not in r.text


def test_method_not_allowed_is_typed_and_keeps_allow(client: TestClient) -> None:
    r = client.post("/api/health")
    assert r.status_code == 405
    body = _refusal(r)
    assert body["code"] == "method_not_allowed"
    assert r.headers.get("allow") == "GET"


def test_export_refused_is_a_typed_503(export_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    m = json.loads((export_dir / "manifest.json").read_text())
    m["files"]["export.duckdb"]["sha256"] = "0" * 64
    (export_dir / "manifest.json").write_text(json.dumps(m))
    monkeypatch.setenv("APP_EXPORT_DIR", str(export_dir))
    reset_store()
    with TestClient(create_app()) as c:
        r = c.get(f"/api/slates/{DATE}")
    reset_store()
    assert r.status_code == 503
    body = _refusal(r)
    assert body["code"] == "export_refused"
    assert str(body["detail"]).startswith("the data export was refused: ")
    assert "sha256" in str(body["detail"])


def test_an_unexpected_error_is_a_plain_500_with_its_id(
    lenient: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    app = lenient.app

    @app.get("/api/__boom")  # type: ignore[attr-defined, untyped-decorator]
    def boom() -> None:
        raise RuntimeError("secret /Users/someone/path in the message")

    with caplog.at_level(logging.INFO, logger="parlayhitters.api"):
        r = lenient.get("/api/__boom")
    assert r.status_code == 500
    body = _refusal(r)
    assert body["code"] == "internal_error"
    assert "secret" not in r.text and "Traceback" not in r.text and "/Users" not in r.text
    assert r.headers["x-content-type-options"] == "nosniff"
    rid = str(body["request_id"])
    assert any(rid in rec.getMessage() for rec in caplog.records)


def test_each_request_is_logged_with_its_id(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="parlayhitters.api"):
        r = client.get("/api/slates/1999-01-01")
    rid = r.headers["x-request-id"]
    lines = [rec.getMessage() for rec in caplog.records if rid in rec.getMessage()]
    assert lines and "status=404" in lines[-1] and "path=/api/slates/1999-01-01" in lines[-1]


def test_a_hostile_path_cannot_split_the_log_line(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="parlayhitters.api"):
        client.get("/api/games/a%0D%0Afake%20line")
    assert all("\n" not in rec.getMessage() for rec in caplog.records)
