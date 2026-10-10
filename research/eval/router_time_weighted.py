# ruff: noqa: E501
"""ROUTER_TIME_WEIGHTED (docs/prereg/ROUTER_TIME_WEIGHTED.md, frozen sha256 bb633f7a, commit 918e03c).

    M="-m research.eval.router_time_weighted"
    .venv/bin/python $M gate    # minimum-data checks 1-4 on the real data (light; C4 coverage from t30 nulls)
    .venv/bin/python $M smoke   # two month blocks, pts: C4 refit vs A0 cache, router on block 2; nothing written
    .venv/bin/python $M run     # gate -> candidates -> router -> score -> report, under heavy.lock

Research module only (``nba/`` untouched). Read-only on ``nba.duckdb``; every SQL carries
``season <= 2024``; the frames are asserted. C0 is read from the FOUR_FACTORS A0 arm cache
(production's OOF integer grids; reproduction to 1e-6 is check 4).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import yaml
from scipy import stats as sps

from nba.eval.context_residual_eval import (
    MAX_SEASON,
    TEST_SEASONS,
    bh_adjust,
    boot_p_value,
    crps_from_quantiles,
    load_inputs,
    month_blocks,
)
from nba.eval.lower_tail_eval import attach_realised
from nba.props.baselines import _DEFAULT_STD
from nba.props.context_residual import (
    CRPS_TAUS,
    PROP_STATS,
    ContextResidualConfig,
    build_features,
    flagged_from_availability,
    stat_feature_names,
    to_integer_support,
)
from research.eval.f11_lineup_context import (
    _ci,
    _jd,
    _write_json,
    frozen_sha256,
    git_sha,
    heavy_lock,
)
from research.eval.four_factors_eval import (
    TLL_THRESHOLDS,  # noqa: F401  (documented: tll thresholds are the FOUR_FACTORS ones)
    check_a0_repro,
    collect_arm,
    row_scores,
)

DOC = Path("docs/prereg/ROUTER_TIME_WEIGHTED.md")
OUT_DIR = Path("reports/prereg_router")
REPORT_MD = Path("reports/prereg_router.md")
FF_CACHE = Path("data/ops/four_factors_cache")
CACHE_DIR = Path("data/ops/router_cache")
FROZEN_PREFIX = "bb633f7a"
FROZEN_FULL = "bb633f7a3176f7cc3709a9c7e8581461eacd1b04f06e293dfbd8998138213c17"
SEEDS = {"model": 0, "bootstrap": 0, "pit": 0}
N_BOOT = 2000
STATS: tuple[str, ...] = PROP_STATS
CANDS: tuple[str, ...] = ("C0", "C1", "C2", "C3", "C4")
ARMS: tuple[str, ...] = ("A0", "A1", "A2", "A3", "A4")
KEYS = ["game_id", "player_id"]

# --- frozen constants
HALF_LIFE_DAYS = 60.0
MIN_TRAIL = 300
TAU = 0.05
TIER_EDGES = (10.0, 17.0, 24.0)
COLD_N = 20
MIN_CELL_2023 = 300
CELL_MIN_N, CELL_MIN_GAMES = 500, 150
COVERAGE_MIN = 0.95
FLOOR = -0.005
A4_FLOOR = -0.003
MAX_BIAS = 0.5
COV_LO, COV_HI = 0.75, 0.85
SLICE_WORSE = 0.01
MIN_SLICE_N = 300
RECENCY_HALFLIFE_GAMES = 10.0  # ForwardConfig.halflife_games default (= m10_/sd10_ states)

UNSTATED: list[str] = [
    "C1 is the OOF equivalent in the harness: Normal(m10_<stat>, sd10_<stat>) from the production player states "
    "(decaying weights 0.5**(age/10) over ALL prior played games, same weighted-variance * n/(n-1) formula as "
    "nba.props.forward.recency_weighted_dist, halflife 10 games = ForwardConfig default; equivalence is unit-tested), "
    "Normal for all four stats as the production fallback is, quantiles at the 199 CRPS taus, then to_integer_support",
    "C2/C3 windows are the player's last 10 / 5 PLAYED games (minutes > 0) strictly before the row's (game_date, game_id), "
    "crossing seasons like the production states; mean = window mean, dispersion = window sample variance (ddof 1); "
    "pts Normal (std 0 -> league default std), reb/ast/fg3m NegBin by method of moments (variance floored at mean*(1+1e-6), "
    "mean floored at 1e-6, i.e. near-Poisson when under-dispersed); every row has n_prior >= 5 so the window has >= 5 games",
    "C4 is REBUILT on the integer grid (the stored reports/lineups_known rows hold continuous 19-quantile q10/q90 and a "
    "continuous CRPS, not an integer grid): same harness as C0 (collect_arm, month blocks, seed 0), feature names "
    "stat_feature_names(stat, lineups_known=True) on build_features(..., lineups_known=True). The t30_* columns are the "
    "box-score starter PROXY of docs/LINEUPS_KNOWN.md (no real T-30 snapshots exist for seasons <= 2024)",
    "C4 'has a T-30 row' = t30_starter is not null on that row; rows without one fall back to the C0 grid; the fallback share is reported",
    "block = calendar month ([start, end) as month_blocks); trailing rows for a scored block are ALL rows with game_date < block start "
    "from both seasons (the 2023-10 block therefore routes to C0 everywhere); weight of a trailing row = 0.5 ** ((start - game_date).days / 60)",
    "'>= 300 trailing rows' is the raw (unweighted) count of trailing rows in the cell; trailing CRPS is the weighted mean integer CRPS of each candidate on those rows",
    "R1 picks the lowest weighted trailing CRPS; ties break toward the lower candidate index (C0 first). R2 weights = softmax(-trailing_crps / 0.05) per cell; "
    "cells under 300 trailing rows use weight 1 on C0",
    "R2 'quantile mixture' = the repo's pool convention (research/stack/scoring.py pool): weighted average of the 199-quantile grids "
    "(Vincentization), then to_integer_support",
    "cells are per stat: ppg tier of m10_pts (the production recency mean of prior pts, halflife 10) with edges [<10, 10-17, 17-24, 24+) x cold (n_prior < 20); "
    "8 cells per stat, 32 in all",
    "check 2 merge: counted on 2023 rows of each stat; within each cold flag the lowest tier group with < 300 rows merges into the next tier up; the top group, if still short, "
    "merges down into its neighbour; the merged definition is fixed from 2023 and used for 2024; check 2 passes if every merged cell has >= 300 rows in 2023",
    "check 1: coverage = share of production (A0) rows with a finite candidate forecast; C0-C3 gated at >= 0.95 in both seasons; C4's coverage (t30 non-null share) is reported separately, not gated",
    "check 3: for every block of every stat, rows dated on/after the block start get dates +1 day and garbage CRPS; the trailing counts, trailing CRPS, R1 picks and R2 weights for the block must be bit-identical; "
    "and a prior-row perturbation must change them (not inert)",
    "A3 oracle: per month block and cell, the candidate with the lowest mean integer CRPS on that block's rows (leak by construction); A4: per stat the candidate with the lowest mean integer CRPS over all 2023 rows",
    "per-cell table: best = candidate with the lowest mean CRPS among C0-C4 in that cell over the 2023 rows (selection season); dcrps_best_vs_c0 is best minus C0 per season "
    "(2023 is in-sample, 2024 is the confirmation); eligibility (>= 500 rows and >= 150 distinct games) is evaluated per season; BH over all eligible cells of that season "
    "(all stats); claim = 2024 only, eligible, CI upper < 0, BH p < 0.05 and point <= -0.005 (same floor as pass rule 1); 2023 claims are never made",
    "pass rule 1 for A1 and A2 is evaluated in BOTH seasons; the BH family is the four stats within a season, separately for A1 and A2; guards, slice guard (cells with n >= 300 in 2024, "
    "arm minus A0 point > +0.01 fails) and rule 3 (arm minus A4 point <= -0.003, 2024 all rows) use 2024; rule 3 additionally needs checks 1-4 ok; a stat passes if A1 or A2 passes",
    "pit_q10/pit_q20 = P(PIT <= 0.10 / 0.20), cov80 = P(0.10 < PIT <= 0.90) with the FOUR_FACTORS randomized integer PIT (seed 0); tll = the FOUR_FACTORS thresholds (pts 10/15, reb 4/6, ast 2/4, fg3m 1/2)",
    "bias = mean(y - mean of the 199-quantile integer grid); n_games = distinct game_id; MDE = half the CI width of the arm minus A0 delta",
    "the 2023 / 2024 split is games.season of the row; the walk-forward OOF rows (2023-10 .. 2025-06 calendar months with games) are exactly the A0 cache rows (55202)",
]


# --------------------------------------------------------------------------- inputs


def load_study(
    db_path: str = "nba.duckdb",
    elo_config: str = "configs/mov_elo_tuned.yaml",
    config_path: str = "configs/context_residual.yaml",
) -> dict[str, Any]:
    elo = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    cfg = ContextResidualConfig(**yaml.safe_load(Path(config_path).read_text()).get("model", {}))
    games, pgs, static, avail = load_inputs(db_path, None)
    assert int(games["season"].max()) <= MAX_SEASON, "frozen holdout (2025) must not be loaded"  # type: ignore[arg-type]
    flagged, _ = flagged_from_availability(avail, games, cfg.report)
    feats = attach_realised(
        build_features(games, pgs, static, flagged, elo, lineups_known=True), games, pgs
    )
    assert int(feats["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
    return {"games": games, "pgs": pgs, "feats": feats, "cfg": cfg}


def load_a0(stat: str) -> dict[str, Any]:
    z = np.load(FF_CACHE / f"arm_{stat}_A0.npz")
    return {
        "qi": z["qi"],
        "y": z["y"],
        "meta": pl.read_parquet(FF_CACHE / f"arm_{stat}_A0.parquet"),
    }


def row_frame(stat: str, a0: dict[str, Any], feats: pl.DataFrame) -> pl.DataFrame:
    """A0 rows (order kept) with the columns the router needs."""
    j = feats.select(
        *KEYS,
        "game_date",
        "m10_pts",
        pl.col(f"m10_{stat}").alias("m_rec"),
        pl.col(f"sd10_{stat}").alias("s_rec"),
        "t30_starter",
    )
    assert j.select(KEYS).is_duplicated().sum() == 0
    out = (
        a0["meta"].select("game_id", "player_id", "season", "n_prior").join(j, on=KEYS, how="left")
    )
    assert out.height == a0["meta"].height == len(a0["y"])
    assert out["game_date"].null_count() == 0 and out["m_rec"].null_count() == 0
    assert int(out["season"].max()) <= MAX_SEASON
    result: pl.DataFrame = out
    return result


# --------------------------------------------------------------------------- candidates


def grid_normal(mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    q = mean[:, None] + std[:, None] * sps.norm.ppf(CRPS_TAUS)[None, :]
    return to_integer_support(q).astype(np.int16)


def grid_nbinom(mean: np.ndarray, var: np.ndarray, chunk: int = 20000) -> np.ndarray:
    m = np.maximum(np.asarray(mean, dtype=float), 1e-6)
    v = np.maximum(np.asarray(var, dtype=float), m * (1 + 1e-6))
    pr = m / v
    nn = m * pr / (1 - pr)
    out = np.empty((len(m), len(CRPS_TAUS)), dtype=np.int16)
    for i in range(0, len(m), chunk):
        sl = slice(i, i + chunk)
        q = sps.nbinom.ppf(CRPS_TAUS[None, :], n=nn[sl, None], p=pr[sl, None])
        out[sl] = to_integer_support(np.asarray(q, dtype=float)).astype(np.int16)
    return out


def rolling_stats(
    played: pl.DataFrame, rows: pl.DataFrame, ordmap: dict[str, int], stat: str, k: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Mean, sample variance and window size of the player's last ``k`` played games strictly
    before each row's (game_date, game_id)."""
    big = 1 << 20
    pp = played["player_id"].to_numpy().astype(np.int64)
    po = np.array([ordmap[g] for g in played["game_id"].to_list()], dtype=np.int64)
    keys = pp * big + po
    assert np.all(np.diff(keys) > 0), (
        "played rows must be unique and sorted by (player, game order)"
    )
    x = played[stat].to_numpy().astype(float)
    cs = np.concatenate([[0.0], np.cumsum(x)])
    cs2 = np.concatenate([[0.0], np.cumsum(x * x)])
    rp = rows["player_id"].to_numpy().astype(np.int64)
    ro = np.array([ordmap[g] for g in rows["game_id"].to_list()], dtype=np.int64)
    idx = np.searchsorted(keys, rp * big + ro, side="left")
    start = np.searchsorted(keys, rp * big, side="left")
    win = np.minimum(k, idx - start)
    s1 = cs[idx] - cs[idx - win]
    s2 = cs2[idx] - cs2[idx - win]
    w = np.maximum(win, 1)
    mean = s1 / w
    var = np.where(win >= 2, np.clip((s2 - win * mean**2) / np.maximum(win - 1, 1), 0.0, None), 0.0)
    return mean, var, win


def rolling_grid(stat: str, mean: np.ndarray, var: np.ndarray) -> np.ndarray:
    if stat == "pts":
        sd = np.sqrt(var)
        sd = np.where(sd > 0, sd, _DEFAULT_STD[stat])
        return grid_normal(mean, sd)
    return grid_nbinom(mean, var)


def build_c1_c3(
    stat: str, rows: pl.DataFrame, games: pl.DataFrame, pgs: pl.DataFrame
) -> dict[str, np.ndarray]:
    cp = CACHE_DIR / f"cand_{stat}_C123.npz"
    if cp.exists():
        z = np.load(cp)
        return {c: z[c] for c in ("C1", "C2", "C3")}
    ordmap = {g: i for i, g in enumerate(games.sort(["game_date", "game_id"])["game_id"].to_list())}
    played = (
        pgs.filter(pl.col("minutes") > 0)
        .with_columns(pl.col("game_id").replace_strict(ordmap, return_dtype=pl.Int64).alias("_o"))
        .sort(["player_id", "_o"])
    )
    out = {"C1": grid_normal(rows["m_rec"].to_numpy(), rows["s_rec"].to_numpy())}
    n_prior = rows["n_prior"].to_numpy()
    for c, k in (("C2", 10), ("C3", 5)):
        mean, var, win = rolling_stats(played, rows, ordmap, stat, k)
        assert np.array_equal(win, np.minimum(k, n_prior)), "window size != min(k, n_prior)"
        out[c] = rolling_grid(stat, mean, var)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(cp, C1=out["C1"], C2=out["C2"], C3=out["C3"])
    return out


def build_c4_raw(
    stat: str, feats: pl.DataFrame, cfg: ContextResidualConfig, a0: dict[str, Any]
) -> np.ndarray:
    cp = CACHE_DIR / f"cand_{stat}_C4raw.npz"
    if cp.exists():
        return np.asarray(np.load(cp)["qi"])
    t0 = time.time()
    o = collect_arm(feats, stat, cfg, stat_feature_names(stat, lineups_known=True))
    assert o["meta"].select(KEYS).equals(a0["meta"].select(KEYS)), "C4 rows != production rows"
    assert np.array_equal(o["y"], a0["y"])
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(cp, qi=o["qi"])
    print(f"{stat} C4 refit done in {time.time() - t0:.0f}s", flush=True)
    return np.asarray(o["qi"])


# --------------------------------------------------------------------------- cells


def tier_of(m10_pts: np.ndarray) -> np.ndarray:
    return np.searchsorted(np.array(TIER_EDGES), m10_pts, side="right").astype(int)


def merge_groups(counts: list[int], minimum: int = MIN_CELL_2023) -> list[list[int]]:
    """Tier groups: the lowest group under ``minimum`` merges up (into the next tier); the top
    group, if still short, merges down."""
    groups = [[i] for i in range(len(counts))]

    def n_of(g: list[int]) -> int:
        return sum(counts[i] for i in g)

    while len(groups) > 1:
        short = [i for i, g in enumerate(groups) if n_of(g) < minimum]
        if not short:
            break
        i = short[0]
        j = i + 1 if i + 1 < len(groups) else i - 1
        lo, hi = min(i, j), max(i, j)
        groups[lo : hi + 1] = [groups[lo] + groups[hi]]
    return groups


def cell_defs(rows: pl.DataFrame) -> dict[str, Any]:
    """Merged cell assignment from the 2023 rows (fixed for both seasons)."""
    tier = tier_of(rows["m10_pts"].to_numpy())
    cold = (rows["n_prior"].to_numpy() < COLD_N).astype(int)
    s23 = (rows["season"] == TEST_SEASONS[0]).to_numpy()
    cell = np.zeros(rows.height, dtype=int)
    labels: list[str] = []
    detail: dict[str, Any] = {}
    names = ["<10", "10-17", "17-24", "24+"]
    for cf in (0, 1):
        counts = [int(((tier == t) & (cold == cf) & s23).sum()) for t in range(4)]
        groups = merge_groups(counts)
        detail[f"cold={cf}"] = {"counts_2023": counts, "groups": groups}
        for g in groups:
            lab = f"{'+'.join(names[i] for i in g)}|{'cold' if cf else 'warm'}"
            cid = len(labels)
            labels.append(lab)
            for t in g:
                cell[(tier == t) & (cold == cf)] = cid
    n23 = [int(((cell == c) & s23).sum()) for c in range(len(labels))]
    detail["n_2023"] = dict(zip(labels, n23, strict=True))
    return {"cell": cell, "labels": labels, "detail": detail, "ok": min(n23) >= MIN_CELL_2023}


# --------------------------------------------------------------------------- router


def trailing(
    crps: np.ndarray, days: np.ndarray, cell: np.ndarray, start_day: int, k: int
) -> tuple[np.ndarray, np.ndarray]:
    """Per cell: raw trailing row count and the exp-weighted trailing mean CRPS of each candidate,
    from rows with game_date STRICTLY before the block start."""
    prev = days < start_day
    c = cell[prev]
    w = 0.5 ** ((start_day - days[prev]) / HALF_LIFE_DAYS)
    cnt = np.bincount(c, minlength=k).astype(float)
    ws = np.bincount(c, weights=w, minlength=k)
    avg = np.zeros((k, crps.shape[1]))
    for j in range(crps.shape[1]):
        num = np.bincount(c, weights=w * crps[prev, j], minlength=k)
        avg[:, j] = np.divide(num, ws, out=np.zeros(k), where=ws > 0)
    return cnt, avg


def route_params(cnt: np.ndarray, avg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """R1 pick (n_cells,) and R2 softmax weights (n_cells, n_cands); C0 when under MIN_TRAIL rows."""
    k, m = avg.shape
    pick = np.zeros(k, dtype=int)
    wts = np.zeros((k, m))
    wts[:, 0] = 1.0
    for c in range(k):
        if cnt[c] >= MIN_TRAIL:
            pick[c] = int(np.argmin(avg[c]))
            z = -avg[c] / TAU
            z -= z.max()
            e = np.exp(z)
            wts[c] = e / e.sum()
    return pick, wts


def block_slices(rows: pl.DataFrame) -> list[tuple[Any, np.ndarray]]:
    dates = rows["game_date"].to_list()
    out = []
    for start, end in month_blocks(rows):
        m = np.array([(start <= d < end) for d in dates])
        if m.any():
            out.append((start, m))
    return out


def epoch_days(rows: pl.DataFrame) -> np.ndarray:
    return rows["game_date"].cast(pl.Int32).to_numpy().astype(np.int64)


def planted_future(
    crps: np.ndarray, rows: pl.DataFrame, cell: np.ndarray, k: int
) -> dict[str, Any]:
    """Shift every row on/after each block start one day later (garbage CRPS): that block's
    weights must not change; and shifting a prior row must change them."""
    days = epoch_days(rows)
    ok_all, inert_ok = True, False
    n_blocks = 0
    for start, _ in block_slices(rows):
        sd = int((start - dt.date(1970, 1, 1)).days)
        cnt0, avg0 = trailing(crps, days, cell, sd, k)
        p0, w0 = route_params(cnt0, avg0)
        d2 = np.where(days >= sd, days + 1, days)
        c2 = np.where((days >= sd)[:, None], 1e6, crps)
        cnt1, avg1 = trailing(c2, d2, cell, sd, k)
        p1, w1 = route_params(cnt1, avg1)
        ok_all &= bool(
            np.array_equal(cnt0, cnt1)
            and np.array_equal(avg0, avg1)
            and np.array_equal(p0, p1)
            and np.array_equal(w0, w1)
        )
        n_blocks += 1
        prior = days < sd
        if prior.any():
            c3 = np.where(prior[:, None], crps + 1.0, crps)
            cnt2, avg2 = trailing(c3, days, cell, sd, k)
            inert_ok |= bool(not np.array_equal(avg0, avg2) or cnt0.sum() == 0)
    return {"unchanged": ok_all, "not_inert": inert_ok, "n_blocks": n_blocks}


# --------------------------------------------------------------------------- walk-forward arms


def build_arm_grids(
    Q: np.ndarray,
    crps: np.ndarray,
    rows: pl.DataFrame,
    cell: np.ndarray,
    k: int,
    a4: int,
) -> dict[str, Any]:
    """A1 (R1 pick), A2 (R2 pool) and A3 (oracle) grids and the A1 pick per row; A4 is a candidate."""
    n = rows.height
    days = epoch_days(rows)
    a1 = np.empty((n, Q.shape[2]), dtype=np.int16)
    a2 = np.empty_like(a1)
    a3 = np.empty_like(a1)
    pick_row = np.zeros(n, dtype=int)
    for start, m in block_slices(rows):
        sd = int((start - dt.date(1970, 1, 1)).days)
        cnt, avg = trailing(crps, days, cell, sd, k)
        pick, wts = route_params(cnt, avg)
        idx = np.where(m)[0]
        pr = pick[cell[idx]]
        pick_row[idx] = pr
        a1[idx] = Q[pr, idx]
        w = wts[cell[idx]]  # (nb, 5)
        pooled = np.einsum("nk,knq->nq", w, Q[:, idx, :].astype(float))
        a2[idx] = to_integer_support(pooled).astype(np.int16)
        # oracle: best candidate per cell on THIS block (leak by construction)
        orc = np.zeros(k, dtype=int)
        for c in range(k):
            sel = cell[idx] == c
            if sel.any():
                orc[c] = int(np.argmin(crps[idx[sel]].mean(axis=0)))
        a3[idx] = Q[orc[cell[idx]], idx]
    return {"A1": a1, "A2": a2, "A3": a3, "A4": Q[a4], "pick_row": pick_row}


def _delta(delta: np.ndarray, gid: np.ndarray) -> dict[str, float]:
    ci = _ci(delta, gid, N_BOOT)
    return {
        "dcrps": ci["point"],
        "ci_lo": ci["lo"],
        "ci_hi": ci["hi"],
        "p": boot_p_value(delta, gid, N_BOOT),
        "mde": (ci["hi"] - ci["lo"]) / 2.0,
    }


def score_stat(
    stat: str, Q: np.ndarray, y: np.ndarray, rows: pl.DataFrame, defs: dict[str, Any]
) -> dict[str, Any]:
    cell, labels = defs["cell"], defs["labels"]
    k = len(labels)
    crps = np.column_stack([crps_from_quantiles(Q[j].astype(float), y) for j in range(Q.shape[0])])
    seas = rows["season"].to_numpy()
    s23 = seas == TEST_SEASONS[0]
    a4 = int(np.argmin(crps[s23].mean(axis=0)))
    grids = build_arm_grids(Q, crps, rows, cell, k, a4)
    sc: dict[str, dict[str, np.ndarray]] = {}
    for a in ("A1", "A2", "A3"):
        sc[a] = row_scores(grids[a], y, stat)
    csc = {c: row_scores(Q[j], y, stat) for j, c in enumerate(CANDS)}
    sc["A0"] = csc["C0"]
    sc["A4"] = csc[CANDS[a4]]
    gid_all = rows["game_id"].to_numpy()
    out_rows: list[dict[str, Any]] = []
    cells_out: list[dict[str, Any]] = []
    switch: list[dict[str, Any]] = []
    attack: list[dict[str, Any]] = []
    slices: list[dict[str, Any]] = []
    best23 = {}
    for c in range(k):
        m = (cell == c) & s23
        best23[c] = int(np.argmin(crps[m].mean(axis=0))) if m.any() else 0
    for season in TEST_SEASONS:
        sm = seas == season
        gid = gid_all[sm]
        rs = {a: {kk: v[sm] for kk, v in s.items()} for a, s in sc.items()}
        for a in ARMS:
            s = rs[a]
            r: dict[str, Any] = {
                "stat": stat,
                "season": season,
                "arm": a,
                "n_rows": int(sm.sum()),
                "n_games": int(len(np.unique(gid))),
                "crps_int": float(s["crps"].mean()),
                "bias": float(s["resid"].mean()),
                "cov80": float(((s["pit"] > 0.10) & (s["pit"] <= 0.90)).mean()),
                "pit_q10": float((s["pit"] <= 0.10).mean()),
                "pit_q20": float((s["pit"] <= 0.20).mean()),
                "tll": float(s["tll"].mean()),
                "dcrps": None,
                "ci_lo": None,
                "ci_hi": None,
                "p": None,
                "p_bh": None,
                "mde": None,
            }
            if a != "A0":
                r.update(_delta(s["crps"] - rs["A0"]["crps"], gid))
            out_rows.append(r)
        ent: dict[str, Any] = {"stat": stat, "season": season, "a4_candidate": CANDS[a4]}
        for a in ("A1", "A2"):
            ent[f"{a}_minus_A4"] = _ci(rs[a]["crps"] - rs["A4"]["crps"], gid, N_BOOT)
            ent[f"{a}_minus_A3"] = _ci(rs[a]["crps"] - rs["A3"]["crps"], gid, N_BOOT)
        for a in ("A1", "A2", "A3", "A4"):
            ent[f"{a}_dcrps"] = float((rs[a]["crps"] - rs["A0"]["crps"]).mean())
        ent["A3_share_captured_A1"] = (
            ent["A1_dcrps"] / ent["A3_dcrps"] if abs(ent["A3_dcrps"]) > 1e-12 else None
        )
        attack.append(ent)
        # candidates (descriptive)
        for c_ in CANDS:
            if c_ == "C0":
                continue
            d = _delta(csc[c_]["crps"][sm] - csc["C0"]["crps"][sm], gid)
            out_rows.append(
                {
                    "stat": stat,
                    "season": season,
                    "arm": c_,
                    "n_rows": int(sm.sum()),
                    "n_games": int(len(np.unique(gid))),
                    "crps_int": float(csc[c_]["crps"][sm].mean()),
                    "bias": float(csc[c_]["resid"][sm].mean()),
                    "cov80": float(
                        ((csc[c_]["pit"][sm] > 0.10) & (csc[c_]["pit"][sm] <= 0.90)).mean()
                    ),
                    "pit_q10": float((csc[c_]["pit"][sm] <= 0.10).mean()),
                    "pit_q20": float((csc[c_]["pit"][sm] <= 0.20).mean()),
                    "tll": float(csc[c_]["tll"][sm].mean()),
                    "p_bh": None,
                    **d,
                }
            )
        cs = cell[sm]
        pick = grids["pick_row"][sm]
        for c in range(k):
            mc = cs == c
            n = int(mc.sum())
            if n == 0:
                continue
            ng = int(len(np.unique(gid[mc])))
            switch.append(
                {
                    "stat": stat,
                    "season": season,
                    "cell": labels[c],
                    "n": n,
                    "switch_rate": float((pick[mc] != 0).mean()),
                }
            )
            bj = best23[c]
            d_best = csc[CANDS[bj]]["crps"][sm][mc] - csc["C0"]["crps"][sm][mc]
            rec: dict[str, Any] = {
                "stat": stat,
                "season": season,
                "cell": labels[c],
                "n": n,
                "n_games": ng,
                "best": CANDS[bj],
                "eligible": bool(n >= CELL_MIN_N and ng >= CELL_MIN_GAMES),
            }
            if bj == 0:
                rec.update({"dcrps": 0.0, "ci_lo": 0.0, "ci_hi": 0.0, "p": 1.0})
            else:
                dd = _delta(d_best, gid[mc])
                rec.update({k_: dd[k_] for k_ in ("dcrps", "ci_lo", "ci_hi", "p")})
            cells_out.append(rec)
            if n >= MIN_SLICE_N:
                for a in ("A1", "A2"):
                    sl = _ci(rs[a]["crps"][mc] - rs["A0"]["crps"][mc], gid[mc], N_BOOT)
                    slices.append(
                        {
                            "stat": stat,
                            "season": season,
                            "arm": a,
                            "cell": labels[c],
                            "n": n,
                            "dcrps": sl["point"],
                            "ci_lo": sl["lo"],
                            "ci_hi": sl["hi"],
                        }
                    )
    return {
        "rows": out_rows,
        "cells": cells_out,
        "switch": switch,
        "attack": attack,
        "slices": slices,
        "a4": CANDS[a4],
    }


def add_bh(rows: list[dict[str, Any]], cells: list[dict[str, Any]]) -> None:
    for season in TEST_SEASONS:
        for arm in ("A1", "A2"):
            tgt = [r for r in rows if r["season"] == season and r["arm"] == arm]
            adj = bh_adjust(np.array([r["p"] for r in tgt]))
            for r, a in zip(tgt, adj, strict=True):
                r["p_bh"] = float(a)
        el = [c for c in cells if c["season"] == season and c["eligible"]]
        if el:
            adj = bh_adjust(np.array([c["p"] for c in el]))
            for c, a in zip(el, adj, strict=True):
                c["p_bh"] = float(a)
        for c in cells:
            if c["season"] == season and not c["eligible"]:
                c["p_bh"] = None
    for c in cells:
        c["claim"] = bool(
            c["season"] == TEST_SEASONS[1]
            and c["eligible"]
            and c["best"] != "C0"
            and c["ci_hi"] < 0
            and c["p_bh"] is not None
            and c["p_bh"] < 0.05
            and c["dcrps"] <= FLOOR
        )


def evaluate_rules(res: dict[str, Any], tests_ok: bool) -> dict[str, Any]:
    tab = {(r["stat"], r["season"], r["arm"]): r for r in res["rows"]}
    atk = {(a["stat"], a["season"]): a for a in res["attack"]}
    out: dict[str, Any] = {}
    for stat in STATS:
        per: dict[str, Any] = {}
        for arm in ("A1", "A2"):
            r1 = {
                str(se): bool(
                    tab[(stat, se, arm)]["dcrps"] <= FLOOR
                    and tab[(stat, se, arm)]["ci_hi"] < 0.0
                    and tab[(stat, se, arm)]["p_bh"] < 0.05
                )
                for se in TEST_SEASONS
            }
            t24 = tab[(stat, 2024, arm)]
            bad = {
                s["cell"]: s["dcrps"]
                for s in res["slices"]
                if s["stat"] == stat
                and s["season"] == 2024
                and s["arm"] == arm
                and s["dcrps"] > SLICE_WORSE
            }
            guards = {
                "abs_bias<=0.5": bool(abs(t24["bias"]) <= MAX_BIAS),
                "cov80_in_[.75,.85]": bool(COV_LO <= t24["cov80"] <= COV_HI),
                "no_cell_worse_than_+0.01": not bad,
            }
            vs4 = atk[(stat, 2024)][f"{arm}_minus_A4"]["point"]
            rule3 = bool(vs4 <= A4_FLOOR and tests_ok)
            p = {
                "rule1_by_season": r1,
                "rule1": all(r1.values()),
                "rule2_guards": guards,
                "rule2": all(guards.values()),
                "cells_worse": bad,
                "minus_A4_2024": vs4,
                "rule3": rule3,
            }
            p["pass"] = bool(p["rule1"] and p["rule2"] and p["rule3"])
            p["one_candidate_is_better"] = bool(p["rule1"] and p["rule2"] and not rule3)
            per[arm] = p
        per["stat_pass"] = bool(per["A1"]["pass"] or per["A2"]["pass"])
        per["any_pass_2023"] = bool(
            per["A1"]["rule1_by_season"]["2023"] or per["A2"]["rule1_by_season"]["2023"]
        )
        out[stat] = per
    out["close_no_stat_passes_2023"] = not any(out[s]["any_pass_2023"] for s in STATS)
    return out


# --------------------------------------------------------------------------- stages


def _chk(name: str, value: Any, ok: Any) -> dict[str, Any]:
    return {"test": name, "ok": bool(ok), "value": value}


def prepare(db_path: str, with_c4: bool) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    t0 = time.time()
    inp = load_study(db_path)
    print(f"inputs loaded {time.time() - t0:.0f}s", flush=True)
    data: dict[str, dict[str, Any]] = {}
    for stat in STATS:
        a0 = load_a0(stat)
        rows = row_frame(stat, a0, inp["feats"])
        cand = build_c1_c3(stat, rows, inp["games"], inp["pgs"])
        d: dict[str, Any] = {"a0": a0, "rows": rows, "cand": cand}
        if with_c4:
            raw = build_c4_raw(stat, inp["feats"], inp["cfg"], a0)
            has = rows["t30_starter"].is_not_null().to_numpy()
            c4 = np.where(has[:, None], raw, a0["qi"]).astype(np.int16)
            d["c4"], d["has_t30"] = c4, has
        data[stat] = d
        print(f"[{time.time() - t0:.0f}s] {stat} candidates ready", flush=True)
    return inp, data


def gate_checks(data: dict[str, dict[str, Any]], with_c4: bool) -> dict[str, Any]:
    tests: list[dict[str, Any]] = []
    cov: dict[str, Any] = {}
    for stat, d in data.items():
        rows = d["rows"]
        for c in ("C1", "C2", "C3"):
            g = d["cand"][c]
            fin = (g.max(axis=1) >= g.min(axis=1)) & (g.min(axis=1) >= 0)
            for season in TEST_SEASONS:
                sm = (rows["season"] == season).to_numpy()
                cov[f"{stat}|{c}|{season}"] = float(fin[sm].mean())
        if with_c4:
            for season in TEST_SEASONS:
                sm = (rows["season"] == season).to_numpy()
                cov[f"{stat}|C4_t30_rows|{season}"] = float(d["has_t30"][sm].mean())
    gated = {k: v for k, v in cov.items() if "C4" not in k}
    tests.append(
        _chk(
            "1 every candidate (C0-C3) has OOF rows for >= 95% of production rows, both seasons",
            {"min": min(gated.values()), "all": cov},
            min(gated.values()) >= COVERAGE_MIN,
        )
    )
    defs_all, ok2, planted = {}, True, {}
    for stat, d in data.items():
        defs = cell_defs(d["rows"])
        defs_all[stat] = defs
        ok2 &= defs["ok"]
        Q = np.stack([d["a0"]["qi"], *[d["cand"][c] for c in ("C1", "C2", "C3")]])
        if with_c4:
            Q = np.concatenate([Q, d["c4"][None]], axis=0)
        else:
            Q = np.concatenate(
                [Q, d["a0"]["qi"][None]], axis=0
            )  # placeholder C4 (= C0) for the gate
        crps = np.column_stack(
            [crps_from_quantiles(Q[j].astype(float), d["a0"]["y"]) for j in range(Q.shape[0])]
        )
        planted[stat] = planted_future(crps, d["rows"], defs["cell"], len(defs["labels"]))
    tests.append(
        _chk(
            "2 all cells >= 300 rows in 2023 (after the declared merge up a tier)",
            {s: defs_all[s]["detail"] for s in defs_all},
            ok2,
        )
    )
    tests.append(
        _chk(
            "3 planted future: shifting a block's rows one day later changes no weight for that block",
            planted,
            all(p["unchanged"] and p["not_inert"] for p in planted.values()),
        )
    )
    repro = check_a0_repro({s: d["a0"] for s, d in data.items()})
    tests.append(
        _chk("4 A0 reproduces production integer CRPS and n to 1e-6", repro["detail"], repro["ok"])
    )
    return {
        "tests": tests,
        "coverage": cov,
        "defs": defs_all,
        "gate_ok": all(t["ok"] for t in tests),
    }


def run_gate(db_path: str = "nba.duckdb") -> dict[str, Any]:
    assert frozen_sha256(DOC) == FROZEN_FULL, "frozen sha mismatch"
    _, data = prepare(db_path, with_c4=False)
    g = gate_checks(data, with_c4=False)
    for t in g["tests"]:
        print(f"{'OK  ' if t['ok'] else 'FAIL'} {t['test']}", flush=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(OUT_DIR / "gate_light.json", {"tests": g["tests"], "coverage": g["coverage"]})
    return g


def run_all(db_path: str = "nba.duckdb") -> dict[str, Any]:
    t0 = time.time()
    sha = frozen_sha256(DOC)
    assert sha == FROZEN_FULL, f"frozen sha mismatch: {sha}"
    inp, data = prepare(db_path, with_c4=True)
    g = gate_checks(data, with_c4=True)
    for t in g["tests"]:
        print(f"{'OK  ' if t['ok'] else 'FAIL'} {t['test']}", flush=True)
    res: dict[str, Any] = {
        "frozen_sha256": sha,
        "git_sha": git_sha(),
        "seeds": SEEDS,
        "n_boot": N_BOOT,
        "tests": g["tests"],
        "coverage": g["coverage"],
        "cell_defs": {s: g["defs"][s]["detail"] for s in g["defs"]},
        "max_season_loaded": int(inp["feats"]["season"].max()),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not g["gate_ok"]:
        res["gate_ok"] = False
        _write_json(OUT_DIR / "results.json", res)
        print("GATE FAILED: stop, no model result", flush=True)
        return res
    tables: dict[str, list[dict[str, Any]]] = {
        k: [] for k in ("rows", "cells", "switch", "attack", "slices")
    }
    a4: dict[str, str] = {}
    fallback: dict[str, Any] = {}
    for stat in STATS:
        d = data[stat]
        Q = np.stack([d["a0"]["qi"], d["cand"]["C1"], d["cand"]["C2"], d["cand"]["C3"], d["c4"]])
        t = score_stat(stat, Q, d["a0"]["y"], d["rows"], g["defs"][stat])
        for k in tables:
            tables[k] += t[k]
        a4[stat] = t["a4"]
        fallback[stat] = {
            str(se): 1.0 - float(d["has_t30"][(d["rows"]["season"] == se).to_numpy()].mean())
            for se in TEST_SEASONS
        }
        print(f"[{time.time() - t0:.0f}s] {stat} scored (A4 = {t['a4']})", flush=True)
    add_bh(tables["rows"], tables["cells"])
    tests_ok = all(t["ok"] for t in g["tests"])
    rules = evaluate_rules(tables, tests_ok)
    res.update(
        {
            "gate_ok": True,
            **tables,
            "rules": rules,
            "a4_candidate": a4,
            "c4_fallback_share": fallback,
            "run_seconds": time.time() - t0,
        }
    )
    _write_json(OUT_DIR / "results.json", res)
    return res


def run_smoke(db_path: str = "nba.duckdb") -> None:
    t0 = time.time()
    inp = load_study(db_path)
    print(f"loaded {time.time() - t0:.0f}s", flush=True)
    stat = "pts"
    a0 = load_a0(stat)
    rows = row_frame(stat, a0, inp["feats"])
    cand = build_c1_c3(stat, rows, inp["games"], inp["pgs"])
    o = collect_arm(
        inp["feats"], stat, inp["cfg"], stat_feature_names(stat, lineups_known=True), (2023,), 2
    )
    n = len(o["y"])
    assert o["meta"].select(KEYS).equals(a0["meta"].select(KEYS).head(n))
    c4 = np.where(
        rows["t30_starter"].head(n).is_not_null().to_numpy()[:, None], o["qi"], a0["qi"][:n]
    )
    # A0 refit on the lineups_known frame (production names): drift proof vs the cache
    o0 = collect_arm(inp["feats"], stat, inp["cfg"], stat_feature_names(stat), (2023,), 2)
    drift = float(np.abs(o0["qi"].astype(int) - a0["qi"][:n].astype(int)).max())
    print(f"A0 refit on the lineups_known frame vs cache: max |dq| = {drift}", flush=True)
    sub = rows.head(n)
    Q = np.stack([a0["qi"][:n], cand["C1"][:n], cand["C2"][:n], cand["C3"][:n], c4])
    crps = np.column_stack([crps_from_quantiles(Q[j].astype(float), a0["y"][:n]) for j in range(5)])
    print("mean CRPS C0..C4 over 2 blocks:", np.round(crps.mean(axis=0), 4), flush=True)
    defs = cell_defs(rows)
    cell = defs["cell"][:n]
    blocks = block_slices(sub)
    start, m = blocks[1]
    sd = int((start - dt.date(1970, 1, 1)).days)
    cnt, avg = trailing(crps, epoch_days(sub), cell, sd, len(defs["labels"]))
    pick, wts = route_params(cnt, avg)
    print(
        f"block {start}: trailing counts {cnt.astype(int).tolist()} pick {pick.tolist()}",
        flush=True,
    )
    print(f"weights row0 {np.round(wts[0], 3).tolist()} [{time.time() - t0:.0f}s]", flush=True)


# --------------------------------------------------------------------------- report


def _f(x: Any, nd: int = 4, sign: bool = False) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def ledger_rows(tab: dict[tuple[str, int, str], dict[str, Any]], pre: str) -> list[str]:
    ln: list[str] = []
    for stat in STATS:
        for season in TEST_SEASONS:
            for arm in ("A1", "A2"):
                t = tab[(stat, season, arm)]
                a0 = tab[(stat, season, "A0")]
                a3 = tab[(stat, season, "A3")]
                a4 = tab[(stat, season, "A4")]
                ln.append(
                    f"| T### | 2026-10-10 | ROUTER_TIME_WEIGHTED (frozen sha256 {pre}) | {stat} {season} {arm} vs A0 all rows, "
                    f"integer-support CRPS, n={t['n_rows']} rows / {t['n_games']} games | {t['dcrps']:+.4f} | "
                    f"{t['ci_lo']:+.4f} | {t['ci_hi']:+.4f} | game | {_f(t['p_bh'], 3)} | MDE {t['mde']:.4f}; "
                    f"bias {a0['bias']:+.3f} -> {t['bias']:+.3f}; cov80 {a0['cov80']:.3f} -> {t['cov80']:.3f}; "
                    f"A3 oracle {a3['dcrps']:+.4f}, A4 best-single {a4['dcrps']:+.4f} | not holdout (<=2024) |"
                )
    return ln


def render_report(res: dict[str, Any]) -> str:
    pre = res["frozen_sha256"][:8]
    rules = res["rules"]
    tab = {(t["stat"], t["season"], t["arm"]): t for t in res["rows"]}
    ln = [
        "# ROUTER_TIME_WEIGHTED results",
        "",
        f"Rule: docs/prereg/ROUTER_TIME_WEIGHTED.md, frozen sha256 {res['frozen_sha256']} (first 8: {pre}), commit 918e03c. "
        f"Run git sha {res['git_sha']}. Seeds {json.dumps(res['seeds'])}; bootstrap {res['n_boot']} game-clustered. Season 2025 never loaded "
        f"(max season in frames {res['max_season_loaded']}).",
        "",
        "## Minimum-data checks",
        "",
        "| test | ok |",
        "|---|---|",
    ]
    for t in res["tests"]:
        ln.append(f"| {t['test']} | {t['ok']} |")
    ln += [
        "",
        "## Verdict per stat",
        "",
        "| stat | A1 pass | A2 pass | stat pass | A1 rule1 (2023/2024) | A2 rule1 (2023/2024) | A1-A4 (2024) | A2-A4 (2024) | one-candidate-better A1/A2 | A4 candidate |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in STATS:
        p = rules[s]
        ln.append(
            f"| {s} | {p['A1']['pass']} | {p['A2']['pass']} | {p['stat_pass']} | "
            f"{p['A1']['rule1_by_season']['2023']}/{p['A1']['rule1_by_season']['2024']} | "
            f"{p['A2']['rule1_by_season']['2023']}/{p['A2']['rule1_by_season']['2024']} | "
            f"{_f(p['A1']['minus_A4_2024'], 4, True)} | {_f(p['A2']['minus_A4_2024'], 4, True)} | "
            f"{p['A1']['one_candidate_is_better']}/{p['A2']['one_candidate_is_better']} | {res['a4_candidate'][s]} |"
        )
    ln += [
        "",
        f"Close (no stat passes rule 1 on 2023 under either router): {rules['close_no_stat_passes_2023']}",
        "",
    ]
    ln += [
        "## Arms table",
        "",
        "| stat | season | arm | n_rows | n_games | crps_int | dcrps | ci_lo | ci_hi | p | p_bh | mde | bias | cov80 | pit_q10 | pit_q20 | tll |",
        "|" + "---|" * 17,
    ]
    for t in res["rows"]:
        ln.append(
            f"| {t['stat']} | {t['season']} | {t['arm']} | {t['n_rows']} | {t['n_games']} | {_f(t['crps_int'])} | "
            f"{_f(t.get('dcrps'), 4, True)} | {_f(t.get('ci_lo'), 4, True)} | {_f(t.get('ci_hi'), 4, True)} | {_f(t.get('p'), 3)} | "
            f"{_f(t.get('p_bh'), 3)} | {_f(t.get('mde'))} | {_f(t['bias'], 3, True)} | {_f(t['cov80'], 3)} | "
            f"{_f(t['pit_q10'], 3)} | {_f(t['pit_q20'], 3)} | {_f(t['tll'])} |"
        )
    ln += [
        "",
        "## Mechanism (dCRPS vs A0; A1/A2 minus A3 and minus A4 with CI)",
        "",
        "| stat | season | A1 | A2 | A3 ceiling | A4 | A1 share of A3 | A1-A4 [lo,hi] | A2-A4 [lo,hi] | A1-A3 [lo,hi] |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for t in res["attack"]:
        ln.append(
            f"| {t['stat']} | {t['season']} | {_f(t['A1_dcrps'], 4, True)} | {_f(t['A2_dcrps'], 4, True)} | {_f(t['A3_dcrps'], 4, True)} | {_f(t['A4_dcrps'], 4, True)} | "
            f"{_f(t['A3_share_captured_A1'], 3)} | "
            f"{_f(t['A1_minus_A4']['point'], 4, True)} [{_f(t['A1_minus_A4']['lo'], 4, True)},{_f(t['A1_minus_A4']['hi'], 4, True)}] | "
            f"{_f(t['A2_minus_A4']['point'], 4, True)} [{_f(t['A2_minus_A4']['lo'], 4, True)},{_f(t['A2_minus_A4']['hi'], 4, True)}] | "
            f"{_f(t['A1_minus_A3']['point'], 4, True)} [{_f(t['A1_minus_A3']['lo'], 4, True)},{_f(t['A1_minus_A3']['hi'], 4, True)}] |"
        )
    ln += [
        "",
        "## Per-cell table (best chosen on 2023; descriptive unless the cell rule holds)",
        "",
        "| stat | season | cell | n | n_games | best | dcrps_best_vs_c0 | ci_lo | ci_hi | p_bh | eligible | claim |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in res["cells"]:
        ln.append(
            f"| {c['stat']} | {c['season']} | {c['cell']} | {c['n']} | {c['n_games']} | {c['best']} | {_f(c['dcrps'], 4, True)} | "
            f"{_f(c['ci_lo'], 4, True)} | {_f(c['ci_hi'], 4, True)} | {_f(c.get('p_bh'), 3)} | {c['eligible']} | {c['claim']} |"
        )
    ln += [
        "",
        "## Switch rate (R1 leaves C0)",
        "",
        "| stat | season | cell | n | switch_rate |",
        "|---|---|---|---|---|",
    ]
    for s in res["switch"]:
        ln.append(
            f"| {s['stat']} | {s['season']} | {s['cell']} | {s['n']} | {s['switch_rate']:.3f} |"
        )
    ln += ["", "## C4 fallback share (rows without a T-30 row fall back to C0)", ""]
    for s, v in res["c4_fallback_share"].items():
        ln.append(f"- {s}: 2023 {v['2023']:.4f}, 2024 {v['2024']:.4f}")
    ln += [
        "",
        "## Cell merges (check 2)",
        "",
        "```",
        json.dumps(res["cell_defs"], indent=1, default=_jd),
        "```",
    ]
    ln += ["", "## Unstated details (chosen before scoring)", ""] + [f"- {u}" for u in UNSTATED]
    ln += ["", "## Proposed ledger rows (draft, unnumbered; maintainer appends)", ""]
    ln += ledger_rows(tab, pre)
    return "\n".join(ln) + "\n"


def write_outputs(res: dict[str, Any]) -> None:
    REPORT_MD.write_text(render_report(res))
    tab = {(t["stat"], t["season"], t["arm"]): t for t in res["rows"]}
    pre = res["frozen_sha256"][:8]
    body = [
        "# ROUTER_TIME_WEIGHTED proposed ledger rows (draft; maintainer numbers and appends to docs/TEST_LEDGER.md)",
        "",
        "Columns: id | date | test | result | effect | CI lo | CI hi | cluster | p(BH) | notes | holdout status.",
        "",
        *ledger_rows(tab, pre),
        "",
        "Verdict per stat and the per-cell claims: see reports/prereg_router.md.",
    ]
    (OUT_DIR / "ledger_rows.md").write_text("\n".join(body) + "\n")
    body_json = json.dumps(res, indent=2, default=_jd)
    (OUT_DIR / "results.json").write_text(body_json)
    (OUT_DIR / "results.sha256").write_text(hashlib.sha256(body_json.encode()).hexdigest() + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ROUTER_TIME_WEIGHTED (frozen rule)")
    ap.add_argument("stage", choices=("gate", "smoke", "run"))
    ap.add_argument("--db-path", default="nba.duckdb")
    a = ap.parse_args(argv)
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    if a.stage == "smoke":
        run_smoke(a.db_path)
    elif a.stage == "gate":
        g = run_gate(a.db_path)
        return 0 if g["gate_ok"] else 2
    else:
        with heavy_lock():
            res = run_all(a.db_path)
        if not res.get("gate_ok"):
            return 2
        write_outputs(res)
        print(json.dumps(res["rules"], indent=1, default=_jd))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
