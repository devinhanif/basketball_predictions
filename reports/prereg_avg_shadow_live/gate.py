"""AVG_SHADOW_LIVE minimum-data gate (descriptive, read-only, NOT evidence).

Input: reports/prereg_seq_full_mix_rt/rehearsal/descriptive/rows.parquet, the saved per-row scores
of the run-#1 rehearsal whose target season is 2024 (2024-25; select side, allowed). It holds
no season-2025 row; the script asserts that. Never opens a database, never reads the data/colab
OOF files (they contain 2025).
Run: .venv/bin/python -I reports/prereg_avg_shadow_live/gate.py (10 s; writes gate.json beside it)
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "reports/prereg_seq_full_mix_rt/rehearsal/descriptive/rows.parquet"
OUT = Path(__file__).resolve().parent / "gate.json"
FLOOR = 0.005
TARGET_COUNT = (
    0.003  # one-sided half-width target for reb/ast/fg3m (direction question, sub-floor offline)
)
FINAL_MIN_WEEK = 12
INTERIM_MIN_WEEK = 4
Z1 = 1.645
SIGMA_F = 0.0010  # fit-noise term (rule); T485 seed sd of one 3-seed ensemble ~0.00074
OPEN = pd.Timestamp("2026-10-20")
GAMES_PER_WEEK = 49
STATS = ["pts", "reb", "ast", "fg3m"]


def boot(d: np.ndarray, g: np.ndarray, B: int = 2000, seed: int = 0) -> dict:
    ug, inv = np.unique(g, return_inverse=True)
    s = np.bincount(inv, weights=d)
    c = np.bincount(inv).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(ug), size=(B, len(ug)))
    est = s[idx].sum(1) / c[idx].sum(1)
    n = len(d)
    var_row = d.var(ddof=1)
    se = float(est.std())
    deff = se**2 * n / var_row
    return {
        "delta": float(d.mean()),
        "ci95": [float(np.quantile(est, 0.025)), float(np.quantile(est, 0.975))],
        "se": se,
        "sd_row": float(math.sqrt(var_row)),
        "deff_game": float(deff),
        "n": int(n),
        "games": int(len(ug)),
    }


def main() -> None:
    d = pd.read_parquet(SRC)
    assert d["date"].min() >= "2024-10-01" and d["date"].max() < "2025-07-01", (
        "season-2025 row present"
    )
    d = d[d["game_id"].str.startswith("002")].copy()  # regular season only (the live population)
    d["dt"] = pd.to_datetime(d["date"])
    out: dict = {
        "source": str(SRC.relative_to(ROOT)),
        "season": 2024,
        "sigma_F": SIGMA_F,
        "stats": {},
    }
    for s in STATS:
        x = d[d["stat"] == s]
        dd = (x["AVG_crps"] - x["FULL_crps"]).to_numpy()
        da = (x["AVG_crps"] - x["A0_crps"]).to_numpy()
        gid = x["game_id"].to_numpy()
        b = boot(dd, gid)
        a = boot(da, gid)
        # n for a one-sided 95% bound (bootstrap se + fit noise in quadrature) of half-width <= target
        target = FLOOR if s == "pts" else TARGET_COUNT
        room = (target / Z1) ** 2 - SIGMA_F**2
        n_min = (b["sd_row"] * math.sqrt(b["deff_game"]) / math.sqrt(room)) ** 2
        # supply: rows per game, and the first three weeks (a burn-in check)
        first = x.sort_values("dt")
        gdates = first.groupby("game_id")["dt"].first().sort_values()
        wk = (gdates - gdates.iloc[0]).dt.days // 7 + 1
        rows_pg = x.groupby("game_id").size()
        by_week = (rows_pg.groupby(wk.reindex(rows_pg.index)).mean()).round(2).head(4).to_dict()
        m5 = x["minutes"] >= 5
        b5 = boot(dd[m5.to_numpy()], gid[m5.to_numpy()]) if s == "pts" else None
        out["stats"][s] = {
            "avg_minus_full": b,
            "avg_minus_a0": a,
            "rows_per_game": float(rows_pg.mean()),
            "rows_per_game_first_weeks": {str(k): v for k, v in by_week.items()},
            "n_min_exact": float(n_min),
            "n_min_frozen": int(math.ceil(n_min / 100.0) * 100),
            "halfwidth_target": target,
            "halfwidth_at_n_min": float(
                Z1
                * math.sqrt(
                    (b["sd_row"] ** 2 * b["deff_game"] / (math.ceil(n_min / 100.0) * 100.0))
                    + SIGMA_F**2
                )
            ),
            "by_month_pts" if s == "pts" else "by_month": {
                str(m): round(float(v), 5)
                for m, v in pd.Series(dd, index=x["dt"].dt.to_period("M").to_numpy())
                .groupby(level=0)
                .mean()
                .items()
            },
            "minutes_ge5": b5,
        }
    # power of (a)+(b) at n_min for pts under a normal approximation
    p = out["stats"]["pts"]
    nmin = p["n_min_frozen"]
    se = (
        p["avg_minus_full"]["sd_row"]
        * math.sqrt(p["avg_minus_full"]["deff_game"])
        / math.sqrt(nmin)
    )
    tot = math.sqrt(se**2 + SIGMA_F**2)
    from math import erf, sqrt

    def cdf(x: float) -> float:
        return 0.5 * (1 + erf(x / sqrt(2)))

    pw = {}
    for eff in (-0.0155, -0.0133, -0.0100, -0.0070, -0.0050, 0.0):
        # PASS(a,b): delta_hat <= -FLOOR and delta_hat + Z1*tot < 0 ; delta_hat ~ N(eff, se)
        thr = min(-FLOOR, -Z1 * tot)
        pw[str(eff)] = round(cdf((thr - eff) / se), 3)
    out["power_pts_at_n_min"] = {
        "n_min": nmin,
        "se_boot": se,
        "pass_threshold": min(-FLOOR, -Z1 * tot),
        "p_pass_ab": pw,
    }
    # week reached
    rows_pg = float(np.mean([out["stats"][s]["rows_per_game"] for s in STATS]))
    weeks = {}
    for label, rpg, burn in (
        ("base", 0.85 * rows_pg, 0),
        ("optimistic", rows_pg, 0),
        ("pessimistic", 0.7 * rows_pg, 2),
    ):
        for s in STATS:
            nm = out["stats"][s]["n_min_frozen"]
            wk_n = math.ceil(nm / (rpg * GAMES_PER_WEEK)) + burn
            wk_final = max(wk_n, FINAL_MIN_WEEK)
            wk_half = max(math.ceil(nm / 2 / (rpg * GAMES_PER_WEEK)) + burn, INTERIM_MIN_WEEK)
            rows_at_final = int(rpg * GAMES_PER_WEEK * max(wk_final - burn, 0))
            vv = out["stats"][s]["avg_minus_full"]
            hw = Z1 * math.sqrt(
                vv["sd_row"] ** 2 * vv["deff_game"] / max(rows_at_final, 1) + SIGMA_F**2
            )
            sun = lambda k: str((OPEN + pd.Timedelta(days=5 + 7 * (k - 1))).date())  # noqa: E731
            weeks[f"{label}:{s}"] = {
                "rows_per_game": round(rpg, 2),
                "week_n_reached": wk_n,
                "final_week": wk_final,
                "final_sunday": sun(wk_final),
                "rows_at_final": rows_at_final,
                "halfwidth_at_final": round(hw, 5),
                "interim_week": wk_half,
                "interim_sunday": sun(wk_half),
            }
    out["weeks"] = weeks
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "stats"}, indent=1))
    for s in STATS:
        v = out["stats"][s]
        print(
            s,
            {
                k: v[k]
                for k in (
                    "rows_per_game",
                    "rows_per_game_first_weeks",
                    "n_min_frozen",
                    "halfwidth_at_n_min",
                )
            },
            "AVG-FULL",
            round(v["avg_minus_full"]["delta"], 5),
            [round(c, 5) for c in v["avg_minus_full"]["ci95"]],
            "sd",
            round(v["avg_minus_full"]["sd_row"], 4),
            "deff",
            round(v["avg_minus_full"]["deff_game"], 3),
            "n",
            v["avg_minus_full"]["n"],
            "AVG-A0",
            round(v["avg_minus_a0"]["delta"], 5),
        )


if __name__ == "__main__":
    main()
