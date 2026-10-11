# ruff: noqa: E501
"""docs/prereg/CALIBRATED_GRID.md harness: map properties, as-of window, hash refusal, wiring.
Synthetic grids only; no database, no real rows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from scipy.stats import poisson

from research.eval import calibrated_grid_eval as cg

D = np.datetime64


def _grid(n: int, k: int = 30, seed: int = 0, widen: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """pge from Poisson(mu) rounded to the 199 grid; labels from a wider (over-dispersed) truth."""
    rng = np.random.default_rng(seed)
    mu = rng.uniform(2, 14, n)
    pge = 1.0 - poisson.cdf(np.arange(1, k + 1)[None, :] - 1, mu[:, None])
    pge = np.round(pge * 199) / 199
    y = (
        rng.negative_binomial(mu / widen, (1.0 / (1.0 + widen)) * np.ones(n)).astype(float)
        if widen != 1.0
        else rng.poisson(mu).astype(float)
    )
    return pge, y


def test_temperature_and_platt_preserve_ranking_and_monotone() -> None:
    pge, _ = _grid(400)
    for m in (
        cg.GridMap("C1", 0.0, 1 / 3.0, False),
        cg.GridMap("C1", 0.0, 3.0, False),
        cg.GridMap("C2", -1.0, 0.5, False),
        cg.GridMap("C2", 1.5, 2.0, False),
    ):
        g = cg.apply_map(pge, m)
        assert (np.diff(g, axis=1) <= 1e-12).all()
        assert g.min() >= 0.0 and g.max() <= 1.0
        o = np.argsort(pge, axis=1, kind="stable")
        gs = np.take_along_axis(g, o, axis=1)
        assert (np.diff(gs, axis=1) >= -1e-12).all()
        exact = (pge == 0.0) | (pge == 1.0)
        assert (g[exact] == pge[exact]).all()


def test_renormalise_repairs_a_violation() -> None:
    p = np.array([[0.9, 0.95, 0.3, 0.4, 0.1]])
    r = cg.renormalise(p)
    assert (np.diff(r, axis=1) <= 0).all() and r[0, 1] == 0.9 and r[0, 3] == 0.3


def test_identity_below_minimum_rows() -> None:
    pge, y = _grid(100)
    date = np.repeat(D("2023-12-01") - np.timedelta64(5, "D"), 100)
    maps = cg.fit_block_maps(date, y, pge, D("2023-12-01"))
    assert all(m.identity for m in maps.values())
    out = cg.apply_map(pge, maps["C1"])
    assert out is pge


def test_temperature_recovers_overconfidence() -> None:
    pge, y = _grid(6000, seed=3, widen=2.0)
    m = cg.fit_temperature(pge, y)
    assert m.temperature > 1.1  # truth is wider than the grid: flatten
    m2 = cg.fit_platt(pge, y)
    assert m2.b < 1.0
    pge2, y2 = _grid(6000, seed=4)
    assert abs(cg.fit_temperature(pge2, y2).temperature - 1.0) < 0.15


def test_window_never_includes_the_block_and_future_labels_never_enter() -> None:
    n = 9000
    rng = np.random.default_rng(1)
    date = (D("2023-10-24") + rng.integers(0, 150, n).astype("timedelta64[D]")).astype(
        "datetime64[D]"
    )
    pge, y = _grid(n, seed=5, widen=2.0)
    start = D("2024-01-01")
    w = cg.window_mask(date, start)
    assert w.any() and date[w].max() < start and date[w].min() >= start - np.timedelta64(60, "D")
    assert not w[date >= start].any()
    base = cg.fit_block_maps(date, y, pge, start)
    y2 = y.copy()
    y2[date >= start] = 77.0
    assert base == cg.fit_block_maps(date, y2, pge, start)
    y3 = y.copy()
    y3[w] = rng.integers(0, 30, int(w.sum())).astype(float)
    assert base["C1"] != cg.fit_block_maps(date, y3, pge, start)["C1"]
    # a label dated exactly on the block start is outside the window
    on_start = date == start
    y4 = y.copy()
    y4[on_start] = 55.0
    assert base == cg.fit_block_maps(date, y4, pge, start)


def test_shuffle_control_differs_from_c1_and_is_deterministic() -> None:
    n = 6000
    date = np.repeat(D("2023-12-01") - np.timedelta64(10, "D"), n)
    pge, y = _grid(n, seed=6, widen=2.0)
    a = cg.fit_block_maps(date, y, pge, D("2023-12-01"))
    b = cg.fit_block_maps(date, y, pge, D("2023-12-01"))
    assert a == b
    assert a["A4"].temperature != a["C1"].temperature


def test_survival_crps_equals_production_route() -> None:
    pge, y = _grid(300, seed=8)
    assert np.allclose(cg.crps_survival(pge, y), cg.production_crps(pge, y), atol=1e-9)


def test_hash_refusal(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    doc.write_text("# x\nrule\n=== RESULTS BELOW ===\nanything\n")
    cg.configure(doc)
    sha = cg.frozen_sha(doc)
    assert cg.require_frozen(sha) == sha
    with pytest.raises(SystemExit):
        cg.require_frozen("0" * 64)
    with pytest.raises(SystemExit):
        cg.require_frozen(None)
    assert cg.require_frozen(None, allow_draft=True) == sha
    doc.write_text("# x\nrule CHANGED\n=== RESULTS BELOW ===\nanything\n")
    with pytest.raises(SystemExit):
        cg.require_frozen(sha)
    doc.write_text("# x\nrule CHANGED\n=== RESULTS BELOW ===\nother results text\n")
    assert cg.frozen_sha(doc) == cg.frozen_sha(doc)  # results text is outside the hash
    cg.configure(cg.DEFAULT_DOC)


def test_season_guard_refuses_holdout() -> None:
    with pytest.raises(SystemExit):
        cg.guard_season(2025)
    cg.guard_season(2023)


def test_run_wiring_decide_and_render() -> None:
    res: dict[str, Any] = {"prereg_sha256": "x", "seasons": {}}
    seasons: dict[str, Any] = {}
    for s in cg.SEASONS:
        stats = {}
        for i, x in enumerate(cg.STATS):
            rows, pairs = cg.make_synthetic(seed=10 * s + i)
            scored = cg.score_stat(rows, pairs)
            stats[x] = cg.summarise_stat(rows, pairs, scored, n_boot=40)
        seasons[str(s)] = {"stats": stats}
    res["seasons"] = seasons
    res["verdict"] = cg.decide(res)
    txt = cg.render(res)
    assert "Verdict" in txt and json.loads(json.dumps(res["verdict"], default=float))
    v = res["verdict"]
    assert set(v["lines"]) == set(cg.STATS)


def test_quantile_roundtrip_matches_survival() -> None:
    pge, _ = _grid(50, seed=9)
    q = cg.quantiles_from_pge(pge)
    back = np.stack([(q >= k).sum(axis=1) / 199 for k in range(1, pge.shape[1] + 1)], axis=1)
    assert np.allclose(back, pge)
