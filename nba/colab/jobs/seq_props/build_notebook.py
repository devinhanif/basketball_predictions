"""Regenerate ``seq_props.ipynb`` from ``seq_props_train.py`` (single source of truth).

uv run python nba/colab/jobs/seq_props/build_notebook.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

INTRO = """# seq_props -- GPU sequence model for player props

Set Runtime > GPU (T4). Run all. Default `NBA_BUDGET=fast` (about 10-15 min on a T4):
transformer (2 layers, d=64), 2 seeds, trained through the 2023 season once, predicts 2024.
`NBA_BUDGET=full` adds a GRU arm, 3 seeds and walk-forward refits (about 35-50 min).
Season 2025 (frozen holdout) is not in the input and is refused if present.
Artifacts (`weights.pt`, `config.json`, `metrics.json`, `oof_2024.parquet`)
land in `artifacts/<timestamp>/`.
Score locally with `python -m nba.eval.seq_props_eval` after `make colab-pull`."""

SETUP = """try:  # Colab?
    from google.colab import drive  # type: ignore

    IN_COLAB = True
except ImportError:
    IN_COLAB = False

if IN_COLAB:
    drive.mount("/content/drive")
os.environ.setdefault("NBA_BUDGET", "fast")  # "fast" (default) or "full"
print("BUDGET:", os.environ["NBA_BUDGET"])"""


def cell(kind: str, src: str) -> dict:
    base = {"cell_type": kind, "metadata": {}, "source": src.splitlines(keepends=True)}
    if kind == "code":
        base.update(execution_count=None, outputs=[])
    return base


def build() -> dict:
    code = (HERE / "seq_props_train.py").read_text()
    code = code.replace('if __name__ == "__main__":\n    main()\n', "")  # the last cell runs it
    code = code.rstrip("\n")
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
        "nbformat_minor": 5,
    }


if __name__ == "__main__":
    (HERE / "seq_props.ipynb").write_text(json.dumps(build(), indent=1) + "\n")
    print("wrote", HERE / "seq_props.ipynb")
