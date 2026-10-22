# ruff: noqa: E501
"""Causal state prefix and causal on-floor five for INGAME_WINPROB_V2 (review M2, M3).

State at T = the longest prefix of the game's events, in action-number order, in which every event has
elapsed <= T. The first event with elapsed > T and everything after it are excluded, even a later-logged
event whose elapsed is <= T. ``np.searchsorted`` on the elapsed vector is NOT used (the vector is not
sorted: elapsed goes backwards about 3 times a game).

The five on the floor at T is the lineup tracker (``nba.parse.lineups``) run on that prefix only, with the
Q1 seed from the box starters (announced before tip) and NO box roster (``roster_by_team=None``: who
played is end-of-game information). Period openers are therefore found only from events <= T; open slots
are filled from the previous closing five by the tracker's own rule. The stints table is never read.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import polars as pl

from nba.parse.lineups import track_lineups


def prefix_len(elapsed: np.ndarray, t: float) -> int:
    """Number of leading events (in the given order) before the first one with elapsed > t."""
    over = np.asarray(elapsed, dtype=float) > t
    return int(np.argmax(over)) if bool(over.any()) else int(len(over))


def causal_prefix(pbp: pd.DataFrame, elapsed: np.ndarray, t: float) -> pd.DataFrame:
    """Events of the causal prefix at T. ``pbp`` and ``elapsed`` must already be in action-number order."""
    return pbp.iloc[: prefix_len(elapsed, t)]


def causal_five(
    prefix: pd.DataFrame, starters_by_team: dict[int, list[int]]
) -> dict[int, tuple[int, ...]]:
    """Five per team at the end of ``prefix`` (an empty prefix returns the starters)."""
    if len(prefix) == 0:
        return {t: tuple(v) for t, v in starters_by_team.items()}
    stints = track_lineups(pl.from_pandas(prefix), starters_by_team)
    out: dict[int, tuple[int, ...]] = {}
    for team, five in starters_by_team.items():
        rows = stints.filter(pl.col("team_id") == team)
        if rows.is_empty():
            out[team] = tuple(five)
            continue
        last = rows.sort(["period", "stint_idx"]).tail(1)
        out[team] = tuple(int(p) for p in last["players"][0])
    return out
