"""Tests for experiment 3 (ctxres_v3 hybrid): frozen config, hybrid assembly, guards, determinism.

Everything runs on synthetic data (no real DB, no GPU, no XGBoost): the job is imported by path and
run end to end in its CPU path with the two XGBoost block functions replaced by deterministic fakes
(the LightGBM ``v1_prod`` arm is real). The default export of ``context_features_v2`` is shown to be
unchanged by the additive ``include_holdout_2025`` flag.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import polars as pl
import pytest
import yaml
from polars.testing import assert_frame_equal

import nba.props.context_features_v2 as cf
from nba.colab.jobs import load_job
from nba.eval import ctxres_v3_eval as ev
from tests.props.test_context_features_v2 import ELO, _league

JOB_DIR = Path(__file__).resolve().parents[2] / "nba" / "colab" / "jobs" / "ctxres_v3_hybrid"
BEST_CONFIG = Path("data/colab/runs/ctxres_v2_sweep/20261008_171446/best_config.json")
STATS = ("pts", "reb", "ast", "fg3m")


def _import(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def job() -> ModuleType:
    return _import(JOB_DIR / "ctxres_v3_hybrid.py", "ctxres_v3_job_under_test")


# ---------------------------------------- frozen / pinned


def test_engine_copy_pinned_and_notebook_in_sync(job: ModuleType) -> None:
    engine = JOB_DIR / "ctxres_v2_engine.py"
    sha = hashlib.sha256(engine.read_bytes()).hexdigest()
    assert sha == job.ENGINE_SHA256 == ev.ENGINE_SHA256
    nb_mod = _import(JOB_DIR / "build_notebook.py", "ctxres_v3_build_nb")
    nb = json.loads((JOB_DIR / "ctxres_v3_hybrid.ipynb").read_text())
    assert nb == nb_mod.build()  # regenerate with build_notebook.py after editing the job
    src = "".join("".join(c["source"]) for c in nb["cells"])
    assert "entry_v3()" in src and "NBA_PREREGISTERED_ID" in src


def test_frozen_config_hash_hybrid_map_and_exp2_source(job: ModuleType) -> None:
    assert job.frozen_sha256() == ev.FROZEN_CONFIG_SHA256
    assert job.HYBRID == ev.HYBRID == job.FROZEN["hybrid"]
    assert job.HYBRID["pts"] == "xgb_v12_quantile"
    assert {job.HYBRID[s] for s in ("reb", "ast", "fg3m")} == {"xgb_v12_poisson_nb"}
    assert (
        job.FROZEN["calibrator"] == ev.CALIBRATOR == "platt_pts_only"
        and job.FROZEN["test_season"] == 2025
    )
    changed = {**job.FROZEN, "p12": {**job.FROZEN["p12"], "max_depth": 4}}
    assert job.frozen_sha256(changed) != job.frozen_sha256()
    if BEST_CONFIG.exists():  # local only: the frozen values equal the exp-2 artifacts
        cfg = json.loads(BEST_CONFIG.read_text())
        assert (
            hashlib.sha256(BEST_CONFIG.read_bytes()).hexdigest()
            == (job.FROZEN["source_best_config_sha256"])
        )
        assert cfg["search_v12"]["params"] == job.FROZEN["p12"]
        bp = cfg["budget_params"]
        for k in ("rounds", "prod_rounds", "min_train", "min_cal"):
            assert bp[k] == job.FROZEN[k]
        assert cfg["seed"] == job.FROZEN["seed"] and cfg["top3_2023"][0] == "xgb_v12_poisson_nb"


def test_job_yaml_is_holdout_job_with_placeholder_id() -> None:
    j = load_job("ctxres_v3_hybrid")
    assert j.touches_holdout is True and bool(str(j.preregistration_id).strip())
    assert {"metrics.json", "oof", "frozen_config.json"} <= set(j.artifacts)
    assert j.inputs[0].path.endswith("ctxres_v3_with2025.parquet")
    assert "--include-holdout-2025" in (j.inputs[0].produce or "")


# ---------------------------------------- hybrid assembly


def _arm(fill: float, n: int = 5) -> dict[str, dict[str, np.ndarray]]:
    return {
        st: {
            "q": np.full((n, 199), fill + i, np.float32),
            "mean": np.full(n, fill + i, np.float32),
            "p": np.full((n, 3), (fill + i) / 100.0, np.float32),
        }
        for i, st in enumerate(STATS)
    }


def test_assemble_hybrid_picks_component_per_stat_and_copies(job: ModuleType) -> None:
    quant, count = _arm(10.0), _arm(20.0)
    h = job.assemble_hybrid({"xgb_v12_quantile": quant, "xgb_v12_poisson_nb": count})
    assert float(h["pts"]["mean"][0]) == 10.0  # quantile arm for pts
    for i, st in enumerate(STATS[1:], start=1):
        assert float(h[st]["mean"][0]) == 20.0 + i  # count arm elsewhere
        assert h[st]["q"].shape == (5, 199)
    h["pts"]["q"][:] = -1.0  # result must not alias the input
    assert float(quant["pts"]["q"][0, 0]) == 10.0
    with pytest.raises(KeyError):
        job.assemble_hybrid({"xgb_v12_quantile": quant})


def _oof_frame(variant: str, shift: float, n: int = 40, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    parts = []
    for st in STATS:
        y = rng.poisson(5, n).astype(float)
        parts.append(
            pl.DataFrame(
                {
                    "variant": variant,
                    "stat": st,
                    "game_id": [f"g{i // 8}" for i in range(n)],
                    "player_id": list(range(n)),
                    "fold_id": 202510,
                    "y": y,
                    "mean": y * 0 + 5 + shift,
                    "crps": np.abs(y - 5 - shift),
                    "tll": np.abs(y - 5 - shift) / 10,
                    "q19": [list(np.linspace(1, 9, 19) + shift) for _ in range(n)],
                    "p_ge": [[0.9, 0.5, 0.1]] * n,
                }
            )
        )
    return pl.concat(parts)


def test_assemble_hybrid_oof_and_consistency_check() -> None:
    quant, count = _oof_frame("xgb_v12_quantile", 0.5), _oof_frame("xgb_v12_poisson_nb", -0.5)
    both = pl.concat([quant, count])
    hyb = ev.assemble_hybrid_oof(both)
    assert set(hyb["variant"].unique()) == {"hybrid_v3"} and hyb.height == 4 * 40
    pts = hyb.filter(pl.col("stat") == "pts").sort("player_id")
    assert pts["mean"][0] == 5.5 and hyb.filter(pl.col("stat") == "reb")["mean"][0] == 4.5
    ok = pl.concat([both, hyb])
    ev.check_hybrid_consistency(ok)  # does not raise
    bad = pl.concat([both, hyb.with_columns(pl.col("crps") + 0.01)])
    with pytest.raises(ev.GuardError):
        ev.check_hybrid_consistency(bad)


# ---------------------------------------- guards (job)


def _meta(tmp: Path, pid: str | None, touches: bool = True) -> Path:
    p = tmp / "job_meta.json"
    p.write_text(json.dumps({"preregistration_id": pid, "touches_holdout": touches}))
    return p


def test_check_prereg_guard(job: ModuleType, tmp_path: Path) -> None:
    good = {"NBA_PREREGISTERED_ID": "abc-1", "NBA_SCORE_HOLDOUT": "True"}
    assert job.check_prereg(good, _meta(tmp_path, "abc-1")) == "abc-1"
    with pytest.raises(job.PreregistrationError, match="not set"):
        job.check_prereg({"NBA_SCORE_HOLDOUT": "True"}, _meta(tmp_path, "abc-1"))
    with pytest.raises(job.PreregistrationError, match="not found"):
        job.check_prereg(good, tmp_path / "missing.json")
    with pytest.raises(job.PreregistrationError, match="placeholder"):
        job.check_prereg(
            {**good, "NBA_PREREGISTERED_ID": "PENDING_X"}, _meta(tmp_path, "PENDING_X")
        )
    with pytest.raises(job.PreregistrationError, match="placeholder"):
        job.check_prereg(good, _meta(tmp_path, None))
    with pytest.raises(job.PreregistrationError, match="!="):
        job.check_prereg({**good, "NBA_PREREGISTERED_ID": "zzz"}, _meta(tmp_path, "abc-1"))
    with pytest.raises(job.PreregistrationError, match="touches_holdout"):
        job.check_prereg(good, _meta(tmp_path, "abc-1", touches=False))
    with pytest.raises(job.PreregistrationError, match="NBA_SCORE_HOLDOUT"):
        job.check_prereg({"NBA_PREREGISTERED_ID": "abc-1"}, _meta(tmp_path, "abc-1"))


def test_main_refuses_before_loading_any_data(
    job: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _meta(tmp_path, "PENDING_SET_AFTER_HOLDOUT_LOG_ROW")
    monkeypatch.setenv("NBA_PARQUET", str(tmp_path / "x.parquet"))
    monkeypatch.setenv("NBA_BUDGET", "smoke")
    monkeypatch.setenv("NBA_PREREGISTERED_ID", "PENDING_SET_AFTER_HOLDOUT_LOG_ROW")
    monkeypatch.setenv("NBA_SCORE_HOLDOUT", "True")
    monkeypatch.setattr(job, "pip_install", lambda pkg: None)
    monkeypatch.setattr(job, "load_v3", lambda: pytest.fail("data was loaded before the guard"))
    with pytest.raises(job.PreregistrationError):
        job.main_v3()
    monkeypatch.setenv("NBA_BUDGET", "fast")
    with pytest.raises(ValueError, match="FULL budget only"):
        job.setup_v3()


# ---------------------------------------- end-to-end (CPU, fakes)


def _make_stage(root: Path, pid: str = "ctxres-v3-test") -> Path:
    """Synthetic 2022-2025 export (two seasons-worth of months per year) + spec + job_meta."""
    rng = np.random.default_rng(7)
    rows: list[dict[str, Any]] = []
    gid = 0
    for season in (2022, 2023, 2024, 2025):
        for year, month in [(season, m) for m in (10, 11, 12)] + [
            (season + 1, m) for m in (1, 2, 3)
        ]:
            for day in range(1, 29, 4):
                for _g in range(2):
                    gid += 1
                    for p in range(10):
                        pid_ = 1000 + (gid % 3) * 10 + p
                        base = 4.0 + 2.0 * (p % 5)
                        row: dict[str, Any] = dict(
                            game_id=f"{gid:07d}", player_id=pid_, team_id=1 + p // 5,
                            game_date=dt.date(year, month, day), season=season,
                            fold_id=year * 100 + month, pos_code=float(p % 3),
                        )  # fmt: skip
                        for st in STATS:
                            mu = base * {"pts": 3, "reb": 1, "ast": 0.6, "fg3m": 0.4}[st]
                            row[st] = float(rng.poisson(mu))
                            row[f"base_m_{st}"] = float(mu + rng.normal(0, 0.3))
                            row[f"base_s_{st}"] = float(np.sqrt(mu) + 0.5)
                            row[f"m10_{st}"] = float(mu + rng.normal(0, 0.5))
                        row["min10"] = float(20 + rng.normal(0, 4))
                        row["x1"] = float(rng.normal())
                        rows.append(row)
    df = pl.DataFrame(rows).sort(["game_date", "game_id", "player_id"])
    d = root / "stage"
    d.mkdir(parents=True)
    df.write_parquet(d / "ctxres_v3_with2025.parquet")
    feats = {st: [f"m10_{st}", "min10"] for st in STATS}
    spec = {
        "v1_features": feats,
        "v2_features": {st: ["x1"] for st in STATS},
        "allowlist": sorted({f for v in feats.values() for f in v} | {"x1"}),
        "leak_deny_regex": r"^minutes$|^starter$|actual|post|tonight|final",
        "default_std": {"pts": 6.0, "reb": 2.5, "ast": 2.0, "fg3m": 1.2},
        "built_at": "test",
    }
    (d / "feature_spec.json").write_text(json.dumps(spec))
    (d / "job_meta.json").write_text(
        json.dumps({"preregistration_id": pid, "touches_holdout": True})
    )
    return d


def _install_fakes(job: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    from scipy.stats import norm

    def fake(scale: float, shift: float):
        def fn(blk: dict[str, Any], *_a: Any, **_k: Any) -> dict[str, Any]:
            out = {}
            for st in STATS:
                m = job.C.m[st][blk["a"] : blk["b"]] + shift
                q = job.clean_q(m[:, None] + scale * job.C.s[st][blk["a"] : blk["b"], None]
                                * norm.ppf(job.TAUS199)[None, :])  # fmt: skip
                out[st] = dict(q=q, mean=m)
            return out

        return fn

    monkeypatch.setattr(job, "pip_install", lambda pkg: None)
    monkeypatch.setattr(job, "quantile_block", fake(0.9, 0.0))
    monkeypatch.setattr(job, "count_block", fake(1.1, 0.1))
    monkeypatch.setattr(
        job, "audit_v3", lambda p12: {st: {"top_gain": ["m10_" + st]} for st in STATS}
    )


def _run_job(job: ModuleType, stage: Path, root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    art = root / "artifacts"
    monkeypatch.setenv("NBA_PARQUET", str(stage / "ctxres_v3_with2025.parquet"))
    monkeypatch.setenv("NBA_ARTIFACT_ROOT", str(art))
    monkeypatch.setenv("NBA_BUDGET", "full")
    monkeypatch.setenv("NBA_PREREGISTERED_ID", "ctxres-v3-test")
    monkeypatch.setenv("NBA_SCORE_HOLDOUT", "True")
    monkeypatch.setenv("NBA_V3_REPLICATES", "1")
    _install_fakes(job, monkeypatch)
    job.main_v3()
    runs = sorted(p for p in art.iterdir() if p.is_dir())
    assert len(runs) == 1
    return runs[0]


def test_job_end_to_end_synthetic_is_deterministic_and_2025_only(
    job: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = _make_stage(tmp_path)
    outs = []
    for k in range(2):
        outs.append(_run_job(job, stage, tmp_path / f"run{k}", monkeypatch))
    a, b = outs
    m = json.loads((a / "metrics.json").read_text())
    assert (
        m["complete"] is True and m["calibrator"] == "platt_pts_only" and m["test_season"] == 2025
    )
    assert m["preregistration_id"] == "ctxres-v3-test"
    assert m["frozen_config_sha256"] == ev.FROZEN_CONFIG_SHA256
    assert not (a / "metrics_partial.json").exists()
    for f in ("frozen_config.json", "run_header.json", "leak_audit.json"):
        assert (a / f).exists()
    hdr = json.loads((a / "run_header.json").read_text())
    folds = [x["fold"] for x in hdr["blocks"]]
    assert folds == sorted(folds) and all(f // 100 in (2025, 2026) for f in folds)
    assert all(x["n_train"] >= 1500 for x in hdr["blocks"])  # trained on 2022-2024 + earlier months
    assert [x["n_train"] for x in hdr["blocks"]] == sorted(x["n_train"] for x in hdr["blocks"])
    names = sorted(p.name for p in (a / "oof").glob("*.parquet"))
    assert names == [
        "hybrid_v3.parquet", "hybrid_v3__platt_pts.parquet", "recency_normal.parquet",
        "v1_prod.parquet",
        "xgb_v12_poisson_nb.parquet", "xgb_v12_quantile.parquet",
    ]  # fmt: skip
    oof_a = pl.concat([pl.read_parquet(p) for p in sorted((a / "oof").glob("*.parquet"))])
    oof_b = pl.concat([pl.read_parquet(p) for p in sorted((b / "oof").glob("*.parquet"))])
    assert_frame_equal(oof_a, oof_b)  # determinism: identical OOF on a rerun
    df = pl.read_parquet(stage / "ctxres_v3_with2025.parquet")
    n25 = df.filter(pl.col("season") == 2025).height
    assert (
        oof_a.filter((pl.col("variant") == "hybrid_v3") & (pl.col("stat") == "pts")).height == n25
    )
    ev.check_hybrid_consistency(oof_a)  # hybrid rows == mapped component rows
    cal = oof_a.filter(pl.col("variant") == "hybrid_v3|platt_pts")
    assert cal.height == 4 * n25  # non-pts stats are raw copies of the hybrid
    raw = oof_a.filter(pl.col("variant") == "hybrid_v3")
    for st in ("reb", "ast", "fg3m"):
        c = cal.filter(pl.col("stat") == st).sort("player_id", "game_id")
        r = raw.filter(pl.col("stat") == st).sort("player_id", "game_id")
        assert c["p_ge"].to_list() == r["p_ge"].to_list()
    assert set(m["seed_replicates"]) == {"seed+1"}
    lb = m["leaderboard_2025"]
    assert lb["hybrid_v3"]["pts"]["n"] == n25 and "d_crps_vs_ref" in lb["hybrid_v3"]["pts"]


def test_job_resumes_arms_from_checkpoint(
    job: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stage = _make_stage(tmp_path)
    root = tmp_path / "r"
    first = _run_job(job, stage, root, monkeypatch)
    capsys.readouterr()
    (first / "metrics.json").unlink()  # simulate a crash right before publishing
    import shutil

    shutil.rmtree(first / "oof")
    job.main_v3()
    assert "RESUMED" in capsys.readouterr().out
    assert (first / "metrics.json").exists() and (first / "oof").is_dir()


def test_load_refuses_parquet_without_2025(
    job: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = _make_stage(tmp_path)
    p = stage / "ctxres_v3_with2025.parquet"
    pl.read_parquet(p).filter(pl.col("season") < 2025).write_parquet(p)
    monkeypatch.setenv("NBA_PARQUET", str(p))
    monkeypatch.setenv("NBA_BUDGET", "full")
    monkeypatch.setattr(job, "pip_install", lambda pkg: None)
    job.setup_v3()
    with pytest.raises((ValueError, IndexError, AssertionError)):
        job.load_v3()


# ---------------------------------------- evaluator guards


def _fake_run(root: Path, pid: str = "ctxres-v3-test", **over: Any) -> tuple[Path, Path]:
    """A pulled-run lookalike plus an export parquet, with a planted hybrid-beats-v1 signal."""
    run = root / "20269999_000001"
    (run / "oof").mkdir(parents=True)
    n = 400
    rng = np.random.default_rng(3)
    quant, count = (
        _oof_frame("xgb_v12_quantile", 0.0, n, 1),
        _oof_frame("xgb_v12_poisson_nb", 0.0, n, 1),
    )
    v1 = _oof_frame("v1_prod", 0.0, n, 1).with_columns(pl.col("crps") + 0.3)
    base = pl.concat([quant, count, v1])
    hyb = ev.assemble_hybrid_oof(base)
    rec = _oof_frame("recency_normal", 0.0, n, 1).with_columns(pl.col("crps") + 0.5)
    cal = hyb.with_columns(
        pl.lit("hybrid_v3|platt_pts").alias("variant"),
        pl.col("tll") * 0.98,
    )
    for v, f in [("a", pl.concat([base, hyb, rec, cal]))]:
        f.write_parquet(run / "oof" / f"{v}.parquet")
    metrics = dict(
        experiment_tag="ctxres_v3_hybrid", complete=True, budget="full",
        frozen_config_sha256=ev.FROZEN_CONFIG_SHA256, engine_sha256=ev.ENGINE_SHA256,
        hybrid=ev.HYBRID, calibrator=ev.CALIBRATOR, preregistration_id=pid,
        leak_audit={"pts": {}}, test_season=2025, n_test_rows=n,
    )  # fmt: skip
    metrics.update(over)
    (run / "metrics.json").write_text(json.dumps(metrics))
    ids = base.filter(pl.col("stat") == "pts").select("game_id", "player_id")
    export = ids.with_columns(
        pl.lit(2025).alias("season"),
        pl.lit(1).alias("has_report"),
        pl.Series("n_out_rot", rng.integers(0, 2, ids.height)),
        pl.Series("min_gap", rng.normal(0, 4, ids.height)),
        pl.Series("team_game_no", rng.integers(1, 80, ids.height)),
        pl.Series("starter10", rng.integers(0, 2, ids.height)),
    )
    ex = root / "export.parquet"
    export.write_parquet(ex)
    return run, ex


def test_validate_run_rejects_each_precondition() -> None:
    good = dict(
        experiment_tag="ctxres_v3_hybrid", complete=True, budget="full",
        frozen_config_sha256=ev.FROZEN_CONFIG_SHA256, engine_sha256=ev.ENGINE_SHA256,
        hybrid=ev.HYBRID, calibrator=ev.CALIBRATOR, preregistration_id="p1",
        leak_audit={"pts": {}}, test_season=2025,
    )  # fmt: skip
    ev.validate_run(good, "p1")
    for key, val in [
        ("experiment_tag", "ctxres_v2"), ("complete", False), ("budget", "fast"),
        ("frozen_config_sha256", "0" * 64), ("engine_sha256", "0" * 64), ("calibrator", "platt"),
        ("preregistration_id", "PENDING_x"), ("preregistration_id", ""), ("leak_audit", None),
        ("test_season", 2024), ("hybrid", {**ev.HYBRID, "pts": "xgb_v12_poisson_nb"}),
    ]:  # fmt: skip
        with pytest.raises(ev.GuardError):
            ev.validate_run({**good, key: val})
    with pytest.raises(ev.GuardError):
        ev.validate_run(good, "other-id")


def test_eval_applies_once_then_refuses_without_force_and_logs_force(tmp_path: Path) -> None:
    run, export = _fake_run(tmp_path)
    ledger = tmp_path / "ledger.jsonl"
    out = tmp_path / "v.json"
    res = ev.run(run, export, out, ledger, n_boot=200)
    assert set(res["verdict_per_stat"]) == set(STATS) and out.exists()
    assert res["verdict_per_stat"]["pts"]["crps_delta"][0] == pytest.approx(-0.3)
    assert res["verdict_per_stat"]["pts"]["checks"]["floor"] is True
    assert (
        res["calibrator_pts"]["keep"] is True
    )  # planted 2% log-loss gain on the pts platt variant
    assert [e["event"] for e in ev.read_ledger(ledger)] == ["applied"]
    with pytest.raises(ev.GuardError, match="already scored"):
        ev.run(run, export, None, ledger, n_boot=200)
    assert [e["event"] for e in ev.read_ledger(ledger)] == ["applied"]  # refusal logs nothing
    res2 = ev.run(run, export, None, ledger, force=True, n_boot=200, argv=["--force"])
    assert res2["forced"] is True
    led = ev.read_ledger(ledger)
    assert [e["event"] for e in led] == ["applied", "force"] and led[-1]["argv"] == ["--force"]
    assert res2["verdict_per_stat"] == res["verdict_per_stat"]  # deterministic given the same run


def test_eval_rejects_bad_run_before_claiming_the_ledger(tmp_path: Path) -> None:
    run, export = _fake_run(tmp_path, calibrator="platt")
    ledger = tmp_path / "ledger.jsonl"
    with pytest.raises(ev.GuardError, match="calibrator"):
        ev.run(run, export, None, ledger, n_boot=100)
    assert ev.read_ledger(ledger) == []  # an invalid run does not consume the one application
    run2, export2 = _fake_run(tmp_path / "b", pid="PENDING_x")
    with pytest.raises(ev.GuardError, match="preregistration_id"):
        ev.run(run2, export2, None, ledger, n_boot=100)


# ---------------------------------------- default export unchanged


def _synthetic_loader(seed_frames: tuple[pl.DataFrame, ...], calls: list[int]):
    games, pgs, pos, av, static = seed_frames
    cut = games["game_date"].sort()[int(games.height * 0.7)]
    late = pl.col("game_date") >= cut
    gap = dt.timedelta(
        days=200
    )  # an off-season gap, as between real seasons (days_to_next caps at 7)
    games = games.with_columns(
        pl.when(late).then(2025).otherwise(2022).alias("season"),
        pl.when(late)
        .then(pl.col("game_date") + gap)
        .otherwise(pl.col("game_date"))
        .alias("game_date"),
    )
    late_ids = set(games.filter(pl.col("season") == 2025)["game_id"].to_list())
    av = av.with_columns(
        pl.when(pl.col("game_id").is_in(list(late_ids)))
        .then(pl.col("as_of") + gap)
        .otherwise(pl.col("as_of"))
        .alias("as_of")
    )

    def load(db_path: str, max_season: int = cf.MAX_SEASON):
        calls.append(max_season)
        g = games.filter(pl.col("season") <= max_season)
        ids = g["game_id"].to_list()
        return (
            g,
            pgs.filter(pl.col("game_id").is_in(ids)),
            static,
            av.filter(pl.col("game_id").is_in(ids)),
            pos.filter(pl.col("game_id").is_in(ids)),
        )

    return load


def test_default_export_unchanged_and_flag_is_additive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert cf.MAX_SEASON == 2024 and cf.EXPORT_SEASONS == (2022, 2023, 2024)
    calls: list[int] = []
    monkeypatch.setattr(cf, "load_inputs_v2", _synthetic_loader(_league(), calls))
    elo = tmp_path / "elo.yaml"
    elo.write_text(yaml.safe_dump(ELO))
    a, a2, b = tmp_path / "a", tmp_path / "a2", tmp_path / "b"
    spec_a = cf.export_dataset("x.duckdb", str(a), str(elo))
    cf.export_dataset("x.duckdb", str(a2), str(elo))
    assert calls[:2] == [2024, 2024]  # default path still loads season <= 2024 only
    assert sorted(p.name for p in a.iterdir()) == ["ctxres_v2.parquet", "feature_spec.json"]
    assert (a / "ctxres_v2.parquet").read_bytes() == (a2 / "ctxres_v2.parquet").read_bytes()
    v2 = pl.read_parquet(a / "ctxres_v2.parquet")
    assert set(v2["season"].unique()) == {2022} and spec_a["seasons"] == [2022, 2023, 2024]
    spec_b = cf.export_dataset("x.duckdb", str(b), str(elo), include_holdout_2025=True)
    assert calls[2] == 2025
    assert sorted(p.name for p in b.iterdir()) == [
        "ctxres_v3_with2025.parquet",
        "feature_spec.json",
    ]
    v3 = pl.read_parquet(b / "ctxres_v3_with2025.parquet")
    assert 2025 in set(v3["season"].unique()) and spec_b["includes_holdout_2025"] is True
    assert "includes_holdout_2025" not in spec_a
    # as-of purity: adding 2025 rows leaves every earlier row untouched
    assert_frame_equal(v3.filter(pl.col("season") <= 2024), v2)
    with pytest.raises(ValueError, match="v2 export"):  # cannot overwrite a v2 directory
        cf.export_dataset("x.duckdb", str(a), str(elo), include_holdout_2025=True)
    assert (a / "ctxres_v2.parquet").read_bytes() == (a2 / "ctxres_v2.parquet").read_bytes()


def test_cli_flag_routes_to_separate_default_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, bool]] = []

    def fake_export(db: str, out: str, elo: str, include: bool = False) -> dict[str, Any]:
        seen.append((out, include))
        return {"n_rows": 0, "date_min": "", "date_max": ""}

    monkeypatch.setattr(cf, "export_dataset", fake_export)
    cf.main([])
    cf.main(["--include-holdout-2025"])
    cf.main(["--include-holdout-2025", "--out-dir", str(tmp_path)])
    assert seen == [
        ("data/colab/ctxres_v2", False),
        ("data/colab/ctxres_v3", True),
        (str(tmp_path), True),
    ]
