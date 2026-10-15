"""Shared locations for the top_parlays tests. Real-data tests read the main checkout's stored
frames (read-only) and skip when they are not on this machine."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()


def main_root() -> Path:
    """The main checkout (worktrees share its data/ and reports/ by absolute path only)."""
    out = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
        cwd=HERE.parent,
    ).stdout.strip()
    return Path(out).parent if out else HERE.parents[2]


@pytest.fixture(scope="session")
def history_dirs() -> tuple[Path, Path, Path]:
    root = main_root()
    pairs = root / "data" / "ops" / "model_clv_pairs"
    model = root / "reports" / "odds_history"
    corr = root / "data" / "parlay" / "joint_model.json"
    needed = [
        pairs / "pairs_2023.parquet",
        pairs / "pairs_2024.parquet",
        model / "model_rows_2023.parquet",
        model / "model_rows_2024.parquet",
        corr,
    ]
    if not all(p.exists() for p in needed):
        pytest.skip("stored OOF frames not on this machine")
    return pairs, model, corr
