# ruff: noqa: E501
"""Watchdog: Pinnacle freshness for games within 3 h of tip, and the pulls job heartbeat."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from nba.ops.watchdog import Alert, evaluate_game_day
from nba.pulls.config import PullsConfig
from nba.pulls.freshness import pulls_problems

NOW = datetime.fromisoformat("2026-10-21T20:00:00-04:00")  # 00:00Z on the 22nd
PC = PullsConfig()


def _market(ops: Path, rows: list[tuple[str, datetime, str]]) -> Path:
    db = ops.parent / "markets" / "market_asof.duckdb"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db))
    con.execute(
        "CREATE TABLE live_book_odds (game_id VARCHAR, book VARCHAR, requested_at TIMESTAMP, snapshot_kind VARCHAR)"
    )
    for gid, req, kind in rows:
        con.execute(
            "INSERT INTO live_book_odds VALUES (?, 'pinnacle', ?, ?)",
            [gid, req.replace(tzinfo=None), kind],
        )
    con.close()
    return db


def _utc(h: float) -> datetime:
    return NOW + timedelta(hours=h)


def _beat(ops: Path, ended: datetime) -> None:
    hb = ops / "heartbeat"
    hb.mkdir(parents=True, exist_ok=True)
    (hb / "pulls.json").write_text(
        json.dumps(
            {"job": "pulls", "start_ts": ended.isoformat(), "end_ts": ended.isoformat(), "rc": 0}
        )
    )


def _naive_utc(t: datetime) -> datetime:
    return t.astimezone(datetime.fromisoformat("2026-01-01T00:00:00+00:00").tzinfo)


def test_fresh_pinnacle_row_is_fine(tmp_path: Path) -> None:
    ops = tmp_path / "ops"
    _market(ops, [("G1", _naive_utc(NOW - timedelta(minutes=40)), "t120")])
    assert pulls_problems(PC, ops, NOW, {"G1": _utc(2)}) == []


def test_a_stale_row_for_a_game_within_three_hours_warns(tmp_path: Path) -> None:
    ops = tmp_path / "ops"
    _market(ops, [("G1", _naive_utc(NOW - timedelta(minutes=80)), "t240")])
    (msg,) = pulls_problems(PC, ops, NOW, {"G1": _utc(2.5)})
    assert "G1 80 min" in msg and "60" in msg and "3 h" in msg


def test_the_grace_covers_a_scheduled_pull_that_lands_a_tick_late(tmp_path: Path) -> None:
    ops = tmp_path / "ops"
    _market(ops, [("G1", _naive_utc(NOW - timedelta(minutes=64)), "t240")])
    assert pulls_problems(PC, ops, NOW, {"G1": _utc(2)}) == []  # 64 <= 60 + 5
    _market_old = ops.parent / "markets" / "market_asof.duckdb"
    _market_old.unlink()
    _market(ops, [("G1", _naive_utc(NOW - timedelta(minutes=66)), "t240")])
    assert len(pulls_problems(PC, ops, NOW, {"G1": _utc(2)})) == 1


def test_a_game_with_no_row_at_all_warns_but_a_far_game_does_not(tmp_path: Path) -> None:
    ops = tmp_path / "ops"
    _market(ops, [("G9", _naive_utc(NOW - timedelta(minutes=5)), "t60")])
    (msg,) = pulls_problems(PC, ops, NOW, {"G1": _utc(1), "G2": _utc(5), "G3": _utc(-1)})
    assert (
        "G1 none" in msg and "G2" not in msg and "G3" not in msg
    )  # 5 h out and already tipped: skipped


def test_unreadable_or_absent_store_is_unknown_not_stale(tmp_path: Path) -> None:
    ops = tmp_path / "ops"
    assert pulls_problems(PC, ops, NOW, {"G1": _utc(1)}) == []  # no market store at all


def test_heartbeat_checked_only_when_it_exists_and_enabled(tmp_path: Path) -> None:
    ops = tmp_path / "ops"
    assert pulls_problems(PC, ops, NOW, {}) == []  # never installed: silent
    _beat(ops, NOW - timedelta(minutes=3))
    assert pulls_problems(PC, ops, NOW, {}) == []
    _beat(ops, NOW - timedelta(minutes=25))
    (msg,) = pulls_problems(PC, ops, NOW, {})
    assert msg.startswith("pulls job heartbeat is 25 min old")
    off = PullsConfig(enabled=False)
    assert pulls_problems(off, ops, NOW, {"G1": _utc(1)}) == []


def test_game_day_probe_raises_one_alert_per_problem_per_half_hour(tmp_path: Path) -> None:
    ops = tmp_path / "ops"
    _beat(ops, NOW - timedelta(minutes=30))
    hb = ops / "heartbeat"
    (hb / "daily-morning.json").write_text(
        json.dumps({"job": "daily-morning", "start_ts": (NOW - timedelta(hours=9)).isoformat(),
                    "end_ts": (NOW - timedelta(hours=8)).isoformat(), "rc": 0})
    )  # fmt: skip
    alerts = evaluate_game_day(
        NOW, ops, battery=lambda: False, caffeinate=lambda: True,
        slate_fn=lambda _d, _t: ["0022600010"], present_fn=lambda _db, ids: set(ids),
        tips_fn=lambda _d, _t: {"0022600010": _utc(2)},
        kalshi_fn=lambda _d: (NOW.astimezone(), []), live_fn=lambda *_a: None,
        pulls_fn=lambda o, n, t: pulls_problems(PC, o, n, t),
    )  # fmt: skip
    pull_alerts: list[Alert] = [a for a in alerts if "pulls-" in a.key]
    assert [a.key for a in pull_alerts] == ["gameday:2026-10-21:pulls-heartbeat@2000"]
    assert pull_alerts[0].message.startswith("pull orchestrator: pulls job heartbeat is 30 min old")


def test_a_crashing_pulls_probe_never_takes_the_rest_down(tmp_path: Path) -> None:
    def boom(*_a: object) -> list[str]:
        raise RuntimeError("x")

    alerts = evaluate_game_day(
        NOW, tmp_path / "ops", battery=lambda: True, caffeinate=lambda: True,
        slate_fn=lambda _d, _t: ["0022600010"], present_fn=lambda _db, ids: set(ids),
        tips_fn=lambda _d, _t: {}, kalshi_fn=lambda _d: (None, []), live_fn=lambda *_a: None,
        pulls_fn=boom,
    )  # fmt: skip
    keys = [a.key for a in alerts]
    assert "gameday:2026-10-21:probe-pulls" in keys and "gameday:2026-10-21:battery" in keys


def test_pinnacle_rule_is_skipped_when_the_hourly_kinds_are_not_scheduled(tmp_path: Path) -> None:
    ops = tmp_path / "ops"
    _market(ops, [("G1", _naive_utc(NOW - timedelta(minutes=80)), "t240")])
    assert len(pulls_problems(PC, ops, NOW, {"G1": _utc(2.5)})) == 1
    assert pulls_problems(PC, ops, NOW, {"G1": _utc(2.5)}, check_pinnacle=False) == []


def test_pulls_alive_needs_enabled_a_fresh_beat_and_not_gated_off(tmp_path: Path) -> None:
    from nba.pulls.freshness import pulls_alive

    ops = tmp_path / "ops"
    cfg = PullsConfig(requests_db=ops / "pulls.duckdb")
    assert pulls_alive(cfg, NOW)[0] is False  # no heartbeat
    _beat(ops, NOW - timedelta(minutes=3))
    assert pulls_alive(cfg, NOW) == (True, "")
    assert (
        pulls_alive(cfg, NOW.replace(tzinfo=None) + timedelta(hours=9))[0] is False
    )  # naive = UTC
    assert (
        pulls_alive(PullsConfig(enabled=False, requests_db=ops / "pulls.duckdb"), NOW)[0] is False
    )
