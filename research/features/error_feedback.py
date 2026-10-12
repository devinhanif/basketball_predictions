# ruff: noqa: E501
"""ERROR_FEEDBACK columns: a player's own recent errors against a REPORT-FREE walk-forward base.

Rule: docs/prereg/ERROR_FEEDBACK.md. Two pieces, both pure functions of their inputs:

* :func:`walk_base` -- the report-free residual base. Production ``context_residual`` heads with the injury-report
  columns (``has_report``, ``n_out_rot``, ``vac_*``) dropped, refit walk-forward in calendar-month blocks; every row
  dated before a block's first day trains it (no window cap). Each row gets the base's integer-support quantile grid
  (199 equal-mass taus, ``ceil(q - 0.5)``), its integer median (the 100th quantile) and its mid-PIT
  ``(F(y - 1) + F(y)) / 2``. A row's base forecast was made by a model that never saw the row or anything on or after
  its block's first day. Report-free, so the 2021-22 hourly-report era (T315) cannot enter the feature.
* :func:`error_feedback_columns` -- for a row of player i dated D: over i's base rows dated STRICTLY before D (latest
  first, at most 10), ``ef_res_l3/l5/l10`` = mean of (y - base median), ``ef_pit_l5`` = mean mid-PIT,
  ``ef_n`` = how many such rows (capped at 10). NaN (and ``ef_n = 0``) when there is none. ``shift = k`` skips the k
  most recent earlier rows (the past-only placebo uses k = 10: the same statistics over rows 11-20 back).

Nothing here reads a database; the harness (``research.eval.error_feedback_eval``) owns loading and the season cap.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.eval.context_residual_eval import month_blocks
from nba.props.context_residual import (
    CRPS_TAUS,
    ContextResidualConfig,
    ContextResidualModel,
    stat_feature_names,
    to_integer_support,
)
from nba.props.lower_tail import raw_quantiles

#: Injury-report columns of the production feature list (dropped from the base).
REPORT_FEATURES: tuple[str, ...] = (
    "has_report",
    "n_out_rot",
    "vac_min",
    "vac_pts",
    "vac_reb",
    "vac_ast",
    "vac_fg3m",
)
WINDOWS: tuple[int, ...] = (3, 5, 10)
MAX_ROWS = 10
EF_COLS: tuple[str, ...] = ("ef_res_l3", "ef_res_l5", "ef_res_l10", "ef_pit_l5", "ef_n")
MEDIAN_INDEX = len(CRPS_TAUS) // 2  # 99: the 100th of 199 equal-mass quantiles
MIN_TRAIN = 1500  # the production month-block loop's floor
_KEY = 100_000  # pid * _KEY + days since epoch (days < 1e5 until year 2243)


def base_feature_names(stat: str) -> list[str]:
    """Production feature names of ``stat`` without the injury-report columns."""
    return [n for n in stat_feature_names(stat) if n not in REPORT_FEATURES]


def mid_pit(qi: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Mid-PIT of integer outcomes under an integer quantile grid on equal-mass taus: (F(y-1) + F(y)) / 2."""
    below = (qi < y[:, None]).mean(axis=1)
    at_or_below = (qi <= y[:, None]).mean(axis=1)
    return np.asarray(0.5 * (below + at_or_below))


def date_days(dates: pl.Series | np.ndarray) -> np.ndarray:
    """Days since 1970-01-01 as int64 (dates from polars Date or numpy datetime64)."""
    arr = dates.to_numpy() if isinstance(dates, pl.Series) else np.asarray(dates)
    return np.asarray(arr.astype("datetime64[D]").astype(np.int64))


def integer_grid(c: np.ndarray, s: np.ndarray, z_sorted: np.ndarray) -> np.ndarray:
    """Production quantiles on ``CRPS_TAUS`` (``c + s * empirical z quantile``) mapped to integer support."""
    return to_integer_support(raw_quantiles(c, s, z_sorted, CRPS_TAUS))


def walk_base(
    sf: pl.DataFrame,
    stat: str,
    cfg: ContextResidualConfig,
    seasons: tuple[int, ...],
    names: list[str] | None = None,
    min_train: int = MIN_TRAIN,
    on_block: Callable[[int, dt.date, dict[str, np.ndarray]], None] | None = None,
    cached: Callable[[int, dt.date], dict[str, np.ndarray] | None] | None = None,
) -> dict[str, np.ndarray]:
    """Walk-forward report-free base for the rows of ``seasons`` in ``sf`` (a ``stat_frame`` with ``minutes``).

    Every row dated before a block's first day trains it, across seasons. ``on_block`` receives each block's arrays
    (checkpointing); ``cached`` may return a block's arrays instead of fitting (resume). Blocks whose training set has
    fewer than ``min_train`` rows are skipped, exactly as in production's loop."""
    names = names if names is not None else base_feature_names(stat)
    leak = sorted(set(names) & set(REPORT_FEATURES))
    if leak:
        raise ValueError(f"report columns in the base feature list: {leak}")
    sf = sf.sort(["game_date", "game_id", "player_id"])
    test_all = sf.filter(pl.col("season").is_in(list(seasons)))
    parts: list[dict[str, np.ndarray]] = []
    for b, (start, end) in enumerate(month_blocks(test_all)):
        got = cached(b, start) if cached is not None else None
        if got is None:
            block = sf.filter(
                (pl.col("game_date") >= start)
                & (pl.col("game_date") < end)
                & pl.col("season").is_in(list(seasons))
            )
            train = sf.filter(pl.col("game_date") < start)
            if block.is_empty() or train.height < min_train:
                continue
            model = ContextResidualModel(stat, cfg, names).fit(train)
            assert model.z_sorted is not None
            c, s = model.predict_components(block)
            y = block["y"].to_numpy().astype(float)
            qi = integer_grid(c, s, model.z_sorted)
            got = {
                "row_gid": block["game_id"].to_numpy().astype(str),
                "row_pid": block["player_id"].to_numpy().astype(np.int64),
                "row_day": date_days(block["game_date"]),
                "row_season": block["season"].to_numpy().astype(np.int64),
                "row_y": y,
                "row_c": c,
                "row_s": s,
                "row_med": qi[:, MEDIAN_INDEX].astype(float),
                "row_pit": mid_pit(qi, y),
                "row_blk": np.full(block.height, b, dtype=np.int64),
                "cal_z": np.asarray(model.z_sorted, dtype=float),
                "cal_blk": np.full(len(model.z_sorted), b, dtype=np.int64),
                "blk_start": np.array([(start - dt.date(1970, 1, 1)).days], dtype=np.int64),
                "blk_train_rows": np.array([train.height], dtype=np.int64),
            }
            if on_block is not None:
                on_block(b, start, got)
        parts.append(got)
    if not parts:
        raise ValueError("no block could be fitted")
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


def save_base(path: Path, base: dict[str, np.ndarray], cap: int) -> Path:
    """Write a base file; refuses any row with ``season > cap`` (the repo cap is 2024: 2025 is the holdout)."""
    if base["row_season"].size and int(base["row_season"].max()) > cap:
        raise ValueError(f"base rows with season > {cap}: refusing to store them")
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **base)  # type: ignore[arg-type]
    return path


def load_base(path: Path, cap: int) -> dict[str, np.ndarray]:
    z = np.load(path, allow_pickle=False)
    out = {k: z[k] for k in z.files}
    if out["row_season"].size and int(out["row_season"].max()) > cap:
        raise ValueError(f"{path} carries season > {cap}")
    return out


def concat_base(*bases: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Row-wise union of base files (row arrays only); duplicate (game, player) rows are refused."""
    keys = [k for k in bases[0] if k.startswith("row_")]
    out = {k: np.concatenate([b[k] for b in bases]) for k in keys}
    pair = np.char.add(np.char.add(out["row_gid"].astype(str), ":"), out["row_pid"].astype(str))
    if len(np.unique(pair)) != len(pair):
        raise ValueError("duplicate (game, player) rows across base files")
    return out


def error_feedback_columns(
    q_pid: np.ndarray,
    q_day: np.ndarray,
    b_pid: np.ndarray,
    b_day: np.ndarray,
    b_res: np.ndarray,
    b_pit: np.ndarray,
    shift: int = 0,
    prefix: str = "ef_",
) -> dict[str, np.ndarray]:
    """Columns for query rows (``q_pid``, ``q_day``) from base rows (``b_*``) dated STRICTLY before the query's day.

    Vectorised: base rows sorted by (player, day); the query's position is the count of the player's base rows with a
    smaller day, so rows on the same day never feed each other. ``shift`` skips the most recent ``shift`` earlier rows."""
    if shift < 0:
        raise ValueError("shift must be >= 0")
    b_pid = np.asarray(b_pid, dtype=np.int64)
    b_day = np.asarray(b_day, dtype=np.int64)
    order = np.lexsort((b_day, b_pid))
    key = b_pid[order] * _KEY + b_day[order]
    res = np.asarray(b_res, dtype=float)[order]
    pit = np.asarray(b_pit, dtype=float)[order]
    q_pid = np.asarray(q_pid, dtype=np.int64)
    q_day = np.asarray(q_day, dtype=np.int64)
    pos = np.searchsorted(key, q_pid * _KEY + q_day, side="left")
    first = np.searchsorted(key, q_pid * _KEY, side="left")
    end = pos - shift
    n_eff = np.clip(np.minimum(end - first, MAX_ROWS), 0, None)
    out: dict[str, np.ndarray] = {}

    def window_mean(v: np.ndarray, w: int) -> np.ndarray:
        # explicit sum over the window's own rows (latest first): a value outside the window can never move it,
        # not even by floating-point rounding (a running cumulative sum would)
        k = np.minimum(n_eff, w)
        acc = np.zeros(len(k))
        for j in range(w):
            ok = j < k
            acc[ok] += v[end[ok] - 1 - j]
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(k > 0, acc / k, np.nan)

    for w in WINDOWS:
        out[f"{prefix}res_l{w}"] = window_mean(res, w)
    out[f"{prefix}pit_l5"] = window_mean(pit, 5)
    out[f"{prefix}n"] = n_eff.astype(float)
    return out


def residuals(base: dict[str, np.ndarray]) -> np.ndarray:
    """Base residual per row: y minus the integer median."""
    return np.asarray(base["row_y"] - base["row_med"], dtype=float)


def shuffled(
    base: dict[str, np.ndarray], seed: int
) -> tuple[np.ndarray, np.ndarray]:  # (res, pit) permuted within season
    """Residuals and mid-PITs permuted across rows WITHIN each season (one permutation shared by both, so a row's
    pair stays a real pair); the timing and player of every base row are kept, the link to the player is broken."""
    rng = np.random.default_rng(seed)
    res, pit = residuals(base), np.asarray(base["row_pit"], dtype=float)
    out_r, out_p = res.copy(), pit.copy()
    for s in np.unique(base["row_season"]):
        idx = np.flatnonzero(base["row_season"] == s)
        perm = rng.permutation(idx)
        out_r[idx], out_p[idx] = res[perm], pit[perm]
    return out_r, out_p


def attach(
    frame: pl.DataFrame,
    base: dict[str, np.ndarray],
    shift: int = 0,
    prefix: str = "ef_",
    res: np.ndarray | None = None,
    pit: np.ndarray | None = None,
) -> pl.DataFrame:
    """``frame`` (needs ``player_id``, ``game_date``) with the five columns appended under ``prefix``."""
    cols = error_feedback_columns(
        frame["player_id"].to_numpy(),
        date_days(frame["game_date"]),
        base["row_pid"],
        base["row_day"],
        residuals(base) if res is None else res,
        base["row_pit"] if pit is None else pit,
        shift=shift,
        prefix=prefix,
    )
    return frame.with_columns([pl.Series(k, v) for k, v in cols.items()])


def column_names(prefix: str = "ef_") -> tuple[str, ...]:
    return tuple(prefix + c[len("ef_") :] for c in EF_COLS)


def describe(base: dict[str, np.ndarray]) -> dict[str, Any]:
    """Row counts per season and block count (no outcome summary)."""
    seasons, counts = np.unique(base["row_season"], return_counts=True)
    return {
        "rows": int(len(base["row_y"])),
        "rows_by_season": {str(int(s)): int(n) for s, n in zip(seasons, counts, strict=True)},
        "blocks": int(len(np.unique(base["row_blk"]))),
    }
