"""The core-API plays parser on REAL bodies (Devin, 2026-11-10 gate for the fresh poller).

Fixtures (trimmed real responses of ``plays?limit=1`` and ``plays?limit=1&page=<count>``) live
in ``tests/live/fixtures/espn_core_real/``. Absent -> skipped with a message, and the launcher
refuses a non-zero ``espn_fresh_interval_s`` (nba.ops.live_capture.fresh_interval).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from nba.live.espn_fresh import last_play_core
from nba.ops.live_capture import REAL_FIXTURE_DIR, REAL_FIXTURES

pytestmark = pytest.mark.skipif(
    not all((REAL_FIXTURE_DIR / f).is_file() for f in REAL_FIXTURES),
    reason=f"real core-API bodies not present in {REAL_FIXTURE_DIR} "
    "(plays_limit1.json, plays_last.json); poller must stay off (espn_fresh_interval_s: 0)",
)


def _load(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((REAL_FIXTURE_DIR / name).read_text())
    return body


@pytest.mark.parametrize("name", REAL_FIXTURES)
def test_real_core_body_parses_to_a_play(name: str) -> None:
    body = _load(name)
    lp = last_play_core(body)
    assert lp is not None, f"{name}: items missing or empty (top-level keys {sorted(body)})"
    assert lp.play_id and lp.wallclock is not None and lp.period and lp.n_plays


def test_last_page_is_not_older_than_first() -> None:
    first = last_play_core(_load("plays_limit1.json"))
    last = last_play_core(_load("plays_last.json"))
    assert first and last and first.wallclock and last.wallclock
    assert last.wallclock >= first.wallclock
    assert (last.n_plays or 0) >= (first.n_plays or 0)
