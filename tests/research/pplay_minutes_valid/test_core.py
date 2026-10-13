# mypy: disable-error-code="no-untyped-def,arg-type,index,assignment,operator,no-any-return,call-overload"
# ruff: noqa: E501
"""Pure pieces of PPLAY_MINUTES_VALID on synthetic arrays (every guard can fail; every guard can pass)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from nba.props.context_residual import ContextResidualConfig
from research.eval import pplay_minutes_valid_core as C
from research.eval import workload_eval as WE

TINY = ContextResidualConfig(
    n_estimators=40, learning_rate=0.2, min_child_samples=10, min_cal_rows=50, num_leaves=5
)


# ---------------------------------------------------------------- bootstrap
def test_cluster_boot_matches_the_workload_ci_and_the_p_floor() -> None:
    rng = np.random.default_rng(1)
    gid = np.repeat(np.arange(300), 4)
    d = rng.normal(-0.02, 0.5, len(gid)) + np.repeat(rng.normal(0, 0.2, 300), 4)
    mine = C.cluster_boot(d, gid)
    ref = WE._ci(d, gid)
    assert mine["point"] == pytest.approx(ref["point"], abs=1e-15)
    assert mine["lo"] == pytest.approx(ref["lo"], abs=1e-12) and mine["hi"] == pytest.approx(
        ref["hi"], abs=1e-12
    )
    assert mine["n"] == len(d) and mine["n_games"] == 300
    # a huge effect: the p-value floors at 1/2000, not at zero (rule: floor 1/2000)
    big = C.cluster_boot(
        np.full(400, -1.0) + np.random.default_rng(0).normal(0, 0.01, 400),
        np.repeat(np.arange(100), 4),
    )
    assert big["p"] == pytest.approx(1 / 2000)
    # two-sided p = 2 x min(share >= 0, share <= 0): a centred effect has p near 1, a shifted one p near 0
    assert C.cluster_boot(d - d.mean(), gid)["p"] > 0.9
    assert C.cluster_boot(d - d.mean() - 0.5, gid)["p"] < 0.01


def test_cluster_boot_is_clustered_not_row_level() -> None:
    gid = np.repeat(np.arange(50), 20)
    shared = np.repeat(
        np.random.default_rng(0).normal(0, 1, 50), 20
    )  # perfectly correlated within a game
    row = C.cluster_boot(shared, gid)
    iid = C.cluster_boot(shared, np.arange(len(shared)))
    assert (row["hi"] - row["lo"]) > 2.5 * (iid["hi"] - iid["lo"])


# ---------------------------------------------------------------- Platt
def test_platt_recovers_a_known_map_and_clips() -> None:
    rng = np.random.default_rng(0)
    p0 = rng.uniform(0.05, 0.95, 40000)
    a, b = -0.4, 0.6
    p_true = 1 / (1 + np.exp(-(a + b * np.log(p0 / (1 - p0)))))
    y = (rng.random(len(p0)) < p_true).astype(int)
    fa, fb = C.fit_platt(p0, y)
    assert fa == pytest.approx(a, abs=0.05) and fb == pytest.approx(b, abs=0.05)
    out = C.apply_platt(np.array([0.0, 1.0, 0.5]), fa, fb)  # clip(1e-3): finite at the extremes
    assert np.isfinite(out).all() and out[2] == pytest.approx(1 / (1 + np.exp(-fa)))
    with pytest.raises(ValueError):
        C.fit_platt(p0[:10], np.ones(10))


# ---------------------------------------------------------------- the walk
def _toy_frame(seed: int = 0, n_days: int = 150, per_day: int = 40) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    start = dt.date(2019, 11, 1)
    for d in range(n_days * 3):
        day = start + dt.timedelta(days=d)
        season = (
            2019 + (day.year * 12 + day.month - (2019 * 12 + 11)) // 12
            if False
            else (2019 if d < 200 else 2020 if d < 400 else 2023)
        )
        for k in range(per_day):
            x = rng.normal()
            p0 = float(np.clip(0.8 + 0.05 * rng.normal(), 0.05, 0.99))
            y = float(rng.random() < 1 / (1 + np.exp(-(0.5 + 0.9 * x))))
            rows.append(
                (
                    f"g{d:04d}{k // 10}",
                    1000 + k,
                    day,
                    season,
                    x,
                    p0,
                    y,
                    1.0 if season == 2020 else 0.0,
                )
            )
    df = pl.DataFrame(
        rows,
        schema=["game_id", "player_id", "game_date", "season", "x", "p0", "played", "era_limited"],
        orient="row",
    )
    return df.with_columns(
        (pl.col("era_limited") > 0).alias("era_any"),
        pl.col("played").alias("min_real"),
        pl.lit(0.0).alias("era_bubble"),
    ).sort(["game_date", "game_id", "player_id"])


def test_walk_never_trains_on_the_scored_rows_and_the_map_excludes_era_games() -> None:
    fr = _toy_frame()
    res, info = C.walk_p(fr, {"R": ["x"]}, TINY, (2023,), min_train=500)
    assert set(np.unique(res["season"])) == {2023}
    starts = [dt.date.fromisoformat(s) for _, s, *_ in info["platt"]]
    assert min(starts) > dt.date(2019, 11, 1)
    # recompute the first block's map by hand: rows strictly before the block start, era-flagged games excluded
    bi, s0, a, b, n = info["platt"][0]
    tr = fr.filter((pl.col("game_date") < dt.date.fromisoformat(s0)) & ~pl.col("era_any"))
    a2, b2 = C.fit_platt(tr["p0"].to_numpy(), tr["played"].to_numpy())
    assert (a, b, n) == (pytest.approx(a2), pytest.approx(b2), tr.height)
    assert (
        tr.height < fr.filter(pl.col("game_date") < dt.date.fromisoformat(s0)).height
    )  # the 2020 era rows really were left out
    # P0 is the raw p; P0c differs from it
    assert np.allclose(res["p_P0"], fr.filter(pl.col("season") == 2023)["p0"].to_numpy())
    assert not np.allclose(res["p_P0c"], res["p_P0"])


def test_shuffle_uses_one_permutation_per_block_for_the_arm_and_the_map_and_scores_real_labels() -> (
    None
):
    fr = _toy_frame()
    real, _ = C.walk_p(fr, {"R": ["x"]}, TINY, (2023,), min_train=500)
    sh, info = C.walk_p(fr, {"R": ["x"]}, TINY, (2023,), shuffle_seed=7, min_train=500)
    h = info["label_hashes"]
    assert h["R"] == h["P0c"]  # one hash, one permutation, shared
    assert np.array_equal(sh["y"], real["y"])  # scored on REAL labels
    assert not np.allclose(sh["p_R"], real["p_R"])
    sh2, _ = C.walk_p(fr, {"R": ["x"]}, TINY, (2023,), shuffle_seed=7, min_train=500)
    assert np.array_equal(sh["p_R"], sh2["p_R"])  # reproducible
    # can-fail: permute=False reproduces the old defect (labels not permuted) and is detected by equal hashes with the real run
    _, info_real = C.walk_p(fr, {"R": ["x"]}, TINY, (2023,), min_train=500)
    _, info_np = C.walk_p(
        fr, {"R": ["x"]}, TINY, (2023,), shuffle_seed=7, min_train=500, permute=False
    )
    assert info_np["label_hashes"]["R"] == info_real["label_hashes"]["R"] != h["R"]


def test_walk_checkpoints_resume_to_identical_output(tmp_path) -> None:
    fr = _toy_frame()
    a, _ = C.walk_p(fr, {"R": ["x"]}, TINY, (2023,), ckpt=tmp_path, min_train=500)
    b, _ = C.walk_p(fr, {"R": ["x"]}, TINY, (2023,), ckpt=tmp_path, min_train=500)
    for k in a:
        assert np.array_equal(a[k], b[k])


def test_a_planted_real_signal_beats_the_map_and_the_shuffled_null_does_not() -> None:
    fr = _toy_frame(seed=3)
    real, _ = C.walk_p(fr, {"R19": ["x"]}, TINY, (2023,), min_train=500)
    d = C.ll_rows(real["p_R19"], real["y"]) - C.ll_rows(real["p_P0c"], real["y"])
    assert d.mean() < -0.02  # x carries the label; the 1-d map of p0 can use it only through p0
    s = []
    for seed in range(1, 6):
        sh, _ = C.walk_p(fr, {"R19": ["x"]}, TINY, (2023,), shuffle_seed=seed, min_train=500)
        s.append(float((C.ll_rows(sh["p_R19"], sh["y"]) - C.ll_rows(sh["p_P0c"], sh["y"])).mean()))
    assert np.mean(s) > -0.003 and d.mean() < min(
        s
    )  # a leak-free harness: shuffled R does not beat the shuffled map


# ---------------------------------------------------------------- verdict
def _rows(
    point_ll: float,
    point_br: float,
    hi: float = -0.001,
    p: float = 0.001,
    lo_d1: float = -0.012,
    d2_ll: float | None = None,
) -> list[dict]:
    rows = []
    for s in C.VERDICT_SEASONS:
        for c in ("D1", "D2"):
            for m in ("ll", "brier"):
                pt = point_ll if m == "ll" else point_br
                if c == "D2" and m == "ll" and d2_ll is not None:
                    pt = d2_ll
                rows.append(
                    {
                        "season": s,
                        "contrast": c,
                        "metric": m,
                        "point": pt,
                        "lo": lo_d1 if (c == "D1" and m == "ll") else pt - 0.003,
                        "hi": hi,
                        "p": p,
                        "p_bh": p,
                        "n": 30000,
                        "n_games": 1300,
                    }
                )
    return rows


def test_decide_beaten_holds_mixed_and_no_verdict() -> None:
    assert C.decide(_rows(-0.02, -0.006), True)["verdict"] == "PRODUCTION_BEATEN"
    assert (
        C.decide(_rows(-0.02, -0.006), False)["verdict"] == "NO VERDICT"
    )  # a failed guard is never a result
    assert C.decide(_rows(-0.02, -0.002), True)["verdict"] == "MIXED"  # Brier below the floor
    assert (
        C.decide(_rows(-0.02, -0.006, hi=0.001), True)["verdict"] == "MIXED"
    )  # CI upper not below 0
    assert C.decide(_rows(-0.02, -0.006, p=0.2), True)["verdict"] == "MIXED"  # BH p above 0.05
    assert (
        C.decide(_rows(-0.02, -0.006, d2_ll=-0.004), True)["verdict"] == "MIXED"
    )  # D2 floor missed
    holds = _rows(-0.001, -0.0005, hi=0.002, lo_d1=-0.004)
    assert (
        C.decide(holds, True)["verdict"] == "PRODUCTION_HOLDS"
    )  # D1 log-loss CI lower > -0.005 in both seasons
    one = _rows(-0.001, -0.0005, hi=0.002, lo_d1=-0.004)
    one[0]["lo"] = -0.007  # 2023 D1 ll
    assert C.decide(one, True)["verdict"] == "MIXED"


def test_family_table_has_eight_tests_with_bh() -> None:
    rng = np.random.default_rng(0)
    n = 4000
    res = {
        "season": np.repeat([2023, 2024], n // 2),
        "gid": np.tile(np.repeat(np.arange(500), 4), 2).astype(str)[:n],
        "y": (rng.random(n) < 0.8).astype(float),
    }
    for k, base in (("P0", 0.8), ("P0c", 0.8), ("R19", 0.8)):
        res[f"p_{k}"] = np.clip(base + rng.normal(0, 0.05, n), 0.05, 0.95)
    rows = C.family_table(res, n_boot=200)
    assert len(rows) == 8 and {(r["season"], r["contrast"], r["metric"]) for r in rows} == {
        (s, c, m) for s in C.VERDICT_SEASONS for c in ("D1", "D2") for m in C.METRICS
    }
    assert all(r["p_bh"] >= r["p"] - 1e-12 for r in rows)


# ---------------------------------------------------------------- G3, G4, G5, ECE and slices
def _scored(
    n_players: int = 200, n_days: int = 400, seed: int = 0, signal: float = 1.0
) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    pid = np.tile(np.arange(n_players), n_days)
    date = np.repeat(np.arange(n_days) + 19000, n_players)
    x = rng.normal(size=len(pid))
    skill = np.tile(rng.normal(0, 0.5, n_players), n_days)
    p_true = 1 / (1 + np.exp(-(1.2 + signal * x + skill)))
    y = (rng.random(len(pid)) < p_true).astype(float)
    res = {
        "season": np.full(len(pid), 2023),
        "pid": pid,
        "date": date,
        "gid": np.array([f"g{d}_{p // 10}" for d, p in zip(date, pid, strict=True)]),
        "y": y,
        "p_P0c": np.full(len(pid), y.mean()),
        "p_P0": np.full(len(pid), y.mean()),
        "p_R19": p_true,
    }
    res["m_tag60_code"] = np.where(x > 1.5, 3.0, 1.0)
    res["m_starter10"] = (skill > 0).astype(float)
    res["m_is_b2b_second"] = (x < -1.5).astype(float)
    res["m_returner"] = (rng.random(len(pid)) < 0.1).astype(float)
    res["m_n_played_prior"] = rng.integers(5, 40, len(pid)).astype(float)
    # duplicate season 2024 so every verdict-season guard has rows
    out = {k: np.concatenate([v, v]) for k, v in res.items()}
    out["season"] = np.r_[np.full(len(pid), 2023), np.full(len(pid), 2024)]
    out["gid"] = np.r_[res["gid"], np.array([g + "b" for g in res["gid"]])]
    return out


def test_g3_g4_pass_on_a_real_signal_and_fail_when_the_signal_is_a_static_player_rate() -> None:
    res = _scored()
    g3 = C.g3_dayshuffle(res, n_boot=200)
    assert all(r["ok"] for r in g3)  # shuffled across players of a date: worse than the map
    g4 = C.g4_pastonly(res, n_boot=200)
    assert all(r["ok"] for r in g4) and all(r["n_without_previous"] > 0 for r in g4)
    # can-fail G3: an inert scorer (R19 == constant) shuffled is identical to the map: lower CI is not > 0
    flat = dict(res)
    flat["p_R19"] = np.full(len(res["y"]), res["y"].mean())
    assert not all(r["ok"] for r in C.g3_dayshuffle(flat, n_boot=200))
    # can-fail G4: p that is a pure static player rate is not beaten by "the player's previous p" (identical)
    stat = dict(res)
    stat["p_R19"] = np.tile(np.random.default_rng(0).uniform(0.4, 0.9, 200), 800)
    assert not all(r["ok"] for r in C.g4_pastonly(stat, n_boot=200))


def test_g4_scores_only_rows_with_an_earlier_row_in_the_same_season() -> None:
    res = _scored(n_players=30, n_days=10)
    g4 = C.g4_pastonly(res, n_boot=100)
    for r in g4:
        assert r["n"] == 30 * 10 - 30 and r["n_without_previous"] == 30


def test_g5_needs_both_the_null_mean_and_the_observed_below_every_draw() -> None:
    ok = C.g5_decide(
        {2023: -0.03, 2024: -0.03},
        {
            2023: np.full(50, 0.0) + np.linspace(-0.002, 0.002, 50),
            2024: np.linspace(-0.002, 0.002, 50),
        },
    )
    assert all(r["ok"] for r in ok) and ok[0]["permutation_p_min"] == pytest.approx(1 / 51)
    leak = C.g5_decide(
        {2023: -0.03, 2024: -0.03}, {2023: np.full(50, -0.01), 2024: np.linspace(-0.002, 0.002, 50)}
    )  # a shuffled R still beats the map
    assert not leak[0]["mean_ok"] and not leak[0]["ok"] and leak[1]["ok"]
    weak = C.g5_decide(
        {2023: -0.001, 2024: -0.03},
        {2023: np.linspace(-0.002, 0.002, 50), 2024: np.linspace(-0.002, 0.002, 50)},
    )  # observed not below the null minimum
    assert not weak[0]["observed_below_min"] and weak[1]["ok"]


def test_ece_and_slice_guards_fail_when_r19_is_worse_calibrated_or_worse_on_a_slice() -> None:
    res = _scored()
    assert C.ece_and_slices(res, n_boot=100)["ok"]
    bad = dict(res)
    bad["p_R19"] = np.clip(res["p_R19"] * 0.5 + 0.05, 0, 1)  # miscalibrated
    out = C.ece_and_slices(bad, n_boot=100)
    assert not out["ok"] and not all(e["ok"] for e in out["ece"])
    # worse on a guard slice with n >= 300 only
    sl = dict(res)
    p = res["p_R19"].copy()
    m = res["m_starter10"] < 0.5
    p[m] = 1 - p[m]
    sl["p_R19"] = p
    out = C.ece_and_slices(sl, n_boot=100)
    assert any((not r["ok"]) and r["slice"] == "bench" for r in out["slices"])
    # a tiny slice (n < 300) can never fail the guard
    tiny = dict(res)
    tiny["m_returner"] = np.zeros(len(res["y"]))
    tiny["m_returner"][:5] = 1.0
    assert all(
        r["ok"] for r in C.ece_and_slices(tiny, n_boot=100)["slices"] if r["slice"] == "returner"
    )


# ---------------------------------------------------------------- G2
def test_g2_detects_an_outcome_dependent_null_and_a_null_with_no_listed_cause() -> None:
    rng = np.random.default_rng(0)
    n = 4000
    played = rng.random(n) < 0.8
    mins = np.where(played, rng.uniform(5, 40, n), 0.0)
    base = pl.DataFrame(
        {"season": np.full(n, 2023), "min_real": mins, "games_played_prior": rng.normal(size=n)}
    )
    clean = base.with_columns(pl.lit(1.0).alias("terc"))
    assert C.g2_missingness(clean, ["games_played_prior", "terc"])["ok"]
    # NULL iff the player did not play: the birth-date fingerprint
    leaky = base.with_columns(
        pl.when(pl.col("min_real") > 0).then(30.0).otherwise(None).alias("age")
    )
    g = C.g2_missingness(leaky, ["games_played_prior", "age"])
    assert not g["ok"] and "age" in g["unexplained_null_inputs"]
    # a listed-cause input whose NULL is outcome-dependent still fails the gap test
    leaky2 = base.with_columns(
        pl.when(pl.col("min_real") > 0).then(0.3).otherwise(None).alias("terc")
    )
    g2 = C.g2_missingness(leaky2, ["games_played_prior", "terc"])
    assert (
        not g2["ok"]
        and not g2["unexplained_null_inputs"]
        and any(not p["ok"] for p in g2["per_season"])
    )
    # random NULLs independent of the outcome with a listed cause pass
    rnd = base.with_columns(
        pl.when(pl.Series(rng.random(n) < 0.3)).then(None).otherwise(1.0).alias("terc")
    )
    assert C.g2_missingness(rnd, ["terc"])["ok"]


def test_g2_exempts_only_inputs_whose_null_has_an_exact_as_of_cause() -> None:
    n = 3000
    rng = np.random.default_rng(1)
    played = rng.random(n) < 0.7
    ret_n = np.where(
        played, 99.0, 3.0
    )  # as-of cause variable correlated with the outcome (legitimate information)
    f = pl.DataFrame(
        {"season": np.full(n, 2023), "min_real": np.where(played, 20.0, 0.0), "ret_n": ret_n}
    ).with_columns(pl.when(pl.col("ret_n") >= 99).then(None).otherwise(5.0).alias("ret_mean_min"))
    assert C.g2_missingness(f, ["ret_n", "ret_mean_min"])["ok"]
    bad = f.with_columns(
        pl.when(pl.col("min_real") > 0).then(None).otherwise(5.0).alias("ret_mean_min"),
        pl.lit(3.0).alias("ret_n"),
    )  # NULL with no as-of cause
    out = C.g2_missingness(bad, ["ret_n", "ret_mean_min"])
    assert out["as_of_cause_violations"]["ret_mean_min"] > 0 and not out["ok"]


# ---------------------------------------------------------------- frame rules
def test_frames_identical_is_byte_exact_nan_aware_and_row_set_aware() -> None:
    a = pl.DataFrame(
        {
            "game_id": ["a", "b"],
            "player_id": [1, 2],
            "x": [1.0, float("nan")],
            "game_date": [dt.date(2023, 1, 1), dt.date(2023, 1, 2)],
        }
    )
    mask = pl.col("game_date") <= dt.date(2023, 1, 2)
    assert C.frames_identical(a, a.clone(), ["x"], mask)["identical"]
    b = a.with_columns(pl.Series("x", [1.0, 2.0]))
    assert C.frames_identical(a, b, ["x"], mask)["differing"] == ["x"]
    assert not C.frames_identical(a, a.head(1), ["x"], mask)["identical"]
    tiny = a.with_columns(pl.Series("x", [1.0 + 1e-15, float("nan")]))
    assert not C.frames_identical(a, tiny, ["x"], mask)[
        "identical"
    ]  # a one-ulp edit is not "identical"
    # label-defined membership: extra rows on the cutoff day are allowed, extra rows before it are not
    c = pl.concat(
        [
            a,
            pl.DataFrame(
                {"game_id": ["c"], "player_id": [3], "x": [0.0], "game_date": [dt.date(2023, 1, 2)]}
            ),
        ]
    )
    assert C.frames_identical(
        a, c, ["x"], mask, strict_before=pl.col("game_date") < dt.date(2023, 1, 2)
    )["identical"]
    d = pl.concat(
        [
            a,
            pl.DataFrame(
                {"game_id": ["c"], "player_id": [3], "x": [0.0], "game_date": [dt.date(2023, 1, 1)]}
            ),
        ]
    )
    assert not C.frames_identical(
        a, d, ["x"], mask, strict_before=pl.col("game_date") < dt.date(2023, 1, 2)
    )["identical"]


def test_era_channels_report_drop_and_the_player_exclusion() -> None:
    fr = pl.DataFrame(
        {
            "game_id": ["a", "b", "c"],
            "player_id": [1, 2, 3],
            "season": [2020, 2021, 2023],
            "tag60_code": [3.0, 3.0, 3.0],
            "has_report": [1, 1, 1],
            "n_out_rot": [2.0, 2.0, 2.0],
        }
    )
    flags = pl.DataFrame(
        {
            "game_id": ["a", "b", "c"],
            "covid_bubble": [False, False, False],
            "no_fans": [False, False, False],
            "limited_fans": [True, False, False],
        }
    )
    e = C.add_era_channels(fr, flags)
    assert (
        e["era_limited"].to_list() == [1.0, 0.0, 0.0]
        and e["era_report_dropped"].to_list() == [0.0, 1.0, 0.0]
        and e["era_any"].to_list() == [True, False, False]
    )
    d = C.drop_report_features(e, ("tag60_code", "n_out_rot"), zero=("has_report",))
    assert d["tag60_code"].to_list() == [3.0, None, 3.0] and d["has_report"].to_list() == [1, 0, 1]
    with pytest.raises(ValueError, match="era flag"):
        C.add_era_channels(fr, flags.head(2))
    f2 = pl.DataFrame(
        {"player_id": [C.EXCLUDED_PLAYER, C.EXCLUDED_PLAYER, 5], "age": [None, 25.0, 30.0]},
        schema={"player_id": pl.Int64, "age": pl.Float64},
    )
    out, n = C.exclude_null_age_player(f2)
    assert n == 1 and out.height == 2
    f3 = pl.DataFrame(
        {"player_id": [C.EXCLUDED_PLAYER, 7], "age": [None, None]},
        schema={"player_id": pl.Int64, "age": pl.Float64},
    )
    with pytest.raises(RuntimeError, match="NULL age beyond player"):
        C.exclude_null_age_player(f3)


def test_returner_is_missed_at_least_three_team_games_on_the_same_team() -> None:
    tg = pl.DataFrame(
        {"game_id": [f"g{i}" for i in range(8)], "team_id": [1] * 8, "tgn": list(range(8))}
    )
    box = pl.DataFrame(
        {
            "game_id": ["g0", "g1", "g5", "g6", "g0", "g1", "g2", "g3", "g4"],
            "player_id": [10, 10, 10, 10, 11, 11, 11, 11, 11],
            "team_id": [1] * 9,
            "minutes": [20.0] * 9,
        }
    )
    rows = pl.DataFrame(
        {
            "game_id": ["g5", "g6", "g7", "g5"],
            "player_id": [10, 10, 10, 11],
            "team_id": [1, 1, 1, 1],
        }
    )
    out = C.returner_flag(box, rows, tg).sort(["player_id", "game_id"])
    got = {(r["player_id"], r["game_id"]): r["returner"] for r in out.iter_rows(named=True)}
    assert got[(10, "g5")] == 1.0  # last played k=1, now k=5: missed games 2,3,4 = 3
    assert got[(10, "g6")] == 0.0  # played g5
    assert got[(10, "g7")] == 0.0  # played g6
    assert got[(11, "g5")] == 0.0  # last played k=4, missed none
    # a player who changed team: the previous game on ANOTHER team does not count
    box2 = pl.DataFrame({"game_id": ["g0"], "player_id": [12], "team_id": [2], "minutes": [10.0]})
    tg2 = pl.concat([tg, pl.DataFrame({"game_id": ["g0"], "team_id": [2], "tgn": [0]})])
    r2 = C.returner_flag(
        box2, pl.DataFrame({"game_id": ["g5"], "player_id": [12], "team_id": [1]}), tg2
    )
    assert r2["returner"].to_list() == [0.0]


def test_no_holdout_path_exists() -> None:
    import research.eval.pplay_minutes_valid as H
    import research.eval.pplay_minutes_valid_rule as R

    holdout = str(2024 + 1)
    for mod in (C, H, R):
        src = Path(mod.__file__).read_text()
        assert holdout not in src, f"{mod.__name__} mentions the holdout season"
    assert C.MAX_SEASON == 2024 and max(C.SCORED_SEASONS) <= 2024 and max(C.TRAIN_SEASONS) == 2024
    with pytest.raises(ValueError, match="holdout"):
        C.assert_holdout(pl.DataFrame({"season": [2023, 2025]}))
    C.assert_holdout(pl.DataFrame({"season": [2023, 2024]}))
    fr = _toy_frame().with_columns(pl.lit(2025).alias("season"))
    with pytest.raises(ValueError, match="holdout"):
        C.walk_p(fr, {"R": ["x"]}, TINY, (2025,), min_train=10)
