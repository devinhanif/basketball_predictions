# mypy: ignore-errors
# ruff: noqa: E501
"""Build the AVG_SHADOW_LIVE_2 chain-rebuild notebook.

    uv run python research/colab/jobs/avg_shadow_rebuild/build_notebook.py

Refuses unless docs/prereg/AVG_SHADOW_LIVE_2.md is FROZEN and its sha256 starts with the pinned 9afbfeb6, bakes its sha256 (text above the results marker) into the notebook, and takes the T478
walk-forward functions VERBATIM from ../seq_full_mix_rt/seq_full_mix_rt_tail.py (AST extraction, no retyping), splicing exactly two blocks (the P1b weight saves at four mid-season months and the install fit with the weight save) into ``run_walk_forward``. A missing anchor or function is a build error, never a silent fallback.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
BASE = ROOT / "research/colab/jobs/seq_props/seq_props_train.py"
RT_TAIL = ROOT / "research/colab/jobs/seq_full_mix_rt/seq_full_mix_rt_tail.py"
TAIL = HERE / "avg_shadow_rebuild_tail.py"
DOC = ROOT / "docs/prereg/AVG_SHADOW_LIVE_2.md"
RULE_SHA_PIN = "9afbfeb6"  # AVG_SHADOW_LIVE_2, frozen 2026-10-14 (commit 6e86f1a); any other rule text is a build error
MARK_MAIN = "# --------------------------------------------------------------------------- main\n"
MARK_RESULTS = "=== RESULTS BELOW ===\n"
COPIED = ("load_npz_25", "concat_rows", "month_blocks", "to_dev", "oof_dict", "run_walk_forward")
ANCHOR_OOF = "    oof = {k: np.concatenate([f[k] for f in frames]) for k in frames[0]}\n"
ANCHOR_PRED = '        m, q = ensemble([predict(x, ts, to_dev(blk, dev), cold["batch"], dev) for x in nets])\n'
ANCHOR_RET = '        "blocks_detail": detail, "window": window,\n'

P1B_BLOCK = """        if str(per) in P1B_MONTHS:  # proof P1b: the weights of this block, saved BEFORE it is predicted, so the CPU can score the same weights the GPU scored
            (out_dir / "p1b" / str(per)).mkdir(parents=True, exist_ok=True)
            for k_, net in enumerate(nets):
                torch.save({n: p.detach().cpu() for n, p in net.state_dict().items()}, out_dir / "p1b" / str(per) / f"seed{seeds[k_]}.pt")
"""

INSTALL_BLOCK = """    # ---- AVG_SHADOW_LIVE install fit (the ONLY addition to T478's walk): the same warm refit (seed 1000 + k, warm config) on EVERY row dated
    # before INSTALL_BEFORE (the pool of month block INSTALL_MONTH), then SAVE the weights. The walk above is unchanged and its OOF rows are
    # consumed only by the gate fit (fit_gate.py); nothing here scores or reads a forecast.
    install_pool = np.flatnonzero((date < np.datetime64(INSTALL_BEFORE)) & (nprior >= 3))
    assert date[install_pool].max() < np.datetime64(INSTALL_BEFORE), "an install-pool row is not before the block"
    install_rows = to_dev(install_pool, dev)
    for k_, net in enumerate(nets):
        seed_everything(1000 + k_)
        fit(net, ts, install_rows, None, warm, dev, warm["lr"], warm["epochs"], 99)
        torch.save({n: p.detach().cpu() for n, p in net.state_dict().items()}, out_dir / f"seed{seeds[k_]}.pt")
    (out_dir / "vocab.json").write_text(json.dumps({str(k): int(v) for k, v in vocab.items()}))
    install_fit = {"pool_rows": int(len(install_pool)), "fit_through": str(date[install_pool].max()), "n_vocab": int(len(vocab))}
    print("  [install fit]", install_fit, f"elapsed {time.monotonic() - t0:.0f}s", flush=True)
"""


def head_text(text: str) -> str:
    head = text.split(MARK_RESULTS)[0]
    if head == text:
        raise SystemExit("results marker missing")
    return head


def rule_sha(doc: Path | None = None) -> str:
    head = head_text((doc or DOC).read_text())
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
    tree = ast.parse(src)
    funcs = {
        n.name: ast.get_source_segment(src, n) for n in tree.body if isinstance(n, ast.FunctionDef)
    }
    missing = [n for n in COPIED if n not in funcs]
    if missing:
        raise SystemExit(f"T478 tail no longer defines {missing}")
    walk = funcs["run_walk_forward"]
    if walk.count(ANCHOR_OOF) != 1 or walk.count(ANCHOR_RET) != 1 or walk.count(ANCHOR_PRED) != 1:
        raise SystemExit(
            "run_walk_forward changed: the install-fit anchors are not found exactly once"
        )
    walk = (
        walk.replace(ANCHOR_PRED, P1B_BLOCK + ANCHOR_PRED)
        .replace(ANCHOR_OOF, INSTALL_BLOCK + ANCHOR_OOF)
        .replace(ANCHOR_RET, ANCHOR_RET + '        "install_fit": install_fit,\n')
    )
    funcs["run_walk_forward"] = walk
    return "\n\n\n".join(funcs[n] for n in COPIED)


def formatted(code: str) -> str:
    """The generated script as ``ruff format`` leaves it (the repo checks formatting of research/ and the notebook must equal the script)."""
    import subprocess
    import sys

    cmd = [sys.executable, "-m", "ruff", "format", "--stdin-filename", "gen.py", "-"]
    return subprocess.run(
        cmd, input=code, capture_output=True, text=True, check=True, cwd=ROOT
    ).stdout


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


INTRO = """# avg_shadow_rebuild -- AVG_SHADOW_LIVE_2 one-time chain rebuild (E2, W_ALL, 3 seeds, weights SAVED), T4 GPU

Runtime > Change runtime type > T4 GPU. Run all. One program (E2, 3 seeds): T478's cold fit and monthly warm refits through the last settled month, then the
install fit (every row before 2026-10-01) with the three state dicts and the vocabulary saved. The notebook refuses unless the staged AVG_SHADOW_LIVE_2.md is the
frozen document it was built for (sha256) and the staged HOLDOUT_ACCESS_LOG.md has the 2026-10-13 rebuild row. It scores nothing. Budget 45 T4-minutes, ceiling 60.
Download seed0.pt seed1.pt seed2.pt vocab.json bundle_parts.json oof_E2.npz info_E2.json metrics.json config.json from the run folder."""
SETUP = """try:  # Colab?
    from google.colab import drive  # type: ignore

    IN_COLAB = True
except ImportError:
    IN_COLAB = False

if IN_COLAB:
    drive.mount("/content/drive")
print("rule sha256 baked in:", RULE_SHA, "window", WINDOW)"""


def cell(kind: str, src: str) -> dict:
    out = {"cell_type": kind, "metadata": {}, "source": src.splitlines(keepends=True)}
    if kind == "code":
        out.update(execution_count=None, outputs=[])
    return out


def build(doc: Path | None = None) -> tuple[str, dict]:
    sha = rule_sha(doc)
    code = formatted(script(sha))
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


def write(out_dir: Path = HERE, stem: str = "avg_shadow_rebuild") -> None:
    code, nb = build()
    (out_dir / f"{stem}_train.py").write_text(code)
    (out_dir / f"{stem}.ipynb").write_text(json.dumps(nb, indent=1) + "\n")
    print("wrote", out_dir / f"{stem}.ipynb", "rule sha256", rule_sha())


if __name__ == "__main__":
    write()
