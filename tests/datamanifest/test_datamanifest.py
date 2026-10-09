"""Tests for nba.datamanifest on tiny temp DuckDB files (no network)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.datamanifest import (
    DataManifestLocked,
    build_manifest,
    diff_manifests,
    latest_data_version,
    leak_candidates,
    load_config,
    render_diff_md,
)
from nba.datamanifest.manifest import schema_hash

N_GAMES = 400


def _make_db(path: Path, *, leak: bool, extra_col: bool = False, drop_rows: int = 0) -> None:
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE games (game_id VARCHAR PRIMARY KEY, season INT, game_date DATE)")
    con.execute(
        "INSERT INTO games SELECT 'g' || i, 2023 + (i % 2), DATE '2023-10-01' + CAST(i AS INT) "
        f"FROM range({N_GAMES}) t(i)"
    )
    con.execute(
        "CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT, minutes FLOAT, pts INT)"
    )
    con.execute(
        "INSERT INTO player_game_stats SELECT 'g' || (i // 2), i % 2, "
        "CASE WHEN i % 4 = 0 THEN 2.0 ELSE 30.0 END, 10 FROM range(800) t(i)"
    )
    # feature table: NULL iff unplayed (leak) vs NULL on a hash-like random pattern
    null_expr = "i % 4 = 0" if leak else "(i * 7919) % 13 < 3"
    con.execute("CREATE TABLE feats (game_id VARCHAR, player_id INT, f DOUBLE)")
    con.execute(
        "INSERT INTO feats SELECT 'g' || (i // 2), i % 2, "
        f"CASE WHEN {null_expr} THEN NULL ELSE 1.5 END FROM range(800) t(i)"
    )
    if extra_col:
        con.execute("ALTER TABLE feats ADD COLUMN g2 INT")
    if drop_rows:
        con.execute(f"DELETE FROM feats WHERE rowid < {drop_rows}")
    con.close()


@pytest.fixture
def cfg() -> dict[str, Any]:
    c = load_config()
    c["natural_keys"] = {"player_game_stats": ["game_id", "player_id"]}
    return c


def test_schema_hash_stable_and_order_independent() -> None:
    a = schema_hash([("x", "INTEGER"), ("y", "VARCHAR")])
    assert a == schema_hash([("y", "VARCHAR"), ("x", "INTEGER")])
    assert a != schema_hash([("x", "BIGINT"), ("y", "VARCHAR")])


def test_data_version_deterministic(tmp_path: Path, cfg: dict[str, Any]) -> None:
    _make_db(tmp_path / "a.duckdb", leak=False)
    _make_db(tmp_path / "b.duckdb", leak=False)
    m1 = build_manifest(tmp_path / "a.duckdb", cfg)
    m2 = build_manifest(tmp_path / "a.duckdb", cfg)
    m3 = build_manifest(tmp_path / "b.duckdb", cfg)
    assert m1["data_version"] == m2["data_version"] == m3["data_version"]
    _make_db(tmp_path / "c.duckdb", leak=False, drop_rows=5)
    assert build_manifest(tmp_path / "c.duckdb", cfg)["data_version"] != m1["data_version"]


def test_manifest_contents(tmp_path: Path, cfg: dict[str, Any]) -> None:
    _make_db(tmp_path / "a.duckdb", leak=False)
    m = build_manifest(tmp_path / "a.duckdb", cfg)
    g = m["tables"]["games"]
    assert g["row_count"] == N_GAMES and g["pk_duplicates"] == 0
    assert set(g["seasons"]) == {"2023", "2024"}
    assert m["tables"]["feats"]["seasons"]["2023"]["coverage"] is not None
    assert m["tables"]["player_game_stats"]["played_split"]["n_unplayed"] == 200


def test_diff_detects_column_row_drop_and_null_shift(tmp_path: Path, cfg: dict[str, Any]) -> None:
    _make_db(tmp_path / "old.duckdb", leak=False)
    _make_db(tmp_path / "new.duckdb", leak=False, extra_col=True, drop_rows=100)
    old = build_manifest(tmp_path / "old.duckdb", cfg)
    new = build_manifest(tmp_path / "new.duckdb", cfg)
    res = diff_manifests(old, new, cfg)
    kinds = {f["kind"] for f in res["flags"]}
    assert "ROW_DROP" in kinds
    assert any("column added g2" in c for c in res["changes"])
    assert "## Data diff" in render_diff_md(res, old, new)
    shifted = copy.deepcopy(old)
    shifted["tables"]["feats"]["columns"]["f"]["null_rate"] = 0.9
    assert any(f["kind"] == "NULL_SHIFT" for f in diff_manifests(old, shifted, cfg)["flags"])


def test_leak_flag_fires_on_planted_pattern_not_random(tmp_path: Path, cfg: dict[str, Any]) -> None:
    _make_db(tmp_path / "clean.duckdb", leak=False)
    _make_db(tmp_path / "leaky.duckdb", leak=True)
    clean = build_manifest(tmp_path / "clean.duckdb", cfg)
    leaky = build_manifest(tmp_path / "leaky.duckdb", cfg)
    assert [f for f in leak_candidates(clean, cfg) if f["table"] == "feats"] == []
    hits = [f for f in leak_candidates(leaky, cfg) if f["table"] == "feats"]
    assert [f["column"] for f in hits] == ["f"]
    new_flags = diff_manifests(clean, leaky, cfg)["flags"]
    assert any(f["kind"] == "POSSIBLE_LEAK" and "NEW" in f["detail"] for f in new_flags)


def test_locked_db_reports_not_hangs(tmp_path: Path, cfg: dict[str, Any]) -> None:
    import subprocess
    import sys

    _make_db(tmp_path / "a.duckdb", leak=False)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import duckdb,sys,time;c=duckdb.connect(sys.argv[1]);"
            "print('x',flush=True);time.sleep(20)",
            str(tmp_path / "a.duckdb"),
        ],
        stdout=subprocess.PIPE,
    )
    try:
        assert holder.stdout is not None
        holder.stdout.readline()
        cfg["lock_retry"] = {"max_wait_s": 1, "first_delay_s": 0.2}
        with pytest.raises(DataManifestLocked):
            build_manifest(tmp_path / "a.duckdb", cfg)
    finally:
        holder.kill()


def test_latest_data_version_reads_newest(tmp_path: Path) -> None:
    (tmp_path / "20260101T000000Z_aaa.json").write_text(json.dumps({"data_version": "aaa"}))
    (tmp_path / "20260102T000000Z_bbb.json").write_text(json.dumps({"data_version": "bbb"}))
    assert latest_data_version(tmp_path) == "bbb"
    assert latest_data_version(tmp_path / "none", tmp_path / "nope.json") is None
