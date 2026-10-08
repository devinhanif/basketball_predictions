"""Out-of-fold (OOF) prediction store in its own DuckDB file.

``nba.duckdb`` is single-writer and busy, so candidate predictions live in
``data/stack/oof.duckdb`` (gitignored). Writes are idempotent per
``(model, model_version)`` and validated: no leakage (``made_with_data_through
< game_date``), probabilities in [0, 1], quantile grids monotone.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

TARGETS = ("win", "pts", "reb", "ast", "fg3m")
N_Q = 19
#: Quantile levels of ``q_grid``: 0.05, 0.10, ..., 0.95.
TAUS = np.round(np.arange(1, N_Q + 1) / 20.0, 2)
GAME_LEVEL_PLAYER = -1
DEFAULT_PATH = Path("data/stack/oof.duckdb")
KEY_COLS = ["target", "game_id", "player_id"]

DDL = f"""
CREATE TABLE IF NOT EXISTS oof_predictions (
  model VARCHAR, model_version VARCHAR, target VARCHAR,
  game_id VARCHAR, player_id INT, game_date DATE, season INT, fold_id INT,
  made_with_data_through DATE, p DOUBLE, q_grid FLOAT[{N_Q}],
  mean DOUBLE, p_play DOUBLE, meta JSON
)
"""

_REQUIRED = ["target", "game_id", "player_id", "game_date", "season", "made_with_data_through"]


def oof_path(path: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the store path: explicit arg > ``OOF_DB_PATH`` env > default."""
    return Path(path or os.environ.get("OOF_DB_PATH") or DEFAULT_PATH)


def validate_oof(df: pd.DataFrame) -> None:
    """Raise ``ValueError`` on any leakage / range / shape violation."""
    missing = [c for c in _REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"missing columns: {missing}")
    if df.empty:
        raise ValueError("empty frame")
    bad_t = set(df["target"].unique()) - set(TARGETS)
    if bad_t:
        raise ValueError(f"unknown targets: {sorted(bad_t)}")
    gd = pd.to_datetime(df["game_date"])
    thru = pd.to_datetime(df["made_with_data_through"])
    if thru.isna().any() or gd.isna().any():
        raise ValueError("null game_date / made_with_data_through")
    if (thru >= gd).any():
        n = int((thru >= gd).sum())
        raise ValueError(f"leakage: {n} rows with made_with_data_through >= game_date")
    if df.duplicated(KEY_COLS).any():
        raise ValueError("duplicate (target, game_id, player_id) keys")
    p = df["p"] if "p" in df.columns else pd.Series(np.nan, index=df.index)
    if ((p < 0) | (p > 1)).any():
        raise ValueError("p outside [0, 1]")
    win = df["target"] == "win"
    if p[win].isna().any():
        raise ValueError("target 'win' rows require p")
    if "q_grid" in df.columns:
        has_q = df["q_grid"].map(lambda v: v is not None and not _is_nan_scalar(v))
    else:
        has_q = pd.Series(False, index=df.index)
    if (~win & ~has_q).any():
        raise ValueError("non-win rows require q_grid")
    if has_q.any():
        q = np.vstack([np.asarray(v, dtype=float) for v in df.loc[has_q, "q_grid"]])
        if q.shape[1] != N_Q:
            raise ValueError(f"q_grid must have {N_Q} entries, got {q.shape[1]}")
        if not np.isfinite(q).all():
            raise ValueError("non-finite q_grid values")
        if (np.diff(q, axis=1) < 0).any():
            raise ValueError("q_grid not monotone non-decreasing")


def _is_nan_scalar(v: Any) -> bool:
    return isinstance(v, float) and np.isnan(v)


def _prepare(df: pd.DataFrame, model: str, version: str) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "model": model,
            "model_version": version,
            "target": df["target"].to_numpy(),
            "game_id": df["game_id"].astype(str).to_numpy(),
            "player_id": df["player_id"].astype("int64").to_numpy(),
            "game_date": pd.to_datetime(df["game_date"]).dt.date.to_numpy(),
            "season": df["season"].astype("int64").to_numpy(),
            "fold_id": (df["fold_id"] if "fold_id" in df.columns else df["season"])
            .astype("int64")
            .to_numpy(),
            "made_with_data_through": pd.to_datetime(
                df["made_with_data_through"]
            ).dt.date.to_numpy(),
        }
    )
    for col in ("p", "mean", "p_play"):
        out[col] = df[col].to_numpy(dtype=float) if col in df.columns else np.nan
    if "q_grid" in df.columns:
        out["q_grid"] = [
            None if v is None or _is_nan_scalar(v) else [float(x) for x in v] for v in df["q_grid"]
        ]
    else:
        out["q_grid"] = None
    metas = df["meta"] if "meta" in df.columns else pd.Series("{}", index=df.index)
    out["meta"] = [m if isinstance(m, str) else json.dumps(m or {}) for m in metas]
    return out


def write_oof(
    df: pd.DataFrame,
    model: str,
    version: str,
    path: str | os.PathLike[str] | None = None,
) -> int:
    """Validate then atomically replace every row of ``(model, version)``.

    Returns the number of rows written. Raises ``ValueError`` before touching
    the file if validation fails.
    """
    validate_oof(df)
    rows = _prepare(df, model, version)
    p = oof_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(p))
    try:
        con.execute(DDL)
        con.register("_new_rows", rows)
        con.execute("BEGIN")
        con.execute(
            "DELETE FROM oof_predictions WHERE model = ? AND model_version = ?", [model, version]
        )
        con.execute(
            f"""INSERT INTO oof_predictions SELECT model, model_version, target, game_id,
            player_id, game_date, season, fold_id, made_with_data_through, p,
            CAST(q_grid AS FLOAT[{N_Q}]), mean, p_play, CAST(meta AS JSON) FROM _new_rows"""
        )
        con.execute("COMMIT")
    finally:
        con.close()
    return len(rows)


def read_oof(
    model: str,
    version: str,
    target: str,
    path: str | os.PathLike[str] | None = None,
) -> pd.DataFrame:
    """Read one candidate's rows for a target (read-only connection)."""
    con = duckdb.connect(str(oof_path(path)), read_only=True)
    try:
        df = con.execute(
            "SELECT * FROM oof_predictions WHERE model = ? AND model_version = ? AND target = ?",
            [model, version, target],
        ).df()
    finally:
        con.close()
    return df


def list_models(path: str | os.PathLike[str] | None = None) -> pd.DataFrame:
    """Summary of stored candidates: model, version, target, n, date range."""
    con = duckdb.connect(str(oof_path(path)), read_only=True)
    try:
        return con.execute(
            """SELECT model, model_version, target, count(*) AS n,
            min(game_date) AS d0, max(game_date) AS d1 FROM oof_predictions
            GROUP BY ALL ORDER BY ALL"""
        ).df()
    finally:
        con.close()
