# ruff: noqa: E501
"""Maintainer tool + test helper: DraftKings fit rungs out of the frozen ALT_RUNG_CAL harness.

``nba`` never imports ``research``, so the arrays the static rung map is fit on are extracted here, by
the harness's own frame builder (``research.eval.alt_rung_cal_eval``), read-only against the main
checkout's data, and written to an .npz that ``python -m nba.parlay.rung_cal_build`` reads.

    .venv/bin/python -W ignore -m tests.parlay.rung_cal_harness --data-root /Users/devin/Downloads/nba-prediction \\
        --out <dir>/rung_fit_arrays.npz

Seasons 2023 and 2024 only (the harness asserts it; asserted again here). No odds price enters.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any

import numpy as np

KEYS = ("pm", "point", "med", "spr", "st10", "x", "zm", "r", "bench", "date", "stat",
        "season", "unit", "hit", "has_main")  # fmt: skip
DK_S = 162_745  # pinned DraftKings S (docs/prereg/ALT_RUNG_CAL.md harness contract item 4)


def extract(data_root: Path) -> dict[str, np.ndarray]:
    from research.eval import (
        alt_rung_cal_eval as h,  # noqa: PLC0415 - tests/tools may import research
    )

    fr = h.build_frames(data_root)
    R = fr["rungs"][h.PRIMARY]
    mismatch = _input_mismatch(h, fr["meta"])
    # odds_history.duckdb grew on 2026-10-15 (six combo markets ingested, rungs untouched): its sha no
    # longer equals the pinned one, so the pinned COUNTS are the guard. Anything else must match.
    if set(mismatch) - {"ODDS_HISTORY_SHA256"}:
        raise RuntimeError(f"pinned input sha256 mismatch: {mismatch}")
    if int(R["pm"].size) != DK_S:
        raise RuntimeError(f"DraftKings S is {R['pm'].size}, pinned {DK_S}")
    if int(R["season"].max()) > 2024:
        raise RuntimeError("season 2025 reached the rungs")
    h.attach_real_labels(fr, data_root)  # real rung labels: the map is fit on them (rule: CAL-rung)
    R = fr["rungs"][h.PRIMARY]
    out: dict[str, Any] = {k: R[k] for k in R if k in KEYS}
    out["hit"] = R["hit"]
    return out


def _input_mismatch(h: Any, meta: dict[str, Any]) -> dict[str, tuple[str, str]]:
    declared = _declared()
    got = {k: meta["input_sha256"][v] for k, v in h.INPUT_NAMES.items()}
    got |= {f"WINDOW_IDS_SHA256_{s}": meta["window_game_ids_sha256"][str(s)] for s in h.SEASONS}
    return {
        k: (v[:12], declared.get(k, "missing")[:12]) for k, v in got.items() if declared.get(k) != v
    }


def _declared() -> dict[str, str]:
    from research.eval import alt_rung_cal_rule as rule  # noqa: PLC0415

    return dict(rule.declared())


def npz_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    arrs = extract(Path(a.data_root))
    np.savez_compressed(a.out, **arrs)  # type: ignore[arg-type]
    print(f"wrote {a.out} rows {arrs['pm'].size} sha256 {npz_sha(Path(a.out))[:8]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
