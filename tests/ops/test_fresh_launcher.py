"""ops/nba_live_capture.sh and the fresh-interval CLI the launcher calls (review F1/F3/F4/F6).

The launcher runs under a stub uv/caffeinate in a tmp root; nothing touches the network or data/.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from nba.ops import live_capture as lc
from nba.ops.watchdog import live_fresh_problem

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "ops" / "nba_live_capture.sh"


def test_fresh_interval_cli_without_date(capsys: pytest.CaptureFixture[str]) -> None:
    assert lc.main(["fresh-interval"]) == 0
    assert capsys.readouterr().out.strip() == "0 off (config 0)"


def test_plan_still_requires_date() -> None:
    with pytest.raises(SystemExit) as e:
        lc.main(["plan"])
    assert e.value.code == 2


STUB_UV = """#!/bin/sh
echo "$*" >> "$FAKE_UV_LOG"
case "$*" in
  *live_capture\\ plan*) echo "start first tip" ;;
  *live_capture\\ fresh-interval*)
    if [ -n "${FAKE_FRESH_OUT:-}" ]; then echo "$FAKE_FRESH_OUT"; exit "${FAKE_FRESH_RC:-0}"; fi
    shift 4   # run --no-sync python -m
    exec "$PYTHON" -m "$@" ;;
esac
exit 0
"""


def _launch(tmp: Path, **extra: str) -> tuple[subprocess.CompletedProcess[str], list[str], str]:
    root = tmp / "root"
    (root / "configs").mkdir(parents=True)
    shutil.copy(REPO / "configs" / "live_capture.yaml", root / "configs" / "live_capture.yaml")
    uv = tmp / "uv"
    uv.write_text(STUB_UV)
    uv.chmod(0o755)
    cf = tmp / "caffeinate"
    cf.write_text('#!/bin/sh\nshift\nexec "$@"\n')  # caffeinate -i <cmd...>
    cf.chmod(0o755)
    env = dict(os.environ)
    env.update(
        NBA_ROOT=str(root),
        NBA_UV=str(uv),
        NBA_CAFFEINATE=str(cf),
        NBA_TEST_DATE_ET="2026-11-10",
        FAKE_UV_LOG=str(tmp / "uv.log"),
        PYTHON=sys.executable,
        PYTHONPATH=str(REPO),
        **extra,
    )
    r = subprocess.run(
        ["/bin/sh", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=90
    )
    calls = (tmp / "uv.log").read_text().splitlines() if (tmp / "uv.log").exists() else []
    alerts_p = root / "data" / "ops" / "ALERTS.md"
    return r, calls, alerts_p.read_text() if alerts_p.exists() else ""


def _capture_call(calls: list[str]) -> str:
    return next(c for c in calls if "nba.live capture" in c)


def test_launcher_default_config_passes_zero_and_no_alert(tmp_path: Path) -> None:
    r, calls, alerts = _launch(tmp_path)
    assert r.returncode == 0
    assert "--fresh-interval-s 0" in _capture_call(calls)
    assert alerts == ""


def test_launcher_config_15_without_real_fixtures_is_refused_with_alert(tmp_path: Path) -> None:
    root_cfg = tmp_path / "root" / "configs" / "live_capture.yaml"
    # build the root first by a dry launch, then flip the value and launch again
    _launch(tmp_path)
    root_cfg.write_text("espn_fresh_interval_s: 15\n")
    (tmp_path / "uv.log").unlink()
    r, calls, alerts = _launch_again(tmp_path)
    assert r.returncode == 0
    assert "--fresh-interval-s 0" in _capture_call(calls)
    assert "fresh poller not started" in alerts and "refused" in alerts


def _launch_again(tmp: Path) -> tuple[subprocess.CompletedProcess[str], list[str], str]:
    env = dict(os.environ)
    root = tmp / "root"
    env.update(
        NBA_ROOT=str(root),
        NBA_UV=str(tmp / "uv"),
        NBA_CAFFEINATE=str(tmp / "caffeinate"),
        NBA_TEST_DATE_ET="2026-11-10",
        FAKE_UV_LOG=str(tmp / "uv.log"),
        PYTHON=sys.executable,
        PYTHONPATH=str(REPO),
    )
    r = subprocess.run(
        ["/bin/sh", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=90
    )
    calls = (tmp / "uv.log").read_text().splitlines()
    return r, calls, (root / "data" / "ops" / "ALERTS.md").read_text()


@pytest.mark.parametrize(
    ("out", "rc", "want"),
    [
        ("15 on", 0, "15"),
        ("5 on", 0, "0"),
        ("1.2.3 on", 0, "0"),
        ("14 on", 0, "0"),
        ("30 on", 0, "30"),
        ("1.2.3 refused", 0, "0"),
        ("15", 0, "0"),
        ("15 on", 1, "0"),
    ],
)
def test_launcher_passes_value_only_when_output_says_on(
    tmp_path: Path, out: str, rc: int, want: str
) -> None:
    _, calls, _ = _launch(tmp_path, FAKE_FRESH_OUT=out, FAKE_FRESH_RC=str(rc))
    got = _capture_call(calls).split("--fresh-interval-s ")[1].split()[0]
    assert got == want


def test_fresh_trouble_has_its_own_watchdog_text(tmp_path: Path) -> None:
    hb_dir = tmp_path / "heartbeat"
    hb_dir.mkdir()
    now = datetime(2026, 11, 10, 1, 0, tzinfo=UTC)
    hb = (
        '{"date": "2026-11-10", "status": "running", "fresh_alive": true, "fresh_live_games": 1,'
        ' "fresh_last_game_success_ts": "2026-11-10T00:40:00+00:00"}'
    )
    path = hb_dir / lc.HEARTBEAT
    path.write_text(hb)
    tips = [datetime(2026, 11, 10, 0, 0, tzinfo=UTC)]
    day = date(2026, 11, 10)
    os.utime(path, (now.timestamp(), now.timestamp()))  # capture beat is fresh
    assert "fresh poller" in (live_fresh_problem(tmp_path, now, tips, day) or "")
    assert lc.problem(now, tips, lc.read_json(path), lc.beat_time(path, None), day) is None
    # R1: stale capture heartbeat + poller on -> the livecap alert owns it, no livefresh alert
    stale = now.timestamp() - 3600
    os.utime(path, (stale, stale))
    assert lc.problem(now, tips, lc.read_json(path), lc.beat_time(path, None), day) is not None
    assert live_fresh_problem(tmp_path, now, tips, day) is None
