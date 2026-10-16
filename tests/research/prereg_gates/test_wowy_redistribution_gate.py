"""WOWY_REDISTRIBUTION gate helpers (docs/prereg/WOWY_REDISTRIBUTION.md DRAFT).

The clustered slope used to size the expected effect, and the declared slices."""

from __future__ import annotations

import numpy as np
import polars as pl

from research.eval import wowy_redistribution_gate as G


def test_clustered_slope_recovers_known_coefficient() -> None:
    rng = np.random.default_rng(0)
    n = 4000
    x = rng.normal(size=n)
    ctrl = rng.normal(size=(n, 2))
    key = np.repeat(np.arange(n // 10), 10)
    y = 0.3 * x + ctrl @ np.array([1.0, -2.0]) + rng.normal(size=n)
    r = G.clustered_slope(y, x, ctrl, key)
    assert abs(r["coef"] - 0.3) < 4 * r["se"]
    assert r["se"] > 0 and r["clusters"] == n // 10


def test_declared_slices_partition_logic() -> None:
    df = pl.DataFrame(
        {
            "n_out_rot": [0, 1, 2, 1],
            "vac_min": [0.0, 10.0, 40.0, 25.0],
            "wo_conf": [0.0, 0.2, 1.0, 0.6],
            "wo_d_min": [0.0, 5.0, 3.0, 1.0],
        }
    )
    got = {k: df.select(e.alias("m"))["m"].to_list() for k, (_d, e) in G.SLICES.items()}
    assert got["ANY"] == [False, True, True, True]
    assert got["VAC24"] == [False, False, True, True]
    assert got["ABSORB"] == [False, False, True, False]
