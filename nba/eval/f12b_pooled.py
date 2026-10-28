"""F12B_POOLED minimum-data gate (rule: docs/prereg/F12B_POOLED.md, DRAFT). Counts, precision.

    .venv/bin/python -m nba.eval.f12b_pooled gate --out reports/prereg_f12b_pooled/gate.json

Two pooled windows, each read-only: SELECT = seasons 2013-2021 from
``data/history/nba_history.duckdb``, REPORT = seasons 2022-2024 from ``nba.duckdb``. Per season:
G1a, G1b, G2-A1, G3 (F12B_CLOSING_RISK's checks, unchanged). Per window: G4 cell counts and G5,
the projected 95% half-width of the pooled young gap (game-clustered bootstrap spread of the
NO-exclusion young gap over every game of the window, already measured in F12_RETEST / F12B's
audit, inflated by sqrt(12 N / sum A_g) over the pooled young x blowout cell). No ratio with
garbage time removed is computed and no gap point estimate is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.eval import f12b_closing_risk as F

WINDOWS: dict[str, tuple[str, tuple[int, ...]]] = {
    "select": ("data/history/nba_history.duckdb", tuple(range(2013, 2022))),
    "report": ("nba.duckdb", (2022, 2023, 2024)),
}
HALF_WIDTH_MAX = 0.04
PER_SEASON_CHECKS = ("G1a", "G1b", "G2-A1", "G3")


def pooled_precision(df: pl.DataFrame, games: list[str]) -> dict[str, Any]:
    """G4 counts and the G5 projection over the pooled rows of one window (spread only)."""
    cs = F.cells(df.filter(pl.col("a_g") >= F.MIN_A))
    cells = {
        f"{g}_{c}": {
            "player_games": sub.height,
            "games": sub["game_id"].n_unique(),
            "sum_A_min": float(sub["a_g"].sum()),
        }
        for (g, c), sub in cs.items()
    }
    cs_all = F.cells(df)
    bs = F.boot_gap(cs_all[("young", "close")], cs_all[("young", "blow")], games, "q4", "q13")
    hw = float((np.quantile(bs, 0.975) - np.quantile(bs, 0.025)) / 2.0)
    yb = cs[("young", "blow")]
    infl = float(np.sqrt(12.0 * yb.height / float(yb["a_g"].sum()))) if yb.height else float("inf")
    return {
        "cells": cells,
        "young_gap_halfwidth_no_exclusion": hw,
        "inflation_sqrt_12_over_A": infl,
        "young_gap_halfwidth_projected": hw * infl,
    }


def run_gate(root: Path = Path(".")) -> dict[str, Any]:
    t0 = time.time()
    out: dict[str, Any] = {"rule": "F12B_POOLED (DRAFT)", "windows": {}, "checks": []}
    for name, (db, seasons) in WINDOWS.items():
        path = str(root / db)
        per = F.run_gate(path, seasons)  # per-season G1-G4 (and per-season G5, reported only)
        keep = [
            c
            for c in per["checks"]
            if any(c["check"].startswith(k + " ") for k in PER_SEASON_CHECKS)
        ]
        df, gm = F.load(path, seasons)
        games = sorted(gm["game_id"].to_list())
        pp = pooled_precision(df, games)
        cmin = min(v["player_games"] for v in pp["cells"].values())
        gmin = min(v["games"] for v in pp["cells"].values())
        a_yb = pp["cells"]["young_blow"]["sum_A_min"]
        checks = [*keep]
        checks.append(
            {
                "check": f"G4 pooled cells >= 500 player-games and >= 150 games ({name})",
                "value": [cmin, gmin],
                "ok": cmin >= 500 and gmin >= 150,
            }
        )
        checks.append(
            {
                "check": f"G4 pooled young x blowout non-garbage Q4 minutes ({name}) >= 1000",
                "value": a_yb,
                "ok": a_yb >= 1000,
            }
        )
        checks.append(
            {
                "check": f"G5 pooled projected young-gap half-width ({name}) <= {HALF_WIDTH_MAX}",
                "value": pp["young_gap_halfwidth_projected"],
                "ok": pp["young_gap_halfwidth_projected"] <= HALF_WIDTH_MAX,
            }
        )
        out["windows"][name] = {
            "db": db,
            "seasons": list(seasons),
            "games": len(games),
            "player_games": df.height,
            "max_season": int(df["season"].max()),  # type: ignore[arg-type]
            "pooled": pp,
            "per_season_info": {
                s: {k: v for k, v in r.items() if k != "young_gap_halfwidth_no_exclusion"}
                for s, r in per["seasons"].items()
            },
        }
        out["checks"] += checks
        print(f"[{time.time() - t0:6.1f}s] {name}: {len(games)} games", flush=True)
    out["all_ok"] = all(c["ok"] for c in out["checks"])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", choices=["gate"])
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    res = run_gate()
    text = json.dumps(F._round(res), indent=2, default=str)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(
        f"sha256 {hashlib.sha256(text.encode()).hexdigest()[:16]} -> {out}  all_ok={res['all_ok']}"
    )
    return 0 if res["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
