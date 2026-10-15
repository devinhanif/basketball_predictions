# ruff: noqa: E501
"""RETURNING_TEAMMATE_2 harness: freeze/pin refusals, the words, the bootstrap, the guards' non-inertness."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from research.eval import returning_teammate_2_eval as E
from research.eval import returning_teammate_2_features as F
from research.eval import returning_teammate_2_rule as R


def _doc(tmp: Path) -> tuple[Path, str]:
    body = "rule text\n"
    p = tmp / "d.md"
    p.write_text(f"{body}=== RESULTS BELOW ===\nx\n")
    return p, hashlib.sha256(body.encode()).hexdigest()


def test_require_frozen_needs_matching_hash_and_the_prereg_commit(tmp_path: Path) -> None:
    p, sha = _doc(tmp_path)

    def good(*a: str) -> str:
        return f"abc\tprereg(RETURNING_TEAMMATE_2): {sha}\n" if a[0] == "log" else p.read_text()

    def other(*a: str) -> str:
        return f"abc\tprereg(RETURNING_TEAMMATE): {sha}\n" if a[0] == "log" else p.read_text()

    assert R.require_frozen(sha, p, good) == sha
    with pytest.raises(RuntimeError, match="no commit"):
        R.require_frozen(sha, p, other)  # v1's subject must not freeze v2
    with pytest.raises(RuntimeError, match="not the frozen rule"):
        R.require_frozen("b" * 64, p, good)
    with pytest.raises(RuntimeError, match="required"):
        R.require_frozen(None, p, good)


def test_run_refuses_without_a_frozen_rule_before_loading_anything() -> None:
    with pytest.raises(RuntimeError):
        E.run("")


def test_harness_pin_refuses_when_not_pinned_or_edited(tmp_path: Path) -> None:
    sha = "a" * 64

    def log_unpinned(*a: str) -> str:
        return f"fff\tprereg(RETURNING_TEAMMATE_2): {sha}\n" if a[0] == "log" else "x"

    with pytest.raises(RuntimeError):
        R.require_harness_pinned(sha, log_unpinned, tmp_path, lambda *a: True)


def test_snapshot_pin_refuses_a_changed_definition() -> None:
    with pytest.raises(RuntimeError, match="snapshot source sha"):
        E.preflight(
            {"games": pl.DataFrame(), "status": pl.DataFrame()},
            pl.DataFrame(),
            {"SNAPSHOT_SOURCE_SHA256": "0" * 64},
        )


def test_bootstrap_is_game_clustered_and_covers_the_mean() -> None:
    rng = np.random.default_rng(0)
    gid = np.repeat(np.arange(300), 6)
    shared = np.repeat(
        rng.normal(0, 1, 300), 6
    )  # a game-level shock: row-level CIs would be too narrow
    v = shared + rng.normal(0, 0.1, len(gid)) - 0.5
    bt = E.Boot(gid, b=2000)
    ci = bt.ci(v, np.ones(len(v), bool))
    naive = 1.96 * v.std(ddof=1) / np.sqrt(len(v))
    assert ci["lo"] < -0.5 < ci["hi"]
    assert (
        ci["hi"] - ci["lo"]
    ) / 2 > 1.8 * naive  # clustered half-width is wider than the row-level one
    assert E.bh_adjust(np.array([0.01, 0.04, 0.03])).tolist() == pytest.approx([0.03, 0.04, 0.04])


def _sc(
    point: float,
    hi: float,
    all_hi: float = 0.0,
    pl_pt: float = -0.01,
    slice_pt: float = 0.0,
    noise_pt: float = 0.0,
) -> dict[str, dict]:
    def blk() -> dict:
        return {
            "RT_minus_A0_affected": {"point": point, "lo": point - 0.01, "hi": hi, "n": 8000},
            "RT_minus_A0_all": {"point": point / 3, "lo": -0.01, "hi": all_hi, "n": 27000},
            "RT_minus_PL_affected": {"point": pl_pt, "lo": -0.02, "hi": 0.0, "n": 8000},
            "NOISE_minus_CTRL_affected": {
                "point": noise_pt,
                "lo": -0.01,
                "hi": -0.001 if noise_pt < 0 else 0.01,
                "n": 8000,
            },
            "slices": {
                "star": {"point": slice_pt, "lo": -0.02, "hi": 0.02, "n": 1500},
                "small": {"n": 50, "reported": False},
            },
        }

    return {"pts": {"2023": blk(), "2024": blk()}, "reb": {"2023": blk(), "2024": blk()}}


def test_decide_pass_and_each_way_to_fail() -> None:
    assert E.decide(_sc(-0.008, -0.001))["word"] == "PASS_STAGE1"
    assert E.decide(_sc(-0.008, 0.001))["word"] == "INCONCLUSIVE"  # CI upper not below 0
    assert (
        E.decide(_sc(-0.003, -0.0005))["word"] == "INCONCLUSIVE"
    )  # CI fine, floor missed, but not closed
    assert E.decide(_sc(0.001, 0.01))["word"] == "CLOSED"  # point above -0.002
    assert E.decide(_sc(-0.008, -0.001, all_hi=0.003))["word"] == "GUARD_FAIL"  # harms all rows
    assert (
        E.decide(_sc(-0.008, -0.001, pl_pt=-0.001))["word"] == "GUARD_FAIL"
    )  # does not beat the placebo
    assert (
        E.decide(_sc(-0.008, -0.001, slice_pt=0.02))["word"] == "GUARD_FAIL"
    )  # a reported slice is worse by > 0.01
    assert (
        E.decide(_sc(-0.008, -0.001, noise_pt=-0.003))["word"] == "GUARD_FAIL"
    )  # noise columns win: not information
    d = E.decide(_sc(-0.008, -0.001, slice_pt=0.02))
    assert not d["guards"]["G5_slices_not_worse_2024"] and d["guards"]["G1_no_harm_pts_2024"]


def test_score_arms_end_to_end_on_planted_shift_and_slices_below_min_n_are_unreported() -> None:
    rng = np.random.default_rng(1)
    n = 6000
    gid = np.repeat(np.arange(n // 5), 5).astype(str)
    season = np.where(np.arange(n) < n // 2, 2023, 2024)
    y = rng.integers(8, 12, n).astype(float)
    q0 = np.tile(np.arange(5, 15), (n, 20))[:, :199].astype(np.int16)
    q_good = q0 + 0  # same grid: zero delta, then a better arm shifts toward y
    rt = {k: np.zeros(n) for k in (*F.FEATURES_ALL, "out_run")}
    rt["ret_min"][::3] = 30.0
    rt["ret_min_star"][::6] = 30.0
    rt["ret_missed"][::3] = 4.0
    sc = E.score_arms(
        {"pts": {"A0": q0, "RT": q_good, "PL": q0, "NOISE": q0, "CTRL": q0}},
        {"pts": y},
        gid,
        season,
        rt,
    )
    b = sc["pts"]["2023"]
    assert b["RT_minus_A0_affected"]["point"] == 0.0 and b["n_affected"] == 1000
    assert b["slices"]["missed_10+"]["reported"] is False  # n < 300 -> not reported
    assert "p_bh_m3" not in b


def test_noise_frame_permutes_columns_jointly_within_season_and_keeps_marginals() -> None:
    fr = pl.DataFrame(
        {"season": [2023] * 6 + [2024] * 6, "a": list(range(12)), "b": [10 * i for i in range(12)]}
    )
    nz = E.noise_frame(fr, ("a", "b"), 7)
    assert sorted(nz["nz_a"].to_list()[:6]) == list(range(6)) and sorted(
        nz["nz_a"].to_list()[6:]
    ) == list(range(6, 12))
    assert all(
        b == 10 * a for a, b in zip(nz["nz_a"].to_list(), nz["nz_b"].to_list(), strict=True)
    )  # joint structure preserved
    assert nz["nz_a"].to_list() != fr["a"].to_list()


def test_checkpoint_older_than_freeze_is_refused(tmp_path: Path) -> None:
    f = tmp_path / "x.npz"
    assert not E.check_checkpoint(f, 1.0)
    f.write_bytes(b"x")
    assert E.check_checkpoint(f, 0.0)
    with pytest.raises(RuntimeError, match="predates the freeze"):
        E.check_checkpoint(f, f.stat().st_mtime + 100)


def test_effect_model_zero_outside_rotation_and_without_returners() -> None:
    rt = {"ret_min": np.array([0.0, 33.0, 33.0]), "ret_min_star": np.array([0.0, 33.0, 0.0])}
    rot = np.array([True, True, False])
    shift, p = E.effect_model(rt, rot, 0.8, 0.6)
    assert (
        shift[0] == 0.0
        and shift[2] == 0.0
        and shift[1] == pytest.approx(-0.81 * 0.8)
        and p[1] == pytest.approx(0.8)
    )


def test_pinned_counts_and_constants_are_the_declared_ones() -> None:
    assert E.SEASONS == (2023, 2024) and E.MAX_SEASON == 2024 and E.FLOOR == -0.005
    assert F.FEATURES_ALL[-1] == "ret_known" and len(F.FEATURES) == 7
    assert R.ID == "RETURNING_TEAMMATE_2" and "returning_teammate_2_features.py" in " ".join(
        R.HARNESS_FILES
    )


def test_coverage_guard_fires_when_rt_coverage_drifts_and_is_reported_by_score_arms() -> None:
    sc = _sc(-0.008, -0.001)
    for s in ("2023", "2024"):
        sc["pts"][s]["coverage_A0_RT"] = {"0.1": [0.10, 0.10], "0.9": [0.90, 0.90]}
    assert E.decide(sc)["word"] == "PASS_STAGE1"
    sc["pts"]["2024"]["coverage_A0_RT"]["0.9"] = [0.90, 0.93]
    d = E.decide(sc)
    assert d["word"] == "GUARD_FAIL" and not d["guards"]["G6_coverage_2024"]
    rng = np.random.default_rng(2)
    n = 400
    qi = np.tile(np.arange(199), (n, 1)).astype(np.int16)
    y = rng.integers(0, 199, n).astype(float)
    rt = {k: np.zeros(n) for k in (*F.FEATURES_ALL, "out_run")}
    out = E.score_arms(
        {"pts": {"A0": qi, "RT": qi}},
        {"pts": y},
        np.arange(n).astype(str),
        np.where(np.arange(n) < 200, 2023, 2024),
        rt,
    )
    cov = out["pts"]["2023"]["coverage_A0_RT"]
    assert set(cov) == {"0.1", "0.2", "0.8", "0.9"} and all(v[0] == v[1] for v in cov.values())
