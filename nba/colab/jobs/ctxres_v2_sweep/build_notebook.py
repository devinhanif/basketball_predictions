# mypy: ignore-errors
# Colab-executed job script helper; kept out of strict type checking.
"""Regenerate ``ctxres_v2_sweep.ipynb`` from ``ctxres_v2_sweep.py`` (single source of truth).

uv run python nba/colab/jobs/ctxres_v2_sweep/build_notebook.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

INTRO = """# ctxres_v2_sweep -- context-residual v2 model-family sweep

Runtime > Change runtime type > **T4 GPU**, then Run all. `NBA_BUDGET=fast` (default) targets
15-20 min (unmeasured estimate; optional arms are skipped once 80% of a 25 min budget is used);
`NBA_BUDGET=full` is the long variant. The expected per-arm runtimes are printed at the top of the
run. Selection uses season 2023 only; season 2024 is report-only; season 2025 is refused.
The leak detector aborts the run if a same-game or non-allow-listed feature appears in the
top-10 importance. Artifacts (`best_config.json`, `metrics.json`, `oof_2024.parquet`,
`feature_importance.json`) land in `artifacts/<timestamp>/`.
Score locally: `uv run python -m nba.eval.ctxres_v2_eval --run <pulled run dir>` after
`make colab-pull JOB=ctxres_v2_sweep`. Rule: docs/CTXRES_V2.md."""

SETUP = """import os  # noqa: F811 (standalone setup cell; may run first)

os.environ.setdefault("NBA_BUDGET", "fast")  # "fast" (default) or "full"
print("BUDGET:", os.environ["NBA_BUDGET"])"""


def cell(kind: str, src: str) -> dict:
    base = {"cell_type": kind, "metadata": {}, "source": src.splitlines(keepends=True)}
    if kind == "code":
        base.update(execution_count=None, outputs=[])
    return base


def build() -> dict:
    code = (HERE / "ctxres_v2_sweep.py").read_text()
    code = code.replace('if __name__ == "__main__":\n    main()\n', "").rstrip("\n")
    return {
        "cells": [
            cell("markdown", INTRO),
            cell("code", code),
            cell("code", SETUP),
            cell("code", "metrics = main()"),
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
