"""Fit the halftime scorer's artifact: T508 arm M and glm_e on 2019-2024 (the maintainer's run).

    NBA_DATA_ROOT=/Users/devin/Downloads/nba-prediction \\
        uv run python -m research.eval.halftime_scorer_train \\
        --out data/models/halftime_ingame/<date>/artifact.json

Spec (docs/prereg/HALFTIME_OVERS_VS_PRICE.md section 3, DRAFT): the T508 arm-M specification and
constants unchanged (two LightGBM stages, 8 leaves, 200 trees, monotone stage B, seed 0), refit on
2019-2024 instead of T508's 2019-2023, pooled over all seven checkpoints with a pooled Platt on the
training predictions (as ``arm_probs``), served at e = 24. glm_e: per-stat logistic on
[logit B0p, logit B0, logit B1, log1p(need)] using the e = 24 training rows only.

Training outcomes are historical (``season <= 2024`` in every SQL: the harness readers enforce it);
no season 2025 or 2026 row is read, no outcome of the forward period. Live-path code lives in
``nba/markets/halftime_model.py``; this file is research-side only (nba.* may not import research).
Write-once output: an existing artifact path is refused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.markets import halftime_model as hm
from research.eval import in_game_line_eval as ev
from research.eval import in_game_line_features as feat
from research.eval import in_game_line_rule as rule

SEASONS = (2019, 2020, 2021, 2022, 2023, 2024)  # every one <= 2024


def robust_platt(p: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Intercept and slope on logit p, unregularised MLE (the harness's Newton can diverge)."""
    from sklearn.linear_model import LogisticRegression

    m = LogisticRegression(C=np.inf, max_iter=2000)
    m.fit(hm.logit(p).reshape(-1, 1), y.astype(int))
    return float(m.intercept_[0]), float(m.coef_[0][0])


def raw_cols(u: pl.DataFrame) -> hm.Cols:
    return {
        "e": u["e"].to_numpy().astype(float),
        "minutes": u["minutes"].to_numpy().astype(float),
        "starter": u["starter"].to_numpy().astype(float),
        "fouls": u["fouls"].to_numpy().astype(float),
        "margin": u["margin"].to_numpy().astype(float),
        "cur": u["cur"].to_numpy().astype(float),
        "line": u["line"].to_numpy().astype(float),
        "pregame_mean": u["pregame_mean"].to_numpy().astype(float),
        "rec_minutes": u["rec_minutes"].to_numpy().astype(float),
    }


def parity_with_harness(u: pl.DataFrame, stat: str, d: hm.Cols) -> float:
    """Max abs difference between the live restatement and the harness B0 / B1 / derived columns."""
    from research.eval import in_game_line_gate as gate

    chk = ev.add_derived(u)
    worst = 0.0
    for name in ("need", "abs_margin", "q4_rem", "blow_x_q4", "absm_x_q4"):
        worst = max(worst, float(np.max(np.abs(chk[name].to_numpy() - d[name]))))
    worst = max(worst, float(np.max(np.abs(gate.b0_for(u, stat) - hm.b0_prob(stat, d)))))
    b1 = rule.b1_prob(stat, u["line"].to_numpy(), u["cur"].to_numpy(), u["pregame_mean"].to_numpy())
    worst = max(worst, float(np.max(np.abs(b1 - hm.b1_prob(stat, d)))))
    return worst


def fit(
    root: Path, seasons: tuple[int, ...] = SEASONS
) -> tuple[dict[str, Any], Any, dict[str, Any], dict[str, Any], dict[str, Any]]:
    t0 = time.time()
    assert all(s <= 2024 for s in seasons)
    states = ev.load_states(root, seasons)
    finals = ev.real_finals(root, (2018, *seasons), states)
    rec = ev.recency(finals)
    stage_a = ev.fit_minutes_stage(ev.minutes_rows(seasons, states, rec))
    feat.log(f"stage A fitted ({time.time() - t0:.0f}s)")
    stage_b: dict[str, Any] = {}
    platt: dict[str, tuple[float, float]] = {}
    glm: dict[str, dict[str, Any]] = {}
    report: dict[str, Any] = {}
    for stat in rule.STATS:
        u = pl.concat([ev.stat_units(s, stat, states, rec, finals) for s in seasons])
        d = hm.derive(raw_cols(u))
        report[f"{stat}_parity_max_abs_diff"] = parity_with_harness(u, stat, d)
        d["rem_min_hat"] = np.asarray(stage_a.predict(hm.matrix(d, hm.FEATURES_A)), float)
        d["b0_logit"] = hm.logit(hm.b0_prob(stat, d))
        y = u["y"].to_numpy().astype(float)
        mono = [ev.MONOTONE_B.get(c, 0) for c in hm.FEATURES_B]
        clf = _fit_stage_b(hm.matrix(d, hm.FEATURES_B), y, mono)
        raw = clf.predict_proba(hm.matrix(d, hm.FEATURES_B))[:, 1]
        platt[stat] = robust_platt(raw, y)
        stage_b[stat] = clf.booster_
        sel = u["e"].to_numpy() == hm.CHECKPOINT
        dg = {k: v[sel] for k, v in d.items()}
        from sklearn.linear_model import LogisticRegression

        g = LogisticRegression(C=np.inf, max_iter=2000)
        g.fit(hm.glm_matrix(stat, dg), y[sel].astype(int))
        glm[stat] = {"coef": [float(x) for x in g.coef_[0]], "intercept": float(g.intercept_[0])}
        report[f"{stat}_n_rows"] = int(u.height)
        report[f"{stat}_n_rows_e24"] = int(sel.sum())
        report[f"{stat}_platt"] = list(platt[stat])
        feat.log(f"{stat}: {u.height} rows, e24 {int(sel.sum())} ({time.time() - t0:.0f}s)")
        del u, d
    return report, stage_a, stage_b, platt, glm


def _fit_stage_b(x: np.ndarray, y: np.ndarray, mono: list[int]) -> Any:
    import lightgbm as lgb

    m = lgb.LGBMClassifier(
        n_estimators=ev.N_TREES,
        learning_rate=ev.LEARNING_RATE,
        num_leaves=ev.N_LEAVES,
        min_child_samples=ev.MIN_DATA_IN_LEAF,
        monotone_constraints=mono,
        random_state=ev.LGBM_SEED,
        deterministic=True,
        force_row_wise=True,
        verbose=-1,
        n_jobs=1,
    )
    m.fit(x, y)
    return m


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.out.exists():
        print(f"refusing: {a.out} exists (write-once)", file=sys.stderr)
        return 2
    root = feat.data_root()
    t0 = time.time()
    report, stage_a, stage_b, platt, glm = fit(root)
    src = {
        p: hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16]
        for p in (
            "research/eval/in_game_line_eval.py",
            "research/eval/in_game_line_rule.py",
            "research/eval/in_game_line_features.py",
            "nba/markets/halftime_model.py",
            "research/eval/halftime_scorer_train.py",
        )
    }
    meta = {
        "rule": "HALFTIME_OVERS_VS_PRICE (DRAFT, not frozen)",
        "seasons": list(SEASONS),
        "checkpoint": hm.CHECKPOINT,
        "versions": rule.versions(),
        "sources_sha256_16": src,
        "report": report,
        "wall_seconds": time.time() - t0,
        "note": "arm M refit on 2019-2024; glm_e B0p reconstructed (attack scripts lost); seed 0",
    }
    digest = hm.save_artifact(a.out, meta, stage_a.booster_, stage_b, platt, glm)
    print(json.dumps({"artifact": str(a.out), "digest": digest, "report": report}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
