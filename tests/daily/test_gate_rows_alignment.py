# ruff: noqa: E501
"""AVG_SHADOW_LIVE_2 gate_rows.py, review S1 (2026-10-14): the join order is pinned and every aligned field is re-derived by key (no polars)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import polars as pl
import pytest

ROOT = Path(__file__).resolve().parents[2]
STATS = ("pts", "reb", "ast", "fg3m")


def _load():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location(
        "avg_shadow_rebuild_job_gate_rows_s1",
        ROOT / "research/colab/jobs/avg_shadow_rebuild/gate_rows.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _load()


def _ext(q: np.ndarray) -> np.ndarray:
    return (
        np.asarray(q, dtype=np.float64) * 2.0
    )  # stand-in for ensemble_eval.ext19: any fixed row-wise map


def _world(n: int = 60, seed: int = 0):  # noqa: ANN202
    rng = np.random.default_rng(seed)
    gid = np.array([f"002{i // 10:07d}" for i in range(n)])
    pid = np.array([100 + i % 10 for i in range(n)], dtype=np.int64)
    q = rng.normal(size=(n, 4, 19))
    meta = {c: rng.integers(1, 90, n).astype(np.float64) for c in G.CONTEXT_COLUMNS}
    meta |= {
        "game_id": gid.copy(),
        "player_id": pid.copy(),
        "date_days": (19000 + np.arange(n) // 10).astype(np.int64),
    }
    oof = {"game_id": gid.copy(), "player_id": pid.copy(), "q": q}
    z: dict[str, np.ndarray] = {}
    for si, st in enumerate(STATS):
        z[f"{st}_game_id"], z[f"{st}_player_id"] = gid.copy(), pid.copy()
        z[f"{st}_seq_199"] = _ext(q[:, si, :])
        for c in G.CONTEXT_COLUMNS:
            z[f"{st}_{c}"] = meta[c].copy()
        z[f"{st}_date_days"] = meta["date_days"].copy()
    return z, oof, meta


def test_verify_passes_when_sources_are_in_a_different_order() -> None:
    z, oof, meta = _world()
    perm = np.random.default_rng(1).permutation(len(oof["game_id"]))
    oof2 = {k: v[perm] for k, v in oof.items()}
    meta2 = {k: v[perm[::-1]] for k, v in meta.items()}
    assert G.verify_alignment(z, STATS, oof2, meta2, _ext) == {"rows": 60, "stats": 4}


@pytest.mark.parametrize(
    "fault", ["roll_seq", "roll_ctx", "roll_dates", "key_order", "dup_key", "missing"]
)
def test_verify_detects_a_planted_misalignment(fault: str) -> None:
    z, oof, meta = _world()
    if fault == "roll_seq":
        z["reb_seq_199"] = np.roll(z["reb_seq_199"], 1, axis=0)
    elif fault == "roll_ctx":
        z["fg3m_team_game_no"] = np.roll(z["fg3m_team_game_no"], 1)
    elif fault == "roll_dates":
        z["pts_date_days"] = np.roll(z["pts_date_days"], 1)
    elif fault == "key_order":
        z["ast_player_id"] = np.roll(z["ast_player_id"], 1)
    elif fault == "dup_key":
        oof["game_id"][5], oof["player_id"][5] = oof["game_id"][4], oof["player_id"][4]
    else:
        meta["game_id"][3] = "002missing"
    with pytest.raises(RuntimeError):
        G.verify_alignment(z, STATS, oof, meta, _ext)


def test_ordered_joins_keeps_left_order_and_restores_polars() -> None:
    orig = pl.DataFrame.join
    left = pl.DataFrame({"k": [5, 3, 9, 1, 3], "o": list(range(5))})
    right = pl.DataFrame({"k": [1, 3, 5, 9], "v": [10, 30, 50, 90]})
    with G.ordered_joins():
        j = left.join(right, on="k", how="left")
        assert pl.DataFrame.join is not orig
        j2 = left.join(
            right, on="k", how="left", maintain_order="right"
        )  # an explicit choice is respected
    assert pl.DataFrame.join is orig
    assert j["o"].to_list() == [0, 1, 2, 3, 4] and j["v"].to_list() == [50, 30, 90, 10, 30]
    assert sorted(j2["o"].to_list()) == [0, 1, 2, 3, 4]


def test_ordered_joins_restores_polars_after_an_error() -> None:
    orig = pl.DataFrame.join
    with pytest.raises(ValueError), G.ordered_joins():
        raise ValueError("boom")
    assert pl.DataFrame.join is orig
