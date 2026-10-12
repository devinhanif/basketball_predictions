# ruff: noqa: E501
"""Fixture tests for the FULL shadow live scorer (docs/prereg/FULL_SHADOW_LIVE_2026_27.md): in-memory DuckDB, synthetic grids."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

import duckdb
import numpy as np
import pytest
from scipy.stats import norm

from nba.daily.full_shadow.settle import mixture_crps
from nba.daily.full_shadow.store import ensure_tables as ensure_full
from nba.daily.lt_live import integer_crps
from nba.daily.pplay_shadow.store import ensure_tables as ensure_pplay
from nba.daily.store import ensure_tables as ensure_primary
from nba.eval import full_shadow_live as L
from nba.props.context_residual import to_integer_support
from nba.props.forward import QUANTILE_TAUS

TAUS = np.array(QUANTILE_TAUS)
T0 = datetime(2026, 11, 3, 23, 0)


def _grid(mu: float, sd: float = 3.0) -> list[float]:
    return [float(v) for v in norm.ppf(TAUS, mu, sd)]


def _db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    ensure_full(con)
    ensure_pplay(con)
    ensure_primary(con)
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT, home_team INT, away_team INT, home_pts INT, away_pts INT)"
    )
    return con


def build(
    effect: float,
    lt_effect: float = 0.0,
    n_dates: int = 40,
    per_date: int = 5,
    per_game: int = 10,
    seed: int = 0,
    stats: tuple[str, ...] = ("pts", "reb", "ast", "fg3m"),
) -> duckdb.DuckDBPyConnection:
    """FULL grid = prod grid pulled ``effect`` of the way toward the truth-generating mean (0 = identical information); the
    lower-tail arm (``forward_predictions``, pts) is pulled ``lt_effect`` of the way (the mixture's own gain, not FULL's)."""
    rng = np.random.default_rng(seed)
    con = _db()
    shadow, scores = [], []
    pid_base = 1000
    for d in range(n_dates):
        day = date(2026, 11, 3) + timedelta(days=d)
        for k in range(per_date):
            gid = f"002260{d:03d}{k:02d}"
            tip = datetime.combine(day, datetime.min.time()) + timedelta(hours=23)
            con.execute(
                "INSERT INTO games VALUES (?,?,?,?,?,?,?)",
                [gid, day, 2026, 10 + k, 20 + k, 100, 99],
            )
            for j in range(per_game):
                pid = pid_base + k * 20 + j
                team = 10 + k if j < per_game // 2 else 20 + k
                mu = 8.0 + 2.0 * j
                err = rng.normal(0, 2.0)
                played = bool(rng.random() > 0.08)
                y = float(max(0, round(rng.normal(mu, 3.0)))) if played else None
                p_prod = 0.9 if j % 4 else 0.7
                p_r = float(np.clip(1.0 if played else 0.2, 0.01, 0.99)) if j % 5 else None
                for stat in stats:
                    gp = _grid(mu + err)
                    gf = _grid(mu + err * (1 - effect))
                    qf = [float(v) for v in to_integer_support(np.asarray(gf))]
                    made = tip - timedelta(minutes=90)
                    if stat == "pts":
                        con.execute("INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)", [f"r{d}", made, gid, tip, L.LT_MODEL, "v", "pts", pid, json.dumps({"q_grid": _grid(mu + err * (1 - lt_effect))})])  # fmt: skip
                    shadow.append((f"r{d}", made, gid, tip, pid, team, stat, "full_v1", json.dumps(qf), 1.0, 0.1, json.dumps(gp), 1.0, p_prod, True, date(2026, 11, 1), 100, date(2026, 11, 2), "s", f"f{d}{k}{j}"))  # fmt: skip
                    yu = y if y is not None else 0.0
                    cp = integer_crps(gp, y) if played and y is not None else None
                    cf = integer_crps(qf, y) if played and y is not None else None
                    jp = mixture_crps(gp, p_prod, yu)
                    jf = None if p_r is None else mixture_crps(qf, p_r, yu)
                    jr = None if p_r is None else mixture_crps(gp, p_r, yu)
                    scores.append((tip, gid, day, 2026, pid, stat, f"r{d}", made, tip, played, y, cp, cf, p_prod, p_r, yu, jp, jf, jr, True, "scored"))  # fmt: skip
                con.execute("INSERT INTO forward_pplay_shadow (run_id, made_at, game_id, tipoff, player_id, team_id, proj_minutes, p_play_prod) VALUES (?,?,?,?,?,?,?,?)", [f"r{d}", tip - timedelta(minutes=90), gid, tip, pid, team, 30.0 if j >= 4 else 12.0, p_prod])  # fmt: skip
    con.executemany("INSERT INTO forward_full_shadow VALUES (" + ",".join("?" * 20) + ")", shadow)
    con.executemany("INSERT INTO forward_full_scores VALUES (" + ",".join("?" * 21) + ")", scores)
    return con


# --------------------------------------------------------------------------- arithmetic
def test_crps_rows_matches_lt_live() -> None:
    g = [float(v) for v in to_integer_support(np.asarray(_grid(12.0)))]
    for y in (0.0, 7.0, 12.0, 30.0):
        assert L.crps_rows(np.asarray([g], dtype=np.int16), np.asarray([y]))[0] == pytest.approx(
            integer_crps(g, y), abs=1e-12
        )


def test_one_sided_and_fit_inflation() -> None:
    rng = np.random.default_rng(1)
    cl = np.repeat(np.arange(200), 10)
    d = rng.normal(-0.02, 0.5, 2000)
    r = L.one_sided(d, cl, sigma_fit=0.003)
    assert r["delta"] == pytest.approx(d.mean())
    assert r["upper95_fit"] == pytest.approx(r["delta"] + L.Z1 * np.hypot(r["se"], 0.003))
    assert (
        r["upper95_fit"] > r["delta"] + L.Z1 * r["se"] * 0.999
    )  # inflation never shrinks the bound
    assert r["p_less"] < 0.5 < r["p_greater"]
    assert L.one_sided(d, np.zeros(2000))["delta"] is None  # one cluster: refuse


def test_n_required_and_frozen_n_min_cover_the_measured_worst_case() -> None:
    # worst measured pts sd_row 0.3398 and design effect 1.1586 (2023-24, reports/prereg_full_shadow_live/rehearsal.json)
    assert L.n_required(0.3397846949658044, 1.158645515383789) == 14477
    assert L.N_MIN >= 14477 and L.N_INTERIM == L.N_MIN // 2
    assert 16.0 < L.week_reached(L.N_MIN) < 16.4 and 9.0 < L.week_reached(L.N_INTERIM) < 9.2


def test_stale_adjust_placebo_uses_only_the_previous_row_of_the_same_player() -> None:
    pid = np.array([1, 1, 2, 1, 2])
    order = np.array([10, 20, 15, 30, 25])
    qp = np.tile(np.arange(19, dtype=np.int16), (5, 1)) + 5
    qf = qp.copy()
    qf[0] += 3  # player 1, time 10: adjustment +3
    qf[2] -= 2  # player 2, time 15: adjustment -2
    pl = L.stale_adjust_placebo(pid, order, qf, qp)
    assert np.array_equal(pl[0], qp[0])  # first row of player 1: no past, production grid
    assert np.array_equal(pl[2], qp[2])  # first row of player 2
    assert np.array_equal(pl[1], qp[1] + 3)  # player 1, time 20 takes the time-10 adjustment
    assert np.array_equal(
        pl[4], np.maximum.accumulate(np.maximum(qp[4] - 2, 0))
    )  # player 2, time 25
    assert np.array_equal(
        pl[3], qp[3]
    )  # player 1, time 30 takes the time-20 adjustment, which is 0


def test_planted_future_in_the_placebo() -> None:
    """Changing a LATER row of a player must not change an earlier row's placebo."""
    rng = np.random.default_rng(0)
    pid, order = np.repeat(np.arange(5), 6), np.tile(np.arange(6), 5)
    qp = np.sort(rng.integers(0, 40, (30, 19)), axis=1).astype(np.int16)
    qf = np.sort(rng.integers(0, 40, (30, 19)), axis=1).astype(np.int16)
    base = L.stale_adjust_placebo(pid, order, qf, qp)
    qf2 = qf.copy()
    qf2[5] += 9  # last row of player 0
    after = L.stale_adjust_placebo(pid, order, qf2, qp)
    assert np.array_equal(base[:5], after[:5]) and np.array_equal(base[6:], after[6:])


def test_shuffle_moves_grids_within_a_day_only() -> None:
    q = np.tile(np.arange(30, dtype=np.int16)[:, None], (1, 19))
    day = np.repeat(np.arange(3), 10).astype("datetime64[D]")
    s = L.shuffle_grids_within_day(q, day)
    assert not np.array_equal(s, q)
    for dd in range(3):
        assert sorted(s[day == day[dd * 10], 0]) == sorted(q[day == day[dd * 10], 0])


# --------------------------------------------------------------------------- the tables
def test_load_live_picks_latest_pre_cutoff_row_and_excludes_non_regular_games() -> None:
    con = build(0.5, n_dates=2, per_date=2, per_game=3, stats=("pts",))
    row = con.execute("SELECT * FROM forward_full_shadow LIMIT 1").fetchone()
    assert row is not None
    cols = [c[0] for c in con.execute("DESCRIBE forward_full_shadow").fetchall()]
    r = dict(zip(cols, row, strict=True))
    late = dict(
        r, run_id="late", made_at=r["tipoff"] - timedelta(minutes=30)
    )  # after the T-60 cutoff: never eligible
    con.execute(
        "INSERT INTO forward_full_shadow VALUES (" + ",".join("?" * len(cols)) + ")",
        [late[c] for c in cols],
    )
    # a playoff game and a preseason game: excluded
    for gid in ("004260001", "001260001"):
        con.execute(
            "INSERT INTO games VALUES (?,?,?,?,?,?,?)",
            [gid, date(2026, 11, 3), 2026, 1, 2, 100, 99],
        )
        e = dict(r, game_id=gid, player_id=7)
        con.execute(
            "INSERT INTO forward_full_shadow VALUES (" + ",".join("?" * len(cols)) + ")",
            [e[c] for c in cols],
        )
        sc = con.execute("SELECT * FROM forward_full_scores LIMIT 1").fetchone()
        assert sc is not None
        scols = [c[0] for c in con.execute("DESCRIBE forward_full_scores").fetchall()]
        s = dict(zip(scols, sc, strict=True), game_id=gid, player_id=7)
        con.execute(
            "INSERT INTO forward_full_scores VALUES (" + ",".join("?" * len(scols)) + ")",
            [s[c] for c in scols],
        )
    out = L.load_live(con, "pts")
    assert len(out["gid"]) == 2 * 2 * 3 and "late" not in set(out["gid"])
    assert all(g.startswith("002") for g in out["gid"]) and int(out["counts"][1]) == 2
    assert np.isfinite(out["proj"]).all()  # proj_minutes joined from forward_pplay_shadow


def test_integrity_gates_count_mismatch_after_tip_and_late_pairs() -> None:
    con = build(0.5, n_dates=2, per_date=1, per_game=3, stats=("pts",))
    g = L.integrity_gates(con)
    assert (
        g["rows_after_tip"] == 0 and g["late_only_pairs"] == 0 and g["prod_compared"] == 0
    )  # no forward_predictions rows: unverifiable
    rows = con.execute(
        "SELECT run_id, game_id, player_id, q_grid_prod, p_play_prod, made_at, tipoff FROM forward_full_shadow"
    ).fetchall()
    for i, (run, gid, pid, qg, pp, made, tip) in enumerate(rows):
        p = {"p_play": pp if i else pp + 0.1, "q_grid": json.loads(qg)}  # the first row disagrees
        con.execute(
            "INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)",
            [run, made, gid, tip, L.PROD_MODEL, "v", "pts", pid, json.dumps(p)],
        )
    g = L.integrity_gates(con)
    assert g["prod_compared"] == len(rows) and g["prod_mismatch"] == 1
    con.execute(
        "UPDATE forward_full_shadow SET made_at = tipoff + INTERVAL 1 MINUTE WHERE player_id = ? AND game_id = ?",
        [rows[0][2], rows[0][1]],
    )
    g = L.integrity_gates(con)
    assert g["rows_after_tip"] == 1 and g["late_only_pairs"] == 1


def test_score_stat_reproduces_settle_and_the_joint_identity() -> None:
    con = build(0.6)
    res = L.score_live(con)
    for stat in L.STATS:
        s = res["stats"][stat]
        assert s["cond"]["repro_max_abs"] < 1e-9
        assert s["joint"]["identity_max_abs"] < 1e-9
        assert s["cond"]["n"] > 1500 and s["joint"]["n"] > 1500
    assert res["stats"]["pts"]["cond"]["game"]["delta"] < 0
    assert (
        res["stats"]["pts"]["shuffled_full_minus_base"]["lower95"] > 0
    )  # non-inert shuffle: mismatched grids are worse


# --------------------------------------------------------------------------- the decision
def _patched(monkeypatch: pytest.MonkeyPatch, n_min: int = 1500) -> None:
    monkeypatch.setattr(L, "N_MIN", n_min)
    monkeypatch.setattr(L, "N_INTERIM", n_min // 2)
    monkeypatch.setattr(
        L, "NULL_SD_PTS", 0.0005
    )  # the fixture's refit noise is nil; keep the inflation term small but non-zero


def test_planted_gain_passes_at_the_final_look(monkeypatch: pytest.MonkeyPatch) -> None:
    _patched(monkeypatch)
    dec = L.score_live(build(0.7))["decision"]
    assert dec["look"] == "final" and dec["integrity_ok"], dec
    assert dec["verdict"] == "PASS", dec
    assert all(dec["checks"].values())


def test_no_gain_is_not_a_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    _patched(monkeypatch)
    dec = L.score_live(build(0.0))["decision"]
    assert dec["verdict"] in ("FAIL", "INCONCLUSIVE") and not dec["checks"]["floor"]


def test_harm_is_a_kill_recommendation_at_the_interim_look(monkeypatch: pytest.MonkeyPatch) -> None:
    _patched(monkeypatch, n_min=100000)
    monkeypatch.setattr(L, "N_INTERIM", 800)
    dec = L.score_live(build(-0.8))["decision"]
    assert (
        dec["look"] == "interim"
        and dec["kill_recommended"]
        and dec["verdict"] == "KILL_RECOMMENDED"
    )


def test_interim_look_can_never_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    _patched(monkeypatch, n_min=100000)
    monkeypatch.setattr(L, "N_INTERIM", 800)
    dec = L.score_live(build(0.9))["decision"]
    assert (
        dec["look"] == "interim"
        and dec["verdict"].startswith("NO_VERDICT")
        and not dec["kill_recommended"]
    )


def test_integrity_failure_blocks_a_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    _patched(monkeypatch)
    con = build(0.7)
    rows = con.execute(
        "SELECT run_id, game_id, player_id, q_grid_prod, p_play_prod, made_at, tipoff FROM forward_full_shadow WHERE stat = 'pts' LIMIT 3"
    ).fetchall()
    for run, gid, pid, qg, pp, made, tip in rows:
        con.execute(
            "INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)",
            [
                run,
                made,
                gid,
                tip,
                L.PROD_MODEL,
                "v",
                "pts",
                pid,
                json.dumps({"p_play": pp + 0.2, "q_grid": json.loads(qg)}),
            ],
        )
    dec = L.score_live(con)["decision"]
    assert not dec["integrity_ok"] and dec["verdict"] == "NO_VERDICT_INTEGRITY"


def test_secondary_family_is_exactly_the_five_declared_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patched(monkeypatch)
    res: dict[str, Any] = L.score_live(build(0.7))
    assert tuple(res["secondary"]) == L.SECONDARY
    assert all(0 <= v["p_bh"] <= 1 for v in res["secondary"].values())
    assert res["secondary"]["cond_reb"]["verdict"] == "improves"


def test_lower_tail_gain_is_not_credited_to_full(monkeypatch: pytest.MonkeyPatch) -> None:
    """FULL = the lower-tail arm in information: FULL beats production (package) but not the comparator, so no PASS."""
    _patched(monkeypatch)
    res = L.score_live(build(0.7, lt_effect=0.7))
    pts = res["stats"]["pts"]
    assert pts["comparator"] == "lt_arm" and res["stats"]["reb"]["comparator"] == "production"
    assert pts["cond_vs_prod"]["game"]["delta"] < -0.05  # the package gain is large ...
    assert abs(pts["cond"]["game"]["delta"]) < 0.02  # ... and none of it is FULL's over the mixture
    assert (
        res["decision"]["verdict"] in ("FAIL", "INCONCLUSIVE")
        and not res["decision"]["checks"]["floor"]
    )


def test_full_beyond_the_lower_tail_arm_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _patched(monkeypatch)
    res = L.score_live(build(0.8, lt_effect=0.3))
    assert res["decision"]["verdict"] == "PASS", res["decision"]
    assert (
        res["stats"]["pts"]["cond"]["game"]["delta"]
        > res["stats"]["pts"]["cond_vs_prod"]["game"]["delta"]
    )


def test_rows_without_a_lower_tail_row_leave_the_primary_population(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patched(monkeypatch)
    con = build(0.7)
    full = L.score_live(con)["stats"]["pts"]["cond"]["n"]
    con.execute(
        "DELETE FROM forward_predictions WHERE model_name = ? AND player_id % 2 = 0", [L.LT_MODEL]
    )
    pts = L.score_live(con)["stats"]["pts"]
    assert (
        0 < pts["cond"]["n"] < full
        and pts["played_rows_without_comparator"] == full - pts["cond"]["n"]
    )


def test_production_comparison_uses_the_row_in_force_at_the_shadow_time() -> None:
    """Production is write-on-change: a production row made AFTER the shadow row must not be compared with it."""
    con = build(0.5, n_dates=1, per_date=1, per_game=2, stats=("pts",))
    run, gid, pid, qg, pp, made, tip = con.execute(
        "SELECT run_id, game_id, player_id, q_grid_prod, p_play_prod, made_at, tipoff FROM forward_full_shadow LIMIT 1"
    ).fetchone()  # type: ignore[misc]
    con.execute("INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)", ["r0", made - timedelta(minutes=5), gid, tip, L.PROD_MODEL, "v", "pts", pid, json.dumps({"p_play": pp, "q_grid": json.loads(qg)})])  # fmt: skip
    con.execute("INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)", ["r9", made + timedelta(minutes=5), gid, tip, L.PROD_MODEL, "v", "pts", pid, json.dumps({"p_play": pp + 0.3, "q_grid": json.loads(qg)})])  # fmt: skip
    g = L.integrity_gates(con)
    assert g["prod_compared"] == 1 and g["prod_mismatch"] == 0
