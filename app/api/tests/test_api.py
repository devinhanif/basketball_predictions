from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api.main import STATS
from parlayhitters_api.store import DEFAULT_FIXTURES, Store

DATE = "2024-11-12"


def test_fixtures_are_pre_2025_everywhere() -> None:
    store = Store()
    assert store.manifest["max_season"] == 2024
    assert store.slates, "no slates loaded"
    for s in store.slates.values():
        assert s["season"] <= 2024
        assert s["date"] < "2025-07-01"
        for g in s["games"]:
            assert g["season"] <= 2024
            assert g["date"] < "2025-07-01"


def test_store_rejects_a_slate_beyond_the_season_cap(tmp_path: Path) -> None:
    for name in ("manifest.json", "evidence.json", "parlay_of_day.json"):
        (tmp_path / name).write_text((DEFAULT_FIXTURES / name).read_text())
    (tmp_path / "slates").mkdir()
    bad = json.loads((DEFAULT_FIXTURES / "slates" / f"{DATE}.json").read_text())
    bad["season"] = 2025
    (tmp_path / "slates" / f"{DATE}.json").write_text(json.dumps(bad))
    m = json.loads((tmp_path / "manifest.json").read_text())
    m["slates"] = [s for s in m["slates"] if s["date"] == DATE]
    (tmp_path / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(ValueError, match="season cap"):
        Store(tmp_path)


def test_health_and_meta(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok", "mode": "fixture_replay"}
    meta = client.get("/api/meta").json()
    assert meta["data_version"] == Store().manifest["source_data_version"]
    assert DATE in meta["slates"]


def test_security_headers(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"


def test_slate_lists_games_with_win_probabilities(client: TestClient) -> None:
    s = client.get(f"/api/slates/{DATE}").json()
    assert len(s["games"]) == 8
    for g in s["games"]:
        wp = g["win_probability"]
        assert 0 < wp["model_home"] < 1
        assert 0 < wp["market_home"] < 1
        assert 1 <= len(g["key_props"]) <= 3
        assert "result" not in g, "slate view must not leak the outcome"


def test_unknown_slate_and_game_are_404(client: TestClient) -> None:
    assert client.get("/api/slates/2025-11-12").status_code == 404
    assert client.get("/api/games/nope").status_code == 404


def test_game_detail_has_no_outcomes(client: TestClient) -> None:
    gid = client.get(f"/api/slates/{DATE}").json()["games"][0]["game_id"]
    body = client.get(f"/api/games/{gid}").json()
    text = json.dumps(body)
    assert '"actual"' not in text and '"minutes"' not in text
    assert body["players"]
    prop = next(iter(body["players"][0]["props"].values()))
    assert len(prop["q"]) == len(prop["taus"]) == 19
    assert prop["model_p_over"] + prop["model_p_under"] + prop["model_p_push"] == pytest.approx(1)


def test_result_endpoint_reports_the_score(client: TestClient) -> None:
    gid = client.get(f"/api/slates/{DATE}").json()["games"][0]["game_id"]
    r = client.get(f"/api/games/{gid}/result").json()
    assert isinstance(r["home_pts"], int) and isinstance(r["away_pts"], int)
    assert r["home_won"] == (r["home_pts"] > r["away_pts"])


def _first_prop(client: TestClient) -> tuple[str, int, str, dict[str, Any]]:
    gid = client.get(f"/api/slates/{DATE}").json()["games"][0]["game_id"]
    pl = client.get(f"/api/games/{gid}").json()["players"][0]
    stat = next(iter(pl["props"]))
    return gid, pl["player_id"], stat, pl["props"][stat]


def test_prop_probability_matches_the_stored_line(client: TestClient) -> None:
    gid, pid, stat, prop = _first_prop(client)
    r = client.get(f"/api/props/{gid}/{pid}/{stat}", params={"line": prop["line"]}).json()
    assert r["p_over"] == pytest.approx(prop["model_p_over"])
    assert "Approximate" in r["method"]


def test_prop_probability_falls_as_the_line_rises(client: TestClient) -> None:
    gid, pid, stat, prop = _first_prop(client)
    lo = client.get(f"/api/props/{gid}/{pid}/{stat}", params={"line": prop["line"] - 3}).json()
    hi = client.get(f"/api/props/{gid}/{pid}/{stat}", params={"line": prop["line"] + 3}).json()
    assert lo["p_over"] >= hi["p_over"]


def test_alt_line_has_a_price_only_at_the_posted_line(client: TestClient) -> None:
    gid, pid, stat, prop = _first_prop(client)
    url = f"/api/props/{gid}/{pid}/{stat}"
    main = client.get(url, params={"line": prop["line"]}).json()
    assert main["is_main_line"] is True
    assert main["over_price"] == prop["over_price"]
    assert main["under_price"] == prop["under_price"]
    alt = client.get(url, params={"line": prop["line"] + 1}).json()
    assert alt["is_main_line"] is False
    assert alt["main_line"] == prop["line"]
    assert alt["over_price"] is None
    assert alt["under_price"] is None
    # same function as the main line: the numbers on an alt line come from the stored quantiles
    assert alt["q"] == prop["q"]


def test_prop_validation(client: TestClient) -> None:
    gid, pid, stat, _ = _first_prop(client)
    assert client.get(f"/api/props/{gid}/{pid}/{stat}").status_code == 422
    assert client.get(f"/api/props/{gid}/{pid}/steals", params={"line": 1.5}).status_code == 404
    assert client.get(f"/api/props/{gid}/1/{stat}", params={"line": 1.5}).status_code == 404
    assert client.get(f"/api/props/{gid}/{pid}/{stat}", params={"line": -1}).status_code == 422


def test_swipe_deck_is_blind_and_seeded(client: TestClient) -> None:
    a = client.get(f"/api/slates/{DATE}/swipe", params={"seed": 7, "n": 10}).json()
    b = client.get(f"/api/slates/{DATE}/swipe", params={"seed": 7, "n": 10}).json()
    c = client.get(f"/api/slates/{DATE}/swipe", params={"seed": 8, "n": 10}).json()
    assert a == b
    assert [x["prop_id"] for x in a["cards"]] != [x["prop_id"] for x in c["cards"]]
    assert len(a["cards"]) == 10
    allowed = {
        "prop_id", "game_id", "player_id", "player", "team", "opponent",
        "stat", "line", "over_price", "under_price", "book", "tip_utc",
    }  # fmt: skip
    for card in a["cards"]:
        assert set(card) == allowed, "a card carries line and price only"
        assert card["stat"] in STATS


def test_swipe_deck_has_no_duplicates_and_all_cards_are_open(client: TestClient) -> None:
    d = client.get(f"/api/slates/{DATE}/swipe", params={"seed": 1, "n": 60}).json()
    ids = [c["prop_id"] for c in d["cards"]]
    assert len(ids) == len(set(ids))
    assert all(c["tip_utc"] > d["clock_utc"] for c in d["cards"])


def test_results_cover_every_prop_with_forecast_at_the_time(client: TestClient) -> None:
    rows = client.get(f"/api/slates/{DATE}/results").json()["rows"]
    n = client.get(f"/api/slates/{DATE}").json()["counts"]["props"]
    assert len(rows) == n
    for r in rows:
        assert isinstance(r["actual"], int)
        assert 0 <= r["model_p_over"] <= 1 and 0 < r["market_p_over"] < 1


def test_parlay_state_is_the_honest_no_parlay_state(client: TestClient) -> None:
    p = client.get("/api/parlay-of-the-day").json()
    assert p["state"] == "C"
    assert p["headline_verdict"] == "no_positive_ev_found"
    assert p["verdict_line"] == "Keep your money."
    assert p["gate"]["gate_open"] is False
    assert p["model_weight"] == 0


def test_evidence_says_the_market_was_better(client: TestClient) -> None:
    e = client.get("/api/evidence").json()
    assert e["games_t60"]["verdict"] == "market_better"
    assert {r["stat"] for r in e["props_consensus_t60"]} == set(STATS)
    assert all(r["delta"] > 0 for r in e["props_consensus_t60"])


def test_cors_allows_localhost_only(client: TestClient) -> None:
    ok = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in bad.headers
    lan = client.get("/api/health", headers={"Origin": "http://192.168.1.20:5173"})
    assert "access-control-allow-origin" not in lan.headers


def test_api_is_read_only(client: TestClient) -> None:
    for method in ("post", "put", "delete", "patch"):
        assert getattr(client, method)("/api/slates").status_code == 405
