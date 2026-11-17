"""Parlay diagnostics: only stored joint chances, from the record whose engine is that one; no
price, stake or expected value; hostile records never crash."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import seal
from fastapi.testclient import TestClient
from honesty_helpers import MONEY_KEYS, export_api, keys_of

# --------------------------------------------------------------------------- parlay


def _parlay(d: Path, records: list[dict[str, Any]], day: str = "2024-11-12") -> None:
    p = {"date": day, "n_evaluated": len(records), "n_positive": 0, "records": records,
         "source": f"evaluate_{day}.json", "status": "evaluated",
         "verdict": "no_positive_ev_found"}  # fmt: skip
    (d / "parlay_of_day.json").write_text(json.dumps(p))
    seal(d)


LEG = {"game_id": "0022400023", "player_id": 990001, "stat": "pts", "side": "yes",
       "threshold": 10.0}  # fmt: skip


def test_parlay_diagnostics_serve_only_what_is_stored(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recs = [
        {"engine": "gaussian_copula", "legs": [LEG, LEG], "raw_model_prob": 0.31,
         "model_prob": 0.3, "model_weight": 0.0, "ev": 0.5, "kelly": 0.1, "price": 0.2,
         "verdict": "no_positive_ev_found", "ticker": "T1"},
        {"engine": "independence", "legs": [LEG, LEG], "raw_model_prob": 0.25,
         "verdict": "no_positive_ev_found"},
        {"engine": "marginal", "legs": [LEG], "raw_model_prob": 0.6},
    ]  # fmt: skip
    _parlay(export_dir, recs)
    for c in export_api(export_dir, monkeypatch):
        body = c.get("/api/parlay-diagnostics/2024-11-12").json()
        assert body["n_records"] == 3 and body["engines"] == {
            "gaussian_copula": 1, "independence": 1, "marginal": 1}  # fmt: skip
        a, b, m = body["records"]
        assert a["joint_copula"] == 0.31 and a["joint_independent"] is None
        assert b["joint_independent"] == 0.25 and b["joint_copula"] is None
        assert m["joint_copula"] is None and m["joint_independent"] is None and m["missing"]
        assert not (keys_of(body) & MONEY_KEYS)
        page = c.get("/api/parlay-diagnostics/2024-11-12?offset=1&limit=1").json()
        assert page["n_returned"] == 1 and page["records"][0]["engine"] == "independence"
        r = c.get("/api/parlay-diagnostics/2024-11-11")
        assert r.status_code == 404 and r.json()["code"] == "no_parlay_check"
        assert r.json()["field"] == "date"


def test_parlay_diagnostics_fixture_and_hostile_records(
    client: TestClient, export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = client.get("/api/parlay-diagnostics/2024-11-12").json()
    assert body["available"] is False and body["records"] == []
    assert client.get("/api/parlay-diagnostics/2031-01-01").status_code == 404
    weird: list[Any] = [{"legs": "x", "raw_model_prob": "0.5", "engine": 7}, {"legs": [1, None]}]
    _parlay(export_dir, weird)
    for c in export_api(export_dir, monkeypatch):
        r = c.get("/api/parlay-diagnostics/2024-11-12")
        assert r.status_code == 200
        assert all(x["joint_stored"] is None for x in r.json()["records"])
