"""Shared test isolation."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_schedule_tips(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fixture games reuse real-looking game ids; never let the cached real schedule
    (data/schedule) leak real tip-off times into them. Tests that need tips pass them."""
    empty: Path = tmp_path_factory.mktemp("no_schedule")
    monkeypatch.setattr("nba.features.game_tipoff.SCHEDULE_DIR", empty)
