# ruff: noqa: E501
"""Past-only placebo: every value comes from a strictly earlier row of the same player-season."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from research.recent_role import placebo as PB


def _frame(seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for pid in range(1, 9):
        for season, start in ((2023, dt.date(2023, 10, 20)), (2024, dt.date(2024, 10, 20))):
            for k in range(30):
                rows.append({
                    "player_id": pid, "season": season, "game_id": f"{season}{pid:02d}{k:03d}",
                    "game_date": start + dt.timedelta(days=int(k * 2)),
                    "a": float(pid * 1000 + season - 2000 + k), "b": float(rng.normal()),
                })  # fmt: skip
    return pl.DataFrame(rows).sample(
        fraction=1.0, shuffle=True, seed=1
    )  # frame order is not time order


def test_every_source_is_a_strictly_earlier_row_of_the_same_player_season() -> None:
    fr = _frame()
    src = PB.past_only_source(fr, seed=3)
    pid, sea, day = fr["player_id"].to_numpy(), fr["season"].to_numpy(), fr["game_date"].to_numpy()
    for i, s in enumerate(src):
        if s < 0:
            continue
        assert pid[s] == pid[i] and sea[s] == sea[i] and day[s] < day[i]
    first = (
        fr.with_row_index("_i")
        .sort(["player_id", "season", "game_date"])
        .group_by(["player_id", "season"], maintain_order=True)
        .first()
    )
    assert all(
        src[i] == -1 for i in first["_i"].to_list()
    )  # the first row of a player-season has no source
    assert (src >= 0).sum() == fr.height - 16


def test_placebo_values_are_real_past_values_and_shared_across_columns() -> None:
    fr = _frame()
    out = PB.permute_past_only(fr, ("a", "b"), seed=5)
    src = PB.past_only_source(fr, seed=5)
    a, b = fr["a"].to_numpy(), fr["b"].to_numpy()
    pa, pb = out["pl_a"].to_numpy(), out["pl_b"].to_numpy()
    ok = src >= 0
    assert np.array_equal(pa[ok], a[src[ok]]) and np.array_equal(pb[ok], b[src[ok]])
    assert np.isnan(pa[~ok]).all()
    # a value that lives in a LATER row can never appear: the 'a' column is increasing in time within a player-season
    d = fr.with_columns(pl.Series("pa", pa))
    chk = d.filter(pl.col("pa").is_not_nan()).filter(pl.col("pa") >= pl.col("a"))
    assert chk.is_empty()


def test_placebo_is_reproducible_and_seed_dependent() -> None:
    fr = _frame()
    assert np.array_equal(PB.past_only_source(fr, 1), PB.past_only_source(fr, 1))
    assert not np.array_equal(PB.past_only_source(fr, 1), PB.past_only_source(fr, 2))


def test_same_date_rows_are_not_sources() -> None:
    fr = pl.DataFrame({
        "player_id": [1, 1, 1], "season": [2024] * 3, "game_id": ["a", "b", "c"],
        "game_date": [dt.date(2024, 1, 1), dt.date(2024, 1, 1), dt.date(2024, 1, 3)], "x": [1.0, 2.0, 3.0],
    })  # fmt: skip
    src = PB.past_only_source(fr, 0)
    assert src[0] == -1 and src[1] == -1 and src[2] in (0, 1)
