"""Injury-adjusted MOV-Elo: leakage, Elo-replay equivalence, fit and e2e tests.

Uses a synthetic in-memory league (the committed fixture has 3 games, too few
for walk-forward value/fit tests). The planted star (team 1, player 101) has a
large real effect so the fit has signal.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import numpy as np
import polars as pl
import pytest

from nba.db.connect import connect
from nba.eval.injury_elo_eval import decide, load_config, run_injury_elo_eval, walk_forward_probs
from nba.eval.walkforward import make_walk_forward_folds
from nba.models.injury_elo import (
    InjuryEloModel,
    InjuryFeatureConfig,
    ValueConfig,
    asof_values,
    build_injury_features,
    build_value_state,
    fit_injury_coefs,
    league_rate_by_date,
    load_games_frame,
    load_stats_frame,
    sequential_elo_logits,
)
from nba.models.rung0_baselines import MovEloBaseline

ELO = {
    "k_factor": 9.49,
    "home_advantage_elo": 40.4,
    "season_carryover": 0.61,
    "mov_c": 3.69,
    "mov_div": 0.00067,
}
CFG_PATH = Path(__file__).resolve().parents[2] / "configs" / "injury_elo.yaml"
STAR = 101


def _make_db(
    seed: int = 0, days: int = 120, star_out_days: set[int] | None = None
) -> duckdb.DuckDBPyConnection:
    rng = np.random.default_rng(seed)
    con = connect(":memory:")
    teams = [1, 2, 3, 4, 5, 6]
    start = dt.date(2023, 10, 20)
    star_out_days = star_out_days if star_out_days is not None else set(range(30, days, 4))
    games, stats, avail = [], [], []
    gid = 0
    for day in range(days):
        d = start + dt.timedelta(days=day)
        order = [int(t) for t in rng.permutation(teams)]
        for h, a in ((order[0], order[1]), (order[2], order[3]), (order[4], order[5])):
            gid += 1
            g = f"G{gid:05d}"
            star_out = (h == 1 or a == 1) and day in star_out_days
            strength = {t: (4.0 if t == 1 else 0.0) for t in teams}
            if star_out:
                strength[1] = 0.0
            margin = strength[h] - strength[a] + 2.0 + rng.normal(0, 11)
            hp = int(100 + margin / 2 + rng.normal(0, 3))
            ap = int(hp - round(margin)) if round(margin) != 0 else hp - 1
            games.append((g, d, 2023, h, a, hp, ap))
            for t in (h, a):
                for k in range(1, 9):
                    pid = t * 100 + k
                    if pid == STAR and star_out:
                        stats.append((g, pid, t, 0.0, 0, 0, 0, 0, 0, 0))
                        continue
                    base = 40.0 if pid == STAR else 6.0
                    stats.append(
                        (
                            g,
                            pid,
                            t,
                            32.0 if k <= 5 else 12.0,
                            int(max(0, rng.normal(base, 3))),
                            int(rng.integers(1, 8)),
                            int(rng.integers(0, 6)),
                            1,
                            0,
                            1,
                        )
                    )
            if star_out:
                avail.append(
                    (STAR, dt.datetime.combine(d, dt.time(17, 45)), g, "out", "nba_official_report")
                )
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
    if avail:
        con.executemany(
            "INSERT INTO player_availability (player_id,as_of,game_id,status,source)"
            " VALUES (?,?,?,?,?)",  # noqa: E501
            avail,
        )
    return con


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    return _make_db()


def _first_star_out_game(con: duckdb.DuckDBPyConnection) -> tuple[str, dt.date]:
    gid, d = con.execute(
        "SELECT game_id, game_date FROM games WHERE (home_team=1 OR away_team=1) "
        "AND game_date >= DATE '2023-12-01' ORDER BY game_date, game_id LIMIT 1"
    ).fetchone()  # type: ignore[misc]
    return str(gid), d


def test_elo_replay_matches_fold_harness(con: duckdb.DuckDBPyConnection) -> None:
    games = load_games_frame(con, 2024)
    logits = sequential_elo_logits(games, ELO)
    folds = make_walk_forward_folds(games, min_train_games=1)
    ids = games["game_id"].to_list()
    pos = {g: i for i, g in enumerate(ids)}
    for fold in folds[::7]:
        m = MovEloBaseline(seed=0, **ELO)
        m.fit(fold.train_df, fold.train_df["y"].to_numpy())
        p = m.predict(fold.test_df)
        for g, pp in zip(fold.test_df["game_id"].to_list(), p, strict=True):
            assert 1 / (1 + np.exp(-logits[pos[g]])) == pytest.approx(pp, abs=1e-9)


def test_value_shrinks_low_sample_and_unseen_is_zero(con: duckdb.DuckDBPyConnection) -> None:
    stats = load_stats_frame(con, 2024)
    vcfg = ValueConfig()
    state = build_value_state(stats, vcfg)
    league = league_rate_by_date(stats)
    pairs = pl.DataFrame(
        {"game_id": ["x", "y"], "player_id": [STAR, 999999], "game_date": [dt.date(2024, 2, 1)] * 2}
    )
    v = asof_values(pairs, state, league, vcfg)
    star = v.filter(pl.col("player_id") == STAR)["value"][0]
    assert star > 5.0
    assert v.filter(pl.col("player_id") == 999999)["value"][0] == 0.0
    early = pairs.with_columns(pl.lit(dt.date(2023, 10, 22)).alias("game_date"))
    assert (
        asof_values(early, state, league, vcfg).filter(pl.col("player_id") == STAR)["value"][0]
        < star
    )


def test_future_games_do_not_change_value(con: duckdb.DuckDBPyConnection) -> None:
    stats = load_stats_frame(con, 2024)
    cut = dt.date(2024, 1, 15)
    vcfg = ValueConfig()
    pairs = pl.DataFrame({"game_id": ["x"], "player_id": [STAR], "game_date": [cut]})
    base = asof_values(pairs, build_value_state(stats, vcfg), league_rate_by_date(stats), vcfg)
    wrecked = stats.with_columns(
        pl.when(pl.col("game_date") >= cut)
        .then(pl.col("comp") * 50 + 999)
        .otherwise(pl.col("comp"))
        .alias("comp")
    )
    other = asof_values(pairs, build_value_state(wrecked, vcfg), league_rate_by_date(wrecked), vcfg)
    assert base["value"][0] == other["value"][0]


def _features(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    games = load_games_frame(con, 2024)
    stats = load_stats_frame(con, 2024)
    return build_injury_features(con, games, stats, InjuryFeatureConfig())


def test_report_after_cutoff_is_ignored() -> None:
    c = _make_db(seed=1, star_out_days=set())  # no reports at all
    gid, d = _first_star_out_game(c)
    base = _features(c).filter(pl.col("game_id") == gid)
    assert base["d_out"][0] == 0.0 and not base["has_report"][0]
    # 18:01 is after the 18:00 cutoff (19:00 tip proxy - 60 min): ignored
    c.execute(
        "INSERT INTO player_availability (player_id,as_of,game_id,status,source) VALUES (?,?,?,?,?)",  # noqa: E501
        [STAR, dt.datetime.combine(d, dt.time(18, 1)), gid, "out", "nba_official_report"],
    )
    late = _features(c).filter(pl.col("game_id") == gid)
    assert late["d_out"][0] == 0.0 and not late["has_report"][0]
    # 17:45 is usable and moves the feature in the right direction
    c.execute(
        "INSERT INTO player_availability (player_id,as_of,game_id,status,source) VALUES (?,?,?,?,?)",  # noqa: E501
        [STAR, dt.datetime.combine(d, dt.time(17, 45)), gid, "out", "nba_official_report"],
    )
    ok = _features(c).filter(pl.col("game_id") == gid)
    home = c.execute("SELECT home_team FROM games WHERE game_id=?", [gid]).fetchone()[0]  # type: ignore[index]
    assert ok["has_report"][0]
    assert (ok["d_out"][0] < 0) if home == 1 else (ok["d_out"][0] > 0)


def test_later_report_supersedes_earlier_out() -> None:
    c = _make_db(seed=1, star_out_days=set())
    gid, d = _first_star_out_game(c)
    for t, st in ((dt.time(10, 0), "out"), (dt.time(17, 45), "probable")):
        c.execute(
            "INSERT INTO player_availability (player_id,as_of,game_id,status,source) VALUES (?,?,?,?,?)",  # noqa: E501
            [STAR, dt.datetime.combine(d, t), gid, st, "nba_official_report"],
        )
    f = _features(c).filter(pl.col("game_id") == gid)
    assert f["d_out"][0] == 0.0


def test_fit_recovers_sign_and_zero_feature_stays_zero() -> None:
    rng = np.random.default_rng(0)
    n = 4000
    x = np.column_stack([rng.normal(0, 1, n) * (rng.random(n) < 0.3), np.zeros(n)])
    off = rng.normal(0, 0.4, n)
    y = (rng.random(n) < 1 / (1 + np.exp(-(off + 0.5 * x[:, 0])))).astype(float)
    coef = fit_injury_coefs(x, off, y, 5.0)
    assert coef[0] == pytest.approx(0.5, abs=0.15)
    assert coef[1] == 0.0


def test_walk_forward_ignores_future_outcomes(con: duckdb.DuckDBPyConnection) -> None:
    games = load_games_frame(con, 2024)
    frame = games.join(_features(con), on="game_id").sort(["game_date", "game_id"])
    off = sequential_elo_logits(frame, ELO)
    p1, _ = walk_forward_probs(frame, off, ("d_out", "d_doubt"), 5.0, 20)
    cut = dt.date(2024, 1, 20)
    flipped = frame.with_columns(
        pl.when(pl.col("game_date") >= cut).then(1 - pl.col("y")).otherwise(pl.col("y")).alias("y")
    )
    p2, _ = walk_forward_probs(flipped, off, ("d_out", "d_doubt"), 5.0, 20)
    before = (frame["game_date"] < dt.date(2024, 1, 1)).to_numpy()
    assert np.allclose(p1[before], p2[before])


def test_model_contract_and_direction(con: duckdb.DuckDBPyConnection) -> None:
    games = load_games_frame(con, 2024)
    frame = games.join(_features(con), on="game_id").sort(["game_date", "game_id"])
    m = InjuryEloModel(ELO)
    m.fit(frame, frame["y"].to_numpy())
    cfg = m.get_config()
    assert cfg["model_name"] == "rung0_injury_elo" and m.get_metrics() == {}
    # star OUT for the home side must lower home win prob vs not out
    row = frame.head(1)
    p0 = m.predict(row.with_columns(pl.lit(0.0).alias("d_out")))[0]
    p1 = m.predict(row.with_columns(pl.lit(-3.0).alias("d_out")))[0]
    assert p1 < p0


def test_end_to_end_and_holdout_guard(tmp_path: Path) -> None:
    c = _make_db(seed=2, days=150)
    cfg = load_config(CFG_PATH)
    cfg["oof_seasons"] = [2023]
    cfg["bootstrap"]["n_boot"] = 100
    cfg["fit"]["min_signal_games"] = 20
    cfg["report"]["backfill_table"] = "player_availability"
    res = run_injury_elo_eval(c, cfg, tmp_path)
    assert res["n_scored"] == 150 * 3 - 3
    assert res["report_coverage"] > 0
    assert "decision" in res["variants"]["injury_elo"]
    assert "decision" not in res["variants"]["ORACLE_not_a_candidate"]
    oof = pl.read_parquet(tmp_path / "oof_predictions.parquet")
    assert oof.columns == ["model", "game_id", "game_date", "season", "p", "made_with_data_through"]
    assert set(oof["model"]) == {"rung0_mov_elo", "rung0_injury_elo"}
    assert oof["p"].is_between(0, 1).all()
    gd = oof.with_columns(pl.col("game_date").cast(pl.Datetime("us")))
    assert gd.select(
        (pl.col("made_with_data_through") < pl.col("game_date") + pl.duration(hours=19)).all()
    ).item()
    bad = dict(cfg, oof_seasons=[2023, 2025])
    with pytest.raises(ValueError):
        run_injury_elo_eval(c, bad)


def test_decision_rule() -> None:
    ov = {"log_loss": {"hi": -0.001}}
    ok = {"a": {"n": 500, "log_loss": {"delta": 0.004}}}
    assert decide(ov, ok)["verdict"] == "KEEP"
    bad = {"a": {"n": 500, "log_loss": {"delta": 0.006}}}
    assert decide(ov, bad)["verdict"] == "REJECT"
    small = {"a": {"n": 50, "log_loss": {"delta": 0.02}}}
    assert decide(ov, small)["verdict"] == "KEEP"
    assert decide({"log_loss": {"hi": 0.001}}, ok)["verdict"] == "REJECT"


def test_holdout_requires_flag_and_default_rejects_it(tmp_path: Path) -> None:
    c = _make_db(seed=3, days=100)
    cfg = load_config(CFG_PATH)
    cfg["bootstrap"]["n_boot"] = 50
    cfg["fit"]["min_signal_games"] = 10
    cfg["report"]["backfill_table"] = "player_availability"
    # default path: holdout season in oof_seasons is rejected
    with pytest.raises(ValueError):
        run_injury_elo_eval(c, dict(cfg, oof_seasons=[2023, int(cfg["holdout_season"])]))
    # confirmatory without the preregistration flag is rejected
    cfg2 = dict(cfg, holdout_season=2023, oof_seasons=[2022])
    with pytest.raises(ValueError):
        run_injury_elo_eval(c, cfg2, confirmatory_holdout=2023)
    # wrong season is rejected even with the flag
    with pytest.raises(ValueError):
        run_injury_elo_eval(c, cfg2, confirmatory_holdout=2024, preregistered=True)
    res = run_injury_elo_eval(c, cfg2, confirmatory_holdout=2023, preregistered=True)
    assert res["seasons"] == [2023] and res["n_scored"] == 100 * 3 - 3
    assert "confirmed" in res["variants"]["injury_elo"]
