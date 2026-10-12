# ruff: noqa: E501
"""WORKLOAD harness: freeze refusal, lock ownership, arm column sets, placebo, pass-rule logic, P(play) walk."""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from nba.props.context_residual import ContextResidualConfig
from research.eval import workload_eval as E
from research.workload import asof as W


def _doc(tmp_path: Path, body: str = "rule text\n", tail: str = "(not run)\n") -> Path:
    p = tmp_path / "doc.md"
    p.write_text(f"{body}=== RESULTS BELOW ===\n{tail}")
    return p


def test_frozen_sha_ignores_results_and_refuses_edits(tmp_path: Path) -> None:
    p = _doc(tmp_path)
    want = hashlib.sha256(b"rule text\n").hexdigest()
    assert E.frozen_sha(p) == want
    assert E.require_frozen(None, p, want) == want
    p.write_text(p.read_text().replace("(not run)", "results added"))
    assert E.require_frozen(None, p, want) == want  # results below the line do not move the hash
    p.write_text(p.read_text().replace("rule text", "rule text edited"))
    with pytest.raises(RuntimeError, match="not the frozen rule"):
        E.require_frozen(None, p, want)
    with pytest.raises(RuntimeError, match="--sha"):
        E.require_frozen("0" * 64, _doc(tmp_path), want)


def test_the_committed_rule_matches_the_frozen_constant() -> None:
    assert E.frozen_sha(E.DOC) == E.FROZEN_FULL


def test_lock_creates_pid_file_and_removes_only_its_own(tmp_path: Path) -> None:
    path = tmp_path / "heavy.lock"
    with E.WorkloadLock(path) as lk:
        assert path.is_dir() and (path / "pid").read_text().strip().isdigit()
        assert lk.owned
    assert not path.exists()
    # a lock held by someone else is never taken or removed
    path.mkdir()
    (path / "pid").write_text("1")
    waits: list[float] = []
    with (
        pytest.raises(TimeoutError),
        E.WorkloadLock(path, retry_s=5.0, sleep=waits.append, max_wait_s=10.0),
    ):
        pass
    assert path.exists() and waits == [5.0, 5.0]
    # even when we think we own it, a changed pid file means it is not ours to remove
    other = tmp_path / "other.lock"
    lk2 = E.WorkloadLock(other)
    with lk2:
        (other / "pid").write_text("999999999")
    assert other.exists()


def test_arm_column_sets_are_the_families() -> None:
    allw = set(E.arm_wl("B"))
    assert len(allw) == 27 and allw == set(W.MODEL_COLS)
    assert set(E.arm_wl("KO_LOAD")) == allw - set(E.LOAD_COLS)
    assert set(E.arm_wl("KO_SCHEDULE")) == allw - set(E.SCHED_COLS)
    assert set(E.arm_wl("KO_TRAVEL")) == allw - set(E.TRAVEL_COLS)
    assert set(E.arm_wl("NT")) == allw - set(E.TRACK_COLS) and len(E.TRACK_COLS) == 7
    assert E.arm_wl("base") == [] and len(E.arm_wl("PL")) == 27
    assert all(c.startswith(E.PL_PREFIX) for c in E.arm_wl("PL"))
    for fam, cols in (
        ("AL_LOAD", E.LOAD_COLS),
        ("AL_SCHEDULE", E.SCHED_COLS),
        ("AL_TRAVEL", E.TRAVEL_COLS),
    ):
        assert set(E.arm_wl(fam)) == set(cols)
    assert not (set(E.LOAD_COLS) & set(E.SCHED_COLS)) and not (
        set(E.SCHED_COLS) & set(E.TRAVEL_COLS)
    )
    # production already holds rest/b2b: never a model column
    assert "wl_rest_days" not in allw and "wl_b2b_second" not in allw


def _toy_frame() -> pl.DataFrame:
    rng = np.random.default_rng(1)
    n = 600
    d = {
        "game_id": [f"g{i // 4}" for i in range(n)],
        "player_id": [i % 4 for i in range(n)],
        "team_id": [1 + (i % 4) // 2 for i in range(n)],
        "season": [2023] * n,
    }
    for c in W.MODEL_COLS:
        d[c] = rng.normal(size=n).tolist()
    return pl.DataFrame(d)


def test_placebo_keeps_marginals_and_breaks_the_link() -> None:
    fr = _toy_frame()
    p = E.make_placebo(fr)
    for c in E.arm_wl("B"):
        pc = E.PL_PREFIX + c
        assert pc in p.columns
        assert (
            sorted(p[pc].to_list()) == sorted(fr[c].to_list()) or c in E.SCHED_COLS + E.TRAVEL_COLS
        )
    assert p["pl_wl_min_3d"].to_list() != fr["wl_min_3d"].to_list()
    assert p.height == fr.height
    # LOAD placebo stays within the player-season
    for pid in range(4):
        sub = fr.filter(pl.col("player_id") == pid)["wl_min_3d"].sort().to_list()
        assert p.filter(pl.col("player_id") == pid)["pl_wl_min_3d"].sort().to_list() == sub


def test_ci_and_carry_logic() -> None:
    gid = np.repeat(np.arange(200), 3)
    neg = np.full(600, -0.01)
    ci = E._ci(neg, gid)
    assert ci["hi"] < 0 and abs(ci["point"] + 0.01) < 1e-12
    rng = np.random.default_rng(0)
    z = E._ci(rng.normal(size=600), gid)
    assert z["lo"] < 0 < z["hi"]
    assert E._ci(neg, gid) == E._ci(neg, gid)  # seeded
    assert E.carries(-0.010, -0.001, None)  # dropping the family leaves 10% of the gain
    assert not E.carries(-0.010, -0.008, None)
    assert E.carries(-0.010, -0.008, -0.009)  # alone keeps 90%
    assert not E.carries(+0.002, 0.0, 0.0)


def _res(
    delta23: float = -0.006, delta24: float = -0.006, p_bh: float = 0.01, b_pl: float = -0.004
) -> dict:
    rows = []
    for s, d in ((2023, delta23), (2024, delta24)):
        rows.append(
            {"cell": "pts", "season": s, "arm": "base", "metric": 3.14, "cov80": 0.80, "bias": 0.01}
        )
        rows.append(
            {
                "cell": "pts",
                "season": s,
                "arm": "B",
                "metric": 3.14 + d,
                "delta": d,
                "ci_lo": d - 0.002,
                "ci_hi": d + 0.002,
                "p": 0.001,
                "p_bh": p_bh,
                "cov80": 0.80,
                "bias": 0.01,
                "n_rows": 1000,
                "n_games": 100,
            }
        )
        rows.append({"cell": "pts", "season": s, "arm": "B_minus_PL", "delta": b_pl})
        for fam in ("LOAD", "SCHEDULE", "TRAVEL"):
            rows.append({"cell": "pts", "season": s, "arm": f"KO_{fam}", "delta": d * 0.6})
        rows.append({"cell": "pts", "season": s, "arm": "B_minus_NT", "delta": -0.004})
    sl = [
        {
            "cell": "pts",
            "season": 2024,
            "slice": "starter",
            "n": 500,
            "delta": -0.005,
            "ci_lo": -0.01,
            "ci_hi": 0.0,
        },
        {
            "cell": "pts",
            "season": 2024,
            "slice": "cold5_9",
            "n": 400,
            "delta": -0.001,
            "ci_lo": -0.004,
            "ci_hi": 0.002,
        },
    ]
    return {"rows": rows, "slices": sl}


def test_evaluate_passes_and_each_rule_can_fail() -> None:
    ok = E.evaluate(_res(), True)["cells"]["pts"]
    assert ok["pass_before_attack"] and ok["tracking_claim"] is True
    assert not E.evaluate(_res(delta23=-0.002), True)["cells"]["pts"]["select_2023"]
    assert not E.evaluate(_res(delta24=-0.004), True)["cells"]["pts"]["rule1_2024"]
    assert not E.evaluate(_res(p_bh=0.2), True)["cells"]["pts"]["rule1_2024"]
    assert not E.evaluate(_res(b_pl=-0.001), True)["cells"]["pts"]["pass_before_attack"]
    assert not E.evaluate(_res(), False)["cells"]["pts"]["pass_before_attack"]
    bad = _res()
    bad["slices"].append(
        {
            "cell": "pts",
            "season": 2024,
            "slice": "bench",
            "n": 900,
            "delta": 0.02,
            "ci_lo": 0.0,
            "ci_hi": 0.04,
        }
    )
    v = E.evaluate(bad, True)["cells"]["pts"]
    assert v["guards"]["no_slice_worse"] is False and not v["pass_before_attack"]
    small = _res()
    small["slices"].append(
        {"cell": "pts", "season": 2024, "slice": "bench", "n": 100, "delta": 0.5}
    )
    assert E.evaluate(small, True)["cells"]["pts"]["guards"][
        "no_slice_worse"
    ]  # n < 300 is not guarded


def test_no_selection_closes_and_floors_are_the_frozen_ones() -> None:
    assert E.evaluate(_res(delta23=-0.001), True)["close"] is True
    assert E.FLOOR["P"] == -0.005 and E.FLOOR["M"] == -0.10 and E.FLOOR["pts"] == -0.005
    assert E.PLACEBO_MARGIN["M"] == -0.05 and E.PLACEBO_MARGIN["P"] == -0.003
    assert E.SLICE_TOL["M"] == 0.05 and E.N_BOOT == 2000


def test_merge_attack_requires_shuffled_and_no_carrier() -> None:
    res = {
        "verdict": E.evaluate(_res(), True),
        "attack": {"cells": {"pts": {"shuffled_ok": True, "one_family_carries": False}}},
    }
    E.merge_attack(res)
    assert res["verdict"]["cells"]["pts"]["passed"] is True
    res["attack"]["cells"]["pts"]["one_family_carries"] = True
    E.merge_attack(res)
    assert res["verdict"]["cells"]["pts"]["passed"] is False
    res["attack"]["cells"] = {}
    E.merge_attack(res)
    assert res["verdict"]["cells"]["pts"]["passed"] is None


def _p_frame() -> pl.DataFrame:
    rng = np.random.default_rng(0)
    rows = []
    d0 = dt.date(2022, 11, 1)
    for k in range(0, 900, 3):
        day = d0 + dt.timedelta(days=k // 3 * 3)
        for pid in range(10):
            x = float(rng.normal())
            rows.append(
                {
                    "game_id": f"g{k}",
                    "player_id": pid,
                    "season": 2023 if day >= dt.date(2023, 10, 1) else 2022,
                    "game_date": day,
                    "x": x,
                    "z": float(rng.normal()),
                    "played": int(rng.random() < 1 / (1 + np.exp(-2 * x))),
                }
            )
    return pl.DataFrame(rows).with_columns(pl.col("game_date").cast(pl.Date))


def test_p_walk_checkpoints_and_shuffled_labels(tmp_path: Path) -> None:
    fr = _p_frame()
    assert fr["season"].max() == 2023
    cfg = ContextResidualConfig()
    arms = {"base": ["x"], "B": ["x", "z"]}
    r1 = E.p_walk(fr, arms, cfg, ckpt=tmp_path, min_train=200, n_estimators=10)
    assert set(r1) >= {"gid", "pid", "season", "y", "p_base", "p_B"} and set(
        np.unique(r1["season"])
    ) == {2023}
    r2 = E.p_walk(fr, arms, cfg, ckpt=tmp_path, min_train=200, n_estimators=10)  # from checkpoints
    assert np.array_equal(r1["p_B"], r2["p_B"])
    r3 = E.p_walk(fr, arms, cfg, shuffle=frozenset({"B"}), min_train=200, n_estimators=10)
    assert np.array_equal(r1["p_base"], r3["p_base"])  # unshuffled arm identical
    assert not np.allclose(r1["p_B"], r3["p_B"])
    # the shuffled arm has lost the x -> played link: its log loss is worse than the real one
    assert E._ll(r3["p_B"], r3["y"]).mean() > E._ll(r1["p_B"], r1["y"]).mean()


def test_p_walk_refuses_season_2025() -> None:
    fr = _p_frame().with_columns(pl.lit(2025).alias("season"))
    with pytest.raises(ValueError, match="holdout"):
        E.assert_seasons(fr)


def test_slice_masks_with_status() -> None:
    meta = pl.DataFrame(
        {
            "game_id": ["a"] * 5,
            "player_id": list(range(5)),
            "team_id": [1] * 5,
            "starter10": [1.0, 1.0, 0.0, 0.0, 0.0],
            "min10": [36.0, 30.0, 25.0, 12.0, 6.0],
            "n_prior": [30, 30, 7, 30, 3],
            "team_game_no": [10] * 5,
            "wl_b2b_second": [1] * 5,
            "wl_three_in_four": [0] * 5,
            "wl_road_trip4plus": [0] * 5,
            "wl_tz_cross_hr": [2.0] * 5,
            "wl_miles_from_prev": [1600.0] * 5,
            "wl_early_tip": [0] * 5,
            "wl_speed_decline": [-0.3, 0.0, 0.1, 0.2, 0.3],
        }
    )
    st = pl.DataFrame({"game_id": ["a"], "team_id": [1], "status": ["contending"], "late": [False]})
    m = E.slice_masks(meta, st)
    assert m["star"].sum() == 3 and m["non_star"].sum() == 2
    assert m["starter"].sum() == 2 and m["bench"].sum() == 3
    assert m["cold5_9"].sum() == 1 and m["first15"].all() and m["contending"].all()
    assert m["second_night"].all() and m["eastbound_ge2tz"].all() and not m["early_tip"].any()
    assert m["speed_decline_bottom_decile"].sum() == 1


def test_audit_nulls_flags_a_minutes_linked_null_pattern() -> None:
    rng = np.random.default_rng(0)
    n = 4000
    minutes = rng.uniform(0, 40, n)
    clean = rng.normal(size=n)
    leaky = np.where(minutes < 5, np.nan, rng.normal(size=n))
    ints = (rng.random(n) > 0.5).astype(int)
    df = pl.DataFrame({"minutes": minutes, "clean": clean, "leaky": leaky, "ints": ints})
    ok = E.audit_nulls(df.select("minutes", "clean", "ints"), ["clean", "ints"], E.minutes_bucket())
    assert ok["ok"] and ok["max_gap_pp"] < 0.5
    bad = E.audit_nulls(df, ["clean", "leaky"], E.minutes_bucket())
    assert not bad["ok"] and bad["worst_col"] == "leaky"


def test_report_prints_every_ambiguity() -> None:
    assert len(E.AMBIGUITIES) >= 10 and all({"text", "reading"} <= set(a) for a in E.AMBIGUITIES)
    res = {
        "rows": _res()["rows"],
        "slices": [],
        "ambiguities": E.AMBIGUITIES,
        "verdict": E.evaluate(_res(), True),
    }
    md = E.render(res)
    assert md.count("Reading:") == len(E.AMBIGUITIES) and "frozen sha256 261fabce" in md


def test_main_refuses_a_wrong_sha() -> None:
    with pytest.raises(RuntimeError, match="--sha"):
        E.main(["report", "--sha", "f" * 64])


def test_check_m2_repro_uses_the_key_intersection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mv2, out = tmp_path / "mv2", tmp_path / "out"
    mv2.mkdir()
    gids = np.array([f"g{i}" for i in range(300)])
    np.savez(
        mv2 / "minutes_scores.npz",
        row_gid=gids[:290],
        row_pid=np.arange(290),
        row_season=np.array([2023] * 150 + [2024] * 140),
        M2_crps=np.linspace(3, 4, 290),
    )
    monkeypatch.setattr(E, "MV2_DIR", mv2)
    monkeypatch.setattr(E, "OUT_DIR", out)
    res = {
        "gid": gids,
        "pid": np.arange(300),
        "season": np.array([2023] * 150 + [2024] * 150),
        "base_crps": np.concatenate([np.linspace(3, 4, 290), np.full(10, 9.0)]),
    }
    rec = E.check_m2_repro(res)  # 10 extra current rows (the clean-up) do not matter
    assert (
        rec["2024"]["n_common"] == 140
        and rec["2024"]["n_now"] == 150
        and rec["2024"]["n_stored"] == 140
    )
    res["base_crps"] = res["base_crps"] + 0.1
    with pytest.raises(RuntimeError, match="does not reproduce"):
        E.check_m2_repro(res)


def test_props_arm_path_end_to_end_on_a_toy_frame() -> None:
    """collect_components + mixture_qi with a toy production-shaped frame (the real fit is the maintainer's run)."""
    rng = np.random.default_rng(0)
    rows = []
    d0 = dt.date(2022, 11, 1)
    for k in range(400):
        day = d0 + dt.timedelta(days=k)
        for pid in range(8):
            m = 12.0 + pid
            rows.append(
                {
                    "game_id": f"g{k}", "player_id": pid, "team_id": 1, "season": 2023 if day >= dt.date(2023, 10, 1) else 2022,
                    "game_date": day, "n_prior": 30, "m10_pts": m, "sd10_pts": 4.0, "pts": int(max(0, rng.normal(m, 4))),
                    "minutes": 25.0 + rng.normal(), "min10": 25.0, "min40": 25.0, "starter10": 1.0, "starter_act": 1.0,
                    "abs_margin": 8.0, "final_margin": 8.0, "b2b": 0, "gap_days": 1.0, "since_flag": 0.0, "x": float(rng.normal()),
                }
            )  # fmt: skip
    fr = pl.DataFrame(rows).with_columns(pl.col("game_date").cast(pl.Date))
    cfg = ContextResidualConfig(n_estimators=20)
    comp = E.collect_components(fr, "pts", cfg, ["x", "m10_pts"])
    n = len(comp["row_y"])
    assert n > 0 and set(np.unique(comp["row_season"])) == {2023}
    nblk = int(comp["row_blk"].max()) + 1
    pic = {
        "row_gid": comp["row_gid"],
        "row_pid": comp["row_pid"],
        "row_pi": np.zeros(n),
        "wq_pts": np.full((nblk, 199), np.nan),
    }
    qi = E.mixture_qi(comp, pic, "pts")
    assert (
        qi.shape == (n, 199)
        and qi.dtype == np.int16
        and (np.diff(qi.astype(int), axis=1) >= 0).all()
    )
    shuf = E.collect_components(fr, "pts", cfg, ["x", "m10_pts"], shuffle=True)
    assert not np.allclose(shuf["row_c"], comp["row_c"])
    bad = dict(pic, row_pid=comp["row_pid"][::-1].copy())
    with pytest.raises(RuntimeError, match="not aligned"):
        E.mixture_qi(comp, bad, "pts")


def test_minutes_walk_on_a_toy_frame(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    rows = []
    d0 = dt.date(2022, 11, 1)
    for k in range(400):
        day = d0 + dt.timedelta(days=k)
        for pid in range(8):
            rows.append(
                {
                    "game_id": f"g{k}", "player_id": pid, "team_id": 1, "season": 2023 if day >= dt.date(2023, 10, 1) else 2022,
                    "game_date": day, "min10": 24.0, "minutes": float(np.clip(rng.normal(24, 6), 2, 46)), "x": float(rng.normal()),
                    "p_mu": 24.0, "p_sigma": 6.0,
                }
            )  # fmt: skip
    fr = pl.DataFrame(rows).with_columns(pl.col("game_date").cast(pl.Date))
    cfg = ContextResidualConfig(n_estimators=15)
    r = E.m_walk(fr, {"base": ["x"], "B": ["x", "min10"]}, cfg, with_baselines=False, ckpt=tmp_path)
    assert {"base_crps", "B_crps", "base_pit", "B_mean", "gid", "pid", "season", "y"} <= set(r)
    assert len(r["base_crps"]) == len(r["y"]) > 0 and (r["base_crps"] >= 0).all()
    r2 = E.m_walk(
        fr, {"base": ["x"], "B": ["x", "min10"]}, cfg, with_baselines=False, ckpt=tmp_path
    )
    assert np.array_equal(r["B_crps"], r2["B_crps"])  # checkpoint resume
    s = E.m_walk(fr, {"B": ["x", "min10"]}, cfg, shuffle=frozenset({"B"}), with_baselines=False)
    assert not np.allclose(s["B_crps"], r["B_crps"])
