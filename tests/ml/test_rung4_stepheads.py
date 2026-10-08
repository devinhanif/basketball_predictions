"""CPU smoke tests for the rung-4 step-head net (fixture-sized, fast).

Proves the training code path works end to end on CPU -- per the task,
no multi-minute real-data jobs belong here; this is forward/backward over
the tiny committed fixture (a few dozen possessions), finishing in well
under a second, matching every other fixture-based test in this repo.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import torch

from nba.features.possession_step_features import (
    POSSESSION_STEP_FEATURE_COLUMNS,
    build_possession_step_training_frame,
)
from nba.ingest.cache import insert_rows
from nba.models.rung4_stepheads import (
    PREDICT_FEATURE_COLUMNS,
    StepHeadsNet,
    StepHeadsRung,
    compute_losses,
    frame_to_tensors,
    seed_everything,
)
from nba.parse.possessions import parse_possessions
from tests.fixtures.loader import build_fixture_db, load_pbp


def _fixture_training_frame() -> pl.DataFrame:
    con = build_fixture_db(":memory:")
    try:
        pbp_map = load_pbp()
        possessions = pl.concat([parse_possessions(df) for df in pbp_map.values()])
        insert_rows(con, "possessions", possessions.columns, possessions)
        return build_possession_step_training_frame(con)
    finally:
        con.close()


def test_fixture_training_frame_has_rows_and_all_label_columns() -> None:
    frame = _fixture_training_frame()
    assert frame.height > 0, "parser + feature builder produced no possessions from the fixture"
    for col in [*POSSESSION_STEP_FEATURE_COLUMNS, "outcome", "shot_zone", "made_shot", "oreb"]:
        assert col in frame.columns


def test_forward_backward_two_steps_runs_and_loss_is_finite() -> None:
    """Exact CPU smoke-test ask: run forward/backward for a couple of steps."""
    frame = _fixture_training_frame()
    seed_everything(0)
    batch, _mean, _std = frame_to_tensors(frame)
    net = StepHeadsNet(n_features=len(POSSESSION_STEP_FEATURE_COLUMNS), hidden=8)
    optimizer = torch.optim.Adam(net.parameters(), lr=1e-2)

    losses: list[float] = []
    for _ in range(2):
        optimizer.zero_grad()
        step_losses = compute_losses(net, batch)
        total = step_losses["total"]
        assert torch.isfinite(total), "loss must be finite"
        total.backward()
        grad_norms = [p.grad.norm().item() for p in net.parameters() if p.grad is not None]
        assert any(g > 0 for g in grad_norms), "no gradient flowed to any parameter"
        optimizer.step()
        losses.append(float(total.detach()))

    assert all(np.isfinite(losses))


def test_step_heads_rung_implements_three_method_contract() -> None:
    frame = _fixture_training_frame()
    model = StepHeadsRung(seed=0, hidden=8, n_epochs=2)
    model.fit(frame, np.zeros(frame.height))  # y ignored by this rung -- see docstring

    config = model.get_config()
    assert config["rung"] == 4
    assert config["model_name"] == "rung4_stepheads"

    model.record_metrics({"note": "smoke test"})
    assert model.get_metrics() == {"note": "smoke test"}

    predict_df = pl.DataFrame(
        {
            "home_off_rtg_prior": [1.15, 1.08],
            "home_def_rtg_prior": [1.10, 1.12],
            "home_pace_prior": [99.0, 101.0],
            "away_off_rtg_prior": [1.12, 1.05],
            "away_def_rtg_prior": [1.14, 1.09],
            "away_pace_prior": [100.0, 98.0],
            "league_avg_ppp_asof": [1.146, 1.146],
        }
    )
    assert set(PREDICT_FEATURE_COLUMNS) <= set(predict_df.columns)
    preds = model.predict(predict_df)
    assert preds.shape == (2,)
    assert np.all((preds >= 0.0) & (preds <= 1.0))


def test_fit_on_empty_frame_does_not_crash_predict_falls_back() -> None:
    empty = build_possession_step_training_frame(build_fixture_db(":memory:"))
    model = StepHeadsRung(seed=0)
    model.fit(empty, np.zeros(0))
    assert model.net is None

    predict_df = pl.DataFrame(
        {
            "home_off_rtg_prior": [1.1],
            "home_def_rtg_prior": [1.1],
            "home_pace_prior": [100.0],
            "away_off_rtg_prior": [1.1],
            "away_def_rtg_prior": [1.1],
            "away_pace_prior": [100.0],
            "league_avg_ppp_asof": [1.146],
        }
    )
    preds = model.predict(predict_df)
    assert preds.shape == (1,)
    assert np.all((preds >= 0.0) & (preds <= 1.0))
