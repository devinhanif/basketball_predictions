"""Official-roster forward path: movers, no-history players, defaults unchanged."""

from __future__ import annotations

import duckdb
import numpy as np
import polars as pl
import pytest

from nba.props.forward import ForwardConfig, predict_slate
from nba.props.role_change import k_multiplier
from nba.props.roster_cold import (
    cold_stat_dist,
    minute_bin_priors,
    new_team_k_multipliers,
    rookie_minutes_mu,
)
from tests.daily.conftest import RUN_DATE, T1, T2, T3, T4, add_history
from tests.fixtures.loader import build_fixture_db

SLATE = pl.DataFrame(
    {"game_id": ["0022600001", "0022600002"], "home_team": [T1, T3], "away_team": [T2, T4]}
)
CFG = ForwardConfig(sim_stats=())


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = build_fixture_db(":memory:")
    add_history(c)
    return c


def _official() -> pl.DataFrame:
    # 1100 (T2 history) moved to T1; 9001 has no NBA history; the rest stay put.
    rows = [(T1, p) for p in (1000, 1001, 1002, 1003, 1004, 1100, 9001)]
    rows += [(T2, p) for p in (1101, 1102, 1103, 1104)]
    rows += [(T3, p) for p in (3000, 3001, 3002, 3003, 3004)]
    rows += [(T4, p) for p in (3100, 3101, 3102, 3103, 3104)]
    return pl.DataFrame(rows, schema={"team_id": pl.Int64, "player_id": pl.Int64}, orient="row")


def _one(df: pl.DataFrame, pid: int, stat: str = "pts") -> dict[str, object]:
    r = df.filter((pl.col("player_id") == pid) & (pl.col("stat") == stat))
    assert r.height == 1, (pid, r.height)
    return r.row(0, named=True)


def test_default_is_unchanged_without_an_official_roster(con: duckdb.DuckDBPyConnection) -> None:
    base = predict_slate(con, RUN_DATE, SLATE, None, config=CFG)
    empty = predict_slate(con, RUN_DATE, SLATE, None, config=CFG, official_roster=pl.DataFrame())
    assert base.equals(empty)
    assert 9001 not in base["player_id"].to_list()  # not in anyone's last-10 games


def test_official_adds_debutant_and_moves_mover_once(con: duckdb.DuckDBPyConnection) -> None:
    out = predict_slate(con, RUN_DATE, SLATE, None, config=CFG, official_roster=_official())
    rookie = _one(out, 9001)
    assert rookie["team_id"] == T1 and rookie["n_games_prior"] == 0
    assert (
        float(rookie["mean"]) > 0.5
    )  # league level at his minutes, not the empty-history 0.0  # type: ignore[arg-type]
    mover = out.filter(pl.col("player_id") == 1100)
    assert set(mover["team_id"].to_list()) == {T1}  # old team T2 does not also project him
    assert out.group_by(["player_id", "stat"]).len()["len"].max() == 1


def test_mover_minutes_shrink_toward_default_not_old_team_blindly(
    con: duckdb.DuckDBPyConnection,
) -> None:
    on = predict_slate(
        con, RUN_DATE, SLATE, None,
        config=ForwardConfig(sim_stats=(), new_team_shrinkage=True),
        official_roster=_official(),
    )  # fmt: skip
    off = predict_slate(
        con, RUN_DATE, SLATE, None,
        config=ForwardConfig(sim_stats=(), new_team_shrinkage=False),
        official_roster=_official(),
    )  # fmt: skip
    a, b = float(_one(on, 1100)["proj_minutes"]), float(_one(off, 1100)["proj_minutes"])  # type: ignore[arg-type]
    assert 24.0 < a < b  # history is 30 min; shrunk toward the 24-minute default, not past it
    # default (switch off) keeps the player's own history: same as explicitly off
    dflt = predict_slate(con, RUN_DATE, SLATE, None, config=CFG, official_roster=_official())
    assert _one(dflt, 1100)["proj_minutes"] == _one(off, 1100)["proj_minutes"]
    # a player who did not move is untouched by the switch
    assert _one(on, 1000)["proj_minutes"] == _one(off, 1000)["proj_minutes"]


def test_out_players_are_removed_from_the_official_roster(con: duckdb.DuckDBPyConnection) -> None:
    out = predict_slate(con, RUN_DATE, SLATE, {9001, 1100}, config=CFG, official_roster=_official())
    assert not {9001, 1100} & set(out["player_id"].to_list())


def test_k_multiplier_boost_and_decay(con: duckdb.DuckDBPyConnection) -> None:
    rows = pl.DataFrame(
        {
            "player_id": [1000, 1100, 9001],
            "team_id": [T1, T1, T1],
            "games_played_prior": [12, 12, 0],
        }
    )
    k = new_team_k_multipliers(con, rows, 2026)
    assert k[0] == 1.0 and k[2] == 1.0  # stays put / no history
    assert k[1] == k_multiplier(0.0)  # moved teams: full boost
    # after his first game for the new team the boost decays, it does not vanish
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) "
        "VALUES ('0022600000', '2026-10-27', 2026, ?, ?, 100, 99)",
        [T1, T3],
    )
    con.execute(
        "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes) "
        "VALUES ('0022600000', 1100, ?, 30.0)",
        [T1],
    )
    k2 = new_team_k_multipliers(con, rows, 2026)
    assert 1.0 < k2[1] < k[1]
    assert k2[1] == pytest.approx(k_multiplier(1.0))


def test_cold_stat_dist_uses_history_bins_and_nearest_bin(con: duckdb.DuckDBPyConnection) -> None:
    bins = minute_bin_priors(con, 2026)
    d30 = cold_stat_dist(bins, "pts", 30.0)
    d12 = cold_stat_dist(bins, "pts", 12.0)  # no bin that low in the fixture: nearest populated
    assert d30 is not None and d12 is not None
    assert d30.mean() == d12.mean() > 0
    assert cold_stat_dist({}, "pts", 12.0) is None


def test_rookie_prior_skipped_without_enough_training_rookies(
    con: duckdb.DuckDBPyConnection,
) -> None:
    static = con.execute("SELECT * FROM players_static").pl()
    rows = pl.DataFrame({"player_id": [9001], "n_played_prior": [0.0]})
    mu, is_rookie = rookie_minutes_mu(con, rows, 2026, static)
    assert not is_rookie.any() and np.isnan(mu).all()
