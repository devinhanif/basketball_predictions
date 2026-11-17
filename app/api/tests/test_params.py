"""The one validation layer for string path parameters (params.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parlayhitters_api.params import contained, is_date, is_event_id, is_game_id, is_stat


@pytest.mark.parametrize("v", ["2024-11-12", "2026-02-28", "2024-02-29"])
def test_real_dates_pass(v: str) -> None:
    assert is_date(v)


@pytest.mark.parametrize(
    "v", ["2025-02-29", "2024-13-01", "2024-00-10", "0000-00-00", "2024-1-1", "2024-11-12 ", ""]
)
def test_non_dates_fail(v: str) -> None:
    assert not is_date(v)


def test_ids_and_stats() -> None:
    assert is_game_id("0022400001") and not is_game_id("a" * 33) and not is_game_id("../x")
    assert is_event_id("401909952") and not is_event_id("40190") and not is_event_id("4" * 13)
    assert is_stat("pts") and is_stat("fg3m") and not is_stat("PTS") and not is_stat("p ts")


@pytest.mark.parametrize(
    ("url", "code", "field", "detail"),
    [
        ("/api/slates/2024-02-30", "not_a_date", "date", "no such slate"),
        ("/api/slates/2024-02-30/swipe", "not_a_date", "date", "no such slate"),
        ("/api/top-parlays/2024-02-30", "not_a_date", "date", "no top parlays file for that date"),
        ("/api/live/2024-02-30", "not_a_date", "date", "not a date"),
        ("/api/games/" + "9" * 40, "unknown_game", "game_id", "no such game"),
        ("/api/edges/not%20an%20id/1/pts", "unknown_game", "game_id", "no such game"),
        ("/api/card/0022400001/1628369/P%20TS", "unknown_stat", "stat", "unknown stat"),
    ],
)
def test_bad_strings_are_refused_with_the_old_status(
    client: TestClient, url: str, code: str, field: str, detail: str
) -> None:
    r = client.get(url)
    assert r.status_code == 404
    body = r.json()
    assert (body["code"], body["field"], body["detail"]) == (code, field, detail)


def test_a_malformed_date_is_refused_before_the_store_loads(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import parlayhitters_api.deps as deps
    import parlayhitters_api.main as m

    def boom() -> object:
        raise AssertionError("the store was loaded for a request that should be refused first")

    m.reset_store()
    monkeypatch.setattr(deps, "load_store", boom)
    assert client.get("/api/slates/not-a-date").json()["code"] == "not_a_date"
    assert client.get("/api/games/..%5C..%5Cetc").json()["code"] == "unknown_game"
    m.reset_store()


def test_contained(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "a" / "link").symlink_to(tmp_path / "outside")
    assert contained(tmp_path / "a", tmp_path / "a" / "x.json")
    assert not contained(tmp_path / "a", tmp_path / "a" / ".." / "outside" / "x.json")
    assert not contained(tmp_path / "a", tmp_path / "a" / "link" / "x.json")


def test_live_paths_cannot_be_built_from_unchecked_parts(tmp_path: Path) -> None:
    from parlayhitters_api import live

    with pytest.raises(ValueError):
        live.event_dirs(tmp_path, "../../etc", "401909952")
    with pytest.raises(ValueError):
        live.event_dirs(tmp_path, "2026-11-09", "../401909952")
    assert live.locate_event(tmp_path, "../../x") is None
    assert live.list_event_ids(tmp_path, "..") == []


def test_top_parlays_refuses_a_file_that_points_outside_its_directory(tmp_path: Path) -> None:
    from parlayhitters_api.top_parlays import TopParlaysMissing, load_top_parlays

    base = tmp_path / "tp"
    base.mkdir()
    outside = tmp_path / "elsewhere.json"
    outside.write_text("{}")
    (base / "top_parlays_2024-01-01.json").symlink_to(outside)
    with pytest.raises(TopParlaysMissing):
        load_top_parlays("2024-01-01", base)


def test_the_store_patch_point_is_the_one_get_store_uses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards the tests above: patching deps.load_store really reaches get_store."""
    import parlayhitters_api.deps as deps
    import parlayhitters_api.main as m

    calls: list[int] = []

    def fake() -> object:
        calls.append(1)
        raise RuntimeError("patched")

    m.reset_store()
    monkeypatch.setattr(deps, "load_store", fake)
    with pytest.raises(RuntimeError, match="patched"):
        m.get_store()
    assert calls == [1]
    m.reset_store()
