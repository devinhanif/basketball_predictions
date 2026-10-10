"""played_only flag: flag-off identity, DNP exclusion, as-of leakage, SB, baselines,
fair-rematch helpers. Synthetic in-memory DBs only (no real data)."""

from __future__ import annotations

import duckdb
import numpy as np
import polars as pl
import pytest

from nba.db.connect import connect
from nba.features.sb_classification import build_sb_classification
from nba.props.distributions import NormalDist
from nba.props.forward import ForwardConfig, recency_weighted_dist
from nba.props.metrics import crps_array
from research.eval import fair_rematch as fr
from research.features import player_possession_features as ppf
from research.features import player_rebound_assist_features as pra
from research.features.played_baseline import (
    FORWARD_RECENCY_HALFLIFE_GAMES,
    build_played_baseline_features,
    recency_played_baseline,
)


def _game(con: duckdb.DuckDBPyConnection, gid: str, day: int, season: int = 2023) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, "
        "away_pts) VALUES (?, ?::DATE + ?::INTEGER, ?, 1, 2, 100, 90)",
        [gid, "2023-10-01", day, season],
    )


def _pgs(
    con: duckdb.DuckDBPyConnection,
    gid: str,
    pid: int,
    team: int,
    minutes: float | None,
    pts: int = 0,
    fgm: int = 0,
    fga: int = 0,
    fta: int = 0,
    ftm: int = 0,
    reb: int = 0,
    ast: int = 0,
) -> None:
    con.execute(
        "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, reb, ast, "
        "fg3m, stl, blk, tov, starter, fgm, fga, ftm, fta, oreb, dreb) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, true, ?, ?, ?, ?, 0, ?)",
        [gid, pid, team, minutes, pts, reb, ast, fgm, fga, ftm, fta, reb],
    )


def _poss(con: duckdb.DuckDBPyConnection, gid: str, idx: int, team: int, shooter: int) -> None:
    con.execute(
        "INSERT INTO possessions (game_id, poss_idx, off_team, def_team, outcome, shooter_id, "
        "shot_zone) VALUES (?, ?, ?, ?, 'FGA_miss', ?, 'rim')",
        [gid, idx, team, 3 - team, shooter],
    )


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = connect(":memory:")
    # Player 101 (team 1): plays g1, DNP g2, plays g3 (target), g4 target-after.
    # Player 103 (team 1) and 201 (team 2) play every game so team chances exist.
    for i, gid in enumerate(["g1", "g2", "g3", "g4"]):
        _game(c, gid, i)
        _pgs(c, gid, 103, 1, 30.0, pts=10, fgm=4, fga=10, reb=3, ast=2)
        _pgs(c, gid, 201, 2, 30.0, pts=12, fgm=5, fga=11, reb=4, ast=3)
        for j in range(6):
            _poss(c, gid, j, 1, 103)
    _pgs(c, "g1", 101, 1, 25.0, pts=8, fgm=3, fga=6, fta=2, ftm=1, reb=5, ast=1)
    _pgs(c, "g2", 101, 1, None)  # DNP: minutes NULL, stats 0
    _pgs(c, "g3", 101, 1, 20.0, pts=6, fgm=2, fga=5, reb=2, ast=1)
    _pgs(c, "g4", 101, 1, 22.0, pts=9, fgm=3, fga=7, reb=3, ast=2)
    for j in range(3):
        _poss(c, "g1", 10 + j, 1, 101)
    yield c
    c.close()


def _row(df: pl.DataFrame, gid: str, pid: int) -> dict[str, object]:
    return df.filter((pl.col("game_id") == gid) & (pl.col("player_id") == pid)).to_dicts()[0]


# ---------------------------------------------------------------- flag-off identity


def test_flag_off_sql_and_frames_identical(con: duckdb.DuckDBPyConnection) -> None:
    assert ppf._shot_rates_sql(False) == ppf._PLAYER_SHOT_RATES_SQL
    assert pra._reb_ast_sql(False) == pra._PLAYER_REB_AST_SQL
    assert "minutes" not in ppf._PLAYER_SHOT_RATES_SQL
    assert "minutes" not in pra._PLAYER_REB_AST_SQL
    assert ppf.build_player_shot_rates(con).equals(
        ppf.build_player_shot_rates(con, played_only=False)
    )
    assert pra.build_player_reb_ast_rates(con).equals(
        pra.build_player_reb_ast_rates(con, played_only=False)
    )
    a = build_sb_classification(con, "pts", min_games=1)
    assert a.equals(build_sb_classification(con, "pts", min_games=1, played_only=False))


# ---------------------------------------------------------------- DNP exclusion


def test_reb_ast_played_only_drops_dnp_chances(con: duckdb.DuckDBPyConnection) -> None:
    off = _row(pra.build_player_reb_ast_rates(con), "g3", 101)
    on = _row(pra.build_player_reb_ast_rates(con, played_only=True), "g3", 101)
    # Team 1 misses 6 FGA/game via 103 (+3 from 101 in g1): chances accumulate for
    # g1 and g2 when DNP is counted, only g1 when played-only.
    assert off["n_oreb_chances_prior"] > on["n_oreb_chances_prior"] > 0
    g1_only = _row(pra.build_player_reb_ast_rates(con, played_only=True), "g2", 101)
    assert on["n_oreb_chances_prior"] == g1_only["n_oreb_chances_prior"]


def test_shot_rates_played_only_drops_dnp_denominator(con: duckdb.DuckDBPyConnection) -> None:
    off = _row(ppf.build_player_shot_rates(con), "g3", 101)
    on = _row(ppf.build_player_shot_rates(con, played_only=True), "g3", 101)
    # Same observed shots; whole-game team FGA of the missed game inflates the
    # denominator only when DNP is counted, so shot share is higher played-only.
    assert on["shot_share_prior"] > off["shot_share_prior"]
    # time-decay path also accepts the flag
    td = ppf.build_player_shot_rates(con, use_time_decay=True, played_only=True)
    assert td.height == 12


def test_target_dnp_row_still_emitted(con: duckdb.DuckDBPyConnection) -> None:
    assert _row(ppf.build_player_shot_rates(con, played_only=True), "g2", 101)
    assert _row(pra.build_player_reb_ast_rates(con, played_only=True), "g2", 101)


# ---------------------------------------------------------------- leakage


def test_played_only_features_ignore_future(con: duckdb.DuckDBPyConnection) -> None:
    before_s = ppf.build_player_shot_rates(con, played_only=True)
    before_r = pra.build_player_reb_ast_rates(con, played_only=True)
    before_b = build_played_baseline_features(con, "pts")
    _game(con, "g9", 50)
    _pgs(con, "g9", 101, 1, 35.0, pts=60, fgm=25, fga=30, reb=30, ast=20)
    _pgs(con, "g9", 201, 2, 35.0, pts=40, fgm=15, fga=20)
    after_s = ppf.build_player_shot_rates(con, played_only=True).filter(pl.col("game_id") != "g9")
    after_r = pra.build_player_reb_ast_rates(con, played_only=True).filter(
        pl.col("game_id") != "g9"
    )
    after_b = build_played_baseline_features(con, "pts").filter(pl.col("game_id") != "g9")
    assert after_s.equals(before_s)
    assert after_r.equals(before_r)
    assert after_b.equals(before_b)


# ---------------------------------------------------------------- SB


def test_sb_played_only_ignores_dnp_zeros() -> None:
    c = connect(":memory:")
    for i in range(30):
        _game(c, f"h{i:02d}", i)
        # plays on even days (pts vary), DNP on odd days
        _pgs(
            c,
            f"h{i:02d}",
            7,
            1,
            25.0 if i % 2 == 0 else None,
            pts=10 + (i % 3) if i % 2 == 0 else 0,
        )
    old = build_sb_classification(c, "pts", min_games=10)
    new = build_sb_classification(c, "pts", min_games=10, played_only=True)
    last_old = old.filter(pl.col("game_id") == "h29").to_dicts()[0]
    last_new = new.filter(pl.col("game_id") == "h29").to_dicts()[0]
    assert last_old["adi"] >= 1.32 and last_old["sb_class"] in {"intermittent", "lumpy"}
    assert last_new["adi"] == 1.0 and last_new["sb_class"] == "smooth"
    assert last_new["n_prior"] == 15  # played games only (even days before i=29)
    c.close()


# ---------------------------------------------------------------- baselines


def test_played_baseline_excludes_dnp_and_matches_forward(con: duckdb.DuckDBPyConnection) -> None:
    f = build_played_baseline_features(con, "pts")
    r = _row(f, "g3", 101)
    assert r["games_played_prior"] == 1 and r["season_avg_prior"] == 8.0  # DNP g2 ignored
    keys = pl.DataFrame({"game_id": ["g3", "g4"], "player_id": [101, 101]})
    d = recency_played_baseline(con, "pts", keys)
    assert d[0].mean() == pytest.approx(8.0)
    exp = recency_weighted_dist(np.array([8.0, 6.0]), "pts", FORWARD_RECENCY_HALFLIFE_GAMES)
    assert d[1].mean() == pytest.approx(exp.mean())
    assert d[1].std == pytest.approx(exp.std)
    assert ForwardConfig().halflife_games == FORWARD_RECENCY_HALFLIFE_GAMES


def test_ensure_played_view_on_read_only(tmp_path) -> None:  # type: ignore[no-untyped-def]
    p = tmp_path / "t.duckdb"
    w = connect(p)
    _game(w, "x", 0)
    _pgs(w, "x", 1, 1, None)
    _pgs(w, "x", 2, 1, 10.0)
    w.close()
    ro = duckdb.connect(str(p), read_only=True)
    ppf.ensure_played_view(ro)
    ppf.ensure_played_view(ro)  # idempotent
    assert ro.execute("select count(*) from player_game_stats_played").fetchone() == (1,)
    ro.close()


# ---------------------------------------------------------------- fair rematch


def test_mixture_crps_reduces_to_base_when_always_plays() -> None:
    dists = [NormalDist(8.0, 4.0), NormalDist(3.0, 2.0)]
    y = np.array([9.0, 0.0])
    got = fr.mixture_crps(dists, np.array([1.0, 1.0]), y)
    assert np.allclose(got, crps_array(list(dists), y), atol=1e-6)
    # a certain DNP with y=0 scores ~0
    assert fr.mixture_crps(dists, np.array([0.0, 0.0]), np.zeros(2)).max() < 1e-9


def test_sampler_refuses_2025_and_is_deterministic(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError):
        fr.sample_game_ids(con, 2025, 10, {"g1"})
    with pytest.raises(ValueError):
        fr.run_fair_rematch(con, fr.FairRematchConfig(season=2025))
    a = fr.sample_game_ids(con, 2023, 2, {"g1", "g2", "g3", "g4"})
    assert a == fr.sample_game_ids(con, 2023, 2, {"g1", "g2", "g3", "g4"})
    assert a == ["g1", "g4"]


def test_decision_rule() -> None:
    base = {"q": 0.01, "hi": -0.01, "lo": -0.03, "delta": -0.02, "mde": 0.005}
    assert fr.decide_primary(base) == "B beats S"
    assert fr.decide_primary({**base, "delta": -0.004, "hi": -0.001}).startswith("no verdict")
    assert fr.decide_primary({**base, "q": 0.2}).startswith("no verdict")
    assert fr.decide_primary({**base, "hi": 0.0}).startswith("no verdict")
    s = {"q": 0.01, "lo": 0.01, "hi": 0.03, "delta": 0.02, "mde": 0.005}
    assert fr.decide_primary(s) == "S beats B"
    assert "underpowered" in fr.decide_primary({**s, "q": 0.5, "mde": 0.05})


def test_fair_rematch_smoke_on_fixture() -> None:
    from tests.fixtures.loader import build_fixture_db

    c = build_fixture_db(":memory:")
    try:
        res = fr.run_fair_rematch(
            c, fr.FairRematchConfig(season=2023, sample_games=3, n_sims=40, n_boot=50)
        )
    finally:
        c.close()
    prim = [x for x in res.cells if x["family"] == "primary"]
    assert {x["stat"] for x in prim} == {"pts", "reb", "ast"}
    for x in prim:
        assert x["q"] is not None and np.isfinite(x["delta"])
    assert "| primary |" in res.markdown()
