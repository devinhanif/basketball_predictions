"""Unit test for the COLDSTART_SHAREBALL_T15 gate helper `vacated` (synthetic, no database)."""

import pandas as pd

from research.eval.coldstart_shareball_gate import vacated


def test_vacated_splits_confirmed_and_unknown_departures() -> None:
    games = 25
    reg = pd.DataFrame(
        {
            "player_id": [1] * games + [2] * games + [3] * games,
            "team_id": [10] * (3 * games),
            "season": [2022] * (3 * games),
            "minutes": [30.0] * games + [20.0] * games + [10.0] * games,
        }
    )
    # 2023 preseason: player 1 moved to team 11, player 2 nowhere, player 3 stays
    pre = pd.DataFrame({"player_id": [1, 3], "team_id": [11, 10], "season": [2023, 2023]})
    v = vacated(reg, pre, 2023)
    row = v[v.team_id == 10].iloc[0]
    assert row.V_confirmed == 30.0
    assert row.V_all == 50.0  # player 3 (10 mpg < 15) is not a departure anyway and is on the log
    assert row.n_gone == 2 and row.n_confirmed == 1
