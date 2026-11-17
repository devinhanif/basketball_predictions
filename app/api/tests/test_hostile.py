"""Hostile input on every path and query parameter (tests/hostile_gen.py, seeded): no 5xx except
the documented 503s, every refusal typed and naming the bad field, and nothing on disk changed."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hostile_gen import ROUTES, VALID, _url, cases, field_name

from parlayhitters_api.errors import REGISTRY
from parlayhitters_api.main import create_app, reset_store

FIX = Path(__file__).resolve().parents[2] / "fixtures"
#: The 503s the contract documents; anything else at 5xx is a crash.
DOCUMENTED_503 = {"export_refused", "top_parlays_unavailable", "too_many_viewers"}
KEYS = {"ok", "code", "text", "field", "request_id", "detail"}


def _tree_digest(root: Path) -> dict[str, tuple[int, int, str]]:
    out: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(root))] = (
                st.st_size,
                st.st_mtime_ns,
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
    return out


@pytest.fixture
def hostile_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("APP_EXPORT_DIR", raising=False)
    monkeypatch.delenv("PH_PRIVATE", raising=False)
    monkeypatch.setenv("APP_LIVE_DIR", str(FIX / "live"))
    monkeypatch.setenv("APP_DESK_SHARED_FILE", str(FIX / "desk_fixture_shared.json"))
    monkeypatch.setenv("APP_CHECKPOINT_SNAPSHOT", str(FIX / "no_checkpoint_snapshot.json"))
    reset_store()
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c
    reset_store()


def test_valid_requests_of_the_generator_are_accepted(hostile_client: TestClient) -> None:
    """The generator's baseline values are good ones, so a refusal below is the bad value's."""
    for template, path_params, query in ROUTES:
        if template == "/api/top-parlays/{date}":
            continue  # no file for the fixture slate date: a 404 by design
        url = _url(template, {p: VALID[p] for p in path_params}, query)
        assert hostile_client.get(url).status_code == 200, url


def test_hostile_values_never_crash_and_are_refused_by_name(hostile_client: TestClient) -> None:
    before = _tree_digest(FIX)
    tally: Counter[str] = Counter()
    problems: list[str] = []
    for k in cases():
        tally["cases"] += 1
        r = hostile_client.get(k.url)
        try:
            body = r.json()
        except ValueError:
            problems.append(f"non-JSON {r.status_code} {k.url[:120]}")
            continue
        if r.status_code < 400:
            tally["accepted"] += 1
            continue
        tally["refused"] += 1
        if not (isinstance(body, dict) and set(body) == KEYS and body["ok"] is False):
            problems.append(f"untyped {r.status_code} {k.url[:120]}")
            continue
        code = body["code"]
        if r.status_code >= 500 and not (r.status_code == 503 and code in DOCUMENTED_503):
            problems.append(f"crash {r.status_code} {code} {k.url[:120]}")
        if code not in REGISTRY or REGISTRY[code]["status"] != r.status_code:
            problems.append(f"code {code} with status {r.status_code} {k.url[:120]}")
        if body["request_id"] != r.headers.get("x-request-id"):
            problems.append(f"request id mismatch {k.url[:120]}")
        # A refusal names the bad parameter. A value that changed the route itself is not this
        # route's refusal: a '/' in it or an empty segment is the router's own 404 (names nothing),
        # and a "." or ".." segment is resolved by the client before sending (RFC 3986), so the
        # request reaches another route, which names its own field.
        rerouted = k.in_path and k.value in (".", "..")
        if (
            k.param
            and not rerouted
            and code != "not_found"
            and body["field"] != field_name(k.param)
        ):
            problems.append(f"field {body['field']!r} for {k.param} {code} {k.url[:120]}")
    assert tally["cases"] > 5000
    assert not problems, f"{len(problems)} problems, first: {problems[:10]}"
    assert _tree_digest(FIX) == before, "a request changed something on disk"


def test_no_hostile_value_is_echoed_into_a_validation_reply(hostile_client: TestClient) -> None:
    marker = "zq9" * 20
    for template, path_params, query in ROUTES:
        for q in query:
            url = _url(template, {p: VALID[p] for p in path_params}, {**query, q: marker})
            r = hostile_client.get(url)
            if r.status_code >= 400:
                assert marker not in r.text, url


def test_hostile_generator_is_deterministic() -> None:
    a = [c.url for c in cases()]
    b = [c.url for c in cases()]
    assert a == b and len(set(a)) > 4000
    assert json.dumps(a[:50]) == json.dumps(b[:50])
