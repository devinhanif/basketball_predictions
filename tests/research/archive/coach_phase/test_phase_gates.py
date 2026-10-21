# ruff: noqa: E501
"""Gate scripts for F10A_RIVALRY and COACH_PHASE on synthetic frames and an in-memory DuckDB (no real data)."""

from __future__ import annotations

import datetime as dt

import duckdb
import numpy as np
import polars as pl

from research.archive.coach_phase import coach_phase_gates as G
from research.eval import f10a_gates as F

BOS, NYK, LAL, DEN = 1610612738, 1610612752, 1610612747, 1610612743


def test_mde_is_clustered_noise_scale() -> None:
    rng = np.random.default_rng(0)
    df = pl.DataFrame({"gid": [f"g{i // 10}" for i in range(500)], "d": rng.normal(0, 1, 500)})
    m = G.mde(df, "d")
    assert 0.05 < m < 0.4
    assert G.mde(df.with_columns(pl.col("d") * 2), "d") > m  # scales with the noise


def test_phase_frame_and_slice_table() -> None:
    gid = np.array(["0022300001", "0022300002", "0042300101", "0052300001"])
    idx = pl.DataFrame(
        {"gid": ["0022300001", "0022300002"], "gp_max": [10, 70], "gp_min": [10, 69]}
    )
    f = G.phase_frame(
        gid, np.array([2023, 2023, 2023, 2023]), {"d": np.array([0.1, 0.2, 0.3, 0.4])}, idx
    )
    assert f["late15"].to_list() == [False, True, False, False]
    assert f["po"].to_list() == [False, False, True, False]
    assert f["phase"].to_list() == [False, True, True, False]
    f2 = f.vstack(f.with_columns(pl.col("gid") + "x", pl.lit(2024, dtype=pl.Int64).alias("season")))
    t = G.slice_table(f2, "d")
    assert t["2023"]["phase"]["rows"] == 2 and t["2024"]["phase"]["games"] == 2


def _db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INTEGER, home_team INTEGER, away_team INTEGER, home_pts INTEGER, away_pts INTEGER)"
    )
    rows = []
    d0 = dt.date(2023, 10, 24)
    for i in range(6):
        rows.append(
            (
                f"00223{i:05d}",
                d0 + dt.timedelta(days=2 * i),
                2023,
                BOS if i % 2 == 0 else NYK,
                NYK if i % 2 == 0 else BOS,
                100,
                90,
            )
        )
        rows.append((f"00223{i + 50:05d}", d0 + dt.timedelta(days=2 * i), 2023, LAL, DEN, 100, 90))
    for i in range(5):
        rows.append(
            (
                f"0042300{i + 1:03d}",
                dt.date(2024, 4, 20) + dt.timedelta(days=2 * i),
                2023,
                BOS,
                NYK,
                100 if i < 4 else 80,
                90,
            )
        )
    for i in range(5):
        rows.append(
            (
                f"0042400{i + 1:03d}",
                dt.date(2025, 4, 20) + dt.timedelta(days=2 * i),
                2024,
                BOS,
                NYK,
                100,
                90,
            )
        )
    for r in rows:
        con.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?)", list(r))
    con.execute(
        "CREATE TABLE team_coach_games (game_id VARCHAR, team_id INTEGER, coach_name VARCHAR, coach_id INTEGER, source VARCHAR, known_at TIMESTAMP)"
    )
    for r in rows:
        for t, c in ((r[3], 1), (r[4], 2)):
            con.execute(
                "INSERT INTO team_coach_games VALUES (?,?,?,?,?,?)",
                [r[0], t, f"c{c}", c, "x", None],
            )
    return con


def test_gate_stages_on_memory_db() -> None:
    con = _db()
    idx = G.team_game_index(con)
    assert (
        idx.height == 12 and idx["gp_max"].max() == 6
    )  # cum_count is 1-based: the gate counts the current game
    coach = G.gate_coach(con)
    assert coach["2023"]["playoff_team_games"] == 10 and "pass_report_season_coaches" in coach
    sc = G.gate_series_and_clinch(con)
    assert sc["playoff_team_games"] == 20 and sc["max_series_game"] == 5
    assert set(sc["clinch_late15_team_games"]) == {"2023", "2024"}


def test_f10a_mde_play_rate_and_summarize() -> None:
    rng = np.random.default_rng(1)
    n = 600
    df = pl.DataFrame(
        {
            "game_id": [f"g{i // 3}" for i in range(n)],
            "player_id": [i % 50 for i in range(n)],
            "season": [2023] * n,
            "ntv": rng.random(n) < 0.2,
            "played": rng.random(n) < 0.5,
            "div_rival": rng.random(n) < 0.2,
            "hist_rival": rng.random(n) < 0.05,
            "any_rival": rng.random(n) < 0.25,
        }
    )
    m = F.mde_play_rate(df, "div_rival", "game_id")
    assert m is not None and 0.02 < m < 0.4
    assert F.mde_play_rate(df.head(10), "div_rival", "game_id") is None
    games = pl.DataFrame(
        {
            "game_id": ["g0", "g1", "h0", "i0"],
            "game_date": [dt.date(2023, 11, 1)] * 4,
            "season": [2023, 2023, 2022, 2024],
            "home_team": [BOS, LAL, BOS, BOS],
            "away_team": [NYK, DEN, NYK, NYK],
        }
    )
    s = F.summarize(df, games, {"g0"})
    assert s["2023"]["questionable_player_games"] == n and s["2023"]["div_rival"]["n_rows"] == int(
        df["div_rival"].sum()
    )
    assert s["2023"]["report_coverage"]["div_rival"]["rival_games"] == 1
