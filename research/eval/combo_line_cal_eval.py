# ruff: noqa: E501
"""COMBO_LINE_CAL harness (rule: docs/prereg/COMBO_LINE_CAL.md; DRAFT until frozen).

    M="-m research.eval.combo_line_cal_eval"
    .venv/bin/python -W ignore $M preflight                  # label-free: pins, counts, A0 means, z floor, lead, population
    .venv/bin/python -W ignore $M rehearse                   # whole Stage 1 path on SYNTHETIC labels (three truths) + planted future
    .venv/bin/python -W ignore $M select --sha <64 hex>      # Stage 1 (2024, real labels), after the freeze AND the harness pin

What it does: the LINE_CALIBRATION map (terms [1, x, z, bench, bench*x], IRLS ridge 1.0, weights 0.5 ** (age_days / 365), one fit per
(book, target, calendar month) on every earlier t60 main-line leg of that book and target) applied to the COMBO_STOCKS_SHADOW arm S at
the book's posted main line. x = logit of P_S(Y >= ceil(line)) from the persisted 199-point integer grid; z = (line - q50) / max((q75 -
q25) / 1.349, 0.5) on that grid; bench = starter10 < 0.5. No price enters a fit. Stage 1 fits 2023 plus earlier 2024 months and
scores 2024; it cannot pass the rule (the live 2026-27 log is the verdict venue) and only decides whether the live test runs.

Label discipline: the ONLY function that reads a box-score stat is ``load_labels``; it is called by ``select`` after
``rule.require_ready`` and nowhere else. ``preflight`` and ``rehearse`` never call it (a test asserts this). Season 2025 is never
loaded: every SQL carries ``season <= 2024`` and ``assert_no_2025`` runs on every frame. Light job (numpy), no heavy lock.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from research.eval import combo_line_cal_rule as rule

MAIN = Path("/Users/devin/Downloads/nba-prediction")
ODDS = MAIN / "data/odds/odds_history.duckdb"
DB = MAIN / "data/backups/nba_20261014.duckdb"
OOF = MAIN / "data/ops/combo_oof_2023_24"
PAIRS = MAIN / "data/ops/model_clv_pairs"
OUT_DIR = Path("reports/prereg_combo_line_cal")
MAX_SEASON = 2024
SEASONS = (2023, 2024)
SELECT_SEASON = 2024
SEED = 0
N_BOOT = 2000
K_NULL = 50

TARGETS: dict[str, dict[str, Any]] = {
    "stl": {"market": "player_steals", "parts": ()},
    "blk": {"market": "player_blocks", "parts": ()},
    "bs": {"market": "player_blocks_steals", "parts": ()},
    "pra": {"market": "player_points_rebounds_assists", "parts": ("pts", "reb", "ast")},
    "pr": {"market": "player_points_rebounds", "parts": ("pts", "reb")},
    "pa": {"market": "player_points_assists", "parts": ("pts", "ast")},
    "ra": {"market": "player_rebounds_assists", "parts": ("reb", "ast")},
}
COMPONENT_MARKET = {"pts": "player_points", "reb": "player_rebounds", "ast": "player_assists"}
TARGET_LIST = tuple(TARGETS)
BOOK_ORDER = ("draftkings", "fanduel", "betmgm", "betrivers")
# bars declared in the rule (LINE_CALIBRATION's where one exists)
MIN_LEGS_SCORED, MIN_GAMES_SCORED, MIN_FIT = 5_000, 300, 2_000
MAX_POINTS, OVERROUND, COMPONENT_TOL, LEAD_MIN = 3, (1.0, 1.25), 4.0, 60.0
HALF_LIFE_DAYS, RIDGE, Z_CLIP, SPREAD_MIN, IQR_TO_SD = 365.0, 1.0, 5.0, 0.5, 1.349
EPS, MARKET_EPS = 0.5 / 199, 1e-9
FLOOR, CAL_ABS_MAX, NULL_SD_MULT = -0.003, 0.015, 2.0
Z_FLOOR_FALLBACK_SHARE = 0.5
SYN_A, SYN_B = (
    0.17,
    0.85,
)  # synthetic truth: logit p = SYN_A + SYN_B logit p_model (LINE_CALIBRATION's T477-like bias)
PINNED_VERSIONS = {"numpy": "2.5.3", "polars": "1.44.2", "duckdb": "1.5.6"}

# Counts and means measured by gate.py on the data_version below (reports/prereg_combo_line_cal/gate_main.json); the preflight
# refuses unless this harness reproduces them. Filled after the first label-free run.
PINNED: dict[str, Any] = {
    "primary": {
        "stl": "betmgm",
        "blk": "betmgm",
        "bs": "betmgm",
        "pra": "draftkings",
        "pr": "draftkings",
        "pa": "draftkings",
        "ra": "draftkings",
    },
    "consistency": {
        "stl": "betrivers",
        "blk": None,
        "bs": None,
        "pra": "fanduel",
        "pr": "fanduel",
        "pa": "fanduel",
        "ra": "fanduel",
    },
    "legs": {
        "stl/betmgm": {"2023": 11001, "2024": 8543},
        "stl/betrivers": {"2023": 5165, "2024": 7316},
        "blk/betmgm": {"2023": 10851, "2024": 14100},
        "bs/betmgm": {"2023": 8510, "2024": 8639},
        "pra/draftkings": {"2023": 10698, "2024": 10308},
        "pra/fanduel": {"2023": 7645, "2024": 6917},
        "pr/draftkings": {"2023": 12927, "2024": 12588},
        "pr/fanduel": {"2023": 11623, "2024": 11959},
        "pa/draftkings": {"2023": 11746, "2024": 9248},
        "pa/fanduel": {"2023": 7831, "2024": 7271},
        "ra/draftkings": {"2023": 8903, "2024": 8384},
        "ra/fanduel": {"2023": 7638, "2024": 6726},
    },
    "a0_mean": {
        "stl/betmgm": 0.4668,
        "stl/betrivers": 0.4797,
        "blk/betmgm": 0.3508,
        "bs/betmgm": 0.4522,
        "pra/draftkings": 0.4696,
        "pra/fanduel": 0.4681,
        "pr/draftkings": 0.4741,
        "pr/fanduel": 0.474,
        "pa/draftkings": 0.4819,
        "pa/fanduel": 0.4795,
        "ra/draftkings": 0.4485,
        "ra/fanduel": 0.4568,
    },
}


def _log(msg: str) -> None:
    print(msg, flush=True)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def assert_no_2025(df: pl.DataFrame) -> None:
    for c in ("season", "season_g"):
        if c in df.columns:
            assert int(df[c].max()) <= MAX_SEASON, "season 2025 must never be loaded"  # type: ignore[arg-type]


# --------------------------------------------------------------------------- legs (label-free)


def main_legs(con: duckdb.DuckDBPyConnection, market: str) -> pl.DataFrame:
    """Two-sided main line per (season, game, player, book) at t60: most balanced raw prices, ties to the lower point."""
    q = f"""
    WITH s AS (
      SELECT season, game_id, player_id, book, point,
             max(CASE WHEN side='over' THEN implied_prob_raw END) po,
             max(CASE WHEN side='under' THEN implied_prob_raw END) pu,
             max(snapshot_ts) snapshot_ts
      FROM odds_history
      WHERE market = '{market}' AND season <= {MAX_SEASON} AND snapshot_kind = 't60' AND player_id IS NOT NULL
      GROUP BY season, game_id, player_id, book, point),
    s2 AS (SELECT *, count(*) OVER (PARTITION BY season, game_id, player_id, book) n_pts FROM s),
    r AS (SELECT *, row_number() OVER (PARTITION BY season, game_id, player_id, book ORDER BY abs(po - pu), point) rn
          FROM s2 WHERE po IS NOT NULL AND pu IS NOT NULL)
    SELECT season, game_id, player_id, book, point, po AS p, po / (po + pu) AS p_mult, po + pu AS book_sum, n_pts, snapshot_ts
    FROM r WHERE rn = 1
    """
    return con.execute(q).pl()


def build_legs(odds: Path = ODDS) -> pl.DataFrame:
    """All t60 main-line legs of the seven targets that pass the price-only integrity tests (no outcome read)."""
    con = duckdb.connect(str(odds), read_only=True)
    con.execute("PRAGMA memory_limit='1GB'")
    con.execute("PRAGMA threads=2")
    try:
        comp = None
        for k, m in COMPONENT_MARKET.items():
            d = main_legs(con, m).select(
                "season", "game_id", "player_id", "book", pl.col("point").alias(f"c_{k}")
            )
            comp = (
                d
                if comp is None
                else comp.join(
                    d, on=["season", "game_id", "player_id", "book"], how="full", coalesce=True
                )
            )
        assert comp is not None
        out = []
        for tgt, spec in TARGETS.items():
            g = main_legs(con, spec["market"]).filter(
                (pl.col("n_pts") <= MAX_POINTS) & pl.col("book_sum").is_between(*OVERROUND)
            )
            if spec["parts"]:
                cols = [f"c_{k}" for k in spec["parts"]]
                g = g.join(comp, on=["season", "game_id", "player_id", "book"], how="left")
                have = pl.all_horizontal([pl.col(c).is_not_null() for c in cols])
                diff = (pl.col("point") - pl.sum_horizontal(cols)).abs()
                g = g.filter(~have | (diff <= COMPONENT_TOL)).drop(cols)
            out.append(
                g.select(
                    "season", "game_id", "player_id", "book", "point", "p_mult", "snapshot_ts"
                ).with_columns(pl.lit(tgt).alias("target"))
            )
    finally:
        con.close()
    legs = pl.concat(out)
    assert_no_2025(legs)
    return legs


def played_population(db: Path = DB) -> pl.DataFrame:
    """Played regular-season rows with n_prior >= 5 (minutes > 0 only; no stat value)."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        d = con.execute(
            "SELECT s.game_id, s.player_id, g.game_date, g.season AS season_g FROM player_game_stats s JOIN games g USING (game_id) "
            f"WHERE g.season <= {MAX_SEASON} AND s.minutes > 0"
        ).pl()
    finally:
        con.close()
    d = d.sort(["player_id", "game_date", "game_id"]).with_columns(
        pl.int_range(pl.len()).over("player_id").alias("n_prior")
    )
    d = d.filter(pl.col("game_id").str.starts_with("002") & (pl.col("n_prior") >= 5))
    assert_no_2025(d)
    return d.select("game_id", "player_id", "game_date", "season_g")


def tips() -> pl.DataFrame:
    return pl.concat(
        [
            pl.read_parquet(PAIRS / f"pairs_{s}.parquet", columns=["game_id", "tipoff_utc"]).unique(
                "game_id"
            )
            for s in SEASONS
        ]
    ).unique("game_id")


def quantiles_199(grid: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    g = np.sort(grid.astype(np.float64), axis=1)
    q25 = g[:, 49] + 0.25 * (g[:, 50] - g[:, 49])
    q75 = g[:, 148] + 0.75 * (g[:, 149] - g[:, 148])
    return q25, g[:, 99], q75


def kept_legs() -> tuple[pl.DataFrame, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Kept legs (integrity + played population + lead >= 60) joined to the persisted S grids; returns legs, A0, q50, spread, starter10."""
    legs = build_legs()
    pop = played_population()
    legs = (
        legs.join(tips(), on="game_id", how="left")
        .with_columns(
            ((pl.col("tipoff_utc") - pl.col("snapshot_ts")).dt.total_seconds() / 60.0).alias(
                "lead_min"
            )
        )
        .join(pop, on=["game_id", "player_id"], how="inner")
        .filter(pl.col("lead_min") >= LEAD_MIN)
    )
    keys = pl.read_parquet(OOF / "keys.parquet").with_row_index("_row")
    legs = legs.join(
        keys.select("game_id", "player_id", "_row", "starter10"),
        on=["game_id", "player_id"],
        how="inner",
    )
    z = np.load(OOF / "grids.npz")
    a0 = np.empty(legs.height)
    q50 = np.empty(legs.height)
    spr = np.empty(legs.height)
    for tgt in TARGET_LIST:
        idx = np.flatnonzero(legs["target"].to_numpy() == tgt)
        if not len(idx):
            continue
        grid = z[tgt][legs["_row"].to_numpy()[idx]]
        k = np.ceil(legs["point"].to_numpy()[idx])
        a0[idx] = (grid >= k[:, None]).mean(axis=1)
        q25, m50, q75 = quantiles_199(grid)
        q50[idx], spr[idx] = m50, (q75 - q25) / IQR_TO_SD
    return legs, a0, q50, spr, legs["starter10"].fill_null(0.0).to_numpy()


def pick_cells(legs: pl.DataFrame) -> dict[str, dict[str, str | None]]:
    """Primary book per target = first of BOOK_ORDER passing the bars; consistency book = the next one passing (or None)."""
    out: dict[str, dict[str, str | None]] = {}
    for tgt in TARGET_LIST:
        passing = []
        for bk in BOOK_ORDER:
            g = legs.filter((pl.col("target") == tgt) & (pl.col("book") == bk))
            s24 = g.filter(pl.col("season") == SELECT_SEASON)
            if (
                s24.height >= MIN_LEGS_SCORED
                and s24["game_id"].n_unique() >= MIN_GAMES_SCORED
                and g.filter(pl.col("season") == 2023).height >= MIN_FIT
            ):
                passing.append(bk)
        out[tgt] = {
            "primary": passing[0] if passing else None,
            "consistency": passing[1] if len(passing) > 1 else None,
        }
    return out


def frame(
    legs: pl.DataFrame,
    a0: np.ndarray,
    q50: np.ndarray,
    spr: np.ndarray,
    st10: np.ndarray,
    cells: dict[str, dict[str, str | None]],
) -> dict[str, Any]:
    """Arrays for the cells in play (primary and consistency books), sorted for reproducibility."""
    keep = np.zeros(legs.height, bool)
    role = np.full(legs.height, "", dtype=object)
    tg, bk = legs["target"].to_numpy(), legs["book"].to_numpy()
    for t, c in cells.items():
        for r, b in c.items():
            if b:
                m = (tg == t) & (bk == b)
                keep |= m
                role[m] = r
    df = legs.with_columns(
        pl.Series("a0", a0),
        pl.Series("q50", q50),
        pl.Series("spr", spr),
        pl.Series("st10", st10),
        pl.Series("role", role.astype(str)),
    ).filter(pl.Series(keep))
    df = df.sort(["target", "book", "game_date", "game_id", "player_id"])
    D: dict[str, Any] = {
        "n": df.height,
        "season": df["season"].to_numpy().astype(np.int64),
        "date": df["game_date"].to_numpy().astype("datetime64[D]"),
        "book": df["book"].to_numpy().astype(str),
        "target": df["target"].to_numpy().astype(str),
        "role": df["role"].to_numpy().astype(str),
        "game_id": df["game_id"].to_numpy().astype(str),
        "player_id": df["player_id"].to_numpy().astype(np.int64),
        "pm": np.clip(df["a0"].to_numpy(), EPS, 1 - EPS),
        "line": df["point"].to_numpy(),
        "med": df["q50"].to_numpy(),
        "spr": df["spr"].to_numpy(),
        "st10": df["st10"].to_numpy(),
        "mult": df["p_mult"].to_numpy(),
        "lead_min": df["lead_min"].to_numpy(),
    }
    D["mstart"] = D["date"].astype("datetime64[M]").astype("datetime64[D]")
    return D


# --------------------------------------------------------------------------- preflight (label-free)


def versions() -> dict[str, str]:
    return {k: importlib.metadata.version(k) for k in PINNED_VERSIONS}


def preflight(
    declared: dict[str, str],
    D: dict[str, Any],
    legs: pl.DataFrame,
    cells: dict[str, dict[str, str | None]],
) -> dict[str, Any]:
    """Label-free refusals: pinned inputs, library versions, counts and A0 means equal PINNED, z floor, lead, whole lines."""
    files = {
        "ODDS_SHA256": ODDS,
        "OOF_KEYS_SHA256": OOF / "keys.parquet",
        "OOF_GRIDS_SHA256": OOF / "grids.npz",
        "DB_SHA256": DB,
        "PAIRS_SHA256_2023": PAIRS / "pairs_2023.parquet",
        "PAIRS_SHA256_2024": PAIRS / "pairs_2024.parquet",
        "UV_LOCK_SHA256": rule.ROOT / "uv.lock",
    }
    bad = {}
    for k, p in files.items():
        h = sha256_file(p)
        if declared.get(k) != h:
            bad[k] = (h[:12], declared.get(k, "missing")[:12])
    if bad:
        raise RuntimeError(f"pinned input sha256 mismatch: {bad}")
    if versions() != PINNED_VERSIONS:
        raise RuntimeError(f"library versions {versions()} != pinned {PINNED_VERSIONS}")
    man = json.loads((OOF / "manifest.json").read_text())
    if not man["population_reproduces"]:
        raise RuntimeError("the OOF store does not reproduce 25,815 / 25,767")
    if not PINNED:
        raise RuntimeError("PINNED counts are empty: this harness has not been pinned")
    if {t: c["primary"] for t, c in cells.items()} != PINNED["primary"] or {
        t: c["consistency"] for t, c in cells.items()
    } != PINNED["consistency"]:
        raise RuntimeError(f"cells {cells} != pinned")
    got: dict[str, Any] = {}
    for t, c in cells.items():
        for b in c.values():
            if not b:
                continue
            m = (D["target"] == t) & (D["book"] == b)
            n = {str(s): int((m & (D["season"] == s)).sum()) for s in SEASONS}
            key = f"{t}/{b}"
            if n != PINNED["legs"][key]:
                raise RuntimeError(f"{key}: legs {n} != pinned {PINNED['legs'][key]}")
            a0m = round(float(D["pm"][m].mean()), 4)
            if abs(a0m - PINNED["a0_mean"][key]) > 1e-9:
                raise RuntimeError(f"{key}: A0 mean {a0m} != pinned {PINNED['a0_mean'][key]}")
            floor_share = float((D["spr"][m] < SPREAD_MIN).mean())
            if floor_share > Z_FLOOR_FALLBACK_SHARE:
                raise RuntimeError(
                    f"{key}: z floor binds on {floor_share:.3f} > {Z_FLOOR_FALLBACK_SHARE}"
                )
            got[key] = {"legs": n, "a0_mean": a0m, "z_floor_share": round(floor_share, 4)}
    if not np.all(D["lead_min"] >= LEAD_MIN):
        raise RuntimeError("a leg with lead < 60 minutes")
    if np.any(D["line"] % 1.0 == 0):
        raise RuntimeError(
            "whole-number line in the kept legs (pushes are not defined in this rule)"
        )
    if int(D["season"].max()) > MAX_SEASON:
        raise RuntimeError("season 2025 in the frame")
    return {
        "inputs_equal_pinned": True,
        "counts_equal_pinned": True,
        "versions": versions(),
        "cells": got,
        "git_head": git_head(),
    }


def git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=rule.ROOT
    ).stdout.strip()


# --------------------------------------------------------------------------- labels (the only reader of a box-score stat)


def load_labels(D: dict[str, Any]) -> np.ndarray:
    """over = 1 iff the realised stat exceeds the line. Called ONLY by ``select``, after ``rule.require_ready``."""
    con = duckdb.connect(str(DB), read_only=True)
    try:
        pg = con.execute(
            "SELECT s.game_id, s.player_id, s.pts, s.reb, s.ast, s.blk, s.stl FROM player_game_stats s JOIN games g USING (game_id) "
            f"WHERE g.season <= {MAX_SEASON} AND s.minutes > 0"
        ).pl()
    finally:
        con.close()
    pg = pg.with_columns(
        (pl.col("pts") + pl.col("reb") + pl.col("ast")).alias("pra"),
        (pl.col("pts") + pl.col("reb")).alias("pr"),
        (pl.col("pts") + pl.col("ast")).alias("pa"),
        (pl.col("reb") + pl.col("ast")).alias("ra"),
        (pl.col("blk") + pl.col("stl")).alias("bs"),
    )
    base = pl.DataFrame(
        {
            "game_id": D["game_id"],
            "player_id": D["player_id"],
            "target": D["target"],
            "line": D["line"],
        }
    )
    j = base.join(pg, on=["game_id", "player_id"], how="left")
    y = np.empty(D["n"])
    for t in TARGET_LIST:
        m = D["target"] == t
        v = j[t].to_numpy().astype(np.float64)
        y[m] = (v[m] > D["line"][m]).astype(np.float64)
        assert np.isfinite(v[m]).all(), f"{t}: legs without a box row"
    return y


def synthetic_labels(D: dict[str, Any], truth: str, seed: int) -> np.ndarray:
    """Labels from a known truth; no real outcome is read. truth: biased (T477-like), calibrated (p = p_model), baserate (no information)."""
    pm = D["pm"]
    if truth == "biased":
        p = sigmoid(SYN_A + SYN_B * logit(pm))
    elif truth == "calibrated":
        p = pm
    elif truth == "baserate":
        p = np.full(D["n"], 0.475)
    else:
        raise ValueError(truth)
    return np.asarray(np.random.default_rng(seed).random(D["n"]) < p, dtype=np.float64)


# --------------------------------------------------------------------------- the map


def logit(p: np.ndarray) -> np.ndarray:
    return np.asarray(np.log(p) - np.log1p(-p))


def sigmoid(x: np.ndarray) -> np.ndarray:
    return np.asarray(1.0 / (1.0 + np.exp(-x)))


def design(
    pm: np.ndarray, line: np.ndarray, med: np.ndarray, spr: np.ndarray, st10: np.ndarray
) -> np.ndarray:
    """[1, x, z, bench, bench * x]. Takes no price argument by construction (market-free)."""
    x = logit(np.clip(pm, EPS, 1 - EPS))
    z = np.clip((line - med) / np.maximum(spr, SPREAD_MIN), -Z_CLIP, Z_CLIP)
    bench = (st10 < 0.5).astype(np.float64)
    return np.column_stack([np.ones_like(x), x, z, bench, bench * x])


def age_weights(dates: np.ndarray, block_start: np.datetime64) -> np.ndarray:
    age = (block_start - dates).astype("timedelta64[D]").astype(np.float64)
    assert np.all(age > 0), "a fit row on or after the block start"
    return np.asarray(0.5 ** (age / HALF_LIFE_DAYS))


def fit_logistic(X: np.ndarray, y: np.ndarray, w: np.ndarray, ridge: float = RIDGE) -> np.ndarray:
    R = np.eye(X.shape[1]) * ridge
    R[0, 0] = 0.0
    b = np.zeros(X.shape[1])
    for _ in range(100):
        p = sigmoid(X @ b)
        g = X.T @ (w * (y - p)) - R @ b
        H = X.T @ (X * (w * p * (1 - p))[:, None]) + R
        step = np.linalg.solve(H, g)
        b = b + step
        if np.max(np.abs(step)) < 1e-10:
            break
    return b


def walk_forward(
    D: dict[str, Any],
    y: np.ndarray,
    season: int,
    arm: str,
    k: int = 0,
    only_block: np.datetime64 | None = None,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Out-of-block probabilities for every leg of ``season`` in every cell; NaN elsewhere. arm in {cal, base, shuffle}.
    A cell (target, book) is fit on every earlier leg of that cell (both seasons), strictly dated before the block start."""
    assert arm in ("cal", "base", "shuffle")
    X = design(D["pm"], D["line"], D["med"], D["spr"], D["st10"])
    out = np.full(D["n"], np.nan)
    info: list[dict[str, Any]] = []
    cells = sorted({(t, b) for t, b in zip(D["target"], D["book"], strict=True)})
    for ci, (tgt, book) in enumerate(cells):
        cell = (D["target"] == tgt) & (D["book"] == book)
        tg = cell & (D["season"] == season)
        for bi, b0 in enumerate(np.unique(D["mstart"][tg])):
            if only_block is not None and b0 != only_block:
                continue
            fit = cell & (D["date"] < b0)
            sc = tg & (D["mstart"] == b0)
            nfit = int(fit.sum())
            rec: dict[str, Any] = {
                "target": tgt,
                "book": book,
                "block": str(b0)[:7],
                "n_fit": nfit,
                "n_score": int(sc.sum()),
            }
            if nfit < MIN_FIT:
                out[sc] = np.clip(D["pm"][sc], EPS, 1 - EPS)
                rec["identity"] = True
            else:
                yf = y[fit]
                wf = age_weights(D["date"][fit], b0)
                rec["n_eff"] = float(wf.sum() ** 2 / (wf**2).sum())
                if arm == "shuffle":
                    yf = np.random.default_rng([1000 + k, ci, bi]).permutation(yf)
                if arm == "base":
                    out[sc] = float(np.clip(np.sum(wf * yf) / np.sum(wf), EPS, 1 - EPS))
                else:
                    beta = fit_logistic(X[fit], yf, wf)
                    out[sc] = np.clip(sigmoid(X[sc] @ beta), EPS, 1 - EPS)
                    rec["beta"] = [float(v) for v in beta]
                rec["identity"] = False
            info.append(rec)
    return out, info


# --------------------------------------------------------------------------- scoring


def ll(p: np.ndarray, o: np.ndarray, eps: float = EPS) -> np.ndarray:
    pc = np.clip(p, eps, 1 - eps)
    return np.asarray(-np.where(o > 0.5, np.log(pc), np.log1p(-pc)))


class Boot:
    """Percentile bootstrap of a mean over clusters (game or date): 2,000 multinomial resamples, seed 0."""

    def __init__(self, codes: np.ndarray, seed: int = SEED, b: int = N_BOOT) -> None:
        u, self.code = np.unique(codes, return_inverse=True)
        self.G, self.B = len(u), b
        self.W = (
            np.random.default_rng(seed)
            .multinomial(self.G, np.full(self.G, 1.0 / self.G), size=b)
            .astype(float)
        )

    def mean(self, v: np.ndarray, m: np.ndarray) -> dict[str, Any]:
        k = m & np.isfinite(v)
        n = int(k.sum())
        if n == 0:
            return {
                "mean": None,
                "lo": None,
                "hi": None,
                "hi_1s": None,
                "lo_1s": None,
                "p": None,
                "n": 0,
            }
        s = np.bincount(self.code[k], weights=v[k], minlength=self.G)
        c = np.bincount(self.code[k], minlength=self.G).astype(float)
        den = self.W @ c
        rep = (self.W @ s) / np.where(den > 0, den, np.nan)
        rep = rep[np.isfinite(rep)]
        lo, hi = np.percentile(rep, [2.5, 97.5])
        lo1, hi1 = np.percentile(rep, [5, 95])
        return {
            "mean": float(v[k].mean()),
            "lo": float(lo),
            "hi": float(hi),
            "lo_1s": float(lo1),
            "hi_1s": float(hi1),
            "p": float(
                (1 + np.sum(rep >= 0)) / (len(rep) + 1)
            ),  # p that the mean is < 0 (negative = CAL better)
            "n": n,
        }


def bh_adjust(p: np.ndarray) -> np.ndarray:
    m = len(p)
    o = np.argsort(p)
    q = np.empty(m)
    prev = 1.0
    for rank, i in reversed(list(enumerate(o, start=1))):
        prev = min(prev, p[i] * m / rank)
        q[i] = prev
    return q


def dense(codes: np.ndarray) -> np.ndarray:
    return np.unique(codes, return_inverse=True)[1]


def score_stage(
    D: dict[str, Any], y: np.ndarray, season: int = SELECT_SEASON, k_null: int = K_NULL
) -> dict[str, Any]:
    """Every arm and contrast of Stage 1. y = labels (real at ``select``; synthetic in tests and the rehearsal)."""
    p_cal, info = walk_forward(D, y, season, "cal")
    p_c0, _ = walk_forward(D, y, season, "base")
    prim = (D["season"] == season) & (D["role"] == "primary")
    cons = (D["season"] == season) & (D["role"] == "consistency")
    bg, bd = Boot(dense(D["game_id"][prim])), Boot(dense(D["date"][prim].astype(str)))
    o = y[prim]
    pm = D["pm"][prim]
    L = {
        "A0": ll(pm, o),
        "CAL": ll(p_cal[prim], o),
        "C0": ll(p_c0[prim], o),
        "COIN": ll(np.full(len(o), 0.5), o),
    }
    L["MKT"] = ll(D["mult"][prim], o, MARKET_EPS)
    every = np.ones(len(o), bool)
    sh = []
    for k in range(k_null):
        p_sh, _ = walk_forward(D, y, season, "shuffle", k)
        sh.append(ll(p_sh[prim], o))
    sh_means = np.array([v.mean() for v in sh]) if sh else np.array([])
    res: dict[str, Any] = {
        "season": season,
        "n": int(prim.sum()),
        "games": bg.G,
        "dates": bd.G,
        "ll": {a: float(v.mean()) for a, v in L.items()},
        "identity_rows": int(sum(r["n_score"] for r in info if r["identity"])),
        "d_cal_a0": bg.mean(L["CAL"] - L["A0"], every),
        "d_cal_a0_date": bd.mean(L["CAL"] - L["A0"], every),
        "d_cal_c0": bg.mean(L["CAL"] - L["C0"], every),
        "d_c0_a0": bg.mean(L["C0"] - L["A0"], every),
    }
    if sh:
        res["null"] = {
            "k": k_null,
            "sd": float(sh_means.std(ddof=1)),
            "sh_minus_a0_mean": float(sh_means.mean() - L["A0"].mean()),
            "sh_minus_c0_mean": float(sh_means.mean() - L["C0"].mean()),
        }
        res["d_cal_sh"] = bg.mean(L["CAL"] - np.mean(sh, axis=0), every)
    res["ref"] = {
        "a0_minus_coin": bg.mean(L["A0"] - L["COIN"], every),
        "cal_minus_coin": bg.mean(L["CAL"] - L["COIN"], every),
        "cal_minus_market": bg.mean(L["CAL"] - L["MKT"], every),
    }
    starter = D["st10"][prim] >= 0.5
    res["calib"] = {
        role: {
            a: bg.mean(p - o, rm) for a, p in (("A0", pm), ("CAL", p_cal[prim]), ("C0", p_c0[prim]))
        }
        for role, rm in (("all", every), ("starter", starter), ("bench", ~starter))
    }
    tg = D["target"][prim]
    per: dict[str, Any] = {}
    for t in TARGET_LIST:
        tm = tg == t
        if not tm.any():
            continue
        per[t] = bg.mean(L["CAL"] - L["A0"], tm) | {
            "cal_minus_c0": bg.mean(L["CAL"] - L["C0"], tm),
            "book": str(D["book"][prim][tm][0]),
        }
    names = [t for t in per if per[t]["p"] is not None]
    for t, q in zip(names, bh_adjust(np.array([per[t]["p"] for t in names])), strict=True):
        per[t]["q_bh"] = float(q)
    res["per_target"] = per
    res["slices"] = {
        nm: bg.mean(L["CAL"] - L["A0"], rm)
        for nm, rm in (("starter", starter), ("bench", ~starter))
    }
    res["objects"] = {
        a: {
            "d_a0": bg.mean(L[a] - L["A0"], every),
            "per_target_lo": {t: bg.mean(L[a] - L["A0"], tg == t)["lo"] for t in per},
            "slices": {
                nm: bg.mean(L[a] - L["A0"], rm)["mean"]
                for nm, rm in (("starter", starter), ("bench", ~starter))
            },
            "calib": res["calib"]["all"][a]["mean"],
        }
        for a in ("CAL", "C0")
    }
    # P7: the consistency books (targets that have one), same walk-forward arms
    if cons.any():
        oc = y[cons]
        l_cal, l_a0, l_c0 = ll(p_cal[cons], oc), ll(D["pm"][cons], oc), ll(p_c0[cons], oc)
        bc = Boot(dense(D["game_id"][cons]))
        ec = np.ones(len(oc), bool)
        res["consistency"] = {
            "n": int(cons.sum()),
            "cal_minus_a0": bc.mean(l_cal - l_a0, ec),
            "c0_minus_a0": bc.mean(l_c0 - l_a0, ec),
        }
    res["blocks"] = info
    res["reliability"] = reliability(p_cal[prim], o)
    return res


def reliability(p: np.ndarray, o: np.ndarray, bins: int = 10) -> list[dict[str, float]]:
    idx = np.minimum((p * bins).astype(int), bins - 1)
    return [
        {
            "bin": b / bins,
            "n": int((idx == b).sum()),
            "pred": float(p[idx == b].mean()),
            "obs": float(o[idx == b].mean()),
        }
        for b in range(bins)
        if (idx == b).any()
    ]


def object_checks(res: dict[str, Any], arm: str) -> dict[str, bool]:
    o = res["objects"][arm]
    d = o["d_a0"]
    cons = res.get("consistency")
    p7 = (
        True
        if cons is None
        else bool((cons["cal_minus_a0"] if arm == "CAL" else cons["c0_minus_a0"])["mean"] < 0)
    )
    return {
        "P1": bool(
            d["mean"] <= FLOOR and d["hi"] < 0 and res["d_cal_a0_date"]["hi"] < 0
            if arm == "CAL"
            else d["mean"] <= FLOOR and d["hi"] < 0
        ),
        "P4": bool(abs(o["calib"]) <= CAL_ABS_MAX),
        "P5": all(not (lo is not None and lo > 0) for lo in o["per_target_lo"].values()),
        "P6": all(v is None or v <= 0 for v in o["slices"].values()),
        "P7": p7,
    }


def evaluate(res: dict[str, Any]) -> dict[str, Any]:
    """The rule's Stage 1 words. CALIBRATED = P1-P7; SHRINKAGE_ONLY = P1, P4-P7 but not P2 or P3 (C0 served, and it must itself pass); FAIL otherwise.
    ``close_rule`` = the kill criterion: pooled CAL - A0 > 0 with a game-clustered one-sided lower bound > 0."""
    ck = object_checks(res, "CAL")
    c = {
        "P1": ck["P1"],
        "P2": bool(res["d_cal_c0"]["hi"] < 0),
        "P3": bool(
            res["d_cal_sh"]["mean"] <= -NULL_SD_MULT * res["null"]["sd"]
            and res["d_cal_sh"]["hi"] < 0
        ),
        "P4": ck["P4"],
        "P5": ck["P5"],
        "P6": ck["P6"],
        "P7": ck["P7"],
    }
    verdict, obj = "FAIL", None
    if all(c.values()):
        verdict, obj = "CALIBRATED", "CAL"
    elif all(v for k, v in c.items() if k not in ("P2", "P3")) and all(
        object_checks(res, "C0").values()
    ):
        verdict, obj = "SHRINKAGE_ONLY", "C0"
    return {
        "checks": c,
        "verdict": verdict,
        "served_object": obj,
        "close_rule": bool(res["d_cal_a0"]["mean"] > 0 and res["d_cal_a0"]["lo_1s"] > 0),
        "stage1_cannot_pass_the_rule": True,
    }


# --------------------------------------------------------------------------- guards


def planted_future(D: dict[str, Any], y: np.ndarray, season: int = SELECT_SEASON) -> dict[str, Any]:
    """Per block: labels dated on/after the block start replaced by their complement leave that block's output identical;
    complementing labels inside the window moves it (positive control). y must be synthetic."""
    probes = identical = moved = 0
    bad: list[str] = []
    tg = D["season"] == season
    for b0 in np.unique(D["mstart"][tg]):
        sc = tg & (D["mstart"] == b0)
        later = D["date"] >= b0
        y_g, y_c = y.copy(), y.copy()
        y_g[later] = 1.0 - y_g[later]
        y_c[~later] = 1.0 - y_c[~later]
        for arm in ("cal", "base", "shuffle"):
            a, _ = walk_forward(D, y, season, arm, only_block=b0)
            g, _ = walk_forward(D, y_g, season, arm, only_block=b0)
            c, _ = walk_forward(D, y_c, season, arm, only_block=b0)
            probes += 1
            same, mv = (
                np.array_equal(a[sc], g[sc], equal_nan=True),
                not np.array_equal(a[sc], c[sc], equal_nan=True),
            )
            identical += same
            moved += mv
            if not (same and mv):
                bad.append(f"{str(b0)[:7]} {arm} same={same} moved={mv}")
    return {"probes": probes, "identical": identical, "positive_control_moved": moved, "bad": bad}


# --------------------------------------------------------------------------- entrypoints


def build_frame() -> tuple[dict[str, Any], pl.DataFrame, dict[str, dict[str, str | None]]]:
    legs, a0, q50, spr, st10 = kept_legs()
    cells = pick_cells(legs)
    return frame(legs, a0, q50, spr, st10, cells), legs, cells


def rehearse(D: dict[str, Any], k_null: int = K_NULL) -> dict[str, Any]:
    """Whole Stage 1 on synthetic labels, three truths. All of CALIBRATED, SHRINKAGE_ONLY and FAIL must be reachable."""
    out: dict[str, Any] = {}
    for truth in ("biased", "baserate", "calibrated"):
        y = synthetic_labels(D, truth, seed=7)
        res = score_stage(D, y, k_null=k_null)
        ev = evaluate(res)
        out[truth] = {
            "verdict": ev["verdict"],
            "checks": ev["checks"],
            "cal_minus_a0": res["d_cal_a0"],
            "cal_minus_c0": res["d_cal_c0"],
            "null_sd": res["null"]["sd"],
            "close_rule": ev["close_rule"],
        }
        _log(
            f"rehearse {truth}: {ev['verdict']} CAL-A0 {res['d_cal_a0']['mean']:+.4f} CAL-C0 {res['d_cal_c0']['mean']:+.4f}"
        )
    y = synthetic_labels(D, "biased", seed=7)
    out["planted_future"] = planted_future(D, y)
    out["expected"] = {"biased": "CALIBRATED", "baserate": "SHRINKAGE_ONLY", "calibrated": "FAIL"}
    out["all_three_reachable"] = all(out[t]["verdict"] == e for t, e in out["expected"].items())
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("preflight")
    r = sub.add_parser("rehearse")
    r.add_argument("--k-null", type=int, default=K_NULL)
    s = sub.add_parser("select")
    s.add_argument("--sha", required=True)
    pr = sub.add_parser("print-pins", help="label-free: print the PINNED dict for the harness")
    pr.add_argument("--k-null", type=int, default=K_NULL)
    a = ap.parse_args(argv)
    t0 = time.time()
    now = subprocess.run(["date"], capture_output=True, text=True, check=False).stdout.strip()
    _log(f"date: {now}")
    if a.cmd == "select":
        rule.require_ready(a.sha)  # frozen rule AND pinned harness, before any outcome is read
    D, legs, cells = build_frame()
    _log(f"frame {D['n']} legs, cells {cells} ({time.time() - t0:.0f}s)")
    if a.cmd == "print-pins":
        pins: dict[str, Any] = {
            "primary": {t: c["primary"] for t, c in cells.items()},
            "consistency": {t: c["consistency"] for t, c in cells.items()},
            "legs": {},
            "a0_mean": {},
        }
        for t, c in cells.items():
            for b in c.values():
                if b:
                    m = (D["target"] == t) & (D["book"] == b)
                    pins["legs"][f"{t}/{b}"] = {
                        str(s_): int((m & (D["season"] == s_)).sum()) for s_ in SEASONS
                    }
                    pins["a0_mean"][f"{t}/{b}"] = round(float(D["pm"][m].mean()), 4)
        print(json.dumps(pins, indent=1))
        return 0
    declared = rule.declared()
    pf = preflight(declared, D, legs, cells)
    _log(f"preflight ok ({time.time() - t0:.0f}s)")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if a.cmd == "preflight":
        (OUT_DIR / "preflight.json").write_text(json.dumps(pf, indent=1, default=str))
        return 0
    if a.cmd == "rehearse":
        rh = rehearse(D, a.k_null)
        res = {
            "date": now,
            "preflight": pf,
            "rehearsal": rh,
            "elapsed_s": round(time.time() - t0, 1),
        }
        (OUT_DIR / "rehearsal.json").write_text(json.dumps(res, indent=1, default=str))
        return 0 if rh["all_three_reachable"] and not rh["planted_future"]["bad"] else 1
    y = load_labels(D)
    res = score_stage(D, y)
    ev = evaluate(res)
    (OUT_DIR / "select.json").write_text(
        json.dumps(
            {"date": now, "preflight": pf, "result": res, "evaluation": ev}, indent=1, default=str
        )
    )
    _log(json.dumps(ev))
    return 0


if __name__ == "__main__":
    sys.exit(main())
