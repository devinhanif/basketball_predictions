"""Sequence exports for the ENSEMBLE rule (docs/prereg/ENSEMBLE.md).

Two npz files, built in separate passes so no 2021-22 game ever enters a window:

* ``cur``: the current database, seasons 2022-2024 (the ``player_sequences`` export, unchanged).
* ``hist``: ``data/history/nba_history.duckdb``, seasons 2019-2020 only. Every report token is
  missing exactly as a reportless game (``player_availability`` is not used at all); positions
  come from ``players_static`` of the history DB plus the per-player cache
  ``data/players_static/<id>.parquet`` (a player with no position keeps ``pos_* = 0``, exactly
  as production treats a player with no position; the residual share is reported).

Season 2025 is never loaded. Read-only on both databases.

    uv run python -m research.features.seq_ensemble_export all --out data/colab/seq_ensemble
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import polars as pl
import yaml

from research.features import player_sequences as P

HIST_DB = "data/history/nba_history.duckdb"
HIST_SEASONS = (2019, 2020)
STATIC_CACHE = Path("data/players_static")
REPORT_CTX = (
    "has_report",
    "st_available",
    "st_probable",
    "st_questionable",
    "st_doubtful",
    "st_out",
    "n_rep_out",
    "vac_rep",
    "n_return",
    "vac_return",
)
POS_CTX = ("pos_g", "pos_f", "pos_c")


def report_ctx_index() -> list[int]:
    """Indices of the report channels in the context vector (zeroed in the K arms)."""
    return [P.CTX_FEATURES.index(c) for c in REPORT_CTX]


def _elo(raw: P.RawInputs, elo_config: str) -> None:
    from nba.props.context_residual import asof_elo_margin

    params = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    g = pl.from_pandas(raw.games).with_columns(pl.col("game_date").cast(pl.Date))
    raw.elo = asof_elo_margin(g, params).to_pandas()


def cache_static(cache_dir: Path = STATIC_CACHE) -> pd.DataFrame:
    rows: list[tuple[int, str]] = []
    for f in sorted(cache_dir.glob("*.parquet")):
        d = pl.read_parquet(f)
        if "position" in d.columns and d.height and d["position"][0]:
            rows.append((int(f.stem), str(d["position"][0])))
    return pd.DataFrame(rows, columns=["player_id", "position"])


def merge_static(db_static: pd.DataFrame, cached: pd.DataFrame) -> pd.DataFrame:
    """DB rows win; the cache only fills players the DB lacks (or has an empty position for)."""
    have = db_static[db_static["position"].fillna("").astype(str).str.len() > 0]
    extra = cached[~cached["player_id"].isin(have["player_id"])]
    return pd.concat([have[["player_id", "position"]], extra], ignore_index=True)


def build_history(
    db_path: str = HIST_DB,
    elo_config: str = "configs/mov_elo_tuned.yaml",
    cache_dir: Path = STATIC_CACHE,
) -> P.SeqExport:
    raw = P.load_raw(db_path, max_season=max(HIST_SEASONS))
    if sorted(int(s) for s in raw.games["season"].unique()) != list(HIST_SEASONS):
        raise ValueError("history export expects exactly seasons 2019 and 2020")
    raw.avail = raw.avail.iloc[0:0]  # report tokens missing, as a reportless game
    raw.static = merge_static(raw.static, cache_static(cache_dir))
    _elo(raw, elo_config)
    cfg = P.SeqConfig(min_target_season=min(HIST_SEASONS), max_season=max(HIST_SEASONS))
    return P.build_export(raw, cfg)


def build_current(
    db_path: str = "nba.duckdb", elo_config: str = "configs/mov_elo_tuned.yaml"
) -> P.SeqExport:
    raw = P.load_raw(db_path, max_season=2024)
    _elo(raw, elo_config)
    return P.build_export(raw)


def write(exp: P.SeqExport, out: Path, name: str) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    arrays = dict(exp.arrays)
    arrays["step_features"] = np.array(exp.step_features)
    arrays["ctx_features"] = np.array(exp.ctx_features)
    arrays["date_str"] = np.array(
        pd.to_datetime(exp.meta["game_date"]).dt.strftime("%Y-%m-%d").to_numpy(), dtype="U10"
    )
    path = out / f"seq_ensemble_{name}.npz"
    np.savez_compressed(path, **arrays)  # type: ignore[arg-type]
    exp.meta.to_parquet(out / f"seq_ensemble_{name}_meta.parquet", index=False)
    return path


def residual_position_share(exp: P.SeqExport) -> dict[str, Any]:
    ctx = exp.arrays["ctx"]
    idx = [P.CTX_FEATURES.index(c) for c in POS_CTX]
    none = ctx[:, idx].sum(axis=1) == 0
    return {"rows": int(len(ctx)), "no_position_rows": int(none.sum()), "share": float(none.mean())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("which", choices=("cur", "hist", "all"))
    ap.add_argument("--out", default="data/colab/seq_ensemble")
    a = ap.parse_args(argv)
    out = Path(a.out)
    info: dict[str, Any] = {}
    if a.which in ("hist", "all"):
        h = build_history()
        info["hist"] = {
            "npz": str(write(h, out, "hist")),
            "targets": int(len(h.meta)),
            "seasons": sorted(int(s) for s in h.meta["season"].unique()),
            "position": residual_position_share(h),
        }
    if a.which in ("cur", "all"):
        c = build_current()
        info["cur"] = {
            "npz": str(write(c, out, "cur")),
            "targets": int(len(c.meta)),
            "seasons": sorted(int(s) for s in c.meta["season"].unique()),
        }
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
