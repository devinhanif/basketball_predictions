# ruff: noqa: E501
"""SEQ_FULL_MIX_RT scorer entry: the SEQ_FULL_MIX harness rebound to the RT rule (real-tip export, W_ALL window, its own outputs).

M="-m research.eval.seq_full_mix_rt_eval"
.venv/bin/python -W ignore $M gate --allow-draft --sel-run data/colab/runs/seq_full_mix_rt_sel/<run_id>   # RT gates on W_ALL from run #1 (stored A0 / FULL + run #1 SEQ)
.venv/bin/python -W ignore $M null --k 50 --sel-run <run #1 dir>
.venv/bin/python -W ignore $M rehearse --k 3 --sel-run <run #1 dir>        # 2025 path with 2024 standing in; run #1 files stand in for run #2
.venv/bin/python -W ignore $M run --sha <64 hex> --seq-run data/colab/runs/seq_full_mix_rt/<run_id>   # THE one 2025 touch (frozen + committed row)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from research.eval import seq_full_mix_eval as S

WINDOW = "WALL"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--sel-run", default=None)
    known, rest = ap.parse_known_args(argv)
    S.configure_rt(Path(known.sel_run) if known.sel_run else None, WINDOW)
    return S.main(rest)


if __name__ == "__main__":
    raise SystemExit(main())
