"""Live feeds: parsers, cache, error states and routes, all against recorded fixtures (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api import live

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "live"


def load(name: str) -> dict[str, Any]:
    d: dict[str, Any] = json.loads((FIX / name).read_text())
    return d


@pytest.fixture(autouse=True)
def fresh_cache() -> None:
    live.clear_cache()


def feed(monkeypatch: pytest.MonkeyPatch, summary: dict[str, Any] | None = None) -> list[str]:
    calls: list[str] = []

    def fake(url: str) -> dict[str, Any]:
        calls.append(url)
        if url.endswith("/scoreboard"):
            return load("espn_scoreboard.json")
        if "/summary?event=" in url:
            return summary if summary is not None else load("espn_summary.json")
        if "kalshi" in url:
            return load("kalshi_markets.json")
        raise live.SourceDown("unexpected url")

    monkeypatch.setattr(live, "http_get", fake)
    return calls


def test_scoreboard_parsed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    feed(monkeypatch)
    r = client.get("/api/live/scoreboard")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "ESPN unofficial feed"
    g = body["games"][0]
    assert g["status"] == {
        "state": "in",
        "period": 3,
        "clock": "3:32",
        "detail": "3:32 - 3rd",
        "completed": False,
    }
    assert (g["home"]["abbr"], g["home"]["score"], g["away"]["score"]) == ("ORL", 89, 91)
    assert g["et_date"] == "2024-11-12"


def test_game_box_score_and_numbers_trace_to_feed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    feed(monkeypatch)
    body = client.get("/api/live/game/401909834").json()
    raw = load("espn_summary.json")["boxscore"]["players"][0]["statistics"][0]
    first = raw["athletes"][0]
    labels = raw["labels"]
    row = next(p for p in body["players"] if p["player_id"] == first["athlete"]["id"])
    assert row["pts"] == int(first["stats"][labels.index("PTS")])
    assert row["reb"] == int(first["stats"][labels.index("REB")])
    assert row["fg3m"] == int(first["stats"][labels.index("3PT")].split("-")[0])
    assert row["pm"] == first["stats"][labels.index("+/-")]
    assert body["status"]["period"] == 3 and body["box_available"] is True


def test_forecast_joined_by_name_and_date_else_none(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = load("espn_summary.json")
    summary["boxscore"]["players"][0]["statistics"][0]["athletes"][0]["athlete"]["displayName"] = (
        "Jayson Tatum"
    )
    feed(monkeypatch, summary)
    body = client.get("/api/live/game/401909834").json()
    assert body["forecast_date"] == "2024-11-12"
    by = {p["name"]: p for p in body["players"]}
    assert by["Jayson Tatum"]["forecast"]["pts"]["mean"] == 26.7
    slate = json.loads((FIX.parent / "slates" / "2024-11-12.json").read_text())
    known = {live.norm_name(pl["name"]) for g in slate["games"] for pl in g["players"]}
    others = [p for p in body["players"] if live.norm_name(p["name"]) not in known]
    assert others and all(p["forecast"] is None for p in others)
    assert all(
        p["forecast"] is not None for p in body["players"] if live.norm_name(p["name"]) in known
    )


def test_no_forecast_when_date_has_no_slate(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = load("espn_summary.json")
    summary["header"]["competitions"][0]["date"] = "2024-11-30T23:00Z"
    feed(monkeypatch, summary)
    body = client.get("/api/live/game/401909834").json()
    assert body["forecast_date"] is None and "no pre-game forecast" in body["forecast_note"].lower()
    assert all(p["forecast"] is None for p in body["players"])


def test_bad_event_id_refused(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = feed(monkeypatch)
    assert client.get("/api/live/game/abc..").status_code in (404, 422)
    assert client.get("/api/live/game/12x").status_code == 422
    assert calls == []


def test_kalshi_prices_in_dollars_and_missing_ticker(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    feed(monkeypatch)
    t0 = load("kalshi_markets.json")["markets"][0]
    r = client.get("/api/live/kalshi", params={"tickers": f"{t0['ticker']},NOPE-1"})
    body = r.json()
    assert body["source"] == "Kalshi public prices"
    m = body["markets"][0]
    assert m["yes_bid"] == float(t0["yes_bid_dollars"]) and m["yes_ask"] == float(
        t0["yes_ask_dollars"]
    )
    assert m["no_ask"] == float(t0["no_ask_dollars"])
    assert body["markets"][1]["found"] is False


def test_kalshi_cents_fields_also_parse() -> None:
    out = live.parse_kalshi({"markets": [{"ticker": "T", "yes_bid": 41, "yes_ask": 45}]}, ["T"])
    assert out[0]["yes_bid"] == 0.41 and out[0]["yes_ask"] == 0.45 and out[0]["no_ask"] is None


@pytest.mark.parametrize("bad", ["", "a b", "x;y", "a" * 90, ",".join(f"T{i}" for i in range(30))])
def test_kalshi_ticker_validation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    calls = feed(monkeypatch)
    assert client.get("/api/live/kalshi", params={"tickers": bad}).status_code == 422
    assert calls == []


def test_cache_within_ttl_and_refetch_after(monkeypatch: pytest.MonkeyPatch) -> None:
    n = {"c": 0}

    def fetch(url: str) -> dict[str, Any]:  # noqa: ARG001
        n["c"] += 1
        return {"n": n["c"]}

    a, stale, _ = live.cached("k", "u", fetch, now=100.0)
    b, _, age = live.cached("k", "u", fetch, now=110.0)
    assert (a, b, stale, age, n["c"]) == ({"n": 1}, {"n": 1}, False, 10.0, 1)
    c, _, _ = live.cached("k", "u", fetch, now=121.0)
    assert c == {"n": 2}


def test_stale_copy_served_marked_then_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def ok(url: str) -> dict[str, Any]:  # noqa: ARG001
        return {"ok": 1}

    def down(url: str) -> dict[str, Any]:  # noqa: ARG001
        raise live.SourceDown("URLError")

    live.cached("k", "u", ok, now=0.0)
    data, stale, age = live.cached("k", "u", down, now=60.0)
    assert data == {"ok": 1} and stale is True and age == 60.0
    with pytest.raises(live.SourceDown):
        live.cached("k", "u", down, now=400.0)


def test_upstream_down_is_a_502_with_a_reason(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(url: str) -> dict[str, Any]:  # noqa: ARG001
        raise live.SourceDown("TimeoutError")

    monkeypatch.setattr(live, "http_get", down)
    r = client.get("/api/live/scoreboard")
    assert r.status_code == 502 and "ESPN" in r.json()["detail"]


def test_http_get_refuses_other_hosts() -> None:
    with pytest.raises(live.SourceDown):
        live.http_get("https://example.com/x")


def test_norm_name() -> None:
    assert live.norm_name("Nikola Jokić") == live.norm_name("Nikola Jokic") == "nikola jokic"
    assert live.norm_name("Jaren Jackson Jr.") == live.norm_name("Jaren Jackson")


def test_model_endpoint_player_win_and_nothing(client: TestClient) -> None:
    slate = json.loads((FIX.parent / "slates" / "2024-11-12.json").read_text())
    g = slate["games"][0]
    pl = g["players"][0]
    q = {"date": "2024-11-12", "kind": "player", "player": pl["name"], "stat": "pts", "line": 20}
    over = client.get("/api/live/model", params=q).json()
    under = client.get("/api/live/model", params={**q, "side": "under"}).json()
    assert 0 < over["p"] < 1 and abs(over["p"] + under["p"] - 1) < 1e-9
    assert "pre-game model" in over["source"]
    w = client.get(
        "/api/live/model", params={"date": "2024-11-12", "kind": "win", "team": g["home"]["abbr"]}
    ).json()
    a = client.get(
        "/api/live/model", params={"date": "2024-11-12", "kind": "win", "team": g["away"]["abbr"]}
    ).json()
    assert w["p"] == g["win_probability"]["model_home"] and abs(w["p"] + a["p"] - 1) < 1e-9
    for bad in (
        {"date": "2024-11-12", "kind": "player", "player": "Nobody Atall"},
        {"date": "2024-11-12", "kind": "total"},
        {"date": "2030-01-01", "kind": "win", "team": "BOS"},
    ):
        r = client.get("/api/live/model", params=bad).json()
        assert r["p"] is None and r["reason"]
    assert client.get("/api/live/model", params={"date": "x", "kind": "win"}).status_code == 422
