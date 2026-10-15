# mypy: ignore-errors
# ruff: noqa: E501
"""Build the AVG_SHADOW_LIVE_2 MONTHLY-refit notebook (one warm block x 3 seeds, T4).

    uv run python research/colab/jobs/avg_shadow_monthly/build_notebook.py

Same construction as ../avg_shadow_rebuild/build_notebook.py (rule pin 9afbfeb6, results-marker sha, T478 model code from ../seq_props, helper functions copied by
AST from the T478 tail, never retyped). Writes ``avg_shadow_monthly.ipynb`` and the script it embeds (``avg_shadow_monthly_train.py``).
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
BASE = ROOT / "research/colab/jobs/seq_props/seq_props_train.py"
RT_TAIL = ROOT / "research/colab/jobs/seq_full_mix_rt/seq_full_mix_rt_tail.py"
TAIL = HERE / "avg_shadow_monthly_tail.py"
DOC = ROOT / "docs/prereg/AVG_SHADOW_LIVE_2.md"
RULE_SHA_PIN = "9afbfeb6"
MARK_MAIN = "# --------------------------------------------------------------------------- main\n"
MARK_RESULTS = "=== RESULTS BELOW ===\n"
COPIED = ("concat_rows", "to_dev")


def _sibling():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location(
        "avg_shadow_rebuild_build_notebook_for_monthly",
        ROOT / "research/colab/jobs/avg_shadow_rebuild/build_notebook.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rule_sha(doc: Path | None = None) -> str:
    text = (doc or DOC).read_text()
    head = text.split(MARK_RESULTS)[0]
    if head == text:
        raise SystemExit("results marker missing")
    if not any(line.replace("*", "").startswith("Status: FROZEN") for line in head.splitlines()):
        raise SystemExit(
            "docs/prereg/AVG_SHADOW_LIVE_2.md is not FROZEN; refusing to build the notebook"
        )
    sha = hashlib.sha256(head.encode()).hexdigest()
    if not sha.startswith(RULE_SHA_PIN):
        raise SystemExit(
            f"rule text sha {sha[:8]} is not AVG_SHADOW_LIVE_2 {RULE_SHA_PIN}; refusing to build the notebook"
        )
    return sha


def copied_functions(rt_tail: Path | None = None) -> str:
    src = (rt_tail or RT_TAIL).read_text()
    funcs = {
        n.name: ast.get_source_segment(src, n)
        for n in ast.parse(src).body
        if isinstance(n, ast.FunctionDef)
    }
    missing = [n for n in COPIED if n not in funcs]
    if missing:
        raise SystemExit(f"T478 tail no longer defines {missing}")
    return "\n\n\n".join(funcs[n] for n in COPIED)


def script(sha: str) -> str:
    base = BASE.read_text()
    if MARK_MAIN not in base:
        raise SystemExit("base script layout changed")
    tail = (
        TAIL.read_text()
        .replace("__RULE_SHA__", sha)
        .replace("__COPIED_FUNCTIONS__", copied_functions())
    )
    return base.split(MARK_MAIN)[0].rstrip("\n") + "\n\n\n" + tail


INTRO = """# avg_shadow_monthly -- AVG_SHADOW_LIVE_2 monthly refit (one warm block x 3 seeds), T4 GPU

Runtime > Change runtime type > T4 GPU. Run all. Staged folder (docs/runbooks/AVG_SHADOW_MONTHLY_REFIT.md): the rebuild's cur25 and hist exports, the month's live export
(`export_monthly.py`), `export_info_live.json`, `prev_bundle/` (last month's installed seed0-2.pt, vocab.json, manifest.json), the frozen AVG_SHADOW_LIVE_2.md and the
HOLDOUT_ACCESS_LOG.md that carries the monthly-refit row for the month. The notebook refuses unless the rule sha256 is the frozen one it was built for and that log row
exists. It scores nothing. Budget 20 T4-minutes, ceiling 60 (halts, never cuts an arm).
Download seed0.pt seed1.pt seed2.pt vocab.json bundle_parts.json config.json metrics.json from the run folder."""
SETUP = """try:  # Colab?
    from google.colab import drive  # type: ignore

    IN_COLAB = True
except ImportError:
    IN_COLAB = False

if IN_COLAB:
    drive.mount("/content/drive")
if "NBA_PARQUET" not in os.environ:  # the staged run folder: the newest one holding export_info_live.json
    import glob

    hits = glob.glob("/content/drive/MyDrive/nba_colab/avg_shadow_monthly/*/export_info_live.json") + glob.glob("./**/export_info_live.json", recursive=True)
    if not hits:
        raise FileNotFoundError("no staged run folder (export_info_live.json): copy data/colab/avg_shadow_monthly/<month>/run to Drive nba_colab/avg_shadow_monthly/<month>")
    run_folder = Path(max(hits, key=os.path.getmtime)).parent
    os.environ["NBA_PARQUET"] = str(run_folder / "seq_ensemble_rt_cur25.npz")
    os.environ.setdefault("NBA_ARTIFACT_ROOT", str(run_folder / "artifacts"))
    print("staged run folder:", run_folder)
print("rule sha256 baked in:", RULE_SHA)"""


def build(doc: Path | None = None) -> tuple[str, dict]:
    sha = rule_sha(doc)
    sib = _sibling()
    code = sib.formatted(script(sha))
    body = code.replace('if __name__ == "__main__":\n    main()\n', "").rstrip("\n")
    nb = {
        "cells": [
            sib.cell("markdown", INTRO),
            sib.cell("code", body),
            sib.cell("code", sib.formatted(SETUP).rstrip("\n")),
            sib.cell("code", "infos = main()"),
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


def write(out_dir: Path = HERE, stem: str = "avg_shadow_monthly") -> None:
    code, nb = build()
    (out_dir / f"{stem}_train.py").write_text(code)
    (out_dir / f"{stem}.ipynb").write_text(json.dumps(nb, indent=1) + "\n")
    print("wrote", out_dir / f"{stem}.ipynb", "rule sha256", rule_sha())


if __name__ == "__main__":
    write()
