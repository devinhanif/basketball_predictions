"""ESPN fresh poller: config gate and watchdog check (Devin, 2026-11-10)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml

from nba.ops import live_capture as lc


def _cfg(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "live_capture.yaml"
    p.write_text(text)
    return p


def test_fresh_interval_default_off_and_refused_without_real_fixtures(tmp_path: Path) -> None:
    fx = tmp_path / "fx"
    assert lc.fresh_interval(_cfg(tmp_path, "espn_fresh_interval_s: 0\n"), fx)[0] == 0
    iv, why = lc.fresh_interval(_cfg(tmp_path, "espn_fresh_interval_s: 15\n"), fx)
    assert iv == 0 and "refused" in why and "plays_limit1.json" in why
    fx.mkdir()
    (fx / "plays_limit1.json").write_text("{}")
    assert lc.fresh_interval(_cfg(tmp_path, "espn_fresh_interval_s: 15\n"), fx)[0] == 0
    (fx / "plays_last.json").write_text("{}")
    assert lc.fresh_interval(_cfg(tmp_path, "espn_fresh_interval_s: 15\n"), fx) == (15.0, "on")
    assert lc.fresh_interval(_cfg(tmp_path, "espn_fresh_interval_s: 10\n"), fx)[0] == 0
    assert lc.fresh_interval(tmp_path / "missing.yaml", fx)[0] == 0
    assert lc.fresh_interval(_cfg(tmp_path, "espn_fresh_interval_s: abc\n"), fx)[0] == 0


def test_shipped_config_is_off_until_real_fixtures_exist() -> None:
    v = yaml.safe_load(Path("configs/live_capture.yaml").read_text())["espn_fresh_interval_s"]
    assert v == 0 or all((lc.REAL_FIXTURE_DIR / f).is_file() for f in lc.REAL_FIXTURES)


def test_fresh_problem_cases() -> None:
    now = datetime(2026, 11, 10, 1, 0, tzinfo=UTC)
    ok = {
        "fresh_alive": True,
        "fresh_live_games": 2,
        "fresh_last_game_success_ts": "2026-11-10T00:58:00+00:00",
    }
    stale = {**ok, "fresh_last_game_success_ts": "2026-11-10T00:51:00+00:00"}
    idle = {**ok, "fresh_live_games": 0, "fresh_last_game_success_ts": "2026-11-10T00:00:00+00:00"}
    dead = {**ok, "fresh_alive": False, "fresh_stopped_reason": "auth"}
    nothing = {"fresh_alive": True, "fresh_live_games": 1, "fresh_last_game_success_ts": ""}
    assert lc.fresh_problem({}, now) is None  # poller off: not judged
    assert lc.fresh_problem(ok, now) is None
    assert "no success for 9 min" in (lc.fresh_problem(stale, now) or "")
    assert lc.fresh_problem(idle, now) is None
    assert "dead" in (lc.fresh_problem(dead, now) or "")
    assert lc.fresh_problem(nothing, now) is None
    assert "no game success yet" in (
        lc.fresh_problem({**nothing, "fresh_consecutive_fail": 3}, now) or ""
    )


def test_problem_includes_fresh_when_running() -> None:
    tips = [datetime(2026, 11, 10, 0, 0, tzinfo=UTC)]
    now = datetime(2026, 11, 10, 1, 0, tzinfo=UTC)
    hb = {
        "date": "2026-11-10",
        "status": "running",
        "fresh_alive": True,
        "fresh_live_games": 1,
        "fresh_last_game_success_ts": "2026-11-10T00:40:00+00:00",
    }
    msg = lc.problem(now, tips, hb, now, now.date())
    assert msg is not None and "fresh poller" in msg
