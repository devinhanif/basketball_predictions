"""The swipe card detail: as-of stats, the full distribution grid, the rung_cal contract fields."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api.forecast import clean_line_cal, clean_null_reason, clean_rung_cal, dist_grid

DATE = "2024-11-12"
PLANTED = ("0022400001", 1627759, "pts")  # app/fixtures/add_rung_cal_example.py


def _card(client: TestClient, gid: str, pid: int, stat: str) -> dict[str, Any]:
    r = client.get(f"/api/card/{gid}/{pid}/{stat}")
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def test_planted_prop_carries_exact_grid_and_rung_cal(client: TestClient) -> None:
    c = _card(client, *PLANTED)
    d = c["dist"]
    assert d["method"] == "exact" and d["n"] == 1000
    assert d["p_ge"][0] == 1.0
    assert all(b <= a for a, b in zip(d["p_ge"], d["p_ge"][1:], strict=False))
    assert c["book_main_line"] == c["line"]
    rc = c["rung_cal"]
    assert rc["map_version"] == "rung_cal_map_v1" and rc["book"] == "draftkings"
    ps = [r["p_over"] for r in rc["lines"]]
    assert ps == sorted(ps, reverse=True)
    assert all(r["line"] % 1 == 0.5 for r in rc["lines"])


def test_rung_lines_only_where_raw_over_is_at_least_half(client: TestClient) -> None:
    """Contract amendment 2026-10-15: the map is fit only where raw P(over) >= 0.5."""
    c = _card(client, *PLANTED)
    rc = c["rung_cal"]
    assert rc["map_sha8"] == "116f4480" and rc["book"] == "draftkings"
    p_ge = c["dist"]["p_ge"]
    assert rc["lines"]
    assert all(p_ge[int(r["line"]) + 1] >= 0.5 for r in rc["lines"])
    higher = [k + 0.5 for k in range(len(p_ge) - 1) if p_ge[k + 1] < 0.5]
    assert higher and not {r["line"] for r in rc["lines"]} & set(higher)


def test_grid_matches_the_alt_line_endpoint(client: TestClient) -> None:
    c = _card(client, *PLANTED)
    gid, pid, stat = PLANTED
    for k in (10, 20, 23, 30):  # P(over k.5) = P(Y >= k+1)
        r = client.get(f"/api/props/{gid}/{pid}/{stat}", params={"line": k + 0.5}).json()
        assert r["p_over"] == pytest.approx(c["dist"]["p_ge"][k + 1])


def test_prop_without_fields_has_none_and_a_quantile_grid(client: TestClient) -> None:
    g = client.get(f"/api/games/{PLANTED[0]}").json()
    pl = g["players"][0]
    stat = next(iter(pl["props"]))
    c = _card(client, PLANTED[0], pl["player_id"], stat)
    assert c["rung_cal"] is None and c["book_main_line"] is None
    assert c["dist"]["method"] == "quantile" and c["dist"]["n"] is None
    assert c["dist"]["line_min"] <= c["line"] <= c["dist"]["line_max"] + 1


def test_stats_are_as_of_before_tonight(client: TestClient) -> None:
    c = _card(client, *PLANTED)
    s = c["stats"]
    assert s["as_of_date"] == DATE
    assert len(s["last5"]) <= 5 and s["last10"][: len(s["last5"])] == s["last5"]
    assert len(s["last10"]) <= 10
    h = s["hit10_over"]
    assert h["n"] == len(s["last10"]) and h["line"] == c["line"]
    assert h["hits"] == sum(v > c["line"] for v in s["last10"])
    b = client.get(f"/api/breakdown/{PLANTED[0]}/{PLANTED[1]}/pts").json()
    assert all(chip["date"] < DATE for chip in b["chips"])


def test_card_404s(client: TestClient) -> None:
    assert client.get("/api/card/nope/1/pts").status_code == 404
    assert client.get(f"/api/card/{PLANTED[0]}/1/pts").status_code == 404
    assert client.get(f"/api/card/{PLANTED[0]}/{PLANTED[1]}/blk").status_code == 404


def test_game_detail_exposes_both_optional_fields(client: TestClient) -> None:
    g = client.get(f"/api/games/{PLANTED[0]}").json()
    pl = next(p for p in g["players"] if p["player_id"] == PLANTED[1])
    pr = pl["props"]["pts"]
    assert pr["book_main_line"] == pr["line"] and pr["rung_cal"]["map_sha8"]


GOOD: dict[str, Any] = {
    "book": "draftkings",
    "main_line": 24.5,
    "price_at": "2024-11-12T22:55:00Z",
    "map_version": "rung_cal_map_v1",
    "map_sha8": "abcdef01",
    "lines": [
        {"line": 23.5, "p_over": 0.6},
        {"line": 24.5, "p_over": 0.5},
        {"line": 25.5, "p_over": 0.5},
    ],
}


def test_clean_rung_cal_accepts_dict_and_json_text() -> None:
    assert clean_rung_cal(GOOD) is not None
    assert clean_rung_cal(json.dumps(GOOD)) == clean_rung_cal(GOOD)
    assert clean_rung_cal(None) is None


@pytest.mark.parametrize(
    "bad",
    [
        {"lines": [{"line": 23.5, "p_over": 0.4}, {"line": 24.5, "p_over": 0.5}]},  # rises
        {"lines": [{"line": 23.0, "p_over": 0.6}]},  # not a half-point
        {"lines": [{"line": 24.5, "p_over": 0.6}, {"line": 23.5, "p_over": 0.7}]},  # not ascending
        {"lines": [{"line": 23.5, "p_over": 1.2}]},
        {"lines": []},
        {"map_version": None},
    ],
)
def test_clean_rung_cal_drops_a_violation_whole(bad: dict[str, Any]) -> None:
    assert clean_rung_cal({**GOOD, **bad}) is None
    assert clean_rung_cal("{not json") is None


def test_dist_grid_exact_reads_the_table_without_interpolation() -> None:
    f = {"q": [float(i) for i in range(19)], "mean": 3.0, "surv": {"n": 100, "c": [90, 60, 30, 0]}}
    d = dist_grid(f)
    assert d["method"] == "exact" and d["p_ge"] == [1.0, 0.9, 0.6, 0.3, 0.0]


def test_top_parlays_passes_the_hindsight_label_through(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "fixtures" / "top_parlays_example.json"
    (tmp_path / "top_parlays_2025-01-27.json").write_text(src.read_text())
    monkeypatch.setenv("APP_TOP_PARLAYS_DIR", str(tmp_path))
    body = client.get("/api/top-parlays/2025-01-27").json()
    assert body["illustration"] is True
    assert body["fixture"]["kind"] == "hindsight_illustration"
    assert "not a forecast" in body["fixture"]["label"]


def test_line_cal_fixture_rows_surface_on_the_card(client: TestClient) -> None:
    gid, pid, _ = PLANTED  # app/fixtures/add_line_cal_example.py
    pts = _card(client, gid, pid, "pts")
    lc = pts["line_cal"]
    assert lc["map"] == "LINE_CALIBRATION" and lc["map_sha8"] == "443743a1"
    assert lc["main_line"] == pts["book_main_line"] == pts["line"]
    assert lc["p_over_raw"] == pytest.approx(pts["dist"]["p_ge"][int(pts["line"]) + 1])
    assert pts["line_cal_null_reason"] is None
    assert "synthetic" not in lc
    ast = _card(client, gid, pid, "ast")
    assert ast["line_cal"] is None and ast["line_cal_null_reason"] == "no_book_line"
    reb = _card(client, gid, pid, "reb")
    assert reb["line_cal"] is None and reb["line_cal_null_reason"] == "grid_mismatch"


def test_rows_without_the_columns_read_as_null(client: TestClient) -> None:
    g = client.get(f"/api/games/{PLANTED[0]}").json()
    pl = g["players"][0]
    c = _card(client, PLANTED[0], pl["player_id"], next(iter(pl["props"])))
    assert c["line_cal"] is None and c["line_cal_null_reason"] is None


GOOD_LC: dict[str, Any] = {
    "book": "draftkings",
    "main_line": 22.5,
    "price_at": "2024-11-12T22:55:00Z",
    "p_over_raw": 0.46,
    "p_over_cal": 0.49,
    "map": "LINE_CALIBRATION",
    "map_sha8": "443743a1",
    "fit_through": "2025-06-30",
}


def test_clean_line_cal_accepts_dict_and_json_text() -> None:
    assert clean_line_cal(GOOD_LC) is not None
    assert clean_line_cal(json.dumps(GOOD_LC)) == clean_line_cal(GOOD_LC)
    assert clean_line_cal(None) is None


@pytest.mark.parametrize(
    "bad",
    [
        {"p_over_cal": 1.2},
        {"p_over_raw": float("nan")},
        {"main_line": 22.0},
        {"map": 5},
        {"p_over_cal": "x"},
    ],
)
def test_clean_line_cal_drops_a_violation_whole(bad: dict[str, Any]) -> None:
    assert clean_line_cal({**GOOD_LC, **bad}) is None
    assert clean_line_cal("{not json") is None


def test_clean_null_reason_keeps_only_contract_codes() -> None:
    assert clean_null_reason("grid_mismatch") == "grid_mismatch"
    assert clean_null_reason("made_up") is None and clean_null_reason(None) is None
