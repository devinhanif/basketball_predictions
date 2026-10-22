from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api import live, scan

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "live"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
TAGS = ("24NOV12",)


def series(name: str) -> list[dict[str, Any]]:
    d: dict[str, Any] = json.loads((FIX / f"kalshi_series_{name}.json").read_text())
    return list(d["markets"])


GOOD: dict[str, Any] = {
    "name": "Tonight",
    "contracts": 14.99,
    "paid": 3.36,
    "combo_price": 0.224,
    "legs": [
        {"label": "Paolo Banchero 45+ points", "kind": "player", "player": "Paolo Banchero",
         "stat": "pts", "threshold": 45},
        {"label": "Golden State wins", "kind": "win", "team": "GS"},
        {"label": "Over 224.5", "kind": "total", "threshold": 224.5, "side": "over"},
        {"label": "DEN -5.5", "kind": "spread", "team": "DEN", "threshold": -5.5},
    ],
}  # fmt: skip


def bad(**change: Any) -> dict[str, Any]:
    d = copy.deepcopy(GOOD)
    d.update(change)
    return d


def test_valid_import_round_trips() -> None:
    out = scan.validate_import(copy.deepcopy(GOOD))
    assert out["contracts"] == 14.99 and out["legs"][2]["side"] == "over"
    assert scan.validate_import(json.loads(json.dumps(out))) == out


@pytest.mark.parametrize(
    "d",
    [
        bad(extra=1),
        bad(contracts=0),
        bad(contracts="3"),
        bad(contracts=True),
        bad(paid=-1),
        bad(combo_price=1.5),
        bad(legs=[]),
        bad(legs=[GOOD["legs"][0]] * 13),
        bad(name=""),
        bad(legs=[{**GOOD["legs"][0], "stat": "blk"}]),
        bad(legs=[{**GOOD["legs"][0], "threshold": None}]),
        bad(legs=[{**GOOD["legs"][1], "side": "over"}]),
        bad(legs=[{**GOOD["legs"][1], "team": "atlanta"}]),
        bad(legs=[{**GOOD["legs"][1], "threshold": 1}]),
        bad(legs=[{**GOOD["legs"][0], "ticker": "a b"}]),
        bad(legs=[{**GOOD["legs"][0], "entry_price": 3}]),
        bad(legs=[{**GOOD["legs"][0], "kind": "prop"}]),
        bad(legs=["x"]),
    ],
)
def test_invalid_imports_refused(d: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        scan.validate_import(d)


def test_not_an_object_refused() -> None:
    values: list[Any] = [[], "x", None, 3]
    for v in values:
        with pytest.raises(ValueError):
            scan.validate_import(v)


def test_extract_json_strips_a_fence() -> None:
    assert scan.extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_resolution_on_recorded_markets() -> None:
    lists = {"KXNBAPTS": series("KXNBAPTS"), "KXNBAGAME": series("KXNBAGAME")}
    banchero = scan.validate_leg(GOOD["legs"][0])
    assert scan.resolve_ticker(banchero, lists, TAGS) == "KXNBAPTS-24NOV12ATLORL-ORLPBANCHERO5-45"
    assert scan.resolve_ticker({**banchero, "threshold": 44}, lists, TAGS) is None  # no such rung
    assert scan.resolve_ticker(banchero, lists, ("24NOV13",)) is None  # not that night
    assert scan.resolve_ticker({**banchero, "player": "Nobody Here"}, lists, TAGS) is None
    win = scan.validate_leg(GOOD["legs"][1])  # "GS" is an alias for GSW
    assert scan.resolve_ticker(win, lists, TAGS) == "KXNBAGAME-24NOV12GSWDEN-GSW"
    spread = scan.validate_leg(GOOD["legs"][3])
    assert scan.resolve_ticker(spread, lists, TAGS) is None  # left for the viewer


def test_existing_ticker_is_kept() -> None:
    leg = scan.validate_leg({**GOOD["legs"][0], "ticker": "KX-OWN-1"})
    assert scan.resolve_ticker(leg, {}, TAGS) == "KX-OWN-1"


def test_date_tags() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    t = datetime(2026, 10, 22, 1, 30, tzinfo=ZoneInfo("UTC"))  # still Oct 21 in New York
    assert scan.date_tags(t) == ("26OCT21", "26OCT20")


# ------------------------------------------------------------------ endpoint


def test_503_without_key_with_the_exact_message(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post(
        "/api/scan/kalshi-screenshot", content=PNG, headers={"content-type": "image/png"}
    )
    assert r.status_code == 503
    assert r.json()["detail"] == (
        "screenshot scan needs ANTHROPIC_API_KEY; or paste the screenshot to Claude and use an import link"
    )


def test_type_and_size_limits(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    post = client.post
    assert (
        post(
            "/api/scan/kalshi-screenshot", content=b"hi", headers={"content-type": "text/plain"}
        ).status_code
        == 415
    )
    assert (
        post(
            "/api/scan/kalshi-screenshot",
            content=b"not a png",
            headers={"content-type": "image/png"},
        ).status_code
        == 415
    )
    assert (
        post(
            "/api/scan/kalshi-screenshot", content=PNG, headers={"content-type": "image/svg+xml"}
        ).status_code
        == 415
    )
    big = PNG + b"\x00" * (5 * 1024 * 1024)
    assert (
        post(
            "/api/scan/kalshi-screenshot", content=big, headers={"content-type": "image/png"}
        ).status_code
        == 413
    )


def test_success_path_validates_resolves_and_never_leaks_the_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret")
    seen: dict[str, Any] = {}

    def fake_ask(image: bytes, media: str, key: str) -> str:
        seen.update(image=image, media=media, key=key)
        return (
            "```json\n" + json.dumps({**GOOD, "max_payout": 14.99, "current_value": 4.1}) + "\n```"
        )

    monkeypatch.setattr(scan, "call_claude", fake_ask)
    monkeypatch.setattr(
        scan, "fetch_series", lambda s: series(s) if s in ("KXNBAPTS", "KXNBAGAME") else []
    )
    monkeypatch.setattr(scan, "date_tags", lambda now=None: TAGS)
    with caplog.at_level(logging.DEBUG):
        r = client.post(
            "/api/scan/kalshi-screenshot", content=PNG, headers={"content-type": "image/png"}
        )
    assert r.status_code == 200
    body = r.json()
    assert seen["image"] == PNG and seen["media"] == "image/png"
    assert body["screen"] == {"max_payout": 14.99, "current_value": 4.1}
    assert body["resolved"] == [True, True, False, False]
    assert body["parlay"]["legs"][0]["ticker"].endswith("ORLPBANCHERO5-45")
    assert "sk-test-secret" not in r.text and "sk-test-secret" not in caplog.text


def test_reading_that_does_not_validate_is_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    for reply in (
        "I cannot read this",
        json.dumps({**GOOD, "legs": []}),
        json.dumps({**GOOD, "surprise": 1}),
    ):
        monkeypatch.setattr(scan, "call_claude", lambda i, m, k, r=reply: r)
        r = client.post(
            "/api/scan/kalshi-screenshot", content=PNG, headers={"content-type": "image/png"}
        )
        assert r.status_code == 422


def test_vision_outage_is_a_502_without_detail(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    def down(i: bytes, m: str, k: str) -> str:
        raise live.SourceDown("URLError")

    monkeypatch.setattr(scan, "call_claude", down)
    r = client.post(
        "/api/scan/kalshi-screenshot", content=PNG, headers={"content-type": "image/png"}
    )
    assert r.status_code == 502 and "URLError" not in r.text
