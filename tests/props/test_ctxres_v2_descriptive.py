"""Synthetic-parquet tests for the descriptive ctxres_v2 report."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl

from nba.eval import ctxres_v2_descriptive as D

THR = {"pts": [10, 15, 20, 25, 30], "reb": [4, 6, 8, 10], "ast": [2, 4, 6, 8], "fg3m": [1, 2, 3, 4]}
GRID = np.arange(1, 20) / 20.0


def _arm(name: str, shift: float, rng: np.random.Generator, full: bool = True) -> pl.DataFrame:
    frames = []
    for st in D.STATS:
        n_games, per = 40, 8
        gid = np.repeat([f"g{i}" for i in range(n_games)], per)
        pid = np.tile(np.arange(per), n_games)
        fold = np.repeat(np.arange(n_games) % 4 + 202401, per)
        base_rng = np.random.default_rng(D.STATS.index(st) + 1)  # same truth for every arm
        mu = base_rng.uniform(2, 20, n_games * per)
        y = base_rng.poisson(mu).astype(np.float32)
        noise = rng.normal(0, 0.1, len(y))
        qs = np.maximum(mu[:, None] + (GRID[None, :] - 0.5) * 8 * (1 + shift) + noise[:, None], 0)
        crps = (np.abs(y - mu) * (1 + shift) + 0.1).astype(np.float32)
        thr = np.array(THR[st])
        p = np.clip(1 - (thr[None, :] - mu[:, None]) / 20, 0.01, 0.99).astype(np.float32)
        tll = (np.abs(y - mu) * (1 + shift) * 0.1).astype(np.float32)
        d = {
            "variant": [name] * len(y),
            "stat": [st] * len(y),
            "game_id": gid,
            "player_id": pid,
            "fold_id": fold,
            "y": y,
            "mean": mu.astype(np.float32),
            "crps": crps,
            "tll": tll,
        }
        df = pl.DataFrame(d)
        if full:
            df = df.with_columns(
                pl.Series("q19", qs.astype(np.float32).tolist(), dtype=pl.List(pl.Float32)),
                pl.Series("p_ge", p.tolist(), dtype=pl.List(pl.Float32)),
            )
        else:
            df = df.with_columns(
                pl.lit(None, dtype=pl.List(pl.Float32)).alias("q19"),
                pl.lit(None, dtype=pl.List(pl.Float32)).alias("p_ge"),
            )
        frames.append(df)
    return pl.concat(frames)


def _make_run(tmp_path: Path) -> Path:
    rng = np.random.default_rng(1)
    oof = tmp_path / "oof"
    oof.mkdir()
    arms = {
        "v1_prod": (0.30, True),
        "xgb_v12_poisson_nb": (0.0, True),
        "xgb_v12_poisson_nb|iso": (0.0, True),
        "catboost_v12": (0.02, False),
        "xgb_v12_quantile": (0.01, True),
        "blend_top3": (0.005, True),
    }
    for name, (shift, full) in arms.items():
        _arm(name, shift, rng, full).write_parquet(oof / (name.replace("|", "__") + ".parquet"))
    lb = {"v1_prod": {st: {"crps": None} for st in D.STATS}}
    metrics = {
        "experiment_tag": "ctxres_v2_exp2_full_breadth",
        "best_arm_2023": "xgb_v12_quantile",
        "recorded_candidate": "xgb_v12_poisson_nb",
        "leaderboard_2024": lb,
        "ablation_drop_group_2024": {"a": {st: [0.02, 0.01, 0.03] for st in D.STATS}},
        "calibration_2024": {
            "xgb_v12_poisson_nb|iso": {
                st: {"d_tll_vs_ref": [-0.01, -0.02, -0.001]} for st in D.STATS
            }
        },
        "zero_inflation_fg3m_2024": {"xgb_v12_poisson_nb": {"obs_p0": 0.42, "pred_p0": 0.40}},
        "drift": {"2023": {"rows": 10}, "2024": {"rows": 12, "pts_mean": 10.0}},
        "selection_scores_2023": {"xgb_v12_poisson_nb": 0.93},
    }
    (tmp_path / "metrics.json").write_text(json.dumps(metrics))
    (tmp_path / "best_config.json").write_text(
        json.dumps({"best_arm_2023": "xgb_v12_quantile", "thresholds": THR})
    )
    return tmp_path


def test_report_runs_and_uses_recorded_candidate(tmp_path: Path) -> None:
    run = _make_run(tmp_path)
    text = D.describe(str(run), n_boot=200)
    assert "xgb_v12_poisson_nb (CAND)" in text
    assert "DESCRIPTIVE" in text
    assert "catboost_v12" in text
    assert "useless/unclear" in text or "helps" in text
    assert "integer-grid" in text
    assert "q (F6, m=4)" in text  # blend vs candidate table


def test_prev_metrics_repro_table(tmp_path: Path) -> None:
    run = _make_run(tmp_path)
    prev = tmp_path / "prev.json"
    prev.write_text(json.dumps({"leaderboard_2024": {"v1_prod": {"pts": {"crps": 1.0}}}}))
    text = D.describe(str(run), prev_metrics=str(prev), n_boot=100)
    assert "Reproducibility" in text


def test_light_arm_has_no_pit_but_has_paired_delta(tmp_path: Path) -> None:
    run = _make_run(tmp_path)
    files = D.variant_files(run / "oof")
    res = D.arm_vs_reference(files, "xgb_v12_poisson_nb", 200, 0)
    cat = res["catboost_v12"]["pts"]
    assert cat["cov_pit"] is None and cat["crps_vs_ref"] is not None
    cand = res["xgb_v12_poisson_nb"]["pts"]
    assert cand["cov_pit"] is not None and 0.0 <= cand["cov_pit"] <= 1.0
    assert cand["crps_vs_ref"]["delta"] < 0  # candidate better than the worse reference
    assert res["v1_prod"]["pts"]["crps_vs_ref"] is None
    assert cand["crps_vs_cand"] is None  # no self-comparison


def test_families_bh_monotone_and_cover_cells(tmp_path: Path) -> None:
    run = _make_run(tmp_path)
    res = D.arm_vs_reference(D.variant_files(run / "oof"), "xgb_v12_poisson_nb", 200, 0)
    fam = D.families(res)
    assert len(fam["F1"]) == 4 * 4  # four non-reference base arms x 4 stats
    for arm, st, p in fam["F1"]:
        q = res[arm][st]["crps_vs_ref"]["q_family"]
        assert q >= p - 1e-12 and q <= 1.0


def test_bh_and_p_from_ci() -> None:
    assert D.family_bh([]) == []
    q = D.family_bh([0.001, 0.04, 0.5])
    assert q[0] <= q[1] <= q[2]
    assert D.p_from_ci(0.0, -1.0, 1.0) > 0.9
    assert D.p_from_ci(1.0, 0.5, 1.5) < 0.01


def test_variant_name_roundtrip(tmp_path: Path) -> None:
    run = _make_run(tmp_path)
    names = D.variant_files(run / "oof")
    assert "xgb_v12_poisson_nb|iso" in names
