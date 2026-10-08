"""Fixture-scale tests for ``nba.eval.time_decay_tune``."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from nba.db.connect import connect
from nba.eval import time_decay_tune as t
from nba.features.time_decay import TimeDecayConfig
from tests.features.test_player_shot_rates_time_decay import _game, _pgs, _shot


def _db():  # type: ignore[no-untyped-def]
    con = connect(":memory:")
    for s in (2022, 2023, 2024, 2025):
        for i in range(3):
            gid = f"{s}-{i}"
            _game(con, gid, f"{s}-11-0{i + 1}", s)
            _pgs(con, gid, 7)
            for j in range(4):
                _shot(con, gid, j, 7, "FGM2" if (j + i) % 2 else "FGA_miss")
    return con


def test_refuses_holdout_season() -> None:
    con = _db()
    with pytest.raises(ValueError, match="frozen holdout"):
        t.run_time_decay_tune(con, max_season=2025)


def test_actuals_exclude_holdout_and_tune_runs_on_fixture() -> None:
    con = _db()
    act = t._load_actuals(con, 2024)
    assert act["season"].max() == 2024
    res = t.run_time_decay_tune(
        con,
        n_boot=50,
        configs=[TimeDecayConfig(half_life_seasons=1.0), TimeDecayConfig(half_life_seasons=2.0)],
        tune_ages=False,
    )
    assert res.report_seasons == [2024] and 2025 not in res.select_seasons
    assert len(res.trials) == 2
    assert {s.name for s in res.report_slices} >= {"first_15", "all"}
    assert isinstance(res.keep_flag_on, bool)


def test_planted_2025_rows_do_not_change_tuned_scores() -> None:
    a, b = _db(), _db()
    # Extreme extra 2025 game in b only.
    _game(b, "x", "2025-12-30", 2025)
    _pgs(b, "x", 7)
    for j in range(30):
        _shot(b, "x", j, 7, "FGM3", "above3")
    cfg = TimeDecayConfig()
    ea = t.score_config(a, t._load_actuals(a, 2024), True, cfg)
    eb = t.score_config(b, t._load_actuals(b, 2024), True, cfg)
    assert np.array_equal(ea, eb, equal_nan=True)


def test_decide_keep_rule() -> None:
    from nba.props.metrics import ConfidenceInterval as CI

    def sl(name: str, p: float, lo: float, hi: float) -> t.SliceDelta:
        return t.SliceDelta(name, 100, 1.0, 1.0, CI(p, lo, hi, 100))

    assert t.decide_keep([sl("first_15", -0.1, -0.2, -0.01), sl("rest", -0.01, -0.03, 0.01)])
    assert not t.decide_keep([sl("first_15", -0.1, -0.2, 0.01), sl("rest", -0.01, -0.03, 0.01)])
    assert not t.decide_keep([sl("first_15", -0.1, -0.2, -0.01), sl("rest", 0.05, 0.03, 0.08)])
    assert not t.decide_keep([])


def test_project_points_pure() -> None:
    df = pl.DataFrame(
        {
            "game_id": ["g"],
            "player_id": [1],
            "shot_share_prior": [0.2],
            "ft_trip_rate_prior": [0.0],
            "ft_pct_prior": [0.8],
            "zone_mix_rim_prior": [1.0],
            "zone_mix_mid_prior": [0.0],
            "zone_mix_above3_prior": [0.0],
            "zone_fg_pct_rim_prior": [0.5],
            "zone_fg_pct_mid_prior": [0.0],
            "zone_fg_pct_above3_prior": [0.0],
        }
    )
    assert t.project_points(df)["pred_pts"][0] == pytest.approx(0.2 * 87 * 1.0, rel=1e-5)
