# ruff: noqa: E501
"""Fixture tests for the AVG shadow reader (docs/prereg/AVG_SHADOW_LIVE_2.md): in-memory DuckDB, synthetic grids built with the real
mixture code. Planted gain passes; no gain and a mixture-only gain do not; harm kills at the interim; stale, coverage and stamp failures
block; intention-to-treat (a missing AVG row is the FULL grid, never dropped); planted-future placebo; shuffle alive; BH keys;
settle reproduction; no look before its date."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pytest
from scipy.stats import norm

from nba.daily.avg_shadow import grids
from nba.daily.avg_shadow.settle import settle_avg_shadow
from nba.daily.avg_shadow.store import COLUMNS, ensure_tables, set_meta
from nba.daily.full_shadow.store import ensure_tables as ensure_full
from nba.daily.predict import CONTEXT_LT_PROPS_MODEL_NAME, CONTEXT_PROPS_MODEL_NAME
from nba.daily.store import ensure_tables as ensure_primary
from nba.eval import avg_shadow_live as L
from nba.eval import full_shadow_live as FL
from nba.props.context_residual import CRPS_TAUS, to_integer_support
from nba.props.forward import QUANTILE_TAUS

TAUS19 = np.array(QUANTILE_TAUS)
SD = {"pts": 3.0, "reb": 1.5, "ast": 1.5, "fg3m": 1.5}


def _ppf19(mu: np.ndarray, sd: float) -> np.ndarray:
    return norm.ppf(TAUS19[None, :], mu[:, None], sd)


def _int199(mu: np.ndarray, sd: float) -> np.ndarray:
    return to_integer_support(norm.ppf(CRPS_TAUS[None, :], mu[:, None], sd)).astype(np.int16)


def _db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    ensure_full(con)
    ensure_primary(con)
    ensure_tables(con)
    set_meta(
        con, "counting_start", "2026-10-20", datetime(2026, 10, 19)
    )  # the fixture world counts from its first day
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT, home_team INT, away_team INT, home_pts INT, away_pts INT)"
    )
    con.execute(
        "CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT, team_id INT, minutes DOUBLE, pts INT, reb INT, ast INT, fg3m INT, starter BOOLEAN)"
    )
    return con


def build(
    avg_info: float = 1.0,  # 1: AVG mixes two independent-error members; 0: AVG is exactly FULL's information
    s_f: float = 1.5, s_s: float = 1.5, s_a: float = 2.5,
    seq_is_a0: bool = False,  # mixture-only world: the sequence member carries nothing beyond A0
    avg_worse: float = 0.0,  # shift AVG centres away from the truth (harm)
    n_dates: int = 140, per_date: int = 5, per_game: int = 10, seed: int = 0, miss_rate: float = 0.0, stale_rate: float = 0.0,
    after_tip_rows: int = 0, stats: tuple[str, ...] = ("pts", "reb", "ast", "fg3m"),
) -> duckdb.DuckDBPyConnection:  # fmt: skip
    rng = np.random.default_rng(seed)
    con = _db()
    base = date(2026, 10, 20)
    pid0 = 1000
    full_rows, avg_rows, pred_rows, games, box = [], [], [], [], []
    # 4 warm-up days (box rows only) so every player has n_prior >= 3 afterwards
    for d in range(4 + n_dates):
        day = base + timedelta(days=d)
        for k in range(per_date):
            gid = f"002260{d:03d}{k:02d}"
            tip = datetime.combine(day, datetime.min.time()) + timedelta(hours=23)
            home, away = 10 + k, 20 + k
            games.append((gid, day, 2026, home, away, 100, 99))
            n = per_game
            mu = {
                s: (8.0 + 2.0 * np.arange(n)) if s == "pts" else (2.0 + 0.4 * np.arange(n))
                for s in STATS_ALL
            }
            played = rng.random(n) > 0.06
            y = {s: np.maximum(np.round(rng.normal(mu[s], SD[s])), 0) for s in STATS_ALL}
            for j in range(n):
                team = home if j < n // 2 else away
                box.append(
                    (
                        gid,
                        pid0 + k * 20 + j,
                        team,
                        30.0 if played[j] else 0.0,
                        int(y["pts"][j]),
                        int(y["reb"][j]),
                        int(y["ast"][j]),
                        int(y["fg3m"][j]),
                        j % 3 == 0,
                    )
                )
            if d < 4:
                continue
            made = tip - timedelta(minutes=90)
            for stat in stats:
                sd = SD[stat]
                ef, es, ea = (rng.normal(0, s, n) for s in (s_f, s_s, s_a))
                c_full, c_a0 = mu[stat] + ef, mu[stat] + ea
                c_seq = c_a0 if seq_is_a0 else mu[stat] + es
                full_i = _int199(c_full, sd)
                seqc_i = _int199(c_seq, sd)
                a0_c = _ppf19(c_a0, sd)
                avg_i = grids.mix_half(seqc_i, full_i) if avg_info else full_i
                if avg_worse:
                    avg_i = _int199(c_full + avg_worse, sd)
                q_full = grids.q19_of_grid199(full_i)
                q_avg, q_seqc = grids.q19_of_grid199(avg_i), grids.q19_of_grid199(seqc_i)
                for j in range(n):
                    pid = pid0 + k * 20 + j
                    team = home if j < n // 2 else away
                    model = (
                        CONTEXT_LT_PROPS_MODEL_NAME if stat == "pts" else CONTEXT_PROPS_MODEL_NAME
                    )
                    pred_rows.append(
                        (
                            "p",
                            made - timedelta(minutes=5),
                            gid,
                            tip,
                            model,
                            "v",
                            stat,
                            pid,
                            json.dumps({"q_grid": [float(v) for v in a0_c[j]]}),
                        )
                    )
                    full_rows.append(
                        (
                            "p",
                            made - timedelta(minutes=4),
                            gid,
                            tip,
                            pid,
                            team,
                            stat,
                            "full_v1",
                            json.dumps([int(v) for v in q_full[j]]),
                            1.0,
                            0.1,
                            "[]",
                            1.0,
                            0.9,
                            True,
                            date(2026, 10, 1),
                            100,
                            base,
                            "s",
                            "f",
                        )
                    )
                    u_miss, u_stale = rng.random(), rng.random()
                    if u_miss < miss_rate:
                        continue
                    stale = bool(u_stale < stale_rate)
                    row_made = made
                    if after_tip_rows and len(avg_rows) < after_tip_rows:
                        row_made = tip + timedelta(minutes=1)
                    avg_rows.append({
                        "made_at": row_made, "run_id": "a", "game_id": gid, "player_id": pid, "stat": stat, "tipoff_utc": tip, "tip_source": "real",
                        "in_prod_log": True, "n_prior": 5, "model_id": "m", "fit_through": date(2026, 9, 30), "month_block": day.strftime("%Y-%m"),
                        "installed_at": datetime(2026, 10, 1), "gate_id": "g", "gate_fit_through": date(2026, 9, 30), "stale_model": stale,
                        "avg_pmf199": json.dumps([int(v) for v in avg_i[j]]), "seqc_pmf199": json.dumps([int(v) for v in seqc_i[j]]),
                        "avg_q19": json.dumps([int(v) for v in q_avg[j]]), "seqc_q19": json.dumps([int(v) for v in q_seqc[j]]),
                        "seq_q19": json.dumps([float(v) for v in q_seqc[j]]), "full_q19": json.dumps([int(v) for v in q_full[j]]),
                        "a0_q19": json.dumps([float(v) for v in a0_c[j]]), "a0_source": "x",
                    })  # fmt: skip
    con.executemany("INSERT INTO games VALUES (?,?,?,?,?,?,?)", games)
    con.executemany("INSERT INTO player_game_stats VALUES (?,?,?,?,?,?,?,?,?)", box)
    con.executemany("INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)", pred_rows)
    con.executemany(
        "INSERT INTO forward_full_shadow (run_id, made_at, game_id, tipoff, player_id, team_id, stat, model_version, q_grid, mean_full, pi_full, q_grid_prod, mean_prod, p_play_prod, in_prod_log, train_through, n_train, features_through, feature_set_sha, features_sha) VALUES ("
        + ",".join("?" * 20) + ")",
        full_rows,
    )  # fmt: skip
    con.executemany(
        f"INSERT INTO forward_avg_shadow ({', '.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))})",
        [tuple(r[c] for c in COLUMNS) for r in avg_rows],
    )
    return con


STATS_ALL = ("pts", "reb", "ast", "fg3m")


@pytest.fixture(scope="module")
def planted() -> duckdb.DuckDBPyConnection:
    return build(n_dates=165)


def _final(con: duckdb.DuckDBPyConnection, look: str = "final") -> dict[str, Any]:
    return L.score_live(con, look)


# --------------------------------------------------------------------------- the shared bootstrap and arithmetic
def test_the_bootstrap_is_the_full_shadows_one_function_not_a_copy() -> None:
    assert L.cluster_boot is FL.cluster_boot and L.one_sided is FL.one_sided
    src = Path(L.__file__).read_text()
    assert "def cluster_boot" not in src and "def one_sided" not in src


def test_mix_atoms_is_the_quantile_of_the_38_atom_pmf() -> None:
    a = np.array(
        [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18]], dtype=np.int16
    )
    b = a + 100
    q = L.mix_atoms(a, b)[0]
    s = np.sort(np.concatenate([a[0], b[0]]))
    for t, v in zip(TAUS19, q, strict=True):
        assert v == s[int(np.ceil(t * 38 - 1e-9)) - 1]
    assert (q[:9] < 100).all() and (q[10:] >= 100).all()


# --------------------------------------------------------------------------- verdict logic
def test_planted_gain_passes_and_every_condition_is_reported(
    planted: duckdb.DuckDBPyConnection,
) -> None:
    res = _final(planted)
    dec = res["decision"]
    assert res["stats"]["pts"]["n"] >= L.N_MIN["pts"]
    assert dec["verdict"] == "PASS", dec
    assert all(dec["checks"].values())
    assert res["stats"]["pts"]["avg_minus_full"]["game"]["delta"] < L.FLOOR
    assert (
        res["coverage"]["ok"] and res["gates"]["stamp_ok"] and res["gates"]["mismatch_ok"] is True
    )
    assert set(res["secondary"]) <= set(L.SECONDARY) and len(res["secondary"]) == 7
    ps = np.array([v["p_less"] for v in res["secondary"].values()])
    pb = np.array([v["p_bh"] for v in res["secondary"].values()])
    assert (pb >= ps - 1e-12).all() and (
        pb <= 1.0
    ).all()  # BH adjusted p-values never fall below the raw ones


def test_shuffle_is_alive_and_placebo_is_worse_than_the_real_row(
    planted: duckdb.DuckDBPyConnection,
) -> None:
    st = _final(planted)["stats"]["pts"]
    assert (
        st["shuffled_minus_full"]["lower95"] > 0
    )  # the scorer is not inert: shuffled grids lose to FULL
    assert (
        st["avg_minus_placebo"]["upper95"] < 0
        and st["avg_minus_placebo"]["delta"] < L.PLACEBO_MARGIN
    )


def test_no_gain_is_not_a_pass() -> None:
    con = build(avg_info=0, n_dates=40)
    dec = _final(con)["decision"]
    assert dec["verdict"] in ("FAIL", "INCONCLUSIVE") and dec["verdict"] != "PASS"
    assert dec["checks"]["a_floor"] is False


def test_mixture_only_gain_is_not_a_pass() -> None:
    """SEQ carries only A0's information: AVG beats FULL (the mixture helps) but not the AVG_A0 control, and not A0."""
    con = build(seq_is_a0=True, s_a=1.0, s_f=2.5, n_dates=40)
    res = _final(con)
    assert res["stats"]["pts"]["avg_minus_full"]["game"]["delta"] < 0  # a gain over FULL exists
    ch = res["decision"]["checks"]
    assert res["decision"]["verdict"] != "PASS" and not (ch["f_vs_avg_a0"] and ch["c_vs_a0"])


def test_harm_kills_at_the_interim_and_the_interim_can_never_declare_success() -> None:
    con = build(avg_worse=1.2, n_dates=40, per_date=10)
    res = L.score_live(con, "interim")
    assert res["stats"]["pts"]["n"] >= L.N_INTERIM
    assert res["decision"]["verdict"] == "KILL" and res["decision"]["kill_statistical"]
    ok = L.score_live(build(n_dates=40, per_date=10), "interim")["decision"]
    assert ok["verdict"] == "NO_KILL" and "PASS" not in json.dumps(ok)


# --------------------------------------------------------------------------- integrity blocks the verdict
def test_stale_share_over_ten_percent_is_inconclusive() -> None:
    con = build(stale_rate=0.15, n_dates=40)
    res = _final(con)
    assert res["gates"]["stale_ok"] is False and res["decision"]["verdict"] == "INCONCLUSIVE"
    # a stale row is excluded from the verdict population but counted as eligible
    assert res["stats"]["pts"]["n"] < res["stats"]["pts"]["n_eligible"]


def test_coverage_below_the_cap_is_inconclusive_and_missing_rows_stay_in_the_population() -> None:
    con = build(miss_rate=0.2, n_dates=40)
    res = _final(con)
    assert res["coverage"]["ok"] is False and res["decision"]["verdict"] == "INCONCLUSIVE"
    full = build(n_dates=40)
    st_full, st_miss = _final(full)["stats"]["pts"], res["stats"]["pts"]
    assert (
        st_miss["n"] == st_full["n"]
    )  # intention to treat: nothing is dropped for a missing AVG row
    d_full = st_full["avg_minus_full"]["game"]["delta"]
    d_miss = st_miss["avg_minus_full"]["game"]["delta"]
    assert (
        d_full < d_miss < 0 and abs(d_miss / d_full - 0.8) < 0.1
    )  # a missing row is delta 0: the effect dilutes by the miss rate
    assert (
        st_miss["per_protocol_avg_minus_full"]["n"] < st_miss["n"]
    )  # descriptive per-protocol figure exists


def test_a_row_after_tip_is_a_stamp_violation_and_an_interim_kill() -> None:
    con = build(n_dates=40, per_date=10, after_tip_rows=3)
    res = L.score_live(con, "interim")
    assert res["integrity_raw"]["rows_after_tip"] == 3 and res["gates"]["stamp_ok"] is False
    assert res["decision"]["verdict"] == "KILL" and res["decision"]["kill_integrity"]
    # such a triple is scored as the FULL grid (late_only), never dropped
    rows = L.load_live(con, "pts")
    assert rows["late_only"].sum() >= 1 and (L.population(rows)["n_late_only"]) >= 1


def test_a0_mismatch_is_reported() -> None:
    con = build(n_dates=6, per_date=2)
    assert L.integrity_gates(con)["a0_mismatch"] == 0
    con.execute(
        "UPDATE forward_predictions SET prediction = '{\"q_grid\": [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19]}' WHERE rowid = 0"
    )
    assert L.integrity_gates(con)["a0_mismatch"] >= 1


def test_the_stored_grid_is_reproducible_from_the_stored_atoms() -> None:
    g = L.integrity_gates(build(n_dates=6, per_date=2))
    assert (
        g["grid_repro_exact_fail"] == 0
        and g["grid_repro_atoms_share"] >= 0.95
        and g["invalid_rows"] == 0
    )


# --------------------------------------------------------------------------- settle reproduction, missingness, calendar
def test_settle_scores_are_reproduced_from_the_grids_to_1e_9() -> None:
    con = build(n_dates=8, per_date=2)
    assert settle_avg_shadow(con, datetime(2027, 1, 1))["avg_scored"] > 0
    rows = L.load_live(con, "pts")
    m = L.population(rows)["eligible"] & rows["has_avg"]
    mine = FL.crps_rows(rows["qavg"][m], rows["y"][m])
    theirs = {
        (g, int(p)): c
        for g, p, c in con.execute(
            "SELECT game_id, player_id, crps_avg FROM forward_avg_scores WHERE stat = 'pts' AND played"
        ).fetchall()
    }
    got = np.array(
        [theirs[(g, int(p))] for g, p in zip(rows["gid"][m], rows["pid"][m], strict=True)]
    )
    assert np.max(np.abs(got - mine)) < 1e-9


def test_missingness_table_flags_gaps_over_two_points() -> None:
    con = build(n_dates=12, per_date=3, miss_rate=0.0)
    # delete the AVG rows of every starter: a planted gap between starters and bench
    con.execute("DELETE FROM forward_avg_shadow WHERE player_id % 20 IN (0, 3, 6, 9)")
    rows = L.load_live(con, "pts")
    t = L.missingness_table(rows)
    assert t["starter"]["flag"] is True and t["starter"]["gap_pp"] > L.MISSING_GAP_PP
    assert set(t) == {"played", "minutes>=5", "starter", "team_win", "pts_above_median"}


def test_no_look_is_computed_before_its_date() -> None:
    con = build(n_dates=30, per_date=20)
    # a Wednesday, and the Sunday before the interim date: nothing is read or computed
    for d in (date(2026, 11, 11), date(2026, 11, 8)):
        with pytest.raises(L.LookNotDue):
            L.read_look(con, d, date(2027, 4, 11))
    # the interim Sunday with enough rows computes the kill-only look, and nothing else
    res = L.read_look(con, date(2026, 11, 15), date(2027, 4, 11))
    assert res["look"] == "interim" and res["decision"]["verdict"] in ("KILL", "NO_KILL")
    # before the final date a Sunday below n_min is not a final look
    assert (
        L.look_due(date(2027, 1, 3), {date(2027, 1, 3): 9999}, date(2027, 4, 11), ["interim"])
        is None
    )
    assert (
        L.look_due(date(2027, 1, 10), {date(2027, 1, 10): 6899}, date(2027, 4, 11), ["interim"])
        is None
    )  # n binds
    assert (
        L.look_due(date(2027, 1, 10), {date(2027, 1, 10): 6900}, date(2027, 4, 11), ["interim"])
        == "final"
    )
    assert (
        L.look_due(date(2027, 4, 11), {date(2027, 4, 11): 100}, date(2027, 4, 11), ["interim"])
        == "final"
    )  # end-of-season look, no second look beyond
    assert (
        L.look_due(
            date(2027, 4, 11), {}, date(2027, 4, 11), ["interim", "final", "final_inconclusive"]
        )
        == "second"
    )
    assert (
        L.look_due(
            date(2027, 4, 11),
            {},
            date(2027, 4, 11),
            ["interim", "final", "final_inconclusive", "second"],
        )
        is None
    )
    assert L.look_due(date(2026, 11, 15), {date(2026, 11, 15): 3449}, date(2027, 4, 11), []) is None
    assert (
        L.look_due(date(2026, 11, 22), {date(2026, 11, 22): 3450}, date(2027, 4, 11), [])
        == "interim"
    )  # first Sunday after reaching it


def test_daily_display_has_counts_and_gates_but_no_delta() -> None:
    out = L.daily_display(build(n_dates=6, per_date=2))
    text = json.dumps(out)
    assert (
        "delta" not in text
        and "crps" not in text
        and out["pts"]["n_eligible"] > 0
        and out["gates"]["rows_after_tip"] == 0
    )


# --------------------------------------------------------------------------- G1 on avg_pmf199 (AVG_SHADOW_LIVE_2)
def _shift_stored(con: duckdb.DuckDBPyConnection, column: str, by: int) -> None:
    """Shift one stored JSON grid of every pts AVG row by ``by`` and leave every other column alone."""
    rows = con.execute(
        f"SELECT run_id, game_id, player_id, CAST({column} AS VARCHAR) FROM forward_avg_shadow WHERE stat = 'pts'"
    ).fetchall()
    con.executemany(
        f"UPDATE forward_avg_shadow SET {column} = ? WHERE run_id = ? AND game_id = ? AND player_id = ? AND stat = 'pts'",
        [(json.dumps([int(v) + by for v in json.loads(g)]), r, gm, p) for r, gm, p, g in rows],
    )


def _g1(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    return dict(_final(con)["stats"]["pts"]["guards"]["G1_calibration"])


def test_g1_reads_the_stored_199_point_pmf_not_the_19_atoms() -> None:
    base = _g1(build(n_dates=60))
    assert base["form"] == "avg_pmf199" and base["share_without_avg_row"] == 0.0
    # a miscalibration planted on avg_pmf199 only (avg_q19, the scored object, untouched) fails G1: a 19-atom reading would still pass
    bad = build(n_dates=60)
    _shift_stored(bad, "avg_pmf199", 3)
    got = _g1(bad)
    assert got["ok"] is False and got["bias"] < -2.0
    assert got["atom19_descriptive"]["bias"] == pytest.approx(base["atom19_descriptive"]["bias"])
    assert got["atom19_descriptive"]["guard"] == base["atom19_descriptive"]["guard"]


def test_a_19_atom_only_shift_does_not_move_g1() -> None:
    base = _g1(build(n_dates=60))
    con = build(n_dates=60)
    _shift_stored(
        con, "avg_q19", 3
    )  # the stored pmf is calibrated; only the 19 scored quantiles move
    got = _g1(con)
    assert got["bias"] == pytest.approx(base["bias"]) and got["cov80"] == pytest.approx(
        base["cov80"]
    )
    assert got["guard"] == base["guard"] and got["ok"] == base["ok"]
    assert got["atom19_descriptive"]["bias"] < base["atom19_descriptive"]["bias"] - 2.0
    assert (
        got["atom19_descriptive"]["ok"] is False
    )  # the descriptive 19-atom reading does see it, and decides nothing


def test_g1_pmf199_for_an_itt_triple_without_an_avg_row_is_the_full_reconstruction() -> None:
    con = build(n_dates=40, miss_rate=0.3)
    rows = L.load_live(con, "pts")
    m = np.ones(len(rows["gid"]), dtype=bool)
    pmf = L.g1_pmf199(rows, m)
    assert pmf.shape == (len(m), 199)
    ha = rows["has_avg"]
    assert 0.1 < (~ha).mean() < 0.5
    want = to_integer_support(grids.ext19(rows["qf"][~ha]))
    assert (pmf[~ha] == want).all()
    assert (pmf[ha] == rows["pavg"][ha]).all()
    assert _g1(con)["share_without_avg_row"] == pytest.approx(
        float((~ha[L.population(rows)["verdict"]]).mean()), abs=1e-9
    )
