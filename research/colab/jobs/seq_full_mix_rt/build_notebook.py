# mypy: ignore-errors
# ruff: noqa: E501
"""Build the SEQ_FULL_MIX_RT notebooks.

    uv run python research/colab/jobs/seq_full_mix_rt/build_notebook.py          # held-out run #2 (REFUSES unless docs/prereg/SEQ_FULL_MIX_RT.md is FROZEN)
    uv run python research/colab/jobs/seq_full_mix_rt_sel/build_notebook.py      # selection run #1 (draft allowed; not a holdout touch)

The held-out notebook bakes the frozen document's sha256 (the construction is W_ALL: all the data) and
refuses on the VM without the staged document and the staged holdout row. The selection notebook bakes the draft's sha256 for provenance only and cannot read
season 2025 (the base loader refuses it and the tail asserts it).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
BASE = ROOT / "research/colab/jobs/seq_props/seq_props_train.py"
TAIL = HERE / "seq_full_mix_rt_tail.py"
DOC = ROOT / "docs/prereg/SEQ_FULL_MIX_RT.md"
MARK_MAIN = "# --------------------------------------------------------------------------- main\n"
MARK_RESULTS = "=== RESULTS BELOW ===\n"

INTROS = {
    "held": """# seq_full_mix_rt -- SEQ_FULL_MIX_RT run #2 (E2 / E2P / E2S walk-forward over 2023-2025 on the real-tip export, W_ALL: all the data), T4 GPU

Runtime > Change runtime type > T4 GPU. Run all. Three programs, 3 seeds, every program completes. The notebook refuses unless the staged SEQ_FULL_MIX_RT.md is the
frozen document it was built for (sha256) and the staged HOLDOUT_ACCESS_LOG.md has the PRE-REGISTERED, DEVIN CONFIRMED row. It scores nothing.""",
    "sel": """# seq_full_mix_rt_sel -- SEQ_FULL_MIX_RT run #1 (selection; NOT a holdout touch): window W_ALL (programs E2, E2S; the construction) plus a W22 E2 diagnostic, scored on 2023 and 2024 only, T4 GPU

Runtime > Change runtime type > T4 GPU. Run all. Three programs (WALL_E2, WALL_E2S, W22_E2), 3 seeds each, about 22 minutes (range 16-35). The input holds nothing after 2024
and the notebook cannot read season 2025. It scores nothing; the gates are measured on W_ALL on the Mac.""",
}
SETUP = """try:  # Colab?
    from google.colab import drive  # type: ignore

    IN_COLAB = True
except ImportError:
    IN_COLAB = False

if IN_COLAB:
    drive.mount("/content/drive")
print("rule/draft sha256 baked in:", RULE_SHA, "mode", MODE, "window", WINDOW)"""


def head_text(text: str) -> str:
    head = text.split(MARK_RESULTS)[0]
    if head == text:
        raise SystemExit("results marker missing")
    return head


def rule_sha(mode: str, doc: Path | None = None) -> str:
    text = (doc or DOC).read_text()
    head = head_text(text)
    if mode == "held" and not any(
        line.replace("*", "").startswith("Status: FROZEN") for line in head.splitlines()
    ):
        raise SystemExit(
            "docs/prereg/SEQ_FULL_MIX_RT.md is not FROZEN; refusing to build the held-out notebook"
        )
    return hashlib.sha256(head.encode()).hexdigest()


def script(mode: str, sha: str, window: str) -> str:
    base = BASE.read_text()
    if MARK_MAIN not in base:
        raise SystemExit("base script layout changed")
    tail = (
        TAIL.read_text()
        .replace("__MODE__", mode)
        .replace("__RULE_SHA__", sha)
        .replace("__WINDOW__", window)
    )
    return base.split(MARK_MAIN)[0].rstrip("\n") + "\n\n\n" + tail


def cell(kind: str, src: str) -> dict:
    out = {"cell_type": kind, "metadata": {}, "source": src.splitlines(keepends=True)}
    if kind == "code":
        out.update(execution_count=None, outputs=[])
    return out


def build(mode: str, doc: Path | None = None) -> tuple[str, dict]:
    sha = rule_sha(mode, doc)
    window = "WALL" if mode == "held" else ""
    code = script(mode, sha, window)
    body = code.replace('if __name__ == "__main__":\n    main()\n', "").rstrip("\n")
    nb = {
        "cells": [
            cell("markdown", INTROS[mode]),
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


def write(mode: str, out_dir: Path, stem: str) -> None:
    code, nb = build(mode)
    (out_dir / f"{stem}_train.py").write_text(code)
    (out_dir / f"{stem}.ipynb").write_text(json.dumps(nb, indent=1) + "\n")
    print("wrote", out_dir / f"{stem}.ipynb")


if __name__ == "__main__":
    write("held", HERE, "seq_full_mix_rt")
