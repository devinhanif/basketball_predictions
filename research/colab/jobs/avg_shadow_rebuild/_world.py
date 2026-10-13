# ruff: noqa: E501
"""Shared loader of the P1a / P1b checks: the stored selection-side world (A0 / FULL, seasons <= 2024) with the rebuild's E2 forecasts for seasons <= 2024.

The rebuild's OOF file also holds 2025 forecasts; they are dropped on the SEASON key before anything else is used (a filtered copy is written to a temp
directory, so the research loaders never see a season above 2024)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from research.eval import seq_full_mix_eval as S

MAX_SEASON = 2024


def filtered_oof(run: Path, tmp: Path, name: str = "oof_E2.npz") -> int:
    z = np.load(run / name, allow_pickle=True)
    keep = z["season"] <= MAX_SEASON
    np.savez(tmp / name, **{k: z[k][keep] for k in z.files})
    return int(keep.sum())


def world(
    run: Path, sel_run_for_stored_meta: Path | None = None
) -> tuple[dict[str, Any], dict[str, np.ndarray], Path]:
    tmp = Path(tempfile.mkdtemp(prefix="avg_p1_"))
    filtered_oof(run, tmp)
    S.configure_rt(tmp, None)
    w = S.stored_world_base()
    seq = {st: g for st, g in S.load_seq_run(tmp, "E2", w["base"], MAX_SEASON).items()}
    return w, seq, tmp
