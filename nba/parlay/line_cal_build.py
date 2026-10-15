"""Build ``line_cal_map_v1.json`` (and the history frames the fixture uses) from stored OOF rows.

Read-only on the inputs: the MODEL_CLV T-60 pairs frames and the ODDS_HISTORY OOF rows of seasons
2023 and 2024 (``pairs_{season}.parquet``, ``model_rows_{season}.parquet``). Season 2025 is the
frozen holdout and is refused by name. The continuous median and spread of each leg come from the
production 199-point grid, recovered exactly from the stored integer survival vectors (the
harness's rule; no refit): per (stat, calendar month) the sorted standardized grid values are
bracketed by the survival counts.

    python -m nba.parlay.line_cal_build --pairs-dir <dir> --model-dir <dir> \\
        --out nba/parlay/line_cal_map_v1.json

The maintainer's run, a few seconds; nothing is written except ``--out``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.parlay import line_cal as lc

FIT_SEASONS = (2023, 2024)
MAX_SEASON = 2024
STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")
BOOKS: tuple[str, ...] = ("pinnacle", "draftkings")
NQ = 199
TAU_POS = {"q25": 0.25 * NQ - 0.5, "q50": 0.5 * NQ - 0.5, "q75": 0.75 * NQ - 0.5}
K4 = ["game_id", "player_id", "stat", "book"]
RULE_SHA8 = "a5441242"  # sha256 of docs/prereg/LINE_CALIBRATION.md above the results marker
NEED_COLS = [
    "game_id", "player_id", "stat", "book", "snapshot_kind", "snapshot_ts", "line", "price_over",
    "price_under", "p_over_mult", "p_model", "over", "tipoff_utc", "starter10", "season",
    "game_date",
]  # fmt: skip
GRID_COLS = ["game_id", "player_id", "stat", "game_date", "season", "c", "s", "p_ge"]
DRIVER_COLS = ["team_id", "min10", "m_recency", "mean_int", "n_out_rot"]


def file_sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_pairs(path: Path, season: int, books: tuple[str, ...] = BOOKS) -> pl.DataFrame:
    if season > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    d = pl.read_parquet(path, columns=NEED_COLS)
    if d["season"].n_unique() != 1 or int(d["season"][0]) != season:
        raise ValueError(f"{path.name}: expected exactly season {season}")
    d = d.filter((pl.col("snapshot_kind") == "t60") & pl.col("book").is_in(list(books)))
    if d.group_by(K4).len()["len"].max() != 1:
        raise ValueError("more than one main line per game/player/stat/book")
    return d.sort(K4)


def read_grid(path: Path, season: int) -> pl.DataFrame:
    if season > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    g = pl.read_parquet(path, columns=[*GRID_COLS, *DRIVER_COLS])
    if g["season"].n_unique() != 1 or int(g["season"][0]) != season:
        raise ValueError(f"{path.name}: expected exactly season {season}")
    return g.with_columns(
        pl.col("player_id").cast(pl.Int64), pl.col("game_date").dt.strftime("%Y-%m").alias("mo")
    )


def block_zq(c: np.ndarray, s: np.ndarray, pge: list[Any]) -> dict[str, Any]:
    """Bracket the block's 199 sorted standardized grid values from the stored integer counts."""
    lens = np.array([len(p) for p in pge])
    flat = np.concatenate([np.asarray(p, dtype=float) for p in pge])
    rows = np.repeat(np.arange(len(c)), lens)
    k = np.concatenate([np.arange(1, n + 1) for n in lens])
    N = np.rint(flat * NQ).astype(np.int64)
    if np.abs(flat * NQ - N).max() >= 1e-6:
        raise ValueError("survival values are not counts out of 199")
    t = (k - 0.5 - c[rows]) / s[rows]
    mx = np.full(NQ + 1, -np.inf)
    np.maximum.at(mx, N, t)
    mn = np.full(NQ + 1, np.inf)
    np.minimum.at(mn, N, t)
    suf = np.maximum.accumulate(mx[::-1])[::-1]
    pre = np.minimum.accumulate(mn)
    j = np.arange(1, NQ + 1)
    lo, hi = suf[NQ + 1 - j], pre[NQ - j]
    mid = np.where(
        np.isfinite(lo) & np.isfinite(hi), (lo + hi) / 2, np.where(np.isfinite(lo), lo, hi)
    )
    return {"lo": lo, "hi": hi, "mid": mid, "inconsistent": int(np.sum(hi < lo))}


def attach_grid(pairs: pl.DataFrame, grid: pl.DataFrame) -> pl.DataFrame:
    """Add ``med`` and ``spr`` (continuous median and robust spread) and the driver columns."""
    legs = pairs.with_columns(pl.col("game_date").dt.strftime("%Y-%m").alias("mo")).join(
        grid.select(["game_id", "player_id", "stat", "c", "s", *DRIVER_COLS]),
        on=["game_id", "player_id", "stat"],
        how="left",
    )
    if legs["c"].null_count():
        raise ValueError("a leg without its stored grid row")
    parts: list[pl.DataFrame] = []
    for (stat, mo), gb in grid.group_by(["stat", "mo"], maintain_order=True):
        z = block_zq(gb["c"].to_numpy(), gb["s"].to_numpy(), gb["p_ge"].to_list())
        lb = legs.filter((pl.col("stat") == stat) & (pl.col("mo") == mo))
        if lb.is_empty():
            continue
        q = lc.zq_to_quantiles(lb["c"].to_numpy(), lb["s"].to_numpy(), z["mid"], TAU_POS)
        parts.append(
            lb.with_columns(
                pl.Series("med", q["q50"]),
                pl.Series("spr", (q["q75"] - q["q25"]) / lc.IQR_TO_SD),
            )
        )
    out = pl.concat(parts).sort(K4)
    if out.height != pairs.height:
        raise ValueError("row count changed while attaching the grid")
    return out


def load_history(
    pairs_dir: Path, model_dir: Path, seasons: tuple[int, ...] = FIT_SEASONS
) -> tuple[pl.DataFrame, dict[str, str]]:
    if any(s > MAX_SEASON for s in seasons):
        raise ValueError("season 2025 is the frozen holdout")
    frames: list[pl.DataFrame] = []
    shas: dict[str, str] = {}
    for s in seasons:
        pp, mp = pairs_dir / f"pairs_{s}.parquet", model_dir / f"model_rows_{s}.parquet"
        frames.append(attach_grid(read_pairs(pp, s), read_grid(mp, s)))
        shas[pp.name], shas[mp.name] = file_sha(pp), file_sha(mp)
    return pl.concat(frames, how="vertical_relaxed"), shas


def to_arrays(d: pl.DataFrame) -> dict[str, Any]:
    return {
        "n": d.height,
        "date": d["game_date"].to_numpy().astype("datetime64[D]"),
        "book": d["book"].to_numpy().astype(str),
        "stat": d["stat"].to_numpy().astype(str),
        "pm": d["p_model"].cast(pl.Float64).to_numpy(),
        "line": d["line"].cast(pl.Float64).to_numpy(),
        "med": d["med"].cast(pl.Float64).to_numpy(),
        "spr": d["spr"].cast(pl.Float64).to_numpy(),
        "st10": d["starter10"].cast(pl.Float64).to_numpy(),
        "over": d["over"].cast(pl.Float64).to_numpy(),
    }


def build_payload(pairs_dir: Path, model_dir: Path) -> dict[str, Any]:
    d, shas = load_history(pairs_dir, model_dir)
    if not bool((d["season"] <= MAX_SEASON).all()):  # pragma: no cover - read_pairs refuses first
        raise ValueError("season 2025 reached the fit")
    last = d["game_date"].max()
    assert isinstance(last, date)
    block_start = np.datetime64(lc.MAX_FIT_DATE, "D") + np.timedelta64(1, "D")
    cells = lc.fit_cells(to_arrays(d), block_start, BOOKS, STATS)
    return lc.to_payload(
        cells,
        fit_through=lc.MAX_FIT_DATE.isoformat(),
        block_start=str(block_start),
        rule_sha8=RULE_SHA8,
        inputs=shas,
        note=(
            f"Fit on seasons 2023 and 2024 only (last fit row {last.isoformat()}); season 2025 not "
            "loaded. Static map: weights are 0.5 ** (age / 365 days) counted from block_start. "
            "Refit only after Devin logs a 2025 training-use row."
        ),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pairs-dir", required=True)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--out", default=str(lc.MAP_PATH))
    a = ap.parse_args(argv)
    payload = build_payload(Path(a.pairs_dir), Path(a.model_dir))
    Path(a.out).write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    print(f"wrote {a.out} sha256 {payload['map_sha256'][:8]}")
    for c in payload["cells"]:
        print(
            c["book"],
            c["stat"],
            c["n_fit"],
            None if c["beta"] is None else [round(v, 3) for v in c["beta"]],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
