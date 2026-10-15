"""Every route a button or link in the app opens must answer, on the fixtures and on exports.

The web app builds these URLs: the game page's "Full breakdown" button, the stat tabs and the
over/under switch on the leg page, a parlay ticket's "Open this leg's breakdown", the swipe card's
stats and slider, "Show the exact details" (parlay-of-the-day) and the night tabs.
A dead one fails here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import edit_db
from fastapi.testclient import TestClient

from parlayhitters_api.main import create_app, reset_store

PLAYERS_PER_GAME = 4
GAMES_PER_SLATE = 3


def _walk(c: TestClient, expect_history: bool) -> int:
    checked = 0
    slates = c.get("/api/slates").json()
    assert slates
    assert c.get("/api/parlay-of-the-day").status_code == 200
    assert c.get("/api/meta").status_code == 200
    for s in slates:
        d = s["date"]
        assert c.get(f"/api/slates/{d}").status_code == 200
        assert c.get(f"/api/slates/{d}/swipe", params={"seed": 1, "n": 5}).status_code == 200
        assert c.get(f"/api/slates/{d}/results").status_code == 200
        assert c.get(f"/api/top-parlays/{d}").status_code in (200, 404)  # 404 = no file, a state
        for g in c.get(f"/api/slates/{d}").json()["games"][:GAMES_PER_SLATE]:
            gid = g["game_id"]
            game = c.get(f"/api/games/{gid}")
            assert game.status_code == 200
            assert c.get(f"/api/games/{gid}/result").status_code in (200, 404)
            for pl in game.json()["players"][:PLAYERS_PER_GAME]:
                for stat in pl["props"]:
                    for side in ("over", "under"):
                        r = c.get(
                            f"/api/breakdown/{gid}/{pl['player_id']}/{stat}", params={"side": side}
                        )
                        assert r.status_code == 200, (gid, pl["player_id"], stat, side, r.text)
                        b = r.json()
                        if not expect_history:
                            assert b["prior_games"] == 0
                        for t in b["tabs"]:  # the stat strip on the leg page
                            rt = c.get(
                                f"/api/breakdown/{gid}/{pl['player_id']}/{t['stat']}",
                                params={"side": side},
                            )
                            assert rt.status_code == 200, (t["stat"], rt.text)
                            checked += 1
                    card = c.get(f"/api/card/{gid}/{pl['player_id']}/{stat}")
                    assert card.status_code == 200, card.text
    return checked


def test_every_link_answers_on_the_fixtures(client: TestClient) -> None:
    assert _walk(client, expect_history=True) > 100


def test_every_link_answers_on_the_small_export(export_client: TestClient) -> None:
    assert _walk(export_client, expect_history=True) > 50


def test_every_link_answers_with_no_game_history(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A preseason export holds no earlier box scores: the leg page must still open."""
    edit_db(export_dir, "DELETE FROM box_scores WHERE game_date < '2024-11-12'")
    monkeypatch.setenv("APP_EXPORT_DIR", str(export_dir))
    reset_store()
    with TestClient(create_app()) as c:
        assert _walk(c, expect_history=False) > 20
    reset_store()


def test_unknown_leg_is_a_clean_404(client: TestClient) -> None:
    assert client.get("/api/breakdown/nope/1/pts").status_code == 404
    assert client.get("/api/breakdown/0022400001/1/pts").status_code == 404
