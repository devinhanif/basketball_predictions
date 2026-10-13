# ruff: noqa: E501
"""Read-only gate item D4 for SEQ_LEARNING_CURVE_2: share of played S1 rows (2015-2018, and 2013-14/2014-15 once ingested)
whose player has no position in any static source (production players_static, history players_static, per-player cache).
    .venv/bin/python -I reports/prereg_seq_learning_curve_2/gate_static.py
No model, season <= 2024 in SQL, read-only connections.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
HIST = ROOT / "data/history/nba_history.duckdb"
CUR = ROOT / "nba.duckdb"
CACHE = ROOT / "data/players_static"
OUT = Path(__file__).resolve().parent / "gate_static.json"


def ids_with_static(path: Path) -> set[int]:
    con = duckdb.connect(str(path), read_only=True)
    try:
        df = con.execute(
            "SELECT player_id FROM players_static WHERE position IS NOT NULL AND position <> ''"
        ).df()
    finally:
        con.close()
    return set(df.player_id.astype(int))


def main() -> None:
    known = ids_with_static(CUR) | ids_with_static(HIST)
    for f in CACHE.glob("*.parquet"):
        d = pd.read_parquet(f)
        if "position" in d.columns and d["position"].notna().any():
            known.add(int(f.stem))
    con = duckdb.connect(str(HIST), read_only=True)
    try:
        df = con.execute(
            "SELECT g.season, s.player_id, s.minutes FROM games g JOIN player_game_stats s USING (game_id) "
            "WHERE g.season <= 2018 AND s.minutes > 0"
        ).df()
    finally:
        con.close()
    df["known"] = df.player_id.astype(int).isin(known)
    out: dict[str, object] = {"per_season": {}}
    for s, g in df.groupby("season"):
        out["per_season"][int(s)] = {  # type: ignore[index]
            "played_rows": len(g),
            "unknown_rows": int((~g.known).sum()),
            "unknown_share": float((~g.known).mean()),
            "unknown_ids": int(g.loc[~g.known, "player_id"].nunique()),
            "mean_min_unknown": float(g.loc[~g.known, "minutes"].mean()),
            "mean_min_known": float(g.loc[g.known, "minutes"].mean()),
        }
    out["pooled_unknown_share"] = float((~df.known).mean())
    out["pooled_unknown_ids"] = int(df.loc[~df.known, "player_id"].nunique())
    out["pooled_unknown_rows"] = int((~df.known).sum())
    out["pooled_rows"] = len(df)
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
