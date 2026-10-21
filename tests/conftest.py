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


@pytest.fixture(autouse=True)
def _isolate_schedule_cache(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The export / top_source team fallback reads data/schedule_cache; fixture games reuse real
    game ids, so never let the real cached schedule supply their teams. Tests that need a cache
    pass ``schedule_cache_dir``."""
    empty: Path = tmp_path_factory.mktemp("no_schedule_cache")
    monkeypatch.setattr("nba.daily.schedule.DEFAULT_SCHEDULE_CACHE_DIR", empty)
