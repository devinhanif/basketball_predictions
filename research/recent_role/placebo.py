# ruff: noqa: E501
"""Past-only placebo for the RECENT_ROLE candidate columns (docs/prereg/RECENT_ROLE.md).

WORKLOAD's placebo permuted a column across a whole player-season, which hands an early row a LATER game's value
(look-ahead on the player's season level; adversary review 2026-10-12). Here each row's placebo value is the real
value of the same columns taken from ONE strictly earlier row of the same player-season, drawn uniformly. The
first row of a player-season has no earlier row and gets NaN (the rate is reported). One source row per target row,
shared by every placebo column, so the columns keep the covariance real rows have.

What it keeps: the marginal and the player-level scale of the columns, real past values only. What it breaks: the link to
THIS row's recent state (min_last of the previous game). It is also correlated with the true value through the
player's persistence, so it is a hard null to beat (conservative for the candidate).
"""

from __future__ import annotations

import numpy as np
import polars as pl


def past_only_source(frame: pl.DataFrame, seed: int = 0) -> np.ndarray:
    """For each row (in the frame's own order) the row index of its placebo source, or -1 for none.

    Source is uniform on the rows of the same (player_id, season) ordered by (game_date, game_id) that are
    strictly earlier in that order and dated strictly before the row's own date."""
    rng = np.random.default_rng(seed)
    order = (
        frame.with_row_index("_i")
        .sort(["player_id", "season", "game_date", "game_id"])
        .select("_i", "player_id", "season", "game_date")
    )
    idx = order["_i"].to_numpy()
    pid = order["player_id"].to_numpy()
    sea = order["season"].to_numpy()
    day = order["game_date"].cast(pl.Int32).to_numpy()
    src = np.full(frame.height, -1, dtype=np.int64)
    start = 0
    n = len(idx)
    while start < n:
        end = start
        while end < n and pid[end] == pid[start] and sea[end] == sea[start]:
            end += 1
        for pos in range(start, end):
            # rows strictly earlier by date: the largest position with a smaller date
            lo = start
            hi = pos
            while hi > lo and day[hi - 1] == day[pos]:
                hi -= 1
            m = hi - lo
            if m > 0:
                src[idx[pos]] = idx[lo + int(rng.integers(0, m))]
        start = end
    return src


def permute_past_only(
    frame: pl.DataFrame, cols: tuple[str, ...], seed: int = 0, prefix: str = "pl_"
) -> pl.DataFrame:
    """Add ``prefix + col`` columns holding the past-only placebo of every column in ``cols``."""
    src = past_only_source(frame, seed)
    has = src >= 0
    out = []
    for c in cols:
        a = frame[c].to_numpy().astype(float)
        v = np.where(has, a[np.maximum(src, 0)], np.nan)
        out.append(pl.Series(prefix + c, v))
    return frame.with_columns(out)
