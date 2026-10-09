"""Pre-registered checkpoint machinery on synthetic forward data (no network)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import pytest
from scipy.stats import poisson
from statsmodels.stats.multitest import multipletests

from nba.daily import checkpoint as ck
from nba.daily.__main__ import main
from nba.daily.settle import settle_pending
from nba.daily.store import ForwardPrediction, append_predictions, ensure_tables
from nba.daily.t30 import DECISIONS_DDL
from nba.db.connect import connect
from nba.props import full_support as fs
from nba.props.forward import QUANTILE_TAUS

PROD, T30, INT, LT = ck.PROD, ck.ARM_T30, ck.ARM_INT, ck.ARM_LT
HOME, AWAY = 1610612738, 1610612755
D0 = date(2026, 10, 22)
STATS = ("pts", "reb", "ast", "fg3m")
LAM = {"pts": 14.0, "reb": 5.0, "ast": 3.5, "fg3m": 1.6}
SETTLED_AT = datetime(2027, 6, 1)


def _full(lam: float, stat: str) -> dict[str, Any]:
    """Vectorised equivalent of ``full_support.from_dist`` for a Poisson (fast fixtures)."""
    ks = np.arange(1, fs.KMAX[stat] + 1)
    c = np.rint(poisson.sf(ks - 1, lam) * fs.PARAM_DENOM).astype(int)
    return {"n": fs.PARAM_DENOM, "c": fs._cut(c, fs.PARAM_DENOM)}


def _pred(lam: float, stat: str, **extra: Any) -> dict[str, Any]:
    q = [float(v) for v in poisson.ppf(QUANTILE_TAUS, lam)]
    return {
        "mean": lam,
        "std": float(np.sqrt(lam)),
        "q10": q[1],
        "q50": q[9],
        "q90": q[17],
        "q_grid": q,
        "p_ge_full": _full(lam, stat),
        **extra,
    }


def _con() -> duckdb.DuckDBPyConnection:
    con = connect(":memory:")
    ensure_tables(con)
    con.execute(DECISIONS_DDL)
    return con


def build(
    n_dates: int = 3,
    n_games: int = 2,
    n_players: int = 6,
    seed: int = 0,
    arm_better: bool = True,
    skip_every: int = 5,
    con: duckdb.DuckDBPyConnection | None = None,
) -> duckdb.DuckDBPyConnection:
    """Nightly slates with known skill gaps.

    True rate ``lam``; production (T-90) predicts ``1.25 * lam`` (miscalibrated). The T-30 and
    lower-tail arms predict ``lam`` when ``arm_better``; the integer arm copies production's
    distribution (so its integer-support delta is exactly 0)."""
    rng = np.random.default_rng(seed)
    if con is None:
        con = _con()
    else:
        ensure_tables(con)
        con.execute(DECISIONS_DDL)
    run_n = 0
    for di in range(n_dates):
        d = D0 + timedelta(days=di)
        tip = datetime(d.year, d.month, d.day, 23, 0)
        for gi in range(n_games):
            gnum = di * n_games + gi
            gid = f"00226{gnum:05d}"
            home_p = float(np.clip(rng.normal(0.58, 0.08), 0.2, 0.9))
            hw = bool(rng.random() < home_p)
            con.execute(
                "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts,"
                " away_pts) VALUES (?,?,?,?,?,?,?)",
                [gid, d, 2026, HOME, AWAY, 110 if hw else 100, 100 if hw else 110],
            )
            run = f"run{run_n}"
            run_n += 1
            batch: list[ForwardPrediction] = []
            t30_rows: list[ForwardPrediction] = []
            for pid in range(1, n_players + 1):
                lams = {s: LAM[s] * float(rng.uniform(0.8, 1.2)) for s in STATS}
                ys = {s: int(rng.poisson(lams[s])) for s in STATS}
                con.execute(
                    "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts,"
                    " reb, ast, fg3m, starter) VALUES (?,?,?,?,?,?,?,?,?)",
                    [gid, pid, HOME, 30.0, ys["pts"], ys["reb"], ys["ast"], ys["fg3m"], pid <= 3],
                )
                sr = {"starter_rate": 1.0 if pid <= 3 else 0.0}
                for s in STATS:
                    lam = lams[s]
                    arm_lam = lam if arm_better else 1.25 * lam
                    batch.append(
                        ForwardPrediction(gid, tip, PROD, "v1", s, _pred(1.25 * lam, s, **sr), pid)
                    )
                    batch.append(
                        ForwardPrediction(gid, tip, INT, "v1", s, _pred(1.25 * lam, s, **sr), pid)
                    )
                    if s == "pts":
                        batch.append(
                            ForwardPrediction(gid, tip, LT, "v1", s, _pred(arm_lam, s, **sr), pid)
                        )
                    snap = (tip - timedelta(minutes=40)).isoformat()
                    t30_rows.append(
                        ForwardPrediction(
                            gid,
                            tip,
                            T30,
                            "t30-v1",
                            s,
                            _pred(arm_lam, s, snapshot_fetched_at=snap, **sr),
                            pid,
                        )
                    )
            append_predictions(con, run, tip - timedelta(minutes=90), batch)
            append_predictions(con, f"t30-{run}", tip - timedelta(minutes=45), t30_rows)
            logged = gnum % skip_every != 0
            con.execute(
                "INSERT INTO forward_t30_decisions VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    gid,
                    tip - timedelta(minutes=45),
                    tip,
                    tip - timedelta(minutes=30),
                    "s",
                    tip - timedelta(minutes=40),
                    "logged" if logged else "skipped",
                    "ok" if logged else "no_confirmed_lineup",
                    24 if logged else 0,
                ],
            )
            for m, bias in (("rung0_injury_elo", 0.0), ("rung0_mov_elo", 0.03)):
                p = float(np.clip(home_p + bias, 0.05, 0.95))
                append_predictions(
                    con,
                    f"w-{m}-{run}",
                    tip - timedelta(minutes=90),
                    [ForwardPrediction(gid, tip, m, "v1", "win_prob_home", {"p_home": p})],
                )
    return con


# ------------------------------------------------------------------ storage + settle selection


def test_comparator_selection_latest_made_at_le_tip_minus_lead() -> None:
    con = _con()
    d = D0
    tip = datetime(d.year, d.month, d.day, 23, 0)
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts)"
        " VALUES ('0022600001', ?, 2026, ?, ?, 100, 90)",
        [d, HOME, AWAY],
    )
    con.execute(
        "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, reb, ast, fg3m)"
        " VALUES ('0022600001', 7, ?, 30, 12, 4, 3, 1)",
        [HOME],
    )
    # production: T-120 (eligible), T-70 (eligible, latest eligible), T-50 and T-5 (too late)
    for run, lead, lam in (("a", 120, 10), ("b", 70, 12), ("c", 50, 14), ("d", 5, 16)):
        append_predictions(
            con,
            run,
            tip - timedelta(minutes=lead),
            [ForwardPrediction("0022600001", tip, PROD, "v1", "pts", _pred(lam, "pts"), 7)],
        )
    # T-30 arm: eligibility is on the lineup snapshot (fetched < tip - 30), not made_at, so a row
    # made at T-20 from a T-50 snapshot is eligible and the latest; a T-10 row built from a T-25
    # snapshot is not
    for run, lead, snap_lead, lam in (("t1", 45, 50, 11), ("t2", 20, 50, 13), ("t3", 10, 25, 15)):
        snap = (tip - timedelta(minutes=snap_lead)).isoformat()
        append_predictions(
            con,
            run,
            tip - timedelta(minutes=lead),
            [
                ForwardPrediction(
                    "0022600001",
                    tip,
                    T30,
                    "t",
                    "pts",
                    _pred(lam, "pts", snapshot_fetched_at=snap),
                    7,
                )
            ],
        )
    settle_pending(con, SETTLED_AT)
    rows = dict(
        con.execute(
            "SELECT model_name, run_id FROM forward_scores_elig WHERE player_id = 7"
        ).fetchall()
    )
    assert rows == {PROD: "b", T30: "t2"}
    # the legacy score table still scores the latest row overall (unchanged behaviour)
    legacy = con.execute(
        "SELECT pred FROM forward_scores WHERE model_name = ? AND player_id = 7", [PROD]
    ).fetchone()
    assert legacy is not None and legacy[0] == 16
    # idempotent
    n = con.execute("SELECT count(*) FROM forward_scores_elig").fetchone()
    settle_pending(con, SETTLED_AT)
    assert con.execute("SELECT count(*) FROM forward_scores_elig").fetchone() == n


def test_rps_stored_equals_brute_force_and_unscorable_counted() -> None:
    con = build(n_dates=2, n_games=1, n_players=3)
    # one production row without p_ge_full -> unscorable, counted not dropped
    con.execute(
        "INSERT INTO forward_predictions VALUES ('legacy', ?, '0022600000', ?, ?, 'v0', 'pts', 99,"
        " ?)",
        [
            datetime(2026, 10, 22, 21, 0),
            datetime(2026, 10, 22, 23, 0),
            PROD,
            json.dumps({"mean": 5.0, "std": 2.0}),
        ],
    )
    con.execute(
        "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, reb, ast, fg3m)"
        " VALUES ('0022600000', 99, ?, 20, 5, 1, 1, 0)",
        [HOME],
    )
    settle_pending(con, SETTLED_AT)
    st = dict(con.execute("SELECT status, count(*) FROM forward_scores_elig GROUP BY 1").fetchall())
    assert st["unscorable"] == 1 and st["scored"] > 0
    rows = con.execute(
        "SELECT s.y, s.rps, p.prediction FROM forward_scores_elig s JOIN forward_predictions p "
        "ON p.run_id = s.run_id AND p.game_id = s.game_id AND p.model_name = s.model_name "
        "AND p.target = s.target AND p.player_id = s.player_id "
        "WHERE s.status = 'scored' AND s.target <> 'win_prob_home'"
    ).fetchall()
    assert rows
    for y, rps, pj in rows:
        c = np.asarray(json.loads(pj)["p_ge_full"]["c"], dtype=float) / fs.PARAM_DENOM
        pmf = np.diff(np.concatenate([[1.0], c, [0.0]])) * -1.0
        cum = 0.0
        ref = 0.0
        for k in range(len(pmf) + int(y) + 1):
            cum += pmf[k] if k < len(pmf) else 0.0
            ref += (cum - float(y <= k)) ** 2
        assert rps == pytest.approx(ref, abs=1e-9)


# ------------------------------------------------------------------ statistics


def test_bh_matches_statsmodels_and_fixes_family() -> None:
    rng = np.random.default_rng(3)
    for _ in range(20):
        p = rng.uniform(0, 1, 9) ** 3
        got = ck.bh_adjust(dict(zip(ck.FAMILY, p, strict=True)))
        ref = multipletests(p, method="fdr_bh")[1]
        assert np.allclose([got[k] for k in ck.FAMILY], ref)
    # missing / NaN tests enter with p = 1.0 and are never dropped from m
    adj = ck.bh_adjust({"win": 0.001, "lt_pts": float("nan")})
    assert adj["win"] == pytest.approx(0.001 * 9) and adj["lt_pts"] == 1.0
    assert len(adj) == 9
    with pytest.raises(KeyError):
        ck.bh_adjust({"t30_pts": 0.01, "bogus": 0.5})


def test_pocock_constants() -> None:
    assert ck.ALPHA_LOOK == 0.0182 and pytest.approx(0.9818) == ck.LEVEL
    assert ck.EFFICACY_LOOKS == ("L30", "L60", "L120", "END")
    assert ck.B_DEFAULT == 10_000 and ck.SEED == 2026 and len(ck.FAMILY) == 9


def test_boot_coverage_and_game_level_is_anticonservative() -> None:
    rng = np.random.default_rng(11)
    n_dates, per = 40, 12
    dates = np.repeat(np.arange(n_dates), per)
    cover_date = cover_unit = 0
    sims = 120
    for s in range(sims):
        v = rng.normal(0, 1.0, n_dates)[dates] + rng.normal(0, 0.3, dates.size)  # null mean 0
        r = ck.date_cluster_boot(v, dates, B=400, seed=s, level=0.95)
        cover_date += r.lo <= 0 <= r.hi
        r2 = ck.date_cluster_boot(v, np.arange(v.size), B=400, seed=s, level=0.95)  # unit clusters
        cover_unit += r2.lo <= 0 <= r2.hi
    assert cover_date / sims >= 0.88
    assert cover_unit / sims <= 0.75  # ignoring the shared date noise undercovers badly


def test_boot_pvalue_floor_nan_and_determinism() -> None:
    v = np.full(60, -1.0)
    dates = np.repeat(np.arange(10), 6)
    r = ck.date_cluster_boot(v, dates, B=500, seed=2026)
    assert r.p == pytest.approx(1 / 501) and r.hi < 0 and r.n == 60 and r.n_dates == 10
    assert ck.date_cluster_boot(v, dates, B=500, seed=2026) == r
    one = ck.date_cluster_boot(v, np.zeros(60), B=500)
    assert np.isnan(one.lo) and np.isnan(one.p) and one.n_dates == 1
    centered = np.tile([-1.0, 1.0], 30)
    assert ck.date_cluster_boot(centered, dates, B=500).p == 1.0


# ------------------------------------------------------------------ pairing + scoring


def test_paired_deltas_counts_and_same_run() -> None:
    con = build(n_dates=3, n_games=2, n_players=6)
    # a DNP player (both arms) and a one-sided arm row
    gid = "0022600000"
    con.execute(
        "UPDATE player_game_stats SET minutes = 0 WHERE game_id = ? AND player_id = 6", [gid]
    )
    tip = datetime(2026, 10, 22, 23, 0)
    append_predictions(
        con,
        "extra",
        tip - timedelta(minutes=80),
        [
            ForwardPrediction(
                gid,
                tip,
                T30,
                "t",
                "pts",
                _pred(9, "pts", snapshot_fetched_at=(tip - timedelta(minutes=60)).isoformat()),
                777,
            )
        ],
    )
    con.execute(
        "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts)"
        " VALUES (?,?,?,?,?)",
        [gid, 777, HOME, 10, 4],
    )
    settle_pending(con, SETTLED_AT)
    pairs, c = ck.paired_deltas(con, T30, PROD, "pts")
    assert c.dnp == 1 and c.only_arm == 1 and c.only_comparator == 0
    assert pairs.height == 6 * 6 - 1 and c.n_pairs == pairs.height
    assert (pairs["delta_int"] < 0).mean() > 0.6  # arm (true rate) better than 1.25x production
    # same-run pairing: put an INT row into a different run than the production row
    con.execute(
        "UPDATE forward_scores_elig SET run_id = 'other' WHERE model_name = ? AND game_id = ?",
        [INT, gid],
    )
    p2, c2 = ck.paired_deltas(con, INT, PROD, "pts", same_run=True)
    assert c2.run_mismatch == 5 and c2.dnp == 1  # the DNP player is counted as DNP first
    assert gid not in set(p2["game_id"])
    assert np.allclose(p2["delta_int"].to_numpy(), 0.0)  # integer arm: same distribution
    assert p2["delta_pinball19"].abs().max() < 1e-12


def test_slice_columns_present() -> None:
    con = build(n_dates=2, n_games=1, n_players=6)
    settle_pending(con, SETTLED_AT)
    pairs, _ = ck.paired_deltas(con, T30, PROD, "reb")
    assert set(ck._PAIR_SCHEMA) <= set(pairs.columns)
    assert (
        pairs["starter"].sum() == 3 * 2 and pairs["is_home"].all() and pairs["early_season"].all()
    )


# ------------------------------------------------------------------ evaluate + immutability


@pytest.fixture(scope="module")
def big() -> duckdb.DuckDBPyConnection:
    """32 dates x 3 games x 16 players = 1,536 pairs per stat; small gates so every path runs."""
    con = build(n_dates=32, n_games=3, n_players=16, seed=5)
    settle_pending(con, SETTLED_AT)
    return con


def test_gate_refuses_then_descriptive_then_writes_immutable(
    big: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    with pytest.raises(ck.GateNotMet):
        ck.evaluate_checkpoint(big, "L60", out_dir=tmp_path, B=200)  # only 32 dates
    assert not list(tmp_path.glob("*.json"))
    desc = ck.evaluate_checkpoint(big, "L60", out_dir=tmp_path, B=200, descriptive=True)
    assert (tmp_path / "descriptive").is_dir() and desc["descriptive"] is True
    assert not list(tmp_path.glob("L60_*.json"))

    snap = ck.evaluate_checkpoint(
        big, "L30", out_dir=tmp_path, B=300, lineups_db=tmp_path / "none.duckdb"
    )
    res = snap["results"]
    assert set(res) == set(ck.FAMILY) and snap["seed"] == 2026 and snap["B"] == 300
    # minimum-n gates: T30/LT need 60/120 dates -> recorded but cannot be claimed
    assert res["t30_pts"]["eligible"] is False and res["t30_pts"]["outcome"] == "KEEP_SHADOWING"
    assert res["lt_pts"]["outcome"] == "KEEP_SHADOWING"
    # INT: 32 dates >= 14, 1,536 pairs >= 1,500 -> eligible; delta_int == 0, deterministic check ok
    assert res["int_reb"]["eligible"] and res["int_reb"]["guards"]["int_fidelity_ok"] is True
    assert abs(res["int_reb"]["secondary"]["point"]) < 1e-9
    assert res["win"]["outcome"] in {"KEEP", "REVIEW"}
    assert snap["recorded_keys"] and res["win"]["n"] == 96
    # BH list always has 9 entries, ineligible tests at 1.0
    assert len(snap["bh_adjusted"]) == 9 and snap["p_raw_used"]["t30_pts"] == 1.0
    # files: json (commit marker), md, one parquet per recorded arm-stat, log line, hashes
    j = Path(snap["paths"]["json"])
    assert j.exists() and Path(snap["paths"]["md"]).exists()
    disk = json.loads(j.read_text())
    assert disk["git_sha"] and disk["pair_tables_sha256"].keys() == set(snap["recorded_keys"])
    pq = next(tmp_path.glob("L30_*_pairs_int_reb.parquet"))
    assert pl.read_parquet(pq).height == res["int_reb"]["n"]
    assert (tmp_path / "checkpoint_log.jsonl").read_text().count("\n") == 1

    # immutability: everything for this look is recorded -> a second run refuses
    with pytest.raises(ck.GateNotMet):
        ck.evaluate_checkpoint(big, "L30", out_dir=tmp_path, B=200)
    # and an explicit collision on the file name is refused, not overwritten
    before = j.read_text()
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "L30_2099-01-01.json").write_text(json.dumps({"recorded_keys": []}))
    with pytest.raises(FileExistsError):
        ck.evaluate_checkpoint(big, "L30", out_dir=fresh, B=200, asof=date(2099, 1, 1))
    assert j.read_text() == before
    assert json.loads((fresh / "L30_2099-01-01.json").read_text()) == {"recorded_keys": []}


def test_promote_candidate_end_to_end_with_small_gates(
    big: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(ck.MIN_GATE, "t30_pts", (30, 1000))
    monkeypatch.setitem(ck.MIN_GATE, "lt_pts", (30, 1000))
    snap = ck.evaluate_checkpoint(big, "L30", out_dir=tmp_path, B=2000, lineups_db=tmp_path / "x")
    r = snap["results"]["t30_pts"]
    assert r["point"] < -0.005 and r["hi"] < 0 and r["eligible"]
    assert r["guards"]["leak_ok"] and r["guards"]["bias_abs_ok"] is not None
    # logged share is 80% over 96 games -> Wilson lower bound ~0.71: gate may or may not hold,
    # so the outcome must follow the gate rather than the effect size
    share_ok = r["guards"]["logged_share_ok"]
    # direction check: a -0.5 effect is far beyond the backtest upper end -> audit, no promotion
    assert r["outcome"] in {"AUDIT", "KEEP_SHADOWING", "PROMOTE_CANDIDATE"}
    if r["guards"]["direction_suspect"]:
        assert r["outcome"] == "AUDIT"
    elif share_ok:
        assert r["outcome"] in {"PROMOTE_CANDIDATE", "KEEP_SHADOWING"}
    lt = snap["results"]["lt_pts"]
    assert "pit_shares" in lt["guards"]


def _res(**kw: Any) -> dict[str, Any]:
    base = {"point": -0.01, "lo": -0.015, "hi": -0.005, "eligible": True, "n": 9999}
    return {**base, **kw}


def test_decide_rules() -> None:
    g_ok = {
        "leak_ok": True,
        "bias_abs_ok": True,
        "bias_rel_ok": True,
        "coverage_ok": True,
        "slices_ok": True,
        "logged_share_ok": True,
    }
    out, _ = ck.decide("t30_reb", "L60", _res(), 0.01, g_ok)
    assert out == "PROMOTE_CANDIDATE"
    # BH p above alpha_look -> not a candidate
    assert ck.decide("t30_reb", "L60", _res(), 0.03, g_ok)[0] == "KEEP_SHADOWING"
    # CI upper bound not below 0
    assert ck.decide("t30_reb", "L60", _res(hi=0.001), 0.001, g_ok)[0] == "KEEP_SHADOWING"
    # below the floor but significant -> keep shadowing
    assert (
        ck.decide("t30_reb", "L60", _res(point=-0.003, lo=-0.007, hi=-0.001), 0.001, g_ok)[0]
        == "KEEP_SHADOWING"
    )
    # logged-share gate
    assert (
        ck.decide("t30_reb", "L60", _res(), 0.001, {**g_ok, "logged_share_ok": False})[0]
        == "KEEP_SHADOWING"
    )
    # guard failure never promotes
    assert (
        ck.decide("t30_reb", "L60", _res(), 0.001, {**g_ok, "slices_ok": False})[0]
        == "KEEP_SHADOWING"
    )
    # drop: CI lower bound >= floor at L60+, but not at L30; harm at any look
    flat = _res(point=-0.001, lo=-0.004, hi=0.002)
    assert ck.decide("t30_reb", "L60", flat, 0.5, g_ok)[0] == "DROP"
    assert ck.decide("t30_reb", "L30", flat, 0.5, g_ok)[0] == "KEEP_SHADOWING"
    harm = _res(point=0.01, lo=0.004, hi=0.016)
    assert ck.decide("t30_reb", "L30", harm, 0.5, g_ok)[0] == "DROP"
    # not eligible (min-n) -> keep shadowing regardless of the estimate
    assert (
        ck.decide("t30_ast", "L60", _res(eligible=False, ineligible_reason="x"), 1.0, g_ok)[0]
        == "KEEP_SHADOWING"
    )
    # direction check: more negative than the backtest upper end -> audit
    assert (
        ck.decide("t30_reb", "L60", _res(), 0.001, {**g_ok, "direction_suspect": True})[0]
        == "AUDIT"
    )
    # L14 monitoring only
    assert ck.decide("t30_reb", "L14", _res(), 0.001, g_ok)[0] == "MONITOR"
    # win: tripwire only, never a claim
    assert ck.decide("win", "L30", _res(point=-0.02, lo=-0.03, hi=-0.01), 0.0, {})[0] == "KEEP"
    assert ck.decide("win", "L30", _res(point=0.02, lo=0.01, hi=0.03), 0.0, {})[0] == "REVIEW"
    # INT is labelled convention-only
    out, notes = ck.decide("int_reb", "L30", _res(), 0.001, {**g_ok, "int_fidelity_ok": True})
    assert out == "PROMOTE_CANDIDATE" and any("convention-only" in n for n in notes)


def test_t30_share_and_leak_guard(big: duckdb.DuckDBPyConnection) -> None:
    sh = ck.t30_share(big, 2026, 500, 2026)
    assert sh["n_decisions"] == 96 and sh["logged"] == 96 - 20 and sh["skipped"] == 20
    lo, hi = sh["logged_wilson"]
    assert 0.6 < lo < sh["logged_share"] < hi < 0.95
    assert sh["skipped_reasons"] == {"no_confirmed_lineup": 20}
    # leak guard: a snapshot fetched after tip-30 fails leak_ok
    pairs, _ = ck.paired_deltas(big, T30, PROD, "pts")
    bad = pairs.with_columns(snapshot_fetched_at=pl.col("tipoff") - timedelta(minutes=10))
    g = ck._guards("t30_pts", bad, "delta_int", None, 0.9)
    assert g["leak_ok"] is False
    assert ck._guards("t30_pts", pairs, "delta_int", None, 0.9)["leak_ok"] is True


def test_cli_refuses_below_gate_and_auto_is_quiet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "t.duckdb"
    con = build(n_dates=3, n_games=1, n_players=4, con=connect(path))
    settle_pending(con, SETTLED_AT)
    con.close()
    out = tmp_path / "ck"
    rc = main(["--db-path", str(path), "checkpoint", "--look", "L30", "--out-dir", str(out)])
    assert rc == 2 and "REFUSED" in capsys.readouterr().out
    assert not list(out.glob("*.json"))
    assert (
        main(["--db-path", str(path), "checkpoint", "--look", "auto", "--out-dir", str(out)]) == 0
    )
    assert (
        main(
            [
                "--db-path",
                str(path),
                "checkpoint",
                "--look",
                "L30",
                "--descriptive",
                "--out-dir",
                str(out),
            ]
        )
        == 0
    )
    assert list((out / "descriptive").glob("L30_*.json"))
