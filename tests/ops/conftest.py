"""Ops tests never read the real schedule: the game-day probe is off unless a test passes it."""

from __future__ import annotations

import pytest

import nba.ops.watchdog as wd


@pytest.fixture(autouse=True)
def _no_real_gameday_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wd, "evaluate_game_day", lambda now, ops_dir, **kw: [])
