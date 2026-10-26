"""REPLACEMENT_VALUE_ELO (DRAFT) features and gate math: parity, as-of, absorption, placebo.

Synthetic in-memory league (the committed fixture has 3 games). No outcome is fitted anywhere.
"""

from __future__ import annotations

import datetime as dt
import math

import duckdb
import numpy as np
import polars as pl
import pytest

from nba.db.connect import connect
from nba.features.injury_report import ReportTriggerConfig, load_report_rows
from nba.models.injury_elo import (
    InjuryFeatureConfig,
    asof_values,
    build_injury_features,
    build_value_state,
    league_rate_by_date,
    load_stats_frame,
)
from research.eval.replacement_value_elo_gate import (
    floor_power,
    oracle_gain,
    recover_block_coefs,
)
from research.models.replacement_value_elo import (
    ARMS,
    ReplacementConfig,
    asof_components,
    build_replacement_features,
    capped_absorption,
    eligible_rosters,
)

CFG = ReplacementConfig(base=InjuryFeatureConfig(tip_source="proxy19"))


def _make_db(seed: int = 0, days: int = 50) -> duckdb.DuckDBPyConnection:
    rng = np.random.default_rng(seed)
    con = connect(":memory:")
    teams = [1, 2, 3, 4]
    start = dt.date(2023, 10, 20)
    games, stats, avail = [], [], []
    gid = 0
    for day in range(days):
        d = start + dt.timedelta(days=day)
        order = [int(t) for t in rng.permutation(teams)]
        for h, a in ((order[0], order[1]), (order[2], order[3])):
            gid += 1
            g = f"G{gid:05d}"
            games.append((g, d, 2023, h, a, 100 + int(rng.integers(0, 20)), 100))
            for t in (h, a):
                outs = set()
                if day >= 10 and day % 3 == 0:
                    outs = {t * 100 + 1, t * 100 + 2} if day % 2 == 0 else {t * 100 + 1}
                dbt = {t * 100 + 3} if (day >= 10 and day % 5 == 0) else set()
                for k in range(1, 11):
                    pid = t * 100 + k
                    if pid in outs or pid in dbt:
                        stats.append((g, pid, t, 0.0, 0, 0, 0, 0, 0, 0))
                        continue
                    mins = 34.0 if k <= 3 else (24.0 if k <= 6 else 8.0)
                    stats.append(
                        (g, pid, t, mins, int(mins * (1.2 if k <= 2 else 0.5) + rng.integers(0, 4)),
                         int(rng.integers(0, 6)), int(rng.integers(0, 4)), 1, 0, 1)
                    )  # fmt: skip
                asof = dt.datetime.combine(d, dt.time(17, 30))
                for pid in outs:
                    avail.append((pid, asof, g, "out", "nba_official_report"))
                for pid in dbt:
                    avail.append((pid, asof, g, "doubtful", "nba_official_report"))
    con.executemany(
        "INSERT INTO games (game_id,game_date,season,home_team,away_team,home_pts,away_pts)"
        " VALUES (?,?,?,?,?,?,?)",
        games,
    )
    con.executemany(
        "INSERT INTO player_game_stats (game_id,player_id,team_id,minutes,pts,reb,ast,stl,blk,tov)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        stats,
    )
    con.executemany(
        "INSERT INTO player_availability (player_id,as_of,game_id,status,source)"
        " VALUES (?,?,?,?,?)",
        avail,
    )
    return con


def _games(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    return con.execute(
        "SELECT game_id, game_date, season, home_team, away_team FROM games "
        "ORDER BY game_date, game_id"
    ).pl()


def _rows(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    return load_report_rows(con, ReportTriggerConfig(tip_source="proxy19"))


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    return _make_db()


@pytest.fixture(scope="module")
def built(con: duckdb.DuckDBPyConnection) -> tuple[pl.DataFrame, pl.DataFrame]:
    stats = load_stats_frame(con, 2024)
    return build_replacement_features(_games(con), stats, _rows(con), CFG)


def test_a0_matches_production(
    con: duckdb.DuckDBPyConnection, built: tuple[pl.DataFrame, pl.DataFrame]
) -> None:
    feats, _ = built
    games = _games(con)
    stats = load_stats_frame(con, 2024)
    prod = build_injury_features(con, games, stats, CFG.base, with_oracle=False)
    j = prod.join(feats, on="game_id")
    assert j.height == games.height
    assert np.allclose(j["d_out"].to_numpy(), j["d_out_a0"].to_numpy(), atol=1e-12)
    assert np.allclose(j["d_doubt"].to_numpy(), j["d_doubt_a0"].to_numpy(), atol=1e-12)
    assert (j["n_rot_out"] == j["n_rot_out_right"]).all()
    assert float(np.abs(j["d_out"].to_numpy()).sum()) > 0


def test_components_reproduce_asof_values(con: duckdb.DuckDBPyConnection) -> None:
    stats = load_stats_frame(con, 2024)
    state = build_value_state(stats, CFG.base.value)
    league = league_rate_by_date(stats)
    pairs = (
        stats.select(["game_id", pl.col("player_id").cast(pl.Int64), "game_date"])
        .unique()
        .sort(["game_id", "player_id"])
        .head(300)
    )
    a = asof_values(pairs, state, league, CFG.base.value).sort(["game_id", "player_id"])
    b = asof_components(pairs, state, league, CFG.base.value).sort(["game_id", "player_id"])
    assert np.allclose(a["value"].to_numpy(), b["value"].to_numpy())


def test_all_arm_columns_present(built: tuple[pl.DataFrame, pl.DataFrame]) -> None:
    feats, diag = built
    for arm in ARMS:
        assert f"d_out_{arm}" in feats.columns and f"d_doubt_{arm}" in feats.columns
    assert diag.filter(pl.col("n_flagged") > 0).height > 0


def test_planted_future_does_not_move_tonight(
    con: duckdb.DuckDBPyConnection, built: tuple[pl.DataFrame, pl.DataFrame]
) -> None:
    feats, _ = built
    games = _games(con)
    target = feats.filter(pl.col("d_out_pool") != 0)["game_id"][5]
    stats = load_stats_frame(con, 2024)
    planted = stats.with_columns(
        pl.when(pl.col("game_id") == target)
        .then(pl.col("comp") * 10 + 50)
        .otherwise(pl.col("comp"))
        .alias("comp"),
        pl.when(pl.col("game_id") == target)
        .then(pl.lit(48.0))
        .otherwise(pl.col("minutes"))
        .alias("minutes"),
    )
    f2, _ = build_replacement_features(games, planted, _rows(con), CFG)
    a = feats.filter(pl.col("game_id") == target)
    b = f2.filter(pl.col("game_id") == target)
    for arm in ARMS:
        assert a[f"d_out_{arm}"][0] == pytest.approx(b[f"d_out_{arm}"][0], abs=1e-12)


def test_capped_absorption() -> None:
    m = np.array([36.0, 30.0, 20.0, 10.0])
    extra, over = capped_absorption(m, 20.0, 1000.0)
    assert over == 0.0
    assert np.allclose(extra, 20.0 * m / m.sum())
    extra, over = capped_absorption(m, 40.0, 38.0)
    assert over == pytest.approx(0.0)
    assert extra.sum() == pytest.approx(40.0)
    assert ((m + extra) <= 38.0 + 1e-9).all()
    assert extra[0] == pytest.approx(2.0)
    extra, over = capped_absorption(m, 200.0, 38.0)
    assert over == pytest.approx(200.0 - (38 * 4 - m.sum()))


def test_ladder_equals_pool_without_caps(
    con: duckdb.DuckDBPyConnection, built: tuple[pl.DataFrame, pl.DataFrame]
) -> None:
    feats, _ = built
    stats = load_stats_frame(con, 2024)
    big = ReplacementConfig(base=CFG.base, minutes_cap=1e9)
    f2, _ = build_replacement_features(_games(con), stats, _rows(con), big)
    assert np.allclose(f2["d_out_ladder"].to_numpy(), f2["d_out_pool"].to_numpy())
    # with the real cap the two differ somewhere (two-starter absences)
    assert not np.allclose(feats["d_out_ladder"].to_numpy(), feats["d_out_pool"].to_numpy())


def test_placebo_breaks_identity(built: tuple[pl.DataFrame, pl.DataFrame]) -> None:
    feats, _ = built
    assert not np.allclose(feats["d_out_pq_pool"].to_numpy(), feats["d_out_pool"].to_numpy())


def test_eligible_roster_window_and_trade() -> None:
    games = pl.DataFrame(
        {
            "game_id": ["a", "b", "c", "d"],
            "game_date": [dt.date(2023, 11, i) for i in (1, 2, 3, 4)],
            "season": [2023] * 4,
            "home_team": [1, 1, 2, 1],
            "away_team": [2, 2, 3, 2],
        }
    )
    stats = pl.DataFrame(
        {
            "player_id": [7, 7, 8, 7],
            "team_id": [1, 1, 2, 2],
            "game_id": ["a", "b", "a", "c"],
            "minutes": [20.0, 20.0, 10.0, 15.0],
        }
    )
    r = eligible_rosters(stats, games, window_games=10)
    assert r[("a", 1)] == set()  # nothing before the first date
    assert r[("b", 1)] == {7}
    assert r[("c", 2)] == {8}
    assert r[("d", 1)] == set()  # traded: last played for team 2
    assert r[("d", 2)] == {7, 8}
    r1 = eligible_rosters(stats, games, window_games=1)
    assert r1[("d", 1)] == set()


def test_floor_power_matches_closed_form() -> None:
    p = floor_power(-0.002, 1318, z_crit=1.959964)
    se = math.sqrt(2 * 0.002 / 1318)
    assert p["se"] == pytest.approx(se)
    assert p["half_width"] == pytest.approx(1.959964 * se)
    assert 0.15 < p["power"] < 0.25


def test_oracle_gain_zero_when_identical() -> None:
    pa = np.array([0.3, 0.5, 0.7])
    g = oracle_gain(pa, pa.copy())
    assert g["mean_gain_ll"] == pytest.approx(0.0)
    pb = np.array([0.4, 0.5, 0.6])
    g = oracle_gain(pa, pb)
    assert g["mean_gain_ll"] < 0 and g["mean_gain_brier"] < 0


def test_recover_block_coefs_exact() -> None:
    rng = np.random.default_rng(1)
    x = rng.normal(size=(200, 2))
    blocks = np.array(["2023-11"] * 100 + ["2023-12"] * 100)
    coef = {"2023-11": np.array([0.3, 0.1]), "2023-12": np.array([0.4, 0.2])}
    s = np.array([x[i] @ coef[b] for i, b in enumerate(blocks)])
    rec = recover_block_coefs(s, x, blocks)
    assert rec["max_abs_resid"] < 1e-10
    assert np.allclose(rec["coefs"]["2023-12"], [0.4, 0.2])
