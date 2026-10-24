# ruff: noqa: E501
"""MARKET_ANCHORED_PROPS eval (rule: docs/prereg/MARKET_ANCHORED_PROPS.md, frozen f7691bbf; constants in market_anchored_props_rule).

    .venv/bin/python -W ignore -m research.eval.market_anchored_props_eval extract --db <byte copy of odds_history.duckdb>
    .venv/bin/python -W ignore -m research.eval.market_anchored_props_eval dryrun --db <byte copy of odds_history.duckdb>
    .venv/bin/python -W ignore -m research.eval.market_anchored_props_eval run --sha <64 hex>

``extract`` (label-free, once, before the harness pin; eval review 2026-10-24 F2): writes the Part L FanDuel rungs and the exchange
quotes of the primary legs from a byte copy of the odds database to committed parquet, whose sha256 are pinned in the rule mirror.
It refuses the live file and refuses to overwrite. ``run`` never opens the odds database.

``dryrun`` (rule header (a)): no real label is read. It (1) only hashes the committed extras frames against their pins (it never calls
``core.build_extras``; a call raises during the dryrun) and checks every other pin, (2) re-runs the committed gate with every synthetic
world sent through THIS module's stage code (``gate_world_record``: Stage 1 -> K1 -> Stage 2 -> run words -> verdict) and compares the
result with ``reports/prereg_market_anchored_props/gate.json`` key by key (only ``seconds``, ``git_head`` and the odds-database capture
counts, which the movement pull has since changed, are excluded; the capture diff is reported). The reproduction uses the core's own
weeks, as the gate measured them; ``run`` uses ISO weeks only. (3) It checks this module's planted future against the gate's and the
scored counts label-free, and (4) rehearses the whole pipeline on synthetic labels (ISO weeks, the pinned descriptive inputs), plus
planted K1, WORSE_THAN_MARKET, descriptive-failure and invalid-run worlds. Output ``dryrun.json`` (synthetic numbers only).

``run`` refuses (before any label is read) a DRAFT doc, a missing ``prereg(...)`` commit, a harness that differs from its blobs, a
missing harness pin commit, and any season > 2024. A pairs / extras / model_rows / extract pin failure, a leg or scored-count
reproduction failure (both checked label-free), or a planted-future failure records ``NO_RESULT_INVALID_RUN`` with both words
``NOT_EVALUATED`` and no number. The missingness table on real labels is written first (``missingness.json``). ``results.json`` is
written as soon as the words exist; each descriptive block then runs alone and a failure is recorded as FAILED (review F4).

Runtime note (review F8; the frozen section 15 is not edited): section 15's "5-10 minutes on CPU" is the compute. Measured here: the
full dryrun (gate reproduction of 100 worlds plus the full-pipeline rehearsal at K = 50 / B = 2,000 plus four small planted
worlds) took 738 s on 2026-10-23 and 296 s on 2026-10-24 with no lock wait, so ``run`` (one pipeline, no gate reproduction)
should compute well inside section 15's 5-10 minutes. The 5,808 s second dryrun spent about 90 minutes in 45 ``heavy.lock`` waits
(another job held it); that is lock time, not compute. Plan the run outside the nightly and morning windows and allow for the wait.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from research.eval import market_anchored_props_core as core
from research.eval import market_anchored_props_gate as gate
from research.eval import market_anchored_props_rule as R
from research.eval import market_prior_residual_eval as mpr
from research.eval import market_prior_residual_rule as mrule
from research.eval.model_clv_eval import Boot

OUT_DIR = R.ROOT / "reports/prereg_market_anchored_props"
MODEL_ROWS_REL = "reports/odds_history/model_rows_{season}.parquet"
LIVE_ODDS_REL = "data/odds/odds_history.duckdb"
KEY = ["game_id", "player_id", "stat"]


def _log(msg: str) -> None:
    print(f"[map-eval {time.strftime('%H:%M:%S')}] {msg}", flush=True)


class InvalidRun(RuntimeError):
    """A planted-future, leg-reproduction, scored-count or pin failure: NO_RESULT_INVALID_RUN, no number reported."""


# ------------------------------------------------------------------------------------------- lock


@contextmanager
def heavy_lock(purpose: str, retry_s: float = 120.0) -> Iterator[None]:
    """``mkdir data/ops/heavy.lock`` (retry every 120 s), pid in ``owner``; removed only if the pid is ours (rule section 14)."""
    lock = mpr.resolve("data/ops") / "heavy.lock"
    while True:
        try:
            lock.mkdir()
            (lock / "owner").write_text(f"{os.getpid()} {purpose}\n")
            break
        except FileExistsError:
            _log("heavy.lock held; retry in 120 s")
            time.sleep(retry_s)
    try:
        yield
    finally:
        try:
            if (lock / "owner").read_text().split()[0] == str(os.getpid()):
                (lock / "owner").unlink()
                lock.rmdir()
        except OSError:
            pass


# ------------------------------------------------------------------------------------------- inputs (label-free unless named)


def check_pairs_pins() -> dict[str, str]:
    out = {}
    for s, want in R.PAIRS_PINNED_SHA256.items():
        got = mrule.file_sha256(mpr.resolve(mpr.PAIRS_REL.format(season=s)))
        if got != want:
            raise InvalidRun(
                f"pairs_{s}.parquet sha256 {got[:12]} differs from its pin {want[:12]}"
            )
        out[str(s)] = got
    return out


def check_extras_pins() -> dict[str, str]:
    """Hash the committed extras frames against their pins. Never rebuilds them."""
    out = {}
    for s in (R.SELECT_SEASON, R.REPORT_SEASON):
        try:
            out[str(s)] = core.check_extras_pin(s, mpr.resolve(core.EXTRAS_REL.format(season=s)))
        except (RuntimeError, FileNotFoundError) as e:
            raise InvalidRun(str(e)) from e
    return out


def load_legs_checked() -> tuple[pl.DataFrame, dict[str, Any], dict[str, np.ndarray]]:
    """The primary legs (core.load_legs, label-free, extras pin checked inside) and their arrays; the leg reproduction is checked."""
    R.assert_seasons((R.SELECT_SEASON, R.REPORT_SEASON))
    try:
        legs, counts = core.load_legs()
    except RuntimeError as e:
        raise InvalidRun(str(e)) from e
    A = core.to_arrays(legs)
    check_leg_reproduction(A)
    return legs, counts, A


def leg_counts(A: dict[str, np.ndarray]) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {}
    for s in (R.SELECT_SEASON, R.REPORT_SEASON):
        k = A["season"] == s
        out[s] = {
            "pooled": int(k.sum()),
            **{st: int((k & (A["stat_i"] == i)).sum()) for i, st in enumerate(R.STATS)},
        }
    return out


def check_leg_reproduction(A: dict[str, np.ndarray]) -> None:
    got = leg_counts(A)
    if got != R.LEGS:
        raise InvalidRun(f"leg reproduction failed: {got} != {R.LEGS}")


def _with_row(legs: pl.DataFrame) -> pl.DataFrame:
    return legs.with_columns(pl.int_range(pl.len()).alias("_row"))


def _join_aligned(legs: pl.DataFrame, other: pl.DataFrame, on: list[str]) -> pl.DataFrame:
    """Left join that keeps the legs' order exactly (row index restored), refusing duplicate keys on the right."""
    if other.select(pl.struct(on).is_duplicated().any()).item():
        raise InvalidRun(f"duplicate keys on {on} in a joined frame")
    out = _with_row(legs).join(other, on=on, how="left").sort("_row")
    assert out.height == legs.height
    return out.drop("_row")


def pairs_columns(legs: pl.DataFrame, cols: list[str], book: str = mrule.BOOK) -> pl.DataFrame:
    """Extra columns of the pinned pairs frames for ``book``'s t60 rows, aligned to ``legs``."""
    parts = []
    for s in sorted(set(legs["season"].to_list())):
        R.assert_seasons([s])
        d = pl.read_parquet(
            mpr.resolve(mpr.PAIRS_REL.format(season=s)),
            columns=[*KEY, "line", "book", "snapshot_kind", *cols],
        )
        parts.append(
            d.filter(
                (pl.col("book") == book) & (pl.col("snapshot_kind") == mrule.SNAPSHOT_KIND)
            ).drop("book", "snapshot_kind")
        )
    on = [*KEY, "line"]
    return _join_aligned(legs.select(on), pl.concat(parts), on).select(cols)


def load_labels(legs: pl.DataFrame, book: str = mrule.BOOK) -> np.ndarray:
    """REAL labels: ``over`` at the leg's line (run only)."""
    y = pairs_columns(legs, ["over"], book)["over"].to_numpy().astype(float)
    if not np.isfinite(y).all():
        raise InvalidRun(f"{int((~np.isfinite(y)).sum())} legs without a label")
    return y


def model_rows(legs: pl.DataFrame, cols: list[str]) -> pl.DataFrame:
    """Columns of the ODDS_HISTORY model rows (the stored grid ``p_ge``, ``min10``; ``minutes`` and ``y`` are outcomes and are
    requested only by ``run``), aligned to ``legs``. Not pinned by the rule; their sha256 is recorded."""
    parts = []
    for s in sorted(set(legs["season"].to_list())):
        R.assert_seasons([s])
        d = pl.read_parquet(mpr.resolve(MODEL_ROWS_REL.format(season=s)), columns=[*KEY, *cols])
        parts.append(d.with_columns(pl.col("player_id").cast(pl.Int64)))
    return _join_aligned(legs.select(KEY), pl.concat(parts), KEY).select(cols)


def iso_week_idx(dates: np.ndarray) -> np.ndarray:
    """Dense codes of ``iso_year-week`` of ``game_date`` (rule section 14)."""
    d = np.asarray(dates, dtype="datetime64[D]")
    uniq, inv = np.unique(d, return_inverse=True)
    keys = np.array(
        [f"{c[0]}-{c[1]:02d}" for c in (dt.date.fromisoformat(str(u)).isocalendar() for u in uniq)]
    )
    return np.asarray(np.unique(keys[inv], return_inverse=True)[1], dtype=np.int64)


def with_week(A: dict[str, np.ndarray], week: str) -> dict[str, np.ndarray]:
    """``iso``: ISO weeks (Monday to Sunday), the frozen text's sections 4 and 14; ``run`` always takes this (ruling #1, F1).
    ``core``: the pinned core's weeks (numpy ``datetime64[W]``: Thursday to Wednesday); used only to reproduce gate.json."""
    if week == "core":
        return A
    if week == "iso":
        return {**A, "week_idx": iso_week_idx(A["date"])}
    raise ValueError(week)


# ------------------------------------------------------------------------------------------- stages and words


def check_walk_forward_ages(
    A: dict[str, np.ndarray], score_season: int, train: tuple[int, ...]
) -> None:
    """Section 14: ``time_weights`` must raise on age <= 0. The pinned core computes the weights inline; this runs the raising
    version on every fitted month first, so a fit row on or after its month's first day stops the run."""
    for mo in np.unique(A["month"][A["season"] == score_season]):
        start = mo.astype("datetime64[D]")
        tr = np.isin(A["season"], train) & (A["date"] < start)
        if tr.sum() >= R.N_MIN_TRAIN:
            mrule.time_weights(A["date"][tr], start)


def stage1(
    A: dict[str, np.ndarray], y: np.ndarray, b: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Stage 1 (select, 2023): expanding month-block fits within 2023, no placebo; first scored month has >= 3,000 prior legs."""
    check_walk_forward_ages(A, R.SELECT_SEASON, (R.SELECT_SEASON,))
    wf = core.walk_forward(A, y, R.SELECT_SEASON, (R.SELECT_SEASON,))
    return wf, core.decompose(A, wf, b=b)


def stage2(
    A: dict[str, np.ndarray], y: np.ndarray, perms: np.ndarray, b: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Stage 2 (report, 2024): each month fit on all 2023 legs plus earlier 2024 months, K placebo derangements, every month scored."""
    check_walk_forward_ages(A, R.REPORT_SEASON, (R.SELECT_SEASON, R.REPORT_SEASON))
    wf = core.walk_forward(A, y, R.REPORT_SEASON, (R.SELECT_SEASON, R.REPORT_SEASON), perms=perms)
    return wf, core.decompose(A, wf, b=b)


def k1_word(select: dict[str, Any]) -> dict[str, str] | None:
    """KILLED_K1 when the 2023 C_total point estimate is above +0.0010 (Stage 2 is then not scored)."""
    return core.run_words(float(select["C_total"]["mean"]))


def words(
    select: dict[str, Any] | None, report: dict[str, Any] | None, invalid: str | None = None
) -> dict[str, str]:
    """Precedence (rule section 4): NO_RESULT_INVALID_RUN, then KILLED_K1, then WORSE_THAN_MARKET, then the model and market words.
    Any of the three replaces the pair with NOT_EVALUATED (never NONE)."""
    if invalid is not None:
        rw = core.run_words(float("nan"), invalid)
        assert rw is not None
        return rw
    if select is None:
        raise ValueError("no select-season decomposition and no invalid reason")
    rw = k1_word(select)
    if rw is not None:
        return rw
    if report is None:
        raise ValueError("Stage 2 was not scored but K1 did not fire")
    return core.verdict(report, select)


def consequences(v: dict[str, str], report: dict[str, Any] | None) -> dict[str, Any]:
    """Consequence mapping (section 4) and the Stage 3 trigger (section 6). Nothing is promoted by this rule."""
    replaced = v["override"] != ""
    ctot = None if report is None else bool(report["C_total"]["hi"] < 0)
    mw, kw = v["model"], v["market"]
    return {
        "evaluated": not replaced,
        "C_total_binding_ci_upper_below_0": ctot,
        "propose_logit_pool": bool(not replaced and ctot and mw in ("INFO", "BET_FLOOR_PASS")),
        "propose_logit_pool_if_live_follow_up_passes": bool(
            not replaced and ctot and mw == "DETECTED_BELOW_INFO_FLOOR"
        ),
        "propose_market_rescaling": bool(not replaced and ctot and kw.startswith("MISCALIBRATION")),
        "stage3_written": bool(
            not replaced and (mw in R.MODEL_CONSEQUENCE_WORDS or kw.startswith("MISCALIBRATION"))
        ),
        "keep_your_money": True,
        "promoted": False,
    }


# ------------------------------------------------------------------------------------------- the gate's worlds through this path


def gate_world_record(
    A: dict[str, np.ndarray],
    eta: np.ndarray,
    seed: int,
    k: int,
    b: int,
    perms: np.ndarray,
    name: str,
) -> dict[str, Any]:
    """Drop-in for ``gate.run_world`` that sends one synthetic world through this module's Stage 1 -> K1 -> Stage 2 -> words.
    Returns the gate's record format exactly (same keys), so the gate's own aggregation and checks run on it unchanged."""
    y = core.draw_labels(eta, seed)
    t0 = time.time()
    _, d_sel = stage1(A, y, b)
    if k1_word(d_sel) is not None:
        raise RuntimeError(
            f"K1 fired in gate world {name} seed {seed}: the eval path cannot reproduce the gate's Stage 2 record"
        )
    rep, d_rep = stage2(A, y, perms[:k], b)
    mask24 = A["season"] == R.REPORT_SEASON
    p_true, p_m = core.sigmoid(eta), core.sigmoid(A["m"])

    def keep(d: dict[str, Any]) -> dict[str, Any]:
        return {
            k_: {kk: v[kk] for kk in ("mean", "lo", "hi", "n")}
            for k_, v in d.items()
            if isinstance(v, dict) and "mean" in v
        }

    res = {
        "world": name, "seed": seed, "oracle_kl_2024": core.oracle_kl(p_true[mask24], p_m[mask24]),
        "select_2023": keep(d_sel), "report_2024": keep(d_rep), "verdict": words(d_sel, d_rep),
        "seconds": round(time.time() - t0, 1), "placebo_draw_means_ll": rep["pl_draw_means"],
    }  # fmt: skip
    res["gamma0_last_month_by_stat"] = rep["coefs"][-1]["beta_mm"][
        len(core.MR_COLS) : len(core.MR_COLS) + len(core.STATS)
    ]
    return res


def planted_future(
    A: dict[str, np.ndarray], y: np.ndarray, test_month: str = "2025-01"
) -> dict[str, Any]:
    """Flip every label on/after the test month's first day: that month's MM and MR predictions must not move (== 0); flip the
    2023 labels inside the training window: they must move (> 0.01). The same construction as gate G6."""
    tm = np.datetime64(test_month)
    start = tm.astype("datetime64[D]")
    train = (R.SELECT_SEASON, R.REPORT_SEASON)
    base = core.walk_forward(A, y, R.REPORT_SEASON, train)
    rows = (A["season"] == R.REPORT_SEASON) & (A["month"] == tm)
    y1 = y.copy()
    y1[A["date"] >= start] = 1.0 - y1[A["date"] >= start]
    f = core.walk_forward(A, y1, R.REPORT_SEASON, train)
    y2 = y.copy()
    tr = (A["date"] < start) & (A["season"] == R.SELECT_SEASON)
    y2[tr] = 1.0 - y2[tr]
    g = core.walk_forward(A, y2, R.REPORT_SEASON, train)
    out: dict[str, Any] = {
        "test_month": str(tm), "n_rows": int(rows.sum()),
        "max_abs_diff_future_flip_mm": float(np.nanmax(np.abs(base["lp_mm"][rows] - f["lp_mm"][rows]))),
        "max_abs_diff_future_flip_mr": float(np.nanmax(np.abs(base["lp_mr"][rows] - f["lp_mr"][rows]))),
        "max_abs_diff_train_flip_mm": float(np.nanmax(np.abs(base["lp_mm"][rows] - g["lp_mm"][rows]))),
    }  # fmt: skip
    out["pass"] = bool(
        out["max_abs_diff_future_flip_mm"] == 0.0
        and out["max_abs_diff_future_flip_mr"] == 0.0
        and out["max_abs_diff_train_flip_mm"] > 0.01
    )
    return out


# ------------------------------------------------------------------------------------------- secondary, descriptive


def _ci(A: dict[str, np.ndarray], v: np.ndarray, mask: np.ndarray, b: int) -> dict[str, Any]:
    """The binding (envelope) CI of ``v`` on ``mask`` with boots built on that mask; n only below SLICE_MIN_N."""
    n = int(mask.sum())
    if n < R.SLICE_MIN_N:
        return {"n": n, "note": f"n < {R.SLICE_MIN_N}: n only"}
    c = core.contrast(core.make_boots(A, mask, b=b), v, mask)
    return {k: c[k] for k in ("mean", "lo", "hi", "n")}


def secondary_family(A: dict[str, np.ndarray], wf: dict[str, Any], b: int) -> list[dict[str, Any]]:
    """BH at 0.05 over the declared family of 10 (labels only, no pass): C_mod by stat (4), C_mkt by stat (4), C_mod on bench (1)
    and on heavy depletion (1). p = max over the three clusterings of (1 + #{bm >= 0}) / (B + 1) (``core.gain_p``)."""
    sc = wf["scored"]
    c_mod, c_mkt = wf["ll_mm"] - wf["ll_mr"], wf["ll_mr"] - wf["ll_m"]
    tests: list[tuple[str, np.ndarray, np.ndarray]] = []
    for i, s in enumerate(R.STATS):
        tests.append((f"C_mod:{s}", c_mod, sc & (A["stat_i"] == i)))
    for i, s in enumerate(R.STATS):
        tests.append((f"C_mkt:{s}", c_mkt, sc & (A["stat_i"] == i)))
    tests.append(("C_mod:bench", c_mod, sc & (A["bench"] == 1)))
    tests.append(("C_mod:heavy_depletion", c_mod, sc & (A["tmout"] == 1)))
    assert len(tests) == R.FAMILY_SIZE
    rows = []
    for name, v, m in tests:
        boots = core.make_boots(A, m, b=b)
        c = core.contrast(boots, v, m)
        rows.append(
            {
                "test": name,
                "n": c["n"],
                "mean": c["mean"],
                "lo": c["lo"],
                "hi": c["hi"],
                "p": core.gain_p(boots, v, m),
            }
        )
    adj = mrule.bh_adjust([r["p"] for r in rows])
    for r, a in zip(rows, adj, strict=True):
        r["bh"] = a
        r["bh_reject_at_0.05"] = bool(a <= R.BH_ALPHA)
    return rows


def refit_b0_md(
    A: dict[str, np.ndarray], y: np.ndarray, wf: dict[str, Any], season: int, train: tuple[int, ...]
) -> dict[str, Any]:
    """Recompute the B0 and MD fits of each scored month exactly as ``core.walk_forward`` does (for their coefficients and
    probabilities, which the pinned core does not return) and prove equivalence: the log losses must equal the core's bit for bit."""
    lp_b0 = np.full(len(y), np.nan)
    lp_md = np.full(len(y), np.nan)
    coefs = []
    for mo in np.unique(A["month"][A["season"] == season]):
        start = mo.astype("datetime64[D]")
        sc = np.flatnonzero((A["season"] == season) & (A["month"] == mo))
        tr = np.flatnonzero(np.isin(A["season"], train) & (A["date"] < start))
        if len(sc) == 0 or len(tr) < R.N_MIN_TRAIN:
            continue
        w = 0.5 ** ((start - A["date"][tr]).astype(float) / R.HALF_LIFE_DAYS)
        b_b0 = core.fit_logit(core.design_b0(A, tr), y[tr], w, core.BETA0_B0)
        b_md = core.fit_logit(
            core.design_md(A, tr),
            y[tr],
            w,
            core.BETA0_MD,
            free=np.array([], dtype=int),
            offset=A["m"][tr],
        )
        lp_b0[sc] = core.design_b0(A, sc) @ b_b0
        lp_md[sc] = A["m"][sc] + core.design_md(A, sc) @ b_md
        coefs.append({"month": str(mo), "beta_b0": b_b0.tolist(), "beta_md": b_md.tolist()})
    s = wf["scored"]
    if not (
        np.array_equal(core.ll_vec(lp_b0[s], y[s]), wf["ll_b0"][s])
        and np.array_equal(core.ll_vec(lp_md[s], y[s]), wf["ll_md"][s])
    ):
        raise RuntimeError("the B0 / MD refit does not reproduce the core's log losses bit for bit")
    return {"lp_b0": lp_b0, "lp_md": lp_md, "coefs": coefs}


def coefficient_table(wf: dict[str, Any], refit: dict[str, Any]) -> list[dict[str, Any]]:
    """Fitted coefficients by month, named, with each market slope's departure from 1 (extremising > 0, shrinking < 0)."""
    names_mm = [*core.MR_COLS, *core.MM_COLS]
    out = []
    for c, r in zip(wf["coefs"], refit["coefs"], strict=True):
        assert c["month"] == r["month"]
        mm = dict(zip(names_mm, c["beta_mm"], strict=True))
        mr = dict(zip(core.MR_COLS, c["beta_mr"], strict=True))
        b0 = dict(
            zip(
                [*(f"icpt_{s}" for s in R.STATS), *(f"m_{s}" for s in R.STATS)],
                r["beta_b0"],
                strict=True,
            )
        )
        md = dict(zip(core.MM_COLS, r["beta_md"], strict=True))
        out.append({
            "month": c["month"], "n_train": c["n_train"], "MM": mm, "MR": mr, "B0": b0, "MD": md,
            "slope_minus_1": {arm: {s: d[f"m_{s}"] - 1.0 for s in R.STATS} for arm, d in (("B0", b0), ("MR", mr), ("MM", mm))},
        })  # fmt: skip
    return out


def brier_deltas(
    A: dict[str, np.ndarray], y: np.ndarray, wf: dict[str, Any], refit: dict[str, Any], b: int
) -> dict[str, Any]:
    s = wf["scored"]
    br = {
        "M": (core.sigmoid(A["m"]) - y) ** 2, "B0": (core.sigmoid(refit["lp_b0"]) - y) ** 2, "MR": (core.sigmoid(wf["lp_mr"]) - y) ** 2,
        "MD": (core.sigmoid(refit["lp_md"]) - y) ** 2, "MM": (core.sigmoid(wf["lp_mm"]) - y) ** 2,
    }  # fmt: skip
    pairs = {
        "B0-M": ("B0", "M"),
        "MR-B0": ("MR", "B0"),
        "MR-M": ("MR", "M"),
        "MM-MR": ("MM", "MR"),
        "MD-M": ("MD", "M"),
        "MM-MD": ("MM", "MD"),
        "MM-M": ("MM", "M"),
    }
    return {k: _ci(A, br[a] - br[c], s, b) for k, (a, c) in pairs.items()}


def tip_hour_et(tipoff_utc: pl.Series) -> np.ndarray:
    return np.asarray(
        tipoff_utc.dt.replace_time_zone("UTC")
        .dt.convert_time_zone("America/New_York")
        .dt.hour()
        .to_numpy(),
        dtype=float,
    )


def slice_masks(A: dict[str, np.ndarray], aux: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Section 4 slices (label-free definitions). ``aux``: min10, team_game_no, game_id, n_soft, tip_hour_et, n_out_rot."""
    gid = aux["game_id"].astype(str)
    prefix = np.array([g[:3] for g in gid])
    m10 = aux["min10"]
    tg = aux["team_game_no"]
    ns = np.nan_to_num(aux["n_soft"], nan=0.0)
    hr = aux["tip_hour_et"]
    nout = np.nan_to_num(aux["n_out_rot"], nan=0.0)
    out: dict[str, np.ndarray] = {f"stat_{s}": A["stat_i"] == i for i, s in enumerate(R.STATS)}
    out |= {"starter": A["bench"] == 0, "bench": A["bench"] == 1}
    out |= {
        "min10_lt15": m10 < 15,
        "min10_15_25": (m10 >= 15) & (m10 < 25),
        "min10_ge25": m10 >= 25,
        "min10_missing": ~np.isfinite(m10),
    }
    out |= {
        "teammate_out_ge1": nout >= 1,
        "teammate_out_ge3": nout >= 3,
        "teammate_out_0": nout < 1,
    }
    out |= {"first15_team_games": tg <= 15, "after_first15": tg > 15}
    out |= {
        "regular_season_002": prefix == "002",
        "postseason_004_005": np.isin(prefix, ["004", "005"]),
    }
    out |= {"hi_line": A["hi"] == 1, "low_line": A["hi"] == 0}
    out |= {"soft_books_lt6": ns < 6, "soft_books_ge6": ns >= 6}
    out |= {
        "tip_hour_et_lt19 (tip-hour slice, not freshness)": hr < 19, "tip_hour_et_19": hr == 19, "tip_hour_et_20": hr == 20,
        "tip_hour_et_ge21": hr >= 21,
    }  # fmt: skip
    for mo in np.unique(A["month"]):
        out[f"month_{mo}"] = A["month"] == mo
    return out


def slices(
    A: dict[str, np.ndarray], wf: dict[str, Any], aux: dict[str, np.ndarray], b: int
) -> dict[str, Any]:
    sc = wf["scored"]
    comps = {
        "C_mod": wf["ll_mm"] - wf["ll_mr"],
        "C_mkt": wf["ll_mr"] - wf["ll_m"],
        "C_total": wf["ll_mm"] - wf["ll_m"],
    }
    out: dict[str, Any] = {}
    for name, m in slice_masks(A, aux).items():
        mm = sc & m
        if not mm.any():
            continue
        out[name] = {k: _ci(A, v, mm, b) for k, v in comps.items()}
    return out


def top5_share(A: dict[str, np.ndarray], wf: dict[str, Any]) -> dict[str, Any]:
    """Share of the summed C_mod carried by the five player-seasons with the most negative summed contribution."""
    sc = wf["scored"]
    v = (wf["ll_mm"] - wf["ll_mr"])[sc]
    ps = A["ps_idx"][sc]
    tot = np.bincount(ps, weights=v)
    order = np.argsort(tot)[:5]
    s_all, s_top = float(v.sum()), float(tot[order].sum())
    return {
        "sum_all": s_all,
        "sum_top5": s_top,
        "share_top5": (s_top / s_all) if s_all != 0 else None,
        "n_player_seasons": int(np.unique(ps).size),
    }


# ------------------------------------------------------------------------------------------- DraftKings direction check


def load_book_legs(book: str) -> tuple[pl.DataFrame, dict[str, np.ndarray]]:
    """``book``'s t60 legs with the primary leg filters (played, known-at, x.5, unique) and the pinned extras at the book's line."""
    frames = []
    for s in (R.SELECT_SEASON, R.REPORT_SEASON):
        d = pl.read_parquet(mpr.resolve(mpr.PAIRS_REL.format(season=s)), columns=core.PAIRS_COLS)
        d = d.filter(
            (pl.col("book") == book) & (pl.col("snapshot_kind") == mrule.SNAPSHOT_KIND) & pl.col("stat").is_in(list(R.STATS))
            & ~pl.col("_dnp") & (pl.col("status") == 0)
        )  # fmt: skip
        d = d.filter(
            pl.Series(mrule.known_at_keep(d["snapshot_ts"].to_numpy(), d["tipoff_utc"].to_numpy()))
        )
        d = d.filter(((pl.col("line") % 1.0) - 0.5).abs() < 1e-9)
        ex_file = mpr.resolve(core.EXTRAS_REL.format(season=s))
        core.check_extras_pin(s, ex_file)
        d = d.join(
            pl.read_parquet(ex_file), on=["game_id", "player_id", "stat", "line"], how="left"
        )
        frames.append(d)
    legs = pl.concat(frames).sort(["game_date", "game_id", "stat", "player_id"])
    if legs.select(pl.struct(KEY).is_duplicated().any()).item():
        raise RuntimeError(f"duplicate {book} legs")
    return legs, core.to_arrays(legs)


def direction_check(A: dict[str, np.ndarray], y: np.ndarray) -> dict[str, Any]:
    """Same arms with the book's own prior; means only (sign), no CI, no placebo."""
    out: dict[str, Any] = {}
    for stage, season, train in (
        ("select_2023", R.SELECT_SEASON, (R.SELECT_SEASON,)),
        ("report_2024", R.REPORT_SEASON, (R.SELECT_SEASON, R.REPORT_SEASON)),
    ):
        wf = core.walk_forward(A, y, season, train)
        s = wf["scored"]
        c = {
            "C_mod": wf["ll_mm"] - wf["ll_mr"],
            "C_mkt": wf["ll_mr"] - wf["ll_m"],
            "C_total": wf["ll_mm"] - wf["ll_m"],
        }
        out[stage] = {
            "n": int(s.sum()),
            **{
                k: {"mean": float(np.mean(v[s])), "sign": int(np.sign(np.mean(v[s])))}
                for k, v in c.items()
            },
        }
    return out


# ------------------------------------------------------------------------------------------- Part L (ladder anchoring)


def _imp(american: np.ndarray) -> np.ndarray:
    a = np.asarray(american, dtype=float)
    return np.where(a < 0, -a / (-a + 100.0), 100.0 / (a + 100.0))


def survival_at(p_ge: list[list[float]], k: np.ndarray) -> np.ndarray:
    """S_A(k) = P(Y >= k) from the stored grid (index k-1); 1 for k <= 0, 0 beyond the grid."""
    out = np.empty(len(k))
    for i, (g, kk) in enumerate(zip(p_ge, k, strict=True)):
        kk = int(kk)
        out[i] = 1.0 if kk <= 0 else (float(g[kk - 1]) if g is not None and kk <= len(g) else 0.0)
    return out


def jeffrey(
    m0: np.ndarray, s_k: np.ndarray, s_k0: np.ndarray, k: np.ndarray, k0: np.ndarray
) -> np.ndarray:
    """Jeffrey anchoring (section 4, Part L): S_J(k) = m0 S_A(k) / S_A(k0) for k >= k0; 1 - (1 - m0)(1 - S_A(k)) / (1 - S_A(k0)) for k < k0.
    NaN where S_A(k0) is 0 or 1 (the anchor is undefined)."""
    ok = (s_k0 > 0) & (s_k0 < 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        up = m0 * s_k / s_k0
        dn = 1.0 - (1.0 - m0) * (1.0 - s_k) / (1.0 - s_k0)
    return np.where(ok, np.where(k >= k0, up, dn), np.nan)


def load_rungs(odds_db: str, legs: pl.DataFrame) -> pl.DataFrame:
    """FanDuel two-sided t60 alternate rungs (x.5) off Pinnacle's line for the primary legs, label-free; read-only on a byte copy."""
    import duckdb

    if Path(odds_db).resolve() == (mpr.main_checkout() / LIVE_ODDS_REL).resolve():
        raise RuntimeError("Part L reads a byte copy of odds_history.duckdb, never the live file")
    inv = {v: k for k, v in R.PART_L_MARKETS.items()}
    mk = ",".join(f"'{v}'" for v in R.PART_L_MARKETS.values())
    parts = []
    con = duckdb.connect(odds_db, read_only=True)
    try:
        for s in (R.SELECT_SEASON, R.REPORT_SEASON):
            R.assert_seasons([s])
            q: pl.DataFrame = con.execute(
                f"select game_id, player_id, market, point, side, price_american from odds_history where season = {int(s)} and "
                f"snapshot_kind = 't60' and book = '{R.PART_L_BOOK}' and market in ({mk}) and player_id is not null and side in ('over','under')"
            ).pl()  # fmt: skip
            parts.append(q)
    finally:
        con.close()
    raw = pl.concat(parts).with_columns(
        pl.col("market").replace_strict(inv, return_dtype=pl.String).alias("stat"),
        pl.col("player_id").cast(pl.Int64),
    )
    k = ["game_id", "player_id", "stat", "point", "side"]
    raw = raw.filter(
        ~raw.select(k).is_duplicated()
    )  # a key quoted twice is dropped, never an arbitrary survivor
    wide = raw.pivot(
        on="side", index=["game_id", "player_id", "stat", "point"], values="price_american"
    )
    wide = (
        wide.drop_nulls(["over", "under"])
        .filter(((pl.col("point") % 1.0) - 0.5).abs() < 1e-9)
        .rename({"over": "price_over", "under": "price_under"})  # American prices, not labels
    )
    base = legs.select(*KEY, "line", "p_over_mult", "season", "game_date")
    out: pl.DataFrame = (
        wide.join(base, on=KEY, how="inner")
        .filter(pl.col("point") != pl.col("line"))
        .select(
            *KEY, "point", "price_over", "price_under", "line", "p_over_mult", "season", "game_date"
        )
        .sort([*KEY, "point"])
    )
    return out


def part_l_arrays(rungs: pl.DataFrame, p_ge: list[list[float]]) -> dict[str, np.ndarray]:
    ro, ru = _imp(rungs["price_over"].to_numpy()), _imp(rungs["price_under"].to_numpy())
    v = ro / (ro + ru)
    k = np.rint(rungs["point"].to_numpy() + 0.5).astype(int)
    k0 = np.rint(rungs["line"].to_numpy() + 0.5).astype(int)
    s_k, s_k0 = survival_at(p_ge, k), survival_at(p_ge, k0)
    j = jeffrey(rungs["p_over_mult"].to_numpy().astype(float), s_k, s_k0, k, k0)
    dates = rungs["game_date"].to_numpy().astype("datetime64[D]")
    return {
        "V": v, "J": j, "k": k, "k0": k0, "dist": k - k0, "stat_i": np.array([R.STATS.index(s) for s in rungs["stat"].to_list()]),
        "season": rungs["season"].to_numpy().astype(int), "date": dates, "month": dates.astype("datetime64[M]"),
        "game_idx": np.unique(rungs["game_id"].to_numpy(), return_inverse=True)[1],
    }  # fmt: skip


def part_l_vcal(
    L: dict[str, np.ndarray], y: np.ndarray, season: int, train: tuple[int, ...]
) -> np.ndarray:
    """V_cal: B0-type re-scaling of the venue price (stat intercept + stat slope on logit V), walk-forward by month with the
    rule's ridge, weights and minimum training size; NaN on unscored months."""
    lv = mrule.logit(L["V"])
    out = np.full(len(y), np.nan)
    A = {"stat_i": L["stat_i"], "m": lv}
    for mo in np.unique(L["month"][L["season"] == season]):
        start = mo.astype("datetime64[D]")
        sc = np.flatnonzero((L["season"] == season) & (L["month"] == mo))
        tr = np.flatnonzero(np.isin(L["season"], train) & (L["date"] < start) & np.isfinite(y))
        if len(sc) == 0 or len(tr) < R.N_MIN_TRAIN:
            continue
        w = 0.5 ** ((start - L["date"][tr]).astype(float) / R.HALF_LIFE_DAYS)
        b = core.fit_logit(core.design_b0(A, tr), y[tr], w, core.BETA0_B0)
        out[sc] = core.design_b0(A, sc) @ b
    return out


def part_l(L: dict[str, np.ndarray], y: np.ndarray, b: int) -> dict[str, Any]:
    """Descriptive only, no pass role: J - V, J - V_cal and rung log loss by distance from the line, game-clustered."""
    out: dict[str, Any] = {
        "n_rungs": int(len(y)),
        "n_anchor_undefined": int((~np.isfinite(L["J"])).sum()),
    }
    ll_v = core.ll_vec(mrule.logit(L["V"]), y)
    ll_j = core.ll_vec(mrule.logit(np.where(np.isfinite(L["J"]), L["J"], 0.5)), y)
    for stage, season, train in (
        ("select_2023", R.SELECT_SEASON, (R.SELECT_SEASON,)),
        ("report_2024", R.REPORT_SEASON, (R.SELECT_SEASON, R.REPORT_SEASON)),
    ):
        lvc = part_l_vcal(L, y, season, train)
        with np.errstate(invalid="ignore"):
            ll_vc = core.ll_vec(lvc, y)  # NaN on unscored months
        base = (L["season"] == season) & np.isfinite(L["J"])
        scored = base & np.isfinite(lvc)
        res: dict[str, Any] = {"n": int(base.sum()), "n_vcal_scored": int(scored.sum())}

        def gci(v: np.ndarray, m: np.ndarray) -> dict[str, Any]:
            if m.sum() == 0:
                return {"n": 0}
            gi = np.unique(L["game_idx"][m], return_inverse=True)[1]
            c = Boot(gi, int(gi.max()) + 1, seed=R.BOOT_SEED, B=b).mean(
                v[m], np.ones(int(m.sum()), dtype=bool)
            )
            return {kk: c[kk] for kk in ("mean", "lo", "hi", "n")}

        res["J_minus_V"] = gci(ll_j - ll_v, base)
        res["J_minus_Vcal"] = gci(ll_j - ll_vc, scored)
        res["Vcal_minus_V"] = gci(ll_vc - ll_v, scored)
        by: dict[str, Any] = {}
        d = L["dist"]
        for lab, m in (
            ("-5_or_less", d <= -5),
            *((f"{i:+d}", d == i) for i in (-4, -3, -2, -1, 1, 2, 3, 4)),
            ("+5_or_more", d >= 5),
        ):
            mm = base & m
            by[lab] = {
                "n": int(mm.sum()),
                "ll_J": float(ll_j[mm].mean()) if mm.any() else None,
                "ll_V": float(ll_v[mm].mean()) if mm.any() else None,
                "J_minus_V": gci(ll_j - ll_v, mm)
                if mm.sum() >= R.SLICE_MIN_N
                else {"n": int(mm.sum())},
                "J_minus_Vcal": gci(ll_j - ll_vc, mm & scored)
                if (mm & scored).sum() >= R.SLICE_MIN_N
                else {"n": int((mm & scored).sum())},
            }
        res["by_distance_rungs_above_line_positive"] = by
        out[stage] = res
    return out


def p_model_vintage_check(legs: pl.DataFrame, p_ge: list[list[float]]) -> dict[str, Any]:
    """Label-free: does the stored grid at the Pinnacle line give the pairs' ``p_model`` (same vintage for J and delta)?"""
    k0 = np.rint(legs["line"].to_numpy() + 0.5).astype(int)
    s0 = survival_at(p_ge, k0)
    pm = legs["p_model"].to_numpy().astype(float)
    d = np.abs(s0 - pm)
    return {
        "n": int(len(pm)),
        "share_abs_diff_le_1e-9": float(np.mean(d <= 1e-9)),
        "max_abs_diff": float(np.nanmax(d)),
    }


# ------------------------------------------------------------------------------------------- exchange-venue slice


def load_exchange(odds_db: str, legs: pl.DataFrame) -> pl.DataFrame:
    """novig / prophetx two-sided t60 quotes at Pinnacle's line, 2024 legs from 2024-12-19, read-only on a byte copy. Label-free."""
    import duckdb

    if Path(odds_db).resolve() == (mpr.main_checkout() / LIVE_ODDS_REL).resolve():
        raise RuntimeError(
            "the exchange slice reads a byte copy of odds_history.duckdb, never the live file"
        )
    inv = {v: k for k, v in core.MARKET_OF.items()}
    mk = ",".join(f"'{v}'" for v in core.MARKET_OF.values())
    bk = ",".join(f"'{b}'" for b in R.EXCHANGE_BOOKS)
    con = duckdb.connect(odds_db, read_only=True)
    try:
        raw: pl.DataFrame = con.execute(
            f"select game_id, player_id, market, book, point, max(case when side='over' then price_american end) po, "
            f"max(case when side='under' then price_american end) pu, count(*) n from odds_history where season = {R.REPORT_SEASON} "
            f"and snapshot_kind = 't60' and book in ({bk}) and market in ({mk}) and player_id is not null group by 1,2,3,4,5"
        ).pl()
    finally:
        con.close()
    raw = raw.filter((pl.col("n") == 2) & pl.col("po").is_not_null() & pl.col("pu").is_not_null())
    raw = raw.with_columns(
        pl.col("market").replace_strict(inv, return_dtype=pl.String).alias("stat"),
        pl.col("player_id").cast(pl.Int64),
    )
    first = dt.date.fromisoformat(R.EXCHANGE_FIRST_DAY)
    base = legs.select(*KEY, "line", "game_date").filter(pl.col("game_date") >= first)
    out: pl.DataFrame = (
        raw.join(base, left_on=[*KEY, "point"], right_on=[*KEY, "line"], how="inner")
        .select(*KEY, "book", "po", "pu")
        .sort([*KEY, "book"])
    )
    return out


def exchange_slice(
    A: dict[str, np.ndarray],
    legs: pl.DataFrame,
    wf: dict[str, Any],
    y: np.ndarray,
    ex: pl.DataFrame,
    b: int,
) -> dict[str, Any]:
    """Report-only (no select season): the components on the legs an exchange quoted two-sided at Pinnacle's line, and the
    exchange's own log loss minus M's, per exchange."""
    out: dict[str, Any] = {}
    sc = wf["scored"]
    for book in R.EXCHANGE_BOOKS:
        e = ex.filter(pl.col("book") == book)
        j = _join_aligned(legs.select(KEY), e.select(*KEY, "po", "pu"), KEY)
        po, pu = j["po"].to_numpy().astype(float), j["pu"].to_numpy().astype(float)
        has = np.isfinite(po) & np.isfinite(pu)
        ro, ru = _imp(np.nan_to_num(po, nan=100.0)), _imp(np.nan_to_num(pu, nan=100.0))
        lx = mrule.logit(ro / (ro + ru))
        m = sc & has
        out[book] = {
            "n": int(m.sum()),
            "X_minus_M": _ci(A, core.ll_vec(lx, y) - wf["ll_m"], m, b),
            "C_mod": _ci(A, wf["ll_mm"] - wf["ll_mr"], m, b),
            "C_mkt": _ci(A, wf["ll_mr"] - wf["ll_m"], m, b),
            "C_total": _ci(A, wf["ll_mm"] - wf["ll_m"], m, b),
        }
    return out


# ------------------------------------------------------------------------------------------- missingness (first output on real labels)


def missingness(
    A: dict[str, np.ndarray], legs: pl.DataFrame, y: np.ndarray, minutes: np.ndarray
) -> dict[str, Any]:
    """Section header (c): null p_model, miss_u1, n_soft < 3, spread missing; rates by minutes < 5 vs >= 5, over vs under at the
    line, starter vs bench, per season; the largest absolute rate difference within each split is reported as the asymmetry."""
    ns = legs["n_soft"].to_numpy().astype(float)
    flags = {
        "null_p_model": legs["p_model"].is_null().to_numpy()
        | ~np.isfinite(legs["p_model"].to_numpy().astype(float)),
        "miss_u1": A["miss_u1"] == 1,
        "n_soft_lt3": ~(np.nan_to_num(ns, nan=0.0) >= 3),
        "spread_missing": ~np.isfinite(legs["spread_abs"].to_numpy().astype(float)),
    }
    splits = {
        "minutes": (minutes < 5, minutes >= 5, "lt5", "ge5"),
        "outcome_at_line": (y == 1, y == 0, "over", "under"),
        "role": (A["bench"] == 0, A["bench"] == 1, "starter", "bench"),
    }
    out: dict[str, Any] = {"asymmetry": []}
    for s in (R.SELECT_SEASON, R.REPORT_SEASON):
        ks = A["season"] == s
        tab: dict[str, Any] = {}
        for split, (a, c, la, lc) in splits.items():
            for f, v in flags.items():
                ma, mc = ks & a, ks & c
                ra = float(v[ma].mean()) if ma.any() else None
                rc = float(v[mc].mean()) if mc.any() else None
                diff = None if ra is None or rc is None else ra - rc
                tab[f"{f}|{split}"] = {
                    la: ra,
                    lc: rc,
                    f"n_{la}": int(ma.sum()),
                    f"n_{lc}": int(mc.sum()),
                    "diff": diff,
                }
                if diff is not None:
                    out["asymmetry"].append(
                        {"season": s, "flag": f, "split": split, "abs_diff": abs(diff)}
                    )
        out[str(s)] = tab
    out["asymmetry"].sort(key=lambda r: -r["abs_diff"])
    return out


# ------------------------------------------------------------------------------------------- pins and label-free reproduction (before any label)


def check_model_rows_pins() -> dict[str, str]:
    """model_rows (min10, minutes, p_ge) pinned in the rule mirror (review F3); a missing file or a mismatch is a pin failure."""
    out = {}
    for s, want in R.MODEL_ROWS_PINNED_SHA256.items():
        try:
            got = mrule.file_sha256(mpr.resolve(MODEL_ROWS_REL.format(season=s)))
        except FileNotFoundError as e:
            raise InvalidRun(f"model_rows_{s}.parquet is missing") from e
        if got != want:
            raise InvalidRun(
                f"model_rows_{s}.parquet sha256 {got[:12]} differs from its pin {want[:12]}"
            )
        out[str(s)] = got
    return out


def check_descriptive_pins() -> dict[str, str]:
    """The extracted Part L rungs and exchange quotes (review F2) equal their pins; the run never opens the odds database."""
    out = {}
    for name, rel in R.DESCRIPTIVE_FILES.items():
        p = R.ROOT / rel
        if not p.exists():
            raise InvalidRun(f"{rel} is missing (run `extract` and pin it before the harness pin)")
        got = mrule.file_sha256(p)
        if got != R.DESCRIPTIVE_PINNED_SHA256[name]:
            raise InvalidRun(
                f"{rel} sha256 {got[:12]} differs from its pin {R.DESCRIPTIVE_PINNED_SHA256[name][:12]}"
            )
        out[name] = got
    return out


def scored_counts(A: dict[str, np.ndarray]) -> dict[int, int]:
    """Legs each stage scores, label-free: a month is scored when it has >= N_MIN_TRAIN prior training legs (core line 405)."""
    out: dict[int, int] = {}
    for season, train in (
        (R.SELECT_SEASON, (R.SELECT_SEASON,)),
        (R.REPORT_SEASON, (R.SELECT_SEASON, R.REPORT_SEASON)),
    ):
        n = 0
        for mo in np.unique(A["month"][A["season"] == season]):
            start = mo.astype("datetime64[D]")
            sc = (A["season"] == season) & (A["month"] == mo)
            tr = np.isin(A["season"], train) & (A["date"] < start)
            if sc.any() and int(tr.sum()) >= R.N_MIN_TRAIN:
                n += int(sc.sum())
        out[season] = n
    return out


def check_scored_counts(A: dict[str, np.ndarray]) -> dict[int, int]:
    """Review F5: the scored counts (28,108 / 37,528) are leg reproduction, checked before any label is read."""
    got = scored_counts(A)
    if got != R.SCORED:
        raise InvalidRun(f"scored-leg reproduction failed: {got} != {R.SCORED}")
    return got


# ------------------------------------------------------------------------------------------- the evaluation: primary, then descriptive


def evaluate_primary(
    A: dict[str, np.ndarray],
    y: np.ndarray,
    k: int = R.K_PLACEBO,
    b: int = R.N_BOOT,
    log: Callable[[str], None] = _log,
    on_words: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Stage 1 -> K1 -> Stage 2 -> words -> consequences -> identity -> placebo draws -> secondary BH family.
    ``on_words`` is called with the result as soon as the words exist (the run writes results.json there). Returns the result
    and the context the descriptive part needs (None when K1 fired: 2024 is not scored)."""
    res: dict[str, Any] = {"settings": {"K": k, "B": b}}
    log("stage 1 (select 2023)")
    _, d_sel = stage1(A, y, b)
    res["select_2023"] = d_sel
    assert d_sel["n_scored"] == R.SCORED[R.SELECT_SEASON], (
        "scored count drifted after the label-free check"
    )
    if k1_word(d_sel) is not None:
        res["words"] = words(d_sel, None)
        res["consequences"] = consequences(res["words"], None)
        res["note"] = "KILLED_K1: 2024 is not scored; the rule is closed (T507 precedent)"
        if on_words is not None:
            on_words(res)
        return res, None
    log(f"stage 2 (report 2024), K = {k}")
    perms = core.derangements(A, k, seed=R.PLACEBO_SEED0)
    wf2, d_rep = stage2(A, y, perms, b)
    assert d_rep["n_scored"] == R.SCORED[R.REPORT_SEASON], (
        "scored count drifted after the label-free check"
    )
    res["report_2024"] = d_rep
    res["words"] = words(d_sel, d_rep)
    res["consequences"] = consequences(res["words"], d_rep)
    res["shared_variance_C_mod_first_minus_C_mod"] = (
        d_rep["C_mod_first"]["mean"] - d_rep["C_mod"]["mean"]
    )
    res["identity_check"] = {
        "C_total_minus_(C_mkt+C_mod)": d_rep["C_total"]["mean"] - d_rep["C_mkt"]["mean"] - d_rep["C_mod"]["mean"],
        "C_total_minus_(C_mod_first+C_mkt_after)": d_rep["C_total"]["mean"] - d_rep["C_mod_first"]["mean"] - d_rep["C_mkt_after"]["mean"],
    }  # fmt: skip
    pl_means = np.asarray(wf2["pl_draw_means"], dtype=float)
    res["placebo_draws"] = {
        "K": int(len(pl_means)), "mean_ll": float(pl_means.mean()),
        "sd_ll": float(pl_means.std(ddof=1)) if len(pl_means) > 1 else None,
    }  # fmt: skip
    if on_words is not None:
        on_words(res)
    log("secondary family (BH over 10)")
    res["secondary_bh_family"] = _block(
        "secondary family", lambda: secondary_family(A, wf2, b), log
    )
    return res, {"wf2": wf2}


def _block(name: str, fn: Callable[[], Any], log: Callable[[str], None]) -> Any:
    """Review F4: one labels-only block; any exception is recorded as FAILED and never touches the words."""
    log(name)
    try:
        return fn()
    except Exception as e:  # noqa: BLE001  (a descriptive failure must not void or crash the primary)
        log(f"{name} FAILED: {e!r}")
        return {"status": "FAILED", "error": repr(e)}


DK_NOTE = (
    "sign only, no CI, no placebo; u1 for DraftKings legs uses the soft-book set from the pinned extras, which excludes only "
    "Pinnacle, so it includes DraftKings itself and is pulled toward DK's own price (review F10)"
)


def evaluate_descriptive(
    legs: pl.DataFrame,
    A: dict[str, np.ndarray],
    y: np.ndarray,
    aux: dict[str, np.ndarray],
    ctx: dict[str, Any],
    b: int,
    dk_loader: Callable[[], tuple[dict[str, np.ndarray], np.ndarray, dict[str, Any]]] | None = None,
    part_l_loader: Callable[[], tuple[dict[str, np.ndarray], np.ndarray, dict[str, Any]]]
    | None = None,
    exchange_loader: Callable[[], pl.DataFrame] | None = None,
    log: Callable[[str], None] = _log,
) -> dict[str, Any]:
    """Labels-only blocks, each isolated (F4): coefficients, Brier, slices, top five, DraftKings, Part L, exchange slice."""
    wf2 = ctx["wf2"]
    train = (R.SELECT_SEASON, R.REPORT_SEASON)
    out: dict[str, Any] = {}
    out["coefficients_by_month_2024"] = _block(
        "coefficients",
        lambda: coefficient_table(wf2, refit_b0_md(A, y, wf2, R.REPORT_SEASON, train)),
        log,
    )
    out["brier_deltas_2024"] = _block(
        "Brier deltas",
        lambda: brier_deltas(A, y, wf2, refit_b0_md(A, y, wf2, R.REPORT_SEASON, train), b),
        log,
    )
    out["slices_2024"] = _block("slices", lambda: slices(A, wf2, aux, b), log)
    out["top5_player_season_share_of_C_mod"] = _block("top five", lambda: top5_share(A, wf2), log)

    def dk() -> dict[str, Any]:
        if dk_loader is None:
            return {"status": "NOT_COMPUTED", "reason": "no DraftKings loader"}
        dA, dy, info = dk_loader()
        return {**direction_check(dA, dy), **info, "note": DK_NOTE}

    out["draftkings_direction"] = _block("DraftKings direction check", dk, log)

    def part_l_block() -> dict[str, Any]:
        if part_l_loader is None:
            return {"status": "NOT_COMPUTED", "reason": "no Part L loader"}
        L, y_r, info = part_l_loader()
        return {**part_l(L, y_r, b), **info}

    out["part_l"] = _block("Part L", part_l_block, log)

    def ex_block() -> dict[str, Any]:
        if exchange_loader is None:
            return {"status": "NOT_COMPUTED", "reason": "no exchange loader"}
        return exchange_slice(A, legs, wf2, y, exchange_loader(), b)

    out["exchange_slice_2024"] = _block("exchange-venue slice", ex_block, log)
    return out


def evaluate(
    legs: pl.DataFrame,
    A: dict[str, np.ndarray],
    y: np.ndarray,
    aux: dict[str, np.ndarray],
    k: int = R.K_PLACEBO,
    b: int = R.N_BOOT,
    dk_loader: Callable[[], tuple[dict[str, np.ndarray], np.ndarray, dict[str, Any]]] | None = None,
    part_l_loader: Callable[[], tuple[dict[str, np.ndarray], np.ndarray, dict[str, Any]]]
    | None = None,
    exchange_loader: Callable[[], pl.DataFrame] | None = None,
    log: Callable[[str], None] = _log,
) -> dict[str, Any]:
    """Primary then descriptive, in one call (the dryrun and the tests). ``run`` calls the two parts itself."""
    res, ctx = evaluate_primary(A, y, k, b, log)
    if ctx is None:
        res["descriptive"] = "NOT_RUN (KILLED_K1: 2024 is not scored)"
        return res
    res |= evaluate_descriptive(
        legs, A, y, aux, ctx, b, dk_loader, part_l_loader, exchange_loader, log
    )
    res["descriptive"] = "DONE"
    return res


def aux_columns(legs: pl.DataFrame) -> dict[str, np.ndarray]:
    """Label-free slice columns aligned to ``legs``."""
    tg = pairs_columns(legs, ["team_game_no"])["team_game_no"].to_numpy().astype(float)
    m10 = model_rows(legs, ["min10"])["min10"].to_numpy().astype(float)
    return {
        "min10": m10, "team_game_no": tg, "game_id": legs["game_id"].to_numpy().astype(str),
        "n_soft": legs["n_soft"].to_numpy().astype(float), "tip_hour_et": tip_hour_et(legs["tipoff_utc"]),
        "n_out_rot": legs["n_out_rot"].to_numpy().astype(float),
    }  # fmt: skip


# ------------------------------------------------------------------------------------------- descriptive loaders (pinned files only)


def read_pinned(name: str) -> pl.DataFrame:
    """A descriptive input extracted before the pin (``extract``); re-hashed against its pin on every read."""
    p = R.ROOT / R.DESCRIPTIVE_FILES[name]
    got = mrule.file_sha256(p)
    if got != R.DESCRIPTIVE_PINNED_SHA256[name]:
        raise RuntimeError(f"{p.name} differs from its pin")
    return pl.read_parquet(p)


def part_l_label_free(legs: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, np.ndarray]]:
    """The pinned rungs and their arrays (V, J from the pinned model_rows grid); no label."""
    rungs = read_pinned("part_l_rungs")
    mr = model_rows(rungs.select(*KEY, "season"), ["p_ge"])
    return rungs, part_l_arrays(rungs, mr["p_ge"].to_list())


def part_l_real(
    legs: pl.DataFrame, y_line: np.ndarray
) -> tuple[dict[str, np.ndarray], np.ndarray, dict[str, Any]]:
    """REAL rung outcomes from the pinned pairs' ``y`` (the stat value, one per KEY; review R13): over at rung = y >= rung + 0.5.
    Also reports, on the primary legs, the agreement of (y >= line + 0.5) with the label ``over`` (descriptive)."""
    rungs, L = part_l_label_free(legs)
    yv = pairs_columns(legs, ["y"])["y"].to_numpy().astype(float)
    k0 = np.rint(legs["line"].to_numpy() + 0.5)
    agree = float(np.mean((yv >= k0).astype(float) == y_line))
    ymap = legs.select(KEY).with_columns(pl.Series("y_stat", yv))
    yr = _join_aligned(rungs.select(KEY), ymap, KEY)["y_stat"].to_numpy().astype(float)
    ok = np.isfinite(yr)
    L = {kk: v[ok] for kk, v in L.items()}
    return (
        L,
        (yr[ok] >= L["k"]).astype(float),
        {"n_rungs_without_y": int((~ok).sum()), "pairs_y_vs_over_agreement_on_legs": agree},
    )


def dk_real() -> tuple[dict[str, np.ndarray], np.ndarray, dict[str, Any]]:
    """REAL DraftKings labels inside the DK block; a missing label drops that leg and is counted, never raised (review F4)."""
    dk_legs, dA = load_book_legs("draftkings")
    over = pairs_columns(dk_legs, ["over"], "draftkings")["over"].to_numpy().astype(float)
    ok = np.isfinite(over)
    return (
        {kk: v[ok] for kk, v in dA.items()},
        over[ok],
        {"n_dk_legs_without_label": int((~ok).sum())},
    )


def _invalid_result(reason: str, base: dict[str, Any]) -> dict[str, Any]:
    v = words(None, None, invalid=reason)
    return {
        **base, "words": v, "consequences": consequences(v, None), "invalid_reason": reason,
        "note": "NO_RESULT_INVALID_RUN: no word, no number is reported",
    }  # fmt: skip


def run(sha: str, out_dir: Path = OUT_DIR) -> int:
    """The scored run (real labels). Refuses before any read unless frozen, pinned and mirrored. ISO weeks (review F1); the odds
    database is never opened (F2); every input is pinned and checked before any label (F2, F3, F5); results.json is written as
    soon as the words exist and each descriptive block fails alone (F4)."""
    R.require_frozen(sha)
    pins = R.require_harness_pinned(sha)
    R.check_mirror(core)
    R.assert_seasons((R.SELECT_SEASON, R.REPORT_SEASON))
    out_dir.mkdir(parents=True, exist_ok=True)
    results = out_dir / "results.json"
    base: dict[str, Any] = {
        "rule_sha256": sha,
        "harness_blobs": pins,
        "week_definition": R.WEEK_DEFINITION,
    }

    def write(res: dict[str, Any]) -> None:
        results.write_text(json.dumps(res, indent=1, default=float))

    with heavy_lock("MARKET_ANCHORED_PROPS run"):
        try:
            base["pairs_sha256"] = check_pairs_pins()
            base["extras_sha256"] = check_extras_pins()
            base["model_rows_sha256"] = check_model_rows_pins()
            base["descriptive_inputs_sha256"] = check_descriptive_pins()
            legs, counts, A0 = load_legs_checked()
            base["legs_counts"] = counts
            A = with_week(A0, "iso")
            base["scored_counts"] = check_scored_counts(A)
            aux = aux_columns(legs)
            y = load_labels(legs)  # REAL labels from here on
            minutes = model_rows(legs, ["minutes"])["minutes"].to_numpy().astype(float)
            miss = missingness(A, legs, y, minutes)
            (out_dir / "missingness.json").write_text(json.dumps(miss, indent=1, default=float))
            _log(f"missingness written first; largest asymmetry {miss['asymmetry'][:1]}")
            base["missingness_first"] = miss["asymmetry"][:10]
            pf = planted_future(A, y)
            base["planted_future"] = pf
            if not pf["pass"]:
                raise InvalidRun(f"planted future failed: {pf}")
            res, ctx = evaluate_primary(
                A,
                y,
                R.K_PLACEBO,
                R.N_BOOT,
                on_words=lambda r: write({**base, **r, "descriptive": "PENDING"}),
            )
        except InvalidRun as e:
            res = _invalid_result(str(e), base)
            write(res)
            _log(f"words {res['words']}")
            return 3
        res = {**base, **res}
        if ctx is None:
            res["descriptive"] = "NOT_RUN (KILLED_K1: 2024 is not scored)"
        else:
            res["descriptive"] = "PENDING"
            write(res)
            res |= evaluate_descriptive(
                legs, A, y, aux, ctx, R.N_BOOT, dk_loader=dk_real,
                part_l_loader=lambda: part_l_real(legs, y), exchange_loader=lambda: read_pinned("exchange_quotes"),
            )  # fmt: skip
            res["descriptive"] = "DONE"
        write(res)
        _log(f"words {res['words']}")
    return 0


# ------------------------------------------------------------------------------------------- extract (label-free, before the pin)


def reconcile_extract(rungs: pl.DataFrame, ex: pl.DataFrame) -> dict[str, Any]:
    """Counts against the gate's Part L table (section 8) and exchange counts (section 9). A reconciliation, not a gate: the extract
    keeps only rungs and quotes of the primary legs (played, known-at, x.5, unique), which the gate's raw counts did not filter."""
    committed = json.loads((R.ROOT / R.GATE_JSON).read_text())["data_capture"]
    name = {"pts": "points", "reb": "rebounds", "ast": "assists", "fg3m": "threes"}
    gate_l = {
        f"{r[0]}_{r[1]}": r[2:]
        for r in committed["stage2_fanduel_two_sided_rungs_off_pinnacle_line"]
    }
    got_l: dict[str, Any] = {}
    for season in (R.SELECT_SEASON, R.REPORT_SEASON):
        for stat in R.STATS:
            g = rungs.filter((pl.col("season") == season) & (pl.col("stat") == stat))
            key = f"{season}_{name[stat]}"
            got = [
                g.height,
                g.select("game_id", "player_id").unique().height,
                g["game_id"].n_unique(),
            ]
            got_l[key] = {"extract": got, "gate_raw": gate_l.get(key)}
    gate_x = {r[0]: r[1:] for r in committed["exchange_two_sided_on_pinnacle_line_2024"]}
    got_x: dict[str, Any] = {}
    for bk in R.EXCHANGE_BOOKS:
        g = ex.filter(pl.col("book") == bk)
        got_x[bk] = {
            "extract": [g.height, g["game_id"].n_unique()],
            "gate_raw_all_dates": gate_x.get(bk),
        }
    return {
        "part_l_rungs_players_games": got_l, "exchange_legs_games": got_x,
        "note": "extract counts are below the gate's raw counts because only the primary legs' rungs and quotes are kept "
        "(played, known-at, x.5, one leg per game/player/stat; exchange from 2024-12-19 only); section 8 sums to 212,712 rungs",
    }  # fmt: skip


def extract(db: str, out_dir: Path = OUT_DIR, overwrite: bool = False) -> dict[str, Any]:
    """Write the label-free Part L rungs and exchange quotes from a BYTE COPY of the odds database to sorted zstd parquet, report
    their sha256 (to be pinned in the rule mirror before the harness pin) and reconcile counts. Never the live file."""
    if Path(db).resolve() == (mpr.main_checkout() / LIVE_ODDS_REL).resolve():
        raise RuntimeError("extract reads a byte copy of odds_history.duckdb, never the live file")
    paths = {name: R.ROOT / rel for name, rel in R.DESCRIPTIVE_FILES.items()}
    if not overwrite and any(p.exists() for p in paths.values()):
        raise FileExistsError(
            f"refusing to overwrite {sorted(str(p) for p in paths.values() if p.exists())}; the pins name these bytes"
        )
    check_pairs_pins()
    check_extras_pins()
    legs, _, _ = load_legs_checked()  # label-free
    rungs = load_rungs(db, legs)
    ex = load_exchange(db, legs)
    for name, frame in (("part_l_rungs", rungs), ("exchange_quotes", ex)):
        assert not set(frame.columns) & {"y", "over"}, "a label column reached an extract"
        paths[name].parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(paths[name], compression="zstd", statistics=False)
    out = {
        "db_copy": db, "db_copy_sha256": mrule.file_sha256(Path(db)),
        "sha256": {name: mrule.file_sha256(p) for name, p in paths.items()},
        "rows": {"part_l_rungs": rungs.height, "exchange_quotes": ex.height},
        "reconciliation": reconcile_extract(rungs, ex),
    }  # fmt: skip
    (out_dir / "extract_manifest.json").write_text(json.dumps(out, indent=1, default=float))
    _log(f"extract: {out['sha256']}")
    return out


# ------------------------------------------------------------------------------------------- dryrun


def compare(
    a: Any, b: Any, path: str = "", ignore: frozenset[str] = frozenset({"seconds", "git_head"})
) -> list[str]:
    """Exact recursive comparison (floats must be equal); keys named in ``ignore`` are skipped at any depth."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k in ignore:
                continue
            if k not in a or k not in b:
                out.append(f"{path}/{k}: missing on one side")
            else:
                out += compare(a[k], b[k], f"{path}/{k}", ignore)
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} != {len(b)}"]
        return [
            d
            for i, (x, z) in enumerate(zip(a, b, strict=True))
            for d in compare(x, z, f"{path}[{i}]", ignore)
        ]
    if a != b and not (
        isinstance(a, float) and isinstance(b, float) and np.isnan(a) and np.isnan(b)
    ):
        return [f"{path}: {a!r} != {b!r}"]
    return []


@contextmanager
def eval_path_in_gate() -> Iterator[None]:
    """Route the gate's worlds through ``gate_world_record`` and make ``core.build_extras`` raise for the duration."""
    orig_world, orig_build = gate.run_world, core.build_extras

    def no_build(*a: Any, **k: Any) -> dict[str, str]:
        raise RuntimeError("the dryrun never calls core.build_extras")

    gate.run_world = gate_world_record
    core.build_extras = no_build
    try:
        yield
    finally:
        gate.run_world = orig_world
        core.build_extras = orig_build


def synthetic_minutes(A: dict[str, np.ndarray], seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.where(
        rng.random(len(A["m"])) < 0.05,
        rng.uniform(0, 5, len(A["m"])),
        rng.uniform(5, 40, len(A["m"])),
    )


def planted_flip_world(
    A: dict[str, np.ndarray], season: int, flip_day: str, gamma: float
) -> np.ndarray:
    """A world whose model coefficient changes sign at ``flip_day`` inside ``season`` (null elsewhere): the walk-forward learns the
    early sign and applies it after the flip, so MM is confidently worse than M (the planted K1 world uses ``season`` = 2023)."""
    s = np.where(A["date"] < np.datetime64(flip_day), 1.0, -1.0)
    return np.asarray(A["m"] + np.where(A["season"] == season, gamma * s * A["delta"], 0.0))


def planted_reversal_world(A: dict[str, np.ndarray], gamma: float) -> np.ndarray:
    """Planted WORSE_THAN_MARKET: the model coefficient is +gamma in 2023 and -gamma in 2024, so every 2024 fit (dominated by
    the 35k 2023 legs) carries the wrong sign while 2023 itself gains (K1 cannot fire). A within-2024 flip at 2024-12-01 was
    tried first in the 2026-10-23 dryrun and did not fire (2024 C_total -0.0024: the fits re-learned the sign within months)."""
    s = np.where(A["season"] == R.SELECT_SEASON, 1.0, -1.0)
    return np.asarray(A["m"] + gamma * s * A["delta"])


def dryrun(db: str, out: Path, rehearse: bool = True, reproduce: bool = True) -> dict[str, Any]:
    if Path(db).resolve() == (mpr.main_checkout() / LIVE_ODDS_REL).resolve():
        raise RuntimeError("--db must be a byte copy of odds_history.duckdb, never the live file")
    R.check_mirror(core)
    t0 = time.time()
    res: dict[str, Any] = {
        "note": "dryrun: every outcome below is synthetic; no real label was read",
        "db_copy": db,
    }
    res["pairs_sha256"] = check_pairs_pins()
    res["extras_sha256"] = check_extras_pins()
    res["model_rows_sha256"] = check_model_rows_pins()
    res["descriptive_inputs_sha256"] = check_descriptive_pins()
    committed = json.loads((R.ROOT / R.GATE_JSON).read_text())
    with heavy_lock("MARKET_ANCHORED_PROPS dryrun"):
        if reproduce:
            _log(
                "reproducing gate.json through the eval path (gate settings K=20, B=1000, reps=10)"
            )
            tmp = out.parent / "dryrun_gate_reproduction.json"
            with eval_path_in_gate():
                gate.run(
                    R.GATE_SETTINGS["k_null"],
                    R.GATE_SETTINGS["boot"],
                    tmp,
                    R.GATE_SETTINGS["reps"],
                    db,
                    rebuild_extras=False,
                )
            got = json.loads(tmp.read_text())
            diffs = compare(
                committed, got, ignore=frozenset({"seconds", "git_head", "data_capture"})
            )
            cap = compare(committed["data_capture"], got["data_capture"])
            res["gate_reproduction"] = {
                "status": "REPRODUCED" if not diffs else "DIFFERENT", "n_differences": len(diffs), "differences": diffs[:200],
                "compared": "every key of gate.json except seconds, git_head and data_capture",
                "data_capture_differences (odds database grew since the gate; descriptive)": cap,
                "reproduction_file_sha256": mrule.file_sha256(tmp),
            }  # fmt: skip
            tmp.unlink()
            _log(
                f"gate reproduction: {res['gate_reproduction']['status']} ({len(diffs)} differences)"
            )
        legs, counts, A_core = load_legs_checked()
        res["eval_planted_future_equals_gate"] = None
        y0 = core.draw_labels(core.world_eta(A_core, "null"), R.GATE_SEEDS["world_labels"] + 10)
        pf = planted_future(A_core, y0)
        res["eval_planted_future"] = pf
        res["eval_planted_future_equals_gate"] = not compare(
            {k: v for k, v in pf.items() if k != "pass"}, committed["planted_future"]
        )
        A_iso = with_week(A_core, "iso")
        res["scored_counts_label_free"] = check_scored_counts(A_iso)
        res["weeks"] = {
            str(s): {"core_thursday_weeks": int(np.unique(A_core["week_idx"][A_core["season"] == s]).size), "iso_weeks": int(np.unique(A_iso["week_idx"][A_iso["season"] == s]).size)}
            for s in (R.SELECT_SEASON, R.REPORT_SEASON)
        }  # fmt: skip
        res["label_free_audit"] = {
            "null_p_model": int(legs["p_model"].is_null().sum()), "null_starter10_filled_as_starter": int(legs["starter10"].is_null().sum()),
            "null_n_out_rot_filled_as_0": int(legs["n_out_rot"].is_null().sum()),
            "p_model_vintage": p_model_vintage_check(legs, model_rows(legs, ["p_ge"])["p_ge"].to_list()),
        }  # fmt: skip
        if rehearse:
            res["rehearsal"] = rehearse_pipeline(legs, A_core, A_iso)
    res["seconds"] = round(time.time() - t0, 1)
    out.write_text(json.dumps(res, indent=1, default=float))
    _log(f"wrote {out} ({res['seconds']} s)")
    return res


def rehearse_pipeline(
    legs: pl.DataFrame, A_core: dict[str, np.ndarray], A_iso: dict[str, np.ndarray]
) -> dict[str, Any]:
    """The whole run pipeline on synthetic labels (planted worlds), with real label-free inputs read from the same pinned
    files the run reads (no odds database)."""
    out: dict[str, Any] = {}
    aux = aux_columns(legs)
    g11 = gate.calibrate_gamma(A_core, 0.0011, A_core["season"] == R.REPORT_SEASON)
    # W_both at the gate's seed of replicate 0: the full pipeline (ISO weeks, K = 50, B = 2,000), DK, Part L, exchange
    y = core.draw_labels(core.world_eta(A_iso, "both", g11), R.GATE_SEEDS["world_labels"] + 50)
    miss = missingness(A_iso, legs, y, synthetic_minutes(A_iso, 7))

    def dk_synth() -> tuple[dict[str, np.ndarray], np.ndarray, dict[str, Any]]:
        _, dA = load_book_legs("draftkings")
        return dA, core.draw_labels(dA["m"], 11), {"labels": "synthetic"}

    def part_l_synth() -> tuple[dict[str, np.ndarray], np.ndarray, dict[str, Any]]:
        rungs, L = part_l_label_free(legs)
        u = np.random.default_rng(12).random(rungs.height)
        j_true = np.where(
            np.isfinite(L["J"]), L["J"], 0.5
        )  # rung outcomes drawn from the anchored grid (synthetic)
        return L, (u < j_true).astype(float), {"labels": "synthetic"}

    full = evaluate(
        legs,
        A_iso,
        y,
        aux,
        R.K_PLACEBO,
        R.N_BOOT,
        dk_synth,
        part_l_synth,
        lambda: read_pinned("exchange_quotes"),
    )
    out["W_both_full_pipeline_iso_weeks"] = {
        "missingness_top": miss["asymmetry"][:5], "words": full["words"], "consequences": full["consequences"],
        "C": {k: {kk: full["report_2024"][k][kk] for kk in ("mean", "lo", "hi", "n")} for k in ("C_mod", "C_mkt", "C_mkt_after", "C_total", "C_pl")},
        "select_C_total": full["select_2023"]["C_total"]["mean"], "identity_check": full["identity_check"],
        "bh_family": [{k: r[k] for k in ("test", "n", "mean", "p", "bh", "bh_reject_at_0.05")} for r in full["secondary_bh_family"]],
        "n_slices": len(full["slices_2024"]), "slices_n_only": sorted(k for k, v in full["slices_2024"].items() if "note" in v["C_mod"]),
        "top5": full["top5_player_season_share_of_C_mod"], "draftkings_direction": full["draftkings_direction"],
        "part_l": {k: v for k, v in full["part_l"].items() if k in ("n_rungs", "n_anchor_undefined")}
        | {s: {kk: full["part_l"][s][kk] for kk in ("n", "n_vcal_scored", "J_minus_V", "J_minus_Vcal")} for s in ("select_2023", "report_2024")},
        "exchange_slice": {b_: {"n": v["n"], "X_minus_M": v["X_minus_M"]} for b_, v in full["exchange_slice_2024"].items()},
        "brier_MM-M": full["brier_deltas_2024"]["MM-M"], "n_coef_months": len(full["coefficients_by_month_2024"]),
    }  # fmt: skip
    _log(f"rehearsal W_both: {full['words']}")
    # planted run words on the real legs (small K and B: only the word matters)
    for name, eta in (
        ("W_planted_K1", planted_flip_world(A_iso, R.SELECT_SEASON, "2023-12-01", 1.5)),
        ("W_planted_WORSE", planted_reversal_world(A_iso, 1.0)),
    ):
        yy = core.draw_labels(eta, 99)
        r = evaluate(legs, A_iso, yy, aux, k=2, b=200)
        out[name] = {
            "words": r["words"], "consequences": r["consequences"], "select_C_total": r["select_2023"]["C_total"]["mean"],
            "report_C_total": None if "report_2024" not in r else {kk: r["report_2024"]["C_total"][kk] for kk in ("mean", "lo", "hi")},
            "stage2_scored": "report_2024" in r,
        }  # fmt: skip
        _log(f"rehearsal {name}: {r['words']}")
    # F4: a descriptive loader that raises leaves the words intact and records FAILED for that block only
    yn = core.draw_labels(A_iso["m"], 98)

    def broken() -> tuple[dict[str, np.ndarray], np.ndarray, dict[str, Any]]:
        raise RuntimeError("planted DK loader failure")

    r = evaluate(legs, A_iso, yn, aux, k=2, b=200, dk_loader=broken)
    out["W_descriptive_failure"] = {
        "words": r["words"],
        "draftkings_direction": r["draftkings_direction"],
        "descriptive": r["descriptive"],
    }
    _log(f"rehearsal descriptive failure: {r['words']} / {r['draftkings_direction']}")
    # invalid run: an extras frame that differs from its pin (the pin is tampered in memory for this call only)
    saved = dict(core.EXTRAS_PINNED_SHA256)
    try:
        core.EXTRAS_PINNED_SHA256[R.REPORT_SEASON] = "0" * 64
        try:
            check_extras_pins()
            out["W_invalid_run"] = {"error": "a tampered pin did not raise"}
        except InvalidRun as e:
            out["W_invalid_run"] = _invalid_result(str(e), {})
    finally:
        core.EXTRAS_PINNED_SHA256.clear()
        core.EXTRAS_PINNED_SHA256.update(saved)
    R.check_mirror(core)
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dryrun")
    d.add_argument(
        "--db", required=True, help="a byte copy of odds_history.duckdb (never the live file)"
    )
    d.add_argument("--out", default=str(OUT_DIR / "dryrun.json"))
    d.add_argument("--no-rehearse", action="store_true")
    d.add_argument("--no-reproduce", action="store_true")
    x = sub.add_parser("extract")
    x.add_argument(
        "--db", required=True, help="a byte copy of odds_history.duckdb (never the live file)"
    )
    x.add_argument(
        "--overwrite", action="store_true", help="replace the extracted files (breaks their pins)"
    )
    r = sub.add_parser("run")
    r.add_argument("--sha", required=True)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "dryrun":
        dryrun(a.db, Path(a.out), rehearse=not a.no_rehearse, reproduce=not a.no_reproduce)
        return 0
    if a.cmd == "extract":
        extract(a.db, overwrite=a.overwrite)
        return 0
    return run(a.sha)


if __name__ == "__main__":
    raise SystemExit(main())
