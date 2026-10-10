# ruff: noqa: E501
# mypy: ignore-errors
# Colab-executed job script helper; kept out of strict type checking.
"""Regenerate ``ridge_v2_sweep.ipynb`` from ``ridge_v2_sweep.py`` (single source of truth).

uv run python research/colab/jobs/ridge_v2_sweep/build_notebook.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

INTRO = """# ridge_v2_sweep -- opponent-adjusted ridge variants (experiment 4)

Runtime > Change runtime type > **T4 GPU** (or any GPU), then Run all. The run prints the GPU and refuses to
start the full budget on a CPU runtime.

* Fixed exp-2 models per stat (pts: multi-quantile XGBoost; reb/ast/fg3m: Poisson + NegBin XGBoost), tuned
  params from `base_config.json`. Each ARM only swaps the ridge columns (v2 group i); see docs/RIDGE_V2.md.
* About 6 min per arm on a T4 (estimate from the exp-2 timings; printed at the top), 25 arms: ~2.5 h in one
  session. **Concurrent sessions**: push the job several times and set `NBA_SHARD=k/n` (e.g. `0/4`) in the
  setup cell of each, or list arms with `NBA_ARMS=ref_fixed,exp2_ref,no_ridge`. Shards are disjoint; the local
  eval merges the pulled run folders by variant.
* CRASH-SAFE: every finished arm is checkpointed to `<run folder>/checkpoints/<hash>/` (predictions, OOF
  parquet, summary). If the kernel dies, re-run the same notebook; finished arms log `RESUMED`.
* Selection uses season 2023 only; 2024 is report-only; season 2025 is refused. A leak audit (null-vs-label
  gap and label rank-correlation of every ridge column) runs before any model is fit.

Artifacts: `best_config.json`, `metrics.json` (`complete: true` only at the end), `oof/` (one parquet per arm) in
`artifacts/<timestamp>/`. Score locally: `uv run python -m research.eval.ridge_v2_eval --run <pulled run dirs>`."""

SETUP = """import os  # noqa: F811 (standalone setup cell; may run first)

os.environ.setdefault("NBA_BUDGET", "full")
# Optional sharding across concurrent sessions, e.g. os.environ["NBA_SHARD"] = "0/4"
# Or an explicit list: os.environ["NBA_ARMS"] = "ref_fixed,exp2_ref,no_ridge,r1,r6"
print("BUDGET:", os.environ["NBA_BUDGET"], "| SHARD:", os.environ.get("NBA_SHARD"), "| ARMS:", os.environ.get("NBA_ARMS"))"""


def cell(kind: str, src: str) -> dict:
    base = {"cell_type": kind, "metadata": {}, "source": src.splitlines(keepends=True)}
    if kind == "code":
        base.update(execution_count=None, outputs=[])
    return base


def build() -> dict:
    code = (HERE / "ridge_v2_sweep.py").read_text()
    code = code.replace('if __name__ == "__main__":\n    entry()\n', "").rstrip("\n")
    return {
        "cells": [
            cell("markdown", INTRO),
            cell("code", code),
            cell("code", SETUP),
            cell("code", "metrics = entry()"),
        ],
        "metadata": {
            "accelerator": "GPU",
            "colab": {"gpuType": "T4"},
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        },
        "nbformat": 4,
        "nbformat_minor": 4,
    }


def main() -> None:
    out = HERE / "ridge_v2_sweep.ipynb"
    out.write_text(json.dumps(build(), indent=1) + "\n")
    print("wrote", out)


if __name__ == "__main__":
    main()
