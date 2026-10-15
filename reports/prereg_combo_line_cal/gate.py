# ruff: noqa: E501
"""COMBO_LINE_CAL minimum-data gate (rule: docs/prereg/COMBO_LINE_CAL.md; DRAFT, nothing frozen).

Measures what historical price data we hold for combo (pra, pr, pa, ra) and stocks (stl, blk, bs) legs, per book and season,
and whether a LINE_CALIBRATION-style map could be fitted and confirmed on it. NO FIT AND NO SCORE: it never reads a box-score
stat value or an outcome; the only box-score field read is ``minutes > 0`` (the played flag that voids DNP legs, as the books do).
Season 2025 is never loaded (``season <= 2024`` in every SQL). Every connection is read only. Absolute paths into the MAIN
checkout (a worktree's own ``data/`` is empty or stale and is never used).

    /Users/devin/Downloads/nba-prediction/.venv/bin/python -W ignore reports/prereg_combo_line_cal/gate.py \
        --out reports/prereg_combo_line_cal/gate_draft.json

About 1 minute, peak memory under 1.5 GB (DuckDB limited to 1 GB, 2 threads); it takes no heavy lock because it is a read-only count.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

MAIN = Path("/Users/devin/Downloads/nba-prediction")
ODDS = MAIN / "data/odds/odds_history.duckdb"
DB = (
    MAIN / "data/backups/nba_20261014.duckdb"
)  # the copy COMBO_STOCKS_SHADOW used (2026-10-14 07:41)
PAIRS = MAIN / "data/ops/model_clv_pairs"
MAX_SEASON = 2024
SEASONS = (2023, 2024)

# Declared before any outcome was read (the rule's numbers; LINE_CALIBRATION's bars where one exists).
MIN_LEGS_SCORED = 5_000  # per (target, book) in the scored season (LC check 1, per stat)
MIN_GAMES_SCORED = 300
MIN_FIT_ROWS = 2_000  # per (book, target, month block): LC "identity below 2,000 fit rows"
HALF_LIFE_DAYS = 365.0
LEAD_MIN = 60.0  # minutes before the real tip
NEAR_HALF = (
    0.15  # nearest rung must be within 0.15 of an implied 0.5 (ALT_LADDER_HIST integrity test)
)
MONO_TOL = 0.02  # raw implied P(over) may not rise with the point by more than this
COMPONENT_TOL = (
    4.0  # points: |ladder main-like point - sum of the same book's component main points|
)
MIN_RUNGS = 3
LC_HW = 0.00106  # LINE_CALIBRATION gate check 5: projected P1 95% half-width at 37,528 legs (borrowed, not re-measured)
LC_N = 37_528
FLOOR = 0.003
LIVE_LEAD_OK = 0.5  # assumed share of live T-60 snapshots with lead >= 60 min (window [55, 65], uniform): UNMEASURED

TARGETS: dict[str, dict[str, Any]] = {
    "stl": {"kind": "main", "market": "player_steals", "parts": ()},
    "blk": {"kind": "main", "market": "player_blocks", "parts": ()},
    "bs": {"kind": "main", "market": "player_blocks_steals", "parts": ()},
    "pra": {
        "kind": "main",
        "market": "player_points_rebounds_assists",
        "parts": ("pts", "reb", "ast"),
    },
    "pr": {"kind": "main", "market": "player_points_rebounds", "parts": ("pts", "reb")},
    "pa": {"kind": "main", "market": "player_points_assists", "parts": ("pts", "ast")},
    "ra": {"kind": "main", "market": "player_rebounds_assists", "parts": ("reb", "ast")},
}
# Main-line leg integrity (price-only, declared before any outcome; revision 2, 2026-10-15 after the combo main-line ingest):
MAX_POINTS = 3  # a main key with more than 3 distinct points is a ladder under the main key (betrivers/unibet_us 2023 pra, betonlineag blk): excluded
OVERROUND = (1.0, 1.25)  # raw implied P(over) + P(under) at the chosen point must lie in this range
MAIN_MARKET = {"pts": "player_points", "reb": "player_rebounds", "ast": "player_assists"}
# main-line markets the vendor lists (betting-markets page, fetched 2026-10-15) that this store would need
WANTED_MAIN = [
    "player_blocks",
    "player_blocks_steals",
    "player_points_rebounds_assists",
    "player_points_rebounds",
    "player_points_assists",
    "player_rebounds_assists",
]


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def played_population() -> tuple[pl.DataFrame, dict[str, Any]]:
    """Played regular-season rows with n_prior and game date (minutes > 0 only; no stat value)."""
    con = duckdb.connect(str(DB), read_only=True)
    try:
        d = con.execute(
            "SELECT s.game_id, s.player_id, g.game_date, g.season, s.minutes "
            "FROM player_game_stats s JOIN games g USING (game_id) "
            f"WHERE g.season <= {MAX_SEASON} AND s.minutes > 0"
        ).pl()
    finally:
        con.close()
    d = d.sort(["player_id", "game_date", "game_id"]).with_columns(
        pl.int_range(pl.len())
        .over("player_id")
        .alias("n_prior")  # 0-based rank = prior played games
    )
    d = d.with_columns((pl.col("game_id").str.slice(0, 3) == "002").alias("regular"))
    pop = d.filter(pl.col("regular") & (pl.col("n_prior") >= 5))
    rep = {
        "rows_played_all_seasons_le_2024": d.height,
        "population_rows_by_season": {
            int(s): int(n) for s, n in pop.group_by("season").len().iter_rows()
        },
        "expected_from_COMBO_STOCKS_SHADOW": {"2023": 25815, "2024": 25767},
        "reproduces_rehearsal_population": bool(
            dict(pop.group_by("season").len().iter_rows()).get(2023) == 25815
            and dict(pop.group_by("season").len().iter_rows()).get(2024) == 25767
        ),
    }
    return d, rep


def tips() -> pl.DataFrame:
    fr = [
        pl.read_parquet(PAIRS / f"pairs_{s}.parquet", columns=["game_id", "tipoff_utc"]).unique(
            "game_id"
        )
        for s in SEASONS
    ]
    return pl.concat(fr).unique("game_id")


def main_legs(con: duckdb.DuckDBPyConnection, market: str) -> pl.DataFrame:
    """Two-sided main line per (season, game, player, book, snapshot): most balanced raw prices, ties to the lower point."""
    q = f"""
    WITH s AS (
      SELECT season, game_id, player_id, book, snapshot_kind, point,
             max(CASE WHEN side='over' THEN implied_prob_raw END) po,
             max(CASE WHEN side='under' THEN implied_prob_raw END) pu,
             max(snapshot_ts) snapshot_ts
      FROM odds_history
      WHERE market = '{market}' AND season <= {MAX_SEASON} AND player_id IS NOT NULL
      GROUP BY season, game_id, player_id, book, snapshot_kind, point),
    s2 AS (SELECT *, count(*) OVER (PARTITION BY season, game_id, player_id, book, snapshot_kind) n_pts FROM s),
    r AS (SELECT *, row_number() OVER (PARTITION BY season, game_id, player_id, book, snapshot_kind
                                       ORDER BY abs(po - pu), point) rn
          FROM s2 WHERE po IS NOT NULL AND pu IS NOT NULL)
    SELECT season, game_id, player_id, book, snapshot_kind, point, po AS p, snapshot_ts, n_pts AS n_rungs,
           po + pu AS book_sum, true AS bracket, false AS mono_viol, true AS two_sided
    FROM r WHERE rn = 1
    """
    return con.execute(q).pl()


def ladder_legs(con: duckdb.DuckDBPyConnection, market: str) -> tuple[pl.DataFrame, dict[str, Any]]:
    """One main-like leg per (season, game, player, book) ladder at t60: the over rung nearest an implied 0.5."""
    dup = con.execute(
        f"""SELECT count(*) FROM (SELECT 1 FROM odds_history WHERE market='{market}' AND side='over'
            AND season <= {MAX_SEASON} AND snapshot_kind='t60' GROUP BY season, game_id, player_id, book, point
            HAVING count(*) > 1)"""
    ).fetchone()
    nullpid = con.execute(
        f"SELECT count(*) FROM odds_history WHERE market='{market}' AND season <= {MAX_SEASON} AND player_id IS NULL"
    ).fetchone()
    q = f"""
    WITH o AS (
      SELECT season, game_id, player_id, book, point, avg(implied_prob_raw) p, max(snapshot_ts) snapshot_ts
      FROM odds_history
      WHERE market='{market}' AND side='over' AND season <= {MAX_SEASON} AND snapshot_kind='t60' AND player_id IS NOT NULL
      GROUP BY season, game_id, player_id, book, point),
    w AS (SELECT *, lag(p) OVER (PARTITION BY season, game_id, player_id, book ORDER BY point) lp FROM o),
    a AS (SELECT season, game_id, player_id, book, count(*) n_rungs, min(p) pmin, max(p) pmax,
                 sum(CASE WHEN p - lp > {MONO_TOL} THEN 1 ELSE 0 END) nviol
          FROM w GROUP BY season, game_id, player_id, book),
    n AS (SELECT o.*, row_number() OVER (PARTITION BY season, game_id, player_id, book ORDER BY abs(p - 0.5), point) rn
          FROM o),
    u AS (SELECT season, game_id, player_id, book, count(*) n_under FROM odds_history
          WHERE market='{market}' AND side='under' AND season <= {MAX_SEASON} AND snapshot_kind='t60' AND player_id IS NOT NULL
          GROUP BY season, game_id, player_id, book)
    SELECT n.season, n.game_id, n.player_id, n.book, 't60' AS snapshot_kind, n.point, n.p, n.snapshot_ts,
           a.n_rungs, CAST(NULL AS DOUBLE) AS book_sum, (a.pmin < 0.5 AND a.pmax >= 0.5) AS bracket, (a.nviol > 0) AS mono_viol,
           (coalesce(u.n_under, 0) > 0) AS two_sided
    FROM n JOIN a USING (season, game_id, player_id, book)
    LEFT JOIN u USING (season, game_id, player_id, book)
    WHERE n.rn = 1
    """
    return con.execute(q).pl(), {
        "duplicate_over_points": int(dup[0] if dup else 0),
        "null_player_id_rows": int(nullpid[0] if nullpid else 0),
    }


def component_main(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Per (game, player, book): main-line points of pts, reb, ast at t60 (most balanced two-sided line)."""
    frames = []
    for k, m in MAIN_MARKET.items():
        d = (
            main_legs(con, m)
            .filter(pl.col("snapshot_kind") == "t60")
            .select(["season", "game_id", "player_id", "book", pl.col("point").alias(f"c_{k}")])
        )
        frames.append(d)
    out = frames[0]
    for f in frames[1:]:
        out = out.join(f, on=["season", "game_id", "player_id", "book"], how="full", coalesce=True)
    return out


def mean_f(x: pl.Series) -> float:
    v = x.mean()
    return float(v) if isinstance(v, int | float) else float("nan")


def qtl(x: pl.Series, q: float) -> float:
    v = x.drop_nulls().quantile(q)
    return float(v) if isinstance(v, int | float) else float("nan")


def forecast_join(oof: Path, kept: dict[tuple[str, str], pl.DataFrame]) -> dict[str, Any]:
    """Join the kept legs to the persisted S grids; label-free: joins, A0 = P_S(Y >= ceil(line)), and the integer-grid z floor."""
    man = json.loads((oof / "manifest.json").read_text())
    keys = pl.read_parquet(oof / "keys.parquet").with_row_index("_row")
    z = np.load(oof / "grids.npz")
    out: dict[str, Any] = {
        "manifest_sha256": {k: v["sha256"] for k, v in man["files"].items()},
        "oof_rows": man["rows"],
        "population_reproduces": man["population_reproduces"],
        "cells": [],
    }
    for (tgt, book), g in sorted(kept.items()):
        if g.height < 500:
            continue
        j = g.join(
            keys.select("game_id", "player_id", "_row", "starter10"),
            on=["game_id", "player_id"],
            how="left",
        )
        ok = j.filter(pl.col("_row").is_not_null())
        grid = np.sort(z[tgt][ok["_row"].to_numpy()].astype(np.float64), axis=1)
        line = ok["point"].to_numpy()
        k = np.ceil(line)
        a0 = (grid >= k[:, None]).mean(axis=1)
        q25 = grid[:, 49] + 0.25 * (grid[:, 50] - grid[:, 49])
        q50 = grid[:, 99]
        q75 = grid[:, 148] + 0.75 * (grid[:, 149] - grid[:, 148])
        spread = (q75 - q25) / 1.349
        floor_bind = spread < 0.5
        sd = grid.std(axis=1)
        floor_bind_sd = sd < 0.5
        eps = 0.5 / 199
        out["cells"].append(
            {
                "target": tgt,
                "book": book,
                "legs": g.height,
                "joined": ok.height,
                "join_share": round(ok.height / g.height, 5),
                "whole_number_lines": int((line % 1.0 == 0).sum()),
                "a0_mean": round(float(a0.mean()), 4),
                "a0_share_at_clip": round(float(((a0 <= eps) | (a0 >= 1 - eps)).mean()), 4),
                "z_floor_binds_share_iqr": round(float(floor_bind.mean()), 4),
                "z_floor_binds_share_sd_fallback": round(float(floor_bind_sd.mean()), 4),
                "z_abs_clip5_share": round(
                    float((np.abs((line - q50) / np.maximum(spread, 0.5)) >= 5).mean()), 4
                ),
                "median_is_integer": bool(np.all(q50 == np.round(q50))),
                "bench_share": round(mean_f(ok["starter10"].fill_null(0.0) < 0.5), 4),
            }
        )
    return out


def structure_ok(spec: dict[str, Any]) -> pl.Expr:
    """Leg structure test. main: at most MAX_POINTS points under the key and a sane overround; ladder: the old nearest-even tests."""
    if spec["kind"] == "main":
        return (pl.col("n_rungs") <= MAX_POINTS) & pl.col("book_sum").is_between(*OVERROUND)
    return (
        (pl.col("n_rungs") >= MIN_RUNGS)
        & pl.col("bracket")
        & ((pl.col("p") - 0.5).abs() <= NEAR_HALF)
        & ~pl.col("mono_viol")
    )


def component_ok() -> pl.Expr:
    """Cross-check of a sum market against the same book's component main lines: a leg is dropped only when the check is
    possible and fails; a leg whose book posts no component line is kept (main markets) and counted separately."""
    return ~pl.col("comp_ok") | (pl.col("comp_diff") <= COMPONENT_TOL)


def hw_proxy(n: float) -> float:
    return LC_HW * float(np.sqrt(LC_N / max(n, 1.0)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-hash", action="store_true", help="skip the sha256 of the 600 MB inputs")
    ap.add_argument(
        "--oof",
        default=None,
        help="dir written by build_oof.py (keys.parquet, grids.npz, manifest.json): adds the forecast-join and z-floor items",
    )
    a = ap.parse_args()
    t0 = time.time()
    now = subprocess.run(["date"], capture_output=True, text=True).stdout.strip()
    print(f"date: {now}", flush=True)
    rep: dict[str, Any] = {
        "date_cmd_output": now,
        "seasons": list(SEASONS),
        "no_fit_no_score": True,
    }

    rep["data_version"] = {
        str(p): {
            "bytes": p.stat().st_size,
            "mtime": dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            "sha256": None if a.no_hash else sha256_file(p),
        }
        for p in (ODDS, DB, PAIRS / "pairs_2023.parquet", PAIRS / "pairs_2024.parquet")
    }
    played, prep = played_population()
    rep["population"] = prep
    print("population:", prep, flush=True)
    pop = played.filter(pl.col("regular") & (pl.col("n_prior") >= 5)).select(
        ["game_id", "player_id", "game_date", pl.col("season").alias("season_g")]
    )
    tp = tips()

    con = duckdb.connect(str(ODDS), read_only=True)
    con.execute("PRAGMA memory_limit='1GB'")
    con.execute("PRAGMA threads=2")

    # (0) what the store holds, by market (labels-free)
    inv = con.execute(
        f"""SELECT market, season, count(*) n_rows, count(DISTINCT game_id) n_games, count(DISTINCT book) n_books
            FROM odds_history WHERE season <= {MAX_SEASON}
              AND (market IN ({",".join("'" + m + "'" for m in WANTED_MAIN)}) OR market IN
                   ('player_steals','player_blocks_alternate','player_steals_alternate',
                    'player_points_rebounds_assists_alternate','player_points_rebounds_alternate',
                    'player_points_assists_alternate','player_rebounds_assists_alternate'))
            GROUP BY ALL ORDER BY 1, 2"""
    ).pl()
    held = {(m, int(s)): int(n) for m, s, n, *_ in inv.iter_rows()}
    rep["main_market_rows_held"] = {
        m: sum(held.get((m, s), 0) for s in SEASONS) for m in WANTED_MAIN
    }
    rep["held_markets"] = [dict(zip(inv.columns, r, strict=True)) for r in inv.iter_rows()]
    first_alt = con.execute(
        f"""SELECT season, min(CAST(snapshot_ts AS DATE)) FROM odds_history
            WHERE market='player_points_rebounds_assists_alternate' AND season <= {MAX_SEASON} GROUP BY 1 ORDER BY 1"""
    ).fetchall()
    rep["first_alt_snapshot_date_by_season"] = {int(s): str(d) for s, d in first_alt}
    print("absent main markets:", rep["main_market_rows_held"], flush=True)

    comp = component_main(con)
    print(f"component main legs {comp.height} ({time.time() - t0:.0f}s)", flush=True)

    rows: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}
    all_legs: dict[str, pl.DataFrame] = {}
    for tgt, spec in TARGETS.items():
        if spec["kind"] == "main":
            legs = main_legs(con, spec["market"])
            m = {"duplicate_over_points": None, "null_player_id_rows": None}
        else:
            legs, m = ladder_legs(con, spec["market"])
        meta[tgt] = m
        legs = legs.with_columns(pl.lit(tgt).alias("target"))
        # component cross-check (price-only integrity) for the sum targets
        if spec["parts"]:
            legs = legs.join(comp, on=["season", "game_id", "player_id", "book"], how="left")
            cols = [f"c_{k}" for k in spec["parts"]]
            legs = legs.with_columns(
                pl.sum_horizontal(cols).alias("comp_sum"),
                pl.all_horizontal([pl.col(c).is_not_null() for c in cols]).alias("comp_ok"),
            ).with_columns((pl.col("point") - pl.col("comp_sum")).abs().alias("comp_diff"))
        else:
            legs = legs.with_columns(
                pl.lit(None, dtype=pl.Float64).alias("comp_diff"), pl.lit(True).alias("comp_ok")
            )
        legs = legs.join(tp, on="game_id", how="left").with_columns(
            ((pl.col("tipoff_utc") - pl.col("snapshot_ts")).dt.total_seconds() / 60.0).alias(
                "lead_min"
            )
        )
        legs = legs.join(pop, on=["game_id", "player_id"], how="left").with_columns(
            pl.col("game_date").is_not_null().alias("in_pop")
        )
        all_legs[tgt] = legs
        print(f"{tgt}: {legs.height} candidate legs ({time.time() - t0:.0f}s)", flush=True)

    # (1) per (target, book, season): funnel
    for tgt, legs in all_legs.items():
        spec = TARGETS[tgt]
        l60 = legs.filter(pl.col("snapshot_kind") == "t60")
        for (book, season), g in l60.group_by(["book", "season"]):
            n_points_ok = g.filter(pl.col("n_rungs") <= MAX_POINTS).height
            clean = g.filter(structure_ok(spec))
            n_structure_only = clean.height  # before the component cross-check
            if spec["parts"]:
                clean = clean.filter(component_ok())
            clean_pop = clean.filter(pl.col("in_pop"))
            timed = clean_pop.filter(pl.col("lead_min") >= LEAD_MIN)
            rows.append(
                {
                    "target": tgt,
                    "kind": spec["kind"],
                    "book": book,
                    "season": int(season),
                    "candidate_legs": g.height,
                    "main_key_with_more_than_3_points_excluded": g.height - n_points_ok,
                    "after_structure_no_component_check": n_structure_only,
                    "after_structure_and_price_integrity": clean.height,
                    "tip_missing_legs": int(g["tipoff_utc"].is_null().sum()),
                    "played_regular_nprior5": clean_pop.height,
                    "t60_lead_ge_60min": timed.height,
                    "games": timed["game_id"].n_unique() if timed.height else 0,
                    "player_games": timed.select(["game_id", "player_id"]).unique().height,
                    "share_with_under_rung": mean_f(g["two_sided"]) if g.height else None,
                    "share_comp_check_possible": mean_f(g["comp_ok"]) if spec["parts"] else None,
                    "comp_diff_q50_q90_q99": (
                        [qtl(g["comp_diff"], q) for q in (0.5, 0.9, 0.99)]
                        if spec["parts"] and g["comp_diff"].drop_nulls().len()
                        else None
                    ),
                    "lead_min_q01_q50_q99": [
                        qtl(clean_pop["lead_min"], q) for q in (0.01, 0.5, 0.99)
                    ]
                    if clean_pop.height
                    else None,
                    "whole_number_point_share": mean_f(clean_pop["point"] % 1.0 == 0)
                    if clean_pop.height
                    else None,
                }
            )
    rows.sort(key=lambda r: (r["target"], r["book"], r["season"]))
    rep["funnel"] = rows
    rep["meta"] = meta

    # (2) fit-row gate per (target, book): walk-forward 2024 blocks, weights 0.5 ** (age/365)
    gates: list[dict[str, Any]] = []
    kept: dict[tuple[str, str], pl.DataFrame] = {}
    for tgt, legs in all_legs.items():
        spec = TARGETS[tgt]
        l60 = legs.filter(pl.col("snapshot_kind") == "t60")
        for book in sorted(l60["book"].unique().to_list()):
            g = l60.filter(pl.col("book") == book)
            g = g.filter(structure_ok(spec) & pl.col("in_pop") & (pl.col("lead_min") >= LEAD_MIN))
            if spec["parts"]:
                g = g.filter(component_ok())
            kept[(tgt, book)] = g
            n23 = g.filter(pl.col("season") == 2023).height
            s24 = g.filter(pl.col("season") == 2024)
            n24 = s24.height
            if n23 + n24 == 0:
                continue
            ds = g["game_date"].to_numpy().astype("datetime64[D]")
            blocks = sorted(
                {
                    d.astype("datetime64[M]")
                    for d in s24["game_date"].to_numpy().astype("datetime64[D]")
                }
            )
            fit_counts, neffs, failing = [], [], 0
            for b in blocks:
                bstart = b.astype("datetime64[D]")
                fit = ds < bstart
                nfit = int(fit.sum())
                age = (bstart - ds[fit]).astype("timedelta64[D]").astype(float)
                w = 0.5 ** (age / HALF_LIFE_DAYS)
                neffs.append(float(w.sum() ** 2 / max((w**2).sum(), 1e-12)) if nfit else 0.0)
                fit_counts.append(nfit)
                scored_in_block = int(
                    ((s24["game_date"].to_numpy().astype("datetime64[M]")) == b).sum()
                )
                if nfit < MIN_FIT_ROWS and scored_in_block > 0:
                    failing += 1
            allw = 0.5 ** (
                (np.datetime64("2026-10-20") - ds).astype("timedelta64[D]").astype(float)
                / HALF_LIFE_DAYS
            )
            gates.append(
                {
                    "target": tgt,
                    "kind": spec["kind"],
                    "book": book,
                    "legs_2023": n23,
                    "legs_2024": n24,
                    "games_2024": s24["game_id"].n_unique() if n24 else 0,
                    "blocks_2024": len(blocks),
                    "blocks_below_2000_fit": failing,
                    "min_fit_rows_2024_block": min(fit_counts) if fit_counts else 0,
                    "min_neff_2024_block": round(min(neffs), 1) if neffs else 0.0,
                    "fit_rows_at_opening_night": n23 + n24,
                    "neff_at_opening_night_halflife365": round(
                        float(allw.sum() ** 2 / max((allw**2).sum(), 1e-12)), 1
                    ),
                    "legs_per_game_2024": round(n24 / max(s24["game_id"].n_unique(), 1), 2)
                    if n24
                    else 0.0,
                    "projected_hw_scored_season": round(hw_proxy(n24), 5) if n24 else None,
                    "pass_legs": bool(n24 >= MIN_LEGS_SCORED),
                    "pass_games": bool(
                        (s24["game_id"].n_unique() if n24 else 0) >= MIN_GAMES_SCORED
                    ),
                    "pass_fit": bool(failing == 0 and n23 + n24 >= MIN_FIT_ROWS),
                }
            )
    for r in gates:
        r["pass_all"] = bool(r["pass_legs"] and r["pass_games"] and r["pass_fit"])
    rep["gate_by_target_book"] = gates
    if a.oof:
        rep["forecast_join"] = forecast_join(Path(a.oof), kept)

    # (3) the rule's primary cells: DraftKings (fixed now from counts and price integrity; the only both-season book
    # without the FanDuel duplicate-listing ambiguity, ALT_LADDER_HIST) and the best other book
    prim = []
    for tgt in ("stl", "blk", "pra", "pr", "pa", "ra", "bs"):
        rs = [r for r in gates if r["target"] == tgt]
        dk = next((r for r in rs if r["book"] == "draftkings"), {})
        best = max(rs, key=lambda r: r["legs_2024"], default=None)
        prim.append(
            {
                "target": tgt,
                "main_line_market_held": tgt == "stl",
                "draftkings": dk,
                "best_other_by_2024_legs": best if best and best["book"] != "draftkings" else None,
                "any_book_passes_all": any(r["pass_all"] for r in rs),
                "draftkings_passes_all": bool(dk and dk["pass_all"]),
            }
        )
    rep["primary_cells"] = prim

    # (4) live projection at the two looks; hw by the borrowed LC proxy (per-leg SD and design effect of LC's four stats)
    looks = {
        "interim_2027-01-10": 590,
        "final_2027-04-11": 1230,
    }  # games: COMBO_STOCKS_SHADOW 579-595 in 12 weeks; 1,230 season
    live = []
    for r in gates:
        if (
            r["book"] not in ("draftkings", "fanduel", "betmgm", "betrivers", "betonlineag")
            or not r["legs_per_game_2024"]
        ):
            continue
        for look, ng in looks.items():
            n = r["legs_per_game_2024"] * ng
            live.append(
                {
                    "target": r["target"],
                    "book": r["book"],
                    "look": look,
                    "games": ng,
                    "legs_per_game_2024_held_type": r["legs_per_game_2024"],
                    "expected_live_legs_if_same_density": int(n),
                    "projected_hw": round(hw_proxy(n), 5),
                    "mde_80pct_one_sided": round(1.27 * hw_proxy(n), 5),
                    "hw_le_half_floor_0.0015": bool(hw_proxy(n) <= 0.0015),
                    # live t60 captures are requested 55-65 min before the real tip (configs/odds_live.yaml window [55, 65]),
                    # so about half have lead < 60 min and are excluded unless the window moves (repair R5)
                    "projected_hw_if_half_lead_ok": round(hw_proxy(n * LIVE_LEAD_OK), 5),
                }
            )
    rep["live_projection"] = live
    rep["live_projection_note"] = (
        "borrowed from LINE_CALIBRATION gate check 5 (0.00106 at 37,528 legs, design effect 1.51 at 29.8 legs per game); "
        "legs per game are ladder-derived (or main for stl) 2024 densities, NOT live main-line densities; legs of one "
        "player-game across pra/pr/pa/ra share outcomes, so a pooled n over targets overstates information"
    )
    # pooled primary-cell projection: one book per target (DraftKings, else FanDuel, else BetMGM, else BetRivers if it passes)
    order = ["draftkings", "fanduel", "betmgm", "betrivers"]
    pooled = []
    for tgt in ("stl", "blk", "bs", "pra", "pr", "pa", "ra"):
        for bk in order:
            hit = [x for x in gates if x["target"] == tgt and x["book"] == bk and x["pass_all"]]
            if hit:
                pooled.append(hit[0])
                break
    for look, ng in looks.items():
        n = sum(r["legs_per_game_2024"] for r in pooled) * ng
        rep.setdefault("pooled_projection", {})[look] = {
            "cells": [(r["target"], r["book"]) for r in pooled],
            "nominal_legs": int(n),
            "hw_nominal": round(hw_proxy(n), 5),
            "hw_if_effective_n_is_half": round(hw_proxy(n / 2), 5),
            "hw_if_half_lead_ok": round(hw_proxy(n * LIVE_LEAD_OK), 5),
            "hw_if_half_lead_ok_and_effective_n_half": round(hw_proxy(n * LIVE_LEAD_OK / 2), 5),
        }
    rep["bars"] = {
        "min_legs_scored": MIN_LEGS_SCORED,
        "min_games_scored": MIN_GAMES_SCORED,
        "min_fit_rows": MIN_FIT_ROWS,
        "half_life_days": HALF_LIFE_DAYS,
        "lead_min": LEAD_MIN,
        "near_half": NEAR_HALF,
        "mono_tol": MONO_TOL,
        "component_tol_points": COMPONENT_TOL,
        "min_rungs": MIN_RUNGS,
        "lc_hw_at_37528": LC_HW,
        "floor": FLOOR,
    }
    rep["elapsed_s"] = round(time.time() - t0, 1)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(rep, indent=1, default=str))
    # compact table
    print(
        "\ntarget book kind  legs23 legs24 games24 blocksBelow2000 minFit24 neff_open  legs/game  hw   passAll"
    )
    for r in sorted(gates, key=lambda r: (r["target"], -r["legs_2024"])):
        if r["legs_2023"] + r["legs_2024"] < 500:
            continue
        print(
            f"{r['target']:4} {r['book']:14} {r['kind']:6} {r['legs_2023']:6} {r['legs_2024']:6} {r['games_2024']:5} "
            f"{r['blocks_below_2000_fit']:2} {r['min_fit_rows_2024_block']:6} {r['neff_at_opening_night_halflife365']:8} "
            f"{r['legs_per_game_2024']:6} {r['projected_hw_scored_season']} {r['pass_all']}"
        )
    print(f"wrote {a.out} in {rep['elapsed_s']} s")


if __name__ == "__main__":
    main()
