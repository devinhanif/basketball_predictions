"""Manual position fill: only NULL/empty rows change, sidecar records provenance (no network)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from nba.ingest.history_positions import SIDECAR, apply_positions, file_sha256


def _setup(tmp: Path) -> tuple[Path, Path]:
    db = tmp / "h.duckdb"
    c = duckdb.connect(str(db))
    c.execute("CREATE TABLE players_static (player_id INTEGER PRIMARY KEY, position VARCHAR)")
    c.execute("INSERT INTO players_static VALUES (1, NULL), (2, ''), (3, 'Guard'), (5, NULL)")
    c.close()
    csv = tmp / "p.csv"
    csv.write_text(
        "player_id,name,source_position,position,source_url,retrieved_at\n"
        "1,A,Center,Center,u1,2026-10-13\n"
        "2,B,Small forward,Forward,u2,2026-10-13\n"
        "3,C,Point guard,Forward,u3,2026-10-13\n"
        "4,D,Center,Center,u4,2026-10-13\n"
    )
    return db, csv


def test_fills_only_empty_and_records_provenance(tmp_path: Path) -> None:
    db, csv = _setup(tmp_path)
    res = apply_positions(db, csv, expect_sha=file_sha256(csv)[:8], backup_dir=tmp_path / "bk")
    assert res["ok"] and res["written"] == 2 and res["skipped_ids"] == [3, 4]
    c = duckdb.connect(str(db), read_only=True)
    got = dict(c.execute("SELECT player_id, position FROM players_static").fetchall())
    assert got == {1: "Center", 2: "Forward", 3: "Guard", 5: None}
    assert c.execute(f"SELECT count(*) FROM {SIDECAR}").fetchone()[0] == 2
    c.close()
    assert Path(res["backup"]).exists()


def test_bad_sha_refused(tmp_path: Path) -> None:
    db, csv = _setup(tmp_path)
    with pytest.raises(ValueError):
        apply_positions(db, csv, expect_sha="deadbeef", backup_dir=tmp_path / "bk")


def test_unquoted_comma_in_url(tmp_path: Path) -> None:
    from nba.ingest.history_positions import read_positions

    csv = tmp_path / "q.csv"
    csv.write_text(
        "player_id,name,source_position,position,source_url,retrieved_at\n"
        "7,E,Power forward,Forward,https://x/Tony_(basketball,_born_1992),2026-10-13\n"
    )
    df = read_positions(csv)
    assert df["source_url"][0] == "https://x/Tony_(basketball,_born_1992)"
    assert df["retrieved_at"][0] == "2026-10-13"


def test_static_apply_after_manual_load_keeps_positions(tmp_path: Path) -> None:
    """Manual load, then a cache apply with an empty position: positions must survive."""
    import polars as pl

    from nba.ingest.history_static_fix import load_players_static_keep
    from nba.ingest.players_static import load_players_static

    db, csv = _setup(tmp_path)
    apply_positions(db, csv, expect_sha=file_sha256(csv)[:8], backup_dir=tmp_path / "bk")
    c = duckdb.connect(str(db))
    c.execute("ALTER TABLE players_static ADD COLUMN height_in FLOAT")
    c.execute("UPDATE players_static SET height_in = 80 WHERE player_id = 1")
    cache = pl.DataFrame(
        {"player_id": [1, 2], "position": [None, ""], "height_in": [None, 78.0]},
        schema={"player_id": pl.Int64, "position": pl.Utf8, "height_in": pl.Float64},
    )
    cols = ["player_id", "position", "height_in"]
    import nba.ingest.players_static as ps

    old = ps._SCHEMA
    ps._SCHEMA = cols
    try:
        load_players_static_keep(c, cache)
        got = dict(c.execute("SELECT player_id, position FROM players_static").fetchall())
        assert got[1] == "Center" and got[2] == "Forward"
        assert (
            c.execute("SELECT height_in FROM players_static WHERE player_id=1").fetchone()[0] == 80
        )
        # the old code path blanks them
        load_players_static(c, cache)
        assert (
            dict(c.execute("SELECT player_id, position FROM players_static").fetchall())[1] is None
        )
    finally:
        ps._SCHEMA = old
        c.close()
