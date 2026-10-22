from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api import bankroll


def test_endpoint_serves_fixture_with_derived_numbers(client: TestClient) -> None:
    r = client.get("/api/bankroll")
    assert r.status_code == 200
    d = r.json()
    assert d["account"] == {"cash": 49.44, "bonus": 6.99}
    assert d["derived"]["floor"] == 25.0
    assert d["derived"]["risk_capital"] == 24.44
    assert d["derived"]["budget"] == 6.11
    assert d["derived"]["target"] == 49.93
    assert d["derived"]["stretch"] == 51.91
    assert [m["id"] for m in d["moves"]] == ["P1", "P2", "P3"]
    assert d["positions"][0]["cost"] == 3.85
    assert "Devin places every order" in d["note"]


def test_floor_compounds_monthly() -> None:
    assert bankroll.months_since(date(2026, 10, 22), date(2026, 11, 21)) == 0
    assert bankroll.months_since(date(2026, 10, 22), date(2026, 11, 22)) == 1
    assert bankroll.months_since(date(2026, 10, 22), date(2027, 10, 22)) == 12
    raw = bankroll.load()
    assert raw["derived"]["months"] == 0  # type: ignore[index]


def test_floor_after_three_months(tmp_path: Path) -> None:
    import json

    d = json.loads(bankroll.DEFAULT_FILE.read_text())
    d["as_of"] = "2027-01-22"
    out = bankroll.build(d)
    assert out["derived"]["floor"] == round(25 * 1.01**3, 2)  # type: ignore[index]


def test_invalid_file_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "b.json"
    bad.write_text('{"as_of": "2026-10-22"}')
    with pytest.raises(ValueError):
        bankroll.load(bad)


def test_no_order_routes(client: TestClient) -> None:
    assert client.post("/api/bankroll").status_code == 405
