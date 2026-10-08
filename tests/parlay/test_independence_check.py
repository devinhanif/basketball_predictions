"""Synthetic checks for nba.parlay.independence_check (no real data, no network)."""

from __future__ import annotations

from datetime import date, timedelta

import duckdb
import numpy as np
import polars as pl
from scipy import stats

from nba.daily.store import ensure_tables
from nba.parlay.independence_check import (
    IndependenceCfg,
    build_cross_game_parlays,
    cross_game_corr,
    forward_check,
    forward_section,
    load_independence_cfg,
    parlay_calibration,
    props_cross_game,
    run_checks,
    wins_cross_game,
)

CFG = IndependenceCfg(n_boot=400, parlays_per_date=15)
GRID = np.round(0.05 * np.arange(1, 20), 2)


def _wins(shock_sd: float, n_dates: int = 250, g: int = 6, seed: int = 1) -> pl.DataFrame:
    """Home-win outcomes from a probit latent with a shared daily shock ."""
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_dates):
        shock = rng.normal() * shock_sd
        for k in range(g):
            p = float(rng.uniform(0.3, 0.7))
            # latent: P(y=1) = p marginally when shock ~ N(0, shock_sd^2)
            eps = rng.normal() * np.sqrt(1 - shock_sd**2)
            y = float(shock + eps < stats.norm.ppf(p))
            rows.append((f"g{d}_{k}", date(2024, 1, 1) + timedelta(days=d), p, y))
    return pl.DataFrame(rows, schema=["game_id", "game_date", "p", "y"], orient="row")


def _props(shock_sd: float, n_dates: int = 150, g: int = 6, seed: int = 2) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    qg = [float(round(20 + 5 * stats.norm.ppf(t), 3)) for t in GRID]
    rows = []
    for d in range(n_dates):
        shock = rng.normal() * shock_sd
        for k in range(g):
            for _ in range(5):
                eps = rng.normal() * np.sqrt(1 - shock_sd**2)
                y = float(max(0, round(20 + 5 * (shock + eps))))
                rows.append((f"g{d}_{k}", date(2024, 1, 1) + timedelta(days=d), "pts", qg, y))
    return pl.DataFrame(
        rows,
        schema={
            "game_id": pl.String,
            "game_date": pl.Date,
            "stat": pl.String,
            "q_grid": pl.List(pl.Float64),
            "y": pl.Float64,
        },
        orient="row",
    )


def test_independent_wins_r_near_zero_and_unflagged() -> None:
    res = wins_cross_game(_wins(0.0), CFG)["std_resid"]
    assert abs(res.r) < 0.04
    assert res.lo < 0 < res.hi
    assert not res.flagged(0.05)


def test_common_daily_shock_detected_in_wins() -> None:
    res = wins_cross_game(_wins(0.5), CFG)["std_resid"]
    assert res.r > 0.05 and res.lo > 0
    assert res.flagged(0.05)


def test_props_independent_vs_shock() -> None:
    ind = props_cross_game(_props(0.0), CFG)["pts"]
    assert ind.lo < 0 < ind.hi
    shocked = props_cross_game(_props(0.5), CFG)["pts"]
    assert shocked.lo > 0 and shocked.flagged(0.05)


def test_single_game_dates_carry_no_information() -> None:
    r = cross_game_corr(np.arange(10.0), [str(i) for i in range(10)], n_boot=50)
    assert r.n_dates == 0 and np.isnan(r.r)


def test_parlays_calibrated_when_independent_and_miscalibrated_under_shock() -> None:
    ind_w = _wins(0.0, n_dates=300)
    par = build_cross_game_parlays(ind_w, _props(0.0).head(0), 20, seed=3)
    assert par["n_legs"].min() >= 2 and par["n_legs"].max() <= 4
    cal = parlay_calibration(par, CFG)
    assert cal is not None and cal.gap_lo < 0 < cal.gap_hi
    # Same-direction (over) prop legs share the daily shock -> hit rate exceeds the product.
    # (Random-side moneyline legs cancel a home-win shock by construction, so use props here.)
    empty = _wins(0.0).head(0)
    shocked = build_cross_game_parlays(empty, _props(0.8, n_dates=300), 20, seed=3)
    sh = parlay_calibration(shocked, CFG)
    assert sh is not None and sh.gap > 0 and sh.gap_lo > 0


def test_run_checks_flag_list() -> None:
    res = run_checks(_wins(0.5), _props(0.0), CFG, with_parlays=False)
    assert "wins:std_resid" in res["flagged"]
    assert not any(f.startswith("props") for f in res["flagged"])


def test_config_loads_independence_block() -> None:
    c = load_independence_cfg()
    assert c.min_forward_dates == 30 and c.flag_abs_r == 0.05


def _forward_db(n_dates: int) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    ensure_tables(con)
    w = _wins(0.0, n_dates=n_dates, g=3)
    for r in w.iter_rows(named=True):
        con.execute(
            "INSERT INTO forward_scores VALUES (now(),?,?,2026,'rung0_injury_elo','v',"
            "'win_prob_home',-1,now(),?,?,0,0,NULL,'scored')",
            [r["game_id"], r["game_date"], r["y"], r["p"]],
        )
    return con


def test_forward_check_waits_then_runs() -> None:
    assert forward_check(_forward_db(10), CFG)["status"] == "insufficient"
    assert "waiting" in forward_section(_forward_db(10), CFG)[-1]
    res = forward_check(_forward_db(40), CFG)
    assert res["status"] == "ok" and res["n_dates"] == 40 and "std_resid" in res["wins"]
