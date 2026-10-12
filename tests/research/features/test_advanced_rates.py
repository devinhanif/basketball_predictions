"""Formula correctness (hand-computed) and as-of correctness for ADVANCED_RATES."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest

from research.features import advanced_rates as ar

D = [dt.date(2024, 1, d) for d in range(1, 8)]


def test_game_score_hand_computed() -> None:
    v = ar.game_score(20, 8, 15, 3, 4, 2, 5, 1, 4, 1, 3, 2)
    assert v == pytest.approx(16.5)


def test_ts_ppws_ppr_usage_orb() -> None:
    assert ar.ts_pct(20, 15, 4) == pytest.approx(20 / (2 * (15 + 1.76)))
    assert ar.ppws(20, 15, 4) == pytest.approx(20 / (15 + 1.9))
    assert ar.pure_point_rating(9, 3, 36, 100, 100) == pytest.approx(100 * (6 - 3) / 36)
    # faster team than league: pace factor < 1 shrinks the rate
    assert ar.pure_point_rating(9, 3, 36, 96, 104) == pytest.approx(100 * (96 / 104) * 3 / 36)
    assert ar.usage_oncourt(10, 5, 2, 50, 20, 10) == pytest.approx((10 + 2.2 + 2) / (50 + 8.8 + 10))
    assert ar.oreb_pct(3, 10, 30) == pytest.approx(0.075)
    assert ar.expected_plays(0.25, 100, 36) == pytest.approx(0.25 * 100 * 36 / 48)


def test_stat_defs_match_scalar_functions() -> None:
    row = {
        "pts": 20, "fgm": 8, "fga": 15, "ftm": 3, "fta": 4, "oreb": 2, "dreb": 5, "stl": 1,
        "ast": 4, "blk": 1, "pf": 3, "tov": 2, "fg3m": 2, "fg3a": 5, "reb": 7, "minutes": 30.0,
        "oc_fga": 50, "oc_fta": 20, "oc_tov": 10, "oc_fgm": 22, "oc_oreb": 8, "oc_opp_dreb": 30,
        "oc_dreb": 25, "oc_opp_oreb": 9, "oc_poss_off": 50, "oc_poss_def": 52, "oc_min": 30.0,
        "pace_factor": 1.0, "tr_fsp": 3, "fsp": 10, "tr_pts": 5, "ast_mk": 4, "mk": 8, "pb_att": 2,
    }  # fmt: skip
    df = pl.DataFrame([row])
    got = {k: df.select(n.alias("n"), d.alias("d")).row(0) for k, (n, d, _) in ar.STAT_DEFS.items()}
    assert got["gmsc"] == (pytest.approx(16.5), 1.0)
    assert got["ts"][0] / got["ts"][1] == pytest.approx(ar.ts_pct(20, 15, 4))
    assert got["ppws"][0] / got["ppws"][1] == pytest.approx(ar.ppws(20, 15, 4))
    assert got["usg"][0] / got["usg"][1] == pytest.approx(ar.usage_oncourt(15, 4, 2, 50, 20, 10))
    assert got["orb"][0] / got["orb"][1] == pytest.approx(ar.oreb_pct(2, 8, 30))
    assert got["ppr"][0] / got["ppr"][1] == pytest.approx(ar.pure_point_rating(4, 2, 30, 1, 1))
    assert got["ast_pct"][0] / got["ast_pct"][1] == pytest.approx(4 / (22 - 8))
    assert got["tr_share"][0] / got["tr_share"][1] == pytest.approx(0.3)
    assert got["tr_ppp"][0] / got["tr_ppp"][1] == pytest.approx(5 / 3)
    assert got["ast_share"][0] / got["ast_share"][1] == pytest.approx(0.5)
    assert got["putback_share"][0] / got["putback_share"][1] == pytest.approx(2 / 15)
    assert got["pace48"][0] / got["pace48"][1] == pytest.approx(51 / (30 / 48))


def _rows(a_vals: dict[int, tuple[float, float]], a_team: dict[int, int]) -> pl.DataFrame:
    """B plays day 1 (num 10, den 2); A plays the days in ``a_vals`` (day -> num, den); A has
    a non-played row on day 3. All one position group."""
    recs = [("B", 1, 9, True, 10.0, 2.0)]
    for day in range(2, 6):
        nv, dv = a_vals.get(day, (float("nan"), float("nan")))
        recs.append(("A", day, a_team.get(day, 1), day in a_vals, nv, dv))
    return pl.DataFrame(
        {
            "player_id": [r[0] for r in recs],
            "game_id": [f"g{r[1]}{r[0]}" for r in recs],
            "game_date": [D[r[1] - 1] for r in recs],
            "team_id": [r[2] for r in recs],
            "pos": ["G"] * len(recs),
            "played": [r[3] for r in recs],
            "x__num": [r[4] for r in recs],
            "x__den": [r[5] for r in recs],
        }
    )


def _get(df: pl.DataFrame, pid: str, day: int, col: str) -> float:
    r = df.filter((pl.col("player_id") == pid) & (pl.col("game_id") == f"g{day}{pid}"))
    return float(r[col][0])


A_VALS = {2: (4.0, 1.0), 4: (6.0, 1.0)}
A_TEAM = {4: 2, 5: 2}


def test_asof_values_hand_computed() -> None:
    out = ar.asof_series(_rows(A_VALS, A_TEAM), ["x"])
    lam5 = 0.5 ** (1 / 5)
    # day 2: only B's day-1 game exists in the position prior: mu = 5, dbar = 2, k = 10
    assert _get(out, "A", 2, "x_ew5") == pytest.approx(5.0)
    assert _get(out, "A", 2, "x_stint20") == pytest.approx(5.0)
    # day 3 (DNP row): prior = B d1 (10, 2) + A d2 (4, 1): mu = 14/3, dbar = 1.5, k = 7.5
    mu, k = 14 / 3, 7.5
    assert _get(out, "A", 3, "x_ew5") == pytest.approx((lam5 * 4 + k * mu) / (lam5 * 1 + k))
    # day 5: team changed on day 4; prior mu = 20/4 = 5 (B 10/2, A 4/1, A 6/1), dbar = 4/3
    mu5, dbar5 = 20 / 4, 4 / 3
    k5 = ar.PSEUDO * dbar5
    lam_s = 0.5 ** (1 / ar.STINT_HALF_LIFE)
    career = (10 + k5 * mu5) / (2 + k5)
    expect = (lam_s * 6 + k5 * career) / (lam_s * 1 + k5)
    assert _get(out, "A", 5, "x_stint20") == pytest.approx(expect)
    # the stint restarted: only one game with the new team before day 5; day 4 row has none
    assert _get(out, "A", 4, "adv_stint_n") == 0
    assert _get(out, "A", 5, "adv_stint_n") == 1
    assert _get(out, "A", 3, "adv_stint_n") == 1  # DNP day 3 still on team 1 after one game


def test_dnp_row_does_not_advance_state() -> None:
    base = ar.asof_series(_rows(A_VALS, A_TEAM), ["x"])
    # day 4 features are identical whether or not a DNP row (day 3) sits between day 2 and 4
    a_no_dnp = _rows(A_VALS, A_TEAM).filter(
        ~((pl.col("player_id") == "A") & (pl.col("game_id") == "g3A"))
    )
    alt = ar.asof_series(a_no_dnp, ["x"])
    assert _get(base, "A", 4, "x_ew20") == pytest.approx(_get(alt, "A", 4, "x_ew20"))


def test_planted_future_changes_nothing_before() -> None:
    base = ar.asof_series(_rows(A_VALS, A_TEAM), ["x"])
    fut = ar.asof_series(_rows({2: (4.0, 1.0), 4: (6.0, 1.0), 5: (999.0, 1.0)}, A_TEAM), ["x"])
    cols = [c for c in base.columns if c.startswith("x_")]
    for day in (2, 3, 4, 5):  # day-5 row itself must not see its own game either
        for c in cols:
            a, b = _get(base, "A", day, c), _get(fut, "A", day, c)
            assert (np.isnan(a) and np.isnan(b)) or a == pytest.approx(b), (day, c)


def test_same_day_other_player_not_in_prior() -> None:
    rows = _rows(A_VALS, A_TEAM)
    extra = pl.DataFrame(
        {
            "player_id": ["C"], "game_id": ["g2C"], "game_date": [D[1]], "team_id": [7],
            "pos": ["G"], "played": [True], "x__num": [1000.0], "x__den": [1.0],
        }
    )  # fmt: skip
    out = ar.asof_series(pl.concat([rows, extra]), ["x"])
    assert _get(out, "A", 2, "x_ew5") == pytest.approx(5.0)  # C's same-day game is excluded


def test_slope_vol_and_shrinkage() -> None:
    w = (2.0 * np.arange(8) + 1.0)[:, None]
    s, v, m = ar._slope_vol(w)
    assert s[0] == pytest.approx(2.0)
    assert v[0] == pytest.approx(np.var(w[:, 0], ddof=1))
    assert m[0] == 8
    w2 = w.copy()
    w2[:3, 0] = np.nan  # 5 valid
    s2, _, m2 = ar._slope_vol(w2)
    assert s2[0] == pytest.approx(2.0) and m2[0] == 5
    w3 = np.full((4, 1), np.nan)
    w3[0, 0] = 1.0  # one valid: slope / variance undefined here, shrunk to the prior by the caller
    s3, v3, m3 = ar._slope_vol(w3)
    assert np.isnan(s3[0]) and np.isnan(v3[0]) and m3[0] == 1


def test_short_history_has_no_nan_after_first_prior() -> None:
    out = ar.asof_series(_rows(A_VALS, A_TEAM), ["x"])
    for day in (3, 4, 5):
        assert np.isfinite(_get(out, "A", day, "x_slope20"))
        assert np.isfinite(_get(out, "A", day, "x_vol20"))
    # one valid game: slope shrunk to 0 (a single point has no slope), vol = position-prior sd
    assert _get(out, "A", 3, "x_slope20") == pytest.approx(0.0)
