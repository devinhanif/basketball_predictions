"""COMBO_LINE_CAL harness: label discipline, leak guard, pinned lines (no real data)."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import numpy as np

from research.eval import combo_line_cal_eval as E
from research.eval import combo_line_cal_rule as R

SRC = Path(E.__file__).read_text()


def _synthetic(n_per_month: int = 1200) -> dict[str, Any]:
    rng = np.random.default_rng(3)
    months = np.arange("2023-11", "2024-06", dtype="datetime64[M]")
    date = np.concatenate(
        [m.astype("datetime64[D]") + rng.integers(0, 27, n_per_month) for m in months]
    )
    n = len(date)
    pm = np.clip(rng.beta(4, 5, n), 0.02, 0.98)
    D: dict[str, Any] = {
        "n": n,
        "season": np.where(date >= np.datetime64("2024-01-01"), 2024, 2023),
        "date": date,
        "book": np.full(n, "draftkings"),
        "target": np.full(n, "pra"),
        "role": np.full(n, "primary"),
        "game_id": np.array([f"g{i // 5}" for i in range(n)]),
        "player_id": np.arange(n),
        "pm": pm,
        "line": np.full(n, 20.5),
        "med": np.full(n, 20.0) + rng.normal(0, 1, n),
        "spr": np.full(n, 4.0),
        "st10": rng.random(n),
        "mult": np.full(n, 0.5),
        "lead_min": np.full(n, 64.0),
    }
    D["mstart"] = D["date"].astype("datetime64[M]").astype("datetime64[D]")
    return D


def test_only_select_reads_labels() -> None:
    tree = ast.parse(SRC)
    callers = set()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for c in ast.walk(fn):
                if isinstance(c, ast.Call) and getattr(c.func, "id", None) == "load_labels":
                    callers.add(fn.name)
    assert callers == {"main"}
    main_src = SRC[SRC.index("def main(") :]
    assert main_src.index("require_ready") < main_src.index("load_labels(D)")
    assert "season 2025" in SRC and "season <= {MAX_SEASON}" in SRC.replace(
        "g.season <= ", "season <= "
    )


def test_walk_forward_ignores_labels_from_the_block_on() -> None:
    D = _synthetic()
    y = E.synthetic_labels(D, "biased", seed=1)
    out = E.planted_future(D, y, season=2024)
    assert out["bad"] == [] and out["identical"] == out["probes"] == out["positive_control_moved"]


def test_synthetic_truths_reach_distinct_verdicts() -> None:
    D = _synthetic(1200)
    verdicts = {}
    for truth in ("biased", "calibrated"):
        res = E.score_stage(D, E.synthetic_labels(D, truth, seed=2), k_null=6)
        verdicts[truth] = E.evaluate(res)["verdict"]
    assert verdicts["biased"] in ("CALIBRATED", "SHRINKAGE_ONLY")
    assert verdicts["calibrated"] == "FAIL"


def test_rule_declares_every_pinned_input() -> None:
    d = R.declared()
    for k in (
        "ODDS_SHA256",
        "OOF_KEYS_SHA256",
        "OOF_GRIDS_SHA256",
        "DB_SHA256",
        "PAIRS_SHA256_2023",
        "PAIRS_SHA256_2024",
        "UV_LOCK_SHA256",
    ):
        assert re.fullmatch(r"[0-9a-f]{64}", d[k])


def test_z_floor_and_design_take_no_price() -> None:
    import inspect

    assert "mult" not in inspect.signature(E.design).parameters
    X = E.design(
        np.array([0.4]), np.array([20.5]), np.array([20.0]), np.array([0.1]), np.array([0.2])
    )
    assert X.shape == (1, 5) and abs(X[0, 2] - 1.0) < 1e-12  # z = 0.5 / max(0.1, 0.5)
