"""Game-day probe: battery, slate in games, morning ended, caffeinate; dedupe once per ET day."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import duckdb
import pytest

from nba.ops.watchdog import (
    Alert,
    caffeinate_running,
    check,
    evaluate_game_day,
    games_in_db,
    todays_slate,
)

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
        "game day 2026-10-21: games table lacks today's slate: 2 of 2 game_ids missing "
        "(the pretip ingest writes today's slate at its first run; this fires if no pretip "
        "run has happened yet)"
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
    assert [a.key for a in out if "probe" in a.key] == ["gameday:2026-10-21:probe-crash"]


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


def test_unreadable_schedule_is_the_probe_alert_not_silence(tmp_path: Path) -> None:
    ops = tmp_path / "data" / "ops"
    _healthy(ops)
    alerts = evaluate_game_day(
        NOW, ops, battery=lambda: True, caffeinate=lambda: False, present_fn=_present_all
    )
    assert _keys(alerts) == ["gameday:2026-10-21:probe"]
    with pytest.raises(FileNotFoundError):
        todays_slate(tmp_path / "data", NOW.date())


def test_corrupt_cache_with_good_live_tips_still_works(tmp_path: Path) -> None:
    import polars as pl

    data = tmp_path / "data"
    (data / "schedule").mkdir(parents=True)
    pl.DataFrame(
        {
            "gameId": ["0022600010", "0022600099", "0012600001"],
            "gameDateTimeUTC": [
                "2026-10-21T23:00:00Z",
                "2026-10-23T23:00:00Z",
                "2026-10-21T23:00:00Z",
            ],
            "gameDateTimeEst": ["x", "x", "x"],
        }
    ).write_parquet(data / "schedule" / "live_tips_2026-27.parquet")
    assert todays_slate(data, NOW.date()) == ["0022600010"]  # no cache file at all


def test_pgrep_error_exit_is_not_not_running() -> None:
    def rc(code: int) -> mock.Mock:
        return mock.Mock(returncode=code)

    with mock.patch("subprocess.run", return_value=rc(0)):
        assert caffeinate_running() is True
    with mock.patch("subprocess.run", return_value=rc(1)):
        assert caffeinate_running() is False
    with mock.patch("subprocess.run", return_value=rc(2)), pytest.raises(RuntimeError):
        caffeinate_running()


def test_pgrep_error_surfaces_as_probe_caffeinate(tmp_path: Path) -> None:
    _healthy(tmp_path)

    def err() -> bool:
        raise RuntimeError("pgrep exit 2")

    assert _keys(_run(tmp_path, caffeinate=err)) == ["gameday:2026-10-21:probe-caffeinate"]


def test_flap_within_a_day_fires_once(tmp_path: Path) -> None:
    ops = tmp_path / "ops"
    _healthy(ops)
    state = {"on": True}

    def gd(now: datetime, o: Path) -> list[Alert]:
        return _run(o, now=now, battery=lambda: state["on"])

    def go(now: datetime) -> list[Alert]:
        return check(ops, now=now, launchctl_text="", notifier=None, gameday=gd)

    assert len([a for a in go(NOW) if a.key.endswith(":battery")]) == 1
    state["on"] = False
    assert go(NOW + timedelta(minutes=10)) == []
    state["on"] = True
    assert [a for a in go(NOW + timedelta(minutes=20)) if a.key.endswith(":battery")] == []


def test_notification_order_battery_caffeinate_morning_slate(tmp_path: Path) -> None:
    _morning(tmp_path, NOW - timedelta(hours=1), None)
    alerts = _run(
        tmp_path, battery=lambda: True, caffeinate=lambda: False, present_fn=_present_none
    )
    assert [a.key.rsplit(":", 1)[1] for a in alerts] == [
        "battery",
        "caffeinate",
        "morning",
        "slate",
    ]
    assert "(60 min since start)" in alerts[2].message
    assert "data/ops/morning_20261021.log" in alerts[2].message


# --- pre-tip window (2026-10-23: lid closed on battery, Kalshi dark, one quiet alert) -----------
from datetime import UTC  # noqa: E402

from nba.ops.watchdog import KALSHI_LABEL, evaluate, kalshi_snapshot_status  # noqa: E402

FIRST_TIP = NOW + timedelta(minutes=120)


def _tips(_data: Path, _day: object) -> dict[str, datetime]:
    return {SLATE[0]: FIRST_TIP, SLATE[1]: FIRST_TIP + timedelta(hours=2)}


def _kalshi(age_min: int, failed: list[str] | None = None):  # type: ignore[no-untyped-def]
    return lambda _d: (NOW - timedelta(minutes=age_min), failed or [])


def test_battery_in_pretip_window_is_loud_and_hourly(tmp_path: Path) -> None:
    _healthy(tmp_path)
    alerts = _run(tmp_path, battery=lambda: True, tips_fn=_tips, kalshi_fn=_kalshi(5))
    assert _keys(alerts) == ["gameday:2026-10-21:battery@09"]
    assert alerts[0].message.startswith("ACTION NEEDED: first tip in 120 min")
    later_now = NOW + timedelta(hours=1)
    later = _run(
        tmp_path,
        now=later_now,
        battery=lambda: True,
        tips_fn=_tips,
        kalshi_fn=lambda _d: (later_now - timedelta(minutes=5), []),
    )
    assert _keys(later) == ["gameday:2026-10-21:battery@10"]


def test_battery_far_from_tip_keeps_once_a_day_key(tmp_path: Path) -> None:
    _healthy(tmp_path)
    far = lambda _d, _t: {SLATE[0]: NOW + timedelta(hours=9)}  # noqa: E731
    alerts = _run(tmp_path, battery=lambda: True, tips_fn=far)
    assert _keys(alerts) == ["gameday:2026-10-21:battery"]


def test_kalshi_stale_before_first_tip_alerts(tmp_path: Path) -> None:
    _healthy(tmp_path)
    alerts = _run(tmp_path, tips_fn=_tips, kalshi_fn=_kalshi(95))
    assert _keys(alerts) == ["gameday:2026-10-21:kalshi@0930"]
    assert "95 min old" in alerts[0].message and alerts[0].message.startswith("ACTION NEEDED")


def test_kalshi_failed_series_alerts_even_if_fresh(tmp_path: Path) -> None:
    _healthy(tmp_path)
    alerts = _run(tmp_path, tips_fn=_tips, kalshi_fn=_kalshi(3, ["KXNBAGAME"]))
    assert "FAILED KXNBAGAME" in alerts[0].message


def test_kalshi_fresh_or_outside_window_is_silent(tmp_path: Path) -> None:
    _healthy(tmp_path)
    assert _run(tmp_path, tips_fn=_tips, kalshi_fn=_kalshi(5)) == []
    far = lambda _d, _t: {SLATE[0]: NOW + timedelta(hours=4)}  # noqa: E731
    assert _run(tmp_path, tips_fn=far, kalshi_fn=_kalshi(500)) == []
    assert _run(tmp_path, tips_fn=_tips, kalshi_fn=_kalshi(500), slate_fn=_no_slate) == []


def test_kalshi_status_reads_last_run(tmp_path: Path) -> None:
    (tmp_path / "snapshot.log").write_text(
        "snapshot 2026-10-23T21:47:35Z markets=274\nper_series {}\n"
        "snapshot 2026-10-24T00:09:17Z markets=3709\n"
    )
    (tmp_path / "snapshot.err").write_text(
        "2026-10-23T21:47:35Z FAILED KXNBAGAME: ConnectError: x\n"
        "2026-10-24T00:09:17Z UNMATCHED player names (2): A, B\n"
    )
    last, failed = kalshi_snapshot_status(tmp_path)
    assert last == datetime(2026, 10, 24, 0, 9, 17, tzinfo=UTC) and failed == []
    (tmp_path / "snapshot.err").write_text("2026-10-24T00:09:17Z FAILED KXNBAGAME: ConnectError\n")
    assert kalshi_snapshot_status(tmp_path)[1] == ["KXNBAGAME"]
    assert kalshi_snapshot_status(tmp_path / "none") == (None, [])


def test_launchctl_exit_1_for_kalshi_not_alerted_but_126_is(tmp_path: Path) -> None:
    hb = tmp_path / "hb"
    for code, expect in ((1, 0), (126, 1)):
        text = f"-\t{code}\t{KALSHI_LABEL}\n"
        out = evaluate(NOW, hb, text, NOW, None, jobs=())
        assert len([a for a in out if a.key.endswith(":launchctl")]) == expect
