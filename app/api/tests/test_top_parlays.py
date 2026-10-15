from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api.top_parlays import ENV

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "top_parlays"


def test_serves_fixture(client: TestClient) -> None:
    r = client.get("/api/top-parlays/2024-11-12")
    assert r.status_code == 200
    d = r.json()
    assert d["schema"] == "top_parlays/1"
    assert d["verdict"] == "keep_your_money"
    assert len(d["sizes"]["2"]) == 3 and len(d["sizes"]["3"]) == 3
    assert [len(t["legs"]) for t in d["sizes"]["3"]] == [3, 3, 3]
    assert any("T490" in c for c in d["caveats"])


def test_fixture_numbers_are_internally_consistent() -> None:
    for f in FIX.glob("top_parlays_*.json"):
        d = json.loads(f.read_text())
        assert d["slate_date"] < "2025-01-01"  # never season 2025
        assert d["illustration"] is True
        for tickets in d["sizes"].values():
            probs = [t["p_joint_cal"] for t in tickets]
            assert probs == sorted(probs, reverse=True)
            for t in tickets:
                lo, hi = t["p_joint_cal_80"]
                assert lo <= t["p_joint_cal"] <= hi
                assert t["ev_low80"] <= t["ev_per_dollar"]


def test_empty_day(client: TestClient) -> None:
    d = client.get("/api/top-parlays/2024-11-20").json()
    assert d["empty_reason"] == "no priced legs"
    assert d["sizes"] == {"2": [], "3": []}


def test_missing_is_404(client: TestClient) -> None:
    assert client.get("/api/top-parlays/2024-01-01").status_code == 404


@pytest.mark.parametrize("bad", ["x", "../etc/passwd", "2024-1-1", "2024-11-12.json"])
def test_bad_date_never_reads_a_file(client: TestClient, bad: str) -> None:
    assert client.get(f"/api/top-parlays/{bad}").status_code == 404


def _write(tmp: Path, name: str, body: object) -> None:
    (tmp / name).write_text(json.dumps(body) if not isinstance(body, str) else body)


def test_unknown_schema_refused(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = json.loads((FIX / "top_parlays_2024-11-12.json").read_text())
    d["schema"] = "top_parlays/2"
    _write(tmp_path, "top_parlays_2024-11-12.json", d)
    monkeypatch.setenv(ENV, str(tmp_path))
    r = client.get("/api/top-parlays/2024-11-12")
    assert r.status_code == 422
    assert "unknown schema" in r.json()["detail"]


def test_malformed_and_wrong_date_refused(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = json.loads((FIX / "top_parlays_2024-11-12.json").read_text())
    monkeypatch.setenv(ENV, str(tmp_path))
    del d["sizes"]["2"][0]["legs"][0]["p_cal"]
    _write(tmp_path, "top_parlays_2024-11-12.json", d)
    assert client.get("/api/top-parlays/2024-11-12").status_code == 422
    _write(tmp_path, "top_parlays_2024-11-13.json", "not json")
    assert client.get("/api/top-parlays/2024-11-13").status_code == 422
    d2 = json.loads((FIX / "top_parlays_2024-11-12.json").read_text())
    _write(tmp_path, "top_parlays_2024-11-14.json", d2)  # slate_date disagrees with the name
    assert client.get("/api/top-parlays/2024-11-14").status_code == 422
