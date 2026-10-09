"""M-B / minors from the 2026-10-09 hardening review: T-30 vs pretip overlap, schedule-cache age
bound, shadow-error alerting, read-only opener lock retry."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest

from nba.daily.__main__ import alert_shadow_errors
from nba.daily.pipeline import RunSummary
from nba.daily.schedule import ScheduledGame, load_schedule_cache, save_schedule_cache
from nba.db.connect import attach_read_only_with_retry, connect_with_retry, is_lock_error
from tests.daily.test_review_fixes import _holder

REPO = Path(__file__).resolve().parents[2]


# --- schedule cache age bound -------------------------------------------------------------
def test_stale_schedule_cache_is_refused(tmp_path: Path) -> None:
    from datetime import datetime

    games = [ScheduledGame("g1", datetime(2026, 10, 20, 23, 0), 1, 2)]
    path = save_schedule_cache(games, "2026-27", tmp_path)
    age = timedelta(hours=40).total_seconds()
    os.utime(path, (time.time() - age, time.time() - age))
    assert load_schedule_cache("2026-27", tmp_path) is not None  # unbounded legacy call
    assert load_schedule_cache("2026-27", tmp_path, max_age=timedelta(hours=36)) is None
    fresh = time.time() - timedelta(hours=2).total_seconds()
    os.utime(path, (fresh, fresh))
    assert load_schedule_cache("2026-27", tmp_path, max_age=timedelta(hours=36)) == games


def test_pipeline_refuses_stale_cached_schedule(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    from nba.daily.pipeline import run_daily
    from tests.daily.conftest import RUN_DATE, before_tip, slate_games

    cache = tmp_path / "sched"
    kw = dict(skip_ingest=True, skip_injury=True, with_props=False, schedule_cache_dir=cache)
    run_daily(con, RUN_DATE, schedule_fn=lambda x: slate_games(), now=before_tip(), **kw)  # type: ignore[arg-type]
    old = time.time() - timedelta(hours=50).total_seconds()
    for f in cache.glob("schedule_*.json"):
        os.utime(f, (old, old))

    def down(season: str) -> list[ScheduledGame]:
        raise ConnectionError("schedule endpoint down")

    with pytest.raises(ConnectionError):
        run_daily(con, RUN_DATE, schedule_fn=down, now=before_tip(60), **kw)  # type: ignore[arg-type]


# --- shadow errors -> ALERTS.md -------------------------------------------------------------
def test_shadow_errors_write_an_alert_line(tmp_path: Path) -> None:
    clean = RunSummary(run_id="r0", run_date=date(2026, 10, 20))
    assert alert_shadow_errors(clean, tmp_path) is False
    assert not (tmp_path / "ALERTS.md").exists()
    s = RunSummary(run_id="r1", run_date=date(2026, 10, 20))
    s.shadow_errors.append("_lt arm: ValueError: boom")
    assert alert_shadow_errors(s, tmp_path) is True
    text = (tmp_path / "ALERTS.md").read_text()
    assert "[daily-run]" in text and "r1" in text and "_lt arm" in text


# --- read-only openers retry on the writer lock ----------------------------------------------
def test_read_only_open_and_attach_wait_for_a_writer(tmp_path: Path) -> None:
    db = tmp_path / "w.duckdb"
    holder = _holder(db, 1.5)
    try:
        t0 = time.monotonic()
        ro = connect_with_retry(
            db, read_only=True, max_wait_s=30, first_delay_s=0.3, max_delay_s=0.5
        )
        assert time.monotonic() - t0 >= 0.5
        ro.close()
    finally:
        holder.kill()
    holder = _holder(db, 1.5)
    try:
        mem = duckdb.connect(":memory:")
        attach_read_only_with_retry(
            mem, db, "nba", max_wait_s=30, first_delay_s=0.3, max_delay_s=0.5
        )
        mem.execute("SELECT 1 FROM information_schema.tables LIMIT 1")
    finally:
        holder.kill()


def test_attach_read_only_is_bounded(tmp_path: Path) -> None:
    db = tmp_path / "w.duckdb"
    holder = _holder(db, 20)
    try:
        mem = duckdb.connect(":memory:")
        with pytest.raises(duckdb.IOException) as exc:
            attach_read_only_with_retry(mem, db, "nba", max_wait_s=1.0, first_delay_s=0.2)
        assert is_lock_error(exc.value)
    finally:
        holder.kill()


# --- lineups.sh: poll lock released before T-30; rc 75 -> skip record -------------------------
def _env(tmp: Path, t30_rc: int) -> dict[str, str]:
    uv = tmp / "fake_uv"
    uv.write_text(
        '#!/bin/sh\necho "$*" >> "$FAKE_UV_LOG"\n'
        f'case "$*" in *run-t30*) '
        'if [ -d "$NBA_ROOT/data/ops/lineups_lock" ]; then '
        'echo POLL_LOCK_HELD >> "$FAKE_UV_LOG"; fi; '
        f"exit {t30_rc};; esac\nexit 0\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    return {
        **os.environ,
        "NBA_ROOT": str(tmp / "root"),
        "NBA_UV": str(uv),
        "FAKE_UV_LOG": str(tmp / "uv.log"),
        "NBA_TEST_HOUR_ET": "19",
    }


def _tick(tmp: Path, t30_rc: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(REPO / "ops" / "nba_lineups.sh")],
        env=_env(tmp, t30_rc),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _hb(tmp: Path) -> Path:
    return tmp / "root" / "data" / "ops" / "heartbeat"


def test_t30_runs_without_the_poll_lock_and_rc75_writes_skip_record(tmp_path: Path) -> None:
    (tmp_path / "root" / "data" / "ops").mkdir(parents=True)
    for expected in (1, 2):
        res = _tick(tmp_path, t30_rc=75)
        assert res.returncode == 0, res.stderr
        skip = json.loads((_hb(tmp_path) / "lineups_t30.skip.json").read_text())
        assert skip["status"] == "skipped" and skip["consecutive_skips"] == expected
    log = (tmp_path / "uv.log").read_text()
    assert "run-t30" in log and "POLL_LOCK_HELD" not in log  # polling is not blocked by T-30
    ops = tmp_path / "root" / "data" / "ops"
    assert not (ops / "lineups_lock").exists() and not (ops / "lineups_t30_lock").exists()
    hb = json.loads((_hb(tmp_path) / "lineups.json").read_text())
    assert hb["rc"] == 0 and hb["end_ts"]  # poll heartbeat is clean; the deferral is a skip record
    assert _tick(tmp_path, t30_rc=0).returncode == 0
    assert not (_hb(tmp_path) / "lineups_t30.skip.json").exists()  # success clears it


def test_second_tick_does_not_start_a_concurrent_t30_run(tmp_path: Path) -> None:
    ops = tmp_path / "root" / "data" / "ops"
    holder = subprocess.Popen(["sleep", "30"])
    try:
        (ops / "lineups_t30_lock").mkdir(parents=True)
        (ops / "lineups_t30_lock" / "pid").write_text(str(holder.pid))
        assert _tick(tmp_path).returncode == 0
        log = (tmp_path / "uv.log").read_text()
        assert "lineups poll" in log and "run-t30" not in log  # still polled
        assert (ops / "lineups_t30_lock").exists()  # the running t30's lock is not stolen
    finally:
        holder.kill()


def test_watchdog_alerts_on_repeated_t30_deferral(tmp_path: Path) -> None:
    from nba.ops.watchdog import SKIP_ONLY
    from tests.ops.test_watchdog import _all_fresh, _eval

    hb = tmp_path / "hb"
    _all_fresh(hb)
    assert not [a for a in _eval(tmp_path) if a.key == "lineups_t30:skipped"]
    n = SKIP_ONLY["lineups_t30"]
    (hb / "lineups_t30.skip.json").write_text(
        json.dumps({"status": "skipped", "consecutive_skips": n - 1})
    )
    assert not [a for a in _eval(tmp_path) if a.key == "lineups_t30:skipped"]
    (hb / "lineups_t30.skip.json").write_text(
        json.dumps({"status": "skipped", "consecutive_skips": n})
    )
    assert [a for a in _eval(tmp_path) if a.key == "lineups_t30:skipped"]
