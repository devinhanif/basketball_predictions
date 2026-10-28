"""F12B gate and premise helpers on a synthetic two-game DB (no real data)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import polars as pl
import pytest

from nba.eval import f12b_closing_risk as f


def _db(path: Path) -> str:
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INTEGER, home_team INTEGER, "
        "away_team INTEGER, home_pts INTEGER, away_pts INTEGER)"
    )
    # g1 close (final 2), g2 blowout (final 30)
    con.execute(
        "INSERT INTO games VALUES ('g1', '2023-11-01', 2023, 1, 2, 100, 98), "
        "('g2', '2023-11-02', 2023, 1, 2, 130, 100)"
    )
    con.execute(
        "CREATE TABLE possessions (game_id VARCHAR, poss_idx INTEGER, period INTEGER, "
        "clock_start FLOAT, clock_end FLOAT, off_team INTEGER, score_diff INTEGER, pts INTEGER)"
    )
    # g1: never garbage. g2: margin 30 entering Q4 -> garbage from 720 (A1) to the end.
    con.execute(
        "INSERT INTO possessions VALUES "
        "('g1', 0, 4, 704, 690, 1, 2, 0), ('g1', 1, 4, 690, 0, 2, -2, 0), "
        "('g2', 0, 4, 704, 600, 1, 30, 0), ('g2', 1, 4, 600, 0, 2, -30, 0)"
    )
    con.execute(
        "CREATE TABLE stints (game_id VARCHAR, team_id INTEGER, period INTEGER, start_clock FLOAT, "
        "end_clock FLOAT, players INTEGER[5], stint_idx INTEGER)"
    )
    for g in ("g1", "g2"):
        for per in (1, 2, 3, 4):
            con.execute(
                "INSERT INTO stints VALUES (?, 1, ?, 720, 0, [10, 11, 12, 13, 14], ?)",
                [g, per, per],
            )
    con.execute(
        "CREATE TABLE player_game_stats (game_id VARCHAR, player_id INTEGER, minutes DOUBLE)"
    )
    for g in ("g1", "g2"):
        for p in (10, 11, 12, 13, 14):
            con.execute("INSERT INTO player_game_stats VALUES (?, ?, 48.0)", [g, p])
    con.execute("CREATE TABLE players_static (player_id INTEGER, draft_year INTEGER)")
    con.execute(
        "INSERT INTO players_static VALUES "
        "(10, 2022), (11, 2015), (12, 2015), (13, 2015), (14, 2015)"
    )
    con.close()
    return str(path)


def test_garbage_tiers() -> None:
    assert f.is_garbage(600.0, 25) and not f.is_garbage(600.0, 24)
    assert f.is_garbage(400.0, 20) and not f.is_garbage(400.0, 19)
    assert f.is_garbage(100.0, 10) and not f.is_garbage(100.0, 9)


def test_load_excludes_garbage_from_720(tmp_path: Path) -> None:
    df, gm = f.load(_db(tmp_path / "t.duckdb"), (2023,))
    g = dict(gm.select(["game_id", "garbage_min"]).iter_rows())
    assert g["g1"] == 0.0
    assert g["g2"] == pytest.approx(12.0)  # the A1 extension covers 720 -> 704 too
    row = df.filter((pl.col("game_id") == "g2") & (pl.col("player_id") == 10)).row(0, named=True)
    assert row["q4"] == pytest.approx(12.0) and row["q4ng"] == pytest.approx(0.0)
    assert row["a_g"] == pytest.approx(0.0) and row["idx"] == 2


def test_ratio_baseline_is_one() -> None:
    sub = pl.DataFrame({"q4ng": [6.0, 3.0], "a_g": [6.0, 3.0], "q13": [36.0, 36.0]})
    assert f.ratio_ng(sub) == pytest.approx(1.0)


def test_boot_gap_seeded_and_zero_when_equal() -> None:
    a = pl.DataFrame({"game_id": ["a", "b"], "q4": [4.0, 6.0], "q13": [12.0, 18.0]})
    b = pl.DataFrame({"game_id": ["c", "d"], "q4": [2.0, 3.0], "q13": [6.0, 9.0]})
    x = f.boot_gap(a, b, ["a", "b", "c", "d"], "q4", "q13")
    y = f.boot_gap(a, b, ["a", "b", "c", "d"], "q4", "q13")
    assert np.array_equal(x, y, equal_nan=True)
    assert np.allclose(x[np.isfinite(x)], 0.0)


def test_premise_refuses_without_frozen_sha(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="refused"):
        f.run_premise(_db(tmp_path / "t.duckdb"), "deadbeef")


def test_sql_refuses_holdout_season() -> None:
    with pytest.raises(AssertionError):
        f._sql((2025,))
