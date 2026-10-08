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
    POSSESSION_STEP_NEUTRAL,
    POSSESSION_STEP_PLAYER_FEATURE_COLUMNS,
    POSSESSION_STEP_TEAM_FEATURE_COLUMNS,
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


def _synthetic_raw(n: int = 400) -> pl.DataFrame:
    """Raw export-shaped frame (what the parquet holds, before label derivation)."""
    rng = np.random.default_rng(0)
    outcomes = rng.choice(["FGM2", "FGM3", "FGA_miss", "TOV", "FT_trip"], size=n)
    zones = np.where(
        np.isin(outcomes, ["FGM2", "FGM3", "FGA_miss", "FT_trip"]),
        rng.choice(["rim", "mid", "above3"], size=n),
        None,
    )
    start = rng.uniform(30, 720, size=n)
    return pl.DataFrame(
        {
            "game_id": ["g"] * n,
            "poss_idx": np.arange(n),
            "game_date": [__import__("datetime").date(2024, 1, 1)] * n,
            "season": [2024] * n,
            "period": rng.integers(1, 5, size=n),
            "clock_start": start,
            "clock_end": start - rng.uniform(-5, 90, size=n),  # incl. negative / huge durations
            "score_diff": rng.integers(-20, 20, size=n),
            "off_is_home": rng.integers(0, 2, size=n).astype(bool),
            "off_off_rtg_prior": rng.normal(1.14, 0.02, n),
            "off_def_rtg_prior": rng.normal(1.14, 0.02, n),
            "off_pace_prior": rng.normal(100, 2, n),
            "def_off_rtg_prior": rng.normal(1.14, 0.02, n),
            "def_def_rtg_prior": rng.normal(1.14, 0.02, n),
            "league_avg_ppp_asof": np.full(n, 1.14),
            **{
                c: rng.normal(POSSESSION_STEP_NEUTRAL[c], 0.01 + 0.0 * i, n)
                for i, c in enumerate(POSSESSION_STEP_PLAYER_FEATURE_COLUMNS)
            },
            "outcome": outcomes,
            "shot_zone": zones,
            "oreb": rng.integers(0, 2, size=n).astype(bool),
        }
    )


def test_derive_step_labels_clamps_duration_and_masks_zone() -> None:
    from nba.features.possession_step_features import DURATION_MAX_S, derive_step_labels

    out = derive_step_labels(_synthetic_raw())
    assert out["duration_s"].min() >= 0.0 and out["duration_s"].max() <= DURATION_MAX_S
    nonshot = out.filter(~pl.col("outcome").is_in(["FGM2", "FGM3", "FGA_miss"]))
    assert nonshot["shot_zone"].null_count() == nonshot.height
    assert out.null_count().select(pl.sum_horizontal(pl.all())).item() == nonshot.height


def _notebook_defs_namespace() -> dict[str, object]:
    import json
    from pathlib import Path

    from torch import nn

    nb = json.loads(Path("nba/models/colab/rung4_stepheads.ipynb").read_text())
    defs = next(
        "".join(c["source"])
        for c in nb["cells"]
        if c["cell_type"] == "code" and "def derive_step_labels" in "".join(c["source"])
    )
    ns: dict[str, object] = {"pl": pl, "np": np, "torch": torch, "nn": nn}
    exec(defs, ns)  # noqa: S102 - our own notebook cell, to prove it matches the module
    return ns


def test_notebook_inline_copy_matches_module() -> None:
    """The Colab notebook duplicates labels/eval code; keep it numerically in sync."""
    from nba.features.possession_step_features import derive_step_labels
    from nba.models.rung4_stepheads import evaluate_heads, frequency_priors

    ns = _notebook_defs_namespace()
    raw = _synthetic_raw()
    mod_frame = derive_step_labels(raw)
    nb_frame = ns["derive_step_labels"](raw)  # type: ignore[operator]
    assert mod_frame.equals(nb_frame)

    mod_batch, mean, std = frame_to_tensors(mod_frame)
    nb_batch, _, _ = ns["frame_to_tensors"](mod_frame)  # type: ignore[operator]
    seed_everything(0)
    net = StepHeadsNet(len(POSSESSION_STEP_FEATURE_COLUMNS), hidden=8)
    mod_eval = evaluate_heads(net, mod_batch, frequency_priors(mod_batch))
    nb_eval = ns["evaluate_heads"](net, nb_batch, ns["frequency_priors"](nb_batch))  # type: ignore[operator]
    assert set(mod_eval) == set(nb_eval) == {"outcome", "zone", "make", "rebound", "duration"}
    for head, vals in mod_eval.items():
        for k, v in vals.items():
            assert np.isclose(v, nb_eval[head][k], rtol=1e-6), (head, k)


def test_load_weights_round_trip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Train tiny, write artifacts exactly as the notebook does, reload, predictions match."""
    import json

    from nba.features.possession_step_features import derive_step_labels

    frame = derive_step_labels(_synthetic_raw())
    model = StepHeadsRung(seed=3, hidden=8, n_epochs=3)
    model.fit(frame, np.zeros(frame.height))
    assert model.net is not None and model.feature_mean_ is not None
    torch.save(model.net.state_dict(), tmp_path / "weights.pt")
    cfg = {
        **model.get_config(),
        "feature_mean": model.feature_mean_.tolist(),
        "feature_std": model.feature_std_.tolist() if model.feature_std_ is not None else None,
        "n_train_rows": frame.height,
    }
    (tmp_path / "config.json").write_text(json.dumps(cfg))

    loaded = StepHeadsRung.load_weights(tmp_path)
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
    assert np.allclose(model.predict(predict_df), loaded.predict(predict_df))
    assert loaded.hidden == 8 and loaded.seed == 3


def test_notebook_feature_columns_match_module() -> None:
    ns = _notebook_defs_namespace()
    assert ns["POSSESSION_STEP_TEAM_FEATURE_COLUMNS"] == POSSESSION_STEP_TEAM_FEATURE_COLUMNS
    assert ns["POSSESSION_STEP_PLAYER_FEATURE_COLUMNS"] == POSSESSION_STEP_PLAYER_FEATURE_COLUMNS
    assert ns["POSSESSION_STEP_FEATURE_COLUMNS"] == POSSESSION_STEP_FEATURE_COLUMNS
    assert ns["FEATURE_COLUMNS"] == POSSESSION_STEP_FEATURE_COLUMNS  # default feature set
    assert len(POSSESSION_STEP_FEATURE_COLUMNS) == len(set(POSSESSION_STEP_FEATURE_COLUMNS))


def test_evaluate_heads_reports_delta_and_clustered_ci() -> None:
    from nba.features.possession_step_features import derive_step_labels
    from nba.models.rung4_stepheads import (
        clustered_delta_ci,
        evaluate_heads,
        frequency_priors,
        per_possession_head_losses,
    )

    frame = derive_step_labels(_synthetic_raw())
    batch, _, _ = frame_to_tensors(frame)
    seed_everything(0)
    net = StepHeadsNet(len(POSSESSION_STEP_FEATURE_COLUMNS), hidden=8)
    pri = frequency_priors(batch)
    ev = evaluate_heads(net, batch, pri)
    rows = per_possession_head_losses(net, batch, pri)
    clusters = np.arange(frame.height) // 20
    for head, (nl, bl, _idx) in rows.items():
        assert np.isclose(ev[head]["delta"], nl.mean() - bl.mean(), rtol=1e-5, atol=1e-6)
        mean, lo, hi = clustered_delta_ci(nl, bl, clusters[_idx], n_boot=200)
        assert lo <= mean <= hi


def test_team_only_artifact_round_trip_and_neutral_predict(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Team-only (10-col) artifacts still load; player-column nets predict at a neutral lineup."""
    import json

    from nba.features.possession_step_features import derive_step_labels

    frame = derive_step_labels(_synthetic_raw())
    pred_df = pl.DataFrame(
        {
            "home_off_rtg_prior": [1.15],
            "home_def_rtg_prior": [1.10],
            "home_pace_prior": [99.0],
            "away_off_rtg_prior": [1.12],
            "away_def_rtg_prior": [1.14],
            "away_pace_prior": [100.0],
            "league_avg_ppp_asof": [1.146],
        }
    )
    for cols in (POSSESSION_STEP_TEAM_FEATURE_COLUMNS, POSSESSION_STEP_FEATURE_COLUMNS):
        model = StepHeadsRung(seed=1, hidden=8, n_epochs=2, feature_columns=list(cols))
        model.fit(frame, np.zeros(frame.height))
        assert model.net is not None and model.feature_std_ is not None
        d = tmp_path / str(len(cols))
        d.mkdir()
        torch.save(model.net.state_dict(), d / "weights.pt")
        cfg = {
            **model.get_config(),
            "feature_mean": model.feature_mean_.tolist(),  # type: ignore[union-attr]
            "feature_std": model.feature_std_.tolist(),
        }
        (d / "config.json").write_text(json.dumps(cfg))
        loaded = StepHeadsRung.load_weights(d)
        assert loaded.feature_columns == list(cols)
        assert np.allclose(model.predict(pred_df), loaded.predict(pred_df))
