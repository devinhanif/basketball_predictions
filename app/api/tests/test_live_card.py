"""The live score card: the live box joined to stored lines, facts only. Synthetic stored game
(fixtures/live_card/stored_game.json) against the committed live capture fixture (LAL at ATL)."""

from __future__ import annotations

import copy
import json
import statistics
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from live_harness import DATE, EVENT, FIXTURE, copy_fixture

from parlayhitters_api import deps
from parlayhitters_api import live as L
from parlayhitters_api import live_card as C
from parlayhitters_api.main import create_app
from parlayhitters_api.store import store_key

CARD_FIX = FIXTURE.parent / "live_card"
SRC = FIXTURE / f"live_{DATE}" / "espn_summary" / EVENT
NAMES = sorted(p.name for p in SRC.iterdir())  # pre, 2 early, mid, final
STORED = json.loads((CARD_FIX / "stored_game.json").read_text())


class FakeStore:
    """The two calls the card makes: slate(date) and nothing else."""

    def __init__(self, game: dict[str, Any] | None = STORED) -> None:
        self.game = game

    def slate(self, date: str) -> dict[str, Any] | None:
        if self.game is None or date != self.game["date"]:
            return None
        return {"date": date, "games": [self.game]}


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    C._static_cache.clear()
    C._snap_cache.clear()
    C._load_started.clear()
    C._load_failed.clear()


@pytest.fixture
def stored(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    """The export store already loaded (what a running server has after its first request)."""
    fs = FakeStore()
    monkeypatch.setitem(deps._cache, "store", fs)
    monkeypatch.setitem(deps._cache, "key", store_key())
    return fs


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    copy_fixture(tmp_path)
    monkeypatch.setenv(L.LIVE_ENV, str(tmp_path))
    monkeypatch.setenv(L.CHECKPOINT_ENV, str(tmp_path / "none.json"))
    return tmp_path


def state_of(name: str, date: str = DATE) -> L.GameState:
    ref = L.BodyRef(SRC / name, "capture", name.removesuffix(".json.gz"), 0)
    return L.load_state(ref, date, EVENT)


NOW = datetime(2026, 11, 10, 1, 41, 30, tzinfo=UTC)


def card(gs: L.GameState, model: dict[str, Any] | None = None) -> dict[str, Any]:
    stored = C.static_for("k", STORED)
    return C.build_card(gs, stored, "ok", NOW, model)


def row(c: dict[str, Any], name: str, stat: str) -> dict[str, Any]:
    return next(r for r in c["rows"] if r["name"] == name and r["stat"] == stat)


# ---------------------------------------------------------------- who is tracked


def test_tracks_every_posted_line_and_says_why_others_are_not() -> None:
    c = card(state_of(NAMES[3]))
    assert c["tracked"]["rows"] == 15 and c["tracked"]["players"] == 11
    assert c["tracked"]["reference_only"] == 2  # a reference line is not a line anyone set
    assert not any(r["name"] in ("Cameron Carr", "Zuby Ejiofor") for r in c["rows"])
    assert c["tracked"]["text"] == (
        "Tracking 15 posted lines for 11 players. 2 more stored lines are the model's own "
        "reference point, not a sportsbook line, so they are not tracked."
    )


def test_counts_come_from_the_live_box_and_the_need_is_arithmetic() -> None:
    gs = state_of(NAMES[3])
    c = card(gs)
    d = row(c, "Luka Doncic", "pts")
    box = next(r for r in gs.box.values() if r["name"] == "Luka Doncic")
    assert d["cur"] == box["pts"] == 22 and d["line"] == 30.5
    assert d["need"] == 9 and d["cleared"] is False  # floor(30.5) + 1 - 22
    assert d["minutes"] == box["min"] and d["fouls"] == box["pf"]
    assert d["status"] == "playing"
    a = row(c, "Luka Doncic", "ast")
    assert a["cur"] == 9 and a["line"] == 8.5 and a["cleared"] is True and a["need"] == 0
    assert row(c, "Nickeil Alexander-Walker", "pts")["cleared"] is True  # 18 on 17.5


def test_players_who_are_not_in_the_game_are_stated_not_dropped() -> None:
    c = card(state_of(NAMES[3]))
    nobody = row(c, "Synthetic Nobody", "pts")
    assert nobody["status"] == "no_box" and nobody["cur"] is None and nobody["need"] is None
    hardy = row(c, "Jaden Hardy", "pts")
    assert hardy["status"] == "dnp" and hardy["reason"]
    assert row(c, "Corey Kispert", "pts")["status"] == "dnp"


def test_status_words(monkeypatch: pytest.MonkeyPatch) -> None:
    gs = state_of(NAMES[3])
    box = next(r for r in gs.box.values() if r["name"] == "Collin Sexton")
    box["pf"] = 6
    assert row(card(gs), "Collin Sexton", "pts")["status"] == "fouled_out"
    box["pf"], box["min"] = 0, 0  # not yet entered
    assert row(card(gs), "Collin Sexton", "pts")["status"] == "not_entered"
    gs.state = "post"
    assert row(card(gs), "Collin Sexton", "pts")["status"] == "dnp"  # never played, game over


def test_regulation_time_left_comes_from_the_game_clock_only() -> None:
    c = card(state_of(NAMES[3]))  # Q3 3:39
    assert c["regulation_left_min"] == pytest.approx(48 - (24 + (720 - 219) / 60), abs=0.01)
    assert c["overtime"] is False
    assert c["as_of"]["label"] == "Q3 3:39"
    pre = card(state_of(NAMES[0]))
    assert pre["regulation_left_min"] == 48.0 and pre["as_of"]["label"] is None
    fin = card(state_of(NAMES[-1]))
    assert fin["regulation_left_min"] == 0.0 and fin["state"] == "post"


def test_overtime_is_flagged_with_no_time_left_in_regulation() -> None:
    gs = state_of(NAMES[3])
    gs.status = {**gs.status, "period": 5, "clock": "2:10", "elapsed_min": 48 + 2.83}
    c = card(gs)
    assert c["overtime"] is True and c["regulation_left_min"] == 0.0
    assert c["as_of"]["label"] == "OT1 2:10"


def test_freshness_fields_say_when_and_how_old() -> None:
    c = card(state_of(NAMES[3]))
    assert c["as_of"]["fetched_at"].startswith("2026-11-10T01:41:24")
    assert c["as_of"]["age_s"] == pytest.approx(6.0, abs=0.1)
    assert "about a minute behind" in c["feed_note"]


# ---------------------------------------------------------------- display locks


def test_a_final_game_never_carries_a_chance() -> None:
    for date in ("2026-10-01", DATE):  # an unlocked night and a locked one
        gs = state_of(NAMES[-1], date)
        assert gs.state == "post"
        assert all(r["chance"] is None for r in card(gs)["rows"])


def test_locked_night_in_play_shows_no_chance_and_says_so() -> None:
    c = card(state_of(NAMES[3]))  # 2026-11-09 is inside LINE_CAL_LIVE
    assert c["chance_locked"] is True
    assert all(r["chance"] is None and r["chance_source"] is None for r in c["rows"])


def test_unlocked_night_in_play_shows_calibrated_numbers_only() -> None:
    c = card(state_of(NAMES[3], "2026-10-01"))
    assert c["chance_locked"] is False
    assert row(c, "Luka Doncic", "pts")["chance"] == 0.54
    assert row(c, "Luka Doncic", "pts")["chance_source"] == "line_cal"
    # no calibrated value: nothing, never the raw model number
    assert row(c, "Luka Doncic", "reb")["chance"] is None
    shown = [r for r in c["rows"] if r["chance"] is not None]
    assert {r["chance"] for r in shown} == {0.54, 0.47, 0.41, 0.5, 0.52, 0.44}
    assert "p_over_raw" not in json.dumps(c) and "p_raw" not in json.dumps(c)


def test_before_tip_the_calibrated_chance_shows_even_on_a_locked_night() -> None:
    pre = state_of(NAMES[0])
    assert pre.state == "pre"
    c = card(pre)
    assert row(c, "Luka Doncic", "pts")["chance"] == 0.54
    assert row(c, "Luka Doncic", "reb")["chance"] is None


def test_the_payload_has_no_probability_computed_here() -> None:
    c = card(state_of(NAMES[3], "2026-10-01"))
    keys = {k for r in c["rows"] for k in r}
    assert not {"p_over", "p_hit", "prob", "probability", "likelihood", "p_model"} & keys


# ---------------------------------------------------------------- routes


def test_card_route(root: Path, stored: FakeStore) -> None:
    with TestClient(create_app()) as cl:
        r = cl.get(f"/api/live/{EVENT}/card")
        assert r.status_code == 200
        body = r.json()
        assert body["game_id"] == EVENT and body["lines_state"] == "ok"
        assert body["tracked"]["rows"] == 15
        assert body["state"] == "post"  # the newest fixture body is the final


def test_unknown_game_is_a_typed_error_not_a_sample(root: Path, stored: FakeStore) -> None:
    with TestClient(create_app()) as cl:
        r = cl.get("/api/live/999999999/card")
        assert r.status_code == 404 and r.json()["code"] == "no_live_capture"
        assert r.json()["field"] == "game_id"
        assert cl.get("/api/live/not-an-id/card").status_code in (404, 422)


def test_no_stored_game_is_said_plainly(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(deps._cache, "store", FakeStore(None))
    monkeypatch.setitem(deps._cache, "key", store_key())
    with TestClient(create_app()) as cl:
        b = cl.get(f"/api/live/{EVENT}/card").json()
        assert b["lines_state"] == "no_stored_game" and b["rows"] == []
        assert "No stored lines" in b["tracked"]["text"]


def test_a_cold_store_is_never_waited_on(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = threading.Event()
    fs = FakeStore()

    def slow() -> FakeStore:
        gate.wait(5)
        deps._cache["store"], deps._cache["key"] = fs, store_key()
        return fs

    monkeypatch.setattr(deps, "get_store", slow)
    deps._cache.clear()
    with TestClient(create_app()) as cl:
        t0 = time.perf_counter()
        b = cl.get(f"/api/live/{EVENT}/card").json()
        assert time.perf_counter() - t0 < 1.0  # answered while the export was still loading
        assert b["lines_state"] == "loading" and b["rows"] == []
        assert b["away"]["abbr"] == "LAL"  # the live facts are all there
        gate.set()
        for _ in range(50):
            time.sleep(0.05)
            b = cl.get(f"/api/live/{EVENT}/card").json()
            if b["lines_state"] == "ok":
                break
        assert b["lines_state"] == "ok" and b["tracked"]["rows"] == 15
    deps._cache.clear()


def test_a_refused_export_says_unavailable(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise RuntimeError("refused")

    monkeypatch.setattr(deps, "get_store", boom)
    deps._cache.clear()
    with TestClient(create_app()) as cl:
        cl.get(f"/api/live/{EVENT}/card")
        for _ in range(50):
            time.sleep(0.05)
            b = cl.get(f"/api/live/{EVENT}/card").json()
            if b["lines_state"] != "loading":
                break
        assert b["lines_state"] == "unavailable" and b["rows"] == []


def test_slate_cards_route(root: Path, stored: FakeStore) -> None:
    with TestClient(create_app()) as cl:
        b = cl.get(f"/api/live/{DATE}/cards").json()
        assert b["date"] == DATE
        ids = [g["game_id"] for g in b["games"]]
        assert EVENT in ids
        mine = next(g for g in b["games"] if g["game_id"] == EVENT)
        assert mine["tracked"]["rows"] == 15
        # a game on the capture with no stored game: no rows, stated
        other = [g for g in b["games"] if g["game_id"] != EVENT]
        assert all(g["lines_state"] == "no_stored_game" for g in other)
        assert cl.get("/api/live/2026-01-01/cards").json()["games"] == []


def test_the_card_is_fast_with_the_store_loaded(root: Path, stored: FakeStore) -> None:
    with TestClient(create_app()) as cl:
        cl.get(f"/api/live/{EVENT}/card")  # first read parses the body
        ts = []
        for _ in range(30):
            t0 = time.perf_counter()
            assert cl.get(f"/api/live/{EVENT}/card").status_code == 200
            ts.append((time.perf_counter() - t0) * 1000)
        # the app's target is 50 ms; this is a loose guard for a busy shared machine
        assert statistics.median(ts) < 100, ts


def test_the_route_is_get_only(root: Path, stored: FakeStore) -> None:
    with TestClient(create_app()) as cl:
        assert cl.post(f"/api/live/{EVENT}/card").status_code in (404, 405)
        assert cl.put(f"/api/live/{DATE}/cards").status_code in (404, 405)


# ---------------- step 2: the in-game model, behind a constant (off)

SNAP_V2 = CARD_FIX / "checkpoint_v2.json"


def test_the_constant_is_off_and_nothing_in_game_is_read(
    root: Path, stored: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert C.INGAME_MODEL_CHANCE_ON is False
    monkeypatch.setenv(L.CHECKPOINT_ENV, str(SNAP_V2))

    def never(*a: Any, **k: Any) -> None:
        raise AssertionError("the version-2 snapshot was read while the constant is off")

    monkeypatch.setattr(C, "read_ingame_v2", never)
    monkeypatch.setattr("parlayhitters_api.live_card_routes.read_ingame_v2", never)
    with TestClient(create_app()) as cl:
        b = cl.get(f"/api/live/{EVENT}/card").json()
    assert b["model_on"] is False
    assert all("in_game" not in r for r in b["rows"])
    assert "Experimental" not in json.dumps(b)


def test_reader_v2_gives_rows_with_age_and_the_exact_label() -> None:
    m = C.read_ingame_v2(EVENT, "in", NOW, SNAP_V2)
    assert m["available"] is True and m["age_s"] == 90.0 and m["clock"] == "3:39"
    c = card(state_of(NAMES[3]), m)
    d = row(c, "Luka Doncic", "pts")["in_game"]
    assert d["chance"] == 0.62 and d["label"] == "Experimental, not yet tested"
    assert d["age_s"] == 90.0 and d["as_of"] == "Q3 3:39"
    assert row(c, "Luka Doncic", "ast")["in_game"]["chance"] == 0.31
    # the row whose k is not this line's over (floor + 1) gets nothing
    assert "in_game" not in row(c, "Onyeka Okongwu", "reb")
    assert "in_game" not in row(c, "Luka Doncic", "reb")
    assert C.INGAME_LABEL == "Experimental, not yet tested"
    assert c["model_on"] is True


def test_reader_v2_shows_nothing_when_old_wrong_version_or_not_in_play(tmp_path: Path) -> None:
    late = NOW + timedelta(seconds=C.SNAPSHOT_STATE_MAX_AGE_S + 1)
    assert C.read_ingame_v2(EVENT, "in", late, SNAP_V2) == {
        "available": False,
        "reason": "snapshot too old",
    }
    just = NOW + timedelta(seconds=C.SNAPSHOT_STATE_MAX_AGE_S)
    assert C.read_ingame_v2(EVENT, "in", just, SNAP_V2)["available"] is True
    for state in ("pre", "post"):
        assert C.read_ingame_v2(EVENT, state, NOW, SNAP_V2)["available"] is False
    v1 = tmp_path / "v1.json"
    v1.write_text(json.dumps({**json.loads(SNAP_V2.read_text()), "version": 1}))
    assert C.read_ingame_v2(EVENT, "in", NOW, v1)["reason"] == "no version-2 snapshot"
    assert C.read_ingame_v2(EVENT, "in", NOW, tmp_path / "missing.json")["available"] is False
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert C.read_ingame_v2(EVENT, "in", NOW, bad)["available"] is False
    assert C.read_ingame_v2("401909999", "in", NOW, SNAP_V2)["reason"] == "no rows for this game"


def test_on_state_through_the_route_and_a_final_game_shows_none(
    root: Path, stored: FakeStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snap = json.loads(SNAP_V2.read_text())
    snap["generated_at"] = datetime.now(UTC).isoformat()
    snap["games"][EVENT]["known_at"] = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    path = tmp_path / "v2.json"
    path.write_text(json.dumps(snap))
    monkeypatch.setenv(L.CHECKPOINT_ENV, str(path))
    monkeypatch.setattr(C, "INGAME_MODEL_CHANCE_ON", True)
    with TestClient(create_app()) as cl:
        b = cl.get(f"/api/live/{EVENT}/card").json()
    assert (
        b["state"] == "post" and b["model_on"] is False
    )  # final: no model number beside the outcome
    assert all("in_game" not in r for r in b["rows"])


def test_the_model_has_no_running_score_anywhere() -> None:
    m = C.read_ingame_v2(EVENT, "in", NOW, SNAP_V2)
    c = card(state_of(NAMES[3]), m)
    text = json.dumps(c).lower()
    for word in (
        "hit_rate",
        "hit rate",
        "record",
        "tally",
        "versus the market",
        "vs market",
        "wins",
        "accuracy",
    ):
        assert word not in text, word
    assert not any(k in c for k in ("model_record", "score", "tally"))


def test_v1_reader_is_unchanged_by_this_module() -> None:
    """The existing version-1 reader still refuses a version-2 file (its documented behaviour)."""
    d = L.read_checkpoint(EVENT, "in", SNAP_V2)
    assert d["available"] is False and d["reason"] == "no checkpoint snapshot"


def test_card_is_deterministic_and_does_not_mutate_the_stored_game() -> None:
    before = copy.deepcopy(STORED)
    a = card(state_of(NAMES[3]))
    b = card(state_of(NAMES[3]))
    assert a == b and STORED == before
