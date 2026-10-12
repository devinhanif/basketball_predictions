"""Over/under hit rates of the model's T-60 pick vs stored lines, 2023-24 and 2024-25 (DESCRIPTIVE).

    .venv/bin/python -m research.eval.market_ou_descriptive \
        --pairs data/ops/model_clv_pairs --out reports/market_descriptive_2026-10-12.json

No rule, no claim, no verdict (Devin, 2026-10-12, "lets do everything"). Reuses the MODEL_CLV
paired frames (``research.eval.model_clv_pairs``: production props model P(over) joined to
de-vigged T-60 quotes by ``odds_history_eval.pairs_for_season``) and MODEL_CLV's loader and
game-clustered bootstrap.
Seasons 2023 and 2024 only; ``load_season`` refuses 2025 (the frozen holdout).

Definitions (fixed before any number was read):
- model pick = over if P_model(over) > 0.5 else under; market pick = over if de-vigged
  P(over) > 0.5.
- hit = the picked side won; pushes cannot occur (every Pinnacle/DK line is x.5, checked); DNP
  legs are voided upstream and excluded (counts in reports/odds_history.md).
- break-even = the book's own vigged implied probability of the picked side at T-60 (1 / decimal
  price); ret = realised return per $1 at that price. Descriptive only, no stake implied.
- confidence bucket on |P - 0.5|: [0, .05), [.05, .10), [.10, .20), [.20, .5].
- starter = starter10 >= 0.5 (model's pre-game starter probability).
- CIs: 95% percentile bootstrap, games resampled (2,000 draws, seed 0), as in MODEL_CLV.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

import research.eval.model_clv_eval as mc

SEASONS = (2023, 2024)
BOOKS = ("pinnacle", "draftkings")
STATS = ("pts", "reb", "ast", "fg3m")
BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("0-0.05", 0.0, 0.05),
    ("0.05-0.10", 0.05, 0.10),
    ("0.10-0.20", 0.10, 0.20),
    (">0.20", 0.20, 0.51),
)
KEYS = ("book", "stat", "pm", "mult", "over", "po", "pu", "line", "starter10")


def pooled(pairs_dir: Path, seasons: tuple[int, ...]) -> dict[str, Any]:
    """T-60 legs of the requested seasons, games re-indexed so one bootstrap covers all of them."""
    parts = [mc.load_season(pairs_dir, s) for s in seasons]
    out: dict[str, Any] = {k: np.concatenate([p[k] for p in parts]) for k in KEYS}
    off, gis = 0, []
    for p in parts:
        gis.append(p["gi"] + off)
        off += p["G"]
    out["gi"] = np.concatenate(gis)
    out["G"] = off
    out["n"] = len(out["pm"])
    return out


def _r(m: dict[str, Any]) -> dict[str, Any]:
    return {k: m.get(k) for k in ("mean", "lo", "hi", "n")}


def side_block(bs: mc.Boot, L: dict[str, Any], p: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
    """Hit rate, mean break-even and realised return per $1 for over picks and under picks of p."""
    pick_over = p > 0.5
    win = np.where(pick_over, L["over"] == 1.0, L["over"] == 0.0).astype(np.float64)
    be = 1.0 / mc.dec(np.where(pick_over, L["po"], L["pu"]))
    ret = np.where(win == 1.0, mc.dec(np.where(pick_over, L["po"], L["pu"])) - 1.0, -1.0)
    res: dict[str, Any] = {}
    for name, sm in (("over", pick_over), ("under", ~pick_over), ("all", np.ones_like(mask))):
        m = mask & sm
        res[name] = {
            "hit": _r(bs.mean(win, m)),
            "break_even": float(be[m].mean()) if m.any() else None,
            "ret_per_unit": _r(bs.mean(ret, m)),
        }
    res["under_minus_over_hit"] = _r(bs.diff(win, mask & ~pick_over, win, mask & pick_over))
    return res


def calib_block(bs: mc.Boot, L: dict[str, Any], p: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
    """Mean forecast P(over), realised over rate, and forecast - realised, clustered CI."""
    return {
        "mean_p_over": float(p[mask].mean()) if mask.any() else None,
        "over_rate": _r(bs.mean(L["over"], mask)),
        "p_minus_realised": _r(bs.mean(p - L["over"], mask)),
    }


def book_report(L: dict[str, Any], book: str) -> dict[str, Any]:
    bs = mc.Boot(L["gi"], L["G"])
    out: dict[str, Any] = {}
    starter = L["starter10"] >= 0.5
    for stat in (*STATS, "all"):
        base = L["book"] == book
        if stat != "all":
            base = base & (L["stat"] == stat)
        conf = np.abs(L["pm"] - 0.5)
        rec: dict[str, Any] = {
            "n_rows": int(base.sum()),
            "n_games": int(len(np.unique(L["gi"][base]))),
            "model": side_block(bs, L, L["pm"], base),
            "market": side_block(bs, L, L["mult"], base),
            "model_buckets": {
                b: side_block(bs, L, L["pm"], base & (conf >= lo) & (conf < hi))
                for b, lo, hi in BUCKETS
            },
            "calibration": {
                role: {
                    "model": calib_block(bs, L, L["pm"], base & rm),
                    "market": calib_block(bs, L, L["mult"], base & rm),
                }
                for role, rm in (
                    ("all", np.ones_like(base)),
                    ("starter", starter),
                    ("bench", ~starter),
                )
            },
        }
        out[stat] = rec
    return out


def run(pairs_dir: Path) -> dict[str, Any]:
    res: dict[str, Any] = {
        "meta": {"seasons": list(SEASONS), "books": list(BOOKS), "timing": "t60"}
    }
    for label, seasons in (("pooled", SEASONS), *((str(s), (s,)) for s in SEASONS)):
        L = pooled(pairs_dir, seasons)
        assert np.all(L["line"] % 1 == 0.5), "a whole-number line would need push handling"
        res[label] = {b: book_report(L, b) for b in BOOKS}
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=Path, default=Path("data/ops/model_clv_pairs"))
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    res = run(a.pairs)
    a.out.write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
