from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from nba.ride.causal_five import causal_five, causal_prefix, prefix_len
from nba.ride.v2_gate import elapsed
from nba.ride.winprob import parse_clock

FIX = Path(__file__).parent / "fixtures" / "pbp_0022300010_h1.parquet"
STARTERS = {
    1610612738: [1627759, 1628369, 204001, 201950, 1628401],
    1610612751: [1629661, 1626220, 1627827, 1628969, 203915],
}


def load() -> tuple[pd.DataFrame, np.ndarray]:
    d = pd.read_parquet(FIX).sort_values("action_number", kind="stable").reset_index(drop=True)
    el = np.array([elapsed(int(r.period), parse_clock(str(r.clock))) for r in d.itertuples()])
    return d, el


def test_prefix_stops_at_first_event_after_t_even_if_a_later_one_is_earlier() -> None:
    el = np.array([0.0, 30.0, 61.0, 45.0, 50.0])  # the 4th and 5th events are logged late
    assert prefix_len(el, 60.0) == 2  # stops before 61, does not pick up 45 or 50
    assert int((el <= 60.0).sum()) == 4  # a "count of elapsed <= T" would see the future
    assert prefix_len(el, 100.0) == 5
    assert prefix_len(el, -1.0) == 0


def test_causal_five_identity_and_planted_future() -> None:
    d, el = load()
    for t in (300.0, 700.0, 800.0, 1000.0):
        full = causal_prefix(d, el, t)
        cut = d.iloc[: len(full)].copy()
        # plant a different future: shuffle the actors of every event after the cut
        future = d.copy()
        later = future.index >= len(full)
        future.loc[later, "player_id"] = 999
        a = causal_five(full, STARTERS)
        b = causal_five(causal_prefix(future, el, t), STARTERS)
        c = causal_five(cut, STARTERS)
        assert a == b == c
        assert all(len(v) == 5 for v in a.values())


def test_empty_prefix_returns_starters() -> None:
    d, _ = load()
    assert causal_five(d.iloc[:0], STARTERS) == {k: tuple(v) for k, v in STARTERS.items()}
