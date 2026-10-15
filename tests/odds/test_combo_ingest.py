"""Combo ingest: six markets only, idempotent, other markets untouched, 2025 refused, backup sha.
Synthetic raw files in the vendor's shape; temp DuckDB; no network."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.odds import combo_ingest as ci
from nba.odds import combo_pull as cp
from nba.odds.history_pull import DDL
from nba.odds.the_odds_api import HistoryError, HoldoutRefused, default_resolver

CC = cp.load_combo_config()
PRA = CC.markets[0]


def raw_file(root: Path, season: int, gid: str, market: str = PRA) -> None:
    outs = [
        {"name": n, "description": "Joel Embiid", "price": -110, "point": 40.5}
        for n in ("Over", "Under")
    ]
    body = {
        "timestamp": "2025-01-15T23:00:00Z",
        "data": {
            "id": "ev1",
            "home_team": "Philadelphia 76ers",
            "away_team": "New York Knicks",
            "bookmakers": [
                {"key": "fanduel", "markets": [{"key": market, "outcomes": outs}]},
            ],
        },
    }
    blob: dict[str, Any] = {
        "season": season,
        "game_id": gid,
        "snapshot_kind": "t60",
        "params": {"date": "2025-01-15T23:10:00Z"},
        "body": body,
    }
    p = root / str(season) / gid / "odds_t60_us_abcd1234.json.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt") as fh:
        json.dump(blob, fh)


def make_db(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute(DDL)
    con.execute(
        "INSERT INTO odds_history (source, season, game_id, market, book, side) "
        "VALUES ('x', 2024, 'g0', 'player_points', 'fanduel', 'over')"
    )
    con.close()


def counts(db: Path) -> tuple[int, int]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        a = con.execute("SELECT count(*) FROM odds_history WHERE market='player_points'").fetchone()
        b = con.execute(
            "SELECT count(*) FROM odds_history WHERE market != 'player_points'"
        ).fetchone()
        assert a is not None and b is not None
        return int(a[0]), int(b[0])
    finally:
        con.close()


def test_ingest_idempotent_other_markets_untouched(tmp_path: Path) -> None:
    raw = tmp_path / "pull"
    raw_file(raw, 2024, "0022400555")
    db = tmp_path / "oh.duckdb"
    make_db(db)
    lines = ci.ingest(CC, raw, db, tmp_path / "bak", tmp_path / "o.json", lock=False)
    assert counts(db) == (1, 2)
    assert any("pra | fanduel | 2024 | t60 | 2" in ln for ln in lines)
    ci.ingest(CC, raw, db, tmp_path / "bak", lock=False)  # rerun: no duplicates
    assert counts(db) == (1, 2)
    assert len(list((tmp_path / "bak").glob("*.duckdb"))) == 2
    meta = json.loads((tmp_path / "o.json").read_text())
    assert meta["sha_before"] != meta["sha_after"]


def test_other_market_and_2025_refused(tmp_path: Path) -> None:
    raw = tmp_path / "p2"
    raw_file(raw, 2025, "0022500001")
    with pytest.raises(HoldoutRefused):
        ci.parse_raw_dir(CC, raw, default_resolver())
    raw3 = tmp_path / "p3"
    raw_file(raw3, 2024, "0022400555", market="player_points")
    with pytest.raises(HistoryError, match="outside the six"):
        ci.parse_raw_dir(CC, raw3, default_resolver())
