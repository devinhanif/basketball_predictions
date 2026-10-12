# mypy: disable-error-code="no-untyped-call,func-returns-value,attr-defined,unused-ignore,no-untyped-def,arg-type,index,assignment,operator,type-arg,no-any-return"
# ruff: noqa: E501
"""SEQ_FULL_MIX descriptive step: personas' arithmetic on a hand-computed fixture, the verdict firewall, confusion counts, context flags, and a synthetic end to end."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research.eval import seq_full_mix_descriptive as D


def _fixture():
    """Two same-night legs, hand-computed below.
    L1 (game 0): over -110 / under -110, market 0.50, arm P(over) 0.55, outcome over.  L2 (game 1): over +100 / under -130, market 0.50, arm P(over) 0.30, outcome over."""
    legs = {
        "po": np.array([-110.0, 100.0]), "pu": np.array([-110.0, -130.0]), "pm": np.array([0.5, 0.5]), "over": np.array([1, 1]),
        "night": np.array([20000, 20000]), "gi": np.array([0, 1]), "idx": np.array([0, 1]),
    }  # fmt: skip
    return legs, np.array([0.55, 0.30])


def test_persona_arithmetic_matches_the_hand_computation() -> None:
    legs, p = _fixture()
    # P1 (edge >= 0.03): L1 edge 0.05 over, wins 20 * 100/110 = 18.181818; L2 edge 0.20 under, loses 20 -> n 2, staked 40, pnl -1.818182, ROI -0.0454545
    b = D.simulate(legs, p, "P1")
    assert list(b["stake"]) == [20.0, 20.0] and b["pnl"] == pytest.approx(
        [18.181818, -20.0], abs=1e-5
    )
    rec, path = D.summarize_bets(b, 2, "P1")
    assert (
        rec["n_bets"] == 2
        and rec["staked"] == 40.0
        and rec["pnl"] == pytest.approx(-1.818182, abs=1e-5)
        and rec["roi"] == pytest.approx(-0.0454545, abs=1e-6)
    )
    assert (
        rec["longest_losing_streak"] == 1
        and rec["max_drawdown"] == pytest.approx(1.818182, abs=1e-5)
        and rec["share_months_profit"] == 0.0
        and len(path) == 1
    )
    # P2 (>= 0.06): only L2, loses 20 -> ROI -1
    b2 = D.simulate(legs, p, "P2")
    assert list(b2["stake"]) == [20.0] and list(b2["pnl"]) == [-20.0]
    # P3 quarter-Kelly on 500: L1 b = 0.909091, f = (0.909091 * 0.55 - 0.45) / 0.909091 = 0.055 -> 0.25 * 0.055 * 500 = 6.875, wins 6.875 * 0.909091 = 6.25;
    # L2 under: b = 0.769231, p = 0.70, f = 0.31 -> 38.75, capped at 20, loses 20
    b3 = D.simulate(legs, p, "P3")
    assert b3["stake"] == pytest.approx([6.875, 20.0], abs=1e-6) and b3["pnl"] == pytest.approx(
        [6.25, -20.0], abs=1e-6
    )
    rec3, path3 = D.summarize_bets(b3, 2, "P3")
    assert (
        rec3["staked"] == pytest.approx(26.875)
        and rec3["pnl"] == pytest.approx(-13.75)
        and path3["bankroll"].iloc[-1] == pytest.approx(486.25)
    )
    # P4: legs on different games, top edge L2 (0.20) then L1 (0.05): price 1.909091 * 1.769231 = 3.377622; L2 loses -> -10, one bet, staked 10
    b4 = D.simulate(legs, p, "P4")
    assert list(b4["stake"]) == [10.0] and list(b4["pnl"]) == [-10.0]
    assert D.simulate(legs, p, "P0")["pnl"].size == 0
    legs_w = dict(
        legs, over=np.array([1, 0])
    )  # now L2's under wins: parlay pays 10 * (3.377622 - 1)
    assert D.simulate(legs_w, p, "P4")["pnl"] == pytest.approx([23.77622], abs=1e-4)
    same_game = dict(legs, gi=np.array([0, 0]))
    assert (
        D.simulate(same_game, p, "P4")["pnl"].size == 0
    )  # two legs of one game are never parlayed


def test_p3_scales_a_night_to_the_bankroll_and_never_bets_negative_kelly() -> None:
    n = 200
    legs = {
        "po": np.full(n, 150.0),
        "pu": np.full(n, -170.0),
        "pm": np.full(n, 0.4),
        "over": np.zeros(n, dtype=int),
        "night": np.full(n, 20000),
        "gi": np.arange(n),
        "idx": np.arange(n),
    }
    b = D.simulate(legs, np.full(n, 0.7), "P3")
    assert b["stake"].sum() <= D.BANKROLL0 + 1e-9 and (b["stake"] <= D.KELLY_CAP + 1e-9).all()
    flat = dict(legs, po=np.full(n, -110.0), pu=np.full(n, -110.0), pm=np.full(n, 0.5))
    none = D.simulate(
        flat, np.full(n, 0.5), "P3"
    )  # at a vigged price a coin-flip forecast has negative Kelly on both sides
    assert none["stake"].size == 0


def test_grid_quantiles_p_over_and_confusion() -> None:
    qi = np.tile(np.arange(199)[None, :] // 20, (2, 1))  # values 0..9
    assert list(D.grid_q(qi, 0.5)) == [4, 4] and D.grid_q(qi, 0.05)[0] == 0
    assert (
        D.p_over_line(qi, np.array([4.5, 9.5]))[0] == pytest.approx((qi[0] > 4.5).mean())
        and D.p_over_line(qi, np.array([9.5, 9.5]))[1] == 0.0
    )
    c = D.confusion(np.array([1, 1, 0, 0, 1]), np.array([1, 0, 0, 1, 1]))
    assert (
        c["tp_over_over"],
        c["fp_pred_over_was_under"],
        c["fn_pred_under_was_over"],
        c["tn_under_under"],
    ) == (2, 1, 1, 1) and c["accuracy"] == 0.6


def test_context_flags_second_night_and_returning_teammate() -> None:
    d0 = dt.date(2025, 11, 1)
    games = pd.DataFrame(
        {
            "game_id": [f"G{i}" for i in range(6)],
            "game_date": [d0 + dt.timedelta(days=x) for x in (0, 2, 3, 5, 7, 9)],
            "home_team": [1] * 6,
            "away_team": [2] * 6,
        }
    )
    rows = []
    for i in range(6):
        rows.append(
            (f"G{i}", 10, 1, 33.0 if i < 2 or i == 5 else None)
        )  # star plays G0, G1, misses G2-G4, back in G5 (3 missed team games)
        rows.append((f"G{i}", 11, 1, 20.0))
    pgs = pd.DataFrame(rows, columns=["game_id", "player_id", "team_id", "minutes"])
    f = D.context_flags(pgs, games).set_index(["game_id", "player_id"])
    assert bool(f.loc[("G2", 11), "second_night_b2b"]) and not bool(
        f.loc[("G1", 11), "second_night_b2b"]
    )  # G1 -> G2 is consecutive days
    assert (
        bool(f.loc[("G5", 11), "teammate_returns"])
        and not bool(f.loc[("G5", 10), "teammate_returns"])
        and not bool(f.loc[("G4", 11), "teammate_returns"])
    )


# --------------------------------------------------------------------------- end to end on synthetic databases
def _synthetic(tmp: Path):
    import duckdb
    from scipy.stats import poisson

    rng = np.random.default_rng(2)
    games, pgs, gid, pid, tid, dd = [], [], [], [], [], []
    for i in range(10):
        day = dt.date(2025, 10, 22) + dt.timedelta(days=i // 2)
        g = f"002250{i:04d}"
        games.append((g, day, 2025, 100 + i, 200 + i, 100, 99, "ABC"))
        for team in (100 + i, 200 + i):
            for k in range(3):
                p = team * 10 + k
                pgs.append((g, p, team, 28.0))
                (
                    gid.append(g),
                    pid.append(p),
                    tid.append(team),
                    dd.append(np.datetime64(day, "D").astype(np.int64)),
                )
    con = duckdb.connect(str(tmp / "nba.duckdb"))
    con.execute(
        "CREATE TABLE games(game_id VARCHAR, game_date DATE, season INTEGER, home_team INTEGER, away_team INTEGER, home_pts INTEGER, away_pts INTEGER, national_tv VARCHAR)"
    )
    con.executemany("INSERT INTO games VALUES (?,?,?,?,?,?,?,?)", games)
    con.execute(
        "CREATE TABLE player_game_stats(game_id VARCHAR, player_id INTEGER, team_id INTEGER, minutes DOUBLE)"
    )
    con.executemany("INSERT INTO player_game_stats VALUES (?,?,?,?)", pgs)
    con.close()
    n = len(gid)
    taus = (np.arange(199) + 0.5) / 199
    base, arms = {}, {}
    for stat in D.STATS:
        lam = rng.uniform(4, 20, n)
        y = rng.poisson(lam).astype(float)
        base[stat] = {
            "game_id": np.array(gid),
            "player_id": np.array(pid),
            "season": np.full(n, 2025),
            "y": y,
            "minutes": np.full(n, 28.0),
            "starter10": rng.random(n),
            "team_id": np.array(tid),
        }
        arms[stat] = {
            a: np.stack([poisson.ppf(taus, m * s) for m in lam]).astype(np.int16)
            for a, s in (("A0", 1.0), ("FULL", 1.05), ("AVG", 0.97))
        }
    world = {"base": base, "date_days": np.array(dd), "_arms": arms}
    oc = duckdb.connect(str(tmp / "odds.duckdb"))
    oc.execute(
        "CREATE TABLE odds_history(season INTEGER, game_id VARCHAR, snapshot_kind VARCHAR, requested_at TIMESTAMP, snapshot_ts TIMESTAMP, book VARCHAR, market VARCHAR, player_name VARCHAR, player_id BIGINT, side VARCHAR, point DOUBLE, price_american INTEGER, implied_prob DOUBLE)"
    )
    ts = dt.datetime(2025, 10, 22, 22, 0)
    for stat, mk in (("pts", "player_points"), ("reb", "player_rebounds")):
        for i, (g, p) in enumerate(zip(gid, pid, strict=True)):
            line = float(np.floor(base[stat]["y"][i] * 0 + np.median(base[stat]["y"]))) + 0.5
            for book in D.BOOKS:
                for side, price in (("over", -105), ("under", -115)):
                    oc.execute(
                        "INSERT INTO odds_history VALUES (2025,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [g, "t60", ts, ts, book, mk, f"Player {p}", p, side, line, price, 0.5],
                    )
    oc.close()
    return world


def test_descriptive_step_refuses_before_the_verdict_and_never_writes_it(tmp_path: Path) -> None:
    world = _synthetic(tmp_path)
    out = tmp_path / "stage2"
    out.mkdir()
    verdict = out / "results.json"
    kw = dict(
        world=world,
        target=2025,
        out_dir=out,
        verdict_path=verdict,
        nba_db=str(tmp_path / "nba.duckdb"),
        odds_db=tmp_path / "odds.duckdb",
        pplay_dir=None,
        seen=(),
    )
    with pytest.raises(RuntimeError, match="after the verdict"):
        D.run_descriptive(**kw)
    assert not (out / "descriptive").exists()  # nothing written before the verdict
    verdict.write_text(json.dumps({"scores": {}}))
    with pytest.raises(RuntimeError, match="no verdict"):
        D.run_descriptive(**kw)
    verdict.write_text(json.dumps({"verdict": {"pass": False}}))
    h0 = hashlib.sha256(verdict.read_bytes()).hexdigest()
    meta = D.run_descriptive(**kw)
    assert hashlib.sha256(verdict.read_bytes()).hexdigest() == h0  # byte-identical
    d = out / "descriptive"
    assert {
        "rows.parquet",
        "rows.csv",
        "confusion.json",
        "spot_check.md",
        "simulation.json",
        "simulation.md",
        "sim_bankroll_paths.csv",
    } <= {p.name for p in d.iterdir()}
    rows = pd.read_parquet(d / "rows.parquet")
    assert len(rows) == 60 * 4 and {
        "AVG_q05",
        "AVG_q95",
        "FULL_crps",
        "A0_pit",
        "pinnacle_line",
        "draftkings_p_over_devig",
        "AVG_p_over_pinnacle",
        "player",
        "p_play",
        "opp_id",
    } <= set(rows.columns)
    pts = rows[rows["stat"] == "pts"]
    assert (
        pts["pinnacle_line"].notna().all()
        and rows[rows["stat"] == "ast"]["pinnacle_line"].isna().all()
    )
    assert (pts["AVG_q05"] <= pts["AVG_q50"]).all() and (pts["AVG_q50"] <= pts["AVG_q95"]).all()
    conf = json.loads((d / "confusion.json").read_text())
    c = conf["pinnacle"]["pts"]["AVG_at_line"]
    assert (
        c["n"] == 60 == conf["pinnacle"]["pts"]["n_rows"]
        and c["tp_over_over"]
        + c["fp_pred_over_was_under"]
        + c["fn_pred_under_was_over"]
        + c["tn_under_under"]
        == 60
    )
    assert (
        set(conf["pinnacle"]["pts"]["AVG_by_confidence"])
        == {"0-0.05", "0.05-0.10", "0.10-0.20", "0.20-0.50"}
        and "market_devig_at_line" in conf["pinnacle"]["pts"]
    )
    sim = json.loads((d / "simulation.json").read_text())
    assert (
        "2025/pinnacle/AVG/P1" in sim
        and sim["2025/pinnacle/AVG/P0"]["n_bets"] == 0
        and "descriptive, not a test of edge" in (d / "simulation.md").read_text()
    )
    assert "AVG beat FULL most" in (d / "spot_check.md").read_text() and meta["rows"] == 240


def test_descriptive_output_dir_cannot_contain_the_verdict(tmp_path: Path) -> None:
    out = tmp_path / "v"
    (out / "descriptive").mkdir(parents=True)
    verdict = out / "descriptive" / "results.json"
    verdict.write_text(json.dumps({"verdict": {}}))
    with pytest.raises(RuntimeError, match="separate"):
        D.run_descriptive({}, 2025, out, verdict, "n", tmp_path, None)
