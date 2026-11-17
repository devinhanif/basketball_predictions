"""Contract tests for every route: GET only, every route fuzzed, response shapes against the models
and docs/contracts, and the edge states (empty slate, stale export, missing export, sha mismatch,
price-free checkpoint, top-parlays raw chances never served)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import EXPORT_DAY, edit_db, seal
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from hostile_gen import ROUTES

from parlayhitters_api.main import create_app, reset_store
from parlayhitters_api.models import SlateView, SwipeDeck

FIX = Path(__file__).resolve().parents[2] / "fixtures"


def _routes() -> list[APIRoute]:
    return [r for r in create_app().routes if isinstance(r, APIRoute)]


def test_every_route_is_get_only() -> None:
    for r in _routes():
        assert r.methods == {"GET"}, (r.path, r.methods)


def test_every_route_but_the_stream_is_in_the_hostile_generator() -> None:
    fuzzed = {
        t.replace("{live_date}", "{date}").replace("{event_id}", "{game_id}") for t, *_ in ROUTES
    }
    served = {r.path for r in _routes()} - {"/api/live/{game_id}/stream"}
    assert served <= fuzzed, served - fuzzed


def test_cors_answers_only_get_from_local_origins(client: TestClient) -> None:
    pre = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert pre.status_code == 400  # Starlette's own preflight refusal (plain text, see report)
    ok = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert ok.status_code == 200 and ok.headers["access-control-allow-methods"] == "GET"
    assert "access-control-allow-credentials" not in ok.headers


def test_untyped_routes_keep_their_keys(client: TestClient) -> None:
    """Routes without a response model: the keys the web client reads are pinned here."""
    gid = "0022400001"
    shapes = {
        "/api/meta": {"mode", "mode_note", "data_version", "built_at", "data_as_of", "export_date",
                      "max_season", "slates", "stubs", "sources", "queries", "export"},
        f"/api/games/{gid}/result": {"game_id", "label", "home_pts", "away_pts", "home_won",
                                     "players"},
        "/api/slates/2024-11-12/results": {"date", "label", "rows"},
        f"/api/edges/{gid}/1628369/pts": {"report_date", "contracts", "note"},
        "/api/live/2026-11-09": {"date", "games", "server_now", "attribution", "delay_note"},
    }  # fmt: skip
    for url, keys in shapes.items():
        r = client.get(url)
        assert r.status_code == 200, url
        optional = {"note"} if url.startswith("/api/live/") else set()  # "no capture" note
        assert keys <= set(r.json()) <= keys | optional, (url, set(r.json()) ^ keys)
    rows = client.get("/api/slates").json()
    assert rows and all(set(x) == {"date", "label", "games", "props", "finished"} for x in rows)


def test_results_never_carry_a_model_chance(client: TestClient, export_client: TestClient) -> None:
    """LINE_CAL_LIVE display lock: outcomes are served with the book's chance only."""
    for c, d in ((client, "2024-11-12"), (export_client, EXPORT_DAY)):
        for row in c.get(f"/api/slates/{d}/results").json()["rows"]:
            assert not any(k.startswith("model") or "cal" in k for k in row), row


def test_top_parlays_never_serves_raw_chances(client: TestClient) -> None:
    for d in ("2024-01-01", "2024-01-02"):
        r = client.get(f"/api/top-parlays/{d}")
        assert r.status_code == 200
        text = r.text
        assert '"p_raw"' not in text and '"p_joint_raw"' not in text


def test_checkpoint_copies_only_the_contract_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snap = {
        "version": 1,
        "games": {
            "401909952": {
                "checkpoint": 25, "e_used": 24.5, "support": "s", "period": 2, "clock": "5:00",
                "known_at": "2026-11-10T01:00:00Z", "ask": 55, "edge": 0.1,
                "rows": [{"player": "A", "stat": "pts", "k": 20, "p_model": 0.5, "cur": 10,
                          "minutes": 15, "fouls": 1, "price": 48, "ask": 50, "bid": 46,
                          "edge_cents": 3}],
            }
        },
    }  # fmt: skip
    f = tmp_path / "snap.json"
    f.write_text(json.dumps(snap))
    monkeypatch.setenv("APP_CHECKPOINT_SNAPSHOT", str(f))
    monkeypatch.setenv("APP_LIVE_DIR", str(tmp_path / "no-capture"))
    with TestClient(create_app()) as c:
        body = c.get("/api/live/401909952/checkpoint").json()
    assert body["available"] is True
    assert "ask" not in body and "edge" not in body
    assert set(body["rows"][0]) == {"player", "stat", "k", "p_model", "cur", "minutes", "fouls"}


def test_an_empty_slate_is_a_first_class_answer(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A night whose games carry no forecasts: the slate still lists the games, the swipe deck
    says why it is empty, and results are an empty list; nothing is invented."""
    edit_db(export_dir, "DELETE FROM prop_grids", "DELETE FROM prop_lines", "DELETE FROM p_play")
    monkeypatch.setenv("APP_EXPORT_DIR", str(export_dir))
    reset_store()
    with TestClient(create_app()) as c:
        s = c.get(f"/api/slates/{EXPORT_DAY}")
        assert s.status_code == 200
        slate = SlateView.model_validate(s.json())
        assert slate.games and slate.counts["props"] == 0
        assert all(g.key_props == [] for g in slate.games)
        deck = SwipeDeck.model_validate(c.get(f"/api/slates/{EXPORT_DAY}/swipe").json())
        assert deck.cards == [] and deck.reason in ("no_lines", "all_started") and deck.note
        assert c.get(f"/api/slates/{EXPORT_DAY}/results").json()["rows"] == []
    reset_store()


def test_a_stale_export_is_served_with_its_age(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old data is shown with how old it is (the web app labels it), not refused."""
    m = json.loads((export_dir / "manifest.json").read_text())
    made = datetime.fromisoformat(m["made_at"].replace("Z", "+00:00"))
    monkeypatch.setenv("APP_EXPORT_DIR", str(export_dir))
    reset_store()
    with TestClient(create_app()) as c:
        h = c.get("/api/health").json()
        assert abs(h["data_age_s"] - (datetime.now(UTC) - made).total_seconds()) < 5
        assert c.get(f"/api/slates/{EXPORT_DAY}").status_code == 200
    reset_store()


def test_a_missing_export_directory_serves_the_labelled_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_EXPORT_DIR", str(tmp_path / "never-built"))
    reset_store()
    with TestClient(create_app()) as c:
        meta = c.get("/api/meta").json()
    reset_store()
    assert meta["mode"] == "fixture_replay" and meta["export"] is None


def test_a_sha_mismatch_is_503_on_every_store_route(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seal(export_dir)
    m = json.loads((export_dir / "manifest.json").read_text())
    m["files"]["parlay_of_day.json"]["sha256"] = "0" * 64
    (export_dir / "manifest.json").write_text(json.dumps(m))
    monkeypatch.setenv("APP_EXPORT_DIR", str(export_dir))
    reset_store()
    with TestClient(create_app()) as c:
        for r in _routes():
            if "{" in r.path or not r.path.startswith("/api/") or "/live/" in r.path:
                continue
            if r.path in ("/api/desk", "/api/docs", "/api/openapi.json", "/api/errors"):
                continue  # not backed by the export store (the error help must work when refused)
            got = c.get(r.path)
            assert got.status_code == 503 and got.json()["code"] == "export_refused", r.path
    reset_store()
