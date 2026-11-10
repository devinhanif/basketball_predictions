# ruff: noqa: E501
"""The in-game snapshot the app reads: v1 fields exactly, v2 script block, atomic write, contained
failures, no price, and the JARVIS hook (flag on only, never in a dry run, never raises)."""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nba.markets import checkpoint_scorer as ck
from nba.markets import checkpoint_snapshot as CS
from nba.markets.checkpoint_store import CheckpointStore
from nba.parlay import game_scripts as GS
from tests.markets.checkpoint_helpers import T0, S, build_night, first_tick, regular
from tests.markets.halftime_helpers import tiny_artifact
from tests.markets.test_checkpoint_scorer import FEE, PIN, SLATE, run

NOW = datetime(2026, 11, 10, 1, 40, 5, tzinfo=UTC)
PARAMS = json.loads((Path(GS.__file__).parent / "combo_params_v1.json").read_text())


def combo_file(path: Path) -> Path:
    doc = {
        "schema": "combo_export_v1", "night": "2026-11-10",
        "shared": {"scripts": {"definitions": PARAMS["definitions"]}},
        "games": [
            {"game_id": "G1", "home_abbr": "PHI", "away_abbr": "DET",
             "game": {"mu_m": 3.0, "sd_m": 13.63, "mu_t": 228.0, "sd_t": 18.82}},
            {"game_id": "G2", "home_abbr": None, "away_abbr": None, "game": {}},
        ],
    }  # fmt: skip
    path.write_text(json.dumps(doc))
    return path


def state(elapsed: float, home: int | None, away: int | None, period: int = 3) -> ck.State:
    return ck.State(
        "evt1", NOW, Path("x"), "STATUS_IN_PROGRESS", period, "5:00", elapsed, "DET", "PHI",
        away, home, NOW,
    )  # fmt: skip


def rows(checkpoint: int = 25, n: int = 2) -> list[dict[str, Any]]:
    return [
        {
            "event_id": "evt1",
            "game": "DET@PHI",
            "checkpoint": checkpoint,
            "e_used": 25.1,
            "model_support": "trained",
            "period": 3,
            "clock": "3:39",
            "known_at": NOW - timedelta(seconds=5),
            "player_name": f"P{i}",
            "stat": "pts",
            "k": 20 + i,
            "p_model": 0.5,
            "cur": 12,
            "minutes": 25,
            "fouls": 1,
            "ask_c": 40,
            "bid_c": 38,
            "fee_c": 2,
            "edge_model": 0.1,
            "p_glm_e": 0.4,
        }
        for i in range(n)
    ] + [{**rows_old(), "checkpoint": checkpoint - 5}]  # an older checkpoint in the same set


def rows_old() -> dict[str, Any]:
    return {"event_id": "evt1", "game": "DET@PHI", "checkpoint": 20, "e_used": 20.0, "model_support": "trained",
            "period": 2, "clock": "1:00", "known_at": NOW - timedelta(minutes=5), "player_name": "Old",
            "stat": "pts", "k": 10, "p_model": 0.9, "cur": 5, "minutes": 20, "fouls": 0}  # fmt: skip


def test_v1_fields_exactly_only_the_newest_checkpoint_and_no_price(tmp_path: Path) -> None:
    snap = CS.build_snapshot({"DET@PHI": rows()}, {}, {}, NOW)
    assert snap["version"] == 2 and snap["generated_at"] == "2026-11-10T01:40:05+00:00"
    g = snap["games"]["evt1"]
    assert set(g) == {"checkpoint", "e_used", "support", "period", "clock", "known_at", "rows"}
    assert g["checkpoint"] == 25 and g["support"] == "trained" and len(g["rows"]) == 2
    assert set(g["rows"][0]) == {"player", "stat", "k", "p_model", "cur", "minutes", "fouls"}
    text = json.dumps(snap)
    for banned in ("ask", "bid", "edge", "fee", "p_glm"):
        assert banned not in text


def test_script_block_from_the_live_state_and_the_pregame_file(tmp_path: Path) -> None:
    pre = CS.load_pregame(combo_file(tmp_path / "combo_2026-11-10.json"))
    assert set(pre) == {"DET@PHI"}  # a game without abbreviations cannot be matched: never guessed
    st = state(25.0 * 60, 62, 40)  # 25 minutes, home +22
    snap = CS.build_snapshot({"DET@PHI": rows()}, {"evt1": st}, pre, NOW)
    s = snap["games"]["evt1"]["script"]
    assert s["status"] == "exploratory" and s["method"] == "independent_increments_v1"
    assert s["margin"] == 22 and s["total"] == 102 and s["elapsed_min"] == 25.0
    assert set(s["p"]) == set(GS.SCRIPTS) and all(0.0 <= v <= 1.0 for v in s["p"].values())
    assert s["p"]["blowout"] > s["prior"]["blowout"] and s["p"]["close"] < s["prior"]["close"]
    assert "UNPROVEN" in s["label"] and s["pregame"]["mu_t"] == 228.0
    # the same numbers the module the browser is tested against gives
    g = {"game": s["pregame"]}
    shared = {"scripts": {"definitions": PARAMS["definitions"]}}
    want = GS.live_script_probs(g, shared, 25.0, 22, 102)
    assert all(abs(s["p"][k] - round(want[k], 4)) < 1e-9 for k in GS.SCRIPTS)


@pytest.mark.parametrize(
    "st",
    [
        None,
        state(900.0, None, 40),
        state(900.0, 50, None),
        state(900.0, 50, 40, period=5),
        state(2900.0, 50, 40, period=4),
    ],
)
def test_no_state_no_score_or_overtime_means_no_script_block(
    st: ck.State | None, tmp_path: Path
) -> None:
    pre = CS.load_pregame(combo_file(tmp_path / "c.json"))
    snap = CS.build_snapshot({"DET@PHI": rows()}, {} if st is None else {"evt1": st}, pre, NOW)
    assert "script" not in snap["games"]["evt1"]


def test_game_without_pregame_ships_without_the_block(tmp_path: Path) -> None:
    snap = CS.build_snapshot({"DET@PHI": rows()}, {"evt1": state(1500.0, 60, 50)}, {}, NOW)
    assert "script" not in snap["games"]["evt1"] and snap["games"]["evt1"]["rows"]


def test_write_is_atomic_and_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "d" / "snap.json"
    n = CS.write_snapshot(out, {"version": 2, "games": {}})
    assert json.loads(out.read_text())["version"] == 2 and n == len(out.read_text())
    assert [p.name for p in out.parent.iterdir()] == ["snap.json"]  # no temp file left

    real = os.replace

    def boom(a: Any, b: Any) -> None:
        raise OSError("rename failed")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        CS.write_snapshot(out, {"version": 2, "games": {"x": 1}})
    monkeypatch.setattr(os, "replace", real)
    assert json.loads(out.read_text())["games"] == {}  # the old file is intact
    assert [p.name for p in out.parent.iterdir()] == ["snap.json"]
    with pytest.raises(ValueError):
        CS.write_snapshot(out, {"junk": "x" * (CS.MAX_BYTES + 1)})


def test_publish_never_raises(tmp_path: Path) -> None:
    ok = CS.publish(tmp_path / "s.json", tmp_path / "absent.json", {"DET@PHI": rows()}, {}, NOW)
    assert ok["ok"] is True and ok["games"] == 1 and ok["scripts"] == 0
    bad = CS.publish(tmp_path / "s2.json", tmp_path / "absent.json", {"G": [{"nope": 1}]}, {}, NOW)
    assert bad["ok"] is False  # contained
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        res = CS.publish(ro / "s.json", tmp_path / "absent.json", {}, {}, NOW)
        assert res["ok"] is False or os.geteuid() == 0
    finally:
        ro.chmod(0o700)


def test_scorer_exposes_the_latest_live_state_of_each_game(tmp_path: Path) -> None:
    night = build_night(tmp_path, regular())
    i = first_tick(10)
    now = T0 + (60 * i + 5) * S + 45 * S
    store = CheckpointStore.open(":memory:", mode="live")
    rep = run(night, store, tmp_path, now=now)
    assert rep.stored > 0 and len(rep.latest_states) == 1
    st = next(iter(rep.latest_states.values()))
    assert st.live and st.elapsed_s is not None and st.home_score is not None
    replay = CheckpointStore.open(":memory:", mode="replay")
    rep2 = ck.score_night_checkpoints(
        night,
        tiny_artifact(tmp_path),
        run.__globals__["FakeRecency"](),
        FEE,
        replay,
        SLATE,
        None,
        PIN,
    )
    assert rep2.latest_states == {}  # the replay path is unchanged


def test_jarvis_hook_writes_only_when_enabled_and_never_in_a_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nba.jarvis import ingame_checkpoints as IC

    class Cfg:
        root = tmp_path
        ingame_checkpoints = {"enabled": True, "snapshot": "snap.json", "script_priors_dir": "pre"}

    (tmp_path / "pre").mkdir()
    combo_file(tmp_path / "pre" / "combo_2026-11-10.json")
    rb = {"DET@PHI": rows()}
    st = {"evt1": state(1500.0, 60, 50)}
    cfg: Any = Cfg()
    assert IC.publish_snapshot(cfg, date(2026, 11, 10), NOW, rb, st, dry_run=True) == {
        "ok": True,
        "skipped": "dry_run",
    }
    assert not (tmp_path / "snap.json").exists()
    res = IC.publish_snapshot(cfg, date(2026, 11, 10), NOW, rb, st, dry_run=False)
    assert res["ok"] and res["scripts"] == 1
    assert "script" in json.loads((tmp_path / "snap.json").read_text())["games"]["evt1"]

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("x")

    monkeypatch.setattr(CS, "publish", boom)
    assert IC.publish_snapshot(cfg, date(2026, 11, 10), NOW, rb, st, dry_run=False) == {
        "ok": False,
        "error": "RuntimeError",
    }


def test_a_stale_state_carries_no_script_block_and_the_label_names_overtime(tmp_path: Path) -> None:
    pre = CS.load_pregame(combo_file(tmp_path / "c.json"))
    old = replace_received(state(1500.0, 60, 50), NOW - timedelta(seconds=CS.STATE_MAX_AGE_S + 1))
    fresh = replace_received(state(1500.0, 60, 50), NOW - timedelta(seconds=CS.STATE_MAX_AGE_S - 1))
    assert (
        "script"
        not in CS.build_snapshot({"DET@PHI": rows()}, {"evt1": old}, pre, NOW)["games"]["evt1"]
    )
    assert (
        "script"
        in CS.build_snapshot({"DET@PHI": rows()}, {"evt1": fresh}, pre, NOW)["games"]["evt1"]
    )
    assert "overtime is not modelled" in CS.SCRIPT_LABEL


def replace_received(st: ck.State, at: datetime) -> ck.State:
    import dataclasses

    return dataclasses.replace(st, received_at=at)
