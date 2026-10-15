from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api.top_parlays import ENV

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "top_parlays"
DAY = "2024-01-01"
EMPTY_DAY = "2024-01-02"


def fixture(date: str = DAY) -> dict[str, Any]:
    d: dict[str, Any] = json.loads((FIX / f"top_parlays_{date}.json").read_text())
    return d


def put(tmp: Path, d: Any, name: str | None = None) -> None:
    text = d if isinstance(d, str) else json.dumps(d)
    (tmp / (name or f"top_parlays_{DAY}.json")).write_text(text)


@pytest.fixture
def tp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(ENV, str(tmp_path))
    return tmp_path


def status(client: TestClient, d: dict[str, Any], tmp: Path) -> int:
    put(tmp, d)
    return client.get(f"/api/top-parlays/{DAY}").status_code


def test_serves_fixture(client: TestClient) -> None:
    r = client.get(f"/api/top-parlays/{DAY}")
    assert r.status_code == 200
    d = r.json()
    assert d["schema"] == "top_parlays/2"
    assert d["illustration"] is True  # asserted on the response, not only on disk
    assert d["verdict"] == "keep_your_money"
    assert d["gate"]["open"] is False
    assert len(d["sizes"]["2"]) == 3 and len(d["sizes"]["3"]) == 3
    assert [len(t["legs"]) for t in d["sizes"]["3"]] == [3, 3, 3]
    assert any("T490" in c and "n 3,337" in c for c in d["caveats"])
    assert d["made_at"].endswith("Z")


def test_illustration_never_overlaps_a_real_store(client: TestClient) -> None:
    dates = {s["date"] for s in client.get("/api/slates").json()}
    game_ids: set[str] = set()
    for date in dates:
        game_ids |= {g["game_id"] for g in client.get(f"/api/slates/{date}").json()["games"]}
    for f in FIX.glob("top_parlays_*.json"):
        d = json.loads(f.read_text())
        assert d["slate_date"] not in dates
        assert d["slate_date"] < "2025-01-01"  # never season 2025
        for tickets in d["sizes"].values():
            for t in tickets:
                for leg in t["legs"]:
                    assert leg["game_id"] not in game_ids
                    assert leg["game_id"].startswith("ILLUS-")
    for date in dates:  # a real night shows "no file", not the illustration
        assert client.get(f"/api/top-parlays/{date}").status_code == 404


def test_fixture_numbers_are_internally_consistent() -> None:
    for f in FIX.glob("top_parlays_*.json"):
        d = json.loads(f.read_text())
        assert d["illustration"] is True
        for tickets in d["sizes"].values():
            probs = [t["p_joint_cal"] for t in tickets]
            assert probs == sorted(probs, reverse=True)


def test_empty_day(client: TestClient) -> None:
    d = client.get(f"/api/top-parlays/{EMPTY_DAY}").json()
    assert d["empty_reason"] == "no priced legs"
    assert d["sizes"] == {"2": [], "3": []}


def test_missing_is_404(client: TestClient) -> None:
    assert client.get("/api/top-parlays/2023-01-01").status_code == 404


@pytest.mark.parametrize(
    "bad", ["x", "../etc/passwd", "2024-1-1", "2024-01-01.json", "2024-13-45", "%D9%A2024-01-01"]
)
def test_bad_date_never_reads_a_file(client: TestClient, bad: str) -> None:
    assert client.get(f"/api/top-parlays/{bad}").status_code == 404


def test_trailing_newline_and_unicode_digits_rejected(client: TestClient) -> None:
    assert client.get("/api/top-parlays/2024-01-01%0A").status_code == 404
    assert client.get("/api/top-parlays/%D9%A2%D9%A0%D9%A2%D9%A4-01-01").status_code == 404


def test_unknown_schemas_refused(client: TestClient, tp_dir: Path) -> None:
    for schema in ("top_parlays/1", "top_parlays/3"):
        d = fixture()
        d["schema"] = schema
        put(tp_dir, d)
        r = client.get(f"/api/top-parlays/{DAY}")
        assert r.status_code == 422
        assert "unknown schema" in r.json()["detail"]


def test_malformed_and_wrong_date_refused(client: TestClient, tp_dir: Path) -> None:
    d = fixture()
    del d["sizes"]["2"][0]["legs"][0]["p_cal"]
    assert status(client, d, tp_dir) == 422
    put(tp_dir, "not json", "top_parlays_2024-01-03.json")
    assert client.get("/api/top-parlays/2024-01-03").status_code == 422
    put(tp_dir, fixture(), "top_parlays_2024-01-04.json")  # slate_date disagrees with the name
    assert client.get("/api/top-parlays/2024-01-04").status_code == 422


def mutate(path: str, fn: Any) -> dict[str, Any]:
    d = copy.deepcopy(fixture())
    fn(d)
    return d


NEGATIVE: dict[str, Any] = {
    "price_after_tip": lambda d: d["sizes"]["2"][0]["legs"][0].update(
        price_at=d["sizes"]["2"][0]["legs"][0]["tipoff"]
    ),
    "made_after_tip": lambda d: d.update(made_at="2024-01-02T00:00:00Z"),
    "naive_time": lambda d: d.update(made_at="2024-01-01T22:10:00"),
    "not_a_time": lambda d: d.update(made_at="soon"),
    "positive_ev_on_illustration": lambda d: d.update(verdict="positive_ev_found"),
    "unknown_verdict": lambda d: d.update(verdict="LOCK_OF_THE_NIGHT"),
    "three_legs_under_size_2": lambda d: d["sizes"]["2"].__setitem__(0, d["sizes"]["3"][0]),
    "four_tickets": lambda d: d["sizes"]["2"].append(copy.deepcopy(d["sizes"]["2"][0])),
    "duplicate_rank": lambda d: d["sizes"]["2"][1].update(rank=1),
    "inverted_range": lambda d: d["sizes"]["2"][0].update(p_joint_cal_80=[0.9, 0.1]),
    "ev_low_above_ev": lambda d: d["sizes"]["2"][0].update(ev_low80=0.9),
    "joint_far_from_legs": lambda d: d["sizes"]["2"][0].update(
        p_joint_cal=0.99, p_joint_cal_80=[0.9, 1.0]
    ),
    "price_in_dead_zone": lambda d: d["sizes"]["2"][0]["legs"][0].update(price_american=50),
    "gate_open_lies": lambda d: d["gate"].update(open=True),
    "no_gate": lambda d: d.pop("gate"),
    "no_illustration_flag": lambda d: d.pop("illustration"),
    "banned_why": lambda d: d["sizes"]["2"][0].update(why="LOCK. Guaranteed."),
    "banned_status": lambda d: d["calibration"].update(status="free money"),
    "banned_caveat": lambda d: d["caveats"].append("can't lose"),
    "banned_reason": lambda d: d["sizes"]["2"][0]["legs"][0].update(reason="best bet"),
}


@pytest.mark.parametrize("name", sorted(NEGATIVE))
def test_negative_cases_refused(client: TestClient, tp_dir: Path, name: str) -> None:
    assert status(client, mutate(name, NEGATIVE[name]), tp_dir) == 422, name


def test_positive_ev_accepted_only_with_open_gate_real_file(
    client: TestClient, tp_dir: Path
) -> None:
    d = fixture()
    d["illustration"] = False
    d["verdict"] = "positive_ev_found"
    d["gate"] = {
        "settled": 31,
        "settled_min": 30,
        "slate_dates": 15,
        "dates_min": 14,
        "skill_vs_market": 0.002,
        "open": True,
    }
    assert status(client, d, tp_dir) == 422  # no ticket has ev_low80 > 0
    d["sizes"]["2"][0]["ev_per_dollar"] = 0.2
    d["sizes"]["2"][0]["ev_low80"] = 0.01
    assert status(client, d, tp_dir) == 200


def test_unconfigured_directory_is_503_not_a_quiet_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENV, str(tmp_path / "nope"))
    assert client.get(f"/api/top-parlays/{DAY}").status_code == 503
    monkeypatch.setenv(ENV, "")
    assert client.get(f"/api/top-parlays/{DAY}").status_code == 503


def test_symlink_out_of_directory_is_missing(
    client: TestClient, tp_dir: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside")
    put(outside, fixture())
    (tp_dir / f"top_parlays_{DAY}.json").symlink_to(outside / f"top_parlays_{DAY}.json")
    assert client.get(f"/api/top-parlays/{DAY}").status_code == 404
