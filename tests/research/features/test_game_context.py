"""Tests for research/features/game_location_context.py and the context screen helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import polars as pl

from research.eval.context_screen_games import _p_two_sided, _stat, bh_reject
from research.features.game_location_context import (
    bucket_for_hour,
    build_context_features,
    load_team_markets,
)

CFG = Path(__file__).resolve().parents[3] / "configs" / "team_markets.yaml"
BOS, LAL, DEN, NYK = 1610612738, 1610612747, 1610612743, 1610612752


def _sched(rows: list[tuple[str, int, int, datetime, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": [r[0] for r in rows],
            "home_team": [r[1] for r in rows],
            "away_team": [r[2] for r in rows],
            "tip_utc": [r[3] for r in rows],
            "arenaName": ["x"] * len(rows),
            "arenaCity": ["c"] * len(rows),
            "arenaState": [r[4] for r in rows],
            "gameLabel": [""] * len(rows),
            "gameSubLabel": [""] * len(rows),
            "gameSubtype": [""] * len(rows),
            "isNeutral": [False] * len(rows),
        }
    )


def test_team_markets_contract() -> None:
    t = load_team_markets(CFG)
    assert len(t) == 30
    assert float(str(t[DEN]["altitude_m"])) > 1500
    assert float(str(t[NYK]["metro_pop"])) == float(str(t[1610612751]["metro_pop"]))


def test_bucket_boundaries() -> None:
    assert [bucket_for_hour(h) for h in (12, 16.99, 17, 18.5, 19, 20.5, 21, 23)] == [
        "matinee", "matinee", "early", "early", "standard", "standard", "late", "late",
    ]  # fmt: skip


def test_bodyclock_west_to_east_early() -> None:
    t = load_team_markets(CFG)
    # 7pm ET on 2023-01-10 (winter) = 00:00 UTC next day; LAL visitor body clock = 16:00.
    s = _sched(
        [
            ("0022200001", BOS, LAL, datetime(2023, 1, 11, 0, 0), "MA"),
            ("0022200002", LAL, BOS, datetime(2023, 1, 11, 3, 0), "CA"),
        ]
    )
    f = build_context_features(s, t).sort("game_id")
    g1, g2 = f.row(0, named=True), f.row(1, named=True)
    assert g1["away_bodyclock_hr"] == 16.0 and g1["tz_shift_east_hr"] == 3.0
    assert g1["west_to_east_early"] == 1 and g1["tip_bucket"] == "standard"
    assert g2["away_bodyclock_hr"] == 22.0 and g2["west_to_east_early"] == 0
    assert g1["is_neutral"] == 0


def test_features_unchanged_when_future_games_removed() -> None:
    """Planted-future check: a game's features cannot depend on later games."""
    t = load_team_markets(CFG)
    rows = [
        ("0022200001", BOS, LAL, datetime(2022, 10, 19, 23, 0), "MA"),
        ("0022200002", DEN, NYK, datetime(2022, 10, 21, 1, 0), "CO"),
        ("0022200003", NYK, DEN, datetime(2023, 3, 1, 1, 0), "NY"),
    ]
    full = build_context_features(_sched(rows), t).sort("game_id")
    part = build_context_features(_sched(rows[:2]), t).sort("game_id")
    assert full.head(2).drop("tip_utc").equals(part.drop("tip_utc")) or full.head(2).equals(part)


def test_stat_recovers_planted_signal_and_null() -> None:
    rng = np.random.default_rng(0)
    n = 20000
    lp = rng.normal(0.2, 0.7, n)
    z = rng.normal(size=n)
    y = (rng.random(n) < 1 / (1 + np.exp(-(lp + 0.3 * z)))).astype(float)
    c, dll = _stat(lp, z, y, np.ones(n))
    assert abs(c - 0.3) < 0.05 and dll > 0.001
    y0 = (rng.random(n) < 1 / (1 + np.exp(-lp))).astype(float)
    c0, dll0 = _stat(lp, z, y0, np.ones(n))
    assert abs(c0) < 0.05 and dll0 < 0.001


def test_bh_and_p() -> None:
    assert bh_reject([0.001, 0.04, 0.5], 0.10) == [True, True, False]
    assert bh_reject([0.2, 0.5], 0.10) == [False, False]
    assert _p_two_sided(np.array([1.0] * 100)) < 0.05
    assert _p_two_sided(np.concatenate([np.ones(50), -np.ones(50)])) > 0.9
