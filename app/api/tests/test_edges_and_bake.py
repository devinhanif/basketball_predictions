from __future__ import annotations

import base64
import gzip
import json
from typing import Any

from fastapi.testclient import TestClient

from parlayhitters_api.bake_artifact import FORMAT, bake
from parlayhitters_api.edges import leg_edges


def _rec(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "legs": [{"game_id": "G1", "player_id": 7, "stat": "ast", "side": "no", "threshold": 6.0}],
        "side": "no",
        "price": 0.99,
        "fees": 0.01,
        "ev": -0.08,
        "ev_low": -0.09,
        "model_prob": 0.92,
        "prob_interval": [0.92, 0.92],
        "title": "A B: 6+ assists",
        "ticker": "K-1",
        "order_type": "taker",
        "price_ts": "2026-11-08 16:20:20",
    }
    return base | over


def test_leg_edges_selects_and_copies_stored_fields() -> None:
    rows = leg_edges(
        [_rec(), _rec(legs=[{"game_id": "G1", "player_id": 8, "stat": "ast", "threshold": 6.0}])],
        "G1",
        7,
        "ast",
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["side"] == "under" and r["threshold"] == 6.0
    # the arithmetic on screen is stored fields only: model - ask - fee == the engine's own edge
    assert abs(r["model_prob"] - r["price"] - r["fees"] - r["ev"]) < 1e-9


def test_leg_edges_drops_malformed_rows() -> None:
    bad = [
        _rec(price="x"),
        _rec(ev=float("nan")),
        _rec(side="maybe", legs=[{"game_id": "G1", "player_id": 7, "stat": "ast", "threshold": 1}]),
        _rec(legs=[{"game_id": "G1", "player_id": 7, "stat": "ast"}, {"x": 1}]),
    ]
    assert leg_edges(bad, "G1", 7, "ast") == []


def test_edges_endpoint(export_client: TestClient) -> None:
    r = export_client.get("/api/edges/G1/7/ast")
    assert r.status_code == 200
    assert r.json()["contracts"] == []
    assert export_client.get("/api/edges/G1/7/pra").status_code == 404


def test_bake_dumps_what_the_pages_ask_for(export_client: TestClient) -> None:
    baked = bake(export_client, n_dates=1, players_per_team=1)
    assert baked["format"] == FORMAT
    assert len(baked["dates"]) == 1
    d = baked["dates"][0]
    assert baked["simple"][f"/api/slates/{d}"]["s"] == 200
    assert baked["simple"]["/api/meta"]["b"]["slates"] == [d]
    assert baked["data_as_of"]
    game_shards = [k for k in baked["shards"] if k.startswith("g:")]
    assert game_shards
    entries = json.loads(gzip.decompress(base64.b64decode(baked["shards"][game_shards[0]])))
    gid = game_shards[0][2:]
    assert entries[f"/api/games/{gid}"]["s"] == 200
    assert any(
        p.startswith(f"/api/breakdown/{gid}/") and p.endswith("?side=under") for p in entries
    )
