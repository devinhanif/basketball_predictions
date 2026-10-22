"""The day runner's schedule on a fake clock."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from nba.papertrade.runner import DayRunner, scan_times

T1 = datetime(2026, 10, 22, 0, 0)
T2 = datetime(2026, 10, 22, 2, 0)


def test_scan_times_one_before_first_tip_then_each_later_window() -> None:
    assert scan_times([T2, T1, T1]) == [T1 - timedelta(minutes=75), T2 - timedelta(minutes=45)]
    assert scan_times([]) == []


def test_runner_scans_on_schedule_monitors_each_tick_and_settles_once(tmp_path: Path) -> None:
    clock = [T1 - timedelta(minutes=80)]
    scans: list[datetime] = []
    mons: list[datetime] = []
    settled: list[datetime] = []

    def sleep(s: float) -> None:
        clock[0] += timedelta(seconds=s)

    r = DayRunner(
        T1.date(), [T1, T2], scans.append, lambda n: (mons.append(n), {"positions": 1})[1],
        settled.append, now_fn=lambda: clock[0], sleep_fn=sleep, interval_s=60.0,
        heartbeat=tmp_path / "hb.json",
    )  # fmt: skip
    assert r.run() == 0
    assert len(scans) == 2 and scans[0] >= T1 - timedelta(minutes=75)
    assert scans[1] >= T2 - timedelta(minutes=45)
    assert mons and len(settled) == 1
    hb = json.loads((tmp_path / "hb.json").read_text())
    assert hb["status"] == "done" and hb["rc"] == 0 and hb["errors"] == 0


def test_runner_survives_a_failing_step(tmp_path: Path) -> None:
    clock = [T1 - timedelta(minutes=70)]

    def boom(now: datetime) -> None:
        raise RuntimeError("scan blew up")

    r = DayRunner(
        T1.date(), [T1], boom, lambda n: {"positions": 0}, lambda n: None,
        now_fn=lambda: clock[0],
        sleep_fn=lambda s: clock.__setitem__(0, clock[0] + timedelta(seconds=s)),
        heartbeat=tmp_path / "hb.json",
    )  # fmt: skip
    assert r.run() == 0
    assert json.loads((tmp_path / "hb.json").read_text())["errors"] >= 1
    assert "scan blew up" in (tmp_path / "hb.errors.log").read_text()


def test_no_games_exits_zero(tmp_path: Path) -> None:
    r = DayRunner(
        T1.date(), [], lambda n: None, lambda n: {}, lambda n: None, heartbeat=tmp_path / "h.json"
    )
    assert r.run() == 0
    assert json.loads((tmp_path / "h.json").read_text())["status"] == "no_games"
