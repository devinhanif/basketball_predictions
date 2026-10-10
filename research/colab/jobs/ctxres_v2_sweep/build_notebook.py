# ruff: noqa: E501
# mypy: ignore-errors
# Colab-executed job script helper; kept out of strict type checking.
"""Regenerate ``ctxres_v2_sweep.ipynb`` from ``ctxres_v2_sweep.py`` (single source of truth).

uv run python research/colab/jobs/ctxres_v2_sweep/build_notebook.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

INTRO = """# ctxres_v2_sweep -- context-residual v2 model-family sweep (experiment 2, full breadth)

Runtime > Change runtime type > **T4 GPU**, then Run all.

* `NBA_BUDGET=full` (default): STRICT, every arm must finish (it raises instead of skipping), 60 Optuna
  trials per tuned arm, CatBoost-GPU arm if it installs, all calibrators, ablation on all 2024 blocks.
  Expected about 65-85 min; the per-arm estimates and the total are printed at the top of the run.
* `NBA_BUDGET=fast`: about 30-35 min (measured); optional arms may be skipped.
* If `completion_prev.json` is staged (docs/CTXRES_V2.md) the notebook runs the COMPLETION step instead
  (no search or selection; refits only the listed arms with already-chosen configs).

Selection uses season 2023 only; season 2024 is report-only; season 2025 is refused.
The leak detector aborts the run if a same-game or non-allow-listed feature appears in the top-10
importance. Artifacts (`best_config.json`, `metrics.json`, `oof/` = one parquet per variant, `feature_importance.json`)
land in `artifacts/<timestamp>/`.
CRASH-SAFE: each finished arm, Optuna study, calibrator tuning and ablation group is checkpointed to
`<run folder>/checkpoints/<hash>/`; if the kernel dies, re-run the same notebook and it resumes (log lines
`RESUMED`). Small artifacts are written before the big files; `metrics.json` has `complete: false` until the
end and the `oof/` folder appears only when everything is written.
Score locally: `uv run python -m research.eval.ctxres_v2_eval --run <pulled run dir>` after
`make colab-pull JOB=ctxres_v2_sweep`. Rule: docs/CTXRES_V2.md."""

SETUP = """import os  # noqa: F811 (standalone setup cell; may run first)

# "full" (default, strict: every arm must finish) or "fast"
os.environ.setdefault("NBA_BUDGET", "full")
print("BUDGET:", os.environ["NBA_BUDGET"])"""


def cell(kind: str, src: str) -> dict:
    base = {"cell_type": kind, "metadata": {}, "source": src.splitlines(keepends=True)}
    if kind == "code":
        base.update(execution_count=None, outputs=[])
    return base


def build() -> dict:
    code = (HERE / "ctxres_v2_sweep.py").read_text()
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
    out = HERE / "ctxres_v2_sweep.ipynb"
    out.write_text(json.dumps(build(), indent=1) + "\n")
    print("wrote", out)


if __name__ == "__main__":
    main()
