# ruff: noqa: E501
"""Build ``rung_cal_map_v1.json``: ONE static CAL-rung map on all DraftKings 2023-24 rungs.

Reads the fit arrays that ``tests/parlay/rung_cal_harness.py`` extracts with the frozen harness
(``nba`` never imports ``research``); fits with the harness's own numerics (``nba.parlay.line_cal``,
pinned to ``line_calibration_eval``): per stat, every rung with a book main line dated before
``block_start`` = 2025-07-01, weight ``0.5 ** (age_days / 365)``, ridge 1.0 on the six non-intercept
terms, identity below 2,000 rows. Season 2025 is refused by assertion.

    .venv/bin/python -W ignore -m tests.parlay.rung_cal_harness --data-root <main checkout> --out <npz>
    .venv/bin/python -m nba.parlay.rung_cal_build --arrays <npz> --out nba/parlay/rung_cal_map_v1.json

The maintainer's run, a few seconds; nothing is written except ``--out``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from nba.parlay import line_cal as lc
from nba.parlay import rung_cal as rc

NEED = ("pm", "point", "med", "spr", "st10", "x", "zm", "r", "bench", "date", "stat", "season",
        "unit", "hit", "has_main")  # fmt: skip


def load_arrays(path: Path) -> dict[str, Any]:
    z = np.load(path, allow_pickle=False)
    D = {k: z[k] for k in NEED}
    D["date"] = D["date"].astype("datetime64[D]")
    # the harness stores zm and r per rung (main line implied); rebuild `main` so the serving design
    # is exercised on the same inputs: r = (point - main) / spr  =>  main = point - r * spr (unclipped
    # rows only), zm = (main - med) / spr. The stored zm / r are authoritative; see check below.
    D["main"] = D["point"] - D["r"] * np.maximum(D["spr"], lc.SPREAD_MIN)
    return D


def check_design(D: dict[str, Any]) -> float:
    """Serving design vs the harness's stored columns on rows with a main line: max abs difference.
    Clipped rows (|r| = 8 or |zm| = 5) are compared on the clipped values, so this is exact."""
    m = D["has_main"]
    X = rc.design(D["pm"][m], D["point"][m], D["main"][m], D["med"][m], D["spr"][m], D["st10"][m])
    x = np.log(np.clip(D["pm"][m], lc.EPS, 1 - lc.EPS)) - np.log1p(
        -np.clip(D["pm"][m], lc.EPS, 1 - lc.EPS)
    )
    b = D["bench"][m].astype(float)
    H = np.column_stack([np.ones_like(x), x, D["zm"][m], D["r"][m], D["r"][m] * x, b, b * x])
    return float(np.max(np.abs(X - H)))


def build_payload(arrays: Path) -> dict[str, Any]:
    D = load_arrays(arrays)
    if int(D["season"].max()) > 2024:
        raise ValueError("season 2025 is the frozen holdout")
    block_start = np.datetime64(lc.MAX_FIT_DATE.isoformat(), "D") + np.timedelta64(1, "D")
    cells = rc.fit_cells(D, block_start)
    fit = D["has_main"] & (D["date"] < block_start)
    row_counts = {
        "S_rungs_all": int(D["pm"].size),
        "S_rungs_with_main_fit": int(fit.sum()),
        "S_rungs_no_main": int((~D["has_main"]).sum()),
        "fit_units": int(np.unique(D["unit"][fit]).size),
        "by_stat": {c.stat: c.n_fit for c in cells},
        "first_fit_date": str(D["date"][fit].min()),
        "last_fit_date": str(D["date"][fit].max()),
        "seasons": sorted({int(s) for s in D["season"]}),
    }
    inputs = {
        "arrays_sha256": hashlib.sha256(arrays.read_bytes()).hexdigest(),
        "odds_history_note": (
            "odds_history.duckdb grew on 2026-10-15 (six combo markets, rungs untouched), so its sha "
            "differs from the ALT_RUNG_CAL pin; the pinned counts (S = 162,745) held"
        ),
    }
    return rc.to_payload(
        cells,
        fit_through=lc.MAX_FIT_DATE.isoformat(),
        block_start=str(block_start),
        row_counts=row_counts,
        inputs=inputs,
        note=(
            "DraftKings 2023-24 rungs only; season 2025 not loaded. Static map: weights are "
            "0.5 ** (age / 365 days) counted from block_start. Display only; not model skill: the "
            "gain over a reliability table is the book's main line (T494). Refit only after Devin "
            "logs a 2025 training-use row."
        ),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arrays", required=True)
    ap.add_argument("--out", default=str(rc.MAP_PATH))
    a = ap.parse_args(argv)
    D = load_arrays(Path(a.arrays))
    err = check_design(D)
    if err > 1e-9:
        raise SystemExit(f"serving design differs from the harness columns by {err}")
    payload = build_payload(Path(a.arrays))
    Path(a.out).write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    print(f"wrote {a.out} map_sha256 {payload['map_sha256'][:8]}")
    for c in payload["cells"]:
        print(
            c["stat"], c["n_fit"], None if c["beta"] is None else [round(v, 3) for v in c["beta"]]
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
