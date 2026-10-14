"""ALT_PARLAY_X minimum-data gate and the FIXED parlay construction (rule:
docs/prereg/ALT_PARLAY_X.md; DRAFT until frozen).

    .venv/bin/python -W ignore -m research.eval.alt_parlay_x_gate \\
        --data-root <checkout holding data/ and reports/>

Counts only. No outcome column (``y``) is read, no hit rate, return or EV is computed; the gate
sizes the parlay sets and their intervals before the rule is frozen (CLAUDE.md non-negotiable 8).
The construction functions below ARE the rule's construction: the scoring harness (written after
the freeze) imports them unchanged.

Legs come from ALT_LINE_70's arm A (BetRivers, capture ``t5`` at or after the model's T-60 cutoff,
two-sided half-point over rungs joined to the stored model P(over)); see
``research/eval/alt_line_70_gate.py``.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from research.eval.alt_line_70_gate import (
    EPS,
    PLAN_BETS_PER_WEEK,
    PRIMARY_BOOK,
    PRIMARY_KIND,
    SEASONS,
    SECONDARY_BINS,
    THRESHOLD,
    priced_rungs,
)

ROOT_SEED = 2026
PER_DATE = 20  # parlays drawn per (season, pool, size, date); fewer when the date has fewer
SIZES = (2, 3)
#: leg pools: the primary set (P >= 0.70, no upper bound) and the four disjoint secondary bins
POOLS: tuple[tuple[str, float, float], ...] = (
    ("ge70", THRESHOLD, 1.0 + 1e-9),
    *SECONDARY_BINS,
)
KEY = ["game_id", "player_id", "stat"]


def pool_legs(rungs: pl.DataFrame, lo: float, hi: float, seed: Sequence[int]) -> pl.DataFrame:
    """One leg per unit (game, player, stat): among the unit's BetRivers rungs with model P in
    [lo, hi), keep ONE chosen by a seeded uniform draw (rungs sorted by key first, so the choice
    is reproducible). No hand selection."""
    e = rungs.filter(
        (pl.col("book") == PRIMARY_BOOK) & (pl.col("pm") >= lo - EPS) & (pl.col("pm") < hi - EPS)
    ).sort([*KEY, "point"])
    if e.is_empty():
        return e
    u = np.random.default_rng(seed).random(e.height)
    e = e.with_columns(pl.Series("u", u))
    return e.sort([*KEY, "u"]).unique(KEY, keep="first", maintain_order=True).sort([*KEY, "point"])


def draw_parlays(legs: pl.DataFrame, m: int, per_date: int, seed: Sequence[int]) -> pl.DataFrame:
    """Cross-game parlays of ``m`` legs from the SAME slate date (``game_date``). For each date, up
    to ``per_date`` draws: pick ``m`` distinct games uniformly without replacement, then one leg
    uniformly within each picked game. Identical leg sets are dropped (no refill). Dates with fewer
    than ``m`` games with legs give none. Returns one row per parlay with the leg row indices
    ``i0..i{m-1}`` into ``legs`` (after the sort below), the date, P (product of leg P) and D
    (product of leg decimal odds)."""
    legs = legs.sort([*KEY, "point"])
    rng = np.random.default_rng(seed)
    pm = legs["pm"].to_numpy()
    dec = legs["dec"].to_numpy()
    gid = legs["game_id"].to_numpy()
    dates = legs["game_date"].to_numpy().astype("datetime64[D]")
    rows: list[tuple[Any, ...]] = []
    for d in np.unique(dates):
        idx = np.flatnonzero(dates == d)
        games, inv = np.unique(gid[idx], return_inverse=True)
        if len(games) < m:
            continue
        by_game = [idx[inv == g] for g in range(len(games))]
        seen: set[tuple[int, ...]] = set()
        for _ in range(per_date):
            gsel = rng.choice(len(games), size=m, replace=False)
            pick = tuple(sorted(int(by_game[g][rng.integers(len(by_game[g]))]) for g in gsel))
            if pick in seen:
                continue
            seen.add(pick)
            rows.append(
                (d.item(), *pick, float(np.prod(pm[list(pick)])), float(np.prod(dec[list(pick)])))
            )
    cols = ["game_date", *[f"i{k}" for k in range(m)], "P", "D"]
    out = pl.DataFrame(rows, schema=cols, orient="row") if rows else pl.DataFrame(schema=cols)
    return out


def summarize_parlays(par: pl.DataFrame) -> dict[str, Any]:
    """Counts and a model-only half-width, clustered by date (parlays on one date share legs and
    the slate, so the date is the cluster). ROI bet sd = D sqrt(P(1-P)) from the model's own P."""
    if par.is_empty():
        return {"parlays": 0}
    sd = par["D"].to_numpy() * np.sqrt(par["P"].to_numpy() * (1.0 - par["P"].to_numpy()))
    by_date = (
        par.with_columns(pl.Series("sd", sd))
        .group_by("game_date")
        .agg(pl.col("sd").sum().alias("s"))
    )
    wk = par.with_columns(pl.col("game_date").dt.truncate("1w").alias("wk")).group_by("wk").len()
    n = par.height
    return {
        "parlays": n,
        "dates": int(par["game_date"].n_unique()),
        "weeks": int(wk.height),
        "weeks_with_ge_10_parlays": int((wk["len"] >= PLAN_BETS_PER_WEEK).sum()),
        "mean_joint_p": round(float(par["P"].mean()), 4),  # type: ignore[arg-type]
        "decimal_p10_med_p90": [
            round(float(par["D"].quantile(q)), 3)  # type: ignore[arg-type]
            for q in (0.1, 0.5, 0.9)
        ],
        # what the MODEL claims (P x D - 1), not a realised return
        "model_claimed_ev_mean": round(float((par["P"] * par["D"] - 1.0).mean()), 4),  # type: ignore[arg-type]
        "roi_halfwidth_independent": round(float(1.96 * np.sqrt((sd**2).sum()) / n), 4),
        "roi_halfwidth_cons_date": round(
            float(1.96 * np.sqrt((by_date["s"].to_numpy() ** 2).sum()) / n), 4
        ),
    }


def pool_parlays(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Pool per-season summaries (cluster sums add across seasons)."""
    parts = [x for x in parts if x.get("parlays", 0) > 0]
    n = sum(int(x["parlays"]) for x in parts)
    if n == 0:
        return {"parlays": 0}
    c = sum((x["roi_halfwidth_cons_date"] * x["parlays"] / 1.96) ** 2 for x in parts)
    i = sum((x["roi_halfwidth_independent"] * x["parlays"] / 1.96) ** 2 for x in parts)
    return {
        "parlays": n,
        "dates": sum(int(x["dates"]) for x in parts),
        "roi_halfwidth_cons_date": round(float(1.96 * np.sqrt(c) / n), 4),
        "roi_halfwidth_independent": round(float(1.96 * np.sqrt(i) / n), 4),
    }


def run_gate(data_root: Path) -> dict[str, Any]:
    tips = pl.concat(
        [
            pl.read_parquet(
                data_root / f"data/ops/model_clv_pairs/pairs_{s}.parquet",
                columns=["game_id", "tipoff_utc"],
            )
            for s in SEASONS
        ]
    ).unique("game_id")
    out: dict[str, Any] = {
        "rule": "docs/prereg/ALT_PARLAY_X.md (DRAFT)",
        "root_seed": ROOT_SEED,
        "per_date": PER_DATE,
        "capture": PRIMARY_KIND,
        "seasons": {},
    }
    for season in SEASONS:
        m_rows = pl.read_parquet(
            data_root / f"reports/odds_history/model_rows_{season}.parquet",
            columns=["game_id", "player_id", "stat", "game_date", "p_ge"],
        )
        assert "y" not in m_rows.columns
        t, meta = priced_rungs(
            data_root / "data/odds/odds_history.duckdb", season, tips, m_rows, PRIMARY_KIND
        )
        assert meta["timing_rule_violations"] == 0
        res: dict[str, Any] = {}
        for pi, (label, lo, hi) in enumerate(POOLS):
            legs = pool_legs(t, lo, hi, [ROOT_SEED, season, pi])
            cell: dict[str, Any] = {
                "legs": legs.height,
                "games": int(legs["game_id"].n_unique()) if legs.height else 0,
                "dates": int(legs["game_date"].n_unique()) if legs.height else 0,
            }
            if legs.height:
                per = legs.group_by("game_date").agg(
                    pl.col("game_id").n_unique().alias("g"), pl.len().alias("n")
                )
                cell["dates_with_ge2_games"] = int((per["g"] >= 2).sum())
                cell["dates_with_ge3_games"] = int((per["g"] >= 3).sum())
                cell["legs_per_date_median"] = float(per["n"].median())  # type: ignore[arg-type]
            for m in SIZES:
                seed = [ROOT_SEED, season, pi, m]
                par = draw_parlays(legs, m, PER_DATE, seed) if legs.height else pl.DataFrame()
                cell[f"size{m}"] = summarize_parlays(par)
            res[label] = cell
        out["seasons"][str(season)] = res
    pooled: dict[str, Any] = {}
    for label, _, _ in POOLS:
        for m in SIZES:
            pooled[f"{label}_size{m}"] = pool_parlays(
                [out["seasons"][str(y)][label][f"size{m}"] for y in SEASONS]
            )
    out["pooled"] = pooled
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, default=Path("."))
    ap.add_argument("--out", type=Path, default=Path("reports/prereg_alt_parlay_x/gate.json"))
    args = ap.parse_args(argv)
    res = run_gate(args.data_root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
