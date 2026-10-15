# ruff: noqa: E501
"""Watchdog drill: a temporary copy of the heartbeat dir, a missed pretip and a failed morning job, both raised; the source is never written."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from nba.ops.watchdog import SHADOW_JOBS, ShadowJob, drill, evaluate_shadow_jobs, main

ET = ZoneInfo("America/New_York")


def _snapshot(d: Path) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(d.rglob("*"))
        if p.is_file()
    }


def _source(tmp: Path) -> Path:
    src = tmp / "ops" / "heartbeat"
    src.mkdir(parents=True)
    (tmp / "ops" / "schedule_tz").write_text("America/New_York\n")
    # a live-looking dir that is already unhealthy: the drill must not depend on it, and must not write to it
    (src / "daily-morning.json").write_text(
        json.dumps(
            {
                "job": "daily-morning",
                "start_ts": "2026-10-14T07:12:14-0500",
                "end_ts": "2026-10-14T09:19:16-0500",
                "rc": 1,
            }
        )
    )
    (src / "avg_shadow.json").write_text(json.dumps({"job": "avg_shadow", "consecutive_bad": 9}))
    return src


def test_the_drill_raises_both_alerts_and_leaves_the_source_alone(tmp_path: Path) -> None:
    src = _source(tmp_path)
    before = _snapshot(tmp_path)
    for now in (
        datetime(2026, 10, 14, 17, 0, tzinfo=ET),
        datetime(2026, 10, 15, 3, 10, tzinfo=ET),
        datetime(2026, 10, 14, 9, 21, tzinfo=ET),
        datetime(2026, 10, 14, 22, 40, tzinfo=ET),
    ):
        res = drill(src, now=now)
        assert res["baseline_alerts"] == [], now
        assert res["missed_pretip_raised"] is True and res["failed_morning_raised"] is True, (
            now,
            res,
        )
        assert set(res["raised"]) == {"daily-pretip:stale", "daily-morning:rc"}  # type: ignore[call-overload]
        assert len(res["notifications"]) == 1 and len(res["alerts_md"]) == 2  # type: ignore[arg-type]
    assert _snapshot(tmp_path) == before  # the source (and everything beside it) is byte-identical


def test_the_cli_exit_code_is_zero_only_when_both_alerts_fire(
    tmp_path: Path, capsys: object
) -> None:
    src = _source(tmp_path)
    assert main(["drill", "--hb-dir", str(src)]) == 0


def test_combo_shadow_is_registered_like_the_avg_shadow(tmp_path: Path) -> None:
    assert (
        ShadowJob("combo_shadow", 3) in SHADOW_JOBS and ShadowJob("avg_shadow", 3, 3) in SHADOW_JOBS
    )
    now = datetime(2026, 10, 20, 15, 0, tzinfo=ET)
    hb = tmp_path
    stamp = (now - timedelta(minutes=5)).replace(tzinfo=None).isoformat()
    rec = {
        "job": "combo_shadow",
        "start_ts": stamp,
        "end_ts": stamp,
        "rc": 1,
        "status": "failed",
        "consecutive_bad": 2,
    }
    (hb / "combo_shadow.json").write_text(json.dumps(rec))
    assert (
        evaluate_shadow_jobs(now.astimezone(ET), hb, ET) == []
    )  # two in a row is normal contention
    rec["consecutive_bad"] = 3
    (hb / "combo_shadow.json").write_text(json.dumps(rec))
    assert [a.key for a in evaluate_shadow_jobs(now, hb, ET)] == ["combo_shadow:failed"]
    rec.update(
        end_ts=None,
        consecutive_bad=0,
        start_ts=(now - timedelta(minutes=45)).replace(tzinfo=None).isoformat(),
    )
    (hb / "combo_shadow.json").write_text(json.dumps(rec))
    assert [a.key for a in evaluate_shadow_jobs(now, hb, ET)] == ["combo_shadow:hung"]
    (hb / "combo_shadow.json").unlink()
    assert evaluate_shadow_jobs(now, hb, ET) == []  # flag off: no file, no alert
