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
    label_gap_candidates,
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


def test_unscanned_parquet_is_not_a_removal_flag(cfg: dict[str, Any]) -> None:
    from nba.datamanifest.diff import diff_manifests

    t = {"row_count": 1, "columns": {}}
    old = {"data_version": "a", "tables": {"games": t, "parquet:x.parquet": t}}
    new = {"data_version": "b", "tables": {"games": t}}
    out = diff_manifests(old, new, cfg)
    assert not any(f["kind"] == "TABLE_REMOVED" for f in out["flags"])
    out2 = diff_manifests(old, {"data_version": "c", "tables": {"parquet:x.parquet": t}}, cfg)
    assert any(f["kind"] == "TABLE_REMOVED" for f in out2["flags"])


def _make_label_db(path: Path) -> None:
    """games (home win drives a NULL), plus a player table with three feature columns:
    f_mid NULL iff minutes in [5, 15) (played-vs-unplayed rates look alike: asymmetry misses it),
    f_ridge NULL iff minutes < 5 (the ridge pattern), f_ok NULL on a hash pattern.
    A 2025 row pattern is planted to prove holdout seasons are excluded from the grouping."""
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE games (game_id VARCHAR PRIMARY KEY, season INT, home_pts INT, away_pts INT,"
        " lead_f DOUBLE)"
    )
    con.execute(
        "INSERT INTO games SELECT 'g' || i, 2023 + (i % 2), 100 + (i % 3), 100 + ((i + 1) % 3) * 2,"
        " CASE WHEN (100 + (i % 3)) > (100 + ((i + 1) % 3) * 2) THEN NULL ELSE 1.0 END"
        " FROM range(600) t(i)"
    )
    con.execute("CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT, minutes FLOAT)")
    con.execute(
        "INSERT INTO player_game_stats SELECT 'g' || (i // 2), i % 2,"
        " CASE WHEN i % 10 = 0 THEN 2.0 WHEN i % 10 < 4 THEN 10.0 ELSE 30.0 END"
        " FROM range(1200) t(i)"
    )
    con.execute(
        "CREATE TABLE feats (game_id VARCHAR, player_id INT, f_mid DOUBLE, f_ridge DOUBLE,"
        " f_ok DOUBLE)"
    )
    con.execute(
        "INSERT INTO feats SELECT 'g' || (i // 2), i % 2,"
        " CASE WHEN i % 10 BETWEEN 1 AND 3 THEN NULL ELSE 1.0 END,"
        " CASE WHEN i % 10 = 0 THEN NULL ELSE 1.0 END,"
        " CASE WHEN (i * 7919) % 13 < 3 THEN NULL ELSE 1.0 END FROM range(1200) t(i)"
    )
    con.close()


def test_label_gap_flags_planted_leaks_not_clean_column(
    tmp_path: Path, cfg: dict[str, Any]
) -> None:
    _make_label_db(tmp_path / "l.duckdb")
    cfg = {**cfg, "thresholds": {**cfg["thresholds"], "min_group_rows": 50}}
    m = build_manifest(tmp_path / "l.duckdb", cfg)
    hits = {(f["table"], f["column"]) for f in label_gap_candidates(m, cfg)}
    assert ("feats", "f_mid") in hits
    assert ("feats", "f_ridge") in hits
    assert ("games", "lead_f") in hits
    assert ("feats", "f_ok") not in hits
    # f_mid: the played-vs-unplayed check alone has no gap this size; the bucket check does
    asym = {f["column"] for f in leak_candidates(m, cfg) if f["table"] == "feats"}
    assert "f_mid" not in asym
    known = {**cfg, "known_label_gap": ["feats.f_mid"]}
    assert ("feats", "f_mid") not in {
        (f["table"], f["column"]) for f in label_gap_candidates(m, known)
    }


def test_label_gap_excludes_holdout_season(tmp_path: Path, cfg: dict[str, Any]) -> None:
    con = duckdb.connect(str(tmp_path / "h.duckdb"))
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, season INT, home_pts INT, away_pts INT, f INT)"
    )
    # f is NULL iff home win, but only in season 2025: grouping must ignore that season entirely
    con.execute(
        "INSERT INTO games SELECT 'g' || i, 2025, 100 + (i % 2) * 5, 102,"
        " CASE WHEN i % 2 = 1 THEN NULL ELSE 1 END FROM range(600) t(i)"
    )
    con.close()
    c = cfg
    m = build_manifest(tmp_path / "h.duckdb", c)
    assert m["tables"]["games"]["label_schemes"]["outcome"] == {}
    assert label_gap_candidates(m, c) == []


def _scheduled_db(path: Path, *, null_dates: list[str]) -> None:
    """2026 season: 40 scored games with box rows, plus NULL-score rows on ``null_dates``."""
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE games (game_id VARCHAR PRIMARY KEY, game_date DATE, season INT, "
        "home_team INT, away_team INT, home_pts INT, away_pts INT)"
    )
    con.execute(
        "INSERT INTO games SELECT 'g' || i, DATE '2026-10-20' - CAST(i AS INT), 2026, 1, 2, "
        "100, 90 FROM range(40) t(i)"
    )
    for k, d in enumerate(null_dates):
        con.execute("INSERT INTO games VALUES (?, ?, 2026, 1, 2, NULL, NULL)", [f"s{k}", d])
    con.execute("CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT, minutes FLOAT)")
    con.execute("INSERT INTO player_game_stats SELECT 'g' || i, 1, 30.0 FROM range(40) t(i)")
    con.close()


def test_scheduled_rows_today_raise_no_shift_or_coverage_flag(
    tmp_path: Path, cfg: dict[str, Any]
) -> None:
    import datetime as dt

    today = dt.date(2026, 10, 21)
    _scheduled_db(tmp_path / "before.duckdb", null_dates=[])
    _scheduled_db(tmp_path / "after.duckdb", null_dates=["2026-10-21"])
    con = duckdb.connect(str(tmp_path / "after.duckdb"))
    for k in range(10):  # a big slate: 11 NULL rows in all
        con.execute(
            "INSERT INTO games VALUES (?, DATE '2026-10-21', 2026, 1, 2, NULL, NULL)", [f"x{k}"]
        )
    con.close()
    old = build_manifest(tmp_path / "before.duckdb", cfg, today=today)
    new = build_manifest(tmp_path / "after.duckdb", cfg, today=today)
    assert new["tables"]["games"]["scheduled_rows"] == {"2026": 11}
    assert new["tables"]["games"]["stale_scheduled"] == 0
    assert new["tables"]["games"]["columns"]["home_pts"]["null_rate"] == 0.0
    assert new["tables"]["player_game_stats"]["seasons"]["2026"]["coverage"] == 1.0
    kinds = {f["kind"] for f in diff_manifests(old, new, cfg)["flags"]}
    assert not kinds & {"NULL_SHIFT", "COVERAGE_DROP", "STALE_SCHEDULED"}


def test_stale_scheduled_row_raises_a_flag(tmp_path: Path, cfg: dict[str, Any]) -> None:
    import datetime as dt

    today = dt.date(2026, 10, 21)
    _scheduled_db(tmp_path / "old.duckdb", null_dates=[])
    _scheduled_db(tmp_path / "new.duckdb", null_dates=["2026-10-18", "2026-10-20"])
    old = build_manifest(tmp_path / "old.duckdb", cfg, today=today)
    new = build_manifest(tmp_path / "new.duckdb", cfg, today=today)
    assert new["tables"]["games"]["stale_scheduled"] == 1  # yesterday's row is not stale
    flags = diff_manifests(old, new, cfg)["flags"]
    assert [f["table"] for f in flags if f["kind"] == "STALE_SCHEDULED"] == ["games"]
