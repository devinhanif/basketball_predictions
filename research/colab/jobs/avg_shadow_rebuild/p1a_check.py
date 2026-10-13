# ruff: noqa: E501
"""Proof P1a (lineage; docs/prereg/AVG_SHADOW_LIVE.md): the rebuilt walk reproduces T478's 2024 regular-season pts AVG minus FULL.

PASS iff |rebuilt - (-0.0161)| <= 0.0021 (2 x sqrt2 x 0.00074, the seed sd of one 3-seed ensemble) on the same 25,767 rows. Only seasons <= 2024 are read
(the 2025 rows of the OOF file are dropped on the season key first). Run after the Colab rebuild:

    .venv/bin/python -W ignore research/colab/jobs/avg_shadow_rebuild/p1a_check.py --run <downloaded run folder>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _world  # noqa: E402

from nba.eval.context_residual_eval import crps_from_quantiles  # noqa: E402
from research.eval import seq_full_mix_eval as S  # noqa: E402
from research.eval import workload_eval as WE  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
STORED = (
    -0.016101018847856204
)  # reports/prereg_avg_shadow_live/gate.json, stats.pts.avg_minus_full.delta (n 25,767)
N_ROWS = 25767
TOL = 0.0021


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=ROOT / "reports/prereg_avg_shadow_live/p1a.json")
    a = ap.parse_args(argv)
    with WE.heavy():
        w, seq, _ = _world.world(a.run)
        b = w["base"]["pts"]
        assert int(b["season"].max()) <= 2024
        arms = S.build_arms("pts", b, w["date_days"], w["ctx"], {"E2": seq["pts"]})
        m = (b["season"] == 2024) & S.regular_flag(b["game_id"]).astype(bool)
        d = crps_from_quantiles(arms["AVG"].astype(float), b["y"]) - crps_from_quantiles(
            arms["FULL"].astype(float), b["y"]
        )
        got = float(d[m].mean())
    res = {
        "rebuilt": got,
        "stored": STORED,
        "abs_diff": abs(got - STORED),
        "tolerance": TOL,
        "n": int(m.sum()),
        "n_expected": N_ROWS,
        "pass": bool(abs(got - STORED) <= TOL and int(m.sum()) == N_ROWS),
        "run": str(a.run),
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
