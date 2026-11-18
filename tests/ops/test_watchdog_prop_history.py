"""Watchdog wiring of the Kalshi prop price history job (optional, display data only)."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from nba.ops.watchdog import (
    ET,
    JOBS,
    PROP_HISTORY_JOB,
    check,
    evaluate,
    evaluate_prop_history,
    last_due,
)

TIP = datetime(2026, 11, 17, 0, 30, tzinfo=UTC)  # 19:30 ET on 2026-11-16
DAY = date(2026, 11, 16)


def _tips(tips: dict[date, list[datetime]]):  # type: ignore[no-untyped-def]
    def fn(_data: Path, day: date) -> dict[str, datetime]:
        return {f"00226{i:05d}": t for i, t in enumerate(tips.get(day, []))}

    return fn


def _hb(ops: Path, **kw: object) -> None:
    d = ops / "heartbeat"
    d.mkdir(parents=True, exist_ok=True)
    rec = {
        "job": PROP_HISTORY_JOB,
        "start_ts": "2026-11-17T01:00:00+00:00",
        "end_ts": "2026-11-17T01:00:04+00:00",
        "rc": 0,
        "last_success_ts": "2026-11-17T01:00:04+00:00",
        **kw,
    }
    (d / f"{PROP_HISTORY_JOB}.json").write_text(json.dumps(rec))


def _latest(data: Path, built: datetime, slate: str = "2026-11-16") -> None:
    d = data / "export/kalshi_prop_history/latest"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.json").write_text(json.dumps({"slate_date": slate, "built_at": built.isoformat()}))


def _final(data: Path, day: date) -> None:
    d = data / "export/kalshi_prop_history" / day.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.json.gz").write_bytes(gzip.compress(json.dumps({"status": "final"}).encode()))


def _eval(tmp: Path, now: datetime, tips: dict[date, list[datetime]]) -> list[str]:
    ops = tmp / "data/ops"
    alerts = evaluate_prop_history(now, ops, data_dir=tmp / "data", tips_fn=_tips(tips))
    return [a.message for a in alerts]


def test_jobspec_is_optional_and_matches_the_plist() -> None:
    spec = next(s for s in JOBS if s.name == PROP_HISTORY_JOB)
    assert spec.optional and spec.label == "local.nba.kalshi-prop-history"
    assert spec.kind == "interval" and spec.interval == timedelta(minutes=15)
    assert spec.grace == timedelta(minutes=30) and spec.max_skips == 4


def test_never_installed_raises_nothing(tmp_path: Path) -> None:
    (tmp_path / "data/ops/heartbeat").mkdir(parents=True)
    now = TIP + timedelta(hours=8)
    assert _eval(tmp_path, now, {DAY: [TIP]}) == []
    spec = next(s for s in JOBS if s.name == PROP_HISTORY_JOB)
    hb = tmp_path / "data/ops/heartbeat"
    assert evaluate(now, hb, "", now - timedelta(days=2), None, jobs=(spec,), tz=ET) == []


def test_healthy_in_game_is_silent(tmp_path: Path) -> None:
    now = TIP + timedelta(minutes=40)
    _hb(tmp_path / "data/ops", last_success_ts=(now - timedelta(minutes=10)).isoformat())
    _latest(tmp_path / "data", now - timedelta(minutes=10))
    assert _eval(tmp_path, now, {DAY: [TIP]}) == []


def test_a_job_that_only_skips_alerts_in_game(tmp_path: Path) -> None:
    now = TIP + timedelta(hours=1)
    _hb(tmp_path / "data/ops", last_success_ts=(now - timedelta(minutes=55)).isoformat())
    _latest(tmp_path / "data", now - timedelta(minutes=55))
    (msg,) = _eval(tmp_path, now, {DAY: [TIP]})
    assert "no successful run" in msg


def test_a_failed_run_alerts(tmp_path: Path) -> None:
    now = TIP + timedelta(minutes=20)
    _hb(tmp_path / "data/ops", rc=1)
    (msg,) = _eval(tmp_path, now, {DAY: [TIP]})
    assert "rc=1" in msg


def test_a_missing_final_night_alerts_for_the_previous_slate(tmp_path: Path) -> None:
    now = datetime(2026, 11, 17, 18, 0, tzinfo=UTC)  # slate 2026-11-17, no games yet
    _hb(tmp_path / "data/ops")
    msgs = _eval(tmp_path, now, {DAY: [TIP]})
    assert msgs == [f"kalshi-prop-history: no final set for {DAY}"]
    _final(tmp_path / "data", DAY)
    assert _eval(tmp_path, now, {DAY: [TIP]}) == []


def test_an_unreadable_schedule_is_left_to_the_game_day_probe(tmp_path: Path) -> None:
    _hb(tmp_path / "data/ops")

    def broken(_d: Path, _day: date) -> dict[str, datetime]:
        raise FileNotFoundError("no schedule")

    alerts = evaluate_prop_history(
        TIP, tmp_path / "data/ops", data_dir=tmp_path / "data", tips_fn=broken
    )
    assert alerts == []  # the game-day probe owns "cannot read the slate"; nothing raises


def test_check_runs_it_and_a_skipping_job_is_seen_by_the_slot_spec(tmp_path: Path) -> None:
    ops = tmp_path / "ops"
    now = datetime(2026, 11, 17, 16, 0, tzinfo=UTC)  # 11:00 ET, no slate checks due
    spec = next(s for s in JOBS if s.name == PROP_HISTORY_JOB)
    start = last_due(spec, now) - timedelta(minutes=5)  # no start for 50 min: skipping
    _hb(ops, start_ts=start.isoformat(), end_ts=(start + timedelta(seconds=4)).isoformat())
    (ops / "heartbeat" / f"{PROP_HISTORY_JOB}.skip.json").write_text(
        json.dumps({"status": "skipped", "consecutive_skips": 6, "reason": "heavy.lock held"})
    )
    fresh = check(ops, now, "", None, None, gameday=lambda _n, _o: [])
    keys = {a.key for a in fresh}
    assert f"{PROP_HISTORY_JOB}:skipped" in keys and f"{PROP_HISTORY_JOB}:stale" in keys
    assert any("heavy.lock held" in a.message for a in fresh)
