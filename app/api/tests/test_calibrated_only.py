"""Devin, 2026-11-10: "only show the calibrated number to me."

Every prop chance an endpoint serves for display is the calibrated one where the export has it
(line_cal at the main line, rung_cal at a listed alternate rung); where it has none, the model's own
number is served and labelled `model`. No 2026 prop calibration is served: see test_modes_tour.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api.forecast import shown_probs

PLANTED = ("0022400001", 1627759, "pts")  # app/fixtures/add_rung_cal_example.py


def _card(client: TestClient, stat: str) -> dict[str, Any]:
    gid, pid, _ = PLANTED
    r = client.get(f"/api/card/{gid}/{pid}/{stat}")
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def _prop(client: TestClient, stat: str) -> dict[str, Any]:
    gid, pid, _ = PLANTED
    g = client.get(f"/api/games/{gid}").json()
    pl = next(p for p in g["players"] if p["player_id"] == pid)
    out: dict[str, Any] = pl["props"][stat]
    return out


def test_every_prop_chance_is_the_calibrated_one_where_the_export_has_it(
    client: TestClient,
) -> None:
    gid, pid, _ = PLANTED
    pts = _prop(client, "pts")
    lc = _card(client, "pts")["line_cal"]
    assert pts["model_p_source"] == "line_cal"
    assert pts["model_p_over"] == pytest.approx(lc["p_over_cal"])
    assert pts["model_p_over"] != pytest.approx(lc["p_over_raw"])
    assert pts["model_p_under"] == pytest.approx(1 - lc["p_over_cal"])
    assert pts["model_p_push"] == 0
    # the leg page, the alt-line endpoint and the ladder agree with it
    b = client.get(f"/api/breakdown/{gid}/{pid}/pts", params={"line": pts["line"]}).json()
    assert b["model"]["p_source"] == "line_cal"
    assert b["model"]["p_over"] == pytest.approx(lc["p_over_cal"])
    alt = client.get(f"/api/props/{gid}/{pid}/pts", params={"line": pts["line"]}).json()
    assert alt["p_source"] == "line_cal" and alt["p_over"] == pytest.approx(lc["p_over_cal"])
    row = next(r for r in b["ladder"] if r["line"] == pts["line"])
    assert row["model_source"] == "line_cal"
    assert row["model_over"] == pytest.approx(lc["p_over_cal"])


def test_where_no_calibrated_number_exists_it_is_labelled_model(client: TestClient) -> None:
    for stat in ("ast", "reb"):  # null reasons in the fixture: no_book_line, grid_mismatch
        p = _prop(client, stat)
        assert p["model_p_source"] == "model"


def test_the_rung_map_is_used_only_at_a_listed_line(client: TestClient) -> None:
    gid, pid, _ = PLANTED
    c = _card(client, "pts")
    rung = c["rung_cal"]["lines"]
    off_main = next(r for r in rung if r["line"] != c["line"])
    got = client.get(f"/api/props/{gid}/{pid}/pts", params={"line": off_main["line"]}).json()
    assert got["p_source"] == "rung_cal" and got["p_over"] == pytest.approx(off_main["p_over"])
    unlisted = max(r["line"] for r in rung) + 3
    far = client.get(f"/api/props/{gid}/{pid}/pts", params={"line": unlisted}).json()
    assert far["p_source"] == "model"
    assert far["p_over"] == pytest.approx(c["dist"]["p_ge"][int(unlisted) + 1])


def test_the_timeline_carries_the_calibrated_chance_on_the_shown_forecast_only(
    client: TestClient,
) -> None:
    gid, pid, _ = PLANTED
    r = client.get(f"/api/knows/{gid}/{pid}/pts")
    assert r.status_code == 200, r.text
    k = r.json()
    assert k["p_over_source"] == "line_cal"
    lc = _card(client, "pts")["line_cal"]
    for s in k["snapshots"]:
        if s["shown"]:
            assert s["p_over"] == pytest.approx(lc["p_over_cal"])
        else:
            assert s["p_over"] is None  # no calibrated value exists for an earlier snapshot


def _prop_dict(**over: Any) -> dict[str, Any]:
    q = [float(10 + i) for i in range(19)]
    base: dict[str, Any] = {
        "forecast": {"q": q, "surv": None},
        "line": 19.5,
        "book_main_line": 19.5,
        "rung_cal": None,
        "line_cal": None,
        "line_cal_null_reason": None,
    }
    return base | over


LC = {
    "book": "draftkings",
    "main_line": 19.5,
    "price_at": None,
    "p_over_raw": 0.5,
    "p_over_cal": 0.43,
    "map": "LINE_CALIBRATION",
    "map_sha8": "443743a1",
    "fit_through": None,
}


def test_shown_probs_rules() -> None:
    assert shown_probs(_prop_dict(line_cal=LC), 19.5).p_over == pytest.approx(0.43)
    assert shown_probs(_prop_dict(line_cal=LC), 19.5).source == "line_cal"
    # line_cal is valid at its own main line only: elsewhere it is the model's own number
    assert shown_probs(_prop_dict(line_cal=LC), 20.5).source == "model"
    # a known null reason at the main line is the model's own number, never a guess
    none = shown_probs(_prop_dict(line_cal_null_reason="no_book_line"), 19.5)
    assert none.source == "model"
    # nothing at all: model only
    assert shown_probs(_prop_dict(), 19.5).source == "model"


def test_a_guess_reveal_shows_the_calibrated_chance_and_no_correctness_field() -> None:
    """The reveal's chance is the one the screens show, the lean follows it, and nothing says
    whether the model was right: that verdict is the locked LINE_CAL_LIVE test, read once."""
    from parlayhitters_api.trackrecord import guess_reveal

    pr = _prop_dict(line_cal=LC, actual=22)
    pr["forecast"]["stamp"] = "s"
    pr["forecast"]["mean"] = 20.0
    pr["line_source"] = "posted"
    game = {
        "game_id": "g1",
        "result": {"home_won": True},
        "players": [{"player_id": 7, "result": None, "props": {"pts": pr}}],
    }

    class Store:
        @staticmethod
        def game(gid: str) -> dict[str, Any] | None:
            return game if gid == "g1" else None

    got = guess_reveal(Store(), "g1:7:pts")
    assert got is not None
    assert got.model_p_over == pytest.approx(0.43) and got.model_p_source == "line_cal"
    assert got.model_call == ("over" if got.model_p_over >= 0.5 else "under")
    assert "model_right" not in got.model_dump()


def _all_keys(x: Any) -> set[str]:
    if isinstance(x, dict):
        return set(x) | {k for v in x.values() for k in _all_keys(v)}
    if isinstance(x, list):
        return {k for v in x for k in _all_keys(v)}
    return set()


def test_no_endpoint_returns_a_model_correctness_field_for_props(client: TestClient) -> None:
    """LINE_CAL_LIVE (frozen): no model-versus-outcome verdict on 2026 props before its look.

    Every response that carries both a model chance and a prop outcome is walked; none may carry
    a field that says the model was right, wrong, correct or a hit.
    """
    # (the published 2024-25 T493 record carries its own "verdict"; it is not a 2026 prop result)
    banned = ("right", "wrong", "correct", "model_hit", "lean_hit", "model_won")
    gid, pid, stat = PLANTED
    slates = client.get("/api/slates").json()
    date = next(s["date"] for s in slates if s["finished"] > 0)
    bodies: list[Any] = [
        client.get(f"/api/slates/{date}").json(),
        client.get(f"/api/slates/{date}/results").json(),
        client.get(f"/api/slates/{date}/swipe").json(),
        client.get(f"/api/games/{gid}").json(),
        client.get(f"/api/games/{gid}/result").json(),
        client.get(f"/api/card/{gid}/{pid}/{stat}").json(),
        client.get("/api/track-record/calibration").json(),
    ]
    deck = client.get("/api/track-record/guess?seed=3&n=8").json()
    assert deck["cards"]
    for c in deck["cards"]:
        g, p, s = c["prop_id"].split(":")
        bodies.append(client.get(f"/api/track-record/guess/{g}/{p}/{s}").json())
    for body in bodies:
        keys = _all_keys(body)
        hits = {k for k in keys if any(b in k.lower() for b in banned)}
        assert not hits, hits
