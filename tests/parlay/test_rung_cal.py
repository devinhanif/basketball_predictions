# ruff: noqa: E501
"""The CAL-rung map: pinned to the frozen ALT_RUNG_CAL harness (research/eval/alt_rung_cal_eval.py)."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from nba.parlay import line_cal as nlc
from nba.parlay import rung_cal as rc
from nba.parlay import rung_cal_build as rb

STATS = rc.STATS


def synth(seed: int = 5) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rungs (3 per unit) and main-line legs, Jan-Jun 2024, harness-shaped arrays (season 2024 only)."""
    rng = np.random.default_rng(seed)
    n_per = 900  # units per stat per month
    cols: dict[str, list[Any]] = {k: [] for k in (
        "pm", "stat", "game", "player", "point", "date", "med", "spr", "st10", "zm", "r", "unit",
        "main", "hit", "q", "thr")}  # fmt: skip
    legs: dict[str, list[Any]] = {k: [] for k in (
        "stat", "date", "pm", "line", "med", "spr", "st10", "over", "unit")}  # fmt: skip
    uid = 0
    for stat_i, stat in enumerate(STATS):
        for month in range(1, 7):
            for _ in range(n_per):
                uid += 1
                day = np.datetime64(f"2024-{month:02d}-01") + int(rng.integers(0, 27))
                med = float(rng.uniform(2, 25))
                spr = float(max(rng.uniform(0.8, 6), 0.5))
                st10 = float(rng.choice([0.0, 1.0]))
                main = np.floor(med) + 0.5
                y = float(max(0, round(med + spr * rng.standard_normal())))
                u = f"g{uid}|{uid}{stat}"
                lg_pm = float(np.clip(0.5 + 0.1 * rng.standard_normal(), 0.05, 0.95))
                legs["stat"].append(stat)
                legs["date"].append(day)
                legs["pm"].append(lg_pm)
                legs["line"].append(main)
                legs["med"].append(med)
                legs["spr"].append(spr)
                legs["st10"].append(st10)
                legs["over"].append(float(y >= main + 0.5))
                legs["unit"].append(u)
                for k in range(3):
                    point = main + k
                    pm = float(np.clip(0.9 - 0.18 * k + 0.07 * rng.standard_normal(), 0.5, 0.97))
                    cols["pm"].append(pm)
                    cols["stat"].append(stat)
                    cols["game"].append(f"g{uid}")
                    cols["player"].append(uid)
                    cols["point"].append(point)
                    cols["date"].append(day)
                    cols["med"].append(med)
                    cols["spr"].append(spr)
                    cols["st10"].append(st10)
                    cols["main"].append(main)
                    cols["zm"].append(float(np.clip((main - med) / spr, -5, 5)))
                    cols["r"].append(float(np.clip((point - main) / spr, -8, 8)))
                    cols["unit"].append(u)
                    cols["q"].append(y)
                    cols["thr"].append(point + 0.5)
                    cols["hit"].append(float(y >= point + 0.5))
        _ = stat_i
    R: dict[str, Any] = {k: np.asarray(v) for k, v in cols.items()}
    R["date"] = R["date"].astype("datetime64[D]")
    R["x"] = np.log(R["pm"]) - np.log1p(-R["pm"])
    R["z"] = np.zeros_like(R["pm"])
    R["bench"] = (R["st10"] < 0.5).astype(np.int64)
    R["has_main"] = np.ones(R["pm"].size, dtype=bool)
    R["season"] = np.full(R["pm"].size, 2024, dtype=np.int64)
    R["real"] = True
    L: dict[str, Any] = {k: np.asarray(v) for k, v in legs.items()}
    L["date"] = L["date"].astype("datetime64[D]")
    return R, L


def test_conventions_match_the_harness() -> None:
    h = pytest.importorskip("research.eval.alt_rung_cal_eval")
    assert (rc.R_CLIP, rc.ZM_CLIP) == (h.R_CLIP, h.ZM_CLIP)
    assert nlc.EPS == h.EPS and tuple(h.STATS) == rc.STATS and rc.BOOK == h.PRIMARY
    assert rc.TERMS == ("1", "x", "zm", "r", "r*x", "bench", "bench*x")
    assert nlc.MAX_FIT_DATE.isoformat() == "2025-06-30"


def test_serving_reproduces_the_harness_cal_rung_for_one_2024_block() -> None:
    h = pytest.importorskip("research.eval.alt_rung_cal_eval")
    R, L = synth()
    arms, info = h.run_book(R, L, 0, "draftkings")
    block = "2024-04"
    for stat in STATS:
        rec = next(r for r in info if r["stat"] == stat and r["block"] == block)
        assert rec["all_arms_fit"]
        rmap = rc.RungCalMap(
            {"map_sha256": "x" * 64}, {stat: rc.RungCell(stat, tuple(rec["beta_rung"]), 1, 1.0)}
        )
        in_block = (
            (R["stat"] == stat) & (R["date"] >= np.datetime64("2024-04-01"))
            & (R["date"] < np.datetime64("2024-05-01"))
        )  # fmt: skip
        assert in_block.sum() > 0
        got = np.full(R["pm"].size, np.nan)
        for u in np.unique(R["unit"][in_block]):  # one ladder at a time, as served
            m = in_block & (R["unit"] == u)
            i = np.flatnonzero(m)[0]
            got[m] = rmap.apply_many(
                stat,
                R["pm"][m],
                R["point"][m],
                R["main"][i],
                R["med"][i],
                R["spr"][i],
                R["st10"][i],
            )
        assert np.allclose(got[in_block], arms["CAL_rung"][in_block], rtol=0, atol=1e-12)
        assert np.max(np.abs(got[in_block] - arms["CAL_rung"][in_block])) < 1e-12


def test_static_map_coefficients_come_out_of_the_harness_fit_code() -> None:
    h = pytest.importorskip("research.eval.alt_rung_cal_eval")
    lc = pytest.importorskip("research.eval.line_calibration_eval")
    R, _ = synth()
    start = np.datetime64("2025-07-01")
    cells = {c.stat: c for c in rc.fit_cells(R, start)}
    Xr = h.rung_design(R)
    assert np.allclose(Xr, rc.design(R["pm"], R["point"], R["main"], R["med"], R["spr"], R["st10"]))
    for stat in STATS:
        fit = (R["stat"] == stat) & R["has_main"] & (R["date"] < start)
        w = lc.age_weights(R["date"][fit], start)
        beta = lc.fit_logistic(Xr[fit], R["hit"][fit], w=w)
        assert cells[stat].beta == pytest.approx(tuple(beta), abs=1e-12)
        assert cells[stat].n_fit == int(fit.sum())


def test_season_2025_is_refused() -> None:
    R, _ = synth()
    bad = copy.deepcopy(R)
    bad["date"][0] = np.datetime64("2025-07-02")
    with pytest.raises(ValueError, match="holdout"):
        rc.fit_cells(bad, np.datetime64("2025-07-01"))
    bad = copy.deepcopy(R)
    bad["season"][3] = 2025
    with pytest.raises(ValueError, match="holdout"):
        rc.fit_cells(bad, np.datetime64("2025-07-01"))
    with pytest.raises(ValueError, match="holdout"):
        rc.fit_cells(R, np.datetime64("2025-09-01"))


def test_shipped_map_loads_verified_and_tamper_is_refused(tmp_path: Path) -> None:
    m = rc.load_map()
    assert m.meta["map_version"] == rc.MAP_VERSION and len(m.map_sha8) == 8
    assert m.meta["rule_sha8"] == "425e4153" and m.meta["fit_through"] == "2025-06-30"
    assert m.meta["book"] == "draftkings" and "FanDuel" in m.meta["fanduel"]
    assert all(m.has(s) for s in STATS) and m.meta["row_counts"]["seasons"] == [2023, 2024]
    p = json.loads(rc.MAP_PATH.read_text())
    p["cells"][0]["beta"][1] += 0.01
    f = tmp_path / "tampered.json"
    f.write_text(json.dumps(p))
    with pytest.raises(ValueError, match="map_sha256"):
        rc.load_map(f)
    p = json.loads(rc.MAP_PATH.read_text())
    p["fit_through"] = "2025-07-01"
    p["map_sha256"] = rc.payload_sha(p)
    f.write_text(json.dumps(p))
    with pytest.raises(ValueError, match="holdout"):
        rc.load_map(f)


def test_shipped_map_equals_a_fresh_fit_when_arrays_are_given() -> None:
    path = os.environ.get("RUNG_CAL_ARRAYS")
    if not path:
        pytest.skip("set RUNG_CAL_ARRAYS to the extracted fit arrays to re-derive the shipped map")
    fresh = rb.build_payload(Path(path))
    shipped = json.loads(rc.MAP_PATH.read_text())
    assert fresh == shipped


def _surv(mean: float = 12.0, sd: float = 4.0, k: int = 60) -> np.ndarray:
    from math import erf, sqrt

    ks = np.arange(1, k + 1)
    return np.array([0.5 * (1 - erf((x - 0.5 - mean) / (sd * sqrt(2)))) for x in ks])


def _stub_map(beta: tuple[float, ...]) -> rc.RungCalMap:
    return rc.RungCalMap({"map_sha256": "ab" * 32}, {"pts": rc.RungCell("pts", beta, 1, 1.0)})


def test_lines_range_monotone_and_identity() -> None:
    surv = _surv()
    ident = _stub_map((0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0))  # p = A0
    counts: dict[str, int] = {}
    lines = rc.rung_cal_lines(
        surv, 12.5, 1.0, "pts", 12.0, 4.0, rmap=ident, counts=counts, served_only=False
    )
    assert lines is not None
    ln = [d["line"] for d in lines]
    assert ln == sorted(ln) and all((x * 2) % 2 == 1 for x in ln) and np.all(np.diff(ln) == 1.0)
    # 5th / 95th percentile of the integer distribution, outward to .5
    cdf = 1 - surv
    m05, m95 = int(np.argmax(cdf >= 0.05)), int(np.argmax(cdf >= 0.95))
    assert ln[0] == m05 - 0.5 and ln[-1] == m95 + 0.5
    p = [d["p_over"] for d in lines]
    assert all(a >= b for a, b in zip(p, p[1:], strict=False))
    i = ln.index(12.5)
    assert p[i] == pytest.approx(round(float(surv[12]), 4))  # P(Y >= 13)
    assert counts["ok"] == 1 and counts["lines"] == len(ln) and counts["dropped_nonmonotone"] == 0
    assert counts["below_fit_support"] == sum(1 for d in lines if d["p_over"] < 0.5)


def test_served_only_keeps_raw_p_at_least_half_and_checks_those_alone() -> None:
    surv = _surv()
    ident = _stub_map((0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0))
    counts: dict[str, int] = {}
    full = rc.rung_cal_lines(surv, 12.5, 1.0, "pts", 12.0, 4.0, rmap=ident, served_only=False)
    served = rc.rung_cal_lines(surv, 12.5, 1.0, "pts", 12.0, 4.0, rmap=ident, counts=counts)
    assert full is not None and served is not None and len(served) < len(full)
    for d in served:
        assert surv[int(d["line"] + 0.5) - 1] >= rc.FIT_SUPPORT_MIN_P
    assert served == [d for d in full if surv[int(d["line"] + 0.5) - 1] >= rc.FIT_SUPPORT_MIN_P]
    assert counts["below_fit_support"] == len(full) - len(served) and counts["lines"] == len(served)


def test_nonmonotone_only_outside_the_support_is_not_dropped() -> None:
    surv = _surv()

    class Bent:
        """Calibrated values fall with the line where raw P >= 0.5 and rise again below it."""

        def has(self, stat: str) -> bool:
            return True

        def apply_many(self, stat: str, pm: np.ndarray, point: np.ndarray, *a: float) -> np.ndarray:
            return np.where(pm >= rc.FIT_SUPPORT_MIN_P, pm, 0.2 + 0.1 * point / point.max())

    bent: Any = Bent()
    c_all: dict[str, int] = {}
    c_srv: dict[str, int] = {}
    full = rc.rung_cal_lines(
        surv, 12.5, 1.0, "pts", 12.0, 4.0, rmap=bent, counts=c_all, served_only=False
    )
    srv = rc.rung_cal_lines(surv, 12.5, 1.0, "pts", 12.0, 4.0, rmap=bent, counts=c_srv)
    assert full is None and c_all["dropped_nonmonotone"] == 1
    assert srv is not None and c_srv["dropped_nonmonotone"] == 0 and c_srv["ok"] == 1


def test_no_supported_lines_is_none_and_counted() -> None:
    counts: dict[str, int] = {}
    ident = _stub_map((0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0))
    low = np.array([0.4, 0.2, 0.1, 0.05, 0.01])  # every raw P(Y >= k) < 0.5
    assert rc.rung_cal_lines(low, 2.5, 1.0, "pts", 2.0, 1.0, rmap=ident, counts=counts) is None
    assert counts["no_supported_lines"] == 1 and counts["ok"] == 0


def test_non_monotone_is_dropped_and_counted() -> None:
    # a strongly positive r*x slope reverses the order of the calibrated probabilities
    bad = _stub_map((0.0, 0.0, 0.0, 0.0, 3.0, 0.0, 0.0))
    counts: dict[str, int] = {}
    assert rc.rung_cal_lines(_surv(), 12.5, 1.0, "pts", 12.0, 4.0, rmap=bad, counts=counts) is None
    assert counts["dropped_nonmonotone"] == 1 and counts["ok"] == 0


def test_no_map_for_stat_is_none_and_counted() -> None:
    counts: dict[str, int] = {}
    ident = _stub_map((0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0))
    assert (
        rc.rung_cal_lines(_surv(), 12.5, 1.0, "blk", 12.0, 4.0, rmap=ident, counts=counts) is None
    )
    assert counts["no_map"] == 1


def test_grid_med_spr_is_the_harness_definition() -> None:
    g = np.sort(np.random.default_rng(1).normal(10, 3, 199))
    med, spr = rc.grid_med_spr(g)
    assert med == pytest.approx(g[99]) and spr > 0.5


def test_no_export_import_no_execution_surface() -> None:
    root = Path(rc.__file__).resolve().parent
    for name in ("rung_cal.py", "rung_cal_build.py"):
        text = (root / name).read_text()
        for bad in ("nba.export", "import research", "from research", "requests", "httpx", "urllib",
                    "/orders", "portfolio", "KALSHI_API"):  # fmt: skip
            assert bad not in text, (name, bad)


def test_real_2024_block_matches_the_harness_when_data_root_is_given() -> None:
    """Maintainer check (set RUNG_CAL_DATA_ROOT to the main checkout): the serving function with the
    block's walk-forward coefficients reproduces the harness's stored CAL-rung probabilities."""
    root = os.environ.get("RUNG_CAL_DATA_ROOT")
    if not root:
        pytest.skip("set RUNG_CAL_DATA_ROOT to run on the real 2024 rungs")
    h = pytest.importorskip("research.eval.alt_rung_cal_eval")
    fr = h.build_frames(Path(root))
    h.attach_real_labels(fr, Path(root))
    R, L = fr["rungs"]["draftkings"], fr["legs"]
    arms, info = h.run_book(R, L, 0, "draftkings")
    stored = np.load(Path(root) / "reports/alt_rung_cal/arrays_draftkings.npz")
    sc = arms["scored"] > 0
    assert int(sc.sum()) == stored["CAL_rung"].size
    assert np.allclose(arms["CAL_rung"][sc], stored["CAL_rung"], rtol=0, atol=1e-12)
    for stat in STATS:
        rec = next(r for r in info if r["stat"] == stat and r["block"] == "2024-03")
        rmap = rc.RungCalMap(
            {"map_sha256": "x" * 64}, {stat: rc.RungCell(stat, tuple(rec["beta_rung"]), 1, 1.0)}
        )
        blk = (
            sc & (R["stat"] == stat) & (R["date"] >= np.datetime64("2024-03-01"))
            & (R["date"] < np.datetime64("2024-04-01"))
        )  # fmt: skip
        assert blk.sum() > 100
        main = R["point"] - R["r"] * R["spr"]  # clipped rows excluded below
        ok = blk & (np.abs(R["r"]) < rc.R_CLIP) & (np.abs(R["zm"]) < rc.ZM_CLIP)
        out = np.empty(int(ok.sum()))
        idx = np.flatnonzero(ok)
        for j, i in enumerate(idx):
            out[j] = rmap.apply_many(
                stat,
                R["pm"][i : i + 1],
                R["point"][i : i + 1],
                main[i],
                R["med"][i],
                R["spr"][i],
                R["st10"][i],
            )[0]
        assert np.max(np.abs(out - arms["CAL_rung"][ok])) < 1e-9
