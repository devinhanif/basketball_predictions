# ruff: noqa: E501
"""The app export job: gated on the morning heartbeat, atomic, own heartbeat, skip with a reason, plist in step with the watchdog."""

from __future__ import annotations

import json
import plistlib
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from nba.ops import app_export as A
from nba.ops.watchdog import JOBS, evaluate, last_due

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 14, 12, 35, tzinfo=ET)
TODAY = date(2026, 10, 14)


GOOD_STEPS = [(n, 0, "ok") for n in A.REQUIRED_STEPS] + [("backup_db_drive", 0, "ok")]


def _morning(
    root: Path,
    *,
    start: datetime,
    end: datetime | None,
    rc: int | None,
    steps: list[tuple[str, int, str]] | None = GOOD_STEPS,
    steps_start: datetime | None = None,
) -> None:
    hb = root / "data/ops/heartbeat"
    hb.mkdir(parents=True, exist_ok=True)
    rec = {
        "job": "daily-morning", "mode": "morning", "host": "h", "start_ts": start.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "end_ts": end.strftime("%Y-%m-%dT%H:%M:%S%z") if end else None, "rc": rc,
    }  # fmt: skip
    (hb / "daily-morning.json").write_text(json.dumps(rec))
    rec_file = hb / "daily-morning.steps"
    if steps is None:
        rec_file.unlink(missing_ok=True)
        return
    stamp = (steps_start or start).strftime("%Y-%m-%dT%H:%M:%S%z")
    rec_file.write_text(f"start_ts {stamp}\n" + "".join(f"{n} {r} {st}\n" for n, r, st in steps))


def _good_morning(root: Path) -> None:
    _morning(root, start=NOW.replace(hour=8, minute=0), end=NOW.replace(hour=10, minute=19), rc=0)


class FakeBuild:
    def __init__(self, rcs: list[int]) -> None:
        self.rcs, self.calls, self.outs = list(rcs), 0, []

    def __call__(self, day: date, out: Path) -> int:
        self.calls += 1
        self.outs.append(out)
        rc = self.rcs.pop(0)
        if rc == 0:
            out.mkdir(parents=True)
            (out / "manifest.json").write_text(
                json.dumps(
                    {
                        "export_date": day.isoformat(),
                        "made_at": (NOW.astimezone(A.UTC) + timedelta(minutes=1))
                        .replace(tzinfo=None)
                        .isoformat(),
                    }
                )
            )
            (out / "export.duckdb").write_text("x")
        return rc


def _hb(root: Path) -> dict[str, object]:
    return json.loads((root / "data/ops/heartbeat/app-export.json").read_text())


def test_builds_into_latest_atomically_with_start_and_end_heartbeat(tmp_path: Path) -> None:
    _good_morning(tmp_path)
    latest = tmp_path / "data/export/latest"
    latest.mkdir(parents=True)
    (latest / "manifest.json").write_text(
        '{"export_date": "2026-10-13", "made_at": "2026-10-13T13:00:00"}'
    )
    b = FakeBuild([0])
    assert A.run(tmp_path, now=NOW, build=b) == 0
    assert json.loads((latest / "manifest.json").read_text())["export_date"] == "2026-10-14"
    assert (
        b.outs[0].name.startswith(".latest.stage.") and not b.outs[0].exists()
    )  # built in staging, gone after the swap
    assert sorted(p.name for p in latest.parent.iterdir()) == [
        "latest"
    ]  # no stage / old directories left behind
    hb = _hb(tmp_path)
    assert (
        hb["job"] == "app-export"
        and hb["rc"] == 0
        and hb["status"] == "ok"
        and hb["start_ts"]
        and hb["end_ts"]
    )
    assert "exported to" in next((tmp_path / "data/ops").glob("app_export_*.log")).read_text()


def test_start_heartbeat_is_written_before_the_build_runs(tmp_path: Path) -> None:
    _good_morning(tmp_path)
    seen: dict[str, object] = {}

    def build(day: date, out: Path) -> int:
        seen.update(_hb(tmp_path))
        return 1

    A.run(tmp_path, now=NOW, build=build, retries=1)
    assert seen["end_ts"] is None and seen["rc"] is None and seen["status"] == "running"


@pytest.mark.parametrize(
    ("kind", "needle"),
    [
        ("missing", "missing"),
        ("running", "not ended"),
        ("stale", "stale"),
        ("data_failed", "data step failed: report"),
        ("no_steps", "step record missing"),
        ("other_run", "another run"),
        ("truncated", "not recorded ok: data_check"),
        ("garbled", "unreadable"),
    ],
)
def test_skip_with_a_recorded_reason(tmp_path: Path, kind: str, needle: str) -> None:
    if kind == "running":
        _morning(tmp_path, start=NOW.replace(hour=8), end=None, rc=None)
    elif kind == "stale":
        y = NOW - timedelta(days=1)
        _morning(tmp_path, start=y.replace(hour=8), end=y.replace(hour=9), rc=0)
    elif kind in {"data_failed", "no_steps", "other_run", "truncated", "garbled"}:
        steps: list[tuple[str, int, str]] | None = list(GOOD_STEPS)
        other = None
        if kind == "data_failed":
            steps = [(n, 1, "failed") if n == "report" else (n, r, st) for n, r, st in GOOD_STEPS]
        elif kind == "no_steps":
            steps = None
        elif kind == "other_run":
            other = NOW.replace(hour=7)
        elif kind == "truncated":
            steps = [x for x in GOOD_STEPS if x[0] != "data_check"]
        _morning(tmp_path, start=NOW.replace(hour=8), end=NOW.replace(hour=9), rc=1,
                 steps=steps, steps_start=other)  # fmt: skip
        if kind == "garbled":
            with (tmp_path / "data/ops/heartbeat/daily-morning.steps").open("a") as fh:
                fh.write("report\n")
    b = FakeBuild([0])
    assert A.run(tmp_path, now=NOW, build=b) == 0  # a skip is not an error exit
    assert b.calls == 0 and not (tmp_path / "data/export/latest").exists()
    hb = _hb(tmp_path)
    assert hb["status"] == "skipped" and hb["rc"] == 0 and needle in str(hb["reason"])
    skip = json.loads((tmp_path / "data/ops/heartbeat/app-export.skip.json").read_text())
    assert (
        skip["status"] == "skipped" and skip["consecutive_skips"] == 1 and needle in skip["reason"]
    )
    A.run(tmp_path, now=NOW, build=b)
    assert (
        json.loads((tmp_path / "data/ops/heartbeat/app-export.skip.json").read_text())[
            "consecutive_skips"
        ]
        == 2
    )


def test_backup_only_failure_still_exports(tmp_path: Path) -> None:
    steps = [(n, 0, "ok") for n in A.REQUIRED_STEPS] + [
        ("backup_registry_drive", 1, "failed"),
        ("backup_db_drive", 1, "failed"),
    ]
    _morning(tmp_path, start=NOW.replace(hour=8), end=NOW.replace(hour=10), rc=1, steps=steps)
    b = FakeBuild([0])
    assert A.run(tmp_path, now=NOW, build=b) == 0
    assert b.calls == 1 and (tmp_path / "data/export/latest/manifest.json").exists()
    assert _hb(tmp_path)["status"] == "ok"


def _steps_problem(tmp_path: Path, steps: list[tuple[str, int, str]]) -> str | None:
    start = NOW.replace(hour=8)
    _morning(tmp_path, start=start, end=NOW.replace(hour=9), rc=1, steps=steps)
    return A.data_step_problem(
        tmp_path / "data/ops/heartbeat", start.strftime("%Y-%m-%dT%H:%M:%S%z")
    )


def test_a_step_once_failed_stays_failed(tmp_path: Path) -> None:
    steps = [(n, 0, "ok") for n in A.REQUIRED_STEPS]
    steps.insert(3, ("report", 1, "failed"))
    assert _steps_problem(tmp_path, steps) == "morning data step failed: report"


def test_only_drive_backups_are_exempt(tmp_path: Path) -> None:
    ok = [(n, 0, "ok") for n in A.REQUIRED_STEPS]
    assert _steps_problem(tmp_path, [*ok, ("backup_lineups_drive", 1, "failed")]) is None
    assert "backup_local" in str(_steps_problem(tmp_path, [*ok, ("backup_local", 1, "failed")]))


def test_a_failed_advisory_stints_step_does_not_block_the_export(tmp_path: Path) -> None:
    ok = [(n, 0, "ok") for n in A.REQUIRED_STEPS]
    assert _steps_problem(tmp_path, [*ok, ("stints_nightly", 6, "failed")]) is None
    assert _steps_problem(tmp_path, [*ok, ("stints_nightly", 142, "failed")]) is None
    bad = _steps_problem(tmp_path, [("report", 1, "failed"), ("stints_nightly", 6, "failed")])
    assert bad == "morning data step failed: report"


def test_advisory_monitors_do_not_block_but_required_steps_and_data_check_do(
    tmp_path: Path,
) -> None:
    ok = [(n, 0, "ok") for n in A.REQUIRED_STEPS]
    adv = [("coverage_yesterday", 3, "failed"), ("data_check_advisory", 3, "failed")]
    assert _steps_problem(tmp_path, [*ok, *adv]) is None
    hard = [(n, 1, "failed") if n == "data_check" else (n, 0, "ok") for n in A.REQUIRED_STEPS]
    assert _steps_problem(tmp_path, [*hard, *adv]) == "morning data step failed: data_check"
    for req in ("settle", "checkpoint", "report", "parlay_settle", "data_snapshot", "data_diff"):
        steps = [(n, 1, "failed") if n == req else (n, 0, "ok") for n in A.REQUIRED_STEPS]
        assert _steps_problem(tmp_path, [*steps, *adv]) == f"morning data step failed: {req}"


def test_a_good_run_clears_the_skip_record(tmp_path: Path) -> None:
    A.run(tmp_path, now=NOW, build=FakeBuild([0]))  # no morning heartbeat: skipped
    assert (tmp_path / "data/ops/heartbeat/app-export.skip.json").exists()
    _good_morning(tmp_path)
    assert A.run(tmp_path, now=NOW, build=FakeBuild([0])) == 0
    assert not (tmp_path / "data/ops/heartbeat/app-export.skip.json").exists()


def test_a_failed_build_leaves_yesterdays_export_and_exits_nonzero(tmp_path: Path) -> None:
    _good_morning(tmp_path)
    latest = tmp_path / "data/export/latest"
    latest.mkdir(parents=True)
    (latest / "manifest.json").write_text('{"export_date": "2026-10-13"}')
    assert A.run(tmp_path, now=NOW, build=FakeBuild([3])) == 3
    assert json.loads((latest / "manifest.json").read_text())["export_date"] == "2026-10-13"
    hb = _hb(tmp_path)
    assert hb["rc"] == 3 and hb["status"] == "failed" and hb["end_ts"]
    assert sorted(p.name for p in latest.parent.iterdir()) == ["latest"]


def test_a_locked_database_is_retried_then_fails_with_rc_2(tmp_path: Path) -> None:
    _good_morning(tmp_path)
    waits: list[float] = []
    b = FakeBuild([2, 2, 0])
    assert A.run(tmp_path, now=NOW, build=b, sleep=waits.append, wait_s=7, retries=4) == 0
    assert b.calls == 3 and waits == [7, 7]
    b2 = FakeBuild([2, 2])
    assert (
        A.run(tmp_path / "x", now=NOW, build=b2, sleep=waits.append, retries=2) == 0
    )  # no morning heartbeat there: skipped, no build
    _good_morning(tmp_path / "y")
    b3 = FakeBuild([2, 2])
    assert (
        A.run(tmp_path / "y", now=NOW, build=b3, sleep=waits.append, retries=2) == 2
        and b3.calls == 2
    )


def test_a_build_that_raises_is_a_failed_run_with_a_heartbeat(tmp_path: Path) -> None:
    _good_morning(tmp_path)

    def boom(day: date, out: Path) -> int:
        raise RuntimeError("no python")

    assert A.run(tmp_path, now=NOW, build=boom, retries=1) == 1
    assert _hb(tmp_path)["status"] == "failed"


def test_the_second_slot_does_nothing_when_todays_export_is_built(tmp_path: Path) -> None:
    _good_morning(tmp_path)
    assert A.run(tmp_path, now=NOW, build=FakeBuild([0])) == 0
    b = FakeBuild([0])
    later = NOW.replace(hour=14)
    assert A.run(tmp_path, now=later, build=b) == 0
    assert (
        b.calls == 0
        and _hb(tmp_path)["reason"] == "already built"
        and _hb(tmp_path)["status"] == "ok"
    )


def test_swap_dir_restores_the_old_export_if_the_rename_fails(tmp_path: Path) -> None:
    final, stage = tmp_path / "latest", tmp_path / "stage"
    final.mkdir()
    (final / "a").write_text("old")
    with pytest.raises(OSError):
        A.swap_dir(stage, final)  # stage does not exist
    assert (final / "a").read_text() == "old"


# --------------------------------------------------------------------------- the plist and the watchdog
PLIST = Path(__file__).resolve().parents[2] / "ops/launchd/local.nba.app-export.plist"


def test_plist_is_in_step_with_the_watchdog_spec() -> None:
    p = plistlib.loads(PLIST.read_bytes())
    spec = next(j for j in JOBS if j.name == "app-export")
    assert p["Label"] == spec.label == "local.nba.app-export"
    slots = sorted((d["Hour"], d["Minute"]) for d in p["StartCalendarInterval"])
    assert slots == sorted((h, m) for h in spec.hours for m in spec.minutes)
    args = p["ProgramArguments"]
    assert (
        args[0].endswith("/uv")
        and "/bin/sh" not in " ".join(args)
        and args[-2:] == ["-m", "nba.ops.app_export"]
    )
    assert p["WorkingDirectory"].endswith("nba-prediction")


def test_watchdog_is_silent_before_install_and_alerts_once_loaded_or_started(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 10, 14, 13, 30, tzinfo=ET)
    hb = tmp_path / "hb"
    hb.mkdir()
    kw = {"tz": ET}
    quiet = [
        a.key for a in evaluate(now, hb, "", now, None, **kw) if a.key.startswith("app-export")
    ]
    assert quiet == []  # never installed: no heartbeat, label not loaded
    loaded = "-\t0\tlocal.nba.app-export\n"
    gone = [
        a.key
        for a in evaluate(now, hb, loaded, now - timedelta(days=1), None, **kw)
        if a.key.startswith("app-export")
    ]
    assert gone == ["app-export:stale"]  # installed but never started
    due = last_due(next(j for j in JOBS if j.name == "app-export"), now, ET)
    (hb / "app-export.json").write_text(
        json.dumps(
            {
                "job": "app-export",
                "start_ts": (due + timedelta(seconds=20)).isoformat(),
                "end_ts": (due + timedelta(minutes=2)).isoformat(),
                "rc": 0,
            }
        )
    )
    assert [
        a.key for a in evaluate(now, hb, loaded, now, None, **kw) if a.key.startswith("app-export")
    ] == []
    (hb / "app-export.json").write_text(
        json.dumps(
            {
                "job": "app-export",
                "start_ts": (due + timedelta(seconds=20)).isoformat(),
                "end_ts": (due + timedelta(minutes=2)).isoformat(),
                "rc": 2,
            }
        )
    )
    assert [
        a.key for a in evaluate(now, hb, loaded, now, None, **kw) if a.key.startswith("app-export")
    ] == ["app-export:rc"]


def test_two_consecutive_skips_alert_with_the_recorded_reason(tmp_path: Path) -> None:
    now = datetime(2026, 10, 14, 13, 30, tzinfo=ET)
    hb = tmp_path / "hb"
    hb.mkdir()
    due = last_due(next(j for j in JOBS if j.name == "app-export"), now, ET)
    (hb / "app-export.json").write_text(
        json.dumps(
            {
                "job": "app-export",
                "start_ts": (due + timedelta(seconds=20)).isoformat(),
                "end_ts": (due + timedelta(minutes=1)).isoformat(),
                "rc": 0,
                "status": "skipped",
            }
        )
    )
    skip = {
        "job": "app-export",
        "status": "skipped",
        "consecutive_skips": 2,
        "reason": "morning job exited rc=1",
    }
    (hb / "app-export.skip.json").write_text(json.dumps(skip))
    out = [a for a in evaluate(now, hb, "", now, None, tz=ET) if a.key == "app-export:skipped"]
    assert len(out) == 1 and "morning job exited rc=1" in out[0].message
    skip["consecutive_skips"] = 1
    (hb / "app-export.skip.json").write_text(json.dumps(skip))
    assert [
        a for a in evaluate(now, hb, "", now, None, tz=ET) if a.key == "app-export:skipped"
    ] == []


def test_install_note_has_the_kickstart() -> None:
    root = Path(__file__).resolve().parents[2]
    assert "launchctl kickstart -k gui/$(id -u)/local.nba.app-export" in PLIST.read_text()
    assert (
        "launchctl kickstart -k gui/$(id -u)/local.nba.app-export"
        in (root / "docs/DAILY_PIPELINE.md").read_text()
    )
