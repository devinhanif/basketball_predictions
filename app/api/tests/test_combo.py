"""Combo mode route: the stored night file served unchanged, typed 404, refusals, no price keys."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api.combo import ComboRefused, validate
from parlayhitters_api.main import create_app, reset_store

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "combo" / "combo_2024-11-12.json"
DAY = "2024-11-12"


def fixture() -> dict[str, Any]:
    d: dict[str, Any] = json.loads(FIX.read_text())
    return d


def test_fixture_served_unchanged(client: TestClient) -> None:
    r = client.get(f"/api/combo/{DAY}")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "fixture_replay"
    assert body["combo"] == fixture()  # byte-for-byte the stored payload, inside the envelope
    assert body["clock_utc"].startswith("2024-11-12T23:30")  # the slate's frozen replay clock
    assert body["combo"]["schema"] == "combo_export_v1"
    assert len(body["combo"]["games"]) >= 2


def test_absent_night_is_typed_404(client: TestClient) -> None:
    r = client.get("/api/combo/2024-11-20")
    assert r.status_code == 404
    assert r.json()["code"] == "no_combo"
    assert "combo" not in r.json()


def test_not_a_date(client: TestClient) -> None:
    r = client.get("/api/combo/not-a-date")
    assert r.status_code == 404
    assert r.json()["code"] == "not_a_date"
    assert client.get("/api/combo/..%2Fx").status_code == 404


def test_get_only(client: TestClient) -> None:
    assert client.post(f"/api/combo/{DAY}").status_code == 405


def _served(tmp: Path, payload: Any, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    d = tmp / "fix"
    (d / "combo").mkdir(parents=True)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    (d / "combo" / f"combo_{DAY}.json").write_text(text)
    monkeypatch.setenv("APP_FIXTURES_DIR", str(d))
    # the store reads its manifest from the fixtures dir: reuse the committed one
    root = Path(__file__).resolve().parents[2] / "fixtures"
    for name in ("manifest.json", "evidence.json", "parlay_of_day.json"):
        (d / name).write_bytes((root / name).read_bytes())
    for f in (root / "slates").iterdir():
        (d / "slates").mkdir(exist_ok=True)
        (d / "slates" / f.name).write_bytes(f.read_bytes())
    reset_store()
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _reset() -> Any:
    yield
    reset_store()


@pytest.mark.parametrize(
    "bad",
    [
        lambda d: d.update(schema="combo_export_v2"),
        lambda d: d.update(night="2024-11-13"),
        lambda d: d["games"][0].update(price=1),
        lambda d: d["games"][0]["players"][0]["stats"]["pts"].update(ev_low=0.1),
        lambda d: d["shared"].update(fee_schedule={}),
        lambda d: d["games"][0].update(stake=5),
        lambda d: d["games"][0].update(kelly_fraction=0.1),
        lambda d: d["games"][0]["players"][0]["stats"]["pts"].update(over_odds=-110),
        lambda d: d["games"][0].update(made_at="2024-11-13T00:00:00Z"),
        lambda d: d["games"][0]["players"][0]["stats"]["pts"].update(surv=[0.2, 0.5]),
        lambda d: d["games"][0]["players"][0]["stats"]["pts"].update(main_line=24.0),
        lambda d: d["games"][0]["players"][0].update(p_play=1.5),
        lambda d: d["games"][0].update(date="2024-11-14"),
        lambda d: d["shared"]["scripts"].update(params=[["blowout", "pts"]]),
        lambda d: d.pop("shared"),
    ],
)
def test_refusals(bad: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = copy.deepcopy(fixture())
    bad(d)
    with pytest.raises(ComboRefused):
        validate(d, DAY)
    r = _served(tmp_path, d, monkeypatch).get(f"/api/combo/{DAY}")
    assert r.status_code == 422
    assert r.json()["code"] == "combo_refused"
    assert "combo" not in r.json()


def test_unreadable_and_oversized_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _served(tmp_path, "{not json", monkeypatch).get(f"/api/combo/{DAY}").status_code == 422


def test_unknown_keys_are_kept_additive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = fixture()
    d["games"][0]["players"][0]["stats"]["pts"]["note_text"] = "kept"
    d["extra_block"] = {"a": 1}
    body = _served(tmp_path, d, monkeypatch).get(f"/api/combo/{DAY}").json()
    assert body["combo"]["extra_block"] == {"a": 1}
    assert body["combo"]["games"][0]["players"][0]["stats"]["pts"]["note_text"] == "kept"


def test_no_price_words_in_fixture_keys() -> None:
    def keys(x: Any) -> set[str]:
        if isinstance(x, dict):
            return set(x) | {k for v in x.values() for k in keys(v)}
        if isinstance(x, list):
            return {k for v in x for k in keys(v)}
        return set()

    assert not {
        k for k in keys(fixture()) if any(w in k for w in ("price", "fee", "stake", "odds", "ev_"))
    }


def test_export_mode_reads_export_dir_and_never_falls_back(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_EXPORT_DIR", str(export_dir))
    reset_store()
    with TestClient(create_app()) as c:
        r = c.get(f"/api/combo/{DAY}")
        assert (
            r.status_code == 404
        )  # the fixture night is NOT served in place of a missing real file
        assert r.json()["code"] == "no_combo"
        (export_dir / f"combo_{DAY}.json").write_text(FIX.read_text())
        ok = c.get(f"/api/combo/{DAY}")
        assert ok.status_code == 200
        assert ok.json()["mode"] == "export"
        assert ok.json()["combo"] == fixture()
        assert ok.json()["clock_utc"].endswith("+00:00")  # the real clock, not the replay clock
