"""Ledger audit pass 2 N3 (last_fair_price DNP policy), N4 (slate roster source), N6 (CI gate)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import duckdb
import numpy as np
import polars as pl
import pytest

from nba.parlay.config import load_config
from nba.parlay.ev import NO_POSITIVE_EV, calibration_sd, evaluate
from nba.parlay.game_model import GameModelParams
from nba.parlay.joint import DNP_POLICIES, effective_prob
from nba.parlay.legs import Leg
from nba.parlay.papertrade import resolve_legs
from nba.parlay.shadow import (
    TrackRecord,
    clustered_lower_bound,
    ensure_schema,
    log_shadow,
    settle_shadow,
    track_record,
)
from nba.parlay.slate import load_slate

CFG = load_config()
HOME, AWAY, OLD, NEW = 10, 20, 30, 40
PARAMS = GameModelParams(0.0, 7.0, 13.6, 220.0, 0.5, 18.8, 220.0, 20.0, 5.0, 1, 1, (2023,))
SLATE = date(2026, 10, 20)
GRID = [float(v) for v in range(19)]


def _db(tmp: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("ATTACH ':memory:' AS nba")
    con.execute(
        "CREATE TABLE nba.forward_predictions (run_id VARCHAR, made_at TIMESTAMP, game_id VARCHAR,"
        " tipoff TIMESTAMP, model_name VARCHAR, version VARCHAR, target VARCHAR, player_id INT,"
        " prediction JSON)"
    )
    con.execute(
        "CREATE TABLE nba.games (game_id VARCHAR, game_date DATE, home_team INT, away_team INT,"
        " home_pts INT, away_pts INT)"
    )
    con.execute("CREATE TABLE nba.player_game_stats (game_id VARCHAR, player_id INT, team_id INT)")
    con.execute("INSERT INTO nba.games VALUES ('0022500001', '2026-04-01', 30, 20, 100, 90)")
    # mover 2: last played for OLD (30), now rostered by HOME (10); veteran 3 stayed on AWAY (20)
    con.execute(
        "INSERT INTO nba.player_game_stats VALUES ('0022500001', 2, 30), ('0022500001', 3, 20)"
    )
    tip = datetime(2026, 10, 21, 0, 30)
    win = {"p_home": 0.6, "home_team": HOME, "away_team": AWAY}
    con.execute(
        "INSERT INTO nba.forward_predictions VALUES ('r', '2026-10-20 12:00', '0022600001', ?, "
        "'rung0_injury_elo', 'v', 'win_prob_home', -1, ?)",
        [tip, json.dumps(win)],
    )
    for pid in (1, 2, 3):  # 1 rookie (no history), 2 mover, 3 veteran
        con.execute(
            "INSERT INTO nba.forward_predictions VALUES ('r', '2026-10-20 12:00', '0022600001', ?, "
            "'props_context_residual', 'v', 'pts', ?, ?)",
            [tip, pid, json.dumps({"q_grid": GRID, "p_play": 0.9})],
        )
    root = tmp / "rosters" / SLATE.isoformat()
    root.mkdir(parents=True)
    pl.DataFrame({"team_id": [HOME, HOME], "player_id": [1, 2]}).write_parquet(
        f"{root}/{HOME}.parquet"
    )
    pl.DataFrame({"team_id": [AWAY], "player_id": [3]}).write_parquet(f"{root}/{AWAY}.parquet")
    return con


def test_recent_source_drops_mover_and_rookie_but_says_so(tmp_path: Path) -> None:
    info = load_slate(_db(tmp_path), SLATE, PARAMS, prefix="nba.")
    ctx = info.ctxs["0022600001"]
    assert set(ctx.team_of) == {3}  # only the veteran is placeable from last-game teams
    reasons = {int(d["player_id"]): str(d["reason"]) for d in info.dropped}  # type: ignore[call-overload]
    assert reasons[1].startswith("no_team_known") and reasons[2].startswith(
        f"team_{OLD}_not_in_game"
    )
    assert any("dropped" in n for n in info.notes)  # never silent


def test_official_source_places_mover_and_rookie(tmp_path: Path) -> None:
    info = load_slate(
        _db(tmp_path), SLATE, PARAMS, prefix="nba.", roster_source="official",
        roster_dir=tmp_path / "rosters",
    )  # fmt: skip
    assert info.roster_source == "official" and not info.dropped
    assert info.ctxs["0022600001"].team_of == {1: HOME, 2: HOME, 3: AWAY}


def test_official_source_without_cache_falls_back_and_says_so(tmp_path: Path) -> None:
    info = load_slate(
        _db(tmp_path), SLATE, PARAMS, prefix="nba.", roster_source="official",
        roster_dir=tmp_path / "nowhere",
    )  # fmt: skip
    assert info.roster_source == "recent" and any("no complete cached" in n for n in info.notes)
    assert {int(d["player_id"]) for d in info.dropped} == {1, 2}  # type: ignore[call-overload]


def test_unknown_roster_source_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_slate(_db(tmp_path), SLATE, PARAMS, prefix="nba.", roster_source="bogus")


# ------------------------------------------------------------------ N6
def test_clustered_bound_open_and_shut() -> None:
    rng = np.random.default_rng(0)
    groups = np.repeat(np.arange(40), 5)
    clear = 0.05 + rng.normal(0, 0.02, 200)
    mean, lo, g = clustered_lower_bound(clear, groups, 0.95)
    assert g == 40 and lo is not None and lo > 0
    noisy = rng.normal(0.0, 0.1, 30)  # n=30, no true skill: the bound must be negative
    _, lo2, _ = clustered_lower_bound(noisy, np.arange(30), 0.95)
    assert lo2 is not None and lo2 < 0
    _, lo3, g3 = clustered_lower_bound(clear[:10], np.repeat([0, 1], 5), 0.95)
    assert lo3 is None and g3 == 2  # fewer than 3 games: no interval, gate shut


def _shadow(n: int, games: int, model_better: bool) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    rng = np.random.default_rng(7)
    for i in range(n):
        y = i % 2 == 0
        if model_better:
            p = 0.9 if y else 0.1
        else:  # a noisy, slightly informative model: right 72% of the time at 0.8 confidence
            right = rng.random() < 0.72
            p = 0.8 if (y == right) else 0.2
        log_shadow(
            con, shadow_id=f"2026-10-{1 + i % 20:02d}|t{i}|yes", ticker=f"t{i}", side="yes",
            legs=[Leg(f"g{i % games}", 1, "pts", 10.0)], raw_model_prob=p, market_mid=0.5,
            price=0.5, engine="x",
        )  # fmt: skip
        con.execute(
            "UPDATE shadow_predictions SET settled=TRUE, outcome=? WHERE ticker=?", [y, f"t{i}"]
        )
    return con


def test_gate_shut_at_n30_default_no_positive_ev() -> None:
    # 30 rows, a model that is slightly better by point estimate: the interval still has no skill
    tr = track_record(_shadow(30, 30, model_better=False))
    assert tr.n_settled == 30 and tr.skill > 0 and tr.diff_lo is not None and tr.diff_lo <= 0
    assert tr.gated_skill(1) == 0.0
    assert tr.diff_lo is not None
    r = evaluate(
        [Leg("g", 1, "pts", 10.0)], 0.99, 0, 0, 0.5, CFG, n_settled=30, skill=tr.gated_skill(1)
    )
    assert r.verdict == NO_POSITIVE_EV and r.model_weight == 0.0


def test_gate_opens_only_with_clear_interval_and_clusters() -> None:
    tr = track_record(_shadow(200, 40, model_better=True))
    assert tr.diff_lo is not None and tr.diff_lo > 0 and tr.gated_skill(14) > 0
    one_game = track_record(_shadow(200, 1, model_better=True))
    assert one_game.diff_lo is None and one_game.gated_skill(14) == 0.0
    assert "gate shut" in one_game.describe()
    assert TrackRecord(0, None, None, 0.0).gated_skill(0) == 0.0


def test_calibration_sd_is_measured_and_wider_in_the_tail() -> None:
    assert CFG.calibration_logit_sd >= 0.6  # lowest measured tail gap, not the old 0.15 guess
    assert calibration_sd(CFG, 0.5) == CFG.calibration_logit_sd
    assert calibration_sd(CFG, 0.9) == calibration_sd(CFG, 0.1) == CFG.calibration_logit_sd_tail
    assert CFG.calibration_logit_sd_tail > CFG.calibration_logit_sd


# ------------------------------------------------------------------ N3
def test_last_fair_price_policy_math() -> None:
    assert "last_fair_price" in DNP_POLICIES
    # all play: identical under every policy
    assert effective_prob(0.6, 1.0, "last_fair_price", 0.5, 0.48) == pytest.approx(0.6)
    # DNP pays the last fair price: EV = p_nov*(p - cost) + (1 - p_nov)*(L - cost)
    p_eff = effective_prob(0.6, 0.8, "last_fair_price", 0.52, 0.48)
    assert p_eff - 0.52 == pytest.approx(0.8 * (0.6 - 0.52) + 0.2 * (0.48 - 0.52))
    # strictly between loss and a refund at cost when L < cost
    assert effective_prob(0.6, 0.8, "loss", 0.52) < p_eff < effective_prob(0.6, 0.8, "void", 0.52)
    with pytest.raises(ValueError):
        effective_prob(0.6, 0.8, "last_fair_price", 0.52)  # no price supplied: fail loudly


def _results(dnp: bool) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE games (game_id VARCHAR, game_date DATE, home_pts INT, away_pts INT)")
    con.execute(
        "CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT, team_id INT,"
        " pts DOUBLE, reb DOUBLE, ast DOUBLE, fg3m DOUBLE, minutes DOUBLE)"
    )
    con.execute("INSERT INTO games VALUES ('g1', '2026-10-20', 100, 90)")
    con.execute(
        "INSERT INTO player_game_stats VALUES ('g1', 1, 1, ?, 0, 0, 0, ?)",
        [None if dnp else 25.0, 0.0 if dnp else 30.0],
    )
    return con


def test_resolve_legs_dnp_policies() -> None:
    leg = [Leg("g1", 1, "pts", 20.0, "yes", 1)]
    res = _results(dnp=True)
    assert resolve_legs(res, leg, "void")[0] == "void"
    assert resolve_legs(res, leg, "loss") == ("done", False)
    assert resolve_legs(res, leg, "last_fair_price")[0] == "last_price"
    with pytest.raises(ValueError):
        resolve_legs(res, leg, "bogus")
    assert resolve_legs(_results(dnp=False), leg, "last_fair_price") == ("done", True)


def test_shadow_last_price_has_no_binary_outcome() -> None:
    con = duckdb.connect(":memory:")
    ensure_schema(con)
    leg = Leg("g1", 1, "pts", 20.0, "yes", 1)
    log_shadow(con, shadow_id="s", ticker="t", side="yes", legs=[leg], raw_model_prob=0.6,
               market_mid=0.5, price=0.5, engine="x")  # fmt: skip
    assert settle_shadow(con, _results(dnp=True), "last_fair_price") == 1
    assert con.execute("SELECT settled, outcome FROM shadow_predictions").fetchone() == (True, None)
