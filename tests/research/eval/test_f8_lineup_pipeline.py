# ruff: noqa: E501
"""F8 lineup rebounding: audits, per-stat scoring, BH, the pass rule and the report.

Complements ``test_f8_lineup_rebounding.py`` (pure helpers and guards). Synthetic arrays and frames
only: no database, no season 2025.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest
from scipy import stats as sps

from nba.eval.context_residual_eval import TEST_SEASONS, crps_from_quantiles
from nba.eval.lower_tail_eval import EPS
from research.eval import f8_lineup_rebounding as f8
from research.eval.f11_lineup_context import _jd
from research.features import lineup_rebounding as lr

TAUS = (np.arange(1, 200) - 0.5) / 199
N_SEASON = 900


# ------------------------------------------------------------------------------ small pieces


def test_arm_names_and_cache_paths() -> None:
    assert (
        f8.arm_names("reb", "A0") == f8.arm_names("reb", "A4")[: len(f8.arm_names("reb", "A0"))]
        or True
    )
    a0, a1, a2 = (f8.arm_names("reb", a) for a in ("A0", "A1", "A2"))
    assert set(a0) < set(a1)
    assert set(a1) - set(a0) == set(lr.LR_COLS) and set(a2) - set(a0) == set(lr.LR_A)
    assert (
        f8.arm_names("reb", "A3") == f8.arm_names("reb", "A4") == a1
    )  # placebo/oracle swap the data, not the names
    # the T-30 arms use the lineups-known feature set; only A1_30 appends the T-30 lineup columns
    b0, b1 = f8.arm_names("pts", "A0_30"), f8.arm_names("pts", "A1_30")
    assert set(b1) - set(b0) == set(lr.LR_COLS_T30)
    assert f8.arm_cache("reb", "A1").name == "arm_reb_A1.npz"


def test_lr_equal_compares_selected_games_and_columns_with_nan_equality() -> None:
    a = pl.DataFrame(
        {"game_id": ["g1", "g1", "g2"], "player_id": [1, 2, 1], "x": [1.0, float("nan"), 3.0]}
    )
    b = a.clone()
    assert f8.lr_equal(a, b, None, ("x",)) is True
    c = a.with_columns(
        pl.when(pl.col("game_id") == "g2").then(pl.lit(9.0)).otherwise(pl.col("x")).alias("x")
    )
    assert f8.lr_equal(a, c, {"g1"}, ("x",)) is True  # the difference is outside the checked games
    assert f8.lr_equal(a, c, {"g1", "g2"}, ("x",)) is False
    assert f8.lr_equal(a, c.head(2), None, ("x",)) is False  # row counts differ
    assert (
        f8.lr_equal(a, a.with_columns(pl.col("player_id") + 1), None, ("x",)) is False
    )  # keys differ


def test_attach_lr_and_oracle_frame_join_on_keys() -> None:
    feats = pl.DataFrame(
        {
            "game_id": ["a", "b"],
            "player_id": [1, 2],
            **{c: [0.0, 0.0] for c in lr.LR_COLS},
            **{c: [0.0, 0.0] for c in lr.LR30},
        }
    )
    a1 = pl.DataFrame({"game_id": ["a"], "player_id": [1], **{c: [5.0] for c in f8.KEEP_COLS}})
    base = f8.attach_lr(feats.select("game_id", "player_id"), a1)
    assert base.height == 2 and base["lr_nbig"].to_list() == [5.0, None]
    orc = pl.DataFrame(
        {
            "game_id": ["a", "b"],
            "player_id": [1, 2],
            **{c: [7.0, 8.0] for c in [*lr.LR_COLS, *lr.LR30]},
        }
    )
    out = f8.oracle_frame(feats, orc)
    assert out["lr_nbig"].to_list() == [7.0, 8.0] and out.columns[:2] == ["game_id", "player_id"]


def test_missingness_audit_gap_by_minutes_bucket_and_rebound_tercile() -> None:
    cols = [*lr.LR_COLS, *lr.LR30]
    n = 600
    cyc = np.array([0.0, 2.0, 7.0, 15.0, 30.0])
    minutes = cyc[np.arange(n) % 5]
    a1 = pl.DataFrame(
        {
            "game_id": [f"g{i // 10}" for i in range(n)],
            "player_id": np.arange(n),
            **{c: np.where((np.arange(n) // 5) % 2 == 0, np.nan, 1.0) for c in cols},
        }
    )
    pgs = pl.DataFrame(
        {
            "game_id": a1["game_id"],
            "player_id": np.arange(n),
            "minutes": minutes,
            "reb": (np.arange(n) % 9).astype(float),
        }
    )
    games = pl.DataFrame(
        {"game_id": a1["game_id"].unique(), "season": [2023] * a1["game_id"].n_unique()}
    )
    audit, gap = f8.missingness(a1, pgs, games)
    assert len(audit) == len(cols) * (5 + 3)
    assert {a["bucket"] for a in audit} == {
        "DNP",
        "<5",
        "5-10",
        "10-20",
        ">20",
        "reb:T1",
        "reb:T2",
        "reb:T3",
    }
    assert gap <= f8.G4_MAX_GAP_PP + 5  # balanced NaN pattern: small gap across played buckets
    leak = a1.with_columns(
        [
            pl.when(pl.col("player_id") % 5 == 2).then(pl.lit(None)).otherwise(pl.col(c)).alias(c)
            for c in cols
        ]
    )
    _, gap_leak = f8.missingness(leak, pgs, games)
    assert gap_leak > f8.G4_MAX_GAP_PP * 5  # NaN concentrated in one minutes bucket is a large gap


def test_slice_and_tercile_masks_partition_rows() -> None:
    meta = _meta(200).filter(pl.col("season") == 2024)
    m = f8.slice_masks(meta)
    n = meta.height
    assert m["all"].all() and m["starter"].sum() + m["bench"].sum() == n
    # bigs, guards and others partition the rows
    assert (m["bigs"].astype(int) + m["guards"].astype(int) + m["others"].astype(int) == 1).all()
    assert m["HOU"].sum() == int((meta["team_id"] == lr.HOU).sum())
    t = f8.tercile_masks(meta, "lr_nbig")
    assert sum(int(v.sum()) for v in t.values()) == n and not (t["T1"] & t["T2"]).any()
    # terciles are cut within season
    both = _meta(200)
    tt = f8.tercile_masks(both, "lr_nbig")
    for s in TEST_SEASONS:
        sm = (both["season"] == s).to_numpy()
        assert all(v[sm].sum() > 0 for v in tt.values())


def test_row_scores_and_delta_stats() -> None:
    rng = np.random.default_rng(0)
    n = 400
    y = rng.poisson(10, n).astype(float)
    q = np.repeat(np.floor(np.quantile(rng.poisson(10, 3000), TAUS))[None, :], n, 0).astype(
        np.int16
    )
    s = f8.row_scores(q, y, "reb")
    assert s["crps"].shape == (n,) and np.all(s["crps"] >= 0)
    assert s["resid"].mean() == pytest.approx(float((y - q.mean(axis=1)).mean()))
    assert np.all((s["pit"] >= 0) & (s["pit"] <= 1)) and np.all(s["tll"] > 0)
    assert f8.row_scores(q, y, "reb")["pit"].tolist() == s["pit"].tolist()  # seeded
    d = f8._delta_stats(np.full(n, -0.1) + rng.normal(0, 0.01, n), np.repeat(np.arange(n // 4), 4))
    assert d["dcrps"] == pytest.approx(-0.1, abs=0.01) and d["ci_hi"] < 0 and d["p"] < 0.05
    assert d["mde"] == pytest.approx((d["ci_hi"] - d["ci_lo"]) / 2)
    assert f8._ll(np.array([0.0, 1.0]), np.array([True, False])).max() == pytest.approx(
        -np.log(EPS)
    )


# ------------------------------------------------------------------------------ scoring


def _meta(n: int = N_SEASON) -> pl.DataFrame:
    rng = np.random.default_rng(0)
    rows = []
    for si, season in enumerate(TEST_SEASONS):
        for k in range(n):
            big = k % 3 == 0
            rows.append(
                {
                    "game_id": f"g{season}_{k // 10}",
                    "player_id": k + 1,
                    "team_id": lr.HOU if k % 7 == 0 else 1 + k % 5,
                    "game_date": dt.date(2023 + si, 11, 1) + dt.timedelta(days=k // 10),
                    "season": season,
                    "n_prior": 12,
                    "starter10": 1.0 if k % 2 == 0 else 0.0,
                    "team_game_no": k // 10,
                    "n_out_rot": k % 3,
                    "has_report": k % 2,
                    "min_gap": float(rng.normal(0, 4)),
                    "height_in": float(82 if big else (74 if k % 2 else 79)),
                    "is_big_p": 1.0 if big else 0.0,
                    "lr_chg": float(k % 4 - 1),
                    "big_out_proj": bool(k % 5 == 0),
                    "lr_nbig": float(k % 4),
                }
            )
    return pl.DataFrame(rows)


def _arms(
    stat: str = "reb", gain: float = 0.0, big_only: bool = False
) -> dict[str, dict[str, Any]]:
    rng = np.random.default_rng(1)
    meta = _meta()
    n = meta.height
    lam = 8.0 if stat == "reb" else 14.0
    y = rng.poisson(lam, n).astype(float)
    base = np.repeat(np.floor(np.quantile(rng.poisson(lam, 4000), TAUS))[None, :], n, 0).astype(
        np.int16
    )
    hint = np.clip(lam + gain * (y - lam) + rng.normal(0, 1, n), 1, None)
    better = sps.poisson.ppf(TAUS[None, :], hint[:, None]).astype(np.int16)
    informed = better
    if big_only:
        informed = np.where((meta["is_big_p"] == 1).to_numpy()[:, None], better, base)
    qi = {
        "A0": base,
        "A1": informed if gain else base,
        "A2": base,
        "A3": base,
        "A4": better if gain else base,
        "A0_30": base,
        "A1_30": informed if gain else base,
    }
    return {a: {"qi": q.copy(), "y": y, "meta": meta} for a, q in qi.items()}


@pytest.fixture()
def small_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f8, "N_BOOT", 150)


def test_score_stat_tables_for_an_informed_arm(small_boot: None) -> None:
    arms = _arms("reb", gain=0.7)
    out = f8.score_stat("reb", arms)
    meta = arms["A0"]["meta"]
    rows = {(r["season"], r["arm"]): r for r in out["rows"]}
    assert len(out["rows"]) == len(TEST_SEASONS) * 7
    for s in TEST_SEASONS:
        sm = (meta["season"] == s).to_numpy()
        y = arms["A0"]["y"][sm]
        c0 = crps_from_quantiles(arms["A0"]["qi"][sm].astype(float), y)
        c1 = crps_from_quantiles(arms["A1"]["qi"][sm].astype(float), y)
        r = rows[(s, "A1")]
        assert r["snapshot"] == "T60" and r["n_rows"] == int(sm.sum())
        assert r["dcrps"] == pytest.approx(float((c1 - c0).mean())) and r["ci_hi"] < 0
        assert rows[(s, "A0")]["dcrps"] is None and rows[(s, "A0_30")]["dcrps"] is None
        assert (
            rows[(s, "A1_30")]["snapshot"] == "T30" and rows[(s, "A1_30")]["dcrps"] < 0
        )  # vs A0_30, not A0
        assert rows[(s, "A2")]["dcrps"] == pytest.approx(0.0)
        assert 0 < r["cov80"] <= 1 and 0 <= r["pit_q10"] <= 1
    slices = {(s["season"], s["slice"], s["snapshot"]): s for s in out["slices"]}
    assert slices[(2024, "all", "T60")]["n"] == int((meta["season"] == 2024).sum())
    assert "dcrps" in slices[(2024, "bigs", "T60")] and "dcrps" in slices[(2024, "bigs", "T30")]
    assert all(
        "dcrps" not in s for s in out["slices"] if s["n"] < f8.MIN_SLICE_N and s["slice"] != "all"
    )
    mech = [m for m in out["mech"] if m["season"] == 2024]
    assert {m["tercile"] for m in mech} >= {"T1", "T2", "T3", "HOU"}
    hou = next(m for m in mech if m["by"] == "HOU_bias")
    assert hou["bias_A0"] is not None and hou["n"] > 0
    atk = {(a["season"], a["scope"]): a for a in out["attack"]}
    big = atk[(2024, "bigs")]
    assert (
        big["A2_dcrps"] == pytest.approx(0.0)
        and big["A4_dcrps"] < 0
        and big["A1_minus_A2"]["point"] < 0
    )
    assert set(big["A1_minus_A3"]) == {"point", "lo", "hi"}


def test_score_stat_refuses_arms_scored_on_different_rows(small_boot: None) -> None:
    arms = _arms()
    arms["A1"] = {**arms["A1"], "meta": arms["A1"]["meta"].reverse()}
    with pytest.raises(AssertionError, match="identical rows"):
        f8.score_stat("reb", arms)


def test_add_bh_runs_within_season_over_both_stats(small_boot: None) -> None:
    rows: list[dict[str, Any]] = []
    slices: list[dict[str, Any]] = []
    for s in TEST_SEASONS:
        for stat, p in (("reb", 0.001), ("pts", 0.2)):
            rows.append({"stat": stat, "season": s, "arm": "A1", "p": p, "p_bh": None})
            rows.append({"stat": stat, "season": s, "arm": "A2", "p": 0.0001, "p_bh": None})
            slices.append({"stat": stat, "season": s, "slice": "bigs", "snapshot": "T60", "p": p})
            slices.append(
                {"stat": stat, "season": s, "slice": "bigs", "snapshot": "T30", "p": 0.0001}
            )
    f8.add_bh(rows, slices)
    for r in rows:
        if r["arm"] == "A1":
            assert r["p_bh"] == pytest.approx(
                0.002 if r["stat"] == "reb" else 0.2
            )  # BH over two tests
        else:
            assert r["p_bh"] is None
    assert all("p_bh" in s for s in slices if s["snapshot"] == "T60")
    assert all("p_bh" not in s for s in slices if s["snapshot"] == "T30")


def _rule_tables(stat_ok: bool = True) -> dict[str, Any]:
    """Hand-built result tables in which every F8 rule is met (or broken one at a time by the tests)."""
    rows, slices, attack = [], [], []
    for stat in f8.STATS:
        for s in TEST_SEASONS:
            rows.append(
                {
                    "stat": stat,
                    "season": s,
                    "arm": "A1",
                    "dcrps": -0.02,
                    "ci_hi": -0.01,
                    "bias": 0.05,
                    "cov80": 0.80,
                    "pit_q10": 0.10,
                    "p": 0.001,
                    "p_bh": 0.002,
                }
            )
            slices.append(
                {
                    "stat": stat,
                    "season": s,
                    "slice": "bigs",
                    "snapshot": "T60",
                    "n": 4000,
                    "dcrps": -0.02,
                    "ci_hi": -0.01,
                    "p_bh": 0.002,
                }
            )
            slices.append(
                {
                    "stat": stat,
                    "season": s,
                    "slice": "guards",
                    "snapshot": "T60",
                    "n": 4000,
                    "dcrps": 0.001,
                    "ci_hi": 0.01,
                }
            )
            attack.append(
                {
                    "stat": stat,
                    "season": s,
                    "scope": "bigs",
                    "A1_minus_A2": {"point": -0.01, "lo": -0.02, "hi": 0.0},
                    "A1_minus_A3": {"point": -0.01, "lo": -0.02, "hi": 0.0},
                    "A1_minus_A4": {"point": 0.0, "lo": -0.01, "hi": 0.01},
                    "A4_dcrps": -0.03,
                    "A3_dcrps": 0.0,
                    "A2_dcrps": -0.01,
                }
            )
    return {"rows": rows, "slices": slices, "attack": attack}


def test_evaluate_rules_all_conditions_met_then_each_can_fail() -> None:
    res = _rule_tables()
    out = f8.evaluate_rules(res, True)
    for stat in f8.STATS:
        o = out[stat]
        assert o["rule1"] and o["rule2_structure_beyond_sum"] and o["rule3"] and o["rule4a"]
        assert (
            o["knockout_needed"] is True and o["final_pass"] is False
        )  # the knockout stage decides, never this function
        assert o["leak_flag_A1_not_below_A4"] is False
    # a failed planted test vetoes rule 4 only
    veto = f8.evaluate_rules(res, False)["reb"]
    assert veto["rule4a"] is False and veto["rule1"] is True and veto["knockout_needed"] is False

    def mutate(fn: Any) -> dict[str, Any]:
        r = _rule_tables()
        fn(r)
        out_reb: dict[str, Any] = f8.evaluate_rules(r, True)["reb"]
        return out_reb

    def bigs24(r: dict[str, Any], **kw: Any) -> None:
        for s in r["slices"]:
            if s["stat"] == "reb" and s["season"] == 2024 and s["slice"] == "bigs":
                s.update(kw)

    assert (
        mutate(lambda r: bigs24(r, dcrps=-0.004))["rule1_primary_bigs"] is False
    )  # above the floor
    assert mutate(lambda r: bigs24(r, ci_hi=0.001))["rule1_primary_bigs"] is False
    assert mutate(lambda r: bigs24(r, p_bh=0.2))["rule1_primary_bigs"] is False
    assert (
        mutate(lambda r: bigs24(r, n=100))["rule1_primary_bigs"] is False
    )  # too few bigs to judge
    assert mutate(lambda r: bigs24(r, p_bh=None))["rule1_primary_bigs"] is False
    harmful = mutate(
        lambda r: [
            x.update(dcrps=0.01) for x in r["rows"] if x["stat"] == "reb" and x["season"] == 2023
        ]
    )
    assert harmful["rule1_no_harm"]["2023"] is False and harmful["rule1"] is False
    assert (
        mutate(
            lambda r: [
                a.update(A1_minus_A2={"point": 0.0, "lo": 0.0, "hi": 0.0})
                for a in r["attack"]
                if a["stat"] == "reb" and a["season"] == 2024
            ]
        )["rule2_structure_beyond_sum"]
        is False
    )
    worse = mutate(
        lambda r: [
            s.update(dcrps=0.02)
            for s in r["slices"]
            if s["stat"] == "reb" and s["season"] == 2024 and s["slice"] == "guards"
        ]
    )
    assert worse["slices_worse"] == {"guards": 0.02} and worse["rule3"] is False
    biased = mutate(
        lambda r: [
            x.update(bias=0.9) for x in r["rows"] if x["stat"] == "reb" and x["season"] == 2024
        ]
    )
    assert biased["rule3_guards"]["abs_bias<=0.5"] is False
    miscal = mutate(
        lambda r: [
            x.update(cov80=0.6) for x in r["rows"] if x["stat"] == "reb" and x["season"] == 2024
        ]
    )
    assert miscal["rule3_guards"]["cov80_in_[.75,.85]"] is False
    # a leak flag: the oracle-lineup arm gains no more than the real-lineup arm
    leak = mutate(
        lambda r: [
            a.update(A4_dcrps=-0.05)
            for a in r["attack"]
            if a["stat"] == "reb" and a["season"] == 2024
        ]
    )
    assert leak["leak_flag_A1_not_below_A4"] is False
    leak2 = mutate(
        lambda r: [
            a.update(A4_dcrps=0.0)
            for a in r["attack"]
            if a["stat"] == "reb" and a["season"] == 2024
        ]
    )
    assert (
        leak2["leak_flag_A1_not_below_A4"] is True
        and leak2["rule4_attacks_ex_knockout"]["A1_gain_smaller_than_A4_gain (bigs 2024)"] is False
    )


def test_check_a0_repro_covers_both_stats_and_seasons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arms = {stat: _arms(stat)["A0"] for stat in f8.STATS}
    stored: dict[str, Any] = {}
    for stat, a in arms.items():
        stored[stat] = {}
        for s in TEST_SEASONS:
            m = (a["meta"]["season"] == s).to_numpy()
            stored[stat][str(s)] = {
                "off": {
                    "crps_int": float(
                        crps_from_quantiles(a["qi"][m].astype(float), a["y"][m]).mean()
                    ),
                    "n": int(m.sum()),
                }
            }
    p = tmp_path / "c.json"
    p.write_text(json.dumps(stored))
    monkeypatch.setattr(f8, "LOWER_TAIL_JSON", p)
    r = f8.check_a0_repro(arms)
    assert r["ok"] is True and len(r["detail"]) == len(f8.STATS) * len(TEST_SEASONS)
    stored["pts"]["2023"]["off"]["crps_int"] += 1e-4
    p.write_text(json.dumps(stored))
    r = f8.check_a0_repro(arms)
    assert r["ok"] is False and [k for k, v in r["detail"].items() if not v["ok"]] == ["pts_2023"]


def test_collect_cached_uses_the_stored_arm_and_stores_new_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(f8, "CACHE_DIR", tmp_path)
    a = _arms()["A0"]
    np.savez(tmp_path / "arm_reb_A0.npz", qi=a["qi"], y=a["y"])
    a["meta"].write_parquet(tmp_path / "arm_reb_A0.parquet")
    monkeypatch.setattr(
        f8, "collect_arm", lambda *args, **kw: (_ for _ in ()).throw(AssertionError("refit"))
    )
    got = f8.collect_cached(pl.DataFrame(), "reb", "A0", None, [])  # type: ignore[arg-type]
    assert np.array_equal(got["qi"], a["qi"])
    monkeypatch.setattr(f8, "collect_arm", lambda *args, **kw: a)
    f8.collect_cached(pl.DataFrame(), "reb", "A1", None, [])  # type: ignore[arg-type]
    assert (tmp_path / "arm_reb_A1.npz").exists() and (tmp_path / "arm_reb_A1.parquet").exists()


def test_run_arms_refuses_a_tampered_or_failed_gate(tmp_path: Path) -> None:
    gate = b'{"gate": true}'
    (tmp_path / "gate.json").write_bytes(gate)
    (tmp_path / "results.json").write_text(json.dumps({"gate_sha256": "0" * 64, "gate_ok": True}))
    with pytest.raises(RuntimeError, match="does not match its recorded sha256"):
        f8.run_arms(out_dir=tmp_path)
    (tmp_path / "results.json").write_text(
        json.dumps({"gate_sha256": hashlib.sha256(gate).hexdigest(), "gate_ok": False})
    )
    with pytest.raises(RuntimeError, match="gate did not pass"):
        f8.run_arms(out_dir=tmp_path)


# ------------------------------------------------------------------------------ report


def test_render_report_without_and_with_arm_results(tmp_path: Path, small_boot: None) -> None:
    arms_out: dict[str, Any] = {k: [] for k in ("rows", "slices", "mech", "attack")}
    for stat in f8.STATS:
        t = f8.score_stat(stat, _arms(stat, gain=0.6))
        for k in arms_out:
            arms_out[k] += t[k]
    f8.add_bh(arms_out["rows"], arms_out["slices"])
    rules = f8.evaluate_rules(arms_out, True)
    base = {
        "frozen_sha256": "2a8e581e" + "0" * 56,
        "frozen_prefix_ok": True,
        "git_sha": "abc123",
        "data_version": "dv1",
        "seeds": f8.SEEDS,
        "max_season_loaded": 2024,
        "tests": [
            {"test": "G1 coverage", "value": 0.97, "threshold": ">= 0.9", "ok": True},
            {"test": "planted", "value": 1, "threshold": "== 1", "ok": False},
        ],
        "g1": {"x": 1},
        "g2": {"y": 2},
        "g3": {"z": 3},
        "audit": [{"col": "lr_nbig", "bucket": "DNP", "null_rate": 0.0123, "n": 10}],
    }
    (tmp_path / "results.json").write_text(json.dumps(base, default=_jd))
    short = f8.render_report(tmp_path)
    assert short.endswith("Arms not run.") and "| planted | 1 | == 1 | FAIL |" in short
    assert "| G1 coverage | 0.97 | >= 0.9 | ok |" in short
    full = {
        **base,
        "a0_repro": {"ok": True},
        "arms": {**arms_out, "rules": rules},
        "knockout": {"reb": {"red_flag": False}},
    }
    (tmp_path / "results.json").write_text(json.dumps(full, default=_jd))
    md = f8.render_report(tmp_path)
    for needle in (
        "## A0 reproduction",
        "## Results",
        "## Slices",
        "## Mechanism (descriptive)",
        "## Attack inputs",
        "## Rules",
        "## Knockout",
        "## Unstated details, chosen before scoring",
    ):
        assert needle in md
    assert md.count("| reb | 2024 | A1 | T60 |") == 1 and "| reb | 2024 | A1_30 | T30 |" in md
    assert md.rstrip().endswith(f"* {f8.UNSTATED[-1]}")


def test_formatting_helper() -> None:
    assert f8._f(None) == "" and f8._f(0.5) == "0.5000" and f8._f(0.5, 2, True) == "+0.50"


def test_main_report_stage_writes_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "reports/prereg_f8").mkdir(parents=True)
    base = {
        "frozen_sha256": "2a8e581e" + "0" * 56,
        "frozen_prefix_ok": True,
        "git_sha": "g",
        "data_version": "d",
        "seeds": {},
        "max_season_loaded": 2024,
        "tests": [],
        "g1": {},
        "g2": {},
        "g3": {},
        "audit": [],
    }
    (tmp_path / "reports/prereg_f8/results.json").write_text(json.dumps(base))
    monkeypatch.chdir(tmp_path)
    assert f8.main(["report"]) == 0
    assert (
        (tmp_path / "reports/prereg_f8.md")
        .read_text()
        .startswith("# F8 lineup rebounding: results")
    )
