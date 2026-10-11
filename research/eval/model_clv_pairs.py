# ruff: noqa: E501
"""Rebuild the paired t60/t5 frames MODEL_CLV reads (provenance for ``pairs_{2023,2024}.parquet``).

    .venv/bin/python -m research.eval.model_clv_pairs --out data/ops/model_clv_pairs

Read-only on ``nba.duckdb`` (inside ``odds_history_eval``); season <= 2024 asserted. This is the
2026-10-11 scratch builder moved into the tree so the frozen parquet sha256 has a recipe; it needs the
``reports/odds_history`` model/market row files. Not exercised by the unit tests.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl

import research.eval.odds_history_eval as oh


def build(out: Path, db: str = "nba.duckdb") -> None:
    out.mkdir(parents=True, exist_ok=True)
    for season in (2023, 2024):
        assert season <= 2024
        model = oh.load_model_rows(season, oh.OUT_DIR)
        market = pl.read_parquet(oh.OUT_DIR / f"market_rows_{season}.parquet")
        played = oh.load_played(season, db)
        tips = oh.load_tips(season, db)
        pairs, _ = oh.pairs_for_season(model, market, played, tips)
        oh.leak_guard(pairs)
        pairs = oh.add_slice_columns(pairs, model).with_columns(pl.lit(season).alias("season"))
        gd = model.select("game_id", "game_date").unique("game_id")
        pairs.join(gd, on="game_id", how="left").write_parquet(out / f"pairs_{season}.parquet")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--db", default="nba.duckdb")
    a = ap.parse_args()
    build(a.out, a.db)
