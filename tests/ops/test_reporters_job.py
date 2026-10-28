"""The reporter poll job: the watchdog knows it (optional), and the launchd sketch is sane."""

from __future__ import annotations

import json
import plistlib
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nba.ops.watchdog import JOBS, evaluate

NOW = datetime(2026, 10, 28, 21, 0, tzinfo=UTC)
PLIST = Path(__file__).resolve().parents[2] / "ops" / "launchd" / "local.nba.reporters.plist"
SPEC = next(j for j in JOBS if j.name == "reporters")


def hb(d: Path, start: datetime, rc: int | None, end: bool = True) -> None:
    d.mkdir(parents=True, exist_ok=True)
    rec = {
        "job": "reporters",
        "start_ts": start.isoformat(),
        "end_ts": (start + timedelta(seconds=3)).isoformat() if end else None,
        "rc": rc,
    }
    (d / "reporters.json").write_text(json.dumps(rec))


def ev(d: Path, launchctl: str = "") -> list[str]:
    return [a.key for a in evaluate(NOW, d, launchctl, NOW - timedelta(days=2), None, jobs=(SPEC,))]


def test_not_installed_means_silent(tmp_path: Path) -> None:
    assert SPEC.optional and ev(tmp_path / "hb") == []


def test_fresh_heartbeat_is_healthy_and_a_stale_or_failing_one_alerts(tmp_path: Path) -> None:
    d = tmp_path / "hb"
    hb(d, NOW - timedelta(minutes=4), 0)
    assert ev(d) == []
    hb(d, NOW - timedelta(minutes=4), 2)  # e.g. the token is missing: the tick refuses, rc 2
    assert ev(d) == ["reporters:rc"]
    hb(d, NOW - timedelta(hours=2), 0)  # the job stopped starting
    assert ev(d) == ["reporters:stale"]


def test_installed_but_never_started_alerts(tmp_path: Path) -> None:
    assert ev(tmp_path / "hb", "-\t0\tlocal.nba.reporters\n") == ["reporters:stale"]


def test_plist_sketch_launches_through_uv_and_is_valid_xml() -> None:
    plist = plistlib.loads(PLIST.read_bytes())
    args = plist["ProgramArguments"]
    assert plist["Label"] == "local.nba.reporters" and plist["StartInterval"] == 300
    assert args[0].endswith("/uv") and "/bin/sh" not in args and "sh" not in args
    assert args[-4:] == ["python", "-m", "nba.ingest.reporters", "tick"]
    assert "--env-file" in args and ".env" in args  # the token comes from .env, never the plist
    text = PLIST.read_text()
    assert not re.search(r"(?i)bearer|AAAA|token=", text.replace("X bearer token is read", ""))
    comment = text[text.index("<!--") : text.index("-->", text.index("<!--") + 4)]
    assert "--" not in comment[4:]  # a double hyphen is illegal inside an XML comment
    assert "NOT INSTALLED" in comment
