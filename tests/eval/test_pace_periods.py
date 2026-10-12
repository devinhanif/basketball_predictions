"""Unit tests for the pace / period study helpers (pure functions; no database)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from research.eval import pace_periods as A
from research.eval import pace_periods_b as B


def _games(n: int = 60, bump_from: int | None = None) -> pl.DataFrame:
    rng = np.random.default_rng(0)
    rows = []
    for i in range(n):
        h, a = (i % 6) + 1, ((i + 1) % 6) + 1 + (6 if i % 2 else 0)
        k = 0 if bump_from is None or i < bump_from else 1
        rows.append(
            {
                "game_id": f"g{i:03d}",
                "game_date": dt.date(2023, 11, 1) + dt.timedelta(days=i),
                "season": 2023,
                "home_team": h,
                "away_team": a if a != h else a + 20,
                "home_pts": int(100 + rng.integers(-8, 8)) + 50 * k,
                "away_pts": int(100 + rng.integers(-8, 8)) + 50 * k,
                "n_poss": int(196 + rng.integers(-6, 6)) + 40 * k,
                "maxp": 4,
            }
        )
    return pl.DataFrame(rows)


def test_asof_rolls_planted_future() -> None:
    base = A.asof_rolls(_games()).sort(["game_id", "team_id"])
    pert = A.asof_rolls(_games(bump_from=40)).sort(["game_id", "team_id"])
    early = base["game_id"] < "g040"
    for c in ("r20_pf", "r20_pa", "r20_pace", "o_pace", "xpace"):
        a, b = base.filter(early)[c].to_numpy(), pert.filter(early)[c].to_numpy()
        assert np.allclose(a, b, equal_nan=True), c
    late = ~early
    assert not np.allclose(
        base.filter(late)["r20_pace"].fill_null(0).to_numpy(),
        pert.filter(late)["r20_pace"].fill_null(0).to_numpy(),
    )


def test_partial_recovers_planted_slope_and_null() -> None:
    rng = np.random.default_rng(1)
    n = 5000
    pace = rng.normal(size=n)
    ctrl = rng.normal(size=(n, 2))
    resid = 0.5 * pace + ctrl[:, 0] + rng.normal(size=n)
    slope, r, r2 = A.partial(ctrl, resid, pace)
    assert abs(slope - 0.5) < 0.06 and r > 0 and abs(r2 - r * r) < 1e-12
    null = ctrl[:, 0] + rng.normal(size=n)
    assert abs(A.partial(ctrl, null, pace)[0]) < 0.06


def test_devig_and_boot_ci_cover_mean() -> None:
    df = pl.DataFrame({"x": [-110, 100, -200]})
    p = df.select(B.amer(pl.col("x")).alias("p"))["p"].to_numpy()
    assert np.allclose(p, [110 / 210, 0.5, 200 / 300])
    v = np.random.default_rng(2).normal(3.0, 1.0, 400)
    g = np.repeat(np.arange(100), 4)
    ci = B.mean_ci(v, g)
    assert ci["ci"][0] < 3.0 < ci["ci"][1] and ci["n"] == 400 and ci["games"] == 100
