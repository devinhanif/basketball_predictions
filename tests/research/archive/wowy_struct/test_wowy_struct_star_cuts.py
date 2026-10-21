"""Star-out cut-offs use only the declared rows."""

from __future__ import annotations

import polars as pl

from research.archive.wowy_struct import wowy_struct_star_cuts as SC


def test_heavy_cuts_ignore_other_seasons_and_empty_out_sets() -> None:
    df = pl.DataFrame(
        {
            "season": [2020, 2020, 2021, 2021, 2022, 2020],
            "has_report": [1, 1, 1, 1, 1, 0],
            "n_out_rot": [1, 1, 2, 0, 1, 1],
            "vac_pts": [10.0, 20.0, 30.0, 999.0, 999.0, 999.0],
            "vac_ast": [1.0, 2.0, 3.0, 99.0, 99.0, 99.0],
        }
    )
    r = SC.heavy_cuts(df)
    assert r["n_rows"] == 3 and r["V_pts"] == 25.0 and r["V_ast"] == 2.5


def test_player_cuts_are_per_player_game() -> None:
    pv = pl.DataFrame(
        {"st_pts": [0.0, 10.0, 20.0, 30.0, 40.0], "st_ast": [1.0, 2.0, 3.0, 4.0, 5.0]}
    )
    r = SC.player_cuts(pv)
    assert r["n_player_games"] == 5 and r["v_pts"] == 30.0 and r["v_ast"] == 4.0
