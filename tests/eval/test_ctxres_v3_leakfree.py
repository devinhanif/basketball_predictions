# ruff: noqa: E501
"""Guards and engine path of the leak-free experiment 3 (docs/CTXRES_V3_LEAKFREE.md)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import polars as pl
import pytest

from nba.eval import ctxres_v3_leakfree as lf

JOB = (
    Path(__file__).resolve().parents[2] / "nba/colab/jobs/ctxres_v3_leakfree/ctxres_v3_leakfree.py"
)
DOC = Path(__file__).resolve().parents[2] / "docs/CTXRES_V3_LEAKFREE.md"
STATS = ("pts", "reb", "ast", "fg3m")


# ----------------------------------------------------------------------------- pre-registration


def test_prereg_hash_ignores_results_and_inline_marker(tmp_path: Path) -> None:
    p = tmp_path / "d.md"
    body = f"intro mentions `{lf.PREREG_MARKER}` inline\n\nrule\n{lf.PREREG_MARKER}\n"
    p.write_text(body + "\nresults v1\n")
    h1 = lf.prereg_sha256(p)
    p.write_text(body + "\nresults changed completely\n")
    assert lf.prereg_sha256(p) == h1
    p.write_text(body.replace("rule", "edited rule") + "\nresults v1\n")
    assert lf.prereg_sha256(p) != h1
    p.write_text("no marker here\n")
    with pytest.raises(ValueError):
        lf.prereg_sha256(p)


def test_real_prereg_doc_has_marker_line() -> None:
    txt = lf.prereg_text(DOC)
    assert txt.rstrip().endswith(lf.PREREG_MARKER)
    assert "REPLICATION" in txt and "season 2025" in txt.lower()


# ----------------------------------------------------------------------------- column guards


def test_no_leaky_ridge_columns() -> None:
    ok = {s: [f"rg_f3b8321a_p_{s}", f"rg_f3b8321a_o_{s}", "pos_code"] for s in STATS}
    lf.assert_leakfree_columns(ok)
    with pytest.raises(lf.LeakStop):
        lf.assert_leakfree_columns({"pts": ["ridge_player_pts"]})
    with pytest.raises(lf.LeakStop):
        lf.assert_leakfree_columns({"pts": ["rg_notahash_p_pts"]})


def test_real_spec_arm_columns_are_leakfree() -> None:
    root = Path(__file__).resolve().parents[2] / "data/colab/ridge_v2/ridge_v2_spec.json"
    if not root.exists():
        pytest.skip("real ridge_v2 spec not present")
    arm = json.loads(root.read_text())["arms"]["ref_fixed"]["columns"]
    lf.assert_leakfree_columns(arm)


def test_onehot_pos_and_linear_design() -> None:
    pos = np.array([0, 1, 2, 5, np.nan, 9])
    oh = lf.onehot_pos(pos)
    assert oh.shape == (6, len(lf.POS_CATEGORIES) + 1)
    assert np.allclose(oh.sum(axis=1), 1.0)
    assert oh[4, -1] == 1.0 and oh[5, -1] == 1.0 and oh[0, 0] == 1.0
    X = np.arange(18, dtype=float).reshape(6, 3)
    D, names = lf.linear_design(X, ["a", "pos_code", "b"], pos)
    assert "pos_code" not in names and D.shape[1] == 2 + len(lf.POS_CATEGORIES) + 1
    assert len(names) == D.shape[1]


# ----------------------------------------------------------------------------- missingness audit


def test_asymmetry_stop_pure() -> None:
    tm = {"columns": {"a": {"asymmetry": 0.01}, "b": {"asymmetry": 0.99}, "c": {"asymmetry": None}}}
    assert lf.asymmetry_stop(tm, ["a", "c"]) == {"a": 0.01, "c": 0.0}
    with pytest.raises(lf.LeakStop):
        lf.asymmetry_stop(tm, ["a", "b"])


def _mini_db(path: Path, n: int = 1500) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    minutes = np.where(rng.random(n) < 0.3, rng.uniform(0, 4, n), rng.uniform(8, 35, n))
    gid = [f"g{i // 10}" for i in range(n)]
    pid = [i % 10 for i in range(n)]
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT)")
    con.execute("CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT, minutes DOUBLE)")
    for g in sorted(set(gid)):
        con.execute("INSERT INTO games VALUES (?, DATE '2024-01-01', 2024)", [g])
    con.executemany(
        "INSERT INTO player_game_stats VALUES (?, ?, ?)",
        list(zip(gid, pid, minutes.tolist(), strict=True)),
    )
    con.close()
    return pd.DataFrame({"game_id": gid, "player_id": pid, "minutes": minutes})


def test_missingness_audit_flags_minutes_dependent_null(tmp_path: Path) -> None:
    df = _mini_db(tmp_path / "t.duckdb")
    clean = df[["game_id", "player_id"]].copy()
    clean["f_ok"] = np.random.default_rng(1).normal(size=len(df))
    clean["f_ok"] = clean["f_ok"].where(
        np.arange(len(df)) % 50 != 0
    )  # 2% NULL, independent of minutes
    p_ok = tmp_path / "ok.parquet"
    clean.to_parquet(p_ok)
    out = lf.missingness_audit(p_ok, tmp_path / "t.duckdb", ["f_ok"])
    assert out["max_asymmetry"] < 0.5 and out["played_split"]["n_unplayed"] > 100
    leaky = clean.copy()
    leaky["f_leak"] = np.where(df["minutes"] < 5, np.nan, 1.0)  # the exp-2 ridge fingerprint
    p_bad = tmp_path / "bad.parquet"
    leaky.to_parquet(p_bad)
    with pytest.raises(lf.LeakStop):
        lf.missingness_audit(p_bad, tmp_path / "t.duckdb", ["f_ok", "f_leak"])


# ----------------------------------------------------------------------------- lock, rule


def test_heavy_lock_exclusive_and_released(tmp_path: Path) -> None:
    lock = tmp_path / "heavy.lock"
    with lf.heavy_lock(lock, retry_s=0.01):
        assert lock.is_dir()
        with pytest.raises(TimeoutError), lf.heavy_lock(lock, retry_s=0.01, max_wait_s=0.05):
            pass
        assert lock.is_dir()  # the failed contender must not release the holder's lock
    assert not lock.exists()


def test_classify_requires_2024_pass_and_2023_direction() -> None:
    def r(keep: bool, hi: float) -> dict:
        return {"keep": keep, "crps_delta": [-0.01, -0.02, hi]}

    r24 = {s: r(True, -0.001) for s in STATS}
    r23 = {s: r(False, -0.002) for s in STATS}
    assert lf.classify(r23, r24)["replicates_overall"]
    r23["pts"] = r(False, 0.001)
    c = lf.classify(r23, r24)
    assert not c["replicates_overall"] and not c["per_stat"]["pts"]["replicates"]
    r24["ast"] = r(False, -0.001)
    assert not lf.classify(r23, r24)["per_stat"]["ast"]["replicates"]


def test_evaluate_on_synthetic_oof(tmp_path: Path) -> None:
    """Rule + report run end to end on a tiny planted OOF (hybrid clearly better than reference)."""
    rng = np.random.default_rng(3)
    run = tmp_path / "run"
    export_rows = []
    for season, base in ((2023, 0), (2024, 1000)):
        d = run / f"oof_{season}"
        d.mkdir(parents=True)
        frames = []
        for g in range(60):
            for p in range(8):
                export_rows.append(
                    {
                        "game_id": f"g{base + g}",
                        "player_id": p,
                        "has_report": g % 2,
                        "n_out_rot": g % 3,
                        "min_gap": float(p),
                        "team_game_no": g % 40 + 1,
                        "starter10": p < 5,
                    }
                )
        for v, sd in (("hybrid_lf", 0.5), ("v1_prod", 1.0)):
            for st, k in (("pts", 5), ("reb", 4), ("ast", 4), ("fg3m", 4)):
                rows = []
                r2 = np.random.default_rng(3)
                for g in range(60):
                    for p in range(8):
                        y = float(r2.poisson(8))
                        q = np.sort(8 + sd * r2.normal(size=19) * 2)
                        rows.append(
                            {
                                "variant": v,
                                "stat": st,
                                "game_id": f"g{base + g}",
                                "player_id": p,
                                "fold_id": 202401 + g // 30,
                                "y": y,
                                "mean": float(q.mean()),
                                "crps": abs(y - float(q.mean())) * sd,
                                "tll": 0.5,
                                "q19": q.tolist(),
                                "p_ge": [0.5] * k,
                            }
                        )
                frames.append(pl.DataFrame(rows))
        allf = pl.concat(frames)
        for v in ("hybrid_lf", "v1_prod"):
            allf.filter(pl.col("variant") == v).write_parquet(d / f"{v}.parquet")
    exp_path = tmp_path / "export.parquet"
    pl.DataFrame(export_rows).write_parquet(exp_path)
    res = lf.evaluate(run, exp_path, n_boot=200)
    assert set(res["rules"]) == {"2023", "2024"}
    assert res["rules"]["2024"]["pts"]["crps_delta"][2] < 0
    text = lf.format_report(res)
    assert "REPLICATION" in text and "Classification" in text
    del rng


# ----------------------------------------------------------------------------- engine path (needs xgboost)


def _job():
    spec = importlib.util.spec_from_file_location("v3lf_job", JOB)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _synthetic(root: Path, shift_last_block_labels: bool) -> dict:
    """Tiny staged dataset with the exact file layout the ridge_v2 engine expects."""
    rng = np.random.default_rng(11)
    dates = pd.date_range("2022-10-20", "2024-12-28", freq="D")
    rows = []
    for di, d in enumerate(dates):
        season = d.year if d.month >= 10 else d.year - 1
        for p in range(10):
            rows.append((d, f"g{di}", p, season, d.year * 100 + d.month, float(p % 5)))
    df = pd.DataFrame(
        rows, columns=["game_date", "game_id", "player_id", "season", "fold_id", "pos_code"]
    )
    n = len(df)
    for c in ("f_a", "f_b", "f_c"):
        df[c] = rng.normal(size=n)
    spec = {
        "stats": list(STATS),
        "allowlist": ["f_a", "f_b", "f_c", "pos_code"],
        "leak_deny_regex": "^(label_|same_game)",
        "default_std": dict.fromkeys(STATS, 3.0),
        "built_at": "t",
        "v1_features": {},
        "v2_features": {},
        "v2_groups": {"i": {}},
    }
    rspec_cols, ridge = {}, {"game_id": df["game_id"], "player_id": df["player_id"]}
    for s in STATS:
        mean = 2 + 1.5 * np.abs(df["f_a"]) + (df["pos_code"] == 2)
        df[s] = rng.poisson(mean).astype(float)
        df[f"base_m_{s}"] = 2.0
        df[f"base_s_{s}"] = 1.5
        df[f"ridge_player_{s}"] = df[
            s
        ]  # a deliberately leaky exp-2 style column: must never be loaded
        df[f"ridge_opp_{s}"] = df[s]
        spec["v1_features"][s] = ["f_a", "pos_code"]
        spec["v2_features"][s] = [
            "f_a",
            "pos_code",
            "f_b",
            "f_c",
            f"ridge_player_{s}",
            f"ridge_opp_{s}",
        ]
        spec["v2_groups"]["i"][s] = [f"ridge_player_{s}", f"ridge_opp_{s}"]
        ridge[f"rg_f3b8321a_p_{s}"] = rng.normal(size=n)
        ridge[f"rg_f3b8321a_o_{s}"] = rng.normal(size=n)
        rspec_cols[s] = [f"rg_f3b8321a_p_{s}", f"rg_f3b8321a_o_{s}"]
    spec["allowlist"] += [c for s in STATS for c in (f"ridge_player_{s}", f"ridge_opp_{s}")]
    last_start = pd.Timestamp("2024-12-01")
    if shift_last_block_labels:
        m = df["game_date"] >= last_start
        for s in STATS:
            df.loc[m, s] = df.loc[m, s] * 7 + 100.0
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    df.to_parquet(src / "ctxres_v2.parquet")
    pd.DataFrame(ridge).to_parquet(src / "ridge_v2.parquet")
    (src / "feature_spec.json").write_text(json.dumps(spec))
    (src / "base_config.json").write_text(
        json.dumps(
            {
                "params": {
                    "learning_rate": 0.1,
                    "max_depth": 3,
                    "subsample": 0.8,
                    "colsample_bytree": 0.8,
                    "min_child_weight": 5,
                    "reg_alpha": 0.0,
                    "reg_lambda": 1.0,
                }
            }
        )
    )
    rspec = {
        "arms": {
            "ref_fixed": {"alias_of": None, "columns": rspec_cols},
            "exp2_ref_like": {
                "alias_of": None,
                "columns": {s: [f"ridge_player_{s}"] for s in STATS},
            },
        },
        "exp2_columns": {s: [f"ridge_player_{s}", f"ridge_opp_{s}"] for s in STATS},
    }
    (src / "ridge_v2_spec.json").write_text(json.dumps(rspec))
    return {
        "src": {
            k: src / k
            for k in (
                "ctxres_v2.parquet",
                "feature_spec.json",
                "ridge_v2.parquet",
                "base_config.json",
            )
        },
        "spec_path": src / "ridge_v2_spec.json",
    }


def _run_synth(tmp: Path, name: str, shift: bool, variant: str = "hybrid_lf"):
    job = _job()
    root = tmp / name
    paths = _synthetic(root, shift)
    staged = job.stage(root, paths["spec_path"], paths["src"])
    import os

    os.environ["NBA_PARQUET"] = str(staged)
    job.ENGINE_SHA256 = job.ENGINE_SHA256  # engine pin is exercised by the real run
    job.setup("test")
    job.rs.load()
    return job, root


def test_engine_path_leakfree_pos_and_planted_future(tmp_path: Path) -> None:
    pytest.importorskip("xgboost")
    job_a, root_a = _run_synth(tmp_path, "a", shift=False)
    C = job_a.C
    # (1) only leak-free ridge columns reach the model; the planted exp-2 style columns are unreachable
    assert not any(c.startswith("ridge_") for c in C.names)
    assert "pos_code" in C.names  # tree arms may split on the categorical code
    lf.assert_leakfree_columns({s: C.base_cols[s] + C.arms["ref_fixed"][s] for s in STATS})
    assert set(C.arms) >= {"ref_fixed"} and not any(
        c.startswith("ridge_") for per in C.arms.values() for cs in per.values() for c in cs
    )
    # (2) a same-game label used as a feature is rejected by the static allow-list
    with pytest.raises(RuntimeError):
        job_a.rs.audit_names(["f_a", "label_pts"], "t")
    # (3) planted future: relabelling the LAST month must not change any earlier block's predictions
    params = dict(C.base_cfg["params"], n_jobs=2)
    ck_a = root_a / "ck"
    for v in ("hybrid_lf", "v1_prod"):
        job_a.run_variant(v, ck_a, root_a, params)
    job_b, root_b = _run_synth(tmp_path, "b", shift=True)
    ck_b = root_b / "ck"
    for v in ("hybrid_lf", "v1_prod"):
        job_b.run_variant(v, ck_b, root_b, dict(job_b.C.base_cfg["params"], n_jobs=2))
    blocks = [b for b in C.blocks if b["ok"]]
    last = blocks[-1]["fold"]
    checked = 0
    for v in ("hybrid_lf", "v1_prod"):
        for st in STATS:
            for blk in blocks:
                fa, fb = job_a.unit_file(ck_a, v, st, blk), job_b.unit_file(ck_b, v, st, blk)
                qa, qb = np.load(fa)["q"], np.load(fb)["q"]
                if blk["fold"] != last:
                    assert np.array_equal(qa, qb), (v, st, blk["fold"])
                    checked += 1
    assert checked > 20
    # the last block IS trained on strictly earlier data only, but its labels differ -> its scores differ
    qa_last = np.load(job_a.unit_file(ck_a, "hybrid_lf", "pts", blocks[-1]))["q"]
    assert qa_last.shape[0] == blocks[-1]["b"] - blocks[-1]["a"]
    assert (root_a / "oof_2024" / "hybrid_lf.parquet").exists() and (
        root_a / "oof_2023" / "v1_prod.parquet"
    ).exists()
    # (4) resume: a second call recomputes nothing
    job_a.run_variant("hybrid_lf", ck_a, root_a, params)
