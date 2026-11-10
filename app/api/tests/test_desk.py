from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api.desk import DeskMissing, DeskRefused, check_desk, load_desk, money_fields
from parlayhitters_api.edges import top_contracts

FIX = Path(__file__).resolve().parents[2] / "fixtures"


def _shared() -> dict[str, Any]:
    d: dict[str, Any] = json.loads((FIX / "desk_fixture_shared.json").read_text())
    return d


def test_fixture_cuts_follow_the_contract() -> None:
    shared = check_desk(_shared())
    assert shared["privacy"] == "shared" and "_fixture" in shared
    assert money_fields(shared) == []
    private = json.loads((FIX / "desk_fixture_private.json").read_text())
    assert money_fields(private)  # the private cut does hold money


def test_a_shared_cut_with_money_is_refused() -> None:
    d = _shared()
    d["balance"] = {"start_usd": 70, "current_usd": 69, "floor_usd": 20}
    with pytest.raises(DeskRefused, match="balance"):
        check_desk(d)
    d = _shared()
    d["plays"][0]["stake_usd"] = 1.0
    with pytest.raises(DeskRefused, match="stake_usd"):
        check_desk(d)


@pytest.mark.parametrize(
    "change", [{"schema": 2}, {"privacy": "public"}, {"plays": "x"}, {"status": None}]
)
def test_malformed_desks_are_refused(change: dict[str, Any]) -> None:
    with pytest.raises(DeskRefused):
        check_desk(_shared() | change)


def test_missing_file_is_a_normal_state(tmp_path: Path) -> None:
    with pytest.raises(DeskMissing):
        load_desk(tmp_path / "nope.json")


def test_desk_route_is_private_only(
    export_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PH_PRIVATE", raising=False)
    monkeypatch.setenv("APP_DESK_SHARED_FILE", str(FIX / "does_not_exist.json"))
    assert export_client.get("/api/desk").status_code == 404
    monkeypatch.setenv("PH_PRIVATE", "1")
    monkeypatch.setenv("APP_DESK_FILE", str(FIX / "desk_fixture_shared.json"))
    r = export_client.get("/api/desk")
    assert r.status_code == 200 and r.json()["privacy"] == "shared"
    monkeypatch.setenv("APP_DESK_FILE", str(FIX / "does_not_exist.json"))
    assert export_client.get("/api/desk").status_code == 404


def test_public_desk_serves_the_shared_cut(
    export_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PH_PRIVATE", raising=False)
    monkeypatch.setenv("APP_DESK_SHARED_FILE", str(FIX / "desk_fixture_shared.json"))
    r = export_client.get("/api/desk")
    assert r.status_code == 200 and r.json()["privacy"] == "shared"


def test_public_desk_refuses_a_cut_with_a_money_field(
    export_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    d = _shared()
    d["balance"] = 12.5
    f = tmp_path / "leaky.json"
    f.write_text(json.dumps(d))
    monkeypatch.delenv("PH_PRIVATE", raising=False)
    monkeypatch.setenv("APP_DESK_SHARED_FILE", str(f))
    r = export_client.get("/api/desk")
    assert r.status_code == 422 and "balance" in r.json()["detail"]
    # a private-labelled file is refused even with no money fields
    f.write_text(json.dumps(_shared() | {"privacy": "private"}))
    assert export_client.get("/api/desk").status_code == 422


def test_public_desk_never_reads_desk_json(
    export_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PH_PRIVATE", raising=False)
    monkeypatch.setenv("APP_DESK_FILE", str(FIX / "desk_fixture_shared.json"))
    monkeypatch.setenv("APP_DESK_SHARED_FILE", str(FIX / "does_not_exist.json"))
    assert export_client.get("/api/desk").status_code == 404


def test_top_contracts_sorts_by_edge_and_skips_unknown_games() -> None:
    def rec(pid: int, ev: float, game: str = "G1") -> dict[str, Any]:
        return {
            "legs": [{"game_id": game, "player_id": pid, "stat": "ast", "threshold": 6.0}],
            "side": "yes", "price": 0.5, "fees": 0.01, "ev": ev, "model_prob": 0.5 + ev,
            "prob_interval": [0.5, 0.5], "ticker": f"T{pid}",
        }  # fmt: skip

    games = {
        "G1": {
            "home": {"abbr": "HHH"}, "away": {"abbr": "AAA"}, "tip_utc": "2026-11-08T20:00:00Z",
            "players": [{"player_id": 1, "side": "home"}, {"player_id": 2, "side": "away"}],
        }
    }  # fmt: skip
    total = rec(0, 0.9)
    total["legs"][0]["stat"] = "total"
    out = top_contracts(
        [rec(1, -0.05), rec(2, 0.02), rec(3, 0.5, "NOPE"), total], {1: "One"}, games.get, n=5
    )
    assert out["n_priced"] == 2 and out["n_positive"] == 1
    first = out["contracts"][0]
    assert first["player_id"] == 2 and first["player"] == "Unnamed player (id 2)"
    assert first["team"] == "AAA" and first["matchup"] == "AAA @ HHH"
    assert out["contracts"][1]["player"] == "One"


def test_contracts_top_endpoint(export_client: TestClient) -> None:
    r = export_client.get("/api/contracts/top?n=3")
    assert r.status_code == 200
    body = r.json()
    assert {"n_priced", "n_positive", "contracts", "report_date"} <= set(body)


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda d: d["plays"][0].__setitem__("contracts", 58), "contracts"),
        (lambda d: d["plays"][0].__setitem__("fee_cents", 4.0), "fee_cents"),
        (lambda d: d["plays"][0].__setitem__("reason", "staked $12.40 here"), "dollar"),
        (
            lambda d: d.__setitem__("sources", {"book": "/Users/someone/data/paper_book.duckdb"}),
            "path",
        ),
        (lambda d: d["plays"][0].__setitem__("explanation", "see C:\\Users\\x\\file"), "path"),
    ],
)
def test_the_consumer_refuses_a_shared_cut_that_regrows_money(mutate: Any, needle: str) -> None:
    d = _shared()
    mutate(d)
    with pytest.raises(DeskRefused, match=needle):
        check_desk(d)


def test_per_one_dollar_unit_text_is_allowed() -> None:
    d = _shared()
    d["plays"][0]["reason"] = "edge -0.1c per $1"
    assert money_fields(check_desk(d)) == []
