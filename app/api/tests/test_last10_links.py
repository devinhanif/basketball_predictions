"""A last-10 game the app links to must open; one the data lacks must not be a link."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api.main import create_app, reset_store

REAL = Path(__file__).resolve().parents[2] / "fixtures" / "export_best_real"
PLAYERS = 6


def _check(c: TestClient) -> tuple[int, int]:
    linked = unlinked = 0
    for s in c.get("/api/slates").json():
        for g in c.get(f"/api/slates/{s['date']}").json()["games"][:3]:
            gid = g["game_id"]
            for pl in c.get(f"/api/games/{gid}").json()["players"][:PLAYERS]:
                for stat in pl["props"]:
                    b = c.get(f"/api/breakdown/{gid}/{pl['player_id']}/{stat}").json()
                    card = c.get(f"/api/card/{gid}/{pl['player_id']}/{stat}").json()
                    dots = card["stats"]["last10_games"] if card["stats"] else []
                    assert [d["game_id"] for d in dots] == [x["game_id"] for x in b["chips"]]
                    items = [(x["game_id"], x["linkable"]) for x in b["chips"]]
                    items += [(d["game_id"], d["linkable"]) for d in dots]
                    for game_id, linkable in items:
                        r = c.get(f"/api/games/{game_id}")
                        if linkable:
                            assert r.status_code == 200, game_id  # a link must open
                            linked += 1
                        else:
                            assert r.status_code == 404, game_id  # no link where there is no page
                            unlinked += 1
    return linked, unlinked


def test_fixture_last10_links(client: TestClient) -> None:
    linked, unlinked = _check(client)
    assert linked + unlinked > 100


def test_export_last10_links(export_client: TestClient) -> None:
    linked, unlinked = _check(export_client)
    assert linked + unlinked > 50


@pytest.fixture
def real_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_EXPORT_DIR", str(REAL))
    reset_store()
    with TestClient(create_app()) as c:
        yield c
    reset_store()


def test_real_hindsight_export_last10_links(real_client: TestClient) -> None:
    linked, unlinked = _check(real_client)
    assert linked + unlinked > 100
    assert unlinked > 0  # older games are not in the export: shown, not linked
