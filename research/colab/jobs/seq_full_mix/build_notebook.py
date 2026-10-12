# mypy: ignore-errors
# ruff: noqa: E501
"""Build ``seq_full_mix_train.py`` and ``seq_full_mix.ipynb`` for the frozen SEQ_FULL_MIX rule.

Refuses unless docs/prereg/SEQ_FULL_MIX.md says FROZEN; the rule's sha256 (text above the results marker) is baked into the script and re-checked on
the Colab VM against the staged document, and the VM also refuses without the staged HOLDOUT_ACCESS_LOG.md row.

    uv run python research/colab/jobs/seq_full_mix/build_notebook.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
BASE = ROOT / "research/colab/jobs/seq_props/seq_props_train.py"
DOC = ROOT / "docs/prereg/SEQ_FULL_MIX.md"
MARK_MAIN = "# --------------------------------------------------------------------------- main\n"
MARK_RESULTS = "=== RESULTS BELOW ===\n"

INTRO = """# seq_full_mix -- SEQ_FULL_MIX rule (E2 walk-forward over 2023-2025, past-only placebo E2P, shuffled-input E2S), T4 GPU

Runtime > Change runtime type > T4 GPU. Run all. Three programs (E2, E2P, E2S), 3 seeds, 27 monthly warm refits each;
every program completes; about 15 minutes (range 10-30). The notebook refuses to run unless (1) the staged SEQ_FULL_MIX.md is
the frozen document it was built for (sha256 check) and (2) the staged HOLDOUT_ACCESS_LOG.md has the PRE-REGISTERED, DEVIN CONFIRMED
row for this rule. It writes `oof_<program>.npz` into `artifacts/<timestamp>/` as each finishes (rerun resumes). It scores nothing;
scoring happens once, on the Mac (`python -m research.eval.seq_full_mix_eval score`)."""

SETUP = """try:  # Colab?
    from google.colab import drive  # type: ignore

    IN_COLAB = True
except ImportError:
    IN_COLAB = False

if IN_COLAB:
    drive.mount("/content/drive")
print("rule sha256 baked in:", RULE_SHA)"""


def rule_sha() -> str:
    text = DOC.read_text()
    head = text.split(MARK_RESULTS)[0]
    if head == text:
        raise SystemExit("results marker missing")
    if not any(line.replace("*", "").startswith("Status: FROZEN") for line in head.splitlines()):
        raise SystemExit("docs/prereg/SEQ_FULL_MIX.md is not FROZEN; refusing to build")
    return hashlib.sha256(head.encode()).hexdigest()


def script(sha: str) -> str:
    base = BASE.read_text()
    if MARK_MAIN not in base:
        raise SystemExit("base script layout changed")
    head = base.split(MARK_MAIN)[0]
    tail = (HERE / "seq_full_mix_tail.py").read_text().replace("__RULE_SHA__", sha)
    return head.rstrip("\n") + "\n\n\n" + tail


def cell(kind: str, src: str) -> dict:
    base = {"cell_type": kind, "metadata": {}, "source": src.splitlines(keepends=True)}
    if kind == "code":
        base.update(execution_count=None, outputs=[])
    return base


def build() -> tuple[str, dict]:
    sha = rule_sha()
    code = script(sha)
    body = code.replace('if __name__ == "__main__":\n    main()\n', "").rstrip("\n")
    nb = {
        "cells": [
            cell("markdown", INTRO),
            cell("code", body),
            cell("code", SETUP),
            cell("code", "infos = main()"),
        ],
        "metadata": {
            "accelerator": "GPU",
            "colab": {"gpuType": "T4"},
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return code, nb


if __name__ == "__main__":
    code, nb = build()
    (HERE / "seq_full_mix_train.py").write_text(code)
    (HERE / "seq_full_mix.ipynb").write_text(json.dumps(nb, indent=1) + "\n")
    print("wrote", HERE / "seq_full_mix_train.py", "and seq_full_mix.ipynb")
