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


# --- slow marker -------------------------------------------------------------------------------
# Tests (or whole files / directories) measured at > ~10 s are marked `slow` HERE, centrally, so
# `make test-fast` (-m "not slow") stays quick and no test file needs editing. Central on purpose:
# tests/research/{pplay_minutes_valid,seq_learning_curve,seq_learning_curve_2} are in the
# REVIEWED_PATHS of their frozen rules, so touching them (even a marker) would void the loader
# review. A prefix ending in ".py" or "/" matches that file or directory; anything else matches a
# test function (parametrized ids ignored). Re-measure with:
#   uv run pytest --no-cov -q --durations=0 --durations-min=10
# (details in docs/handoff_2026-11-17/lane_ci.md). A function prefix also matches longer names.
SLOW_PREFIXES: tuple[str, ...] = ()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        base = item.nodeid.split("[", 1)[0]
        if any(base.startswith(p) for p in SLOW_PREFIXES):
            item.add_marker(pytest.mark.slow)
