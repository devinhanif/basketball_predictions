# ruff: noqa: E501
# mypy: ignore-errors
# Colab-executed job script helper; kept out of strict type checking.
"""Regenerate ``ctxres_v3_hybrid.ipynb`` from the frozen engine copy + ``ctxres_v3_hybrid.py``.

uv run python nba/colab/jobs/ctxres_v3_hybrid/build_notebook.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN_GUARD = 'if __name__ == "__main__":\n    entry()\n'
V3_GUARD = 'if __name__ == "__main__":\n    entry_v3()\n'

INTRO = """# ctxres_v3_hybrid -- experiment 3: CONFIRMATORY season-2025 touch (frozen per-stat hybrid)

Runtime > Change runtime type > **T4 GPU** (or better; the GPU is printed), then Run all.

Pre-registration: docs/CTXRES_V3.md. NOTHING is searched or tuned here: pts <- 19-quantile XGBoost,
reb/ast/fg3m <- Poisson+NegBin XGBoost, frozen experiment-2 hyperparameters; Platt on pts thresholds only.
Walk-forward by month over season 2025; every block is fit on all played rows strictly before it.
Comparator: `v1_prod` (production LightGBM recipe, v1 features) refit identically; plus the recency baseline.

**The run refuses to start** unless `NBA_PREREGISTERED_ID` equals the `preregistration_id` staged in
`job_meta.json` (the maintainer sets it in job.yaml only after the HOLDOUT_ACCESS_LOG row exists). Set
it in the second code cell below (or put it in job.yaml `env`), exactly as printed in RUN.md.

CRASH-SAFE: each finished arm is checkpointed to `<run folder>/checkpoints/<hash>/`; if the kernel dies,
re-run the same notebook and it resumes (log lines `RESUMED`) into the same artifact folder. Small artifacts
(`frozen_config.json`, `run_header.json`, `leak_audit.json`) are written first; `metrics.json` has
`complete: false` until the end and `oof/` appears only when everything is written.
Score locally, ONCE: `uv run python -m nba.eval.ctxres_v3_eval --run <pulled run dir>`
(refuses a second application to the same run without --force)."""

SETUP = """import os  # noqa: F811 (standalone setup cell; may run first)

os.environ.setdefault("NBA_BUDGET", "full")  # full only (smoke = CPU test budget)
# Confirmatory guard: set to the preregistration_id in RUN.md / job_meta.json.
# Left empty the run refuses to start.
os.environ.setdefault("NBA_PREREGISTERED_ID", "")
# descriptive seed-sensitivity reruns of the two component arms
os.environ.setdefault("NBA_V3_REPLICATES", "1")
_has_id = bool(os.environ["NBA_PREREGISTERED_ID"])
print("BUDGET:", os.environ["NBA_BUDGET"], "| prereg id set:", _has_id)"""


def cell(kind: str, src: str) -> dict:
    base = {"cell_type": kind, "metadata": {}, "source": src.splitlines(keepends=True)}
    if kind == "code":
        base.update(execution_count=None, outputs=[])
    return base


def engine_source() -> str:
    from_file = (HERE / "ctxres_v2_engine.py").read_text()
    code = from_file.replace(MAIN_GUARD, "").rstrip("\n")
    return code


def v3_source() -> str:
    code = (HERE / "ctxres_v3_hybrid.py").read_text().replace(V3_GUARD, "").rstrip("\n")
    return code


def engine_sha256() -> str:
    return hashlib.sha256((HERE / "ctxres_v2_engine.py").read_bytes()).hexdigest()


def build() -> dict:
    return {
        "cells": [
            cell("markdown", INTRO),
            cell("code", engine_source()),
            cell("code", SETUP),
            cell("code", v3_source()),
            cell("code", "metrics = entry_v3()"),
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
    out = HERE / "ctxres_v3_hybrid.ipynb"
    out.write_text(json.dumps(build(), indent=1) + "\n")
    print("wrote", out, "engine sha256", engine_sha256())


if __name__ == "__main__":
    main()
