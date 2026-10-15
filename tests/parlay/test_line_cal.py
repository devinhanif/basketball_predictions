"""The CAL map: pinned to the frozen harness (research/eval/line_calibration_eval.py)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from nba.parlay import line_cal as lc
from nba.parlay import line_cal_build as lb


def test_numeric_conventions_match_the_harness() -> None:
    h = pytest.importorskip("research.eval.line_calibration_eval")
    for name in ("EPS", "SPREAD_MIN", "IQR_TO_SD", "Z_CLIP", "RIDGE", "MIN_FIT", "HALF_LIFE_DAYS"):
        assert getattr(lc, name) == getattr(h, name), name
    assert lb.TAU_POS == h.TAU_POS and lb.RULE_SHA8 == "a5441242"
    rng = np.random.default_rng(3)
    n = 3000
    pm = rng.uniform(0.02, 0.98, n)
    line = rng.integers(0, 30, n) + 0.5
    med, spr = rng.uniform(0, 30, n), rng.uniform(0.1, 6, n)
    st10 = rng.uniform(0, 1, n)
    X_mine, X_h = lc.design(pm, line, med, spr, st10), h.design(pm, line, med, spr, st10)
    assert np.array_equal(X_mine, X_h)
    y = (rng.random(n) < lc.sigmoid(0.3 * X_h[:, 1] - 0.2)).astype(float)
    dates = np.datetime64("2024-01-01") + rng.integers(0, 300, n).astype("timedelta64[D]")
    start = np.datetime64("2024-12-01")
    w = lc.age_weights(dates, start)
    assert np.array_equal(w, h.age_weights(dates, start))
    assert np.array_equal(lc.fit_logistic(X_h, y, w=w), h.fit_logistic(X_h, y, w=w))


def test_cal_map_reproduces_the_harness_2024_fit_for_one_block(
    history_dirs: tuple[Path, Path, Path],
) -> None:
    h = pytest.importorskip("research.eval.line_calibration_eval")
    pairs, model, _ = history_dirs
    D_h, _ = h.load_real(pairs, model)
    block = np.datetime64("2024-11-01")
    d, _ = lb.load_history(pairs, model)
    D = lb.to_arrays(d)
    # the continuous-grid inputs agree with the harness, leg for leg
    assert D_h["n"] == len(D["pm"])
    mine = d.sort(["game_id", "player_id", "stat", "book"])
    assert np.allclose(np.sort(D_h["med"]), np.sort(mine["med"].to_numpy()), atol=1e-12)
    assert np.allclose(np.sort(D_h["spr"]), np.sort(mine["spr"].to_numpy()), atol=1e-12)
    # the fit for one block (pinnacle, every stat) equals the harness's record to the last digit
    ours = {c.stat: c for c in lc.fit_cells(D, block, ("pinnacle",), lb.STATS)}
    for stat in lb.STATS:
        _, info = h.walk_forward(
            D_h, np.asarray(D_h["over"]), 2024, "pinnacle", "cal", only_block=block
        )
        rec = next(r for r in info if r["stat"] == stat)
        assert ours[stat].n_fit == rec["n_fit"]
        assert ours[stat].beta == pytest.approx(rec["beta"], abs=1e-9)
        assert ours[stat].n_eff == pytest.approx(rec["n_eff"], rel=1e-9)


def test_shipped_map_verifies_and_is_the_season_2024_fit(
    history_dirs: tuple[Path, Path, Path],
) -> None:
    cm = lc.load_map()
    assert date.fromisoformat(cm.meta["fit_through"]) <= lc.MAX_FIT_DATE
    assert cm.meta["rule_sha8"] == "a5441242" and cm.meta["rule"] == "LINE_CALIBRATION"
    assert cm.books == ("draftkings", "pinnacle")
    assert all(cm.has(b, s) for b in cm.books for s in lb.STATS)
    pairs, model, _ = history_dirs
    fresh = lb.build_payload(pairs, model)
    stored = json.loads(lc.MAP_PATH.read_text())
    assert fresh["map_sha256"] == stored["map_sha256"]  # rebuildable, byte for byte


def test_tampered_map_is_refused(tmp_path: Path) -> None:
    payload = json.loads(lc.MAP_PATH.read_text())
    payload["cells"][0]["beta"][1] += 0.01
    bad = tmp_path / "m.json"
    bad.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="sha"):
        lc.load_map(bad)
    payload = json.loads(lc.MAP_PATH.read_text())
    payload["fit_through"] = "2025-12-31"
    payload["map_sha256"] = lc.payload_sha(payload)
    bad.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="holdout"):
        lc.load_map(bad)


def test_season_2025_is_refused_everywhere() -> None:
    D = {
        "date": np.array(["2025-09-01"], dtype="datetime64[D]"),
        "book": np.array(["pinnacle"]),
        "stat": np.array(["pts"]),
        "pm": np.array([0.5]),
        "line": np.array([10.5]),
        "med": np.array([10.0]),
        "spr": np.array([3.0]),
        "st10": np.array([1.0]),
        "over": np.array([1.0]),
    }
    with pytest.raises(ValueError, match="holdout"):
        lc.fit_cells(D, np.datetime64("2026-01-01"), ("pinnacle",), ("pts",))
    with pytest.raises(ValueError, match="holdout"):
        lb.read_pairs(Path("nonexistent.parquet"), 2025)
    with pytest.raises(ValueError, match="holdout"):
        lb.load_history(Path("a"), Path("b"), (2024, 2025))


def test_apply_is_identity_without_enough_rows_and_calibrates_otherwise() -> None:
    cm = lc.load_map()
    p = cm.apply("pinnacle", "pts", 0.7, 20.5, 22.0, 5.0, 1.0)
    assert 0.4 < p < 0.7  # the map shrinks a confident raw probability toward the base rate
    many = cm.apply_many(
        "pinnacle", "pts", np.array([0.7, 0.3]), np.full(2, 20.5), np.full(2, 22.0),
        np.full(2, 5.0), np.ones(2),
    )  # fmt: skip
    assert many[0] == pytest.approx(p)
    ident = lc.CalMap({}, {("x", "pts"): lc.CalCell("x", "pts", None, 10, None)})
    assert ident.apply("x", "pts", 0.7, 1.5, 1.0, 1.0, 1.0) == pytest.approx(0.7)
