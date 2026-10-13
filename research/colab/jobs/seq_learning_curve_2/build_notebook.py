# mypy: ignore-errors
# ruff: noqa: E501
"""Build the SEQ_LEARNING_CURVE_2 notebook (docs/prereg/SEQ_LEARNING_CURVE_2.md).

    uv run python research/colab/jobs/seq_learning_curve_2/build_notebook.py     # REFUSES unless the rule document is FROZEN (add --draft for the pre-freeze notebook, which refuses to run)

The notebook bakes the frozen document's sha256 and, on the VM, refuses without (1) the staged frozen document and (2) the loader-review attestation written by
`python -m research.eval.seq_learning_curve_2_rule stage` (which itself refuses without a committed docs/reviews/seq_learning_curve_2_loader_<date>.md descended from the
freeze commit). Building the notebook does not need the review; running it does.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
BASE = ROOT / "research/colab/jobs/seq_props/seq_props_train.py"
TAIL = HERE / "seq_learning_curve_2_tail.py"
DOC = ROOT / "docs/prereg/SEQ_LEARNING_CURVE_2.md"
MARK_MAIN = "# --------------------------------------------------------------------------- main\n"
MARK_RESULTS = "=== RESULTS BELOW ===\n"

INTRO = """# seq_learning_curve_2 -- SEQ_LEARNING_CURVE_2 (windows 4/6/8/9/10 of the E2 sequence transformer, unmatched and step-matched, 6 seeds, scored on 2023-24 only), T4 GPU

Runtime > Change runtime type > T4 GPU. Run all. 10 arms (U 4/6/8/9/10, M 6/8/9/10, UP 4) in 5 window units, 6 seeds each, full budgets, 9 monthly warm refits per walk; estimate 90-170 T4 minutes
(not measured), hard ceiling 240 cumulative minutes (a unit not finished is reported as unfinished). The notebook refuses unless the staged SEQ_LEARNING_CURVE_2.md is the frozen
document it was built for AND the staged loader-review attestation is present and matches. Nothing after season 2023 can be loaded. It scores nothing."""
SETUP = """try:  # Colab?
    from google.colab import drive  # type: ignore

    IN_COLAB = True
except ImportError:
    IN_COLAB = False

if IN_COLAB:
    drive.mount("/content/drive")
print("rule sha256 baked in:", RULE_SHA)"""


def head_text(text: str) -> str:
    head = text.split(MARK_RESULTS)[0]
    if head == text:
        raise SystemExit("results marker missing")
    return head


def rule_sha(doc: Path | None = None) -> str:
    head = head_text((doc or DOC).read_text())
    if not any(line.replace("*", "").startswith("Status: FROZEN") for line in head.splitlines()):
        raise SystemExit(
            "docs/prereg/SEQ_LEARNING_CURVE_2.md is not FROZEN; refusing to build the notebook"
        )
    return hashlib.sha256(head.encode()).hexdigest()


def script(sha: str) -> str:
    base = BASE.read_text()
    if MARK_MAIN not in base:
        raise SystemExit("base script layout changed")
    tail = TAIL.read_text().replace("__RULE_SHA__", sha)
    return base.split(MARK_MAIN)[0].rstrip("\n") + "\n\n\n" + tail


def cell(kind: str, src: str) -> dict:
    out = {"cell_type": kind, "metadata": {}, "source": src.splitlines(keepends=True)}
    if kind == "code":
        out.update(execution_count=None, outputs=[])
    return out


DRAFT_SHA = "DRAFT_NOT_FROZEN"  # a draft notebook can never pass check_gates: the staged document must hash to RULE_SHA and say Status: FROZEN


def build(doc: Path | None = None, draft: bool = False) -> tuple[str, dict]:
    sha = DRAFT_SHA if draft else rule_sha(doc)
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


def write(out_dir: Path = HERE, stem: str = "seq_learning_curve_2", draft: bool = False) -> None:
    code, nb = build(draft=draft)
    (out_dir / f"{stem}_train.py").write_text(code)
    (out_dir / f"{stem}.ipynb").write_text(json.dumps(nb, indent=1) + "\n")
    print("wrote", out_dir / f"{stem}.ipynb")


if __name__ == "__main__":
    import sys

    write(
        draft="--draft" in sys.argv
    )  # --draft: the pre-freeze notebook (refuses to run); without it the builder refuses unless the rule is FROZEN
