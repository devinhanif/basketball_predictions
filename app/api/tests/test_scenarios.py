# ruff: noqa: E501
"""'If a teammate sits': the API says what the evidence supports (T526 / T526-a) and no more.

Points: a direction per teammate and one team-level number. Rebounds: a direction, size marked uncertain, no
size sent. Assists and threes: never sent, even if a table holds them. No price anywhere. A finished game on a
locked night gets no scenario. Where the export has none (or its step failed) the API says so.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from conftest import EXPORT_DAY, EXPORT_SMALL, edit_db, seal
from fastapi.testclient import TestClient

from parlayhitters_api import scenarios as S
from parlayhitters_api.main import create_app, reset_store

GAME = "0022400023"  # tonight in the small export: home 1610612760 vs away 1610612738, not final
HOME, AWAY = 1610612760, 1610612738
COLS = (
    "schema_version INTEGER, made_at TIMESTAMP, game_id VARCHAR, team_id BIGINT, sitter_id BIGINT, "
    "sitter_status VARCHAR, player_id BIGINT, stat VARCHAR, base_mean DOUBLE, scn_mean DOUBLE, "
    "delta_mean DOUBLE, base_q10 DOUBLE, base_q50 DOUBLE, base_q90 DOUBLE, scn_q10 DOUBLE, "
    "scn_q50 DOUBLE, scn_q90 DOUBLE, p_play DOUBLE, rule_word VARCHAR"
)
OK_INFO = {
    "status": "ok", "reason": None, "n_rows": 0, "n_scenarios": 1, "n_games": 1,
    "rule_sha8": "c23f7b36", "stats": ["pts", "reb"], "seconds": 1.0,
}  # fmt: skip


def _players(team: int) -> list[int]:
    con = duckdb.connect(str(EXPORT_SMALL / "export.duckdb"), read_only=True)
    try:
        rows = con.execute(
            "SELECT DISTINCT player_id FROM prop_grids WHERE team_id = ? AND game_id = ? ORDER BY 1",
            [team, GAME],
        ).fetchall()
    finally:
        con.close()
    return [int(r[0]) for r in rows]


def _row(sitter: int, status: str, pid: int, stat: str, delta: float, p_play: float) -> str:
    return (
        f"(1, TIMESTAMP '2024-11-12 18:00:00', '{GAME}', {HOME}, {sitter}, '{status}', {pid}, "
        f"'{stat}', 10.0, {10.0 + delta}, {delta}, 5.0, 10.0, 15.0, 5.0, 10.0, 15.0, {p_play}, 'HELPS')"
    )


@pytest.fixture
def home() -> list[int]:
    return _players(HOME)


@pytest.fixture
def sits_client(
    export_dir: Path, home: list[int], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    s, a, b, c, d = home[:5]
    rows = [
        _row(s, "questionable", a, "pts", 1.2, 0.95),
        _row(s, "questionable", b, "pts", 0.6, 0.9),
        _row(s, "questionable", c, "pts", -0.02, 0.9),
        _row(s, "questionable", d, "pts", 0.9, 0.2),  # unlikely to play
        _row(s, "questionable", a, "reb", 0.4, 0.95),
        _row(s, "questionable", b, "reb", -0.3, 0.9),
        _row(s, "questionable", a, "ast", 0.7, 0.95),  # never sent
        _row(s, "questionable", a, "fg3m", 0.5, 0.95),  # never sent
    ]
    edit_db(
        export_dir,
        f"CREATE TABLE teammate_sits ({COLS})",
        "INSERT INTO teammate_sits VALUES " + ", ".join(rows),
    )
    seal(export_dir, teammate_sits={**OK_INFO, "n_rows": len(rows)})
    monkeypatch.setenv("APP_EXPORT_DIR", str(export_dir))
    reset_store()
    with TestClient(create_app()) as cl:
        yield cl
    reset_store()


def test_points_direction_and_one_team_number_reb_direction_only(
    sits_client: TestClient, home: list[int]
) -> None:
    r = sits_client.get(f"/api/games/{GAME}/scenarios")
    assert r.status_code == 200
    v = r.json()
    assert v["available"] and v["reason"] is None and v["made_at"] == "2024-11-12T18:00:00Z"
    assert len(v["sitters"]) == 1
    s = v["sitters"][0]
    assert s["player_id"] == home[0] and s["side"] == "home" and s["status"] == "questionable"
    # team number: sum of p_play * delta (1.2*.95 + .6*.9 - .02*.9 + .9*.2), and the likely-player mean
    assert s["pts"]["total"] == pytest.approx(1.14 + 0.54 - 0.018 + 0.18, abs=0.06)
    assert s["pts"]["per_player"] == pytest.approx((1.2 + 0.6 - 0.02) / 3, abs=0.06)
    assert s["pts"]["n_likely"] == 3 and s["pts"]["direction"] == "up"
    # per teammate: direction only, no size field
    by = {t["player_id"]: t for t in s["teammates"]}
    assert by[home[1]]["pts"] == "up" and by[home[3]]["pts"] == "flat"  # -0.02 is rounding
    assert by[home[4]]["pts"] == "up" and by[home[4]]["likely_plays"] is False
    assert by[home[1]]["reb"] == "up" and by[home[2]]["reb"] == "down"
    for t in s["teammates"]:
        assert set(t) == {"player_id", "name", "likely_plays", "pts", "reb"}
    # rebounds: direction and "uncertain size", no number
    assert s["reb"] == {"direction": "up", "uncertain_size": True}
    # evidence in plain words, and what the scenario does not model
    assert any("2023-24 and 2024-25 seasons" in e and "points" in e for e in v["evidence"])
    assert any("rebounds direction only" in e for e in v["evidence"])
    assert v["not_modelled"]


def _keys(x: Any) -> set[str]:
    if isinstance(x, dict):
        return set(x) | {k for v in x.values() for k in _keys(v)}
    if isinstance(x, list):
        return {k for v in x for k in _keys(v)}
    return set()


def test_assists_threes_prices_and_raw_means_never_leave(sits_client: TestClient) -> None:
    body = sits_client.get(f"/api/games/{GAME}/scenarios").json()
    # the only fields that can exist: no assist/three field, no per-player size, no mean, no price
    assert _keys(body["sitters"]) == {
        "player_id", "name", "team_id", "side", "status", "pts", "reb", "teammates",
        "direction", "total", "per_player", "n_likely", "uncertain_size", "likely_plays",
    }  # fmt: skip
    words = " ".join(json.dumps(s).lower() for s in body["sitters"])
    for banned in ("price", "odds", "stake", "p_over", "probab", "scn_", "base_", "delta"):
        assert banned not in words, banned


def test_other_game_and_unknown_game(sits_client: TestClient) -> None:
    other = [
        g["game_id"]
        for g in sits_client.get(f"/api/slates/{EXPORT_DAY}").json()["games"]
        if g["game_id"] != GAME
    ][0]
    v = sits_client.get(f"/api/games/{other}/scenarios").json()
    assert not v["available"] and "no scenarios for this game" in v["reason"].lower()
    assert v["sitters"] == [] and v["evidence"] == []
    assert sits_client.get("/api/games/nope/scenarios").status_code == 404


def test_read_only_routes(sits_client: TestClient) -> None:
    assert sits_client.post(f"/api/games/{GAME}/scenarios").status_code == 405
    assert sits_client.put(f"/api/games/{GAME}/scenarios").status_code == 405


def test_export_without_the_table_says_so(client: TestClient) -> None:
    v = client.get("/api/games/0022400015/scenarios")
    # the session client runs on the committed fixtures: no scenarios there, and it says so
    if v.status_code == 200:
        assert not v.json()["available"] and "no 'if a teammate sits'" in v.json()["reason"].lower()


@pytest.mark.parametrize(
    ("info", "text"),
    [
        ({"status": "absent", "reason": "no_model_cache"}, "saved model"),
        ({"status": "absent", "reason": "disabled"}, "switched off"),
        ({"status": "absent", "reason": "error:MemoryError"}, "that step failed"),
        ({"status": "absent", "reason": "something_new"}, "not built"),
    ],
)
def test_a_failed_or_absent_step_is_named(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch, info: dict[str, Any], text: str
) -> None:
    seal(export_dir, teammate_sits={**OK_INFO, **info, "n_rows": 0})
    monkeypatch.setenv("APP_EXPORT_DIR", str(export_dir))
    reset_store()
    try:
        with TestClient(create_app()) as cl:
            v = cl.get(f"/api/games/{GAME}/scenarios").json()
    finally:
        reset_store()
    assert not v["available"] and text in v["reason"].lower() and v["sitters"] == []


class _Store:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.sits_info = dict(OK_INFO)
        self.sits_rows = {GAME: rows}
        self.names = {1: "Sitter", 2: "Mate"}


def _game(date: str, final: bool) -> dict[str, Any]:
    return {
        "game_id": GAME, "date": date, "final": final, "players": [],
        "home": {"id": HOME}, "away": {"id": AWAY},
    }  # fmt: skip


def _rows() -> list[dict[str, Any]]:
    base = {
        "game_id": GAME, "team_id": HOME, "sitter_id": 1, "sitter_status": "available",
        "player_id": 2, "p_play": 0.9, "made_at": datetime(2026, 11, 10, 15, 0),
    }  # fmt: skip
    return [dict(base, stat="pts", delta_mean=0.8), dict(base, stat="reb", delta_mean=0.2)]


def test_a_finished_game_on_a_locked_night_gets_no_scenario() -> None:
    store = _Store(_rows())
    locked = S.game_scenarios(store, _game("2026-11-05", True))
    assert not locked.available and locked.sitters == [] and "locked test" in (locked.reason or "")
    live = S.game_scenarios(
        store, _game("2026-11-05", False)
    )  # before tip: no outcome to sit beside
    assert live.available and live.sitters[0].name == "Sitter"
    before_lock = S.game_scenarios(store, _game("2026-10-29", True))
    assert before_lock.available  # the lock starts 2026-10-30


def test_flat_threshold_and_empty_points_rows() -> None:
    rows = [dict(r, delta_mean=0.01) for r in _rows()]
    v = S.game_scenarios(_Store(rows), _game("2026-11-10", False))
    assert v.sitters[0].pts.direction == "flat" and v.sitters[0].teammates[0].pts == "flat"
    only_reb = [r for r in _rows() if r["stat"] == "reb"]
    v2 = S.game_scenarios(_Store(only_reb), _game("2026-11-10", False))
    assert not v2.available  # a scenario with no points rows is not shown at all


def test_not_modelled_names_questionable_players_and_late_scratches() -> None:
    v = S.game_scenarios(_Store(_rows()), _game("2026-11-10", False))
    text = " ".join(v.not_modelled).lower()
    assert "questionable" in text and "late scratch" in text and "an hour before tip" in text
    assert any("mostly from a few players" in e for e in v.evidence)


def test_per_player_size_needs_three_likely_teammates() -> None:
    v = S.game_scenarios(_Store(_rows()), _game("2026-11-10", False))  # one likely teammate
    assert v.sitters[0].pts.n_likely == 1 and v.sitters[0].pts.per_player is None
