# ruff: noqa: E501
# mypy: ignore-errors
# Colab-executed job script helper; kept out of strict type checking.
"""Regenerate ``joint_game_set.ipynb`` from ``joint_game_set.py`` (single source of truth).

uv run python research/colab/jobs/joint_game_set/build_notebook.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

INTRO = """# joint_game_set -- set transformer over a whole game (joint pts/reb/ast/fg3m + margin + total)

Runtime > Change runtime type > **a GPU** (T4, or L4 / A100 if your Colab Pro offers them; the job is
device-agnostic and prints which GPU it got), then Run all.

* `NBA_BUDGET=full` (default, thorough): 24 random-search trials (early stopped on a time-ordered 2023 slice),
  5-seed final ensemble fit through 2023, 100k joint draws per game, monthly walk-forward refits for 2024.
  Expected about 60-100 min on a T4/L4 and 40-60 min on an A100. These are ESTIMATES from CPU step timings;
  the per-stage estimates print at the top and a measured projection prints after the first trial.
* `NBA_BUDGET=fast`: about 25-40 min (8 trials, 3 seeds).
* Selection uses season 2023 only; season 2024 is report-only; season 2025 is refused.
* CRASH-SAFE: mid-trial / mid-fit state every few epochs, every finished trial, final model, walk-forward model
  and prediction chunk is checkpointed to `<run folder>/checkpoints/<hash>/`. If the kernel dies, re-run the same
  notebook and it resumes (log lines `RESUMED`). Small artifacts first; `metrics.json` is written LAST
  (`metrics_partial.json` before that).
Score locally (applies the pre-registered rule in docs/JOINT_GAME_SET.md): `make colab-pull JOB=joint_game_set`
then `uv run python -m research.eval.joint_game_set_eval --run data/colab/runs/joint_game_set/<run_id>`."""

SETUP = """import os  # noqa: F811 (standalone setup cell; may run first)

# "full" (default, thorough), "fast" or "smoke"
os.environ.setdefault("NBA_BUDGET", "full")
print("BUDGET:", os.environ["NBA_BUDGET"])"""


def cell(kind: str, src: str) -> dict:
    base = {"cell_type": kind, "metadata": {}, "source": src.splitlines(keepends=True)}
    if kind == "code":
        base.update(execution_count=None, outputs=[])
    return base


def build() -> dict:
    code = (HERE / "joint_game_set.py").read_text()
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
            "colab": {"provenance": []},
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        },
        "nbformat": 4,
        "nbformat_minor": 4,
    }


def main() -> None:
    out = HERE / "joint_game_set.ipynb"
    out.write_text(json.dumps(build(), indent=1) + "\n")
    print("wrote", out)


if __name__ == "__main__":
    main()
