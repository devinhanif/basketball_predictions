"""Fixture-scale tests for ``nba.eval.time_decay_tune``."""

from __future__ import annotations

import contextlib

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


def test_current_season_only_equivalence() -> None:
    """carryover weight 1 => prior seasons contribute nothing => decay-mode
    counts equal a pure current-season (partition player, season) cumulative."""
    from nba.features.player_possession_features import build_player_shot_rates

    con = _db()
    cfg = TimeDecayConfig(half_life_seasons=1e-3, carryover_decay_weight=1.0)
    on = build_player_shot_rates(con, use_time_decay=True, time_decay_config=cfg)
    # Season openers (first game of each season) must see exactly zero history.
    openers = on.filter(pl.col("game_id").str.ends_with("-0"))
    assert (openers["n_fga_prior"] == 0).all()
    # Later games see only same-season shots (4 per earlier game).
    g2 = on.filter(pl.col("game_id") == "2024-2").row(0, named=True)
    assert g2["n_fga_prior"] == 8


def test_prior_totals_only_from_earlier_seasons() -> None:
    from nba.features.player_possession_features import build_player_shot_rates

    con = _db()
    cfg = TimeDecayConfig(half_life_seasons=1e6, carryover_decay_weight=0.0)
    on = build_player_shot_rates(con, use_time_decay=True, time_decay_config=cfg)
    # No age info -> multiplier 1, huge half-life -> weight 1: 2023 opener = 12 shots from 2022.
    assert on.filter(pl.col("game_id") == "2023-0")["n_fga_prior"][0] == 12
    assert on.filter(pl.col("game_id") == "2022-0")["n_fga_prior"][0] == 0


def test_flag_reaches_sim_rate_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    import nba.eval.player_points_sim_eval as ev

    seen: list[dict[str, object]] = []
    real = ev.build_player_shot_rates

    def spy(con, **kw):  # type: ignore[no-untyped-def]
        seen.append(kw)
        return real(con, **kw)

    monkeypatch.setattr(ev, "build_player_shot_rates", spy)
    con = _db()
    cfg = TimeDecayConfig(half_life_seasons=0.5)
    for flag in (False, True):
        with contextlib.suppress(Exception):  # tiny fixture lacks team data
            ev.run_sim_vs_baseline_eval(
                con,
                n_sims=10,
                use_time_decay=flag,
                time_decay_config=cfg,
                min_season=2024,
                max_season=2024,
                sample_games=2,
            )
    assert "use_time_decay" not in seen[0]
    assert seen[1]["use_time_decay"] is True and seen[1]["time_decay_config"] == cfg
    # And rates for a 2024 game genuinely differ on vs off.
    from nba.features.player_possession_features import build_player_shot_rates as b

    off = b(con).filter(pl.col("game_id") == "2024-0")
    on = b(con, use_time_decay=True, time_decay_config=cfg).filter(pl.col("game_id") == "2024-0")
    assert off["n_fga_prior"][0] != on["n_fga_prior"][0]
