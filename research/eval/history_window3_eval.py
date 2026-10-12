# ruff: noqa: E501
"""HISTORY_WINDOW_3: 2019-20 and 2020-21 box-score history as extra TRAINING rows for the production props model.

    python -W ignore -m research.eval.history_window3_eval gate --doc docs/prereg/HISTORY_WINDOW_3.md --draft [--repro]
    python -W ignore -m research.eval.history_window3_eval run  --doc docs/prereg/HISTORY_WINDOW_3.md --sha <64 hex>
    python -W ignore -m research.eval.history_window3_eval report --doc docs/prereg/HISTORY_WINDOW_3.md --sha <64 hex>

Design (see the rule): the history seasons enter ONLY as training rows. Their features are built in a separate pass over
the 2019-20 and 2020-21 games alone (so the gap season 2021-22 never touches a feature), with the injury report treated
as missing exactly as production treats a reportless game (``flagged = {}``: ``has_report = 0``, ``n_out_rot`` and
``vac_*`` NULL). Every evaluation row (2023, 2024) keeps the byte-identical W0 feature row. Arms A0, A1, A2, K0, K1.

``gate`` computes the minimum-data gate, the missingness audit, the era-predictability guard and the planted checks,
with NO arm fitted; ``--repro`` adds the W0 pts reproduction (the only fit allowed before the freeze). ``run`` refuses
unless the document is frozen (``--sha`` equals the hash above ``=== RESULTS BELOW ===``, no ``Status: DRAFT`` line),
the gate passed, and the audit file still hashes to the value the gate logged. Both databases are opened read-only and
every query carries ``season <= 2024`` (season 2025 is never loaded).
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl
from sklearn.metrics import roc_auc_score

from nba.eval.context_residual_eval import bh_adjust
from nba.parse.rebuild_lineups import heavy_lock
from nba.props.context_residual import (
    PROP_STATS,
    ContextResidualConfig,
    build_features,
    stat_feature_names,
    stat_frame,
)
from research.eval import history_window_eval as hw

DOC = Path("docs/prereg/HISTORY_WINDOW_3.md")
OUT_DIR = Path("reports/history_window_3")
CACHE = Path("data/history_window_3")
REPORT_MD = Path("reports/history_window_3.md")

# --- constants (docs/prereg/HISTORY_WINDOW_3.md)
HIST3: tuple[int, ...] = (2019, 2020)
CUR: tuple[int, ...] = hw.CUR_SEASONS
TEST_SEASONS: tuple[int, ...] = hw.TEST_SEASONS
REPORT_FEATURES: tuple[str, ...] = (
    "has_report",
    "n_out_rot",
    "vac_min",
    "vac_pts",
    "vac_reb",
    "vac_ast",
    "vac_fg3m",
)
ARMS3: tuple[str, ...] = ("A0", "A1", "A2", "K0", "K1")
FAMILY3: tuple[str, ...] = ("A1", "A2")
HIST_WEIGHT = 0.5
KO_FLOOR = (
    -0.0025
)  # half the practical floor must survive with the report features removed from every season
MATERIAL = 0.01  # a NULL-rate deviation below 1 percentage point of rows is immaterial
NULL_CI = 0.99
AUC_CI = 0.95
N_BOOT_NULL = 1000
N_BOOT_AUC = 200
MIN_ADD_FRAC = 0.30  # history rows must add >= 30% to the W0 training rows at the LAST month block
MIN_HIST_ROWS = 20_000  # eligible history rows per stat
#: W0 reference on today's data (DATA_CHANGELOG 2026-10-11: five restored 2024 neutral-site games); the older
#: 3.1884 (n 27,583) of HISTORY_WINDOW is the pre-clean-up value.
REPRO_REF: dict[int, tuple[int, float]] = {2023: (27619, 3.152830), 2024: (27701, 3.187553)}
REPRO_TOL = 1e-4
RESTORED_2024_OLD = 3.1884  # pre-clean-up value, reported only
PLANT_NULL_FRAC = 0.15
PLANT_AUC_FLIP = 0.10
MIN_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("m<=10", -1.0, 10.0),
    ("m10-20", 10.0, 20.0),
    ("m20-30", 20.0, 30.0),
    ("m>30", 30.0, 1e9),
)
BOX_COLS: tuple[str, ...] = (
    "minutes",
    "pts",
    "reb",
    "ast",
    "fg3m",
    "starter",
    "stl",
    "blk",
    "tov",
    "pf",
    "fta",
    "fga",
    "oreb",
)


def _log(msg: str) -> None:
    hw._log(msg)


# --------------------------------------------------------------------------- data and frames


def restrict_world(w: hw.World) -> hw.World:
    """History side limited to 2019-20 and 2020-21 (2021-22 dropped everywhere); availability dropped."""
    ids = set(w.hist["games"].filter(pl.col("season").is_in(list(HIST3)))["game_id"].to_list())
    h = w.hist
    games = h["games"].filter(pl.col("game_id").is_in(sorted(ids)))
    pgs = h["pgs"].filter(pl.col("game_id").is_in(sorted(ids)))
    flags = h["flags"].filter(pl.col("game_id").is_in(sorted(ids)))
    hist = {
        "games": games,
        "pgs": pgs,
        "static": h["static"],
        "avail": h["avail"].clear(),
        "flags": flags,
    }
    return dataclasses.replace(w, hist=hist)


STATIC_CACHE = Path("data/players_static")


def static_with_cache(
    static_all: pl.DataFrame, player_ids: list[int], cache_dir: Path = STATIC_CACHE
) -> pl.DataFrame:
    """``players_static`` rows (player_id, position, draft_year) for the union of both DBs, completed from the
    per-player CommonPlayerInfo parquet cache (read-only) for players the DBs lack. Nothing is written."""
    have = set(static_all["player_id"].to_list())
    extra: list[pl.DataFrame] = []
    for pid in sorted(set(player_ids) - have):
        f = cache_dir / f"{pid}.parquet"
        if f.exists():
            d = pl.read_parquet(f)
            if d.height:
                extra.append(
                    d.select(
                        pl.col("player_id").cast(pl.Int64),
                        pl.col("position").cast(pl.Utf8),
                        pl.col("draft_year").cast(pl.Int64, strict=False),
                    )
                )
    base = static_all.select(
        pl.col("player_id").cast(pl.Int64),
        pl.col("position").cast(pl.Utf8),
        pl.col("draft_year").cast(pl.Int64, strict=False),
    )
    return pl.concat([base, *extra], how="vertical_relaxed").unique("player_id", keep="first")


def history_static_missing(w: hw.World, cache_dir: Path = STATIC_CACHE) -> list[int]:
    """History-block player ids with no static row in either DB and no cache file (what `fetch-static` pulls)."""
    ids = set(w.hist["pgs"]["player_id"].unique().to_list())
    have = set(w.static_all["player_id"].to_list())
    return sorted(p for p in ids - have if not (cache_dir / f"{p}.parquet").exists())


@dataclass
class Frames3:
    f0: pl.DataFrame  # W0: current window, production features (evaluation rows live here)
    fh: pl.DataFrame  # history block (2019-20, 2020-21), report features missing
    fx: pl.DataFrame  # fh + f0
    fx_w: pl.DataFrame  # fx with the A2 sample weight column
    flags_h: pl.DataFrame
    cur_start: dt.date


def hist_flag_weight(flags: pl.DataFrame) -> pl.DataFrame:
    """(game_id, sample_w): HIST_WEIGHT for a game with ANY era flag, else 1.0."""
    return flags.select(
        "game_id",
        pl.when(pl.any_horizontal(*[pl.col(c) for c in hw.FLAG_COLS]))
        .then(HIST_WEIGHT)
        .otherwise(1.0)
        .alias("sample_w"),
    )


def build_frames3(w: hw.World) -> Frames3:
    """f0 = production features of the current window; fh = history features with ``flagged = {}``."""
    cfg = w.cfg
    flagged_cur, _u, _r = hw.flagged_real(w.cur["avail"], w.cur["games"], cfg, w.tips_cur, set())
    f0 = build_features(
        w.cur["games"],
        w.cur["pgs"].select(list(hw.PROD_PGS)),
        w.cur["static"],
        flagged_cur,
        w.elo_params,
    )
    hg, hp = w.hist["games"], w.hist["pgs"]
    if hg.height and int(hg["season"].max()) > max(HIST3):  # type: ignore[arg-type]
        raise hw.Stop("window", "history block contains a season after 2020-21")
    static_h = static_with_cache(w.static_all, hp["player_id"].unique().to_list())
    fh = build_features(hg, hp.select(list(hw.PROD_PGS)), static_h, {}, w.elo_params)
    fx = pl.concat([fh, f0], how="vertical_relaxed")
    wts = hist_flag_weight(w.hist["flags"])
    fx_w = fx.join(wts, on="game_id", how="left").with_columns(pl.col("sample_w").fill_null(1.0))
    cur_start = w.cur["games"]["game_date"].min()
    assert isinstance(cur_start, dt.date)
    return Frames3(f0, fh, fx, fx_w, w.hist["flags"], cur_start)


def report_free_names(stat: str) -> list[str]:
    return [n for n in stat_feature_names(stat) if n not in REPORT_FEATURES]


def arm_spec3(fr: Frames3, arm: str, stat: str) -> tuple[pl.DataFrame, list[str]]:
    """(feature frame, feature names) of an arm. Knockout arms drop every report column, every season."""
    full = stat_feature_names(stat)
    if arm == "A0":
        return fr.f0, full
    if arm == "A1":
        return fr.fx, full
    if arm == "A2":
        return fr.fx_w, full
    if arm == "K0":
        return fr.f0, report_free_names(stat)
    if arm == "K1":
        return fr.fx, report_free_names(stat)
    raise ValueError(arm)


def union_free_names() -> list[str]:
    seen: dict[str, None] = {}
    for s in PROP_STATS:
        for n in report_free_names(s):
            seen.setdefault(n, None)
    return list(seen)


def eligible(df: pl.DataFrame) -> pl.DataFrame:
    return df.filter(pl.col("n_prior") >= 5)


# --------------------------------------------------------------------------- exactness of the treatment


def treatment_exactness(fr: Frames3) -> dict[str, Any]:
    """The history block carries the report as MISSING exactly as production does for a reportless game; the
    current part of ``fx`` is the W0 frame, row for row."""
    fh = fr.fh
    n = fh.height
    hr0 = int((fh["has_report"] == 0).sum()) if n else 0
    nulls = {
        c: int(fh[c].null_count() + (fh[c].is_nan().sum() if fh[c].dtype.is_float() else 0))
        for c in REPORT_FEATURES
        if c != "has_report"
    }
    cur_part = fr.fx.filter(pl.col("season") >= 2022)
    same = cur_part.height == fr.f0.height and cur_part.equals(fr.f0)
    # a reportless CURRENT game (production behaviour) must look the same in kind
    cur_noreport = fr.f0.filter(pl.col("has_report") == 0)
    cur_null_ok = cur_noreport.height == 0 or all(
        cur_noreport[c].null_count() == cur_noreport.height for c in nulls
    )
    ok = bool(n > 0 and hr0 == n and all(v == n for v in nulls.values()) and same and cur_null_ok)
    return {
        "history_rows": n,
        "history_has_report_zero": hr0,
        "history_report_nulls": nulls,
        "current_part_equals_f0": bool(same),
        "current_reportless_rows_null_like_history": bool(cur_null_ok),
        "current_reportless_rows": cur_noreport.height,
        "pass": ok,
    }


# --------------------------------------------------------------------------- minimum-data gate


def data_gate(w: hw.World, games_dir: Path, hist_db: str) -> dict[str, Any]:
    gh, ph = w.hist["games"], w.hist["pgs"]
    cached: set[str] = set()
    for f in ("2019-20.parquet", "2020-21.parquet"):
        p = games_dir / f
        if p.exists():
            cached |= set(pl.read_parquet(p, columns=["game_id"])["game_id"].to_list())
    ids = gh["game_id"].to_list()
    missing = sorted(cached - set(ids))
    extra = sorted(set(ids) - cached)
    box_share = ph.select("game_id").unique().height / max(len(cached), 1)
    played = ph.filter(pl.col("minutes") > 0)
    fields = float(
        played.select(
            pl.all_horizontal(
                pl.col("pf").is_not_null(), pl.col("fta").is_not_null(), pl.col("fga").is_not_null()
            ).mean()
        ).item()
    )
    dnp = ph.filter(pl.col("minutes").is_null())
    dnp_bad = dnp.filter(
        (pl.col("pts") != 0) | (pl.col("reb") != 0) | (pl.col("ast") != 0) | (pl.col("fg3m") != 0)
    ).height

    def share(pgs: pl.DataFrame, games: pl.DataFrame) -> dict[int, float]:
        j = pgs.join(games.select("game_id", "season"), on="game_id")
        return {
            int(r[0]): float(r[1])
            for r in j.group_by("season").agg(pl.col("minutes").is_null().mean()).iter_rows()
        }

    sh_h, sh_c = share(ph, gh), share(w.cur["pgs"], w.cur["games"])
    lo, hi = min(sh_c.values()) - hw.G01_DNP_PP, max(sh_c.values()) + hw.G01_DNP_PP
    g03 = hw.g03(hist_db)
    mae_ok = all(
        str(s) in g03["per_season"] and g03["per_season"][str(s)]["mae"] <= hw.G03_MAE
        for s in HIST3
    )
    flags_ok = hw.g02(w)
    all_short = bool(
        w.hist["flags"].height > 0
        and w.hist["flags"]["shortened_season"].all()  # every A2 weight is 0.5
    )
    res: dict[str, Any] = {
        "history_games": {str(s): int(gh.filter(pl.col("season") == s).height) for s in HIST3},
        "cached_games": len(cached),
        "missing_vs_cache": len(missing),
        "extra_vs_cache": len(extra),
        "duplicates": len(ids) - len(set(ids)),
        "games_with_box_share": float(box_share),
        "pf_fta_fga_non_null_share": fields,
        "dnp_rows_with_nonzero_stats": int(dnp_bad),
        "dnp_share_history": sh_h,
        "dnp_share_current": sh_c,
        "dnp_band": [lo, hi],
        "possession_mae": {str(s): g03["per_season"].get(str(s)) for s in HIST3},
        "era_flags": {
            k: flags_ok[k]
            for k in (
                "history_flag_coverage",
                "current_flag_coverage",
                "flags_valid",
                "current_all_false",
                "rule_version_sha256",
            )
            if k in flags_ok
        },
        "all_history_games_flagged_shortened": all_short,
    }
    res["pass"] = bool(
        len(cached) > 0
        and not missing
        and not extra
        and res["duplicates"] == 0
        and box_share >= hw.G01_GAMES_BOX
        and fields >= hw.G01_FIELDS
        and dnp_bad == 0
        and all(lo <= v <= hi for v in sh_h.values())
        and mae_ok
        and res["era_flags"].get("history_flag_coverage", 0.0) >= 1.0
        and bool(res["era_flags"].get("flags_valid"))
        and bool(res["era_flags"].get("current_all_false"))
    )
    return res


def rows_gate(fr: Frames3) -> dict[str, Any]:
    """Eligible training rows: history block vs W0, and the relative addition at the LAST month block."""
    out: dict[str, Any] = {}
    ok = True
    for stat in PROP_STATS:
        f0 = stat_frame(fr.f0, stat)
        fh = stat_frame(fr.fh, stat)
        last_test = f0.filter(pl.col("season").is_in(list(TEST_SEASONS)))["game_date"].max()
        assert isinstance(last_test, dt.date)
        first_test = f0.filter(pl.col("season").is_in(list(TEST_SEASONS)))["game_date"].min()
        assert isinstance(first_test, dt.date)
        w0_last = f0.filter(
            pl.col("game_date") < dt.date(last_test.year, last_test.month, 1)
        ).height
        w0_first = f0.filter(pl.col("game_date") < first_test).height
        add = fh.height
        out[stat] = {
            "w0_rows_first_block": w0_first,
            "w0_rows_last_block": w0_last,
            "history_rows": add,
            "history_rows_by_season": {
                str(int(s)): int(n)
                for s, n in fh.group_by("season").len().sort("season").iter_rows()
            },
            "add_frac_first_block": add / max(w0_first, 1),
            "add_frac_last_block": add / max(w0_last, 1),
        }
        ok = ok and add >= MIN_HIST_ROWS and add / max(w0_last, 1) >= MIN_ADD_FRAC
    out["pass"] = bool(ok)
    return out


# --------------------------------------------------------------------------- missingness audit


def _is_missing(c: str) -> pl.Expr:
    x = pl.col(c).cast(pl.Float64, strict=False)
    return (x.is_null() | x.is_nan()).cast(pl.Int64)


def _boot_diff(
    t_n: np.ndarray,
    t_k: np.ndarray,
    r_n: np.ndarray,
    r_k: np.ndarray,
    rng: np.random.Generator,
    n_boot: int,
) -> np.ndarray:
    """Game-clustered bootstrap draws of ``rate(target) - rate(reference)`` from per-game (n, nulls) counts."""
    gt, gr = len(t_n), len(r_n)
    it = rng.integers(0, gt, size=(n_boot, gt), dtype=np.int32)
    ir = rng.integers(0, gr, size=(n_boot, gr), dtype=np.int32)
    tn = t_n[it].sum(axis=1).astype(float)
    tk = t_k[it].sum(axis=1).astype(float)
    rn = r_n[ir].sum(axis=1).astype(float)
    rk = r_k[ir].sum(axis=1).astype(float)
    out: np.ndarray = tk / np.maximum(tn, 1.0) - rk / np.maximum(rn, 1.0)
    return out


def _abs_lower(draws: np.ndarray, level: float) -> float:
    """Lower end of the central ``level`` interval of |D|: 0 when the interval contains 0."""
    a = (1.0 - level) / 2.0
    lo, hi = float(np.quantile(draws, a)), float(np.quantile(draws, 1.0 - a))
    return 0.0 if lo <= 0.0 <= hi else float(min(abs(lo), abs(hi)))


def null_audit(
    d: pl.DataFrame,
    cols: list[str],
    *,
    hist: tuple[int, ...] = HIST3,
    cur: tuple[int, ...] = CUR,
    declared: tuple[str, ...] = (),
    n_boot: int = N_BOOT_NULL,
    level: float = NULL_CI,
    material: float = MATERIAL,
    seed: int = 0,
) -> dict[str, Any]:
    """NULL-pattern audit. ``d`` has game_id, season, stratum and ``cols``.

    Per (column, stratum): rate by season; ``D_s = rate_s - rate(reference)``, reference = pooled current seasons
    for a history season and the pooled OTHER current seasons for a placebo (current) season. Threshold
    ``T = max over current seasons q of |D_q|`` (point values; for a placebo season its own D is left out).
    A season is FLAGGED when the lower end of the ``level`` game-clustered interval of ``|D_s|`` exceeds
    ``max(T, material)``. Columns in ``declared`` (the report features) are tabulated, never flagged."""
    seasons = tuple(hist) + tuple(cur)
    cnt = (
        d.group_by(["game_id", "season", "stratum"])
        .agg(pl.len().alias("_n"), *[_is_missing(c).sum().alias(f"k_{c}") for c in cols])
        .sort(
            ["stratum", "season", "game_id"]
        )  # group_by order is not deterministic: sort for a reproducible bootstrap
    )
    rng = np.random.default_rng(seed)
    cells: list[dict[str, Any]] = []
    for stratum in sorted(cnt["stratum"].unique().to_list()):
        sub = cnt.filter(pl.col("stratum") == stratum)
        per: dict[int, pl.DataFrame] = {s: sub.filter(pl.col("season") == s) for s in seasons}
        if any(per[s].is_empty() for s in seasons):
            continue
        n_arr = {s: per[s]["_n"].to_numpy().astype(np.int64) for s in seasons}
        for c in cols:
            k_arr = {s: per[s][f"k_{c}"].to_numpy().astype(np.int64) for s in seasons}
            rate = {s: float(k_arr[s].sum()) / max(float(n_arr[s].sum()), 1.0) for s in seasons}

            def pooled(
                ss: tuple[int, ...],
                k: dict[int, np.ndarray] = k_arr,
                n: dict[int, np.ndarray] = n_arr,
            ) -> tuple[np.ndarray, np.ndarray]:
                return (
                    np.concatenate([n[s] for s in ss]),
                    np.concatenate([k[s] for s in ss]),
                )

            ref_cur = pooled(cur)
            rate_ref_cur = float(ref_cur[1].sum()) / max(float(ref_cur[0].sum()), 1.0)
            d_pt: dict[int, float] = {}
            refs: dict[int, tuple[np.ndarray, np.ndarray]] = {}
            for s in hist:
                d_pt[s] = rate[s] - rate_ref_cur
                refs[s] = ref_cur
            for p in cur:
                others = tuple(q for q in cur if q != p)
                refs[p] = pooled(others)
                d_pt[p] = rate[p] - float(refs[p][1].sum()) / max(float(refs[p][0].sum()), 1.0)
            no_nulls = all(k_arr[s].sum() == 0 for s in seasons)
            row: dict[str, Any] = {
                "column": c,
                "stratum": stratum,
                "declared": c in declared,
                "rate": {str(s): rate[s] for s in seasons},
                "D": {str(s): d_pt[s] for s in seasons},
                "n_rows": {str(s): int(n_arr[s].sum()) for s in seasons},
            }
            if no_nulls:
                row.update(T=0.0, flags={}, placebo_flags={}, lower_abs={})
            else:
                lower = {
                    s: _abs_lower(
                        _boot_diff(n_arr[s], k_arr[s], refs[s][0], refs[s][1], rng, n_boot), level
                    )
                    for s in seasons
                }
                t_all = max(abs(d_pt[p]) for p in cur)
                row["T"] = t_all
                row["lower_abs"] = {str(s): lower[s] for s in seasons}
                row["flags"] = {str(s): bool(lower[s] > max(t_all, material)) for s in hist}
                row["placebo_flags"] = {
                    str(p): bool(lower[p] > max(max(abs(d_pt[q]) for q in cur if q != p), material))
                    for p in cur
                }
            cells.append(row)
    flagged = [
        f"{r['column']}|{r['stratum']}|{s}"
        for r in cells
        if not r["declared"]
        for s, v in r["flags"].items()
        if v
    ]
    placebo = [
        f"{r['column']}|{r['stratum']}|{s}"
        for r in cells
        if not r["declared"]
        for s, v in r["placebo_flags"].items()
        if v
    ]
    return {
        "cells": cells,
        "n_cells": len(cells),
        "flagged_history_cells": flagged,
        "flagged_placebo_cells": placebo,
        "level": level,
        "material": material,
        "n_boot": n_boot,
    }


def feature_audit_frame(fr: Frames3, w: hw.World) -> tuple[pl.DataFrame, list[str]]:
    """Eligible (n_prior >= 5) rows of every season, the union of report-free feature columns plus the report
    columns and the four labels, with realised-minutes strata ('all' rows are duplicated under stratum 'all')."""
    names = union_free_names()
    labels = list(PROP_STATS)
    cols = [*names, *REPORT_FEATURES, *labels]
    cols = list(dict.fromkeys(cols))
    mins = pl.concat(
        [
            w.cur["pgs"].select("game_id", "player_id", "minutes"),
            w.hist["pgs"].select("game_id", "player_id", "minutes"),
        ]
    )
    base = (
        eligible(fr.fx)
        .select("game_id", "player_id", "season", *cols)
        .join(mins, on=["game_id", "player_id"], how="left")
    )
    parts = [base.with_columns(pl.lit("all").alias("stratum"))]
    for label, lo, hi in MIN_BUCKETS:
        parts.append(
            base.filter((pl.col("minutes") > lo) & (pl.col("minutes") <= hi)).with_columns(
                pl.lit(label).alias("stratum")
            )
        )
    return pl.concat(parts), cols


def box_audit_frame(w: hw.World) -> pl.DataFrame:
    """Rostered box-score rows (DNP kept) of every season, strata played / DNP."""
    rows = []
    for side in (w.hist, w.cur):
        j = side["pgs"].join(side["games"].select("game_id", "season"), on="game_id")
        rows.append(j)
    d = pl.concat(rows, how="vertical_relaxed")
    return d.with_columns(
        pl.when(pl.col("minutes") > 0)
        .then(pl.lit("played"))
        .otherwise(pl.lit("dnp"))
        .alias("stratum")
    )


# --------------------------------------------------------------------------- era predictability


def _row_index_by_game(gid: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    order = np.argsort(gid, kind="stable")
    g_sorted = gid[order]
    _, starts, counts = np.unique(g_sorted, return_index=True, return_counts=True)
    return order, starts, counts


def _feature_matrix(d: pl.DataFrame, names: list[str], indicator: bool) -> np.ndarray:
    """Values, or (indicator=True) the 0/1 NULL-or-NaN indicators of ``names``."""
    if indicator:
        return d.select([_is_missing(c).alias(c) for c in names]).to_numpy().astype(float)
    return d.select([pl.col(c).cast(pl.Float64) for c in names]).to_numpy()


def era_auc(
    a: pl.DataFrame,
    b: pl.DataFrame,
    names: list[str],
    *,
    indicator: bool = False,
    n_boot: int = N_BOOT_AUC,
    level: float = AUC_CI,
    seed: int = 0,
) -> dict[str, Any]:
    """AUC of a LightGBM classifier (100 trees, 15 leaves, seed 0) separating rows of ``a`` (label 1) from ``b``
    (label 0); game-level split (hash(game_id) % 3 == 0 held out); game-clustered bootstrap CI. With
    ``indicator`` the features are the NULL indicators only (a missingness-pattern AUC)."""
    d = pl.concat(
        [a.select("game_id", *names), b.select("game_id", *names)], how="vertical_relaxed"
    )
    y = np.r_[np.ones(a.height, dtype=int), np.zeros(b.height, dtype=int)]
    x = _feature_matrix(d, names, indicator)
    gid = d["game_id"].to_numpy()
    held = np.array([int(hashlib.sha256(str(g).encode()).hexdigest(), 16) % 3 == 0 for g in gid])
    if not (held & (y == 1)).any() or not (held & (y == 0)).any() or not names:
        return {
            "auc": float("nan"),
            "lo": float("nan"),
            "hi": float("nan"),
            "n_test": int(held.sum()),
        }
    clf = lgb.LGBMClassifier(
        n_estimators=100,
        num_leaves=15,
        random_state=seed,
        n_jobs=4,
        verbose=-1,
        deterministic=True,
        force_row_wise=True,
    )
    clf.fit(x[~held], y[~held])
    p = np.asarray(clf.predict_proba(x[held]))[:, 1]
    yt, gt = y[held], gid[held]
    auc = float(roc_auc_score(yt, p))
    order, starts, counts = _row_index_by_game(gt)
    rng = np.random.default_rng(seed)
    ng = len(starts)
    draws = np.empty(n_boot)
    for i in range(n_boot):
        gs = rng.integers(0, ng, size=ng)
        lens = counts[gs]
        off = np.cumsum(lens) - lens
        idx = order[np.arange(lens.sum()) - np.repeat(off, lens) + np.repeat(starts[gs], lens)]
        draws[i] = roc_auc_score(yt[idx], p[idx]) if len(set(yt[idx])) == 2 else np.nan
    q = (1.0 - level) / 2.0
    return {
        "auc": auc,
        "lo": float(np.nanquantile(draws, q)),
        "hi": float(np.nanquantile(draws, 1.0 - q)),
        "n_test": int(held.sum()),
        "n_games_test": int(ng),
        "n_train": int((~held).sum()),
    }


def _season_rows(fx: pl.DataFrame, seasons: tuple[int, ...]) -> pl.DataFrame:
    return eligible(fx).filter(pl.col("season").is_in(list(seasons)))


def indicator_names(fx: pl.DataFrame, exclude: tuple[str, ...] = ()) -> list[str]:
    """Report-free columns with at least one NULL/NaN anywhere (constant-present columns carry no pattern)."""
    d = eligible(fx)
    out = []
    for c in union_free_names():
        if c in exclude:
            continue
        if int(d.select(_is_missing(c).sum()).item()) > 0:
            out.append(c)
    return out


def era_guard(
    fx: pl.DataFrame, n_boot: int = N_BOOT_AUC, exclude: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Can the era be predicted from the PATTERN of missing values alone (report columns excluded)? AUC of a
    classifier on NULL indicators, for history-vs-2022 and pooled history-vs-pooled current, against placebo pairs
    of current seasons. A history pair is flagged when the lower end of its interval exceeds
    ``max(max placebo AUC, 0.5 + MATERIAL / 2)`` (a single column NULL in a share ``r`` of one era only gives
    AUC 0.5 + r / 2, so the floor is the same 1-percentage-point materiality as the NULL-rate audit).
    The value-based AUC (descriptive) and the declared report-columns AUC are reported, never gated."""
    names = indicator_names(fx, exclude)
    pairs_placebo = [(2022, 2023), (2023, 2024), (2022, 2024)]
    pairs_hist = [(2019, 2022), (2020, 2022)]
    res: dict[str, Any] = {"indicator_columns": names, "pairs": {}, "value_auc_descriptive": {}}
    for a, b in [*pairs_placebo, *pairs_hist]:
        res["pairs"][f"{a}v{b}"] = era_auc(
            _season_rows(fx, (a,)), _season_rows(fx, (b,)), names, indicator=True, n_boot=n_boot
        )
    res["pairs"]["hist_pooled_v_cur_pooled"] = era_auc(
        _season_rows(fx, HIST3), _season_rows(fx, CUR), names, indicator=True, n_boot=n_boot
    )
    t_all = max(res["pairs"][f"{a}v{b}"]["auc"] for a, b in pairs_placebo)
    limit = max(t_all, 0.5 + MATERIAL / 2.0)
    placebo_self = {}
    for a, b in pairs_placebo:
        others = max(res["pairs"][f"{x}v{y}"]["auc"] for x, y in pairs_placebo if (x, y) != (a, b))
        placebo_self[f"{a}v{b}"] = bool(
            res["pairs"][f"{a}v{b}"]["lo"] > max(others, 0.5 + MATERIAL / 2.0)
        )
    hist_flags = {
        k: bool(res["pairs"][k]["lo"] > limit)
        for k in ("2019v2022", "2020v2022", "hist_pooled_v_cur_pooled")
    }
    full = union_free_names()
    for a, b in [*pairs_placebo, (2019, 2022), (2020, 2022)]:
        res["value_auc_descriptive"][f"{a}v{b}"] = era_auc(
            _season_rows(fx, (a,)), _season_rows(fx, (b,)), full, n_boot=min(n_boot, 50)
        )
    res["declared_report_columns_auc"] = era_auc(
        _season_rows(fx, HIST3),
        _season_rows(fx, CUR),
        list(REPORT_FEATURES),
        n_boot=min(n_boot, 50),
    )
    res.update(
        placebo_max_auc=t_all,
        limit=limit,
        placebo_self_flags=placebo_self,
        history_flags=hist_flags,
        pass_rule=bool(not any(hist_flags.values())),
        placebo_rule_ok=bool(not any(placebo_self.values())),
    )
    return res


# --------------------------------------------------------------------------- planted checks (real frames, no arm)


def planted_future(
    w: hw.World, fr: Frames3, cutoff: dt.date = dt.date(2020, 3, 1)
) -> dict[str, Any]:
    """As-of check on the history build: features of rows before ``cutoff`` computed from games before ``cutoff``
    only must equal the full build's features for the same rows."""
    hg = w.hist["games"].with_columns(pl.col("game_date").cast(pl.Date))
    early_ids = hg.filter(pl.col("game_date") < cutoff)["game_id"].to_list()
    g2 = hg.filter(pl.col("game_id").is_in(early_ids))
    p2 = w.hist["pgs"].filter(pl.col("game_id").is_in(early_ids)).select(list(hw.PROD_PGS))
    static_h = static_with_cache(w.static_all, p2["player_id"].unique().to_list())
    part = build_features(g2, p2, static_h, {}, w.elo_params)
    full = fr.fh.filter(pl.col("game_id").is_in(early_ids))
    keys = ["game_id", "player_id"]
    j = part.join(full, on=keys, suffix="__full", how="inner")
    bad: list[str] = []
    for c in part.columns:
        if c in keys or f"{c}__full" not in j.columns:
            continue
        a = j[c].cast(pl.Float64, strict=False).to_numpy()
        b = j[f"{c}__full"].cast(pl.Float64, strict=False).to_numpy()
        if not np.allclose(a, b, equal_nan=True, rtol=1e-9, atol=1e-9):
            bad.append(c)
    return {
        "cutoff": str(cutoff),
        "rows_compared": j.height,
        "rows_full": full.height,
        "rows_truncated": part.height,
        "columns_differing": bad,
        "pass": bool(j.height == full.height == part.height and not bad),
    }


def planted_detection(audit_df: pl.DataFrame, fr: Frames3) -> dict[str, Any]:
    """The guards must have power on the real frames: 15% of one column's history values set NULL is flagged by the
    NULL-rate audit AND lifts the NULL-indicator AUC above its limit, while the unplanted data is flagged by neither."""
    col = "rest_days"
    sub = audit_df.filter(pl.col("stratum") == "all").select(
        "game_id", "player_id", "season", "stratum", col
    )
    rng = np.random.default_rng(0)
    is_h = sub["season"].is_in(list(HIST3)).to_numpy()
    kill = is_h & (rng.random(sub.height) < PLANT_NULL_FRAC)
    planted = sub.with_columns(
        pl.when(pl.lit(pl.Series(kill))).then(None).otherwise(pl.col(col)).alias(col)
    )
    clean = null_audit(sub.drop("player_id"), [col], n_boot=300)
    dirty = null_audit(planted.drop("player_id"), [col], n_boot=300)
    fx = fr.fx.select("game_id", "player_id", "season", "n_prior", col)
    kill_fx = fx["season"].is_in(list(HIST3)).to_numpy() & (
        np.random.default_rng(1).random(fx.height) < PLANT_NULL_FRAC
    )
    fx_pl = fx.with_columns(
        pl.when(pl.lit(pl.Series(kill_fx))).then(None).otherwise(pl.col(col)).alias(col)
    )
    # a clean copy has no NULL in this column, so the indicator classifier needs a NULL somewhere: the plant is the test
    auc_plant = era_auc(
        _season_rows(fx_pl, (2020,)), _season_rows(fx_pl, (2022,)), [col], indicator=True, n_boot=50
    )
    auc_clean = era_auc(
        _season_rows(fx, (2020,)), _season_rows(fx, (2022,)), [col], indicator=True, n_boot=50
    )
    return {
        "null_plant_column": col,
        "null_plant_fraction": PLANT_NULL_FRAC,
        "clean_flags": clean["flagged_history_cells"],
        "planted_flags": dirty["flagged_history_cells"],
        "null_plant_detected": bool(dirty["flagged_history_cells"]),
        "null_clean_not_flagged": bool(not clean["flagged_history_cells"]),
        "indicator_auc_clean": auc_clean["auc"],
        "indicator_auc_planted": auc_plant["auc"],
        "indicator_auc_planted_lo": auc_plant["lo"],
        "pass": bool(
            dirty["flagged_history_cells"]
            and not clean["flagged_history_cells"]
            and auc_plant["lo"] > 0.5 + MATERIAL / 2.0
        ),
    }


# --------------------------------------------------------------------------- W0 reproduction (the only fit before the freeze)


def w0_repro(fr: Frames3, cfg: ContextResidualConfig) -> dict[str, Any]:
    t0 = time.time()
    sc, info = hw.walk_arm(fr.f0, "pts", cfg, hw.stat_names("pts", ()), keys=None, progress=True)
    secs = time.time() - t0
    res: dict[str, Any] = {
        "seconds": secs,
        "n_blocks": len(info["blocks"]),
        "skipped": info["skipped"],
    }
    ok = True
    for season, (n_want, crps_want) in REPRO_REF.items():
        m = sc["season"] == season
        got = float(sc["crps_int"][m].mean()) if m.any() else float("nan")
        good = bool(abs(got - crps_want) <= REPRO_TOL and int(m.sum()) == n_want)
        res[str(season)] = {
            "got": got,
            "want": crps_want,
            "n": int(m.sum()),
            "n_want": n_want,
            "ok": good,
        }
        ok = ok and good
    res["old_2024_reference_3.1884_would_pass"] = bool(
        abs(res["2024"]["got"] - RESTORED_2024_OLD) <= REPRO_TOL
    )
    res["blocks_train_rows"] = [b["n_train"] for b in info["blocks"]]
    res["pass"] = ok
    return res


# --------------------------------------------------------------------------- gate driver


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_gate(
    w: hw.World,
    fr: Frames3,
    hist_db: str,
    games_dir: Path,
    out_dir: Path,
    *,
    repro: bool,
    n_boot_null: int = N_BOOT_NULL,
    n_boot_auc: int = N_BOOT_AUC,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    items: dict[str, Any] = {}
    _log("G1 data completeness")
    items["G1_data"] = data_gate(w, games_dir, hist_db)
    miss_static = history_static_missing(w)
    # Amended pre-freeze (2026-10-11): the props model trains on PLAYED rows only, so the gate
    # covers players with at least one played history row; never-played ids are listed, not failed.
    played_ids = set(w.hist["pgs"].filter(pl.col("minutes") > 0)["player_id"].unique().to_list())
    miss_played = [i for i in miss_static if i in played_ids]
    items["G1b_static"] = {
        "history_players": int(w.hist["pgs"]["player_id"].n_unique()),
        "without_static_in_either_db_or_cache": len(miss_static),
        "without_static_and_played": len(miss_played),
        "never_played_without_static": sorted(int(i) for i in miss_static if i not in played_ids),
        "pass": bool(not miss_played),
        "remedy": "python -W ignore -m research.eval.history_window3_eval fetch-static (free nba_api, ~0.6 s per player, resumable)",
    }
    _log("G2 eligible training rows")
    items["G2_rows"] = rows_gate(fr)
    _log("G3 treatment exactness")
    items["G3_treatment"] = treatment_exactness(fr)
    _log("G4 missingness audit (features)")
    adf, cols = feature_audit_frame(fr, w)
    feat = null_audit(adf, cols, declared=REPORT_FEATURES, n_boot=n_boot_null)
    _log("G4 missingness audit (box columns by played/DNP)")
    box = null_audit(box_audit_frame(w), list(BOX_COLS), n_boot=n_boot_null)
    items["G4_missingness"] = {
        "feature_cells": feat["n_cells"],
        "box_cells": box["n_cells"],
        "flagged_history_cells": [*feat["flagged_history_cells"], *box["flagged_history_cells"]],
        "false_flags_on_current_seasons": [
            *feat["flagged_placebo_cells"],
            *box["flagged_placebo_cells"],
        ],
        "pass": bool(
            not feat["flagged_history_cells"]
            and not box["flagged_history_cells"]
            and not feat["flagged_placebo_cells"]
            and not box["flagged_placebo_cells"]
        ),
    }
    _log("G5 era predictability (report-free features) with placebo pairs")
    items["G5_era"] = era_guard(fr.fx, n_boot_auc)
    items["G5_era"]["pass"] = bool(
        items["G5_era"]["pass_rule"] and items["G5_era"]["placebo_rule_ok"]
    )
    diag_flags = [
        c for c in items["G4_missingness"]["flagged_history_cells"] if not c.startswith("pos_code|")
    ]
    diag_era = era_guard(fr.fx, n_boot_auc, exclude=("pos_code",))
    items["DIAGNOSTIC_without_pos_code"] = {
        "note": "not a gate item; what G4/G5 would show if pos_code were NULL-free in the history block",
        "g4_flagged_history_cells": diag_flags,
        "g4_false_flags_on_current_seasons": items["G4_missingness"][
            "false_flags_on_current_seasons"
        ],
        "g5_pass_rule": diag_era["pass_rule"],
        "g5_placebo_rule_ok": diag_era["placebo_rule_ok"],
        "g5_history_flags": diag_era["history_flags"],
        "g5_limit": diag_era["limit"],
        "g5_pairs": {k: v["auc"] for k, v in diag_era["pairs"].items()},
        "pass": True,
    }
    _log("G6 planted future (history build is as-of)")
    items["G6_planted_future"] = planted_future(w, fr)
    _log("G7 planted detection (guards have power on the real frames)")
    items["G7_planted_detection"] = planted_detection(adf, fr)
    if repro:
        _log("G8 W0 reproduction (pts)")
        items["G8_w0_repro"] = w0_repro(fr, w.cfg)
    audit = {"feature_null_audit": feat, "box_null_audit": box}
    audit_path = out_dir / "audit.json"
    audit_path.write_text(json.dumps(audit, indent=2, default=str, sort_keys=True))
    audit_sha = file_sha(audit_path)
    _log(f"audit sha256 {audit_sha}")
    failed = [k for k, v in items.items() if not v.get("pass", False)]
    gate = {
        "items": items,
        "failed_items": failed,
        "audit_sha256": audit_sha,
        "repro_run": bool(repro),
        "gate_pass": bool(not failed and repro),
    }
    (out_dir / "gate.json").write_text(json.dumps(gate, indent=2, default=str, sort_keys=True))
    return gate


# --------------------------------------------------------------------------- freeze guards


def frozen_ok(doc: Path, sha: str) -> str:
    cur = hw.frozen_sha256(doc)
    if not re.fullmatch(r"[0-9a-f]{64}", sha) or sha != cur:
        raise SystemExit(f"REFUSED: frozen-section sha256 of {doc} is {cur}, --sha given {sha}")
    top = doc.read_text().split(hw.MARKER)[0]
    if re.search(r"^Status:\s*\**DRAFT", top, flags=re.M):
        raise SystemExit("REFUSED: the document is still marked DRAFT")
    return cur


def require_gate3(out_dir: Path) -> dict[str, Any]:
    p = out_dir / "gate.json"
    if not p.exists():
        raise SystemExit("REFUSED: run the gate stage first (gate.json missing)")
    gate: dict[str, Any] = json.loads(p.read_text())
    if not gate.get("gate_pass"):
        raise SystemExit(f"REFUSED: gate did not pass: {gate.get('failed_items')}")
    a = out_dir / "audit.json"
    if not a.exists() or file_sha(a) != gate["audit_sha256"]:
        raise SystemExit("REFUSED: audit.json is missing or no longer hashes to the logged value")
    return gate


# --------------------------------------------------------------------------- arms (run stage; refused until frozen)


def run_arms3(
    fr: Frames3,
    cfg: ContextResidualConfig,
    *,
    stats: tuple[str, ...] = PROP_STATS,
    arms: tuple[str, ...] = ARMS3,
    cache: Path | None = None,
    only_block: tuple[int, int] | None = None,
    progress: bool = True,
) -> dict[str, Any]:
    scores: dict[tuple[str, str], dict[str, np.ndarray]] = {}
    infos: dict[str, Any] = {}
    key_log: dict[str, Any] = {}
    for stat in stats:
        a0: dict[str, np.ndarray] | None = None
        for arm in ["A0", *[a for a in arms if a != "A0"]]:
            path = None if cache is None else cache / f"arm_{arm}_{stat}.npz"
            if path is not None and path.exists() and only_block is None:
                sc, info = hw._load(path)
                _log(f"{arm} {stat}: resumed from {path}")
            else:
                feats, names = arm_spec3(fr, arm, stat)
                _log(f"{arm} {stat}: fitting ({feats.height} feature rows, {len(names)} features)")
                keys = None
                if arm != "A0":
                    assert a0 is not None
                    keys = pl.DataFrame({"game_id": a0["gid"], "player_id": a0["pid"]})
                sc, info = hw.walk_arm(
                    feats,
                    stat,
                    cfg,
                    names,
                    keys=keys,
                    cal_floor=fr.cur_start if arm in ("A1", "A2", "K1") else None,
                    only_block=only_block,
                    progress=progress,
                )
                if not sc:
                    raise hw.Stop("empty", f"{arm} {stat} scored no rows")
                if path is not None and only_block is None:
                    hw._save(path, sc, info)
            if arm == "A0":
                a0 = sc
            else:
                assert a0 is not None
                sc = hw.align(a0, sc)
            scores[(arm, stat)] = sc
            infos[f"{arm}|{stat}"] = info
            key_log[f"{arm}|{stat}"] = {
                "n_rows": int(len(sc["gid"])),
                "n_games": int(len(np.unique(sc["gid"]))),
                "key_hash": hw.key_hash(sc["gid"], sc["pid"]),
            }
            if key_log[f"{arm}|{stat}"]["key_hash"] != key_log[f"A0|{stat}"]["key_hash"]:
                raise hw.Stop("keys", f"{arm} {stat}: key hash differs from A0")
    return {"scores": scores, "infos": infos, "key_log": key_log}


def slice_masks3(
    fr: Frames3, static_all: pl.DataFrame, keys: pl.DataFrame, season: np.ndarray
) -> dict[str, np.ndarray]:
    """Labels identical across arms (never features). 'cold_start' uses the current-window n_prior (production
    definition); 'true_rookie' uses the first season in the union of the history block and the current window."""
    f0 = stat_frame(fr.f0, "pts").select(
        "game_id", "player_id", "n_prior", "starter10", "team_game_no", "n_out_rot", "has_report"
    )
    first = fr.fx.group_by("player_id").agg(pl.col("season").min().alias("first_union_season"))
    draft = static_all.select("player_id", "draft_year").unique("player_id", keep="first")
    meta = (
        keys.select("game_id", "player_id")
        .join(f0, on=["game_id", "player_id"], how="left")
        .join(first, on="player_id", how="left")
        .join(draft, on="player_id", how="left")
    )
    npr = meta["n_prior"].to_numpy().astype(float)
    nout = meta["n_out_rot"].to_numpy().astype(float)
    hasr = meta["has_report"].to_numpy().astype(float)
    st10 = meta["starter10"].to_numpy().astype(float)
    tgn = meta["team_game_no"].to_numpy().astype(float)
    fs = meta["first_union_season"].to_numpy().astype(float)
    dy = meta["draft_year"].to_numpy().astype(float)
    rookie = (fs == season) & (np.isnan(dy) | (dy >= season - 1))
    return {
        "cold_start(n_prior<20, current window)": npr < 20,
        "true_rookie": rookie,
        "injury_heavy(n_out_rot>=2)": nout >= 2,
        "teammate_out(n_out_rot>0)": (hasr > 0) & (nout > 0),
        "starter(starter10>=.5)": st10 >= 0.5,
        "bench": st10 < 0.5,
        "first15_team_games": tgn < 15,
    }


def arm_verdict3(
    per_stat: dict[str, dict[str, dict[str, Any]]], knock: dict[str, dict[str, float]]
) -> dict[str, Any]:
    """``per_stat[stat][season] = {"pass", "harm"}``; ``knock[stat][season]`` = K1-K0 point.
    2023 selects, 2024 confirms (same stat). The knockout guard must hold in both seasons for a confirmed stat."""
    sel, rep = str(TEST_SEASONS[0]), str(TEST_SEASONS[1])
    confirmed = [s for s, v in per_stat.items() if v[sel]["pass"] and v[rep]["pass"]]
    ko_ok = [s for s in confirmed if knock[s][sel] <= KO_FLOOR and knock[s][rep] <= KO_FLOOR]
    harm = [f"{s}:{x}" for s, v in per_stat.items() for x in (sel, rep) if v[x]["harm"]]
    if harm:
        status = "HARM"
    elif ko_ok:
        status = "PASS"
    elif confirmed:
        status = "FAIL_KNOCKOUT"
    else:
        status = "FAIL"
    return {
        "status": status,
        "confirmed_stats": confirmed,
        "knockout_ok_stats": ko_ok,
        "harm": harm,
    }


def analyze3(
    run: dict[str, Any],
    fr: Frames3,
    static_all: pl.DataFrame,
    stats: tuple[str, ...] = PROP_STATS,
    n_boot: int = hw.N_BOOT,
) -> dict[str, Any]:
    sc = run["scores"]
    a0 = sc[("A0", stats[0])]
    keys = pl.DataFrame({"game_id": a0["gid"], "player_id": a0["pid"]})
    masks = slice_masks3(fr, static_all, keys, a0["season"].astype(float))
    out: dict[str, Any] = {
        "baseline": {},
        "contrasts": {},
        "knockout": {},
        "family": {},
        "verdict": {},
    }
    for season in TEST_SEASONS:
        for stat in stats:
            b = sc[("A0", stat)]
            m = b["season"] == season
            out["baseline"][f"{stat}|{season}"] = {
                "n_rows": int(m.sum()),
                "n_games": int(len(np.unique(b["gid"][m]))),
                "crps_int": float(b["crps_int"][m].mean()),
            }
            for arm in FAMILY3:
                out["contrasts"][f"{arm}|{stat}|{season}"] = hw.contrast(
                    b, sc[(arm, stat)], season, masks, n_boot
                )
            out["knockout"][f"K1-K0|{stat}|{season}"] = hw.contrast(
                sc[("K0", stat)], sc[("K1", stat)], season, None, n_boot
            )
            out["knockout"][f"K0-A0|{stat}|{season}"] = hw.contrast(
                b, sc[("K0", stat)], season, None, n_boot
            )
    vin: dict[str, dict[str, dict[str, dict[str, Any]]]] = {
        a: {s: {} for s in stats} for a in FAMILY3
    }
    for season in TEST_SEASONS:
        fam = [(a, s) for a in FAMILY3 for s in stats]
        p = np.array([out["contrasts"][f"{a}|{s}|{season}"]["p"] for a, s in fam])
        adj = bh_adjust(p)
        out["family"][str(season)] = {
            "tests": [f"{a}|{s}" for a, s in fam],
            "p": p.tolist(),
            "bh_adj_p": adj.tolist(),
        }
        for (a, s), q in zip(fam, adj, strict=True):
            c = out["contrasts"][f"{a}|{s}|{season}"]
            pc = hw.pass_checks(c, float(q))
            c["pass_checks"] = pc
            c["harm"] = hw.is_harm(c)
            vin[a][s][str(season)] = {"pass": pc["pass"], "harm": c["harm"]}
    for a in FAMILY3:
        knock = {
            s: {
                str(se): float(out["knockout"][f"K1-K0|{s}|{se}"]["d_crps_int"]["point"])
                for se in TEST_SEASONS
            }
            for s in stats
        }
        out["verdict"][a] = arm_verdict3(vin[a], knock)
    return out


def draft_ledger_rows3(res: dict[str, Any], sha: str) -> list[str]:
    rows = []
    for a in FAMILY3:
        v = res["verdict"][a]
        rows.append(
            f"HISTORY_WINDOW_3 (sha256 {sha[:8]}) arm {a}: {v['status']}; confirmed stats {v['confirmed_stats']}; "
            f"knockout-ok {v['knockout_ok_stats']}; harm {v['harm']}"
        )
    return rows


def fetch_static(
    db: str, hist_db: str, config: str, elo_config: str, schedule_dir: str | None
) -> int:
    """Pull CommonPlayerInfo (free nba_api, resumable, one parquet per player under data/players_static) for the
    history players that no DB or cache row covers. Writes no database. Run by the maintainer, not by the harness."""
    from nba.ingest.cache import RateLimiter
    from nba.ingest.players_static import pull_players_static

    w = restrict_world(hw.load_world(db, hist_db, config, elo_config, schedule_dir))
    ids = history_static_missing(w)
    print(f"{len(ids)} history players need a CommonPlayerInfo pull", flush=True)
    limiter = RateLimiter(min_interval_s=1.0)
    failed: list[tuple[int, str]] = []
    for pid in (int(i) for i in ids):
        try:
            pull_players_static([pid], rate_limiter=limiter)
        except Exception as exc:  # one bad response must not stop the other players
            failed.append((pid, f"{type(exc).__name__}: {exc}"))
            print(f"failed {pid}: {type(exc).__name__}: {exc}", flush=True)
    print(f"pull done: {len(ids) - len(failed)} ok, {len(failed)} failed", flush=True)
    left = history_static_missing(w)
    print(f"still missing after the pull: {len(left)}", flush=True)
    return 0 if not left else 1


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HISTORY_WINDOW_3 gate / run / report")
    ap.add_argument("stage", choices=("gate", "run", "report", "fetch-static"))
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--history-db", default="data/history/nba_history.duckdb")
    ap.add_argument("--doc", default=str(DOC))
    ap.add_argument("--sha", default="")
    ap.add_argument("--draft", action="store_true", help="gate stage on an uncommitted draft")
    ap.add_argument("--repro", action="store_true", help="gate: also reproduce W0 pts (one fit)")
    ap.add_argument("--config", default="configs/context_residual.yaml")
    ap.add_argument("--elo-config", default="configs/mov_elo_tuned.yaml")
    ap.add_argument("--schedule-dir", default=None)
    ap.add_argument("--games-dir", default=str(hw.HIST_GAMES))
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--cache-dir", default=str(CACHE))
    ap.add_argument("--report-md", default=str(REPORT_MD))
    ap.add_argument("--n-boot", type=int, default=hw.N_BOOT)
    a = ap.parse_args(argv)
    doc, out = Path(a.doc), Path(a.out_dir)
    if a.stage == "fetch-static":
        return fetch_static(a.db, a.history_db, a.config, a.elo_config, a.schedule_dir)
    if a.stage == "gate":
        if not a.draft:
            frozen_ok(doc, a.sha)
    else:
        sha = frozen_ok(doc, a.sha)
    if a.stage == "report":
        res = json.loads((out / "results.json").read_text())
        Path(a.report_md).write_text(
            "# HISTORY_WINDOW_3 results\n\n```json\n"
            + json.dumps(res["verdict"], indent=1)
            + "\n```\n"
        )
        print("wrote", a.report_md)
        return 0
    try:
        if a.stage == "run":
            require_gate3(out)
        w = restrict_world(
            hw.load_world(a.db, a.history_db, a.config, a.elo_config, a.schedule_dir)
        )
        fr = build_frames3(w)
        if a.stage == "gate":
            with heavy_lock():
                gate = run_gate(w, fr, a.history_db, Path(a.games_dir), out, repro=a.repro)
            print(
                json.dumps(
                    {k: gate[k] for k in ("gate_pass", "failed_items", "audit_sha256")}, indent=1
                )
            )
            return 0 if gate["gate_pass"] else 3
        with heavy_lock():
            run = run_arms3(fr, w.cfg, cache=Path(a.cache_dir))
            res = analyze3(
                run,
                fr,
                static_with_cache(w.static_all, fr.fx["player_id"].unique().to_list()),
                n_boot=a.n_boot,
            )
        res["key_log"] = run["key_log"]
        res["ledger_rows_draft"] = draft_ledger_rows3(res, sha)
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.json").write_text(json.dumps(res, indent=2, default=str))
        print(json.dumps(res["verdict"], indent=1))
        return 0
    except hw.Stop as e:
        hw._write_stop(out, e)
        print(str(e), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
