# ruff: noqa: E501
"""COMBO_STOCKS reader (nba/eval/combo_shadow_live.py): look calendar, refusal before a look, the frozen verdict logic on crafted inputs, the
loader on a synthetic season, servability and coverage, and daily display (counts and gates only)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

import duckdb
import numpy as np
import polars as pl
import pytest
from scipy import stats

from nba.daily.combo_shadow import store
from nba.daily.combo_shadow.config import COMBO_TARGETS, TARGETS, load_config
from nba.eval import combo_shadow_live as L
from nba.ingest.cache import insert_rows
from nba.props.combo_copula import q19_of_grid199
from nba.props.context_residual import CRPS_TAUS
from tests.daily.conftest import STAT_COLS
from tests.fixtures.loader import build_fixture_db

CFG = load_config()


# --------------------------------------------------------------------------- the calendar
def _ns(by_date: dict[date, int]) -> dict[str, dict[date, int]]:
    return {t: dict(by_date) for t in TARGETS}


def test_interim_needs_every_target_at_1500_rows_and_a_sunday() -> None:
    sun = date(2026, 11, 15)
    assert sun.weekday() == 6
    assert set(L.look_due(sun, _ns({sun: 1500}), date(2027, 4, 11)).values()) == {"interim"}
    short = _ns({sun: 1500})
    short["blk"][sun] = 1499
    assert set(L.look_due(sun, short, date(2027, 4, 11)).values()) == {None}
    assert set(
        L.look_due(date(2026, 11, 14), _ns({date(2026, 11, 14): 9999}), date(2027, 4, 11)).values()
    ) == {None}  # a Saturday
    assert set(
        L.look_due(date(2026, 11, 8), _ns({date(2026, 11, 8): 9999}), date(2027, 4, 11)).values()
    ) == {None}  # before the date
    later = date(2026, 11, 22)
    assert set(L.look_due(later, _ns({later: 1500}), date(2027, 4, 11)).values()) == {
        "interim"
    }  # the first Sunday after it is true


def test_final_is_per_target_and_waits_for_3000_rows() -> None:
    fin = date(2027, 1, 10)
    assert fin.weekday() == 6
    ns = _ns({fin: 3000})
    ns["stl"][fin] = 2999
    due = L.look_due(fin, ns, date(2027, 4, 11), {t: ["interim"] for t in TARGETS})
    assert due["stl"] is None and due["blk"] == "final"
    nxt = fin + timedelta(days=7)
    assert (
        L.look_due(nxt, {"stl": {nxt: 3000}}, date(2027, 4, 11), {t: ["interim"] for t in TARGETS})[
            "stl"
        ]
        == "final"
    )
    end = date(
        2027, 4, 11
    )  # the last regular-season Sunday: the final look happens whatever the count is
    assert (
        L.look_due(end, {"stl": {end: 10}}, end, {t: ["interim"] for t in TARGETS})["stl"]
        == "final"
    )
    assert (
        L.look_due(end, {}, end, {"blk": ["interim", "final", "final_inconclusive"]})["blk"]
        == "second"
    )
    assert (
        L.look_due(end, {}, end, {"blk": ["interim", "final", "final_inconclusive", "second"]})[
            "blk"
        ]
        is None
    )


# --------------------------------------------------------------------------- the verdict logic
def _res(
    target: str = "pra",
    delta: float = -0.30,
    floor: float = 0.0438,
    half: float = 0.02,
    n: int = 5000,
    g1: bool = True,
    slices: bool = True,
    late: bool = True,
    indep: float | None = -0.03,
) -> dict[str, Any]:
    def one(d: float, h: float) -> dict[str, Any]:
        return {
            "delta": d,
            "se": h / 1.6448536,
            "upper95": d + h,
            "lower95": d - h,
            "half_width_one_sided": h,
            "p_less": 0.001,
            "n": n,
        }

    out = {
        "target": target,
        "n": n,
        "floor": floor,
        "game": one(delta, half),
        "date": one(delta, half),
        "g1": {"ok": g1},
        "slice_guard": {"ok": slices},
        "later_half": {"ok": late},
    }
    if indep is not None:
        out["s_minus_indep"] = one(indep, 0.01)
    return out


def test_pass_needs_every_condition() -> None:
    assert L.decide_target(_res(), 0.01, True, "final")["verdict"] == "PASS"
    for kw in (
        {"g1": False},
        {"slices": False},
        {"late": False},
        {"delta": -0.02},
        {"indep": 0.001},
        {"indep": None},
    ):
        v = L.decide_target(_res(**kw), 0.01, True, "final")
        assert v["verdict"] != "PASS", kw
    assert L.decide_target(_res(), 0.20, True, "final")["verdict"] != "PASS"  # BH
    assert L.decide_target(_res(), 0.01, False, "final")["verdict"] != "PASS"  # integrity
    assert (
        L.decide_target(_res(half=0.2, delta=-0.30), 0.01, True, "final")["conditions"][
            "b_upper_game_and_date"
        ]
        is True
    )
    bad_date = _res()
    bad_date["date"]["upper95"] = 0.001
    assert (
        L.decide_target(bad_date, 0.01, True, "final")["conditions"]["b_upper_game_and_date"]
        is False
    )  # BOTH clusterings


def test_bs_does_not_need_the_indep_contrast() -> None:
    assert (
        L.decide_target(_res("bs", floor=0.0061, delta=-0.03, indep=None), 0.01, True, "final")[
            "verdict"
        ]
        == "PASS"
    )
    assert (
        "g_indep"
        not in L.decide_target(
            _res("blk", floor=0.005, delta=-0.02, indep=None), 0.01, True, "final"
        )["conditions"]
    )
    assert "g_indep" in L.decide_target(_res("pa", indep=-0.01), 0.01, True, "final")["conditions"]


def test_fail_inconclusive_and_below_n_min() -> None:
    fail = L.decide_target(
        _res(delta=-0.01, half=0.01), 0.01, True, "final"
    )  # not past the floor, but known to within the floor
    assert fail["verdict"] == "FAIL"
    inc = L.decide_target(_res(delta=-0.01, half=0.2), 0.5, True, "final")
    assert inc["verdict"] == "INCONCLUSIVE"
    small = L.decide_target(_res(n=2999), 0.01, True, "final")
    assert small["verdict"] == "INCONCLUSIVE" and "n_min" in small["reason"]
    assert L.decide_target({"target": "blk", "n": 0}, None, True, "final")["verdict"] == "NO_DATA"


def test_interim_can_only_kill() -> None:
    ok = L.decide_target(_res(delta=-0.5), 0.0001, True, "interim")
    assert ok["verdict"] == "CONTINUE"  # a win at the interim look is never declared
    worse = _res(delta=0.2)
    worse["game"]["lower95"] = 0.05
    assert L.decide_target(worse, 0.9, True, "interim")["verdict"] == "KILL"
    noisy = _res(delta=0.2)
    noisy["game"]["lower95"] = -0.05
    assert L.decide_target(noisy, 0.9, True, "interim")["verdict"] == "CONTINUE"
    # review S3: gates that only block a PASS (coverage 80-95%, staleness) never kill; a hard gate or coverage below 80% does
    assert L.decide_target(_res(delta=-0.5), 0.01, False, "interim")["verdict"] == "CONTINUE"
    killed = L.decide_target(_res(delta=-0.5), 0.01, False, "interim", kill_gate_ok=False)
    assert killed["verdict"] == "KILL" and killed["integrity_kill"] is True


def test_second_look_uses_z_196() -> None:
    res = _res(
        delta=-0.04, half=0.0443
    )  # upper95 = +0.0043 > 0: fails (b) either way; check the z arithmetic itself
    assert L._upper(res["game"], L.Z1) == pytest.approx(0.0043)
    assert L._upper(res["game"], L.Z2) == pytest.approx(-0.04 + 1.959964 * (0.0443 / 1.6448536))
    assert L._half(res["game"], L.Z2) == pytest.approx(1.959964 * 0.0443 / 1.6448536)


# --------------------------------------------------------------------------- scoring on synthetic arrays
def _rows(n: int = 400, better: float = 0.0, seed: int = 1) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    lam = rng.uniform(1.0, 6.0, n)
    y = rng.poisson(lam).astype(float)
    s = np.stack(
        [
            stats.poisson.ppf(CRPS_TAUS, mu + better * (yy - mu))
            for mu, yy in zip(lam, y, strict=True)
        ]
    ).astype(np.int16)  # better > 0 leaks the label: S beats RECENT by construction
    rec = np.stack([stats.poisson.ppf(CRPS_TAUS, 3.5) for _ in lam]).astype(np.int16)
    day = (np.datetime64("2026-11-01") + rng.integers(0, 40, n)).astype("datetime64[D]")
    return {
        "gid": np.array([f"002260{i // 8:04d}" for i in range(n)]), "day": day, "tip": day.astype("datetime64[s]"), "pid": np.arange(n, dtype=np.int64),
        "team": np.zeros(n, np.int64), "s": s, "s_q19": q19_of_grid199(s).astype(np.int16), "recent": rec, "indep": s.copy(), "has_indep": np.ones(n, bool),
        "fit_through": day - np.timedelta64(1, "D"), "n_prior": np.full(n, 20.0), "n_late": np.zeros(n), "n_prior_box": np.full(n, 20.0), "played": np.ones(n, bool), "y": y,
        "minutes": rng.uniform(5, 38, n), "won": rng.random(n) > 0.5, "b2b": rng.random(n) > 0.8, "tgn": rng.integers(0, 60, n).astype(float),
        "starter10": rng.random(n), "tm_out": rng.random(n) > 0.7,
    }  # fmt: skip


def test_score_target_sign_slices_and_g1_shape() -> None:
    good = L.score_target(_rows(better=0.6), "pra", 0.04)
    bad = L.score_target(_rows(better=0.0), "pra", 0.04)
    assert good["game"]["delta"] < bad["game"]["delta"] and good["game"]["delta"] < 0
    assert set(good["slice_guard"]["slices"]) == {
        "starter",
        "bench",
        "min<10",
        "min10-20",
        "min>20",
        "teammate_out",
        "second_night",
        "cold_start",
        "first15",
    }
    assert (
        good["g1"]["n"] == good["n"] and good["g1"]["enough_rows"] is False
    )  # 400 < 3,000: G1 cannot pass
    assert good["later_half"]["n"] == good["n"] - good["n"] // 2
    assert good["s_minus_indep"]["delta"] == pytest.approx(0.0)
    assert good["n_games"] > 1 and good["n_dates"] > 1


def test_unplayed_and_cold_rows_are_not_scored() -> None:
    r = _rows(100)
    r["played"][:10] = False
    r["n_prior"][10:20] = 4
    assert L.eligible(r).sum() == 80
    r["y"][20] = np.nan
    assert L.eligible(r).sum() == 79
    assert L.score_target(r, "pra", 0.04)["n"] == 79


def test_slice_masks_follow_the_rule_text() -> None:
    r = _rows(200)
    m = np.ones(200, bool)
    sl = L.slice_masks(r, m)
    assert (sl["starter"] == (r["starter10"] >= 0.5)).all() and (
        sl["bench"] == ~sl["starter"]
    ).all()
    mn = r["minutes"]
    assert (
        (sl["min<10"] == (mn < 10)).all()
        and (sl["min10-20"] == ((mn >= 10) & (mn <= 20))).all()
        and (sl["min>20"] == (mn > 20)).all()
    )
    assert (sl["cold_start"] == (r["n_prior"] < 10)).all() and (
        sl["first15"] == (r["tgn"] < 15)
    ).all()


# --------------------------------------------------------------------------- the loader on a synthetic season
TEAMS = (1610612738, 1610612755)
START = datetime(2026, 10, 21, 23, 30)


def _build(
    n_days: int = 9,
    drop_shadow: set[tuple[str, int]] | None = None,
    seed_prod: bool = True,
    counting: str | None = "2026-10-21",
) -> duckdb.DuckDBPyConnection:
    con = build_fixture_db(":memory:")
    store.ensure_tables(con)
    if counting:
        store.set_meta(con, "counting_start", counting, datetime(2026, 10, 19))
    rng = np.random.default_rng(3)
    games, box, shadow, prod = [], [], [], []
    for d in range(n_days):
        gid = f"00226{d:05d}"
        day = START.date() + timedelta(days=d)
        tip = START + timedelta(days=d)
        games.append(
            {
                "game_id": gid,
                "game_date": day,
                "season": 2026,
                "home_team": TEAMS[0],
                "away_team": TEAMS[1],
                "home_pts": 100 + d,
                "away_pts": 99,
            }
        )
        for t_i, team in enumerate(TEAMS):
            for j in range(8):
                pid = 7000 + 100 * t_i + j
                mins = 0.0 if (j == 7 and d % 2) else 34.0 - 3 * j
                row = {"game_id": gid, "player_id": pid, "team_id": team, "minutes": mins, "pts": int(rng.poisson(0.4 * mins)), "reb": int(rng.poisson(0.12 * mins)),
                       "ast": int(rng.poisson(0.08 * mins)), "fg3m": 1, "stl": int(rng.poisson(0.03 * mins)), "blk": int(rng.poisson(0.02 * mins)), "tov": 1,
                       "starter": j < 5, "fgm": 3, "fga": 8, "fg3a": 3, "ftm": 1, "fta": 2, "oreb": 1, "dreb": 3, "pf": 2}  # fmt: skip
                box.append(row)
                if seed_prod:
                    for stat, model in (
                        ("pts", "props_context_residual_lt"),
                        ("reb", "props_context_residual"),
                        ("ast", "props_context_residual"),
                        ("fg3m", "props_context_residual"),
                    ):
                        prod.append(
                            ("p", tip - timedelta(hours=3), gid, tip, model, "v1", stat, pid, "{}")
                        )
                for target in TARGETS:
                    if drop_shadow and (gid, pid) in drop_shadow:
                        continue
                    lam = {
                        "blk": 0.5,
                        "stl": 0.9,
                        "pra": 20.0,
                        "pr": 15.0,
                        "pa": 13.0,
                        "ra": 6.0,
                        "bs": 1.4,
                    }[target]
                    s = stats.poisson.ppf(CRPS_TAUS, lam).astype(int)
                    shadow.append({
                        "made_at": tip - timedelta(hours=3), "run_id": "r", "game_id": gid, "player_id": pid, "target": target, "tipoff_utc": tip, "tip_source": "real",
                        "in_prod_log": True, "n_prior": d, "model_id": "m", "fit_through": day - timedelta(days=1), "r_id": CFG.r_id, "base_id": CFG.base_id,
                        "s_pmf199": store.dumps_ints(s), "s_q19": store.dumps_ints(q19_of_grid199(s[None, :])[0]), "indep_pmf199": store.dumps_ints(s) if target in COMBO_TARGETS else None,
                        "recent_pmf199": store.dumps_ints(s), "direct_pmf199": None, "recent_cal_pmf199": None, "src": "{}",
                    })  # fmt: skip
    g = pl.DataFrame(games)
    insert_rows(con, "games", g.columns, g)
    insert_rows(con, "player_game_stats", STAT_COLS, pl.DataFrame(box).select(STAT_COLS))
    if prod:
        con.executemany("INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)", prod)
    for tip in sorted({r["tipoff_utc"] for r in shadow}):
        store.append_rows(
            con, [r for r in shadow if r["tipoff_utc"] == tip], tip - timedelta(hours=3)
        )
    return con


def test_load_live_order_labels_and_eligibility() -> None:
    con = _build()
    rows = L.load_live(con, "pra")
    n = len(rows["pid"])
    assert n == 9 * 16
    order = np.lexsort((rows["pid"], rows["gid"], rows["day"]))
    assert (order == np.arange(n)).all()  # the fixed (game_date, game_id, player_id) order
    box = con.execute(
        "SELECT game_id, player_id, pts + reb + ast FROM player_game_stats"
    ).fetchall()
    want = {(g, p): float(v) for g, p, v in box}
    assert all(rows["y"][i] == want[(rows["gid"][i], int(rows["pid"][i]))] for i in range(n))
    el = L.eligible(rows)
    assert (
        el.sum() < n and not el[rows["n_prior"] < 5].any()
    )  # n_prior < 5 never scored; DNP never scored
    assert not el[~rows["played"]].any()
    assert (
        L.load_live(con, "blk")["y"][0]
        == con.execute(
            "SELECT blk FROM player_game_stats ORDER BY game_id, player_id LIMIT 1"
        ).fetchone()[0]
    )  # type: ignore[index]
    assert rows["starter10"].shape == (n,) and rows["has_indep"].all()
    assert not L.load_live(con, "blk")["has_indep"].any()


def test_a_late_row_is_excluded_and_counted() -> None:
    con = _build(n_days=2)
    tip = START
    row = con.execute("SELECT run_id FROM forward_combo_shadow LIMIT 1").fetchone()
    assert row is not None
    con.execute(
        "INSERT INTO forward_combo_shadow SELECT ?, 'late', game_id, player_id, target, tipoff_utc, tip_source, in_prod_log, n_prior, model_id, fit_through, r_id, base_id, "
        "'[0]', '[0]', indep_pmf199, recent_pmf199, NULL, NULL, src FROM forward_combo_shadow WHERE game_id = '0022600000' AND player_id = 7000 AND target = 'pra'",
        [tip - timedelta(minutes=30)],
    )
    rows = L.load_live(con, "pra")
    i = int(np.flatnonzero((rows["gid"] == "0022600000") & (rows["pid"] == 7000))[0])
    assert (
        rows["n_late"][i] == 1 and len(rows["s"][i]) == 199 and rows["s"][i].max() > 0
    )  # the 30-minute row did not replace the on-time one
    assert L.integrity_gates(con, {"pra": rows}, CFG)["late_rows"] == 1


def test_servability_and_coverage_and_missingness() -> None:
    con = _build(n_days=9, drop_shadow={("0022600007", 7000), ("0022600008", 7001)})
    serv = L.servability(con)
    assert (
        serv["played"] > 0
        and serv["servable"] == serv["rostered"] == serv["played"]
        and serv["flag"] is False
    )
    rows = {t: L.load_live(con, t) for t in TARGETS}
    cov = L.coverage_and_missingness(con, serv, rows)
    assert set(cov["by_target"]) == set(TARGETS)
    pra = cov["by_target"]["pra"]
    assert pra["overall"] == pytest.approx(1 - 2 / pra["n_servable"]) and pra["ok"] is True
    assert "starter" in cov["missingness"] and "minutes<5" in cov["missingness"]
    # no production rows at all: nobody is servable; the count is reported, not imputed
    none = _build(n_days=9, seed_prod=False)
    s2 = L.servability(none)
    assert s2["servable"] == 0 and s2["rostered"] == 0


def test_not_servable_share_is_flagged_above_five_percent() -> None:
    con = _build(n_days=9)
    con.execute(
        "DELETE FROM forward_predictions WHERE target = 'fg3m' AND player_id IN (7000, 7001, 7002)"
    )
    s = L.servability(con)
    assert (
        s["not_servable_share"] is not None and s["not_servable_share"] > 0.05 and s["flag"] is True
    )


def test_integrity_gates_see_a_row_after_tip_and_a_wrong_r_id() -> None:
    con = _build(n_days=3)
    rows = {t: L.load_live(con, t) for t in TARGETS}
    g = L.integrity_gates(con, rows, CFG)
    assert (
        g["rows_after_tip"] == 0
        and g["repro_q19_fail"] == 0
        and g["id_mismatch"] == 0
        and g["stale_ok"] is True
    )
    con.execute(
        "UPDATE forward_combo_shadow SET r_id = 'x' WHERE game_id = '0022600000' AND player_id = 7000 AND target = 'blk'"
    )
    con.execute(
        "UPDATE forward_combo_shadow SET made_at = tipoff_utc WHERE game_id = '0022600001' AND player_id = 7001 AND target = 'blk'"
    )
    g = L.integrity_gates(con, rows, CFG)
    assert g["id_mismatch"] == 1 and g["rows_after_tip"] == 1
    rows["pra"]["s_q19"][0, 0] += 1
    assert L.integrity_gates(con, rows, CFG)["repro_q19_fail"] == 1


def test_score_live_runs_end_to_end_and_n_min_blocks_a_verdict() -> None:
    con = _build()
    res = L.score_live(con, "final", cfg=CFG)
    assert set(res["decisions"]) == set(TARGETS)
    assert all(
        d["verdict"] == "INCONCLUSIVE" and "n_min" in d["reason"] for d in res["decisions"].values()
    )
    assert set(res["bh_adjusted_p"]) == set(TARGETS) and res["servability"]["servable"] > 0
    assert res["integrity"]["rows_after_tip"] == 0


def test_daily_display_has_counts_and_gates_but_no_delta() -> None:
    con = _build()
    d = L.daily_display(con)
    assert set(d) == {
        "counting_start",
        "note",
        "n_eligible",
        "servability",
        "coverage",
        "missingness",
        "gates",
    }
    text = json.dumps(d, default=str).lower()
    assert "delta" not in text and "crps" not in text and d["n_eligible"]["pra"] > 0


def test_no_look_is_computed_before_its_date(monkeypatch: pytest.MonkeyPatch) -> None:
    con = _build()

    def boom(*a: object, **k: object) -> object:
        raise AssertionError("a score was computed on a day with no look")

    monkeypatch.setattr(L, "score_live", boom)
    for day in (
        date(2026, 11, 3),
        date(2026, 11, 8),
        date(2026, 11, 14),
        date(2026, 11, 15),
    ):  # incl. the interim Sunday with too few rows
        with pytest.raises(L.LookNotDue):
            L.read_look(con, day, date(2027, 4, 11))


def test_a_look_is_read_once_its_date_and_counts_are_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    con = _build()
    monkeypatch.setattr(L, "n_by_sunday", lambda *a, **k: _ns({date(2026, 11, 15): 1500}))
    monkeypatch.setattr(
        L,
        "score_live",
        lambda con, look, season=2026, *a, **k: {
            "decisions": {t: {"verdict": "CONTINUE"} for t in TARGETS},
            "look": look,
        },
    )
    out = L.read_look(con, date(2026, 11, 15), date(2027, 4, 11))
    assert set(out["looks"]) == {"interim"} and out["looks"]["interim"]["due_targets"] == list(
        TARGETS
    )


def test_main_prints_display_on_a_non_look_day(
    capsys: pytest.CaptureFixture[str], tmp_path: Any
) -> None:
    con = _build(n_days=2)
    path = tmp_path / "n.duckdb"
    con.execute(f"ATTACH '{path}' AS out")
    for t in (
        "games",
        "player_game_stats",
        "forward_predictions",
        "forward_combo_shadow",
        "forward_combo_scores",
        "forward_combo_heartbeat",
        "forward_combo_meta",
    ):
        con.execute(f"CREATE TABLE out.{t} AS SELECT * FROM {t}")
    con.execute("DETACH out")
    assert L.main(["--db", str(path), "--today", "2026-11-04"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["look"] is None and "display" in printed


# --------------------------------------------------------------------------- review fixes S2, S3
def test_interim_coverage_between_80_and_95_flags_and_continues_below_80_kills() -> None:
    """S3, end to end: 86% coverage -> CONTINUE (PASS blocked), 72% -> KILL, hard stamp failure -> KILL."""
    pairs = [(f"002260000{d}", 7000 + j) for d in (5, 6, 7, 8) for j in range(7)]
    flagged = L.score_live(_build(drop_shadow=set(pairs[:8])), "interim", cfg=CFG)
    assert flagged["coverage"]["pra"]["ok"] is False and flagged["coverage"]["pra"]["kill"] is False
    assert {d["verdict"] for d in flagged["decisions"].values()} == {"CONTINUE"}
    killed = L.score_live(_build(drop_shadow=set(pairs[:16])), "interim", cfg=CFG)
    assert killed["coverage"]["pra"]["kill"] is True
    assert {d["verdict"] for d in killed["decisions"].values()} == {"KILL"}
    con = _build()
    con.execute(
        "UPDATE forward_combo_shadow SET made_at = tipoff_utc WHERE game_id = '0022600001' AND player_id = 7001 AND target = 'blk'"
    )
    hard = L.score_live(con, "interim", cfg=CFG)
    assert hard["decisions"]["blk"]["verdict"] == "KILL" and hard["decisions"]["pra"][
        "verdict"
    ] in ("KILL", "CONTINUE")


def test_rows_before_the_counting_start_are_warm_up_and_none_set_scores_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S2 for the combo reader."""
    all_rows = L.load_live(_build(), "pra")
    later = L.load_live(_build(counting="2026-10-24"), "pra")
    assert 0 < len(later["pid"]) < len(all_rows["pid"]) and later["day"].min() >= np.datetime64(
        "2026-10-24"
    )
    assert L.counting_start(_build(counting="2026-10-24")) == date(2026, 10, 24)
    unset = _build(counting=None)
    assert len(L.load_live(unset, "pra")["pid"]) == 0 and L.servability(unset)["servable"] == 0
    with pytest.raises(L.CountingNotStarted):
        L.score_live(unset, "final", cfg=CFG)
    monkeypatch.setattr(
        L,
        "score_live",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("scored with no counting start")),
    )
    with pytest.raises(L.CountingNotStarted):
        L.read_look(unset, date(2026, 11, 15), date(2027, 4, 11))
    d = L.daily_display(unset)
    assert (
        d["counting_start"] is None and "UNSET" in d["note"] and sum(d["n_eligible"].values()) == 0
    )


def test_the_avg_reader_drops_warm_up_rows_and_scores_nothing_without_a_counting_start() -> None:
    from nba.daily.avg_shadow import store as avg_store
    from nba.eval import avg_shadow_live as A
    from tests.eval.test_avg_shadow_live import build

    con = build(n_dates=20, per_date=6)
    full = A.load_live(con, "pts")
    assert len(full["pid"]) > 0
    avg_store.set_meta(
        con, "counting_start", "2026-11-01", datetime(2026, 10, 30)
    )  # a later write wins
    later = A.load_live(con, "pts")
    assert 0 < len(later["pid"]) < len(full["pid"]) and later["day"].min() >= np.datetime64(
        "2026-11-01"
    )
    con.execute("DELETE FROM forward_avg_meta")
    assert len(A.load_live(con, "pts")["pid"]) == 0
    with pytest.raises(A.CountingNotStarted):
        A.read_look(con, date(2026, 11, 15), date(2027, 4, 11))
    with pytest.raises(A.CountingNotStarted):
        A.score_live(con, "interim")
    d = A.daily_display(con)
    assert d["counting_start"] is None and "UNSET" in d["note"]


def test_the_full_reader_honours_a_counting_start_when_its_meta_table_exists() -> None:
    """FULL has no meta table (its rule counts from the first regular-season game): absent table = no extra filter; a table without a start scores nothing."""
    from nba.eval import full_shadow_live as F
    from tests.eval.test_full_shadow_live import build

    con = build(0.5, n_dates=12, per_date=2, per_game=3, stats=("pts",))
    base = F.load_live(con, "pts")
    assert len(base["pid"]) > 0 and F.counting_start(con) == date.min
    con.execute("CREATE TABLE forward_full_meta (key VARCHAR, value VARCHAR, written_at TIMESTAMP)")
    assert len(F.load_live(con, "pts")["pid"]) == 0
    day = base["day"][len(base["day"]) // 2]
    con.execute(
        "INSERT INTO forward_full_meta VALUES ('counting_start', ?, TIMESTAMP '2026-10-19 00:00:00')",
        [str(day)],
    )
    later = F.load_live(con, "pts")
    assert 0 < len(later["pid"]) < len(base["pid"]) and later["day"].min() >= day
