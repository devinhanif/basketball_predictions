# ruff: noqa: E501
"""The first live T442 gate (docs/prereg/AVG_SHADOW_LIVE.md, "What the steward builds" 3): a SoftmaxGate over [A0, SEQ], one fit on every row
strictly earlier than the month it serves, written as ``gate.json`` for the weights bundle.

The fit is T442's own code (``research.stack.router.ContextEncoder`` + ``SoftmaxGate``, 30-day-block walk replaced by ONE fit because the live
month uses every earlier row; the 3,000-row minimum applies). ``fit_gate_params`` is pure and unit-tested; ``main`` consumes an ALIGNED npz
(per stat: a0_199 continuous A0 grids, seq_199 continuous SEQ grids, y, and the five context columns) that the steward assembles from the
rebuild's OOF file and the stored members. It reads labels and forecasts of 2023-2025 as TRAINING rows (HOLDOUT_ACCESS_LOG row 72) and scores
nothing.

    .venv/bin/python research/colab/jobs/avg_shadow_rebuild/fit_gate.py --aligned <aligned.npz> --fit-through 2026-06-13 --out <dir>/gate.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nba.daily.avg_shadow.grids import GATE_COLUMNS
from research.eval import ensemble_eval as E
from research.stack.data import StackData
from research.stack.router import ContextEncoder, SoftmaxGate

STATS = ("pts", "reb", "ast", "fg3m")
MIN_ROWS = 3000


def fit_gate_params(
    stat: str,
    a0_199: np.ndarray,
    seq_199: np.ndarray,
    y: np.ndarray,
    ctx: pd.DataFrame,
    date: np.ndarray,
    season: np.ndarray,
) -> dict[str, Any]:
    if len(y) < MIN_ROWS:
        raise ValueError(
            f"{stat}: {len(y)} rows < {MIN_ROWS}; T442 uses 0.5/0.5 below the minimum, there is nothing to fit"
        )
    ctx = ctx[list(GATE_COLUMNS)].reset_index(drop=True)
    keys = pd.DataFrame(
        {"game_date": pd.to_datetime(date.astype("datetime64[D]")), "season": season}
    )
    cand = np.stack([E.grid19(a0_199), E.grid19(seq_199)], axis=1)
    data = StackData(stat, ["first", "seq"], keys, y.astype(float), cand, ctx)
    enc = ContextEncoder().fit(ctx)
    gate = SoftmaxGate()
    gate.fit(enc.transform(ctx), data)
    assert gate.coef is not None and enc.num == list(GATE_COLUMNS)
    return {
        "columns": list(GATE_COLUMNS), "med": enc.med.tolist(), "mu": enc.mu.tolist(), "sd": enc.sd.tolist(),
        "coef": gate.coef.tolist(), "n_fit": int(len(y)),
    }  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--aligned", type=Path, required=True)
    ap.add_argument("--fit-through", required=True, help="last game date among the rows (ISO)")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    z = np.load(a.aligned, allow_pickle=False)
    out: dict[str, Any] = {}
    for st in STATS:
        ctx = pd.DataFrame(
            {
                "log_n_prior": np.log1p(z[f"{st}_n_prior"]),
                **{c: z[f"{st}_{c}"] for c in GATE_COLUMNS[1:]},
            }
        )
        out[st] = fit_gate_params(
            st,
            z[f"{st}_a0_199"],
            z[f"{st}_seq_199"],
            z[f"{st}_y"],
            ctx,
            z[f"{st}_date_days"],
            z[f"{st}_season"],
        )
        assert str(np.max(z[f"{st}_date_days"].astype("datetime64[D]"))) <= a.fit_through
    a.out.write_text(json.dumps({**out}, indent=1))
    print(json.dumps({st: out[st]["n_fit"] for st in STATS}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
