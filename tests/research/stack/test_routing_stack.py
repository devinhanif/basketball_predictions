"""Frozen gate, artifact adapters, threshold routing, cell guardrails, holdout guards."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nba.stack.oof import TAUS, validate_oof
from research.stack.adapters import quantile_grid
from research.stack.artifacts import context_residual_frame, elo_frames, exp2_arm_frame
from research.stack.cells import cell_evidence
from research.stack.data import StackData
from research.stack.frozen import FrozenGate, freeze_softmax
from research.stack.holdout import (
    TargetResult,
    decide,
    ece,
    require_preregistration,
    score_route,
    touch,
)
from research.stack.threshold import expand_thresholds, grid_exceed_prob


def _data(n: int = 3000, seed: int = 0, season: int = 2023) -> StackData:
    rng = np.random.default_rng(seed)
    x = rng.integers(0, 2, n)
    mu = rng.normal(15, 3, n)
    y = mu + rng.normal(0, 3, n)
    qa = quantile_grid(mu + np.where(x == 1, 0.0, 6.0), np.full(n, 3.0), "normal")
    qb = quantile_grid(mu + np.where(x == 1, 6.0, 0.0), np.full(n, 3.0), "normal")
    keys = pd.DataFrame(
        {
            "game_id": [f"g{i // 4}" for i in range(n)],
            "player_id": np.arange(n),
            "game_date": pd.Timestamp("2023-11-01") + pd.to_timedelta(np.arange(n) // 20, "D"),
            "season": season,
        }
    )
    ctx = pd.DataFrame({"x": x.astype(float), "cold_start_bucket": rng.choice(["a", "b"], n)})
    return StackData("pts", ["A", "B"], keys, y, np.stack([qa, qb], axis=1), ctx)


def test_frozen_gate_json_round_trip_and_hash() -> None:
    d = _data()
    g = freeze_softmax(d, max_season=2024)
    g2 = FrozenGate.from_dict(json.loads(json.dumps(g.to_dict())))
    assert np.allclose(g.weights(d.context), g2.weights(d.context))
    assert g.content_hash() == g2.content_hash()
    w = g.weights(d.context)
    assert np.allclose(w.sum(axis=1), 1.0)
    # the planted signal is learned: A gets the weight when x == 1
    assert w[d.context["x"].to_numpy() == 1, 0].mean() > 0.8
    with pytest.raises(KeyError):
        g.weights(d.context.drop(columns=["x"]))


def test_frozen_gate_survives_sorted_json_with_two_categoricals() -> None:
    d = _data()
    rng = np.random.default_rng(3)
    d.context["zz_cat"] = rng.choice(["u", "v", "w"], d.n)  # sorts AFTER cold_start_bucket
    d.context["aa_cat"] = rng.choice(["m", "n"], d.n)  # sorts BEFORE it
    d.context = d.context[["x", "zz_cat", "cold_start_bucket", "aa_cat"]]
    g = freeze_softmax(d, max_season=2024)
    blob = json.dumps(g.to_dict(), sort_keys=True)  # what write_spec does
    g2 = FrozenGate.from_dict(json.loads(blob))
    assert g2.features == g.features
    assert np.allclose(g.weights(d.context), g2.weights(d.context))


def test_freeze_refuses_frozen_season() -> None:
    with pytest.raises(ValueError):
        freeze_softmax(_data(season=2025), max_season=2025)
    with pytest.raises(ValueError):
        freeze_softmax(_data(season=2025), max_season=2024)


def test_exp2_arm_adapter_and_leak_guard(tmp_path: Path) -> None:
    n = 40
    q = quantile_grid(np.full(n, 10.0), np.full(n, 3.0), "normal")
    arm = pd.DataFrame(
        {
            "variant": "x",
            "stat": "pts",
            "game_id": [f"g{i}" for i in range(n)],
            "player_id": np.arange(n),
            "fold_id": 202411,
            "y": 10.0,
            "mean": 10.0,
            "q19": list(q),
        }
    )
    arm.to_parquet(tmp_path / "arm.parquet")
    dates = pd.DataFrame(
        {
            "game_id": arm["game_id"],
            "player_id": arm["player_id"],
            "target": "pts",
            "game_date": pd.Timestamp("2024-11-05"),
            "season": 2024,
        }
    )
    f = exp2_arm_frame(tmp_path / "arm.parquet", dates)
    assert (f["made_with_data_through"] == pd.Timestamp("2024-10-31")).all()
    validate_oof(f)  # contract holds
    arm.assign(q19=None).to_parquet(tmp_path / "light.parquet")
    with pytest.raises(ValueError, match="light arm"):
        exp2_arm_frame(tmp_path / "light.parquet", dates)


def test_context_residual_adapter_refuses_2025(tmp_path: Path) -> None:
    q = quantile_grid(np.array([10.0]), np.array([3.0]), "normal")
    row = {
        "model": "m", "fold_id": 1, "game_id": "g", "player_id": 1, "target": "pts",
        "game_date": pd.Timestamp("2025-11-01"), "season": 2025, "mean": 10.0,
        "q_grid": q[0], "made_with_data_through": pd.Timestamp("2025-10-31"),
    }  # fmt: skip
    pd.DataFrame([row]).to_parquet(tmp_path / "o.parquet")
    with pytest.raises(ValueError, match="frozen holdout"):
        context_residual_frame(tmp_path / "o.parquet")


def test_elo_same_day_cutoff_normalised(tmp_path: Path) -> None:
    raw = pd.DataFrame(
        {
            "model": ["rung0_injury_elo"] * 2,
            "game_id": ["a", "b"],
            "game_date": pd.to_datetime(["2023-11-01", "2023-11-02"]),
            "season": [2023, 2023],
            "p": [0.6, 0.4],
            "made_with_data_through": pd.to_datetime(["2023-11-01 17:00", "2023-11-02 17:00"]),
        }
    )
    raw.to_parquet(tmp_path / "e.parquet")
    f = elo_frames(tmp_path / "e.parquet")["rung0_injury_elo"]
    validate_oof(f)
    assert json.loads(f["meta"].iloc[0])["cutoff_ts"].startswith("2023-11-01 17:00")
    late = raw.assign(made_with_data_through=pd.to_datetime(["2023-11-01 21:00"] * 2))
    late.to_parquet(tmp_path / "l.parquet")
    with pytest.raises(ValueError, match="tip-off"):
        elo_frames(tmp_path / "l.parquet")


@settings(max_examples=40, deadline=None)
@given(
    mu=st.floats(1, 40),
    sd=st.floats(0.5, 8),
    lines=st.lists(st.integers(0, 50), min_size=2, max_size=8, unique=True),
)
def test_exceed_prob_monotone_and_bounded(mu: float, sd: float, lines: list[int]) -> None:
    q = quantile_grid(np.array([mu]), np.array([sd]), "normal")
    ls = sorted(lines)
    p = np.array([grid_exceed_prob(q, np.array([float(n)]))[0] for n in ls])
    assert ((p >= 0) & (p <= 1)).all()
    assert (np.diff(p) <= 1e-12).all()  # P(Y >= N) non-increasing in N


def test_exceed_prob_close_to_normal_in_body() -> None:
    from scipy import stats

    q = quantile_grid(np.array([20.0]), np.array([5.0]), "normal")
    for n in (16, 20, 24):
        approx = grid_exceed_prob(q, np.array([float(n)]))[0]
        exact = 1 - stats.norm.cdf(n - 0.5, 20, 5)
        assert abs(approx - exact) < 0.03
    assert len(TAUS) == 19


def test_expand_thresholds_regions_and_labels() -> None:
    d = _data(2000)
    td, src = expand_thresholds(d, "pts")
    assert td.target == "thr_pts" and td.n == len(src)
    assert set(td.context["line_region"]) <= {"below", "near", "above"}
    assert ((td.cand >= 0) & (td.cand <= 1)).all()
    # labels are the realised event y >= N
    assert np.array_equal(td.y, (d.y[src] >= td.context["line_n"].to_numpy()).astype(float))
    assert (np.diff(td.dates.astype("int64")) >= 0).all()  # still time ordered


def test_cell_guardrails_small_cells_never_claim() -> None:
    d = _data(3000)
    n = d.n
    rng = np.random.default_rng(1)
    big = np.where(d.context["x"].to_numpy() == 1, "x1", "x0")
    tiny = np.where(np.arange(n) < 30, "tiny", "rest")
    noise = rng.choice(["p", "q"], n)
    champ, cells = cell_evidence(d, {"x": big, "tiny": tiny, "noise": noise}, n_boot=200)
    by = {(c.dimension, c.group): c for c in cells}
    assert by[("tiny", "tiny")].eligible is False and by[("tiny", "tiny")].claim is None
    # planted: in x==1 candidate A is right, in x==0 candidate B is
    claims = {k: c.claim for k, c in by.items() if c.claim}
    other = "B" if champ == "A" else "A"
    assert claims.get(("x", "x0" if champ == "A" else "x1")) == other
    assert all(by[k].n >= 500 and by[k].games >= 150 for k in claims)
    assert ("noise", "p") not in claims and ("noise", "q") not in claims


def _router_spec(d: StackData) -> dict:
    from research.registry.routing import RouteSpec

    g = freeze_softmax(d, max_season=2024)
    cands = [{"id": c, "holdout_status": "untouched"} for c in d.candidates]
    return RouteSpec(
        "pts", "full", "router", "A", cands, 2025,
        {"seasons": [2023], "date_min": "x", "date_max": "y", "n_rows": d.n},
        g.to_dict(), g.features,
    ).to_dict()  # fmt: skip


def test_holdout_guards(tmp_path: Path) -> None:
    spec = _router_spec(_data())
    ledger = tmp_path / "ledger.md"
    ledger.write_text("nothing here")
    with pytest.raises(PermissionError, match="pre-register"):
        require_preregistration(spec, ledger)
    ledger.write_text(f"row quoting {spec['route_hash']}")
    require_preregistration(spec, ledger)
    assert touch(spec, tmp_path / "t", allow_repeat=False) == "clean-first-touch"
    with pytest.raises(PermissionError, match="already touched"):
        touch(spec, tmp_path / "t", allow_repeat=False)
    assert touch(spec, tmp_path / "t", allow_repeat=True) == "repeated-use"


def test_score_route_keep_rule_on_planted_signal() -> None:
    train = _data(3000, seed=0)
    spec = _router_spec(train)
    hold = _data(3000, seed=5, season=2025)
    sl = {
        "cold_start": hold.context["cold_start_bucket"].to_numpy(),
        "season_phase": np.where(np.arange(hold.n) % 3 == 0, "first15", "rest"),
        "starter": np.where(hold.context["x"].to_numpy() == 1, "starter", "bench"),
    }
    r = score_route(spec, hold, sl, n_boot=200)
    decide([r])
    assert r.delta_vs_best < -0.5 and r.ci[1] < 0
    assert r.q < 0.05
    # champion specs make no claim
    spec_c = dict(spec, kind="champion")
    rc = score_route(spec_c, hold, sl, n_boot=50)
    decide([rc])
    assert rc.kept is False and "no routing claim" in rc.reasons[0]


def test_decide_flags_calibration_and_effect_floor() -> None:
    r = TargetResult(
        "pts", "h", "router", 1000, 100, "A", {"A": 1.0}, -0.001, (-0.002, -0.0001), 0.01,
        calib={"router": 0.1, "best_single": 0.0},
    )  # fmt: skip
    decide([r])
    assert not r.kept
    assert any("floor" in x for x in r.reasons) and any("calibration" in x for x in r.reasons)


def test_ece_bounds() -> None:
    p = np.linspace(0.05, 0.95, 1000)
    y = (np.random.default_rng(0).random(1000) < p).astype(float)
    assert 0 <= ece(p, y) < 0.08
