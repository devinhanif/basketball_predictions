"""Synthetic tests for the TEAM_USAGE_CAP gate helpers (no database, no outcomes)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.eval import team_usage_cap_gate as gate


def _box() -> pd.DataFrame:
    rows = []
    # player 1: team 10 for three games, then team 20 for three games
    for i, (t, d) in enumerate([(10, 1), (10, 2), (10, 3), (20, 4), (20, 5), (20, 6)]):
        rows.append((f"g{i}", 1, t, 30.0 + i, pd.Timestamp(2023, 1, d), 2023))
    return pd.DataFrame(
        rows, columns=["game_id", "player_id", "team_id", "minutes", "game_date", "season"]
    )


def test_since_change_counts_appearances_before_the_game() -> None:
    app = gate.asof_tables(_box())
    sc = app.set_index("game_id")["since_change_before"]
    assert np.isnan(sc["g0"])  # no earlier appearance
    assert sc["g1"] == 1 and sc["g2"] == 2
    assert sc["g3"] == 0  # first game after the change
    assert sc["g4"] == 1 and sc["g5"] == 2


def test_rolling_minutes_use_only_earlier_games() -> None:
    app = gate.asof_tables(_box()).set_index("game_id")
    assert np.isnan(app.loc["g1", "m10_before"])  # fewer than 3 earlier games
    assert app.loc["g3", "m10_before"] == np.mean([30.0, 31.0, 32.0])
    # planted future: changing the last game's minutes cannot move any earlier row
    b = _box()
    b.loc[b.game_id == "g5", "minutes"] = 99.0
    app2 = gate.asof_tables(b).set_index("game_id")
    assert (app2["m10_before"].iloc[:5].fillna(-1) == app["m10_before"].iloc[:5].fillna(-1)).all()


def test_run_started_with_change() -> None:
    before = pd.DataFrame({"team_id": [10, 10, 20, 20]})
    assert gate._run_started_with_change(before)
    assert not gate._run_started_with_change(pd.DataFrame({"team_id": [10, 10, 10]}))
