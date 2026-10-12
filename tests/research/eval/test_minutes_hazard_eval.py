# ruff: noqa: E501
"""Behaviour tests for the MINUTES_HAZARD evaluator's data loaders, audits, scoring and report.

Everything runs on small synthetic frames; the heavy fit/simulate stages are the maintainer's.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import numpy as np
import polars as pl
import pytest
from scipy import stats as sps

from nba.props.context_residual import CRPS_TAUS
from research.eval import minutes_hazard_eval as mhe
from research.eval.minutes_v2_eval import score_minutes

# ------------------------------------------------------------------------------ small helpers


def test_pit_rates_partition_the_unit_interval() -> None:
    pit = np.array([0.05, 0.10, 0.5, 0.9, 0.95])
    q10, q90, mid = mhe._pit_rates(pit)
    assert q10 == pytest.approx(2 / 5) and q90 == pytest.approx(4 / 5)
    assert mid == pytest.approx(q90 - q10)  # (0.10, 0.90] is the 80% interval


def test_bucket_name_and_open_ended_bucket() -> None:
    assert mhe.bucket_name(0.0, 10.0) == "[0,10)"
    assert mhe.bucket_name(30.0, 1e9) == "[30,inf)"


def test_max_gap_pp_is_range_of_null_rates_per_column() -> None:
    audit = [
        {"col": "a", "bucket": "x", "n": 1, "null_rate": 0.01},
        {"col": "a", "bucket": "y", "n": 1, "null_rate": 0.04},
        {"col": "b", "bucket": "x", "n": 1, "null_rate": 0.0},
        {"col": "b", "bucket": "y", "n": 1, "null_rate": 0.0},
    ]
    gaps = mhe.max_gap_pp(audit)
    assert gaps["a"] == pytest.approx(3.0) and gaps["b"] == 0.0


def test_ci_txt_and_table_markdown() -> None:
    assert mhe._ci_txt(-0.01234, -0.02, 0.0) == "-0.0123 [-0.0200,+0.0000]"
    assert mhe._ci_txt(1.0, 0.5, 1.5, nd=1) == "+1.0 [+0.5,+1.5]"
    t = mhe._table(["a", "b"], [["1", "2"], ["3", "4"]])
    assert t == ["| a | b |", "|---|---|", "| 1 | 2 |", "| 3 | 4 |"]


def test_assert_seasons_refuses_the_frozen_holdout() -> None:
    mhe.assert_seasons(pl.DataFrame({"season": [2022, 2024]}))
    mhe.assert_seasons(pl.DataFrame({"x": [1]}))  # no season column: nothing to check
    mhe.assert_seasons(pl.DataFrame({"season": []}, schema={"season": pl.Int64}))
    with pytest.raises(ValueError, match="holdout"):
        mhe.assert_seasons(pl.DataFrame({"season": [2024, 2025]}))


def test_heavy_lock_creates_waits_and_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "heavy.lock"
    monkeypatch.setattr(mhe, "LOCK", lock)
    with mhe.HeavyLock():
        assert lock.is_dir()
    assert not lock.exists()  # rmdir in __exit__
    # a held lock is waited out (sleep releases it), never broken
    lock.mkdir()
    sleeps: list[float] = []

    def fake_sleep(s: float) -> None:
        sleeps.append(s)
        lock.rmdir()

    monkeypatch.setattr(time, "sleep", fake_sleep)
    with mhe.HeavyLock():
        assert lock.is_dir()
    assert sleeps == [120]
    # released even when the body raises
    with pytest.raises(RuntimeError), mhe.HeavyLock():
        raise RuntimeError("boom")
    assert not lock.exists()


def test_require_frozen_draft_mismatch_and_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = tmp_path / "RULE.md"
    doc.write_text("rule\n=== RESULTS BELOW ===\nignored\n")
    monkeypatch.setattr(mhe, "PREREG", doc)
    sha = mhe.frozen_sha(doc)
    assert mhe.require_frozen(allow_draft=True) == sha  # draft: no equality check
    assert mhe.require_frozen(sha) == sha
    with pytest.raises(SystemExit, match="STOP"):
        mhe.require_frozen("0" * 64)
    doc.write_text("rule edited\n=== RESULTS BELOW ===\nignored\n")
    assert mhe.frozen_sha(doc) != sha  # the hash covers everything above the marker only


def test_main_refusal_paths() -> None:
    with pytest.raises(SystemExit, match="--sha"):
        mhe.main(["gate", "--doc", "docs/prereg/OTHER.md"])
    with pytest.raises(SystemExit, match="gate stage only"):
        mhe.main(["run", "--allow-draft"])


# ------------------------------------------------------------------------------ data loading


def test_game_prob_table_joins_oof_probs_and_as_of_margin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mhe, "ODDS_DIR", tmp_path)
    for s, ids in ((2023, ["g1", "g2"]), (2024, ["g3"])):
        pl.DataFrame(
            {"game_id": ids, "p": [0.6] * len(ids), "other": [1] * len(ids)}
        ).write_parquet(tmp_path / f"game_rows_{s}.parquet")
    frame = pl.DataFrame(
        {
            "game_id": ["g1", "g1", "g2", "g2", "g22"],
            "exp_margin": [4.0, -4.0, 2.0, -2.0, 1.0],
            "is_home_f": [1, 0, 1, 0, 1],
        }
    )
    t = mhe.game_prob_table(frame).sort("game_id")
    assert t["game_id"].to_list() == ["g1", "g2", "g22"]
    # exp_margin is signed to the home side: home 4 and away -4 * (-1) => home margin 4 for both rows
    assert t["mu_elo"].to_list() == [4.0, 2.0, 1.0]
    assert t["p_home"].to_list() == [0.6, 0.6, None]  # g22 has no stored probability


def test_load_tables_is_read_only_and_bounded_by_max_season(tmp_path: Path) -> None:
    db = tmp_path / "t.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        "create table games (game_id varchar, season int, home_team int, away_team int, home_pts int, away_pts int)"
    )
    con.execute("insert into games values ('a', 2024, 1, 2, 100, 90), ('b', 2025, 1, 2, 100, 90)")
    con.execute(
        "create table stints (game_id varchar, team_id int, period int, start_clock double, end_clock double, players varchar)"
    )
    con.execute("insert into stints values ('a', 1, 1, 720, 600, 'x'), ('b', 1, 1, 720, 600, 'x')")
    con.execute(
        "create table possessions (game_id varchar, poss_idx int, period int, clock_start double, clock_end double, off_team int, score_diff int)"
    )
    con.execute(
        "insert into possessions values ('a', 0, 1, 720, 700, 1, 0), ('b', 0, 1, 720, 700, 1, 0)"
    )
    con.close()
    tabs = mhe.load_tables(str(db))
    assert set(tabs) == {"games", "stints", "poss"}
    # season 2025 (the holdout) never leaves the database
    assert tabs["games"]["game_id"].to_list() == ["a"]
    assert tabs["stints"]["game_id"].to_list() == ["a"]
    assert tabs["poss"]["game_id"].to_list() == ["a"]


def test_date_and_game_id_casts() -> None:
    import datetime as dt

    d = pl.Series([dt.date(1970, 1, 2), dt.date(1970, 1, 11)])
    assert mhe._date_int(d).tolist() == [1, 10]
    assert mhe._gid_int(pl.Series(["22300001", "22300002"])).tolist() == [22300001, 22300002]


def test_returning_3plus_flags_gaps_of_three_or_more_games_within_a_season() -> None:
    import datetime as dt

    rows = [
        # player 1: games 1, 2, then 6 (misses 3) in the same season, then season change
        (0, 1, 2024, dt.date(2024, 1, 1), "a", 1),
        (1, 1, 2024, dt.date(2024, 1, 3), "b", 2),
        (2, 1, 2024, dt.date(2024, 1, 10), "c", 6),
        (3, 1, 2025, dt.date(2025, 1, 10), "d", 20),  # new season: not flagged
        # player 2: misses exactly two games: not flagged
        (4, 2, 2024, dt.date(2024, 1, 1), "a", 1),
        (5, 2, 2024, dt.date(2024, 1, 5), "d2", 4),
    ]
    f = pl.DataFrame(
        rows,
        schema=["rid", "player_id", "season", "game_date", "game_id", "team_game_no"],
        orient="row",
    )
    out = mhe.returning_3plus(f)
    assert out.tolist() == [False, False, True, False, False, False]


def test_missingness_audit_null_rates_by_minutes_bucket() -> None:
    n = 40
    mins = np.array([5.0] * 10 + [15.0] * 10 + [25.0] * 10 + [35.0] * 10)
    # vac_min is null for every low-minute player and no one else: a missingness asymmetry
    vac = [None] * 10 + [1.0] * 30
    sf = pl.DataFrame(
        {
            "rid": list(range(n)),
            "game_id": ["g1"] * 20 + ["g2"] * 20,
            "minutes": mins,
            "vac_min": vac,
            "min10": [1.0] * n,
            "starter10": [0.0] * n,
            "pf": [2.0] * n,
        },
        schema_overrides={"vac_min": pl.Float64},
    )
    gp = pl.DataFrame(
        {"game_id": ["g1", "g2"], "p_home": [0.6, None], "mu_elo": [1.0, float("nan")]}
    )
    a = {
        "asof": SimpleNamespace(k_habit=np.ones(n, dtype=int), fden40=np.ones(n)),
        "margin": np.array([[1.0, -2.0], [0.0, 0.0]]),
        "gidx": {"g1": 0, "g2": 1},
        "y": np.ones((n, 3)),
        "parsed": np.ones(n, dtype=bool),
    }
    audit = mhe.missingness({}, a, gp, sf)
    by = {(r["col"], r["bucket"]): r for r in audit}
    assert len(audit) == 10 * len(mhe.BUCKETS)
    assert by[("vac_min", "[0,10)")]["null_rate"] == 1.0
    assert by[("vac_min", "[10,20)")]["null_rate"] == 0.0
    assert by[("vac_min", "[0,10)")]["n"] == 10
    # g2 has neither a probability nor a margin path => those inputs are "missing" for g2 rows only
    assert by[("pretip_margin_mu", "[0,10)")]["null_rate"] == 0.0
    assert by[("pretip_margin_mu", "[30,inf)")]["null_rate"] == 1.0
    assert by[("actual_margin_path", "[30,inf)")]["null_rate"] == 1.0
    gaps = mhe.max_gap_pp(audit)
    assert gaps["vac_min"] == pytest.approx(100.0)
    assert gaps["pf"] == 0.0 and gaps["habit_history(k=0)"] == 0.0


# ------------------------------------------------------------------------------ M2 reproduction


def _quantiles(mu: np.ndarray, sd: float) -> np.ndarray:
    return np.asarray(mu[:, None] + sd * sps.norm.ppf(CRPS_TAUS)[None, :])


def test_m2_cached_roundtrip_and_reproduction_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mhe, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(mhe, "STORED", tmp_path / "stored")
    (tmp_path / "stored").mkdir()
    rng = np.random.default_rng(1)
    n = 30
    seasons = np.array([2023] * 15 + [2024] * 15)
    y = rng.uniform(5, 40, n)
    sf = pl.DataFrame(
        {
            "rid": np.arange(n),
            "game_id": [f"g{i // 10}" for i in range(n)],
            "player_id": np.arange(n),
            "season": seasons,
            "minutes": y,
        }
    )
    q = _quantiles(y + rng.normal(0, 3, n), 4.0)
    stored_crps = score_minutes(q, y)["crps"]
    np.savez(
        tmp_path / "stored" / "minutes_scores.npz",
        row_gid=sf["game_id"].to_numpy(),
        row_pid=sf["player_id"].to_numpy(),
        M2_crps=stored_crps,
    )
    m2 = {"q": q, "rid": np.arange(n), "mv2": pl.DataFrame({"game_id": ["g0"], "x": [1.0]})}
    rep = mhe.m2_reproduction(sf, m2)
    assert rep["n_matched"] == rep["n_stored"] == n
    assert rep["max_abs_row_diff"] < 1e-9 and rep["ok"] is True
    assert rep["abs_diff_2023"] < 1e-9
    # a drifted M2 breaks the gate (season-2023 tolerance is 1e-6)
    drifted = dict(m2, q=q + 0.5)
    assert mhe.m2_reproduction(sf, drifted)["ok"] is False
    # a missing stored row (fewer matched than stored) also fails
    short = dict(m2, q=q[:-1], rid=np.arange(n - 1))
    assert mhe.m2_reproduction(sf, short)["ok"] is False
    # cache: miss => run_m2 is used and its output stored; hit => same arrays without rerunning
    calls: list[int] = []

    def fake_run(world: Any, frame: Any) -> dict[str, Any]:
        calls.append(1)
        return m2

    monkeypatch.setattr(mhe, "run_m2", fake_run)
    first = mhe.m2_cached({}, sf)
    again = mhe.m2_cached({}, sf)
    assert calls == [1]
    assert np.array_equal(first["q"], again["q"]) and np.array_equal(again["rid"], np.arange(n))
    assert again["mv2"].equals(m2["mv2"])


# ------------------------------------------------------------------------------ analysis

N_G = 24  # games per season
PER_G = 10


def _analysis_inputs() -> tuple[
    pl.DataFrame, dict[str, Any], list[dict[str, Any]], pl.DataFrame, np.ndarray
]:
    rng = np.random.default_rng(5)
    rows = []
    rid = 0
    for s in mhe.SEASONS:
        for gi in range(N_G):
            gid = f"{s}{gi:03d}"
            for k in range(PER_G):
                rows.append(
                    {
                        "rid": rid,
                        "game_id": gid,
                        "season": s,
                        "is_home_f": k % 2,
                        "starter10": 1.0 if k < 5 else 0.0,
                        "team_game_no": gi,
                        "n_prior": 5 + gi * 2,
                        "has_report": k % 3 == 0,
                        "n_out_rot": k % 2,
                        "minutes": float(np.clip(rng.normal(22 if k < 5 else 12, 6), 0, 48)),
                    }
                )
                rid += 1
    sf = pl.DataFrame(rows).with_columns(
        pl.col("has_report").cast(pl.Int64), pl.col("n_out_rot").cast(pl.Int64)
    )
    n = sf.height
    y = sf["minutes"].to_numpy()
    q_m2 = _quantiles(y + rng.normal(0, 6, n), 6.0)
    m2 = {"q": q_m2, "rid": sf["rid"].to_numpy()}
    # the last two blocks are scored; q1 is full-length per block, q0/q2 are test rows only
    cut = n // 2
    blocks = [np.arange(0, cut), np.arange(cut, n)]
    recs = []
    for b in blocks:
        test = np.ones(len(b), dtype=bool)
        test[:3] = False  # a few training rows in each block
        q1 = _quantiles(y[b] + rng.normal(0, 2, len(b)), 3.0)  # sharp and centred: best arm
        q0 = _quantiles(y[b][test] + rng.normal(0, 5, int(test.sum())), 5.0)
        q2 = _quantiles(y[b][test] + rng.normal(0, 2.5, int(test.sum())), 3.5)
        recs.append({"rid": b, "test": test, "q1": q1, "q0": q0, "q2": q2})
    gp = pl.DataFrame(
        {
            "game_id": sf["game_id"].unique(maintain_order=True),
            "p_home": rng.uniform(0.2, 0.8, 2 * N_G),
            "mu_elo": 0.0,
        }
    )
    ret3 = rng.random(n) < 0.1
    return sf, m2, recs, gp, ret3


def test_analyze_minutes_scores_arms_against_m2_on_aligned_rows() -> None:
    sf, m2, recs, gp, ret3 = _analysis_inputs()
    res = mhe.analyze_minutes(sf, m2, recs, gp, ret3)
    tab = {(r["season"], r["arm"]): r for r in res["table"]}
    assert set(tab) == {(s, a) for s in mhe.SEASONS for a in ("M2", "H1", "H0", "H2")}
    for s in mhe.SEASONS:
        # M2 is the reference: exactly zero delta, and the row/game counts add up
        assert tab[(s, "M2")]["dcrps"] == 0.0
        assert tab[(s, "M2")]["n_games"] == N_G
    assert sum(tab[(s, "H1")]["n_rows"] for s in mhe.SEASONS) == sum(
        int(r["test"].sum()) for r in recs
    )
    # independent recomputation of H1 - M2 on the pooled test rows
    rid_h = np.concatenate([r["rid"][r["test"]] for r in recs])
    y = sf["minutes"].to_numpy()[rid_h]
    q1 = np.concatenate([r["q1"][r["test"]] for r in recs])
    h1 = score_minutes(q1, y)["crps"]
    m2c = score_minutes(m2["q"][rid_h], y)["crps"]
    seasons = sf["season"].to_numpy()[rid_h]
    for s in mhe.SEASONS:
        want = float((h1 - m2c)[seasons == s].mean())
        got = tab[(s, "H1")]
        assert got["dcrps"] == pytest.approx(want)
        assert got["ci_lo"] <= got["dcrps"] <= got["ci_hi"]
        assert got["dcrps"] < 0 and got["ci_hi"] < 0  # the sharp, centred arm beats the noisy M2
        assert 0 < got["cov80"] < 1
    # pairs, slices and the Elo-bucket mechanism table
    assert {r["pair"] for r in res["pairs"]} == {"H1-H0", "H2-H1"}
    h1h0 = next(r for r in res["pairs"] if r["pair"] == "H1-H0" and r["season"] == 2023)
    assert h1h0["dcrps"] < 0  # H1 beats the unconditional-hazard comparator
    names = {r["slice"] for r in res["slices"]}
    assert {
        "all",
        "starter",
        "bench",
        "first15",
        "cold(n_prior<20)",
        "teammate_out",
        "returning_3plus",
    } <= names
    allrow = next(
        r for r in res["slices"] if r["slice"] == "all" and r["arm"] == "H1" and r["season"] == 2023
    )
    assert allrow["n"] == tab[(2023, "H1")]["n_rows"]
    st = next(
        r
        for r in res["slices"]
        if r["slice"] == "starter" and r["arm"] == "H1" and r["season"] == 2023
    )
    be = next(
        r
        for r in res["slices"]
        if r["slice"] == "bench" and r["arm"] == "H1" and r["season"] == 2023
    )
    assert st["n"] + be["n"] == allrow["n"]
    for s in mhe.SEASONS:
        mech_n = sum(r["n"] for r in res["mechanism"] if r["season"] == s and r["arm"] == "H1")
        assert mech_n >= tab[(s, "H1")]["n_rows"]  # buckets overlap only at the shared edges


def test_analyze_minutes_refuses_misaligned_rows() -> None:
    sf, m2, recs, gp, ret3 = _analysis_inputs()
    sf_missing = sf.filter(pl.col("rid") != int(recs[0]["rid"][10]))
    with pytest.raises(AssertionError, match="not aligned"):
        mhe.analyze_minutes(sf_missing, m2, recs, gp, ret3)


def _passing_table() -> dict[str, Any]:
    t, pairs = [], []
    for s in mhe.SEASONS:
        t.append(
            {
                "season": s,
                "arm": "H1",
                "dcrps": -0.2,
                "ci_hi": -0.1,
                "pit_q10": 0.10,
                "pit_q90": 0.90,
                "bias": 0.1,
            }
        )
        pairs.append({"season": s, "pair": "H1-H0", "dcrps": -0.1})
    return {"table": t, "pairs": pairs, "slices": []}


def test_minutes_verdict_requires_every_frozen_check() -> None:
    res = _passing_table()
    v = mhe.minutes_verdict(res)
    assert all(v[str(s)]["pass"] for s in mhe.SEASONS)
    # each check can fail independently
    for field, bad, check in (
        ("dcrps", -0.05, "crps_floor"),  # smaller than the practical floor
        ("ci_hi", 0.01, "ci_upper_lt_0"),
        ("pit_q10", 0.15, "pit_q10"),
        ("pit_q90", 0.85, "pit_q90"),
        ("bias", 0.9, "bias"),
    ):
        r = _passing_table()
        r["table"][0][field] = bad
        out = mhe.minutes_verdict(r)["2023"]
        assert out["checks"][check] is False and out["pass"] is False
        assert mhe.minutes_verdict(r)["2024"]["pass"] is True  # other season untouched
    r = _passing_table()
    r["pairs"][0]["dcrps"] = -0.01  # does not beat H0 by the frozen margin
    assert mhe.minutes_verdict(r)["2023"]["checks"]["beats_H0"] is False
    # a harmful slice only counts with enough rows
    r = _passing_table()
    r["slices"] = [
        {"season": 2023, "slice": "bench", "arm": "H1", "n": mhe.MIN_SLICE_N, "dcrps": 0.2},
        {"season": 2023, "slice": "tiny", "arm": "H1", "n": mhe.MIN_SLICE_N - 1, "dcrps": 5.0},
        {"season": 2023, "slice": "ok", "arm": "H0", "n": 1000, "dcrps": 5.0},
    ]
    out = mhe.minutes_verdict(r)["2023"]
    assert out["bad_slices"] == ["bench"] and out["checks"]["slices"] is False


def _props_pr(gain: float, rng: np.random.Generator) -> dict[str, dict[str, np.ndarray]]:
    n = 600
    gid = np.repeat(np.arange(n // 5), 5)
    base = {
        "gid": gid,
        "pid": np.arange(n),
        "season": np.where(np.arange(n) < n // 2, 2023, 2024),
    }
    pr = {}
    for stat in mhe.PROP_STATS:
        c0 = rng.uniform(1.0, 2.0, n)
        pr[f"{stat}/A0"] = {**base, "crps_int": c0}
        pr[f"{stat}/A_M2"] = {**base, "crps_int": c0 - 0.0005 + rng.normal(0, 0.01, n)}
        pr[f"{stat}/A1"] = {**base, "crps_int": c0 - gain + rng.normal(0, 0.01, n)}
    return pr


def test_analyze_props_deltas_bh_and_verdict() -> None:
    rng = np.random.default_rng(3)
    res = mhe.analyze_props(_props_pr(0.05, rng))
    rows = res["rows"]
    assert len(rows) == len(mhe.PROP_STATS) * len(mhe.SEASONS) * 4
    a1 = [r for r in rows if r["arm"] == "A1"]
    for r in a1:
        assert r["dcrps"] == pytest.approx(-0.05, abs=0.005)
        assert r["ci_hi"] < 0 and r["p"] < 0.05 and r["p_bh"] >= r["p"] - 1e-12
        assert r["n_rows"] == 300 and r["n_games"] == 60
    assert all(np.isnan(r["p"]) for r in rows if r["arm"] == "A0")
    assert all(v["pass"] for v in res["verdict"].values())
    assert set(res["verdict"]) == {f"{st}/{s}" for st in mhe.PROP_STATS for s in mhe.SEASONS}
    # no gain => no stat passes the floor / BH / beat-A_M2 checks
    flat = mhe.analyze_props(_props_pr(0.0, np.random.default_rng(3)))
    assert not any(v["pass"] for v in flat["verdict"].values())
    assert all(v["checks"]["floor"] is False for v in flat["verdict"].values())


def test_analyze_props_rejects_misaligned_arms() -> None:
    pr = _props_pr(0.05, np.random.default_rng(3))
    pr["pts/A1"]["pid"] = pr["pts/A1"]["pid"][::-1].copy()
    with pytest.raises(ValueError, match="row-aligned"):
        mhe.analyze_props(pr)


def test_run_props_resumes_from_checkpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rng = np.random.default_rng(0)
    pr = _props_pr(0.05, rng)
    ck = tmp_path / "ck"
    ck.mkdir()
    for key, arrs in pr.items():
        stat, arm = key.split("/")
        np.savez(ck / f"props_{stat}_{arm}.npz", **arrs)  # type: ignore[arg-type]

    def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("props_walk must not run when every checkpoint exists")

    monkeypatch.setattr(mhe, "props_walk", boom)
    frame = pl.DataFrame({"game_id": ["g"], "player_id": [1]})
    out = mhe.run_props(
        frame,
        None,
        frame.with_columns(pl.lit(1.0).alias("m")),
        frame.with_columns(pl.lit(1.0).alias("m")),
        ck,
    )
    assert set(out) == set(pr)
    assert np.array_equal(out["pts/A1"]["crps_int"], pr["pts/A1"]["crps_int"])
    # a missing checkpoint is computed once and then stored
    (ck / "props_pts_A0.npz").unlink()
    monkeypatch.setattr(
        mhe,
        "props_walk",
        lambda fr, stat, extra, cfg: {**pr["pts/A0"], "y": pr["pts/A0"]["crps_int"]},
    )
    out2 = mhe.run_props(
        frame,
        None,
        frame.with_columns(pl.lit(1.0).alias("m")),
        frame.with_columns(pl.lit(1.0).alias("m")),
        ck,
    )
    assert (ck / "props_pts_A0.npz").exists()
    assert np.array_equal(out2["pts/A0"]["crps_int"], pr["pts/A0"]["crps_int"])


# ------------------------------------------------------------------------------ verdict + report


def test_overall_verdict_branches() -> None:
    def mv(ok: bool) -> dict[str, Any]:
        return {str(s): {"pass": ok} for s in mhe.SEASONS}

    def pv(stats_ok: set[str]) -> dict[str, Any]:
        return {f"{st}/{s}": {"pass": st in stats_ok} for st in mhe.PROP_STATS for s in mhe.SEASONS}

    assert (
        mhe.overall(mv(True), pv({"pts", "reb"}))
        == "H1 PASS (minutes) and H2 PASS for ['pts', 'reb']"
    )
    assert "H2 FAIL" in mhe.overall(mv(True), pv(set()))
    assert "H1 FAIL" in mhe.overall(mv(False), pv(set(mhe.PROP_STATS)))
    # a stat must pass in both seasons
    mixed = pv({"pts"})
    mixed["pts/2024"]["pass"] = False
    assert "H2 FAIL" in mhe.overall(mv(True), mixed)


def _results() -> dict[str, Any]:
    sf, m2, recs, gp, ret3 = _analysis_inputs()
    mres = mhe.analyze_minutes(sf, m2, recs, gp, ret3)
    mres["verdict"] = mhe.minutes_verdict(mres)
    pres = mhe.analyze_props(_props_pr(0.05, np.random.default_rng(3)))
    gate = {
        "1_stints_coverage": {"ok": True},
        "5a_missingness_gap": {
            "ok": False,
            "detail": {"max_gap_pp_by_col": {"vac_min": 3.5, "pf": 0.1}},
            "audit": [
                {"col": "vac_min", "bucket": "[0,10)", "n": 10, "null_rate": 0.05},
                {"col": "pf", "bucket": "[0,10)", "n": 10, "null_rate": 0.0},
                {"col": "min10", "bucket": "[0,10)", "n": 7, "null_rate": 0.0},
            ],
        },
        "5b_planted": {"ok": True, "detail": {}},
        "5c_m2_reproduces": {"ok": False, "detail": {}},
        "all_ok": False,
    }
    res = {
        "sha256": "ab" * 32,
        "git_sha": "deadbeef",
        "seeds": {"sim": 1},
        "gate": gate,
        "minutes": mres,
        "props": pres,
    }
    res["verdict"] = mhe.overall(mres["verdict"], pres["verdict"])
    return res


def test_render_reports_every_section_with_numbers() -> None:
    res = _results()
    md = mhe.render(res)
    assert md.endswith("\n")
    for needle in (
        "# MINUTES_HAZARD results",
        "## Gate (minimum data checks 1-5)",
        "| 1_stints_coverage | True |",
        "| 5c_m2_reproduces | False |",
        "Mechanism by Elo bucket",
        "H1 minutes verdict:",
        "H2 props verdict:",
        f"Overall: {res['verdict']}",
        "ab" * 32,
    ):
        assert needle in md
    n_h1 = sum(1 for r in res["minutes"]["table"] if r["arm"] == "H1")
    assert md.count("| H1 |") >= n_h1
    assert "pts/2023: pass=" in md
    assert "nan" not in md.split("## Props")[0].lower()


def test_ledger_rows_one_line_per_claim_with_rule_prefix() -> None:
    res = _results()
    txt = mhe.ledger_rows(res)
    lines = txt.strip().splitlines()
    assert lines[0].startswith("| id |") and f"rule {'ab' * 4}" in lines[2]
    n_min = len(mhe.SEASONS) * (1 + 2)  # H1 vs M2 + two pairs per season
    n_props = len(mhe.PROP_STATS) * len(mhe.SEASONS)
    body = [ln for ln in lines if ln.startswith("| T###")]
    assert len(body) == n_min + n_props
    assert lines[-1] == f"Overall: {res['verdict']}"
    assert sum("pass" in ln for ln in body if "props" in ln) == n_props


def test_write_gate_stop_records_failed_checks_and_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mhe, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(mhe, "REPORT_PATH", tmp_path / "report.md")
    doc = tmp_path / "RULE.md"
    doc.write_text("rule\n")
    monkeypatch.setattr(mhe, "PREREG", doc)
    monkeypatch.setattr(mhe, "git_sha", lambda: "abc123")
    gate = _results()["gate"]
    mhe.write_gate_stop(gate)
    res = json.loads((tmp_path / "out" / "results.json").read_text())
    assert res["status"] == "STOPPED_AT_GATE" and res["git_sha"] == "abc123"
    assert res["max_gap_pp_by_col"] == {"vac_min": 3.5, "pf": 0.1}
    assert {t["test"]: t["ok"] for t in res["tests"]}["5c_m2_reproduces"] is False
    md = (tmp_path / "report.md").read_text()
    assert "STOPPED AT THE GATE" in md and "vac_min | 3.50" in md
    assert "| vac_min | [0,10) | 10 | 0.0500 |" in md
    assert "| pf | [0,10)" not in md  # only inputs with a non-zero null rate are listed
    ledger = (tmp_path / "out" / "ledger_rows.md").read_text()
    assert "vac_min 3.50pp" in ledger and "STOP: no fit, no model result" in ledger
    assert "7 eligible rows" in ledger
    assert "5b planted ok=True" in ledger and "5c M2 reproduces ok=False" in ledger


def test_git_sha_returns_hash_or_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    sha = mhe.git_sha()
    assert sha == "unknown" or len(sha) == 40
    monkeypatch.setattr(
        subprocess, "check_output", lambda *a, **k: (_ for _ in ()).throw(OSError("x"))
    )
    assert mhe.git_sha() == "unknown"
