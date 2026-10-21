# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""WOWY_STRUCT harness (rule: docs/prereg/WOWY_STRUCT.md; DRAFT until the main session commits `prereg(WOWY_STRUCT): <sha256>`).
Model class: LightGBM context-residual heads (production) with added as-of columns, read through the lower-tail mixture, integer-support CRPS.

    R="-m research.archive.wowy_struct.wowy_struct_eval"; D=/Users/devin/Downloads/nba-prediction
    /Users/devin/Downloads/nba-prediction/.venv/bin/python -W ignore $R check    --data-root $D [--smoke]      # label-free preflight (no score of any kind); --smoke fits ONE block of pts
    /Users/devin/Downloads/nba-prediction/.venv/bin/python -W ignore $R rehearse [--world pass|guard|one_season|no_gain|all] [--seeds 5]   # synthetic scores, no data, no fit
    /Users/devin/Downloads/nba-prediction/.venv/bin/python -W ignore $R run      --data-root $D --sha <64 hex>   # frozen + pinned only; heavy.lock; per-arm checkpoints; verdict

``run`` refuses unless the rule is frozen AND the harness files equal the `harness(WOWY_STRUCT): <pin>` commit (``wowy_struct_rule.require_ready``), then runs the
preflight (library versions, input hashes pinned in the rule, F1's arm-C cache is the frozen F1 run, the priors, the stored gate counts) BEFORE any 2023-24 outcome
is scored. ``nba.duckdb`` and the history DB are opened read-only with ``season <= 2024`` in SQL; season 2025 is never loaded.

Arms (every arm trains on rows dated 2019-10-01 or later, the production month blocks, seed 0, the mixture ``pi`` / ``w_q`` of F1's arm C): ``A0`` is F1_WARM_WINDOW's arm C
read from its cache (tag-checked), NOT refitted; the other arms are the same walk with columns added: ROLE, S_R (the claim), P_R (S_R's column permuted within
player-season), T_R (STRUCT_REPL, Devin's twist), TP_R (T_R's columns permuted), TQ (T_R's identity placebo), RAW (the un-normalised on/off). The verdict arm is S_R;
the twist arms are DESCRIPTIVE (the cell gate dropped them: docs/prereg/WOWY_STRUCT.md).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import yaml

from nba.eval.context_residual_eval import MAX_SEASON, month_blocks
from nba.eval.lower_tail_eval import ROW_COVARIATES
from nba.props.context_residual import (
    ContextResidualConfig,
    ContextResidualModel,
    split_calibration,
    stat_feature_names,
    stat_frame,
)
from nba.props.metrics import _clustered_boot_means
from research.archive.wowy_struct import wowy_struct_crps_null as NU
from research.archive.wowy_struct import wowy_struct_repl_gate as WG
from research.archive.wowy_struct import wowy_struct_rule as rule
from research.eval import f1_warm_window_run as F1R

SEASONS = (2023, 2024)
STATS = ("pts", "ast")
CELLS = WG.CELLS
CELL_KEYS = tuple(f"{st}|{sn}" for st, sn in CELLS)
CHOSEN = WG.CHOSEN
FLOOR = -0.005  # S_R minus ROLE, integer CRPS, per cell
NOHARM = 0.001  # G2: S_R minus A0 on all rows of the A0 row set (point estimate)
COV_TAUS = (0.10, 0.20, 0.80, 0.90)
COV_TOL = 0.01  # G3: |coverage(S_R) - coverage(A0)| <= 0.01 at each tau, all rows
SLICE_TOL = 0.01  # G5: S_R minus ROLE inside a gated slice <= +0.01
MIN_SLICE_N = 300
TWIST_FLOOR = -0.0025  # descriptive label only: T_R minus S_R
N_BOOT = 2000
SEED = 0
ALPHA = 0.05
T_C = {
    "pts|leader_out": 0.0050,
    "pts|top25_fixed": 0.0063,
    "ast|leader_out": 0.0030,
}  # G1 thresholds, frozen from the 2020-22 permutation null (rule line 6)
STAR_CUT = {"pts": 15.1289, "ast": 3.4512}
HEAVY_CUT = {"pts": 45.0087, "ast": 10.4241}
WINDOW_START = dt.date(2019, 10, 1)
ARMS = ("A0", "ROLE", "S_R", "P_R", "T_R", "TP_R", "TQ", "RAW")
FIT_ARMS = ARMS[1:]
WORDS = ("PASS", "FAIL_GUARD", "FAIL_ONE_SEASON", "FAIL_NO_GAIN")
TWIST_LABELS = ("TWIST_BETTER", "TWIST_TIE", "TWIST_WORSE")
GATED_SLICES = (
    "bench",
    "starter",
    "n_out_rot=1",
    "n_out_rot>=2",
    "star_out",
    "first15",
    "playoffs",
)
REPORTED_SLICES = ("heavy_absence",)
DESC_PAIRS = (
    ("T_R", "A0"),
    ("T_R", "S_R"),
    ("T_R", "ROLE"),
    ("T_R", "TQ"),
    ("T_R", "TP_R"),
    ("T_R", "RAW"),
    ("ROLE", "A0"),
    ("S_R", "A0"),
)
OUT_DIR = Path(__file__).resolve().parents[3] / "reports/prereg_wowy_struct"
CACHE_REL = "data/ops/wowy_struct_cache"
F1_CACHE_REL = "data/ops/f1_warm_window_cache"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------------------------ inference (house code path: _clustered_boot_means)
def delta1(
    delta: np.ndarray, cluster: np.ndarray, n_boot: int = N_BOOT, seed: int = SEED
) -> dict[str, float]:
    """Mean paired delta, 95% percentile CI and the ONE-SIDED bootstrap p = (1 + #{draws >= 0}) / (1 + n_boot), clustered by ``cluster``
    (draws: ``nba.props.metrics._clustered_boot_means`` with ``default_rng(seed)``)."""
    d = np.asarray(delta, dtype=float)
    draws = _clustered_boot_means(d, np.asarray(cluster), n_boot, np.random.default_rng(seed))
    return {
        "delta": float(d.mean()),
        "ci_lo": float(np.quantile(draws, ALPHA / 2)),
        "ci_hi": float(np.quantile(draws, 1 - ALPHA / 2)),
        "p": float((1 + int((draws >= 0).sum())) / (1 + n_boot)),
        "n_rows": int(len(d)),
        "n_clusters": int(len(np.unique(cluster))),
    }


def coverage(pit: np.ndarray, mask: np.ndarray) -> dict[str, float]:
    return {f"{t:.2f}": float((pit[mask] < t).mean()) for t in COV_TAUS}


def g3_coverage(cov_a0: dict[str, float], cov_sr: dict[str, float]) -> bool:
    return all(abs(cov_sr[k] - cov_a0[k]) <= COV_TOL for k in cov_a0)


def twist_label(h2h: list[dict[str, float]]) -> str:
    """DESCRIPTIVE label of T_R minus S_R, both seasons: BETTER (point <= TWIST_FLOOR and CI upper < 0), WORSE (point >= -TWIST_FLOOR and CI lower > 0), else TIE."""
    if all(r["delta"] <= TWIST_FLOOR and r["ci_hi"] < 0 for r in h2h):
        return "TWIST_BETTER"
    if all(r["delta"] >= -TWIST_FLOOR and r["ci_lo"] > 0 for r in h2h):
        return "TWIST_WORSE"
    return "TWIST_TIE"


def evaluate(
    bundle: dict[str, Any], gates_ok: bool = True, repro_ok: bool = True
) -> dict[str, Any]:
    """The frozen decision. ``bundle["stats"][stat]`` carries aligned arrays on the A0 row set: ``gid, date, season``, ``score[arm]`` (per-row integer CRPS for
    ARMS), ``pit[arm]`` for A0 and S_R, ``cells[name]`` (cell membership, bool), ``slices[name]`` (GATED_SLICES, bool), ``reported_slices[name]``.
    Per cell and season: clears = S_R minus ROLE <= FLOOR, CI upper < 0 (game-clustered), BH p <= ALPHA over the 3 cells of the season (one-sided p);
    guards G1 (S_R minus P_R <= -t_c), G2 (S_R minus A0 on all rows <= +NOHARM, point), G3 (coverage within COV_TOL of A0 at every tau, all rows),
    G5 (gated slices with n >= MIN_SLICE_N: S_R minus ROLE <= +SLICE_TOL), G9 (the primary also clears on games NOT stamp-exposed, BEST_PRACTICES 2026-10-15:
    point <= FLOOR and CI upper < 0 on the cell rows of games that are NOT stamp-exposed, BH not re-applied; the F1_WARM_WINDOW G9). G4 (planted future, NULL rule) is a STOP before any fit, never a word.
    Cell words: PASS (clears in both seasons, guards hold in both) > FAIL_GUARD (clears in both, a guard fails) > FAIL_ONE_SEASON (clears in exactly one) > FAIL_NO_GAIN.
    Overall word = the best cell word in that order. The twist arms are descriptive: ``desc`` deltas and ``twist`` labels never change a word."""
    if not (gates_ok and repro_ok):
        raise RuntimeError(
            "STOP: a gate item or a reproduction check failed; no verdict is printed"
        )
    rows: list[dict[str, Any]] = []
    for s in SEASONS:
        recs = []
        for st, sn in CELLS:
            b = bundle["stats"][st]
            key = f"{st}|{sn}"
            m_all = b["season"] == s
            m = m_all & b["cells"][sn]
            sc = b["score"]
            r: dict[str, Any] = {"cell": key, "season": s, "stat": st}
            r["primary"] = delta1(sc["S_R"][m] - sc["ROLE"][m], b["gid"][m])
            r["primary_date"] = {
                k: delta1(sc["S_R"][m] - sc["ROLE"][m], b["date"][m])[k] for k in ("ci_lo", "ci_hi")
            }
            r["g1_t_c"] = T_C[key]
            r["g1_delta"] = float((sc["S_R"][m] - sc["P_R"][m]).mean())
            r["g1"] = bool(r["g1_delta"] <= -T_C[key])
            r["g2_delta"] = float((sc["S_R"][m_all] - sc["A0"][m_all]).mean())
            r["g2"] = bool(r["g2_delta"] <= NOHARM)
            r["coverage"] = {a: coverage(b["pit"][a], m_all) for a in ("A0", "S_R")}
            r["g3"] = g3_coverage(r["coverage"]["A0"], r["coverage"]["S_R"])
            sl: dict[str, Any] = {}
            for name in GATED_SLICES:
                mm = m & b["slices"][name]
                n = int(mm.sum())
                sl[name] = {
                    "n": n,
                    "delta": float((sc["S_R"][mm] - sc["ROLE"][mm]).mean()) if n else None,
                    "gated": n >= MIN_SLICE_N,
                }
            r["slices"] = sl
            r["g5_bad"] = [k for k, v in sl.items() if v["gated"] and v["delta"] > SLICE_TOL]
            r["g5"] = not r["g5_bad"]
            r["reported_slices"] = {
                name: {
                    "n": int((m & b["reported_slices"][name]).sum()),
                    "delta": float(
                        (
                            sc["S_R"][m & b["reported_slices"][name]]
                            - sc["ROLE"][m & b["reported_slices"][name]]
                        ).mean()
                    )
                    if (m & b["reported_slices"][name]).any()
                    else None,
                }
                for name in REPORTED_SLICES
            }
            mu = m & b["unexposed"]
            r["unexposed"] = delta1(sc["S_R"][mu] - sc["ROLE"][mu], b["gid"][mu])
            r["g9"] = bool(r["unexposed"]["delta"] <= FLOOR and r["unexposed"]["ci_hi"] < 0)
            r["guards_ok"] = bool(r["g1"] and r["g2"] and r["g3"] and r["g5"] and r["g9"])
            r["desc"] = {
                f"{a}-{c}": {
                    k: v for k, v in delta1(sc[a][m] - sc[c][m], b["gid"][m]).items() if k != "p"
                }
                for a, c in DESC_PAIRS
            }  # descriptive: no p-value is carried (review E5)
            me = m & ~b["unexposed"]
            r["exposed"] = (
                {
                    k: v
                    for k, v in delta1(sc["S_R"][me] - sc["ROLE"][me], b["gid"][me]).items()
                    if k != "p"
                }
                if me.any()
                else None
            )  # reported only: S_R minus ROLE on the stamp-exposed games (review E2c)
            recs.append(r)
        padj = F1R.bh_adjust(np.array([r["primary"]["p"] for r in recs]))
        for r, a in zip(recs, padj, strict=True):
            r["p_bh"] = float(a)
            r["clears"] = bool(
                r["primary"]["delta"] <= FLOOR and r["primary"]["ci_hi"] < 0 and r["p_bh"] <= ALPHA
            )
        rows += recs
    by = {(r["cell"], r["season"]): r for r in rows}
    cells_out: dict[str, Any] = {}
    for key in CELL_KEYS:
        r23, r24 = by[(key, 2023)], by[(key, 2024)]
        both = r23["clears"] and r24["clears"]
        if both and r23["guards_ok"] and r24["guards_ok"]:
            word = "PASS"
        elif both:
            word = "FAIL_GUARD"
        elif r23["clears"] or r24["clears"]:
            word = "FAIL_ONE_SEASON"
        else:
            word = "FAIL_NO_GAIN"
        cells_out[key] = {
            "word": word,
            "clears_2023": r23["clears"],
            "clears_2024": r24["clears"],
            "guards_ok_2023": r23["guards_ok"],
            "guards_ok_2024": r24["guards_ok"],
            "twist": twist_label([r23["desc"]["T_R-S_R"], r24["desc"]["T_R-S_R"]]),
        }
    order = {w: i for i, w in enumerate(WORDS)}
    overall = min((v["word"] for v in cells_out.values()), key=lambda w: order[w])
    return {
        "word": overall,
        "verdict": "PASS" if overall == "PASS" else "FAIL",
        "cells": cells_out,
        "rows": rows,
    }


# ------------------------------------------------------------------------------------ arms: columns, permutations, the walk
def arm_columns(arm: str, stat: str) -> list[str]:
    """Columns an arm adds to the ``stat`` head (all but A0 carry the role share)."""
    role, col = f"role_share_{stat}", CHOSEN[stat]
    rb, rx, rr = f"rb_{stat}", f"rx_{stat}", f"rr_{stat}"
    return {
        "ROLE": [role],
        "S_R": [role, col],
        "P_R": [role, f"_perm_{col}"],
        "T_R": [role, rb, rx, "ro_conf"],
        "TP_R": [role, f"_perm_{rb}", f"_perm_{rx}", "_perm_ro_conf"],
        "TQ": [role, f"pq_{rb}", f"pq_{rx}", "pq_ro_conf"],
        "RAW": [role, rr, "ro_conf"],
    }[arm]


def add_permuted(frame: pl.DataFrame, stat: str) -> pl.DataFrame:
    """Permuted copies ``_perm_<col>`` of S_R's column and T_R's columns: ONE draw, ``default_rng(0)`` created fresh per season, within player-season, groups in
    ``np.unique`` key order (``wowy_struct_crps_null.perm_player_season``), rows ordered by (game_date, game_id, player_id) first; the columns of one arm share the
    permutation (they stay a coherent set). NULLs move with their row index like any value."""
    cols = [CHOSEN[stat], f"rb_{stat}", f"rx_{stat}", "ro_conf"]
    f = frame.sort(["game_date", "game_id", "player_id"])
    idx = np.arange(f.height, dtype=float)
    new = idx.copy()
    season = f["season"].to_numpy()
    pid = f["player_id"].to_numpy()
    for s in np.unique(season):
        m = np.flatnonzero(season == s)
        rng = np.random.default_rng(0)
        new[m] = NU.perm_player_season(idx[m], pid[m], season[m], rng)
    take = new.astype(int)
    return f.with_columns(
        [pl.Series(f"_perm_{c}", f[c].to_numpy().astype(float)[take]) for c in cols]
    )


def collect_heads_x(
    frame: pl.DataFrame,
    stat: str,
    cfg: Any,
    extra: list[str],
    test_seasons: tuple[int, ...] = SEASONS,
    log: bool = True,
) -> dict[str, np.ndarray]:
    """``f1_warm_window_run.collect_heads`` with ``extra`` columns appended to the head's feature names (identical walk: month blocks, production model,
    calibration split, covariates; with ``extra = []`` it equals F1's walk: the label-free block-0 smoke checks that against F1's arm-C cache)."""
    if max(test_seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    sf = stat_frame(frame, stat)
    names = [*stat_feature_names(stat), *extra]
    test_all = sf.filter(pl.col("season").is_in(list(test_seasons)))
    rows: dict[str, list[np.ndarray]] = {
        k: [] for k in ("gid", "pid", "season", "y", "c", "s", "blk", *ROW_COVARIATES)
    }
    cal: dict[str, list[np.ndarray]] = {
        k: [] for k in ("y", "c", "s", "z", "blk", "minutes", "min10", "m")
    }
    blocks = month_blocks(test_all)
    for b, (start, end) in enumerate(blocks):
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(test_seasons))
        )
        train = sf.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train.height < F1R.MIN_TRAIN:
            continue
        t0 = time.time()
        model = ContextResidualModel(stat, cfg, names).fit(train)
        c, s = model.predict_components(block)
        n = block.height
        rows["gid"].append(block["game_id"].to_numpy().astype(str))
        rows["pid"].append(block["player_id"].to_numpy().astype(np.int64))
        rows["season"].append(block["season"].to_numpy())
        rows["y"].append(block["y"].to_numpy())
        rows["c"].append(c)
        rows["s"].append(s)
        rows["blk"].append(np.full(n, b))
        for k in ROW_COVARIATES:
            rows[k].append(block[k].cast(pl.Float64).to_numpy())
        _, cal_df = split_calibration(train, cfg)
        xc = cal_df.select([pl.col(k).cast(pl.Float64) for k in names]).to_numpy()
        mu = np.asarray(model.mean_head.predict(xc))
        sc = model._scale(xc)
        cal["y"].append(cal_df["y"].to_numpy())
        cal["c"].append(cal_df["m"].to_numpy() + mu)
        cal["s"].append(sc)
        cal["z"].append((cal_df["resid"].to_numpy() - mu) / sc)
        cal["blk"].append(np.full(cal_df.height, b))
        for k in ("minutes", "min10", "m"):
            cal[k].append(cal_df[k].cast(pl.Float64).to_numpy())
        if log:
            _log(
                f"{stat} block {b + 1}/{len(blocks)} {start} train {train.height} test {n} {time.time() - t0:.0f}s ({len(extra)} extra columns)"
            )
    if not rows["c"]:
        raise ValueError("no month block had enough training rows")
    out = {f"row_{k}": np.concatenate(v) for k, v in rows.items()}
    out.update({f"cal_{k}": np.concatenate(v) for k, v in cal.items()})
    return out


def training_frame(root: Path, world: dict[str, Any]) -> pl.DataFrame:
    """F1's warm frame (history + nba.duckdb, F1's ``_frame``: features plus the realised box columns, pinned configs) windowed to 2019-10-01, with the STRUCT /
    STRUCT_REPL columns joined on (game_id, player_id). The row set must not change."""
    base = F1R._frame(root, ["data/history/nba_history.duckdb", "nba.duckdb"])
    base = F1R.windowed(base, "C").with_columns(pl.col("player_id").cast(pl.Int64))
    extra_cols = [
        c
        for c in world["fr"].columns
        if c.startswith(
            (
                "struct_",
                "leader_out_",
                "role_share_",
                "V_",
                "Vmax_",
                "rb_",
                "rx_",
                "rr_",
                "pq_",
                "ro_conf",
            )
        )
    ]
    j = base.join(
        world["fr"].select("game_id", "player_id", *extra_cols),
        on=["game_id", "player_id"],
        how="left",
    )
    if j.height != base.height:
        raise RuntimeError(
            f"attaching the WOWY_STRUCT columns changed the rows: {base.height} -> {j.height}"
        )
    miss = int(j["role_share_pts"].is_null().sum())
    if miss > 0 and not bool(
        j.filter(pl.col("has_report") != 1)["role_share_pts"].is_null().sum() == miss
    ):
        raise RuntimeError(
            "a reported row lost its STRUCT columns in the join: the NULL pattern is not the report's"
        )
    return j


# ------------------------------------------------------------------------------------ the bundle
def cell_and_slice_masks(
    fr: pl.DataFrame, stat: str, gid: np.ndarray, pid: np.ndarray
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Cell masks, gated slices and reported slices at the evaluation keys, from feature columns only. Cells use the fixed cut-offs of ``struct_warm`` (STRUCT gate json)."""
    stored = json.loads(WG.STRUCT_GATE.read_text())["struct_warm"]
    cuts = tuple(stored["fixed_cutoffs_2020_21(|struct| quantiles 0.75 / 0.90)"][stat])
    keys = pl.DataFrame({"game_id": gid.astype(str), "player_id": pid.astype(np.int64)})
    cols = [
        "game_id",
        "player_id",
        "vac_min",
        "has_report",
        "n_out_rot",
        "last_starter",
        "team_game_no",
        CHOSEN[stat],
        f"leader_out_{stat}",
        f"Vmax_{stat}",
        f"V_{stat}",
    ]
    j = keys.join(fr.select(cols), on=["game_id", "player_id"], how="left")
    if j.height != keys.height or j["team_game_no"].null_count() > 0:
        raise RuntimeError("an evaluation key is missing from the feature frame")
    j = j.with_columns(
        pl.col("vac_min").fill_null(0.0),
        pl.col("n_out_rot").fill_null(0),
        pl.col("has_report").fill_null(0),
        pl.col(CHOSEN[stat]).fill_null(0.0),
        pl.col(f"leader_out_{stat}").fill_null(0),
        pl.col(f"Vmax_{stat}").fill_null(0.0),
        pl.col(f"V_{stat}").fill_null(0.0),
    )
    cells = WG.SG.cell_masks(j, CHOSEN[stat], f"leader_out_{stat}", cuts)
    ls = j["last_starter"].to_numpy().astype(float)
    n_out = j["n_out_rot"].to_numpy()
    slices = {
        "bench": ls == 0,
        "starter": ls == 1,
        "n_out_rot=1": n_out == 1,
        "n_out_rot>=2": n_out >= 2,
        "star_out": j[f"Vmax_{stat}"].to_numpy() >= STAR_CUT[stat],
        "first15": j["team_game_no"].to_numpy() < 15,
        "playoffs": np.char.startswith(gid.astype(str), "004"),
    }
    reported = {"heavy_absence": j[f"V_{stat}"].to_numpy() >= HEAVY_CUT[stat]}
    return cells, slices, reported


def build_bundle(
    comps: dict[str, dict[str, dict[str, np.ndarray]]],
    pic_c: dict[str, np.ndarray],
    fr: pl.DataFrame,
    game_dates: dict[str, str],
    exposed: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Per-row scores / PIT / masks of every arm on A0's (arm C's) rows. ``comps[stat][arm]`` heads dicts; ``A0`` is F1's cache. Integer grid for every arm; arm rows are
    aligned to A0's keys, the outcomes must be identical on the common keys."""
    out: dict[str, Any] = {"stats": {}}
    for st in STATS:
        ca = comps[st]["A0"]
        gid, pid = ca["row_gid"].astype(str), ca["row_pid"].astype(np.int64)
        y = ca["row_y"]
        u = np.random.default_rng(SEED).random(len(y))
        cells, slices, reported = cell_and_slice_masks(fr, st, gid, pid)
        rec: dict[str, Any] = {
            "gid": gid,
            "pid": pid,
            "season": ca["row_season"],
            "date": np.array([game_dates[g] for g in gid]),
            "score": {},
            "pit": {},
            "cells": {sn: cells[sn] for sn in ("leader_out", "top25_fixed")},
            "slices": slices,
            "reported_slices": reported,
            "unexposed": ~np.isin(gid, list(exposed)),
        }
        for arm in ARMS:
            c = comps[st][arm]
            idx = F1R.align_index(gid, pid, c["row_gid"], c["row_pid"])
            if not np.array_equal(c["row_y"][idx], y):
                raise RuntimeError(f"{st} arm {arm}: outcomes differ from A0 on the common keys")
            qi = F1R.qi_of(c, pic_c, st)[idx]
            rec["score"][arm] = F1R.crps_int(qi, y)
            if arm in ("A0", "S_R"):
                rec["pit"][arm] = F1R.pit_from_qi(qi, y, u)
        out["stats"][st] = rec
    return out


# ------------------------------------------------------------------------------------ synthetic rehearsal (no data, no fit)
def synthetic_bundle(world: str, seed: int = 0) -> dict[str, Any]:
    """Per-row synthetic scores with game-clustered noise at the widths the gate sized (cell half-widths ~0.011 pts, ~0.006 ast) and a known true effect, to drive
    ``evaluate``. Worlds: ``pass`` (S_R beats ROLE by 0.015 pts / 0.012 ast in every cell and season, P_R keeps 15% of it), ``guard`` (the same numbers, but P_R keeps
    90%: G1 fires), ``one_season`` (the effect exists in 2023 only), ``no_gain`` (none). The twist arms: T_R sits 0.003 worse than S_R in every world except ``pass``
    where T_R = S_R (a tie). Not a fit and not data: it rehearses the decision pipeline only."""
    rng = np.random.default_rng(seed)
    n_games, per = 1300, 21
    seasons = np.concatenate([np.full(n_games * per, s) for s in SEASONS])
    game = np.concatenate([np.repeat(np.arange(n_games), per) for _ in SEASONS])
    gid = np.char.add(seasons.astype(str), np.char.zfill(game.astype(str), 5))
    date = np.char.add(seasons.astype(str), np.char.zfill((game // 8 % 200).astype(str), 3))
    n = len(gid)
    out: dict[str, Any] = {"stats": {}}
    frac_p = {"pass": 0.15, "guard": 0.90, "one_season": 0.15, "no_gain": 0.0}[world]
    for st in STATS:
        scale = 0.348 if st == "pts" else 0.205
        cells = {
            "leader_out": rng.random(n) < (0.16 if st == "pts" else 0.18),
            "top25_fixed": rng.random(n) < 0.13,
        }
        if st == "ast":
            cells["top25_fixed"] = np.zeros(n, dtype=bool)
        base = 1.5 + 0.2 * rng.standard_normal(n)
        gnoise = (
            np.concatenate([rng.standard_normal(n_games)[game[seasons == s]] for s in SEASONS])
            * 0.07
            * scale
        )
        eff_size = 0.015 if st == "pts" else 0.012
        in_cell = np.zeros(n, dtype=bool)
        for sn in ("leader_out", "top25_fixed"):
            in_cell |= cells[sn]
        season_on = np.ones(n, dtype=bool) if world != "one_season" else (seasons == 2023)
        eff = np.where(in_cell & season_on & (world != "no_gain"), -eff_size, 0.0)
        noise = gnoise + scale * 0.2 * rng.standard_normal(n)
        role = base + 0.0003 * rng.standard_normal(n)
        s_r = role + eff + noise
        p_r = role + frac_p * eff + 0.5 * noise + 0.3 * scale * 0.2 * rng.standard_normal(n)
        twist_off = 0.0 if world == "pass" else 0.003
        t_r = s_r + twist_off + 0.1 * scale * rng.standard_normal(n)
        score = {
            "A0": base,
            "ROLE": role,
            "S_R": s_r,
            "P_R": p_r,
            "T_R": t_r,
            "TP_R": role + 0.1 * eff,
            "TQ": role + 0.1 * eff,
            "RAW": role,
        }
        pit_a0 = rng.random(n)
        pit_sr = pit_a0.copy()
        out["stats"][st] = {
            "gid": gid,
            "pid": np.arange(n),
            "season": seasons,
            "date": date,
            "score": score,
            "pit": {"A0": pit_a0, "S_R": pit_sr},
            "cells": cells,
            "slices": {
                "bench": rng.random(n) < 0.5,
                "starter": rng.random(n) < 0.5,
                "n_out_rot=1": rng.random(n) < 0.05,
                "n_out_rot>=2": rng.random(n) < 0.9,
                "star_out": rng.random(n) < 0.9,
                "first15": rng.random(n) < 0.17,
                "playoffs": rng.random(n) < 0.02,
            },
            "reported_slices": {"heavy_absence": rng.random(n) < 0.5},
            "unexposed": rng.random(n) > 0.05,
        }
    return out


def rehearse(world: str, seeds: int) -> dict[str, Any]:
    worlds = ("pass", "guard", "one_season", "no_gain") if world == "all" else (world,)
    res: dict[str, Any] = {}
    for w in worlds:
        words: dict[str, int] = {}
        twist: dict[str, int] = {}
        for sd in range(seeds):
            ev = evaluate(synthetic_bundle(w, sd))
            words[ev["word"]] = words.get(ev["word"], 0) + 1
            for v in ev["cells"].values():
                twist[v["twist"]] = twist.get(v["twist"], 0) + 1
        res[w] = {"seeds": seeds, "words": words, "twist_labels_over_cells": twist}
        _log(f"rehearsal {w}: {words} twist {twist}")
    return res


# ------------------------------------------------------------------------------------ pinned inputs and the label-free preflight
def sha256_file(p: Path) -> str:
    return F1R.sha256_file(p)


def libs_ok() -> dict[str, str]:
    return F1R.require_versions()


def input_hashes(root: Path) -> dict[str, str]:
    cache = root / F1_CACHE_REL
    out = {
        "C_SHA256_PTS": sha256_file(cache / "C_pts.npz"),
        "C_SHA256_AST": sha256_file(cache / "C_ast.npz"),
        "C_SHA256_PIC": sha256_file(cache / "C_pic.npz"),
        "GATE_SHA256": sha256_file(rule.ROOT / "reports/prereg_wowy_repl/gate.json"),
        "STRUCT_GATE_SHA256": sha256_file(
            rule.ROOT / "reports/prereg_wowy_redistribution/gate.json"
        ),
        "F1_GATE_SHA256": sha256_file(rule.ROOT / "reports/prereg_f1_warm_window/gate.json"),
    }
    f1 = F1R.input_hashes(root)
    for k in (
        "HISTORY_DB_SHA256",
        "FP_NBA_LE2024",
        "FP_HISTORY_LE2024",
        "FP_STATIC_MERGED",
        "FP_SCHEDULE_LE2024",
    ):
        out[k] = f1[k]
    return out


def c_tags(root: Path, declared: dict[str, str]) -> dict[str, str]:
    """F1's arm-C cache must be the frozen F1 run: tag = ``<F1 rule sha>:<F1 harness pin>`` as pinned in this rule (reads the tag only, no array)."""
    want = f"{declared['F1_RULE_SHA256']}:{declared['F1_PIN_SHA256']}"
    got = {}
    for name in ("C_pts", "C_ast", "C_pic"):
        z = np.load(root / F1_CACHE_REL / f"{name}.npz", allow_pickle=True)
        got[name] = str(z["_tag"])
        if got[name] != want:
            raise RuntimeError(
                f"{name}.npz tag {got[name][:20]} is not the frozen F1 run {want[:20]}"
            )
    return got


def preflight(
    root: Path, declared: dict[str, str], allow_undeclared: bool = False
) -> dict[str, Any]:
    """Label-free: library versions equal the pinned set; every input hash equals the line pinned in the rule; F1's cache is the frozen F1 run. No outcome array is read."""
    ver = libs_ok()
    got = input_hashes(root)
    bad = {
        k: (v[:12], declared.get(k, "missing")[:12]) for k, v in got.items() if declared.get(k) != v
    }
    if bad and not allow_undeclared:
        raise RuntimeError(f"input hashes differ from the rule's pinned lines (got, pinned): {bad}")
    tags = c_tags(root, declared) if "F1_RULE_SHA256" in declared else {}
    return {"versions": ver, "hashes": got, "c_tags": tags, "mismatch_vs_rule": bad}


def stored_counts_check(world: dict[str, Any], root: Path) -> dict[str, Any]:
    """Cell rows and games of the A0 row set (feature columns only) must equal the counts in the committed gate.json (``power`` block)."""
    gate = json.loads((rule.ROOT / "reports/prereg_wowy_repl/gate.json").read_text())
    z = {st: np.load(root / F1_CACHE_REL / f"C_{st}.npz", allow_pickle=True) for st in STATS}
    out: dict[str, Any] = {}
    for st, sn in CELLS:
        gid, pid, season = (
            z[st]["row_gid"].astype(str),
            z[st]["row_pid"].astype(np.int64),
            z[st]["row_season"],
        )
        cells, _, _ = cell_and_slice_masks(world["fr"], st, gid, pid)
        for s in SEASONS:
            m = cells[sn] & (season == s)
            have = {"rows": int(m.sum()), "games": int(np.unique(gid[m]).size)}
            gp = gate["power"]["cells"][f"{st}|{sn}|{s}|STRUCT"]
            want = {"rows": gp["rows"], "games": gp["games"]}
            out[f"{st}|{sn}|{s}"] = {"have": have, "gate": want}
            if have != want:
                raise RuntimeError(f"{st}|{sn}|{s}: counts {have} != gate.json {want}")
    return out


def smoke_block0(root: Path, frame: pl.DataFrame, cfg: Any, stat: str = "pts") -> dict[str, Any]:
    """Label-free: the walk with NO extra column on the first block (2023-10) equals F1's arm-C cache for that block (heads centre and scale, exactly). Outcomes are
    dropped before comparing; no score, delta or statistic of an outcome is computed."""
    fr = frame.filter(pl.col("game_date") < dt.date(2023, 11, 1))
    t0 = time.time()
    comp = collect_heads_x(fr, stat, cfg, [], test_seasons=(2023,), log=False)
    comp.pop("row_y")
    z = np.load(root / F1_CACHE_REL / f"C_{stat}.npz", allow_pickle=True)
    m = z["row_blk"] == 0
    gid_c, pid_c = z["row_gid"][m].astype(str), z["row_pid"][m].astype(np.int64)
    idx = F1R.align_index(gid_c, pid_c, comp["row_gid"], comp["row_pid"])
    d_c = float(np.abs(comp["row_c"][idx] - z["row_c"][m]).max())
    d_s = float(np.abs(comp["row_s"][idx] - z["row_s"][m]).max())
    return {
        "stat": stat,
        "rows": int(m.sum()),
        "max_abs_diff": {"row_c": d_c, "row_s": d_s},
        "pass": bool(max(d_c, d_s) <= F1R.HEADS_TOL),
        "seconds": round(time.time() - t0, 1),
    }


# ------------------------------------------------------------------------------------ the run
def _ckpt(root: Path, arm: str, stat: str) -> Path:
    return root / CACHE_REL / f"{arm}_{stat}.npz"


def fit_arms(
    root: Path, frame: pl.DataFrame, cfg: Any, tag: str
) -> dict[str, dict[str, dict[str, np.ndarray]]]:
    comps: dict[str, dict[str, dict[str, np.ndarray]]] = {}
    for st in STATS:
        fs = add_permuted(frame, st)
        comps[st] = {}
        for arm in FIT_ARMS:
            c = F1R._load(_ckpt(root, arm, st), tag)
            if c is None:
                _log(f"fitting {st} arm {arm}")
                c = collect_heads_x(fs, st, cfg, arm_columns(arm, st))
                F1R._save(_ckpt(root, arm, st), tag, c)
            else:
                _log(f"{st} arm {arm}: from checkpoint")
            comps[st][arm] = c
    return comps


def load_a0(
    root: Path, declared: dict[str, str]
) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, np.ndarray]]:
    c_tags(root, declared)
    comp: dict[str, dict[str, np.ndarray]] = {}
    for st in STATS:
        z = np.load(root / F1_CACHE_REL / f"C_{st}.npz", allow_pickle=True)
        comp[st] = {k: z[k] for k in z.files if k != "_tag"}
    zp = np.load(root / F1_CACHE_REL / "C_pic.npz", allow_pickle=True)
    pic = {k: zp[k] for k in zp.files if k != "_tag"}
    return comp, pic


def run_real(root: Path, sha: str) -> dict[str, Any]:
    if Path.cwd().resolve() != rule.ROOT:
        raise RuntimeError(f"cwd must be the pinned checkout {rule.ROOT}")
    blobs = rule.require_ready(sha)
    pin = rule.pin_of(blobs)
    _log(f"rule frozen and harness pinned: {pin[:12]}")
    declared = rule.declared()
    pre = preflight(root, declared)
    _log(
        "preflight: library versions, every pinned input hash and F1's arm-C tag equal; no outcome scored"
    )
    # taken BEFORE the world and frame build: the first CPU-heavy step (review E9)
    lock = F1R.acquire_lock(root)
    try:
        return _run_locked(root, sha, pin, declared, pre)
    finally:
        lock.rmdir()


def _run_locked(
    root: Path, sha: str, pin: str, declared: dict[str, str], pre: dict[str, Any]
) -> dict[str, Any]:
    t0 = time.time()
    world = WG.build_world(t0, first_season=2019, root=root)
    counts = stored_counts_check(world, root)
    if WG.priors_sha(world["pri"]) != declared["PRIORS_SHA256"]:
        raise RuntimeError(
            "replacement rates / prior strengths differ from the PRIORS_SHA256 line of the rule"
        )
    planted = WG.planted_future(world, t0)
    if not planted["pass"]:
        raise RuntimeError(
            f"STOP before any arm is fitted: planted-future test failed (G4): {planted}"
        )
    _log("counts equal gate.json, priors pinned, planted-future holds; building the training frame")
    cfg = ContextResidualConfig(
        **yaml.safe_load((rule.ROOT / "configs/context_residual.yaml").read_text()).get("model", {})
    )
    if cfg.report.tip_source != "real":
        raise RuntimeError("tip_source must be 'real' (no proxy tips)")
    frame = training_frame(root, world)
    chk = smoke_block0(root, frame, cfg)
    if not chk["pass"]:
        raise RuntimeError(
            f"STOP before any arm is fitted: the A0 walk does not equal F1's arm-C cache on block 0: {chk}"
        )
    _log("A0 walk equals F1's arm C on block 0 (max abs diff 0.0)")
    comps = fit_arms(root, frame, cfg, tag=f"{sha}:{pin}")
    comp0, pic_c = load_a0(root, declared)
    for st in STATS:
        comps[st]["A0"] = comp0[st]
    games = frame.select("game_id", "game_date").unique()
    gd = {g: str(d) for g, d in zip(games["game_id"], games["game_date"], strict=True)}
    bundle = build_bundle(comps, pic_c, world["fr"], gd, F1R.exposed_games(root))
    res = evaluate(bundle, gates_ok=True, repro_ok=True)
    res.update(
        {
            "preflight": pre,
            "counts": counts,
            "planted_future": planted,
            "smoke_block0": chk,
            "rule_sha": sha,
            "pin": pin,
            "versions": F1R.versions(),
        }
    )
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("check", "rehearse", "run"))
    ap.add_argument("--data-root", default=None)
    ap.add_argument("--sha", default=None)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--world", default="all")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    t0 = time.time()
    out = Path(a.out) if a.out else OUT_DIR
    if a.cmd == "rehearse":
        res = rehearse(a.world, a.seeds)
        fn = "rehearsal.json"
    else:
        root = Path(a.data_root or WG.REAL).resolve()
        if a.cmd == "check":
            res = preflight(root, rule.declared(), allow_undeclared=True)
            world = WG.build_world(t0, first_season=2019, root=root)
            res["counts"] = stored_counts_check(world, root)
            res["priors_sha256"] = WG.priors_sha(world["pri"])
            res["priors_match_rule"] = res["priors_sha256"] == rule.declared().get("PRIORS_SHA256")
            res["planted_future"] = WG.planted_future(world, t0)
            if a.smoke:
                cfg = ContextResidualConfig(
                    **yaml.safe_load((rule.ROOT / "configs/context_residual.yaml").read_text()).get(
                        "model", {}
                    )
                )
                res["smoke_block0"] = smoke_block0(root, training_frame(root, world), cfg)
            fn = "check.json"
        else:
            res = run_real(root, a.sha or "")
            fn = "results.json"
    res["wall_seconds"] = round(time.time() - t0, 1)
    out.mkdir(parents=True, exist_ok=True)
    (out / fn).write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    _log(f"wrote {out / fn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
