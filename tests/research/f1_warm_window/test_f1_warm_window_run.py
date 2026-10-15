# ruff: noqa: E501
"""F1_WARM_WINDOW harness: the decision (one test per word), the inference code path, the guards, alignment and slices. Synthetic only."""

from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from nba.eval.context_residual_eval import MAX_SEASON
from nba.eval.lower_tail_eval import randomized_pit
from nba.props.metrics import paired_score_delta_ci
from research.eval import f1_warm_window_gate as gate
from research.eval import f1_warm_window_run as R

DOC = Path(__file__).resolve().parents[3] / "docs/prereg/F1_WARM_WINDOW.md"


# ----------------------------------------------------------------- one test per word
@pytest.mark.parametrize(
    ("world", "word"),
    [
        ("helps", "PASS"),
        ("null", "FAIL_NO_GAIN"),
        ("harm", "FAIL_HARM"),
        ("one_season", "FAIL_ONE_SEASON"),
        ("guard", "FAIL_GUARD"),
    ],
)
def test_each_word_is_reachable_in_its_world(world: str, word: str) -> None:
    res = R.evaluate(R.synthetic_bundle(world))
    assert res["word"] == word
    assert res["verdict"] == ("PASS" if word == "PASS" else "FAIL")


def test_every_word_is_tested_and_written_in_the_rule() -> None:
    keep: list[str] = []
    for line in DOC.read_text().splitlines():
        if line == "=== RESULTS BELOW ===":
            break
        keep.append(line)
    text = "\n".join(keep)
    for w in R.WORDS:
        assert w in text, w


def test_helps_world_confirms_every_stat_and_null_confirms_none() -> None:
    h = R.evaluate(R.synthetic_bundle("helps"))
    assert all(v["confirmed"] for v in h["stats"].values())
    n = R.evaluate(R.synthetic_bundle("null"))
    assert not any(v["clears_2023"] or v["clears_2024"] for v in n["stats"].values())


def test_one_season_world_clears_2023_only() -> None:
    res = R.evaluate(R.synthetic_bundle("one_season"))
    assert all(v["clears_2023"] and not v["clears_2024"] for v in res["stats"].values())


def test_harm_world_flags_pts_only() -> None:
    res = R.evaluate(R.synthetic_bundle("harm"))
    assert res["stats"]["pts"]["harm"] and not any(
        res["stats"][s]["harm"] for s in ("reb", "ast", "fg3m")
    )


def test_harm_outranks_a_confirmed_stat() -> None:
    b = R.synthetic_bundle("helps")
    d = b["stats"]["pts"]
    m = d["season"] == 2024
    d["score"]["C"] = d["score"]["A"].copy()
    d["score"]["C"][m] += 0.012  # pts is worse in 2024 while reb / ast / fg3m are confirmed
    assert R.evaluate(b)["word"] == "FAIL_HARM"


def test_gate_or_reproduction_failure_is_a_stop_not_a_word() -> None:
    b = R.synthetic_bundle("helps")
    with pytest.raises(RuntimeError, match="STOP"):
        R.evaluate(b, gates_ok=False)
    with pytest.raises(RuntimeError, match="STOP"):
        R.evaluate(b, repro_ok=False)


def test_a_gain_between_zero_and_the_floor_does_not_clear() -> None:
    b = R.synthetic_bundle("null")
    for st, d in b["stats"].items():
        d["score"]["C"] = d["score"]["A"] - 0.6 * R.FLOOR[st]  # real but below the practical floor
    res = R.evaluate(b)
    assert res["word"] == "FAIL_NO_GAIN"
    assert all(r["ci_hi"] < 0 and not r["clears"] for r in res["rows"])


def test_floors_are_the_declared_ones() -> None:
    assert R.FLOOR == {"pts": 0.005, "reb": 0.0025, "ast": 0.0025, "fg3m": 0.0025} == gate.FLOOR


# ----------------------------------------------------------------- inference code path
def test_ci_equals_paired_score_delta_ci_and_p_formula() -> None:
    rng = np.random.default_rng(1)
    gid = np.repeat(np.arange(200), 10)
    a, c = rng.normal(1.0, 0.3, 2000), rng.normal(1.0, 0.3, 2000)
    s = R.delta_stats(c - a, gid)
    ci = paired_score_delta_ci(c, a, n_boot=2000, seed=0, cluster_ids=gid)
    assert (
        abs(s["ci_lo"] - ci.lo) < 1e-12
        and abs(s["ci_hi"] - ci.hi) < 1e-12
        and abs(s["delta"] - ci.point) < 1e-12
    )
    strong = R.delta_stats(np.full(2000, -0.5) + rng.normal(0, 0.01, 2000), gid)
    assert strong["p"] == pytest.approx(2.0 / 2001)  # no draw reaches 0: p = 2 (1 + 0) / (B + 1)
    assert 0.0 < s["p"] <= 1.0 and s["n_clusters"] == 200 and s["n_rows"] == 2000


def test_clustering_widens_the_interval_relative_to_rows() -> None:
    rng = np.random.default_rng(2)
    g = rng.normal(0, 1, 100)
    d = np.repeat(g, 20) + rng.normal(0, 0.1, 2000)
    by_game = R.delta_stats(d, np.repeat(np.arange(100), 20))
    by_row = R.delta_stats(d, np.arange(2000))
    assert by_game["ci_hi"] - by_game["ci_lo"] > 2 * (by_row["ci_hi"] - by_row["ci_lo"])


def test_bh_adjust_known_values() -> None:
    assert np.allclose(R.bh_adjust(np.array([0.01, 0.02, 0.03, 0.04])), [0.04] * 4)
    assert np.allclose(R.bh_adjust(np.array([0.001, 0.5, 0.5, 0.5])), [0.004, 0.5, 0.5, 0.5])


def test_pit_matches_production_randomized_pit_for_the_same_uniforms() -> None:
    rng = np.random.default_rng(3)
    q = np.sort(rng.integers(0, 30, (400, 199)), axis=1).astype(float)
    y = rng.integers(0, 30, 400).astype(float)
    u = np.random.default_rng(0).random(400)
    assert np.allclose(R.pit_from_qi(q, y, u), randomized_pit(q, y, seed=0))


# ----------------------------------------------------------------- guards, each one can fail
def test_g1_uses_distance_to_nominal_not_difference_from_a() -> None:
    nominal = {f"{t:.2f}": t for t in R.COV_TAUS}
    off = {f"{t:.2f}": t + 0.05 for t in R.COV_TAUS}
    assert R.guard_g1(off, nominal)  # C better calibrated than A by 0.05: must pass
    assert not R.guard_g1(nominal, off)  # C worse by 0.05
    near = {f"{t:.2f}": t + 0.009 for t in R.COV_TAUS}
    assert R.guard_g1(nominal, near)


def test_g2_ece_band_and_g3_binding_slices() -> None:
    assert R.guard_g2(0.020, 0.0249) and not R.guard_g2(0.020, 0.0251)
    ok, bad = R.guard_g3(
        {"x": (0.011, 299), "y": (0.009, 500)}, 0.005
    )  # limit 2 x floor = 0.01; x not binding (n < 300)
    assert ok and bad == []
    ok, bad = R.guard_g3({"x": (0.0101, 300)}, 0.005)
    assert not ok and bad == ["x"]


def test_ece_zero_for_perfectly_calibrated_bins_and_positive_otherwise() -> None:
    p = np.repeat([0.1, 0.9], 1000)
    y = np.concatenate([np.r_[np.ones(100), np.zeros(900)], np.r_[np.ones(900), np.zeros(100)]])
    assert R.ece(p, y) == pytest.approx(0.0)
    assert R.ece(np.full(1000, 0.5), np.ones(1000)) == pytest.approx(0.5)


def test_slice_guard_failure_blocks_confirmation() -> None:
    b = R.synthetic_bundle("helps")
    d = b["stats"]["pts"]
    m = d["slices"]["bench"]
    d["score"]["C"] = d["score"]["C"] + np.where(
        m, 0.05, 0.0
    )  # harms the bench slice by more than 2 x floor, mean still gains
    res = R.evaluate(b)
    assert not res["stats"]["pts"]["confirmed"]


# ----------------------------------------------------------------- alignment, windows, slices, versions
def test_align_index_orders_by_reference_and_raises_on_missing() -> None:
    gid = np.array(["g1", "g2", "g3", "g4"])
    pid = np.array([1, 2, 3, 4])
    idx = R.align_index(np.array(["g3", "g1"]), np.array([3, 1]), gid, pid)
    assert idx.tolist() == [2, 0]
    with pytest.raises(RuntimeError, match="missing"):
        R.align_index(np.array(["g9"]), np.array([9]), gid, pid)


def test_windowed_applies_the_declared_start_dates() -> None:
    fr = pl.DataFrame(
        {
            "game_date": [
                dt.date(2019, 9, 30),
                dt.date(2019, 10, 1),
                dt.date(2022, 9, 30),
                dt.date(2022, 10, 1),
            ]
        }
    )
    assert (
        R.windowed(fr, "C").height == 3
        and R.windowed(fr, "B").height == 1
        and R.windowed(fr, "A").height == 1
    )
    assert R.FRAME_OF == {"A": "cold", "B": "warm", "C": "warm"}
    assert R.WINDOW["C"] == gate.WARM_START and R.WINDOW["B"] == R.WINDOW["A"] == gate.COLD_START


def _frame() -> pl.DataFrame:
    n = 6
    return pl.DataFrame(
        {
            "game_id": [
                "0042300101",
                "0022300001",
                "0022300001",
                "0022300002",
                "0022300002",
                "0022300003",
            ],
            "player_id": list(range(n)),
            "n_prior": [30.0, 10.0, 25.0, 6.0, 40.0, 19.0],
            "starter10": [0.9, 0.2, 0.5, 0.0, 1.0, 0.1],
            "n_out_rot": [0, 1, 2, 3, 0, 1],
            "has_report": [1, 1, 1, 1, 0, 1],
            "team_game_no": [90, 3, 14, 15, 40, 60],
            "m10_pts": [10.0] * n,
            "sd10_pts": [3.0] * n,
            "pts": [10] * n,
        }
    )


def test_slice_masks_follow_the_declared_definitions() -> None:
    f = _frame()
    sl = R.slice_masks(f, "pts", f["game_id"].to_numpy(), f["player_id"].to_numpy())
    assert set(sl) == set(R.SLICE_NAMES)
    assert sl["starter"].tolist() == [True, False, True, False, True, False]  # starter10 >= 0.5
    assert sl["bench"].tolist() == [False, True, False, True, False, True]
    assert sl["n_out_rot>=1"].tolist() == [False, True, True, True, False, True]
    assert sl["n_out_rot>=2"].tolist() == [False, False, True, True, False, False]
    assert sl["has_report=0"].tolist() == [False, False, False, False, True, False]
    assert sl["cold_start"].tolist() == [False, True, False, True, False, True]  # n_prior < 20
    assert sl["first15"].tolist() == [False, True, True, False, False, False]  # team_game_no < 15
    assert sl["playoffs"].tolist() == [
        True,
        False,
        False,
        False,
        False,
        False,
    ]  # game_id prefix 004


def _arm(seed: int, order: np.ndarray, extra: int, shift: float) -> dict[str, Any]:
    """A synthetic arm: heads components + mixture rows for 40 base keys (+ ``extra`` keys), in the given order."""
    rng = np.random.default_rng(seed)
    n = 40 + extra
    gid = np.array([f"0022300{i // 4:03d}" for i in range(n)])[order]
    pid = np.arange(n)[order]
    y = (np.arange(n) % 17).astype(float)[order]
    nb, ncal = 2, 300
    blk = (np.arange(n) // 25)[order]
    comp = {
        "row_gid": gid, "row_pid": pid, "row_y": y, "row_season": np.full(n, 2023), "row_blk": blk,
        "row_c": (8 + shift + 0.0 * y), "row_s": np.full(n, 3.0), "row_m": np.full(n, 8.0), "row_min10": np.full(n, 28.0),
        "row_minutes": np.full(n, 30.0),
        "cal_z": rng.normal(0, 1, nb * ncal), "cal_c": np.full(nb * ncal, 8.0), "cal_blk": np.repeat(np.arange(nb), ncal),
        "cal_min10": np.full(nb * ncal, 28.0), "cal_minutes": np.full(nb * ncal, 30.0), "cal_s": np.full(nb * ncal, 3.0), "cal_m": np.full(nb * ncal, 8.0), "cal_y": np.full(nb * ncal, 8.0),
    }  # fmt: skip
    pic = {
        "row_gid": gid,
        "row_pid": pid,
        "row_pi": np.full(n, 0.1),
        **{f"wq_{st}": np.full((nb, 199), np.nan) for st in R.STATS},
    }
    return {"comp": {st: comp for st in R.STATS} | {}, "pic": pic}


def test_build_bundle_scores_arms_on_a_keys_in_a_order_with_integer_grid() -> None:
    rng = np.random.default_rng(0)
    arms = {
        "A": _arm(1, rng.permutation(40), 0, 0.0),
        "B": _arm(1, rng.permutation(43), 3, 0.0),
        "C": _arm(1, rng.permutation(43), 3, 1.0),
    }
    games = {g: "2023-11-01" for g in np.unique(arms["A"]["comp"]["pts"]["row_gid"])}
    f = _frame_for_bundle(arms["A"]["comp"]["pts"])
    b = R.build_bundle(arms, f, games)
    d = b["stats"]["pts"]
    assert len(d["gid"]) == 40 and d["gid"].tolist() == arms["A"]["comp"]["pts"]["row_gid"].tolist()
    assert np.allclose(
        d["score"]["A"], d["score"]["B"]
    )  # same inputs, different row order and 3 extra keys: identical scores per key
    assert (d["score"]["C"] != d["score"]["A"]).any()  # a shifted centre moves the score
    assert set(b["pi"]) == set(R.ARMS) and len(b["pi"]["C"]["p"]) == 40


def _frame_for_bundle(comp: dict[str, Any]) -> pl.DataFrame:
    n = len(comp["row_gid"])
    return pl.DataFrame(
        {
            "game_id": comp["row_gid"], "player_id": comp["row_pid"], "n_prior": np.full(n, 30.0), "starter10": np.full(n, 0.6),
            "n_out_rot": np.zeros(n, dtype=int), "has_report": np.ones(n, dtype=int), "team_game_no": np.full(n, 30),
            "m10_pts": np.full(n, 8.0), "sd10_pts": np.full(n, 3.0), "pts": np.full(n, 8),
            "m10_reb": np.full(n, 8.0), "sd10_reb": np.full(n, 3.0), "reb": np.full(n, 8),
            "m10_ast": np.full(n, 8.0), "sd10_ast": np.full(n, 3.0), "ast": np.full(n, 8),
            "m10_fg3m": np.full(n, 8.0), "sd10_fg3m": np.full(n, 3.0), "fg3m": np.full(n, 8),
        }
    )  # fmt: skip


def test_build_bundle_refuses_when_an_arm_misses_an_evaluation_key() -> None:
    rng = np.random.default_rng(0)
    a = _arm(1, rng.permutation(40), 0, 0.0)
    short = _arm(1, np.arange(40)[:39], 0, 0.0)
    comp = short["comp"]["pts"]
    for k in (
        "row_gid",
        "row_pid",
        "row_y",
        "row_season",
        "row_blk",
        "row_c",
        "row_s",
        "row_m",
        "row_min10",
        "row_minutes",
    ):
        comp[k] = comp[k][:39]
    short["pic"] = {
        **short["pic"],
        "row_gid": short["pic"]["row_gid"][:39],
        "row_pid": short["pic"]["row_pid"][:39],
        "row_pi": short["pic"]["row_pi"][:39],
    }
    short["comp"] = {st: comp for st in R.STATS}
    games = {g: "2023-11-01" for g in np.unique(a["comp"]["pts"]["row_gid"])}
    with pytest.raises(RuntimeError, match="missing"):
        R.build_bundle(
            {"A": a, "B": short, "C": copy.deepcopy(short)},
            _frame_for_bundle(a["comp"]["pts"]),
            games,
        )


def test_pinned_versions_are_the_installed_ones() -> None:
    assert R.require_versions() == R.PINNED_VERSIONS


def test_loaders_are_capped_at_the_last_selectable_season() -> None:
    assert MAX_SEASON == 2024
    src = (Path(__file__).resolve().parents[3] / "research/eval/f1_warm_window_gate.py").read_text()
    assert "flagged_from_availability(avail, games, cfg.report, max_season=MAX_SEASON)" in src
    assert 'if int(games["season"].max()) > MAX_SEASON' in src


def test_nan_max_abs() -> None:
    a = np.array([np.nan, 1.0])
    assert R._nan_max_abs(a, a.copy()) == 0.0
    assert R._nan_max_abs(a, np.array([0.0, 1.0])) == float("inf")
    assert R._nan_max_abs(np.array([1.0, 2.0]), np.array([1.0, 2.5])) == 0.5
