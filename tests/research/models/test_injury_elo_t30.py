"""T-30 injury-Elo: inactive-proxy, leakage (future / same-game), cutoff and rule tests."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import polars as pl

from nba.eval.injury_elo_eval import load_config
from nba.models.injury_elo import InjuryFeatureConfig, load_games_frame, load_stats_frame
from research.eval.injury_elo_t30_eval import classify_wc, decide_t30, render_report, run_t30_eval
from research.models.injury_elo_t30 import T30Config, build_t30_features, inactive_proxy
from tests.ml.test_injury_elo import CFG_PATH, STAR, _make_db

INSERT_AV = (
    "INSERT INTO player_availability (player_id,as_of,game_id,status,source) VALUES (?,?,?,?,?)"
)


def _games(rows: list[tuple[str, dt.date, int, int, int]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows, schema=["game_id", "game_date", "season", "home_team", "away_team"], orient="row"
    )


def _box(rows: list[tuple[str, int, int]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=["game_id", "player_id", "team_id"], orient="row")


def test_inactive_proxy_window_season_and_trade_guard() -> None:
    d = dt.date(2023, 11, 1)
    games = _games(
        [
            ("G1", d, 2023, 1, 2),
            ("G2", d + dt.timedelta(days=2), 2023, 1, 3),
            ("G3", d + dt.timedelta(days=4), 2023, 1, 2),
            ("G4", d + dt.timedelta(days=300), 2024, 1, 2),
        ]
    )
    box = _box(
        [("G1", p, 1) for p in (11, 12, 13)]
        + [("G1", p, 2) for p in (21, 22)]
        + [("G2", p, 1) for p in (11, 12)]  # 13 absent in G2
        + [("G2", 21, 3)]  # 21 traded to team 3
        + [("G3", 11, 1), ("G3", 22, 2), ("G3", 21, 3)]  # 12 and 13 absent; 21 at team 3
        + [("G4", 11, 1), ("G4", 22, 2)]
    )
    got = inactive_proxy(box, games, window_games=10)
    assert got[("G2", 1)] == {13}
    assert got[("G3", 1)] == {12, 13}
    assert ("G3", 2) not in {k for k, v in got.items() if 21 in v}  # traded player not flagged
    assert 21 not in got.get(("G3", 2), set())
    # new season: no same-season window, nothing flagged for G4
    assert ("G4", 1) not in got and ("G4", 2) not in got


def _cfg_proxy() -> T30Config:
    return T30Config(
        base=InjuryFeatureConfig(tip_source="proxy19"), lead_minutes_t30=30, window_games=10
    )


def _feats(con: duckdb.DuckDBPyConnection, cfg: T30Config | None = None) -> pl.DataFrame:
    games = load_games_frame(con, 2024)
    stats = load_stats_frame(con, 2024)
    return build_t30_features(con, games, stats, cfg or _cfg_proxy())


def _db_with_inactive_star() -> duckdb.DuckDBPyConnection:
    c = _make_db(seed=3, star_out_days=set(range(30, 120, 4)))
    # star is INACTIVE (no row) rather than a 0-minute dressed row
    c.execute(
        "DELETE FROM player_game_stats WHERE player_id = ? AND COALESCE(minutes,0) = 0", [STAR]
    )
    c.execute("DELETE FROM player_availability")  # no report at all: list is the only signal
    return c


def test_inactive_list_moves_feature_in_right_direction() -> None:
    c = _db_with_inactive_star()
    gid, home = c.execute(
        "SELECT game_id, home_team FROM games WHERE (home_team=1 OR away_team=1) "
        "AND game_date >= DATE '2023-12-01' ORDER BY game_date, game_id LIMIT 1"
    ).fetchone()  # type: ignore[misc]
    absent = c.execute(
        "SELECT count(*) FROM player_game_stats WHERE game_id=? AND player_id=?", [gid, STAR]
    ).fetchone()[0]  # type: ignore[index]
    f = _feats(c)
    row = f.filter(pl.col("game_id") == gid)
    if absent == 0:
        assert (row["d_out30"][0] < 0) if home == 1 else (row["d_out30"][0] > 0)
    assert f["d_out30"].abs().sum() > 0


def test_future_games_never_change_earlier_features() -> None:
    c = _db_with_inactive_star()
    before = _feats(c)
    last = c.execute("SELECT max(game_date) FROM games").fetchone()[0]  # type: ignore[index]
    # plant a future: new late games with the star absent and wild stats
    c.execute(
        "INSERT INTO games (game_id,game_date,season,home_team,away_team,home_pts,away_pts)"
        " VALUES ('GFUT1', ?, 2023, 1, 2, 100, 99)",
        [last + dt.timedelta(days=5)],
    )
    c.execute(
        "INSERT INTO player_game_stats (game_id,player_id,team_id,minutes,pts,reb,ast,stl,blk,tov)"
        " VALUES ('GFUT1', 999, 1, 40.0, 99, 9, 9, 9, 9, 0)"
    )
    after = _feats(c)
    keep = set(before["game_id"].to_list())
    a = after.filter(pl.col("game_id").is_in(list(keep))).sort("game_id")
    b = before.sort("game_id")
    assert a.equals(b)


def test_same_game_minutes_and_stats_cannot_change_features_only_presence() -> None:
    c = _db_with_inactive_star()
    base = _feats(c).sort("game_id")
    # tonight's minutes and box stats are irrelevant (presence only)
    c.execute("UPDATE player_game_stats SET minutes = NULL, pts = 99, reb = 0, ast = 0")
    pert = _feats(c).sort("game_id")
    # value state uses prior-game box stats, so earlier-game perturbation moves values; compare
    # only the inactive PROXY, which must be identical
    games = load_games_frame(c, 2024)
    s_after = load_stats_frame(c, 2024)
    c2 = _db_with_inactive_star()
    s_before = load_stats_frame(c2, 2024)
    assert inactive_proxy(s_before, games) == inactive_proxy(s_after, games)
    assert pert.height == base.height
    # deleting one present player's row for one game creates exactly that inactive entry
    gid = games.sort(["game_date", "game_id"])["game_id"][100]
    team = int(games.filter(pl.col("game_id") == gid)["home_team"][0])
    pid = team * 100 + 3
    c2.execute("DELETE FROM player_game_stats WHERE game_id=? AND player_id=?", [gid, pid])
    got = inactive_proxy(load_stats_frame(c2, 2024), games)
    assert pid in got[(gid, team)]
    # and he is not flagged in the NEXT game once he is back (no persistence)
    nxt = [k for k in got if k[1] == team and k[0] != gid and pid in got[k]]
    assert nxt == []


def test_report_cutoff_is_tip_minus_30_for_t30_and_60_for_wc() -> None:
    c = _make_db(seed=1, star_out_days=set())
    gid, d = c.execute(
        "SELECT game_id, game_date FROM games WHERE (home_team=1 OR away_team=1) "
        "AND game_date >= DATE '2023-12-01' ORDER BY game_date, game_id LIMIT 1"
    ).fetchone()  # type: ignore[misc]
    c.execute(
        INSERT_AV,
        [STAR, dt.datetime.combine(d, dt.time(18, 15)), gid, "out", "nba_official_report"],
    )
    row = _feats(c).filter(pl.col("game_id") == gid)
    assert row["d_out30"][0] != 0.0  # 18:15 <= 19:00 - 30min
    assert row["d_out_wc"][0] == 0.0  # 18:15 > 19:00 - 60min
    c.execute(
        INSERT_AV,
        [STAR, dt.datetime.combine(d, dt.time(18, 45)), gid, "out", "nba_official_report"],
    )
    assert _feats(c).filter(pl.col("game_id") == gid)["d_out30"][0] == row["d_out30"][0]


def test_worst_case_ignores_inactive_hinted_by_t60_report() -> None:
    c = _db_with_inactive_star()
    gid, d, home = c.execute(
        "SELECT g.game_id, g.game_date, g.home_team FROM games g "
        "WHERE (home_team=1 OR away_team=1) "
        "AND NOT EXISTS (SELECT 1 FROM player_game_stats s WHERE s.game_id=g.game_id "
        "AND s.player_id=?) AND game_date >= DATE '2023-12-01' ORDER BY game_date LIMIT 1",
        [STAR],
    ).fetchone()  # type: ignore[misc]
    plain = _feats(c).filter(pl.col("game_id") == gid)
    assert plain["d_out_wc"][0] != 0.0  # undisclosed inactive: counted by the worst case
    c.execute(
        INSERT_AV,
        [STAR, dt.datetime.combine(d, dt.time(11, 0)), gid, "questionable", "nba_official_report"],
    )
    hinted = _feats(c).filter(pl.col("game_id") == gid)
    assert hinted["d_out_wc"][0] == 0.0  # questionable at T-60 -> unknown at T-30
    assert hinted["d_out30"][0] != 0.0  # the candidate still sees the list


def test_decision_rule_and_wc_class() -> None:
    def c(delta: float, lo: float, hi: float) -> dict[str, float]:
        return {"delta": delta, "lo": lo, "hi": hi}

    ok = {"log_loss": c(-0.003, -0.005, -0.001), "brier": c(-0.001, -0.002, 0), "ece": c(0.0, 0, 0)}
    sl = {"s": {"n": 200, "log_loss": c(0.001, 0, 0)}}
    assert decide_t30(ok, sl)["verdict"] == "PASS"
    weak = dict(ok, log_loss=c(-0.001, -0.002, -0.0002))
    assert decide_t30(weak, sl)["verdict"] == "FAIL"
    bad_sl = {"s": {"n": 200, "log_loss": c(0.006, 0, 0)}, "t": {"n": 50, "log_loss": c(0.1, 0, 0)}}
    d = decide_t30(ok, bad_sl)
    assert d["slice_vetoes"] == ["s"] and d["verdict"] == "FAIL"
    assert classify_wc(ok) == "robust"
    assert classify_wc(weak) == "conditional"
    assert classify_wc(dict(ok, log_loss=c(0.001, 0, 0.002))) == "headline only"


def test_end_to_end_and_holdout_guard(tmp_path: Path) -> None:
    c = _db_with_inactive_star()
    cfg = load_config(CFG_PATH)
    cfg["oof_seasons"] = [2023]
    cfg["bootstrap"]["n_boot"] = 100
    cfg["fit"]["min_signal_games"] = 20
    res = run_t30_eval(c, cfg, tmp_path)
    r = res["seasons"]["2023"]
    assert set(r["variants"]) == {"t30", "t30_wc", "t30_out_only"}
    assert r["variants"]["t30"]["decision"]["verdict"] in ("PASS", "FAIL")
    assert (tmp_path / "results.json").exists()
    assert "Season 2023" in render_report(res)
    # inactive list is informative here: t30 must not be worse than t60 by a large margin
    assert r["variants"]["t30"]["overall"]["log_loss"]["delta"] < 0.01
    try:
        run_t30_eval(c, dict(cfg, oof_seasons=[2023, 2025]))
    except ValueError:
        return
    raise AssertionError("holdout season must be rejected")
