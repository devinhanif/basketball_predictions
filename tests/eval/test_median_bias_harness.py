# ruff: noqa: E501
"""Unit tests for the MEDIAN_BIAS harness pieces and the freeze/pin refusals. Synthetic arrays only (no real data, no DuckDB)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from research.eval import median_bias_eval as ev
from research.eval import median_bias_gate as gate
from research.eval import median_bias_rule as rule


def make_comp(
    seed: int = 0, n_blocks: int = 3, rows: int = 600, cal: int = 1500, two_seasons: bool = False
) -> dict[str, np.ndarray]:
    """Three blocks; calibration pools whose standardized residuals are more right-skewed for short-minutes rows."""
    rng = np.random.default_rng(seed)
    out: dict[str, list[np.ndarray]] = {
        k: []
        for k in (
            "row_c",
            "row_s",
            "row_blk",
            "row_min10",
            "row_gid",
            "row_pid",
            "row_season",
            "row_n_prior",
            "cal_z",
            "cal_blk",
            "cal_min10",
            "cal_c",
            "cal_s",
        )
    }
    for b in range(n_blocks):
        m_r = rng.uniform(5, 38, rows)
        m_c = rng.uniform(5, 38, cal)
        z = np.where(m_c < 15, rng.gamma(1.5, 0.8, cal) - 1.2, rng.normal(0.0, 1.0, cal))
        out["row_c"].append(rng.uniform(3, 25, rows))
        out["row_s"].append(rng.uniform(1, 6, rows))
        out["row_blk"].append(np.full(rows, b))
        out["row_min10"].append(m_r)
        out["row_gid"].append(np.array([f"g{b}_{i // 10}" for i in range(rows)], dtype=object))
        out["row_pid"].append(np.arange(rows))
        out["row_season"].append(
            np.full(rows, 2024 if two_seasons and b >= n_blocks // 2 else 2023)
        )
        out["row_n_prior"].append(np.full(rows, 20.0))
        out["cal_z"].append(z)
        out["cal_blk"].append(np.full(cal, b))
        out["cal_min10"].append(m_c)
        out["cal_c"].append(rng.uniform(3, 25, cal) + 1000 + b)
        out["cal_s"].append(rng.uniform(1, 6, cal))
    return {k: np.concatenate(v) for k, v in out.items()}


def test_constants_and_helpers_equal_production() -> None:
    lt = pytest.importorskip("nba.props.lower_tail")
    cr = pytest.importorskip("nba.props.context_residual")
    ce = pytest.importorskip("nba.eval.context_residual_eval")
    assert tuple(lt.MINUTES_EDGES) == gate.EDGES and lt.MIN_BIN == gate.MIN_BIN
    assert np.array_equal(cr.CRPS_TAUS, gate.TAUS)
    rng = np.random.default_rng(1)
    q = np.sort(rng.normal(10, 6, (50, 199)), axis=1)
    assert np.array_equal(cr.to_integer_support(q), gate.to_int(q))
    y = rng.integers(0, 30, 50).astype(float)
    assert np.allclose(ce.crps_from_quantiles(q, y), gate.crps_int(q, y))


def test_a0_grid_equals_production_raw_quantiles() -> None:
    lt = pytest.importorskip("nba.props.lower_tail")
    comp = make_comp()
    g = gate.a0_grid(comp)
    b = 1
    r = comp["row_blk"] == b
    want = lt.raw_quantiles(
        comp["row_c"][r], comp["row_s"][r], comp["cal_z"][comp["cal_blk"] == b], gate.TAUS
    )
    assert np.allclose(g[r], want)


def test_cand_equals_a0_when_cells_share_the_pooled_pool_and_falls_back_when_small() -> None:
    comp = make_comp()
    comp["cal_z"] = np.random.default_rng(3).normal(0, 1, comp["cal_z"].size)
    comp["cal_min10"] = np.full(comp["cal_min10"].size, 30.0)  # every pool row in the top cell
    q0, qc = gate.a0_grid(comp), ev.cand_grid(comp)
    assert np.allclose(
        q0, qc
    )  # the other two cells have < MIN_BIN rows: pooled quantiles, i.e. production


def test_cand_moves_the_short_minutes_cell_only_and_never_reads_outcomes() -> None:
    comp = make_comp()
    q0, qc = gate.a0_grid(comp), ev.cand_grid(comp)
    cell = gate.cell_of(comp["row_min10"])
    moved = np.abs(qc - q0).max(axis=1) > 1e-9
    assert moved[cell == 0].mean() > 0.9  # the skewed cell gets its own quantiles
    shift = np.abs(qc[:, 99] - q0[:, 99])
    assert (
        shift[cell == 0].mean() > shift[cell == 2].mean()
    )  # the skewed pool's own quantiles differ most from the pooled ones
    comp2 = dict(comp)
    comp2["row_y"] = np.random.default_rng(9).integers(0, 50, comp["row_c"].size).astype(float)
    assert np.array_equal(
        ev.cand_grid(comp2), qc
    )  # a planted outcome array changes nothing: the candidate sees no label


def test_planted_future_is_caught_by_the_overlap_detector() -> None:
    comp = make_comp()
    assert ev.leak_overlap(comp) == 0
    b = 0
    r = np.flatnonzero(comp["row_blk"] == b)[0]
    ci = np.flatnonzero(comp["cal_blk"] == b)[0]
    comp["cal_c"][ci], comp["cal_s"][ci] = (
        comp["row_c"][r],
        comp["row_s"][r],
    )  # a test row planted in its own block's calibration pool
    assert ev.leak_overlap(comp) >= 1


def test_midmed_is_zero_in_expectation_for_a_calibrated_integer_forecast() -> None:
    rng = np.random.default_rng(0)
    n = 40_000
    lam = rng.uniform(1, 12, n)
    y = rng.poisson(lam).astype(float)
    taus = gate.TAUS
    from scipy.stats import poisson

    qi = np.stack(
        [poisson.ppf(taus, l_) for l_ in lam[:2000]]
    )  # a calibrated arm; quantiles are integers
    mm = ev.midmed(qi, y[:2000])
    big = np.stack([poisson.ppf(taus, l_) for l_ in lam[:n:8]])
    mm_big = ev.midmed(big, y[:n:8])
    assert abs(mm_big.mean()) < 0.02 and np.isfinite(mm).all()
    shifted = ev.midmed(
        big + 1, y[:n:8]
    )  # forecast too high: outcomes fall below the median, the gap is negative
    assert shifted.mean() < -0.05


def test_cell_of_matches_lower_tail_bins() -> None:
    x = np.array([0.0, 14.99, 15.0, 24.99, 25.0, 40.0])
    assert list(gate.cell_of(x)) == [0, 0, 1, 1, 2, 2]


def test_synthetic_y_is_reproducible_integer_and_label_free() -> None:
    comp = make_comp()
    a, b = ev.synthetic_y(comp, "oracle", 5), ev.synthetic_y(comp, "oracle", 5)
    assert np.array_equal(a, b) and (a == np.round(a)).all() and (a >= 0).all()
    assert "row_y" not in comp
    assert not np.array_equal(ev.synthetic_y(comp, "a0", 5), a)


def test_oracle_truth_shows_a_cell_gap_that_the_candidate_removes() -> None:
    comp = make_comp(rows=4000, cal=6000)
    y = ev.synthetic_y(comp, "oracle", 1)
    q0, qc = gate.a0_grid(comp), ev.cand_grid(comp)
    cell0 = gate.cell_of(comp["row_min10"]) == 0
    g0 = ev.arm_rows(q0, y)["mm"][cell0].mean()
    gc = ev.arm_rows(qc, y)["mm"][cell0].mean()
    assert abs(gc) < abs(g0)


def test_bh_and_boot() -> None:
    p = np.array([0.01, 0.04, 0.03, 0.5])
    adj = ev.bh_adjust(p)
    assert np.all(adj >= p) and adj[0] == pytest.approx(0.04) and adj[3] == pytest.approx(0.5)
    gid = np.repeat(np.arange(100), 5).astype(str)
    v = np.random.default_rng(0).normal(0.5, 1, 500)
    bt = ev.Boot(gid, 1, 300)
    r = bt.reps(v, np.ones(500, bool))
    assert bt.point(v, np.ones(500, bool)) == pytest.approx(v.mean())
    assert np.nanpercentile(r, 2.5) < v.mean() < np.nanpercentile(r, 97.5)


def _score(
    gap_a0: float, gap_c: float, p: float, dcrps: float, hi: float, dk_gain: float = 1.5
) -> dict[str, Any]:
    def per() -> dict[str, Any]:
        d: dict[str, Any] = {}
        for st in gate.STATS:
            cells = {c: {"A0": {"point": 1.0}, "CAND": {"point": 0.5}} for c in gate.CELLS}
            d[st] = {
                "H": {"A0": {"point": gap_a0}, "CAND": {"point": gap_c}, "p_bh": p},
                "cells": cells,
                "crps_int": {"CAND_minus_A0": {"point": dcrps, "lo": dcrps - 0.001, "hi": hi}},
            }
        d["pooled"] = {
            "H": {
                "A0": {"point": gap_a0},
                "CAND": {"point": gap_c},
                "abs_CAND_minus_A0": {
                    "point": abs(gap_c) - abs(gap_a0),
                    "hi": abs(gap_c) - abs(gap_a0) + 0.3,
                },
                "p_CAND_abs_not_smaller": p,
            },
            "dk_gain_pp": {"point": dk_gain, "lo": dk_gain - 0.2, "hi": dk_gain + 0.2},
        }
        return d

    return {"by_season": {"2024": per(), "2023": per()}}


GUARDS_OK = {k: {"pass": True} for k in ("G1_null", "G2_placebo", "G3_leak", "G4_a0_repro")}


def test_words_follow_the_frozen_rule() -> None:
    assert ev.words(_score(3.0, 0.4, 0.001, -0.003, -0.001), GUARDS_OK)["word"] == "MEDIAN_FIXED"
    w = ev.words(_score(3.0, 0.4, 0.001, -0.003, -0.001), GUARDS_OK)
    assert w["ON_LINES"] and w["IMPROVES_CRPS"] and all(w["per_stat_fixed_bh"].values())
    assert not ev.words(_score(3.0, 0.4, 0.001, -0.001, -0.0005), GUARDS_OK)["IMPROVES_CRPS"]
    assert (
        ev.words(_score(3.0, 0.4, 0.001, 0.0005, 0.002), GUARDS_OK)["word"]
        == "FIXED_BUT_HARMS_CRPS"
    )
    assert (
        ev.words(_score(3.0, 1.4, 0.001, -0.003, -0.001), GUARDS_OK)["word"] == "PARTIAL"
    )  # shrinks, but the pooled gap stays above 1.0 pp
    assert (
        ev.words(_score(3.0, 0.4, 0.2, -0.003, -0.001), GUARDS_OK)["word"] == "NOT_FIXED"
    )  # the reduction is not significant
    assert (
        ev.words(_score(0.6, 0.4, 0.001, -0.003, -0.001), GUARDS_OK)["word"] == "NOT_FIXED"
    )  # reduction 0.2 pp is below the 0.5 pp floor
    bad = dict(GUARDS_OK) | {"G1_null": {"pass": False}}
    assert ev.words(_score(3.0, 0.4, 0.001, -0.003, -0.001), bad)["word"] == "GUARD_FAIL"
    assert (
        ev.words(_score(0.1, 0.1, 0.9, 0.0, 0.0), bad)["word"] == "NOT_FIXED"
    )  # a null stays a null even when a guard also fails
    assert not ev.words(_score(3.0, 0.4, 0.001, -0.003, -0.001, dk_gain=0.5), GUARDS_OK)["ON_LINES"]


def test_load_comp_refuses_season_2025_and_hides_labels(tmp_path: Path) -> None:
    comp = make_comp(rows=50, cal=80)
    arrays = {k: v for k, v in comp.items()} | {"row_y": np.zeros(comp["row_c"].size)}
    root = tmp_path
    (root / "data/pinned/median_bias").mkdir(parents=True)
    np.savez(root / gate.COMP_FILES["pts"], **cast(Any, arrays))
    got = gate.load_comp(root, "pts")
    assert "row_y" not in got
    assert "row_y" in gate.load_comp(root, "pts", labels=True)
    bad = {
        **arrays,
        "row_min10": np.where(np.arange(comp["row_c"].size) == 3, np.nan, comp["row_min10"]),
    }
    np.savez(root / gate.COMP_FILES["pts"], **cast(Any, bad))
    with pytest.raises(AssertionError, match="NaN"):
        gate.load_comp(root, "pts")
    arrays["row_season"] = np.full(comp["row_c"].size, 2025)
    np.savez(root / gate.COMP_FILES["pts"], **cast(Any, arrays))
    with pytest.raises(AssertionError):
        gate.load_comp(root, "pts")


# ------------------------------------------------------------------------------------ freeze / pin refusals


def test_text_sha_stops_at_the_marker() -> None:
    a = "rule\nbody\n" + rule.MARKER + "\nresult one\n"
    b = "rule\nbody\n" + rule.MARKER + "\nresult two\n"
    assert rule.text_sha(a) == rule.text_sha(b)
    assert rule.text_sha(a) != rule.text_sha("rule\nbody changed\n" + rule.MARKER + "\n")


def test_require_frozen_refuses_without_a_matching_commit(tmp_path: Path) -> None:
    doc = tmp_path / "MEDIAN_BIAS.md"
    doc.write_text("rule\n" + rule.MARKER + "\n")
    sha = rule.text_sha(doc.read_text())
    with pytest.raises(RuntimeError):
        rule.require_frozen(None, doc)
    with pytest.raises(RuntimeError):
        rule.require_frozen("0" * 64, doc, lambda *a: "")
    with pytest.raises(RuntimeError):
        rule.require_frozen(sha, doc, lambda *a: "")  # no prereg commit in the log


def test_require_harness_pinned_refuses_without_pin_or_with_drift(tmp_path: Path) -> None:
    sha = "a" * 64

    def run_no_pin(*args: str) -> str:
        if args[0] == "log":
            return f"c1\tprereg(MEDIAN_BIAS): {sha}\n"
        return "rule\n" + rule.MARKER + "\n"

    # freeze_commit needs the doc at the commit to hash to sha; fake it by monkeypatching text_sha
    orig = rule.text_sha
    rule.text_sha = lambda text: sha
    try:
        with pytest.raises(RuntimeError, match="not pinned"):
            rule.require_harness_pinned(sha, run_no_pin, tmp_path, lambda *a: True)

        def run_pin(*args: str) -> str:
            if args[0] == "log":
                return f"p1\tharness(MEDIAN_BIAS): {'b' * 64}\nc1\tprereg(MEDIAN_BIAS): {sha}\n"
            return "frozen version\n"

        for rel in rule.HARNESS_FILES:
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_text("edited on disk\n")
        with pytest.raises(RuntimeError, match="differs"):
            rule.require_harness_pinned(sha, run_pin, tmp_path, lambda *a: True)
    finally:
        rule.text_sha = orig


def test_pin_is_order_free_and_tracks_every_file() -> None:
    blobs = {k: "0" * 64 for k in rule.HARNESS_FILES}
    p = rule.pin_of(blobs)
    assert p == rule.pin_of(dict(reversed(list(blobs.items()))))
    blobs2 = dict(blobs)
    blobs2[rule.HARNESS_FILES[0]] = "1" * 64
    assert rule.pin_of(blobs2) != p
    assert set(rule.HARNESS_FILES) == {
        "research/eval/median_bias_eval.py",
        "research/eval/median_bias_rule.py",
        "research/eval/median_bias_gate.py",
    }


def test_declared_reads_hash_lines_above_the_marker(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    doc.write_text(f"x\nCOMPONENTS_SHA256_PTS: {'c' * 64}\n{rule.MARKER}\nOTHER: {'d' * 64}\n")
    assert rule.declared(doc) == {"COMPONENTS_SHA256_PTS": "c" * 64}


def test_real_path_runs_end_to_end_on_a_fake_root(tmp_path: Path) -> None:
    """The real-outcome branch of ``evaluate`` (row_y loaded, A0 repro skipped) on a tiny synthetic root: shapes, keys, no crash."""
    import pandas as pd

    root = tmp_path
    (root / "data/pinned/median_bias").mkdir(parents=True)
    (root / "data/ops/model_clv_pairs").mkdir(parents=True)
    rows = []
    for k, st in enumerate(gate.STATS):
        comp = make_comp(seed=k, n_blocks=4, rows=500, cal=1200, two_seasons=True)
        y = ev.synthetic_y(comp, "oracle", 3 + k)
        np.savez(root / gate.COMP_FILES[st], **cast(Any, comp | {"row_y": y}))
        for g, p in list(zip(comp["row_gid"], comp["row_pid"], strict=True))[::3]:
            rows.append(
                {
                    "game_id": g,
                    "player_id": int(p),
                    "stat": st,
                    "book": "draftkings",
                    "snapshot_kind": "t60",
                }
            )
    for y_ in gate.SEASONS:
        pd.DataFrame(rows).to_parquet(root / gate.PAIRS_FILES[y_])
    res = ev.evaluate(root, True, "", 3, None)
    assert res["real_outcomes"] and res["decision"]["word"] in {
        "MEDIAN_FIXED",
        "PARTIAL",
        "NOT_FIXED",
        "FIXED_BUT_HARMS_CRPS",
        "GUARD_FAIL",
    }
    assert set(res["score"]["by_season"]) == {"2023", "2024"}
    assert res["guards"]["G3_leak"]["pass"]
    h = res["score"]["by_season"]["2024"]["pts"]["H"]
    assert h["n"] > 0 and {"A0", "CAND", "PLACEBO"} <= set(h)


# ------------------------------------------------------------------------------------ pre-freeze review items 1, 6, 7


def _arms_from(comps: dict[str, dict[str, np.ndarray]], truth: str) -> dict[str, Any]:
    arms: dict[str, Any] = {}
    for k, (st, comp) in enumerate(comps.items()):
        y = ev.synthetic_y(comp, truth, seed=3 + k)
        arms[st] = {
            "season": comp["row_season"].astype(int),
            "cell": gate.cell_of(comp["row_min10"]),
            "y": y,
            "qi": {"A0": gate.to_int(gate.a0_grid(comp)).astype(np.int16)},
            "null_comp": ev.report_subset(comp),
        }
    return arms


def test_calibration_shuffle_null_is_centred_on_zero_when_cells_share_one_pool() -> None:
    comps = {}
    for k, st in enumerate(gate.STATS):
        c = make_comp(seed=k, n_blocks=4, rows=3000, cal=40000, two_seasons=True)
        c["cal_z"] = np.random.default_rng(50 + k).normal(
            0, 1, c["cal_z"].size
        )  # one pool for every cell
        comps[st] = c
    nd = ev.cal_shuffle_null(_arms_from(comps, "a0"), 2024, 6)
    assert abs(nd["gain_pp"].mean()) < 0.1 and abs(nd["dcrps"].mean()) < 0.002


def test_calibration_shuffle_null_removes_a_real_cell_gain() -> None:
    comps = {
        st: make_comp(seed=k, n_blocks=4, rows=3000, cal=40000, two_seasons=True)
        for k, st in enumerate(gate.STATS)
    }
    arms = _arms_from(comps, "oracle")
    real = []
    for st, a in arms.items():
        lo = np.flatnonzero(a["season"] == 2024)
        qc = gate.to_int(ev.cand_grid(report := ev.report_subset(comps[st]))).astype(np.int16)
        assert report["row_c"].size == lo.size
        H = a["cell"][lo] == gate.H_CELL
        y = a["y"][lo]
        real.append((ev.midmed(a["qi"]["A0"][lo], y)[H].sum(), ev.midmed(qc, y)[H].sum(), H.sum()))
    s0, sc, n = (sum(x[i] for x in real) for i in range(3))
    obs = 100 * (abs(s0 / n) - abs(sc / n))
    nd = ev.cal_shuffle_null(arms, 2024, 4)
    assert obs > nd["gain_pp"].mean() + 3 * max(nd["gain_pp"].std(ddof=1), 0.01)


def test_truths_mix50_and_harm_are_integer_reproducible_and_distinct() -> None:
    comp = make_comp()
    ys = {tr: ev.synthetic_y(comp, tr, 5) for tr in ev.TRUTHS}
    for y in ys.values():
        assert (y == np.round(y)).all() and (y >= 0).all()
    assert all(not np.array_equal(ys[a], ys[b]) for a in ev.TRUTHS for b in ev.TRUTHS if a < b)
    assert np.array_equal(ev.synthetic_y(comp, "harm", 5), ys["harm"])


def test_crps_harm_flag_and_followup_precedence() -> None:
    harm = season(gap_c=1.2, dcrps=0.0005, hi=0.0011)
    w = ev.words(sc(harm), OK)
    assert (
        w["word"] == "PARTIAL" and w["CRPS_HARM"] and not w["FOLLOWUP_OPEN"]
    )  # PARTIAL with harm closes the idea
    w = ev.words(sc(season(gap_c=1.2)), OK)
    assert (
        w["word"] == "PARTIAL" and not w["CRPS_HARM"] and w["FOLLOWUP_OPEN"]
    )  # under-correction, no harm: follow-up open
    w = ev.words(sc(season(gap_c=-1.2)), OK)
    assert (
        w["word"] == "PARTIAL" and not w["FOLLOWUP_OPEN"]
    )  # overshoot (sign flipped) is not what the nine-cell map addresses
    w = ev.words(sc(season(dcrps=0.0005, hi=0.0011)), OK)
    assert w["word"] == "FIXED_BUT_HARMS_CRPS" and w["CRPS_HARM"]
    assert not ev.words(sc(), OK)["CRPS_HARM"]


def test_run_refuses_before_building_the_candidate_when_g4_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called = {"evaluate": False}
    monkeypatch.setattr(rule, "require_ready", lambda sha: {})
    monkeypatch.setattr(rule, "pin_of", lambda b: "0" * 64)
    monkeypatch.setattr(ev, "preflight", lambda root, decl: {})
    monkeypatch.setattr(rule, "a0_pins_declared", lambda: {})
    monkeypatch.setattr(
        ev, "a0_repro", lambda root, pins: {"pass": False, "mismatches": {"pts_2023": 1.0}}
    )
    monkeypatch.setattr(ev, "evaluate", lambda *a, **k: called.__setitem__("evaluate", True))
    with pytest.raises(RuntimeError, match="before any candidate"):
        ev.main(["run", "--data-root", str(tmp_path), "--sha", "a" * 64])
    assert not called["evaluate"]


def test_preflight_refuses_a_library_or_uv_lock_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    decl = {f"COMPONENTS_SHA256_{s.upper()}": "0" * 64 for s in gate.STATS}
    monkeypatch.setattr(gate, "sha256_file", lambda p: "0" * 64)
    monkeypatch.setattr(
        ev, "versions", lambda: {"numpy": "0.0", "pandas": "3.0.6", "pyarrow": "25.0.1"}
    )
    decl |= {f"PAIRS_SHA256_{y}": "0" * 64 for y in gate.SEASONS}
    with pytest.raises(RuntimeError, match="library versions"):
        ev.preflight(tmp_path, decl)
    monkeypatch.setattr(ev, "versions", lambda: dict(ev.PINNED_VERSIONS))
    with pytest.raises(RuntimeError, match="uv.lock"):
        ev.preflight(tmp_path, decl | {"UV_LOCK_SHA256": "1" * 64})


# Appendix A of the pre-freeze review (adversary): every word, every PARTIAL branch, inclusive boundaries.


def season(
    gap_a0: float = 3.0,
    gap_c: float = 0.4,
    p: float = 0.001,
    dcrps: float = -0.003,
    hi: float = -0.001,
    red_hi: float | None = None,
) -> dict[str, Any]:  # noqa: F811
    d: dict[str, Any] = {}
    for st in gate.STATS:
        d[st] = {
            "H": {"A0": {"point": gap_a0}, "CAND": {"point": gap_c}, "p_bh": p},
            "cells": {c: {"A0": {"point": 1.0}, "CAND": {"point": 0.5}} for c in gate.CELLS},
            "crps_int": {"CAND_minus_A0": {"point": dcrps, "lo": dcrps - 0.001, "hi": hi}},
        }
    red = abs(gap_c) - abs(gap_a0)
    d["pooled"] = {
        "H": {
            "A0": {"point": gap_a0},
            "CAND": {"point": gap_c},
            "abs_CAND_minus_A0": {"point": red, "hi": red + 0.3 if red_hi is None else red_hi},
            "p_CAND_abs_not_smaller": p,
        },
        "dk_gain_pp": {"point": 1.5, "lo": 1.3, "hi": 1.7},
    }
    return d


def sc(rep: dict[str, Any] | None = None, con: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"by_season": {"2024": rep or season(), "2023": con or season()}}


OK = {k: {"pass": True} for k in ("G1_null", "G2_placebo", "G3_leak", "G4_a0_repro")}


def word(s: dict[str, Any], g: dict[str, Any] = OK) -> str:
    return str(ev.words(s, g)["word"])


def test_appendix_a_fixed_harm_and_guard() -> None:
    import copy

    assert word(sc()) == "MEDIAN_FIXED"
    assert word(sc(season(dcrps=0.0005, hi=0.0011))) == "FIXED_BUT_HARMS_CRPS"
    for g in ("G1_null", "G2_placebo", "G3_leak", "G4_a0_repro"):
        bad = copy.deepcopy(OK)
        bad[g]["pass"] = False
        assert word(sc(), bad) == "GUARD_FAIL"
        assert (
            word(sc(season(dcrps=0.0005, hi=0.0011)), bad) == "GUARD_FAIL"
        )  # harm + guard: the guard wins


def test_appendix_a_every_partial_branch() -> None:
    assert word(sc(season(gap_c=1.2))) == "PARTIAL"  # P1a: pooled |gap| above 1.0
    r = season()
    r["reb"]["H"]["CAND"]["point"] = 1.6  # P1c: one stat above the 1.5 cap
    assert word(sc(r)) == "PARTIAL"
    assert word(sc(con=season(red_hi=0.01))) == "PARTIAL"  # P3: 2023 pooled reduction CI crosses 0
    c = season()
    for st in ("pts", "reb"):
        c[st]["H"]["CAND"]["point"] = 3.5  # P3: only 2 of 4 stats smaller in 2023
    assert word(sc(con=c)) == "PARTIAL"
    r = season()
    r["ast"]["cells"]["lt15"] = {
        "A0": {"point": 0.2},
        "CAND": {"point": 0.8},
    }  # P5: a cell worse by 0.6 pp
    assert word(sc(r)) == "PARTIAL"


def test_appendix_a_not_fixed_branches_and_boundaries() -> None:
    import copy

    assert word(sc(season(p=0.06))) == "NOT_FIXED"
    assert word(sc(season(gap_a0=0.8, gap_c=0.4))) == "NOT_FIXED"  # reduction 0.4 < 0.5
    bad = copy.deepcopy(OK)
    bad["G1_null"]["pass"] = False
    assert word(sc(season(p=0.5)), bad) == "NOT_FIXED"
    w = ev.words(sc(season(gap_c=1.2)), OK)
    assert w["word"] == "PARTIAL" and not w["ON_LINES"] and not w["IMPROVES_CRPS"]
    assert word(sc(season(gap_c=1.0))) == "MEDIAN_FIXED"  # P1a: <= 1.0
    assert word(sc(season(hi=0.001))) == "MEDIAN_FIXED"  # P2: <= +0.001
    assert word(sc(season(p=0.05))) == "MEDIAN_FIXED"  # P1b: p <= 0.05
    assert word(sc(season(gap_a0=0.9, gap_c=0.4))) == "MEDIAN_FIXED"  # reduction exactly 0.5
