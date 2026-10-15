# ruff: noqa: E501
"""ALT_RUNG_CAL minimum-data gate (rule: docs/prereg/ALT_RUNG_CAL.md; DRAFT until frozen).

    .venv/bin/python -W ignore -m research.eval.alt_rung_cal_gate \
        --data-root /Users/devin/Downloads/nba-prediction

Counts, widths and label-free support checks only. No outcome column (``y``, ``over``, ``minutes``)
is read, no CAL map is fit, nothing is scored, no price enters a statistic (CLAUDE.md
non-negotiable 8). It sizes the walk-forward fits and the cells of the ALT_RUNG_CAL rule on the
DraftKings (and FanDuel) alternate rungs of the primary arm of ALT_LADDER_HIST, and it measures how
far the frozen LINE_CALIBRATION map would have to extrapolate on them.

Inputs (read-only; seasons 2023 and 2024 only, asserted before any read):
* ``data/odds/odds_history.duckdb`` table ``odds_history``, snapshot ``t60``: the four player
  ``*_alternate`` markets and the four main markets, ``season in (2023, 2024)`` in every query;
* ``reports/odds_history/model_rows_{2023,2024}.parquet``: ``p_ge``, ``c``, ``s``, ``starter10`` (no ``y``);
* ``data/ops/model_clv_pairs/pairs_{2023,2024}.parquet``: DraftKings main-line legs, without the
  ``over`` and price columns;
* ``nba.duckdb`` ``player_availability`` joined to ``games`` (report stamps; seasons 2023-24);
* ``reports/prereg_alt_ladder_hist/gate.json``: the published primary-arm counts (reproduction check).

The only outcome-derived numbers in the file are the published T491 reliability gaps
(``PUBLISHED_GAP``), copied from docs/prereg/ALT_LADDER_HIST.md; they define an ORACLE recalibration
used to size the effect and the interval (exposure disclosed in the rule).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from research.eval import line_calibration_eval as lc
from research.eval.alt_ladder_hist_gate import (
    LEAD_CAP_MIN,
    alt_over_rungs,
    load_alt_quotes,
    load_main_lines,
    load_report_stamps,
    window_games,
)
from research.eval.alt_line_70_gate import (
    EPS,
    SEASONS,
    STATS,
    cluster_halfwidth,
    sha256_file,
)

MAX_SEASON = 2024
BOOKS = ("draftkings", "fanduel")
PRIMARY = "draftkings"
BIN_EDGES: tuple[tuple[str, float, float], ...] = (
    ("55", 0.50, 0.60),
    ("65", 0.60, 0.70),
    ("75", 0.70, 0.80),
    ("85", 0.80, 0.90),
    ("95", 0.90, 1.0001),
)
MIN_FIT = lc.MIN_FIT  # 2,000 fit rows, the LINE_CALIBRATION identity rule
HALF_LIFE = lc.HALF_LIFE_DAYS
CELL_MIN_GAMES, CELL_MIN_RUNGS, CELL_MAX_HW = 300, 3_000, 0.03
Q_LO, Q_HI = 0.01, 0.99  # support band of the fit legs for the extrapolation share
#: T491 published pooled reliability gaps (hit rate minus mean model P), DraftKings primary arm,
#: both seasons pooled (docs/prereg/ALT_LADDER_HIST.md, Part 1S table), by (A0 bin, stat); the two
#: top sub-bins are the pooled break-even table (bin 0.90-0.95 and 0.95-1.00).
PUBLISHED_GAP: dict[str, dict[str, float]] = {
    "55": {"pts": 0.0182, "reb": 0.0304, "ast": 0.0381, "fg3m": 0.0470},
    "65": {"pts": 0.0123, "reb": 0.0261, "ast": 0.0235, "fg3m": 0.0224},
    "75": {"pts": 0.0003, "reb": 0.0079, "ast": -0.0054, "fg3m": -0.0039},
    "85": {"pts": -0.0321, "reb": -0.0216, "ast": -0.0401, "fg3m": -0.0496},
}
PUBLISHED_TOP: tuple[tuple[float, float, float], ...] = (
    (0.90, 0.95, 0.869 - 0.925),
    (0.95, 1.0001, 0.895 - 0.968),
)
INPUT_FILES = (
    "reports/odds_history/model_rows_2023.parquet",
    "reports/odds_history/model_rows_2024.parquet",
    "data/ops/model_clv_pairs/pairs_2023.parquet",
    "data/ops/model_clv_pairs/pairs_2024.parquet",
    "data/odds/odds_history.duckdb",
    "reports/prereg_alt_ladder_hist/gate.json",
)
PAIRS_COLS = [
    "game_id", "player_id", "stat", "book", "snapshot_kind", "snapshot_ts", "line", "p_model",
    "starter10", "tipoff_utc", "season", "game_date",
]  # fmt: skip
MODEL_COLS = ["game_id", "player_id", "stat", "game_date", "season", "starter10", "c", "s", "p_ge"]
FORBIDDEN = {"y", "over", "minutes", "price_over", "price_under", "p_over_mult"}


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------------------------ helpers


def bin_label(p: np.ndarray) -> np.ndarray:
    out = np.full(p.shape, "", dtype="<U2")
    for label, lo, hi in BIN_EDGES:
        out[(p >= lo - EPS) & (p < hi - EPS)] = label
    return out


def logit(p: np.ndarray) -> np.ndarray:
    return lc.logit(np.clip(p, lc.EPS, 1 - lc.EPS))


def oracle_gap(pm: np.ndarray, stat: np.ndarray) -> np.ndarray:
    """Published pooled gap of each rung's (A0 bin, stat) cell; the two top sub-bins by P."""
    gap = np.zeros(pm.shape)
    lab = bin_label(pm)
    for label, per_stat in PUBLISHED_GAP.items():
        for s, g in per_stat.items():
            gap[(lab == label) & (stat == s)] = g
    for lo, hi, g in PUBLISHED_TOP:
        gap[(pm >= lo - EPS) & (pm < hi - EPS)] = g
    return gap


def oracle_effect(pm: np.ndarray, stat: np.ndarray, game: np.ndarray) -> dict[str, Any]:
    """If the T491 published gaps were the truth and a map recovered them exactly, the expected
    per-rung log-loss gain over A0 is KL(T || A0), and the paired difference d has a known sd.
    Returns the mean KL, the conservative (all rungs of a game perfectly correlated) and the
    independent 95% half-width of the mean difference, and the KL by A0 bin. Expectations only: no
    outcome is read and nothing is sampled."""
    a = np.clip(pm, lc.EPS, 1 - lc.EPS)
    t = np.clip(a + oracle_gap(pm, stat), 1e-3, 1 - 1e-3)
    l1 = np.log(t / a)
    l0 = np.log((1 - t) / (1 - a))
    kl = t * l1 + (1 - t) * l0  # E[loss(A0) - loss(T)] under T
    sd = np.sqrt(t * (1 - t)) * np.abs(l1 - l0)
    _, gi = np.unique(game, return_inverse=True)
    n = float(pm.size)
    per_game_sd_sum = np.bincount(gi, weights=sd)
    lab = bin_label(pm)
    return {
        "n": int(pm.size),
        "mean_kl": round(float(kl.mean()), 5),
        "hw_conservative": round(float(1.96 * np.sqrt((per_game_sd_sum**2).sum()) / n), 5),
        "hw_independent": round(float(1.96 * np.sqrt((sd**2).sum()) / n), 5),
        "kl_by_bin": {
            b: round(float(kl[lab == b].mean()), 5) for b, _, _ in BIN_EDGES if (lab == b).any()
        },
        "share_of_kl_by_bin": {
            b: round(float(kl[lab == b].sum() / kl.sum()), 3)
            for b, _, _ in BIN_EDGES
            if (lab == b).any()
        },
    }


def synthetic_projection(
    pm: np.ndarray,
    stat: np.ndarray,
    game: np.ndarray,
    unit: np.ndarray,
    seed: int = 0,
    n_boot: int = 300,
) -> dict[str, Any]:
    """Projected precision of the primary contrast CAL - A0 at the real rows and games. Truth = the
    oracle (published gaps); labels are SYNTHETIC: one uniform draw per (game, player, stat) unit and
    ``hit_k = U < T_k`` (a coupling that keeps a ladder's rungs nested, as one realised stat does).
    d_i = loss(A0) - loss(T) on those synthetic labels; the half-width is from a game bootstrap of the
    mean of d (``n_boot`` multinomial resamples, ``seed``). No real label is read and no map is fit."""
    a = np.clip(pm, lc.EPS, 1 - lc.EPS)
    t = np.clip(a + oracle_gap(pm, stat), 1e-3, 1 - 1e-3)
    rng = np.random.default_rng(seed)
    uu, ui = np.unique(unit, return_inverse=True)
    u = rng.random(uu.size)[ui]
    h = u < t
    d = np.where(h, np.log(t / a), np.log((1 - t) / (1 - a)))
    gg, gi = np.unique(game, return_inverse=True)
    sg = np.bincount(gi, weights=d)
    ng = np.bincount(gi).astype(float)
    w = rng.multinomial(gg.size, np.full(gg.size, 1.0 / gg.size), size=n_boot).astype(float)
    est = (w @ sg) / (w @ ng)
    lo, hi = np.quantile(est, [0.025, 0.975])
    ug_sd = np.sqrt(t * (1 - t)) * np.abs(np.log(t / a) - np.log((1 - t) / (1 - a)))
    n = float(pm.size)
    unit_sum = np.bincount(ui, weights=ug_sd)
    return {
        "synthetic_mean_gain": round(float(d.mean()), 5),
        "hw_game_bootstrap": round(float((hi - lo) / 2), 5),
        "hw_unit_perfect_corr": round(float(1.96 * np.sqrt((unit_sum**2).sum()) / n), 5),
        "units": int(uu.size),
        "games": int(gg.size),
    }


def conditioning(X: np.ndarray) -> dict[str, Any]:
    """Label-free identifiability of a design matrix: condition number of the standardized
    non-intercept columns and the largest pairwise |correlation|."""
    Z = X[:, 1:]
    sd = Z.std(axis=0)
    keep = sd > 0
    if int(keep.sum()) < 2:
        return {
            "cond_standardized": None,
            "max_abs_corr": None,
            "constant_columns": int((~keep).sum()),
        }
    Zs = (Z[:, keep] - Z[:, keep].mean(axis=0)) / sd[keep]
    c = np.asarray(np.corrcoef(Zs.T))
    off = np.abs(c - np.eye(c.shape[0]))
    sv = np.linalg.svd(Zs, compute_uv=False)
    return {
        "cond_standardized": round(float(sv[0] / sv[-1]), 1),
        "max_abs_corr": round(float(off.max()), 3),
        "constant_columns": int((~keep).sum()),
    }


def cell(pm: np.ndarray, game: np.ndarray) -> dict[str, Any]:
    if pm.size == 0:
        return {"rungs": 0, "games": 0}
    _, cnt = np.unique(game, return_counts=True)
    p = float(pm.mean())
    return {
        "rungs": int(pm.size),
        "games": int(cnt.size),
        "mean_p": round(p, 4),
        "hw_cons": round(cluster_halfwidth(p, cnt.astype(float)), 4),
        "hw_indep": round(float(1.96 * np.sqrt(p * (1 - p) / pm.size)), 4),
    }


def cell_ok(c: dict[str, Any]) -> bool:
    return bool(
        c.get("rungs", 0) >= CELL_MIN_RUNGS
        and c.get("games", 0) >= CELL_MIN_GAMES
        and c.get("hw_cons", 9.0) <= CELL_MAX_HW
    )


# ------------------------------------------------------------------------------------ loading


def read_pairs_free(path: Path, season: int) -> pl.DataFrame:
    assert season <= MAX_SEASON
    d = pl.read_parquet(path, columns=PAIRS_COLS)
    assert not (set(d.columns) & FORBIDDEN)
    assert d["season"].n_unique() == 1 and int(d["season"][0]) == season
    return d.filter(pl.col("snapshot_kind") == "t60")


def read_model(path: Path, season: int) -> pl.DataFrame:
    assert season <= MAX_SEASON
    g = pl.read_parquet(path, columns=MODEL_COLS)
    assert not (set(g.columns) & FORBIDDEN)
    assert g["season"].n_unique() == 1 and int(g["season"][0]) == season
    return g.with_columns(
        pl.col("player_id").cast(pl.Int64), pl.col("game_date").dt.strftime("%Y-%m").alias("mo")
    )


def with_grid(frame: pl.DataFrame, grid: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Attach the continuous-grid median and robust spread (LINE_CALIBRATION ``attach_grid``) to a
    frame with columns game_id, player_id, stat, book, line, p_model. Row order is restored."""
    f = frame.with_row_index("rid")
    out, diag = lc.attach_grid(f, grid)
    return out.sort("rid"), diag


def design_xz(d: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    pm = d["p_model"].to_numpy()
    x = logit(pm)
    z = np.clip(
        (d["line"].to_numpy() - d["med"].to_numpy())
        / np.maximum(d["spr"].to_numpy(), lc.SPREAD_MIN),
        -lc.Z_CLIP,
        lc.Z_CLIP,
    )
    return x, z


def month_start(dates: np.ndarray) -> np.ndarray:
    return dates.astype("datetime64[M]").astype("datetime64[D]")


# ------------------------------------------------------------------------------------ stage


def stage_block(
    stages: tuple[int, ...],
    rungs: dict[str, np.ndarray],
    legs: dict[str, np.ndarray],
) -> dict[str, Any]:
    """Walk-forward bookkeeping for one stage season on one book's primary-arm rungs with P >= 0.5.

    ``rungs``: arrays pm, stat, game, date, x, z, has_main, bench for ALL seasons of the book;
    ``legs``: the same book's main-line legs (arrays stat, date, x, z). Stage ``stage`` rows are scored;
    fit rows are strictly earlier dates. Returns per-block fit counts, which rows are common to all
    arms (every arm's block has >= MIN_FIT fit rows AND the unit has a main line), cells and the
    extrapolation share of the frozen main-line map."""
    sel = np.isin(rungs["season"], stages)
    blocks: list[dict[str, Any]] = []
    common = np.zeros(rungs["pm"].size, dtype=bool)
    out_x = np.zeros(rungs["pm"].size, dtype=bool)
    out_z = np.zeros(rungs["pm"].size, dtype=bool)
    mstart = month_start(rungs["date"])
    for stat in STATS:
        cell_r = rungs["stat"] == stat
        cell_l = legs["stat"] == stat
        for b0 in np.unique(mstart[sel & cell_r]):
            tgt = sel & cell_r & (mstart == b0)
            fit_l = cell_l & (legs["date"] < b0)
            fit_r = cell_r & (rungs["date"] < b0) & rungs["has_main"]
            n_l, n_r = int(fit_l.sum()), int(fit_r.sum())
            age = (b0 - rungs["date"][fit_r]).astype("timedelta64[D]").astype(float)
            w = 0.5 ** (age / HALF_LIFE)
            n_eff = float(w.sum() ** 2 / (w**2).sum()) if n_r else 0.0
            ok = n_l >= MIN_FIT and n_r >= MIN_FIT
            rec: dict[str, Any] = {
                "stat": stat,
                "block": str(b0)[:7],
                "n_score": int(tgt.sum()),
                "n_score_with_main": int((tgt & rungs["has_main"]).sum()),
                "n_fit_main_legs": n_l,
                "n_fit_rungs": n_r,
                "n_eff_rungs": round(n_eff, 1),
                "all_arms_fit": ok,
            }
            if ok:
                rows = tgt & rungs["has_main"]
                common |= rows
                if n_l:
                    xlo, xhi = np.quantile(legs["x"][fit_l], [Q_LO, Q_HI])
                    zlo, zhi = np.quantile(legs["z"][fit_l], [Q_LO, Q_HI])
                    out_x |= rows & ((rungs["x"] < xlo) | (rungs["x"] > xhi))
                    out_z |= rows & ((rungs["z"] < zlo) | (rungs["z"] > zhi))
                    rec["main_fit_x_band"] = [round(float(xlo), 3), round(float(xhi), 3)]
                    rec["main_fit_z_band"] = [round(float(zlo), 3), round(float(zhi), 3)]
            blocks.append(rec)
    pm = rungs["pm"]
    lab = bin_label(pm)
    res: dict[str, Any] = {
        "blocks": blocks,
        "rungs_in_stage": int(sel.sum()),
        "rungs_common_to_all_arms": int(common.sum()),
        "rungs_dropped_no_main_line": int((sel & ~rungs["has_main"]).sum()),
        "rungs_dropped_identity_block": int((sel & rungs["has_main"] & ~common).sum()),
        "identity_blocks": int(sum(1 for b in blocks if not b["all_arms_fit"])),
        "blocks_total": len(blocks),
        "extrapolation_share_x_outside_p01_p99": {
            b: round(float(out_x[common & (lab == b)].mean()), 3)
            for b, _, _ in BIN_EDGES
            if (common & (lab == b)).any()
        },
        "extrapolation_share_z_outside_p01_p99": {
            b: round(float(out_z[common & (lab == b)].mean()), 3)
            for b, _, _ in BIN_EDGES
            if (common & (lab == b)).any()
        },
        "extrapolation_share_x_all": round(float(out_x[common].mean()), 3)
        if common.any()
        else None,
    }
    c = common
    res["cells_pooled_over_stats"] = {
        b: cell(pm[c & (lab == b)], rungs["game"][c & (lab == b)]) for b, _, _ in BIN_EDGES
    }
    res["cells_by_stat"] = {
        b: {
            s: cell(
                pm[c & (lab == b) & (rungs["stat"] == s)],
                rungs["game"][c & (lab == b) & (rungs["stat"] == s)],
            )
            for s in STATS
        }
        for b, _, _ in BIN_EDGES
    }
    res["cells_pooled_ok"] = {b: cell_ok(v) for b, v in res["cells_pooled_over_stats"].items()}
    res["cells_by_stat_ok"] = {
        b: {s: cell_ok(v) for s, v in per.items()} for b, per in res["cells_by_stat"].items()
    }
    res["all_scored"] = cell(pm[c], rungs["game"][c])
    res["by_stat_scored"] = {
        s: cell(pm[c & (rungs["stat"] == s)], rungs["game"][c & (rungs["stat"] == s)])
        for s in STATS
    }
    res["by_role_scored"] = {
        "starter": cell(pm[c & (rungs["bench"] == 0)], rungs["game"][c & (rungs["bench"] == 0)]),
        "bench": cell(pm[c & (rungs["bench"] == 1)], rungs["game"][c & (rungs["bench"] == 1)]),
    }
    unit = np.char.add(
        np.char.add(rungs["game"][c], "|"),
        np.char.add(rungs["player"][c].astype(str), rungs["stat"][c]),
    )
    res["p1_oracle_projection"] = (
        {
            **oracle_effect(pm[c], rungs["stat"][c], rungs["game"][c]),
            **synthetic_projection(pm[c], rungs["stat"][c], rungs["game"][c], unit),
        }
        if c.any()
        else {}
    )
    # CAL-rung design identifiability on the common rows (terms: 1, x, zm, r, r x, bench, bench x)
    if c.any():
        zm = rungs["z"][c] - rungs["r"][c]
        xx = rungs["x"][c]
        X = np.column_stack(
            [
                np.ones(xx.size),
                xx,
                zm,
                rungs["r"][c],
                rungs["r"][c] * xx,
                rungs["bench"][c],
                rungs["bench"][c] * xx,
            ]
        )
        res["cal_rung_design_conditioning"] = conditioning(X)
        X5 = np.column_stack(
            [np.ones(xx.size), xx, rungs["z"][c], rungs["bench"][c], rungs["bench"][c] * xx]
        )
        res["cal_main_design_on_rungs_conditioning"] = conditioning(X5)
    _, ucnt = np.unique(unit, return_counts=True)
    res["ladder_structure"] = {
        "units": int(ucnt.size),
        "rungs_per_unit_mean": round(float(ucnt.mean()), 2) if ucnt.size else None,
        "rungs_per_unit_max": int(ucnt.max()) if ucnt.size else None,
        "rungs_per_game_mean": round(float(c.sum() / max(np.unique(rungs["game"][c]).size, 1)), 1),
    }
    res["common_mask_rungs_by_month"] = {
        str(m)[:7]: int((common & (mstart == m)).sum()) for m in np.unique(mstart[common])
    }
    return res


# ------------------------------------------------------------------------------------ run


def book_rungs(
    t: pl.DataFrame, book: str, grid: pl.DataFrame, model: pl.DataFrame
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Primary-arm rungs of one book with A0 P >= 0.5, with the continuous-grid median/spread, bench and
    the book's main line. Window games, lead > 66 and the ladder-integrity filters are already in
    ``t`` as columns ``in_window``, ``lead_min``, ``ladder_off``."""
    prim = t.filter(
        (pl.col("book") == book)
        & ~pl.col("in_window")
        & (pl.col("lead_min") <= LEAD_CAP_MIN)
        & ~pl.col("ladder_off")
    )
    s = prim.filter(pl.col("pm") >= 0.5 - EPS)
    diag_in: dict[str, Any] = {
        "primary_arm_rungs": prim.height,
        "primary_arm_rungs_p_ge_half": s.height,
    }
    frame = s.select(
        "game_id",
        "player_id",
        "stat",
        "book",
        pl.col("point").alias("line"),
        pl.col("pm").alias("p_model"),
        "game_date",
        "lead_min",
        "main_point",
    ).with_columns(pl.col("player_id").cast(pl.Int64))
    g, diag = with_grid(frame, grid)
    st = model.select("game_id", "player_id", "stat", "starter10").unique(
        ["game_id", "player_id", "stat"]
    )
    g = g.join(
        st.with_columns(pl.col("player_id").cast(pl.Int64)),
        on=["game_id", "player_id", "stat"],
        how="left",
    )
    diag_in["grid"] = diag
    diag_in["starter10_missing"] = int(g["starter10"].null_count())
    return g, diag_in


def to_rung_arrays(g: pl.DataFrame, season: int) -> dict[str, np.ndarray]:
    x, z = design_xz(g)
    has_main = g["main_point"].is_not_null().to_numpy()
    spr = np.maximum(g["spr"].to_numpy(), lc.SPREAD_MIN)
    r = np.where(
        has_main, (g["line"].to_numpy() - g["main_point"].fill_null(0.0).to_numpy()) / spr, 0.0
    )
    return {
        "season": np.full(g.height, season, dtype=np.int64),
        "pm": g["p_model"].to_numpy().astype(float),
        "stat": g["stat"].to_numpy().astype(str),
        "game": g["game_id"].to_numpy().astype(str),
        "player": g["player_id"].to_numpy().astype(np.int64),
        "date": g["game_date"].to_numpy().astype("datetime64[D]"),
        "x": x,
        "z": z,
        "r": r,
        "has_main": has_main,
        "bench": (g["starter10"].fill_null(1.0).to_numpy() < 0.5).astype(np.int64),
        "lead": g["lead_min"].to_numpy().astype(float),
    }


def concat_arrays(parts: list[dict[str, np.ndarray]]) -> dict[str, np.ndarray]:
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


def run_gate(data_root: Path, hash_inputs: bool = True) -> dict[str, Any]:
    assert max(SEASONS) <= MAX_SEASON  # before any read
    t0 = time.time()
    odds_db = data_root / "data/odds/odds_history.duckdb"
    nba_db = data_root / "nba.duckdb"
    result: dict[str, Any] = {
        "rule": "docs/prereg/ALT_RUNG_CAL.md (DRAFT)",
        "date_cdt": time.strftime("%a %b %d %H:%M:%S %Z %Y"),
        "data_root": str(data_root),
        "seasons": list(SEASONS),
        "min_fit": MIN_FIT,
        "half_life_days": HALF_LIFE,
        "cell_thresholds": {
            "games": CELL_MIN_GAMES,
            "rungs": CELL_MIN_RUNGS,
            "hw_cons": CELL_MAX_HW,
        },
        "published_gap_oracle": {"cells": PUBLISHED_GAP, "top": PUBLISHED_TOP},
    }
    if hash_inputs:
        result["input_sha256"] = {f: sha256_file(data_root / f) for f in INPUT_FILES}
    pairs = {
        s: read_pairs_free(data_root / f"data/ops/model_clv_pairs/pairs_{s}.parquet", s)
        for s in SEASONS
    }
    tips = pl.concat([p.select("game_id", "tipoff_utc") for p in pairs.values()]).unique("game_id")
    stamps = load_report_stamps(nba_db)
    ref = json.loads((data_root / "reports/prereg_alt_ladder_hist/gate.json").read_text())

    per_book: dict[str, dict[int, dict[str, np.ndarray]]] = {b: {} for b in BOOKS}
    legs_by_season: dict[int, dict[str, np.ndarray]] = {}
    repro: dict[str, Any] = {}
    meta_all: dict[str, Any] = {}
    for season in SEASONS:
        _log(f"season {season}: load")
        model = read_model(data_root / f"reports/odds_history/model_rows_{season}.parquet", season)
        raw = load_alt_quotes(odds_db, season)
        raw = raw.filter(pl.col("book").is_in(list(BOOKS)))
        main = load_main_lines(odds_db, season)
        main = main.filter(pl.col("book").is_in(list(BOOKS)))
        model_alt = model.drop("starter10", "c", "s", "mo", "season").with_columns(
            pl.col("player_id").cast(raw.schema["player_id"])
        )
        t, meta = alt_over_rungs(raw, tips, model_alt, main)
        snap = (
            raw.group_by("game_id")
            .agg(pl.col("snapshot_ts").max().alias("snapshot_ts"))
            .join(tips, on="game_id", how="inner")
        )
        wg = window_games(stamps, snap.select("game_id", "tipoff_utc", "snapshot_ts"))
        w_set = set(wg["game_id"].to_list())
        t = t.with_columns(pl.col("game_id").is_in(list(w_set)).alias("in_window")).join(
            main.select("game_id", "player_id", "stat", "book", "main_point"),
            on=["game_id", "player_id", "stat", "book"],
            how="left",
        )
        meta["games_with_report_stamp_in_window"] = len(w_set)
        meta_all[str(season)] = meta
        _log(f"season {season}: rungs {t.height}")
        for book in BOOKS:
            g, dg = book_rungs(t, book, model, model)
            meta_all[str(season)][f"{book}_set"] = dg
            per_book[book][season] = to_rung_arrays(g, season)
            ref_b = ref["per_season"][str(season)]["books"][book]["primary"]
            mine = {
                b: int((bin_label(per_book[book][season]["pm"]) == b).sum())
                for b, _, _ in BIN_EDGES
            }
            published = {b: int(ref_b["bins"][b]["rungs"]) for b in ("55", "65", "75", "85")}
            published["95"] = int(ref_b["ge70"]["rungs"]) - published["75"] - published["85"]
            repro[f"{book}_{season}"] = {
                "mine": mine,
                "published_alt_ladder_hist_gate": published,
                "equal": mine == published,
            }
        # DraftKings main-line legs with the continuous grid (the fit set of the frozen CAL map)
        leg = (
            pairs[season]
            .filter(pl.col("book") == PRIMARY)
            .with_columns(pl.col("player_id").cast(pl.Int64))
        )
        lg, ldiag = with_grid(
            leg.select("game_id", "player_id", "stat", "book", "line", "p_model", "game_date"),
            model,
        )
        x, z = design_xz(lg)
        legs_by_season[season] = {
            "stat": lg["stat"].to_numpy().astype(str),
            "date": lg["game_date"].to_numpy().astype("datetime64[D]"),
            "x": x,
            "z": z,
            "game": lg["game_id"].to_numpy().astype(str),
        }
        meta_all[str(season)]["draftkings_main_legs"] = {
            "legs": leg.height,
            "grid_diag": ldiag,
            "units_with_odds_history_main_line_equal_to_leg_line": None,
        }
        # agreement between the pairs main line and the odds_history main line (price-nearest-0.5)
        a = leg.select("game_id", "player_id", "stat", "line").join(
            main.filter(pl.col("book") == PRIMARY).select(
                "game_id", pl.col("player_id").cast(pl.Int64), "stat", "main_point"
            ),
            on=["game_id", "player_id", "stat"],
            how="left",
        )
        meta_all[str(season)]["draftkings_main_legs"][
            "units_with_odds_history_main_line_equal_to_leg_line"
        ] = {
            "legs": a.height,
            "matched": int(a["main_point"].is_not_null().sum()),
            "equal": int(((a["line"] - a["main_point"]).abs() < 1e-9).sum()),
        }
        _log(f"season {season}: done")
    result["reproduction_vs_alt_ladder_hist_gate"] = repro
    result["meta"] = meta_all

    legs = {
        k: np.concatenate([legs_by_season[s][k] for s in SEASONS])
        for k in legs_by_season[SEASONS[0]]
    }
    stages: dict[str, Any] = {}
    for book in BOOKS:
        arr = concat_arrays([per_book[book][s] for s in SEASONS])
        stages[book] = {str(stage): stage_block((stage,), arr, legs) for stage in SEASONS}
        stages[book]["pooled_2023_2024"] = stage_block(tuple(SEASONS), arr, legs)
        stages[book]["lead_min_min"] = round(float(arr["lead"].min()), 2)
        stages[book]["rungs_lead_below_60"] = int((arr["lead"] < 60.0).sum())
        stages[book]["rungs_total_p_ge_half"] = int(arr["pm"].size)
        stages[book]["rungs_with_main_line_share"] = round(float(arr["has_main"].mean()), 4)
        stages[book]["rungs_with_main_line_share_by_season"] = {
            str(s): round(float(per_book[book][s]["has_main"].mean()), 4) for s in SEASONS
        }
    result["stages"] = stages
    lx = legs["x"]
    result["dk_main_legs_x_z_quantiles_all_fit_legs"] = {
        "n": int(lx.size),
        "x_p01_p05_p50_p95_p99": [
            round(float(v), 3) for v in np.quantile(lx, [0.01, 0.05, 0.5, 0.95, 0.99])
        ],
        "z_p01_p05_p50_p95_p99": [
            round(float(v), 3) for v in np.quantile(legs["z"], [0.01, 0.05, 0.5, 0.95, 0.99])
        ],
        "max_p_model_equivalent": round(float(1 / (1 + np.exp(-np.quantile(lx, 0.99)))), 3),
    }
    result["checks"] = checks(result)
    result["cost"] = {"wall_seconds": round(time.time() - t0, 1)}
    return result


FLOOR = -0.002  # proposed P1 floor (see the rule); the gate asks whether it is detectable
HW_BAR = abs(FLOOR) / 2  # projected 95% half-width of P1 must be at most half the floor
DESIGN_EFFECT = (
    1.51  # LINE_CALIBRATION T493 measured game design effect (legs); applied to the projection
)


def chk(name: str, measured: Any, bar: str, ok: bool) -> dict[str, Any]:
    return {"check": name, "measured": measured, "bar": bar, "pass": bool(ok)}


def checks(r: dict[str, Any]) -> list[dict[str, Any]]:
    """The gate table: every bar was fixed before the numbers were read."""
    dk = r["stages"][PRIMARY]
    pooled = dk["pooled_2023_2024"]
    out: list[dict[str, Any]] = []
    rep = r["reproduction_vs_alt_ladder_hist_gate"]
    out.append(
        chk(
            "primary-arm rung counts by A0 bin equal ALT_LADDER_HIST gate.json (book x season)",
            {k: v["equal"] for k, v in rep.items()},
            "4 of 4 equal",
            all(v["equal"] for v in rep.values()),
        )
    )
    out.append(
        chk(
            "rungs with snapshot lead < 60 min before the real tip",
            {b: r["stages"][b]["rungs_lead_below_60"] for b in BOOKS},
            "0",
            all(r["stages"][b]["rungs_lead_below_60"] == 0 for b in BOOKS),
        )
    )
    g = r["meta"]
    grid_ok = all(
        g[s][f"{b}_set"]["grid"]["p_cont_mismatch"] == 0
        and g[s][f"{b}_set"]["grid"]["inconsistent_blocks"] == 0
        and g[s][f"{b}_set"]["starter10_missing"] == 0
        for s in g
        for b in BOOKS
    )
    out.append(
        chk(
            "continuous-grid P(over) equals the served P at every rung; 0 contradictory brackets; starter10 present",
            "all",
            "0 mismatches, 0 inconsistent blocks, 0 missing",
            grid_ok,
        )
    )
    legs = {
        s: g[s]["draftkings_main_legs"]["units_with_odds_history_main_line_equal_to_leg_line"]
        for s in g
    }
    out.append(
        chk(
            "DraftKings main-leg line equals the odds_history main line",
            {s: f"{v['equal']} of {v['legs']}" for s, v in legs.items()},
            "all equal",
            all(v["equal"] == v["legs"] for v in legs.values()),
        )
    )
    cells = pooled["cells_pooled_ok"]
    out.append(
        chk(
            "DraftKings pooled 2023-24: the five A0-bin cells pass (>= 300 games, >= 3,000 rungs, conservative hit-rate hw <= 0.03)",
            {
                b: (
                    pooled["cells_pooled_over_stats"][b]["rungs"],
                    pooled["cells_pooled_over_stats"][b]["hw_cons"],
                )
                for b in cells
            },
            "5 of 5",
            all(cells.values()),
        )
    )
    c23 = dk["2023"]["cells_pooled_ok"]
    out.append(
        chk(
            "DraftKings 2023 alone: the five A0-bin cells pass (the select-season test)",
            {b: dk["2023"]["cells_pooled_over_stats"][b]["hw_cons"] for b in c23},
            "5 of 5",
            all(c23.values()),
        )
    )
    c24 = dk["2024"]["cells_pooled_ok"]
    out.append(
        chk(
            "DraftKings 2024 alone: the five A0-bin cells pass",
            {b: dk["2024"]["cells_pooled_over_stats"][b]["hw_cons"] for b in c24},
            "5 of 5",
            all(c24.values()),
        )
    )
    n_stat_ok = sum(v for per in pooled["cells_by_stat_ok"].values() for v in per.values())
    out.append(
        chk(
            "DraftKings pooled: stat x A0-bin cells that can issue a word (descriptive, no pass needed)",
            f"{n_stat_ok} of 20",
            "reported",
            True,
        )
    )
    for label, st in (("pooled", pooled), ("2023", dk["2023"]), ("2024", dk["2024"])):
        o = st["p1_oracle_projection"]
        proj = o["hw_game_bootstrap"] * DESIGN_EFFECT**0.5
        out.append(
            chk(
                f"P1 projected 95% half-width, DraftKings {label} (synthetic coupled labels, oracle truth; x sqrt(1.51) game design effect)",
                {
                    "rungs": o["n"],
                    "games": o["games"],
                    "oracle_mean_gain": o["mean_kl"],
                    "hw_bootstrap": o["hw_game_bootstrap"],
                    "hw_projected": round(proj, 5),
                    "hw_unit_perfect": o["hw_unit_perfect_corr"],
                    "hw_game_perfect": o["hw_conservative"],
                },
                f"<= {HW_BAR} (floor/2)",
                proj <= HW_BAR,
            )
        )
    comm = [b for b in pooled["blocks"] if b["all_arms_fit"]]
    out.append(
        chk(
            "walk-forward blocks where every arm has >= 2,000 fit rows (pooled 2023-24, DraftKings)",
            {
                "blocks": f"{len(comm)} of {pooled['blocks_total']}",
                "identity": [
                    f"{b['stat']} {b['block']}" for b in pooled["blocks"] if not b["all_arms_fit"]
                ],
                "min_fit_main_legs": min(b["n_fit_main_legs"] for b in comm),
                "min_fit_rungs": min(b["n_fit_rungs"] for b in comm),
            },
            "all 2024 blocks fit; 2023 identity blocks counted, not hidden",
            all(b["all_arms_fit"] for b in dk["2024"]["blocks"]),
        )
    )
    out.append(
        chk(
            "DraftKings rungs dropped from the common set: no main line / identity block (pooled)",
            {
                "no_main_line": pooled["rungs_dropped_no_main_line"],
                "identity_block": pooled["rungs_dropped_identity_block"],
                "common": pooled["rungs_common_to_all_arms"],
                "of": pooled["rungs_in_stage"],
            },
            "reported; common >= 100,000",
            pooled["rungs_common_to_all_arms"] >= 100_000,
        )
    )
    out.append(
        chk(
            "FanDuel pooled 2023-24: the five A0-bin cells pass (replicate, P7)",
            {b: r["stages"]["fanduel"]["pooled_2023_2024"]["cells_pooled_ok"][b] for b in cells},
            "5 of 5",
            all(r["stages"]["fanduel"]["pooled_2023_2024"]["cells_pooled_ok"].values()),
        )
    )
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("reports/prereg_alt_rung_cal/gate.json"))
    ap.add_argument("--no-hash", action="store_true")
    args = ap.parse_args(argv)
    res = run_gate(args.data_root, hash_inputs=not args.no_hash)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
