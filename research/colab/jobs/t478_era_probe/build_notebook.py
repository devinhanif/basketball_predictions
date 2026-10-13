# mypy: ignore-errors
# ruff: noqa: E501
"""Build the T478 era-probe notebook: ``uv run python research/colab/jobs/t478_era_probe/build_notebook.py [--allow-draft]``.

The notebook is the seq_props model code + the SEQ_FULL_MIX_RT tail (sel mode, season 2025 unreadable) + this job's tail. It bakes the sha256 of
reports/t478_era_probe/PLAN.md above its results marker (provenance). Without ``--allow-draft`` it refuses unless the plan's status line is COMMITTED
(the main session sets it when it commits the plan), so rebuild once after that line changes."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
BASE = ROOT / "research/colab/jobs/seq_props/seq_props_train.py"
RT_TAIL = ROOT / "research/colab/jobs/seq_full_mix_rt/seq_full_mix_rt_tail.py"
TAIL = HERE / "t478_era_probe_tail.py"
PLAN = ROOT / "reports/t478_era_probe/PLAN.md"
MARK_MAIN = "# --------------------------------------------------------------------------- main\n"
MARK_RESULTS = "=== RESULTS BELOW ===\n"
INTRO = """# t478_era_probe -- ENSEMBLE's P-emb era-embedding probe on the W_ALL sequence model (descriptive; no bearing on T478), T4 GPU

Runtime > Change runtime type > T4 GPU. Run all. One program (WALL_E2, seed 0, seasons 2019-2024 only; 2025 cannot be read), about 7 minutes (5-12).
It writes the head-input states (128-d); the AUCs are computed on the Mac by `research/eval/t478_era_probe.py`. Plan: reports/t478_era_probe/PLAN.md."""
SETUP = """try:  # Colab?
    from google.colab import drive  # type: ignore

    IN_COLAB = True
except ImportError:
    IN_COLAB = False

if IN_COLAB:
    drive.mount("/content/drive")
print("plan sha256 baked in:", PLAN_SHA)"""


def plan_sha(allow_draft: bool = False, doc: Path | None = None) -> str:
    text = (doc or PLAN).read_text()
    head = text.split(MARK_RESULTS)[0]
    if head == text:
        raise SystemExit("results marker missing from the plan")
    committed = any(ln.replace("*", "").startswith("Status: COMMITTED") for ln in head.splitlines())
    if not committed and not allow_draft:
        raise SystemExit("PLAN.md is not COMMITTED; refusing (use --allow-draft only to preview)")
    return hashlib.sha256(head.encode()).hexdigest()


def script(sha: str) -> str:
    base, rt = BASE.read_text(), RT_TAIL.read_text()
    if MARK_MAIN not in base:
        raise SystemExit("base script layout changed")
    rt = rt.replace("__MODE__", "sel").replace("__RULE_SHA__", "none").replace("__WINDOW__", "")
    rt = rt.replace('if __name__ == "__main__":\n    main()\n', "")
    tail = TAIL.read_text().replace("__PLAN_SHA__", sha)
    return base.split(MARK_MAIN)[0].rstrip("\n") + "\n\n\n" + rt.rstrip("\n") + "\n\n\n" + tail


def cell(kind: str, src: str) -> dict:
    out = {"cell_type": kind, "metadata": {}, "source": src.splitlines(keepends=True)}
    if kind == "code":
        out.update(execution_count=None, outputs=[])
    return out


def build(allow_draft: bool = False, doc: Path | None = None) -> tuple[str, dict]:
    code = script(plan_sha(allow_draft, doc))
    body = code.replace('if __name__ == "__main__":\n    main()\n', "").rstrip("\n")
    nb = {
        "cells": [
            cell("markdown", INTRO),
            cell("code", body),
            cell("code", SETUP),
            cell("code", "infos = main_probe()"),
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
    code, nb = build("--allow-draft" in sys.argv)
    (HERE / "t478_era_probe_train.py").write_text(code)
    (HERE / "t478_era_probe.ipynb").write_text(json.dumps(nb, indent=1) + "\n")
    print("wrote", HERE / "t478_era_probe.ipynb")
