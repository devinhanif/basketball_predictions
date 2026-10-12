"""Decision scoring and market-slow descriptive metrics on synthetic frames (no network, no DB)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from nba.markets import decision_scoring as ds
from nba.markets import market_slow as ms


def test_american_to_decimal() -> None:
    d = ds.american_to_decimal(np.array([-110, 150, -200, 100]))
    assert d == pytest.approx([1 + 100 / 110, 2.5, 1.5, 2.0])


def test_choose_side_ev_and_profit() -> None:
    # model says 60% over at -110 / -110: over EV = 0.6*1.909 - 1 > 0; under EV < 0
    over, ev, p_side, dec = ds.choose_side(np.array([0.6]), np.array([-110]), np.array([-110]))
    assert bool(over[0]) and p_side[0] == pytest.approx(0.6)
    assert ev[0] == pytest.approx(0.6 * (1 + 100 / 110) - 1)
    # model says 20% over at +140 / -180: take the under at -180
    over, ev, p_side, dec = ds.choose_side(np.array([0.2]), np.array([140]), np.array([-180]))
    assert not bool(over[0]) and p_side[0] == pytest.approx(0.8)
    assert ev[0] == pytest.approx(0.8 * (1 + 100 / 180) - 1)
    # explicit fee lowers EV one-for-one
    _, ev_fee, _, _ = ds.choose_side(
        np.array([0.2]), np.array([140]), np.array([-180]), ds.FeeSpec(per_unit=0.01)
    )
    assert ev_fee[0] == pytest.approx(ev[0] - 0.01)
    pr = ds.profit_per_unit(np.array([True, True]), np.array([1.0, 0.0]), np.array([2.0, 2.0]))
    assert pr.tolist() == [1.0, -1.0]


def test_boot_ratio_ci_covers_estimate_and_games_cluster() -> None:
    rng = np.random.default_rng(0)
    gi = np.repeat(np.arange(200), 5)
    x = rng.normal(0.1, 1.0, gi.size)
    b = ds.Boot(gi, 200, n_boot=500)
    r = b.ratio(x, np.ones_like(x))
    assert r["lo"] < r["est"] < r["hi"] and r["n"] == 1000
    assert r["est"] == pytest.approx(x.mean())


def _pairs(n_games: int = 60, season: int = 2023, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    tip = datetime(2024, 1, 10, 0, 0, tzinfo=None)
    for g in range(n_games):
        for pid in range(1, 5):
            for stat in ("pts", "reb"):
                p = float(rng.uniform(0.3, 0.7))
                mult = float(rng.uniform(0.4, 0.6))
                over = float(rng.random() < p)
                for kind, dt in (("t60", -64), ("t5", -9)):
                    line = 10.5
                    rows.append(
                        {
                            "game_id": f"G{g:03d}", "player_id": pid, "stat": stat,
                            "book": "draftkings", "snapshot_kind": kind,
                            "snapshot_ts": tip + timedelta(minutes=dt), "tipoff_utc": tip,
                            "line": line, "price_over": -110, "price_under": -110,
                            "p_over_mult": mult + (0.01 if kind == "t5" else 0.0),
                            "p_model": p, "over": over, "season": season,
                            "game_date": date(2024, 1, 9),
                            "sl_tier": "high", "sl_role": "starter", "sl_tm": "teammate_out",
                        }
                    )  # fmt: skip
    return pl.DataFrame(rows).with_columns(pl.col("player_id").cast(pl.Int64))


def test_load_and_run_all_on_synthetic(tmp_path: Path) -> None:
    _pairs().write_parquet(tmp_path / "pairs_2023.parquet")
    legs = ds.load_legs(tmp_path, seasons=(2023,))
    assert legs["gi"].size == 60 * 4 * 2  # one T-60 leg per (game, player, stat)
    boot = ds.Boot(legs["gi"], legs["G"], n_boot=200)
    elig = np.ones(legs["gi"].size, dtype=bool)
    r = ds.score_threshold(boot, legs, elig, 0.0, np.random.default_rng(0), n_placebo=20)
    m = r["model"]
    assert m["n"] == int((legs["ev"] >= 0).sum())
    assert r["market_lean"]["n"] == m["n"] and r["placebo"]["n"] == m["n"]
    assert m["roi_lo"] <= m["roi"] <= m["roi_hi"]
    hi = ds.score_threshold(boot, legs, elig, 0.10, np.random.default_rng(0), n_placebo=20)
    assert hi["model"]["n"] <= m["n"]  # thresholds nest
    assert ds.example_bet(legs, 0.0)["ev_per_unit"] >= 0.0


def test_holdout_season_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="holdout"):
        ds.load_legs(tmp_path, seasons=(2025,))
    with pytest.raises(ValueError, match="holdout"):
        ms.load(tmp_path, seasons=(2025,))


def test_market_slow_derive_and_bh(tmp_path: Path) -> None:
    _pairs().write_parquet(tmp_path / "pairs_2023.parquet")
    L = ms.load(tmp_path, seasons=(2023,))
    D = ms.derive(L)
    assert D["unch"].sum() == D["joined"].sum()  # every synthetic line is unchanged
    assert np.all(D["dm"][D["unch"] > 0] == pytest.approx(0.01))
    assert D["mdir"].max() == 1.0 and D["chg"].sum() == 0
    q = ms.bh([0.001, 0.04, 0.5])
    assert q[0] <= q[1] <= q[2] and q[0] == pytest.approx(0.003)


def test_no_execution_surface_in_new_modules() -> None:
    src = (Path(ds.__file__).read_text() + Path(ms.__file__).read_text()).lower()
    for word in ("requests.post", "place_order", "create_order", "api_key", "httpx"):
        assert word not in src
