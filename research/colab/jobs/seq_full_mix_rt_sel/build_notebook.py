# mypy: ignore-errors
# ruff: noqa: E501
"""Build the selection notebook (run #1) of SEQ_FULL_MIX_RT: ``uv run python research/colab/jobs/seq_full_mix_rt_sel/build_notebook.py``."""

from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "seq_full_mix_rt_build", HERE.parent / "seq_full_mix_rt" / "build_notebook.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

if __name__ == "__main__":
    _mod.write("sel", HERE, "seq_full_mix_rt_sel")
