"""Fit the win-prob model on 2022-24 play-by-play and report calibration and the comeback rate.

Out-of-sample by construction: each season is predicted by a model fit on the other two seasons
(leave-one-season-out). The shipped coefficients are the fit on all three.
Season 2025 is never read.
Run: python -m nba.ride backtest
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd

from nba.ride import winprob as wp

PBP_DIR = Path("data/pbp")
THRESHOLD = 0.98
SEASON_NAMES = {"00222": "2022-23", "00223": "2023-24", "00224": "2024-25"}
BINS = (0.95, 0.97, 0.98, 0.99, 1.0001)


def load_game(path: Path) -> pd.DataFrame | None:
    d = pd.read_parquet(
        path, columns=["game_id", "action_number", "period", "clock", "score_home", "score_away"]
    )
    d = d.sort_values("action_number")
    for c in ("score_home", "score_away"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d[["score_home", "score_away"]] = d[["score_home", "score_away"]].ffill().fillna(0)
    if len(d) < 50 or d["score_home"].iloc[-1] == d["score_away"].iloc[-1]:
        return None  # not a finished game
    final_home = float(d["score_home"].iloc[-1] > d["score_away"].iloc[-1])
    secs = d["clock"].map(wp.parse_clock).to_numpy()
    per = d["period"].to_numpy()
    sl = np.where(per <= 4, (4 - per) * 720.0 + secs, secs)
    out = pd.DataFrame(
        {
            "game_id": d["game_id"].to_numpy(),
            "margin": (d["score_home"] - d["score_away"]).to_numpy(),
            "secs_left": sl,
            "y": final_home,
        }
    )
    return out[out["secs_left"] > 0].reset_index(drop=True)


def load_frame(pbp_dir: Path = PBP_DIR) -> pd.DataFrame:
    parts = []
    for prefix in wp.ALLOWED_SEASON_PREFIXES:
        for p in sorted(pbp_dir.glob(f"{prefix}*.parquet")):
            g = load_game(p)
            if g is not None:
                g["season"] = prefix
                parts.append(g)
    return pd.concat(parts, ignore_index=True)


def cluster_ci(
    per_game: np.ndarray, stat: Callable[[np.ndarray], float], n_boot: int = 2000, seed: int = 7
) -> tuple[float, float]:
    """Game-clustered bootstrap percentile CI. ``per_game`` rows are per-game sums; ``stat`` maps
    the column sums to a number."""
    rng = np.random.default_rng(seed)
    n = len(per_game)
    vals = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        vals.append(stat(per_game[idx].sum(axis=0)))
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def oos_predictions(df: pd.DataFrame) -> np.ndarray:
    p = np.zeros(len(df))
    for s in sorted(df["season"].unique()):
        tr, te = (df["season"] != s).to_numpy(), (df["season"] == s).to_numpy()
        m = wp.fit_logistic(
            wp.features(df.loc[tr, "margin"], df.loc[tr, "secs_left"]), df.loc[tr, "y"].to_numpy()
        )
        p[te] = m.prob_home(df.loc[te, "margin"].to_numpy(), df.loc[te, "secs_left"].to_numpy())
    return p


def calibration_tail(df: pd.DataFrame, p_home: np.ndarray) -> list[dict[str, object]]:
    lead = np.maximum(p_home, 1 - p_home)
    leader_won = np.where(p_home >= 0.5, df["y"].to_numpy(), 1 - df["y"].to_numpy())
    games = df["game_id"].to_numpy()
    rows = []
    for lo, hi in zip(BINS[:-1], BINS[1:], strict=True):
        sel = (lead >= lo) & (lead < hi)
        if not sel.any():
            rows.append({"bin": f"[{lo:.2f},{min(hi, 1.0):.2f})", "n_rows": 0})
            continue
        g = (
            pd.DataFrame({"g": games[sel], "n": 1.0, "w": leader_won[sel], "p": lead[sel]})
            .groupby("g")
            .sum()
        )
        arr = g[["n", "w", "p"]].to_numpy()
        tot = arr.sum(axis=0)
        lo_ci, hi_ci = cluster_ci(arr, lambda s: s[1] / s[0])
        rows.append(
            {
                "bin": f"[{lo:.2f},{min(hi, 1.0):.2f})",
                "n_rows": int(tot[0]),
                "n_games": len(g),
                "mean_pred": round(float(tot[2] / tot[0]), 4),
                "leader_won": round(float(tot[1] / tot[0]), 4),
                "ci95_game_clustered": [round(lo_ci, 4), round(hi_ci, 4)],
            }
        )
    return rows


def comeback(
    df: pd.DataFrame, p_home: np.ndarray, threshold: float = THRESHOLD
) -> dict[str, object]:
    """Of games where a leader FIRST reaches >= threshold by the model, how often did they lose."""
    d = df.assign(lead=np.maximum(p_home, 1 - p_home), ph=p_home)
    hit = d[d["lead"] >= threshold]
    first = hit.groupby("game_id", sort=False).head(1)
    leader_home = first["ph"].to_numpy() >= 0.5
    lost = np.where(leader_home, first["y"].to_numpy() == 0.0, first["y"].to_numpy() == 1.0)
    n, k = len(first), int(lost.sum())
    arr = np.column_stack([np.ones(n), lost.astype(float)])
    lo, hi = cluster_ci(arr, lambda s: s[1] / s[0])  # one row per game: the game is the cluster
    wl, wh = wp.wilson(k, n)
    by_secs = {}
    s = first["secs_left"].to_numpy()
    for name, sel in (
        ("secs_left>1440", s > 1440),
        ("720-1440", (s > 720) & (s <= 1440)),
        ("<=720", s <= 720),
    ):
        by_secs[name] = {"n": int(sel.sum()), "lost": int(lost[sel].sum())}
    return {
        "threshold": threshold,
        "games_reaching": n,
        "games_total": int(df["game_id"].nunique()),
        "leader_lost": k,
        "rate": round(k / n, 5) if n else None,
        "ci95_bootstrap": [round(lo, 5), round(hi, 5)],
        "ci95_wilson": [round(wl, 5), round(wh, 5)],
        "by_time_left_at_first_reach": by_secs,
        "reverses_plan_if_over": 0.03,
        "over_3pct": bool(n and k / n > 0.03),
    }


def run(
    pbp_dir: Path = PBP_DIR, out: Path = Path("reports/ride/backtest.json")
) -> dict[str, object]:
    df = load_frame(pbp_dir)
    p = oos_predictions(df)
    full = wp.fit_logistic(wp.features(df["margin"], df["secs_left"]), df["y"].to_numpy())
    result: dict[str, object] = {
        "seasons": ["2022-23", "2023-24", "2024-25"],
        "n_games": int(df["game_id"].nunique()),
        "n_rows": len(df),
        "oos_logloss": round(wp.logloss(p, df["y"].to_numpy()), 5),
        "coefs": list(full.coefs),
        "calibration_ge_0.95_oos": calibration_tail(df, p),
        "comeback_98": comeback(df, p),
        "comeback_98_by_season": {
            SEASON_NAMES[s]: comeback(df[df["season"] == s], p[(df["season"] == s).to_numpy()])
            for s in sorted(df["season"].unique())
        },
        "comeback_95": comeback(df, p, 0.95),
        "comeback_99": comeback(df, p, 0.99),
        "possession": "not modelled (disclosed)",
    }
    full.save(
        meta={
            "seasons": result["seasons"],
            "n_games": result["n_games"],
            "oos_logloss": result["oos_logloss"],
        }
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1))
    return result
