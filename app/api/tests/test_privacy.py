"""Desk data stays private: no money field in any response a non-private request can get, and the
private cut only for PH_PRIVATE=1 AND a direct local request (loopback peer, local Host, no proxy
header). Checked server-side, on every route the app serves."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api.main import create_app, reset_store

FIX = Path(__file__).resolve().parents[2] / "fixtures"
#: A key that names somebody's money. ``contracts`` is excluded when it is a list: there it is the
#: market's contract listings (title, ask, fee), not a count anybody holds.
MONEY_KEY = re.compile(r"usd|stake|bankroll|balance|pnl|kelly|wager|units|bet_size", re.I)
DESK_MONEY = ("stake_usd", "contracts", "fee_cents")


def _money(x: Any, where: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(x, dict):
        for k, v in x.items():
            if v is not None and MONEY_KEY.search(str(k)):
                found.append(f"{where}.{k}")
            if k == "contracts" and v is not None and not isinstance(v, list):
                found.append(f"{where}.{k}")
            found += _money(v, f"{where}.{k}")
    elif isinstance(x, list):
        for i, v in enumerate(x):
            found += _money(v, f"{where}[{i}]")
    return found


def _crawl(c: TestClient) -> Iterator[tuple[str, Any]]:
    fixed = [
        "/api/health", "/api/meta", "/api/slates", "/api/parlay-of-the-day", "/api/evidence",
        "/api/track-record/calibration", "/api/track-record/guess?n=50", "/api/contracts/top?n=50",
        "/api/desk", "/api/top-parlays/2024-01-01", "/api/top-parlays/2024-01-02",
    ]  # fmt: skip
    for u in fixed:
        yield u, c.get(u).json()
    for s in c.get("/api/slates").json():
        d = s["date"]
        for u in (f"/api/slates/{d}", f"/api/slates/{d}/swipe?n=60", f"/api/slates/{d}/results"):
            yield u, c.get(u).json()
        for g in c.get(f"/api/slates/{d}").json()["games"]:
            gid = g["game_id"]
            for u in (f"/api/games/{gid}", f"/api/games/{gid}/scenarios", f"/api/knows/{gid}"):
                yield u, c.get(u).json()
            game = c.get(f"/api/games/{gid}").json()
            for pl in game["players"][:3]:
                for st, pr in pl["props"].items():
                    base = f"{gid}/{pl['player_id']}/{st}"
                    for u in (
                        f"/api/props/{base}?line={pr['line']}", f"/api/card/{base}",
                        f"/api/knows/{base}", f"/api/breakdown/{base}",
                        f"/api/track-record/guess/{base}", f"/api/edges/{base}",
                    ):  # fmt: skip
                        yield u, c.get(u).json()


@pytest.fixture
def public_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PH_PRIVATE", raising=False)
    monkeypatch.setenv("APP_DESK_FILE", str(FIX / "desk_fixture_private.json"))
    monkeypatch.setenv("APP_DESK_SHARED_FILE", str(FIX / "desk_fixture_shared.json"))


def test_no_money_field_on_any_route_of_the_export(
    public_env: None, export_client: TestClient
) -> None:
    seen = 0
    for url, body in _crawl(export_client):
        seen += 1
        assert not _money(body), (url, _money(body))
    assert seen > 50


def test_no_money_field_on_any_route_of_the_fixtures(public_env: None) -> None:
    reset_store()
    with TestClient(create_app()) as c:
        seen = 0
        for url, body in _crawl(c):
            seen += 1
            assert not _money(body), (url, _money(body))
    reset_store()
    assert seen > 50


def test_shared_desk_money_fields_are_null(public_env: None, export_client: TestClient) -> None:
    d = export_client.get("/api/desk").json()
    assert d["privacy"] == "shared" and d.get("balance") is None
    assert d["plays"], "the shared fixture should carry plays to check"
    for p in d["plays"]:
        assert all(p.get(k) is None for k in DESK_MONEY), p
        assert (p.get("result") or {}).get("pnl_usd") is None
    assert all(day.get("pnl_usd") is None for day in d.get("daily", []))


@pytest.mark.parametrize(
    "peer", [("192.168.1.20", 50000), ("10.0.0.5", 1), ("testclient", 50000), ("172.16.0.9", 1)]
)
def test_private_cut_needs_a_loopback_peer_whatever_the_host_header_says(
    monkeypatch: pytest.MonkeyPatch, export_dir: Path, peer: tuple[str, int]
) -> None:
    monkeypatch.setenv("APP_EXPORT_DIR", str(export_dir))
    monkeypatch.setenv("PH_PRIVATE", "1")
    monkeypatch.setenv("APP_DESK_FILE", str(FIX / "desk_fixture_private.json"))
    monkeypatch.setenv("APP_DESK_SHARED_FILE", str(FIX / "desk_fixture_shared.json"))
    reset_store()
    with TestClient(create_app(), client=peer) as c:
        for host in ("localhost", "127.0.0.1:8000", "[::1]:8000"):
            r = c.get("/api/desk", headers={"host": host})
            assert r.status_code == 200 and r.json()["privacy"] == "shared", (peer, host)
    reset_store()


@pytest.mark.parametrize("flag", [None, "0", "true", "yes", " 1", ""])
def test_private_cut_needs_exactly_ph_private_1(
    monkeypatch: pytest.MonkeyPatch, export_client: TestClient, flag: str | None
) -> None:
    if flag is None:
        monkeypatch.delenv("PH_PRIVATE", raising=False)
    else:
        monkeypatch.setenv("PH_PRIVATE", flag)
    monkeypatch.setenv("APP_DESK_FILE", str(FIX / "desk_fixture_private.json"))
    monkeypatch.setenv("APP_DESK_SHARED_FILE", str(FIX / "desk_fixture_shared.json"))
    r = export_client.get("/api/desk", headers={"host": "127.0.0.1:8000"})
    assert r.json()["privacy"] == "shared"


def test_ipv4_mapped_loopback_peer_counts_as_local(
    monkeypatch: pytest.MonkeyPatch, export_dir: Path
) -> None:
    monkeypatch.setenv("APP_EXPORT_DIR", str(export_dir))
    monkeypatch.setenv("PH_PRIVATE", "1")
    monkeypatch.setenv("APP_DESK_FILE", str(FIX / "desk_fixture_private.json"))
    reset_store()
    for peer in (("::1", 1), ("::ffff:127.0.0.1", 1)):
        with TestClient(create_app(), client=peer) as c:
            r = c.get("/api/desk", headers={"host": "localhost:8000"})
            assert r.json()["privacy"] == "private", peer
    reset_store()
