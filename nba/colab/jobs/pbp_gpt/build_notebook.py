# ruff: noqa: E501
# mypy: ignore-errors
# Colab-executed job script helper; kept out of strict type checking.
"""Regenerate ``pbp_gpt.ipynb`` from ``pbp_gpt.py`` (single source of truth).

uv run python nba/colab/jobs/pbp_gpt/build_notebook.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

INTRO = """# pbp_gpt -- decoder-only transformer over tokenized play-by-play ("basketball GPT")

Runtime > Change runtime type > **a GPU** (T4, or L4 / A100 if your Colab Pro offers them; the job is
device-agnostic and prints which GPU it got), then Run all.

* `NBA_BUDGET=full` (default, thorough): 6 search trials (default / large / 4 random configs, early stopped on the
  last 20% of season 2023), then batched sampling from five checkpoints of 300 seeded 2024 games
  (tip on 150): 256-1024 continuations each. Expected about 2-3 h on a T4/L4 and 1-1.5 h on an A100. These are
  ESTIMATES; a measured projection prints after epoch 1 and after the first sampled chunk.
* `NBA_BUDGET=fast`: about 40-70 min (3 trials, 100 live games).
* Season 2024 is report-only; season 2025 is refused.
* CRASH-SAFE: per-epoch checkpoints of the current trial, every finished trial, the selected model and every sampled
  chunk are written to `<run folder>/checkpoints/<hash>/`. If the kernel dies, re-run the same notebook and it
  resumes (log lines `RESUMED`). Small artifacts first; `metrics.json` is written LAST (`metrics_partial.json` before).
Score locally (applies the pre-registered rules in docs/PBP_GPT.md): `make colab-pull JOB=pbp_gpt` then
`uv run python -m nba.eval.pbp_gpt_eval --run data/colab/runs/pbp_gpt/<run_id>/artifacts/<ts> --data data/colab/pbp_gpt`."""

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
    code = (HERE / "pbp_gpt.py").read_text()
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
    out = HERE / "pbp_gpt.ipynb"
    out.write_text(json.dumps(build(), indent=1) + "\n")
    print("wrote", out)


if __name__ == "__main__":
    main()
