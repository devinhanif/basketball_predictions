# ruff: noqa: E501
"""Assemble the weights bundle from a finished rebuild run folder and the fitted gate, then verify it.

    .venv/bin/python research/colab/jobs/avg_shadow_rebuild/assemble_bundle.py --run <downloaded run folder> --gate gate.json \
        --gate-fit-through 2026-06-13 --out data/avg_shadow/incoming/2026-10

Writes ``manifest.json`` (shas, ``model_id``, ``fit_through`` from the rebuild's install pool, ``month_block``, gate id) next to the copied files and calls
``nba.daily.avg_shadow.bundle.verify``. It does NOT install: ``python -m nba.daily.avg_shadow.bundle install <out>`` stamps ``installed_at`` and is the
maintainer's act, before the first game of the month.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from nba.daily.avg_shadow import RULE_SHA256
from nba.daily.avg_shadow import bundle as B


def assemble(
    run: Path, gate: Path, gate_fit_through: str, out: Path, data_version: str | None = None
) -> B.Bundle:
    parts = json.loads((run / "bundle_parts.json").read_text())
    if parts["rule_sha256"] != RULE_SHA256:
        raise ValueError(f"the run was built for rule {parts['rule_sha256']}, not {RULE_SHA256}")
    if parts.get("smoke"):
        raise ValueError("a SMOKE run cannot be assembled into a bundle")
    out.mkdir(parents=True, exist_ok=True)
    for name in ("seed0.pt", "seed1.pt", "seed2.pt", "vocab.json"):
        shutil.copy2(run / name, out / name)
        if B.file_sha256(out / name) != parts["weights_sha256"][name]:
            raise ValueError(f"{name}: sha256 differs from the one the notebook recorded")
    shutil.copy2(gate, out / "gate.json")
    files = {n: B.file_sha256(out / n) for n in B.DATA_FILES}
    manifest = {
        "rule_sha256": RULE_SHA256, "kind": parts.get("kind", "E2_W_ALL_REBUILD"), "model_id": B.model_id_of(files), "files": files, "seeds": parts["seeds"],
        "hidden": parts["hidden"], "n_step": parts["n_step"], "n_ctx": parts["n_ctx"], "month_block": parts["month_block"],
        "fit_through": parts["fit_through"][:10], "gate_id": files["gate.json"], "gate_fit_through": gate_fit_through,
        "export_npz_sha256": parts["export_npz_sha256"], "hist_npz_sha256": parts["hist_npz_sha256"], "w_all_seasons": parts["w_all_seasons"],
        "data_version": data_version, "oof_sha256": parts.get("oof_sha256"),
    }  # fmt: skip
    for k in (
        "prev_model_id",
        "prev_month_block",
        "live_npz_sha256",
    ):  # present for the monthly refit (research/colab/jobs/avg_shadow_monthly), absent for the rebuild
        if k in parts:
            manifest[k] = parts[k]
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return B.verify(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--gate", type=Path, required=True)
    ap.add_argument("--gate-fit-through", required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    from nba.daily.stamps import current_data_version

    b = assemble(a.run, a.gate, a.gate_fit_through, a.out, current_data_version())
    print(
        json.dumps(
            {
                "model_id": b.model_id,
                "month_block": b.month_block,
                "fit_through": str(b.fit_through),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
