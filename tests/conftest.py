"""Shared test isolation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# LightGBM and torch each bundle an OpenMP runtime; real work with both in one process crashes on
# macOS, and WHICH order is safe depends on what else is imported. Two measured facts (2026-11-16):
#  * `pytest tests/research/props` alone: test_cli.py imports torch at collection, then the first
#    LightGBM fit segfaults. Importing LightGBM first (below) fixes it; the whole suite is fine.
#  * `pytest tests/research/models/test_rung4_stepheads.py` alone (the Makefile's second process):
#    with LightGBM loaded first the torch training step segfaults, so that process must not preload
#    (the `--ignore=...rung4...` of the first process does not count).
if not any("rung4_stepheads" in a and not a.startswith("--ignore") for a in sys.argv):
    import lightgbm  # noqa: F401


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
SLOW_PREFIXES: tuple[str, ...] = (
    # whole files: module fixtures / most tests over ~10 s (measured 2026-11-16, 8 GB Mac, nice 15)
    # NOT here on purpose (adversary M2, 2026-11-17): tests/daily/test_checkpoint.py,
    # tests/eval/test_avg_shadow_live.py and tests/eval/test_full_shadow_live.py are the only
    # tests of the nightly checkpoint step and of the two frozen shadow-rule readers, so they
    # stay in the blocking fast set although they take about a minute together.
    "tests/research/lineups_known_rebase/test_lineups_known_rebase.py",
    "tests/research/seq_learning_curve/test_eval.py",
    "tests/research/seq_learning_curve_2/test_eval.py",
    "tests/research/workload_pts_v2/test_workload_pts_v2_eval.py",
    "tests/research/near_miss_bundle/",
    "tests/research/router_g2/test_router_g2_eval.py",
    "tests/research/eval/test_alt_ladder_hist_eval.py",
    "tests/research/eval/test_alt_parlay_x_eval.py",
    "tests/research/eval/test_alt_line_70_eval.py",
    "tests/research/eval/test_history_window_eval.py",
    # single tests over ~10 s
    "tests/research/props/test_ctxres_v3.py::test_job_end_to_end_synthetic_is_deterministic",
    "tests/research/seq_full_mix/test_rt_job.py::test_held_out_run_refuses_without_the_holdout_row",
    "tests/research/seq_learning_curve/test_job.py::test_run_script_refuses_without_the_review",
    "tests/research/teammate_sits/test_run_harness.py::test_first_match_order_and_unresolved",
    "tests/research/eval/test_referees_eval.py::test_pipeline_smoke_end_to_end_on_a_synthetic_league",
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        base = item.nodeid.split("[", 1)[0]
        if any(base.startswith(p) for p in SLOW_PREFIXES):
            item.add_marker(pytest.mark.slow)
