"""Game-day probe: battery, slate in games, morning ended, caffeinate; dedupe once per ET day."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from nba.ops.watchdog import Alert, check, evaluate_game_day, games_in_db, todays_slate

# 09:30 ET on 2026-10-21 (08:30 CDT)
NOW = datetime.fromisoformat("2026-10-21T09:30:00-04:00")
SLATE = ["0022600010", "0022600011"]


def _slate(_data: Path, _day: object) -> list[str]:
    return list(SLATE)


def _no_slate(_data: Path, _day: object) -> list[str]:
    return []


def _present_all(_db: Path, ids: object) -> set[str]:
    return set(ids)  # type: ignore[call-overload]


def _present_none(_db: Path, _ids: object) -> set[str]:
    return set()


def _morning(ops: Path, start: datetime | None, end: datetime | None) -> None:
    hb = ops / "heartbeat"
    hb.mkdir(parents=True, exist_ok=True)
    rec = {
        "job": "daily-morning",
        "start_ts": start.isoformat() if start else None,
        "end_ts": end.isoformat() if end else None,
        "rc": 0 if end else None,
    }
    (hb / "daily-morning.json").write_text(json.dumps(rec))


def _healthy(ops: Path) -> None:
    _morning(ops, NOW - timedelta(hours=1), NOW - timedelta(minutes=30))


def _run(ops: Path, now: datetime = NOW, **kw: object) -> list[Alert]:
    args: dict[str, object] = {
        "battery": lambda: False,
        "caffeinate": lambda: True,
        "slate_fn": _slate,
        "present_fn": _present_all,
    }
    args.update(kw)
    return evaluate_game_day(now, ops, **args)  # type: ignore[arg-type]


def _keys(alerts: list[Alert]) -> list[str]:
    return [a.key for a in alerts]


def test_battery_alerts(tmp_path: Path) -> None:
    _healthy(tmp_path)
    alerts = _run(tmp_path, battery=lambda: True)
    assert _keys(alerts) == ["gameday:2026-10-21:battery"]
    assert alerts[0].message == (
        "game day 2026-10-21: on battery; plug in and run `caffeinate -dimsu`"
    )


def test_caffeinate_missing_alerts(tmp_path: Path) -> None:
    _healthy(tmp_path)
    alerts = _run(tmp_path, caffeinate=lambda: False)
    assert _keys(alerts) == ["gameday:2026-10-21:caffeinate"]
    assert "caffeinate -dimsu" in alerts[0].message


def test_slate_missing_alerts(tmp_path: Path) -> None:
    _healthy(tmp_path)
    alerts = _run(tmp_path, present_fn=_present_none)
    assert _keys(alerts) == ["gameday:2026-10-21:slate"]
    assert alerts[0].message == (
        "game day 2026-10-21: games table lacks today's slate: 2 of 2 game_ids missing; "
        "the schedule fetch does not write scheduled games"
    )


def test_slate_partial(tmp_path: Path) -> None:
    _healthy(tmp_path)
    alerts = _run(tmp_path, present_fn=lambda _d, ids: {SLATE[0]})
    assert "1 of 2 game_ids missing" in alerts[0].message


def test_morning_not_ended_alerts(tmp_path: Path) -> None:
    _morning(tmp_path, NOW - timedelta(hours=1), None)
    alerts = _run(tmp_path)
    assert _keys(alerts) == ["gameday:2026-10-21:morning"]
    assert "started but has not ended" in alerts[0].message


def test_morning_no_start_today_alerts(tmp_path: Path) -> None:
    _morning(tmp_path, NOW - timedelta(days=1), NOW - timedelta(days=1) + timedelta(hours=1))
    assert _keys(_run(tmp_path)) == ["gameday:2026-10-21:morning"]
    assert "has not started today" in _run(tmp_path)[0].message


def test_morning_no_heartbeat_alerts(tmp_path: Path) -> None:
    assert _keys(_run(tmp_path)) == ["gameday:2026-10-21:morning"]


def test_all_healthy_silent(tmp_path: Path) -> None:
    _healthy(tmp_path)
    assert _run(tmp_path) == []


def test_non_game_day_silent(tmp_path: Path) -> None:
    assert _run(tmp_path, battery=lambda: True, slate_fn=_no_slate) == []


def test_before_nine_et_silent(tmp_path: Path) -> None:
    early = datetime.fromisoformat("2026-10-21T08:59:00-04:00")
    assert _run(tmp_path, now=early, battery=lambda: True, caffeinate=lambda: False) == []
    at_nine = datetime.fromisoformat("2026-10-21T08:00:00-05:00")  # 09:00 ET exactly (CDT clock)
    assert _run(tmp_path, now=at_nine, battery=lambda: True) != []


def test_probe_failure_is_its_own_alert(tmp_path: Path) -> None:
    _healthy(tmp_path)

    def boom() -> bool:
        raise OSError("pmset missing")

    alerts = _run(tmp_path, battery=boom)
    assert _keys(alerts) == ["gameday:2026-10-21:probe-battery"]
    assert "battery probe could not compute (OSError: pmset missing)" in alerts[0].message

    def bad_slate(_d: Path, _day: object) -> list[str]:
        raise ValueError("bad cache")

    alerts = _run(tmp_path, slate_fn=bad_slate)
    assert _keys(alerts) == ["gameday:2026-10-21:probe"]


def test_db_busy_is_not_an_alert(tmp_path: Path) -> None:
    from nba.ops.watchdog import DbBusy

    def busy(_d: Path, _ids: object) -> set[str]:
        raise DbBusy("locked")

    _healthy(tmp_path)
    assert _run(tmp_path, present_fn=busy) == []


def test_check_dedupes_once_per_day(tmp_path: Path) -> None:
    ops = tmp_path / "ops"
    _healthy(ops)
    sent: list[str] = []

    def gd(now: datetime, o: Path) -> list[Alert]:
        return _run(o, now=now, battery=lambda: True)

    def go(now: datetime) -> list[Alert]:
        return check(
            ops, now=now, launchctl_text="", kalshi_log=None, notifier=sent.append, gameday=gd
        )

    first = [a for a in go(NOW) if a.key.startswith("gameday:")]
    assert _keys(first) == ["gameday:2026-10-21:battery"]
    # 10 minutes and 7 hours later (past REALERT_AFTER): same day, no re-alert
    assert [a for a in go(NOW + timedelta(minutes=10)) if a.key.startswith("gameday:")] == []
    assert [a for a in go(NOW + timedelta(hours=7)) if a.key.startswith("gameday:")] == []
    text = (ops / "ALERTS.md").read_text()
    assert text.count("on battery; plug in") == 1
    # next game day: a new key, alerts again
    nxt = NOW + timedelta(days=1)
    _morning(ops, nxt - timedelta(hours=1), nxt - timedelta(minutes=30))
    assert any("2026-10-22:battery" in a.key for a in go(nxt))


def test_check_survives_probe_crash(tmp_path: Path) -> None:
    def crash(_now: datetime, _ops: Path) -> list[Alert]:
        raise RuntimeError("kaput")

    out = check(tmp_path / "ops", now=NOW, launchctl_text="", notifier=None, gameday=crash)
    assert [a.key for a in out if a.key == "gameday:probe"] == ["gameday:probe"]


def test_todays_slate_and_games_in_db(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "schedule_cache").mkdir(parents=True)
    rows = [
        ["0022600010", "2026-10-21T23:00:00", 1, 2],  # 19:00 ET today
        ["0022600011", "2026-10-22T02:00:00", 3, 4],  # 22:00 ET today (UTC tomorrow)
        ["0022600012", "2026-10-22T23:00:00", 5, 6],  # tomorrow
        ["0012600001", "2026-10-21T23:00:00", 7, 8],  # preseason
    ]
    (data / "schedule_cache" / "schedule_2026-27.json").write_text(json.dumps(rows))
    assert todays_slate(data, NOW.date()) == ["0022600010", "0022600011"]
    db = tmp_path / "nba.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE games (game_id VARCHAR)")
    con.execute("INSERT INTO games VALUES ('0022600010')")
    con.close()
    assert games_in_db(db, SLATE) == {"0022600010"}
