"""Game-window gate: fake clock + fake schedule (temp parquet), incl. the 2026-11-01 DST day."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from nba.ingest import game_window as gw
from nba.ingest import queue as q


def _sched(tmp_path: Path, tips: list[str]) -> Path:
    d = tmp_path / "schedule"
    d.mkdir()
    pl.DataFrame(
        {"gameId": [f"g{i}" for i in range(len(tips))], "gameDateTimeUTC": tips}
    ).write_parquet(d / "live_tips_2026-27.parquet")
    return d


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t
        self.sleeps = 0

    def now(self) -> datetime:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps += 1
        self.t += timedelta(seconds=s)


def _gate(d: Path, c: Clock, **kw: object) -> gw.GameWindowGate:
    return gw.GameWindowGate(schedule_dir=d, clock=c.now, sleep=c.sleep, **kw)  # type: ignore[arg-type]


def test_in_window_pauses_until_last_tip_plus_30min(tmp_path: Path) -> None:
    # 2026-10-20 ET: tips 15:00 and 19:00 EDT (19:00Z, 23:00Z) -> window 16:30Z .. 23:30Z
    d = _sched(tmp_path, ["2026-10-20T19:00:00Z", "2026-10-20T23:00:00Z"])
    c = Clock(_utc("2026-10-20T17:00:00"))
    g = _gate(d, c)
    assert g.in_window()
    slept = g.wait()
    assert c.t >= _utc("2026-10-20T23:30:00")
    assert c.t < _utc("2026-10-20T23:32:00")
    assert slept == pytest.approx((c.t - _utc("2026-10-20T17:00:00")).total_seconds())
    assert not g.in_window()


def test_out_of_window_runs_immediately(tmp_path: Path) -> None:
    d = _sched(tmp_path, ["2026-10-20T19:00:00Z", "2026-10-20T23:00:00Z"])
    for t in ("2026-10-20T16:29:00", "2026-10-20T23:30:00", "2026-10-21T12:00:00"):
        c = Clock(_utc(t))
        assert _gate(d, c).wait() == 0.0
        assert c.sleeps == 0


def test_day_with_no_games_never_pauses(tmp_path: Path) -> None:
    d = _sched(tmp_path, ["2026-10-20T23:00:00Z"])
    c = Clock(_utc("2026-10-22T00:30:00"))
    g = _gate(d, c)
    assert g.wait() == 0.0 and not g.in_window()
    assert gw.build_windows([]) == []


def test_dst_fall_back_day_uses_eastern_offsets(tmp_path: Path) -> None:
    # 2026-11-01 (clocks go back at 02:00 ET). Tips 13:00 EST (18:00Z) and 22:00 EST (03:00Z 11-02).
    d = _sched(tmp_path, ["2026-11-01T18:00:00Z", "2026-11-02T03:00:00Z"])
    g = _gate(d, Clock(_utc("2026-11-01T12:00:00")))
    assert not g.in_window(_utc("2026-11-01T15:29:00"))  # 10:29 EST: before first tip - 2.5 h
    assert g.in_window(_utc("2026-11-01T15:31:00"))
    assert g.in_window(_utc("2026-11-02T03:29:00"))
    assert not g.in_window(_utc("2026-11-02T03:31:00"))


def test_dst_spring_forward_day(tmp_path: Path) -> None:
    # 2027-03-14: EDT from 02:00. Tip 19:00 EDT = 23:00Z -> window starts 16:30 EDT = 20:30Z.
    d = _sched(tmp_path, ["2027-03-14T23:00:00Z"])
    g = _gate(d, Clock(_utc("2027-03-14T12:00:00")))
    assert not g.in_window(_utc("2027-03-14T20:29:00"))
    assert g.in_window(_utc("2027-03-14T20:31:00"))


def test_disabled_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = _sched(tmp_path, ["2026-10-20T19:00:00Z"])
    c = Clock(_utc("2026-10-20T18:00:00"))
    assert _gate(d, c, on=False).wait() == 0.0
    assert gw.enabled({}) and not gw.enabled({gw.WINDOW_ENV: "0"})
    assert not gw.enabled({gw.WINDOW_ENV: "off"})


def test_missing_schedule_fails_open(tmp_path: Path) -> None:
    c = Clock(_utc("2026-10-20T18:00:00"))
    assert _gate(tmp_path / "nope", c).wait() == 0.0


def test_live_tips_override_raw(tmp_path: Path) -> None:
    d = tmp_path / "schedule"
    d.mkdir()
    pl.DataFrame({"gameId": ["a"], "gameDateTimeUTC": ["2026-10-20T19:00:00Z"]}).write_parquet(
        d / "raw_2026-27.parquet"
    )
    pl.DataFrame({"gameId": ["a"], "gameDateTimeUTC": ["2026-10-20T20:00:00Z"]}).write_parquet(
        d / "live_tips_2026-27.parquet"
    )
    assert gw.load_tips(d) == [_utc("2026-10-20T20:00:00")]


def test_queue_load_step_deferred_in_window_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = _sched(tmp_path, ["2026-10-20T19:00:00Z"])
    c = Clock(_utc("2026-10-20T18:00:00"))
    monkeypatch.setattr(gw, "gate_from_env", lambda: _gate(d, c))
    monkeypatch.setattr(q, "holders", lambda _p: [])
    queue = q.Queue(q.build_steps())
    cur = next(s for s in queue.steps if s.name == "load:officials")
    hist = next(s for s in queue.steps if s.name == "hist:load:tracking")
    assert not queue.try_load(cur) and cur.state == "deferred" and "game window" in cur.note
    assert queue.try_load(hist)  # history DB is not nba.duckdb
    c.t = _utc("2026-10-21T12:00:00")
    cur.state = "pending"
    assert queue.try_load(cur)
