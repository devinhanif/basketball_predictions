"""Star-out cut-offs use only the declared seasons and non-empty OUT sets."""

from __future__ import annotations

import polars as pl

from research.eval import wowy_struct_star_cuts as SC


def test_star_cuts_ignore_other_seasons_and_empty_out_sets() -> None:
    df = pl.DataFrame(
        {
            "season": [2020, 2020, 2021, 2021, 2022, 2020],
            "has_report": [1, 1, 1, 1, 1, 0],
            "n_out_rot": [1, 1, 2, 0, 1, 1],
            "vac_pts": [10.0, 20.0, 30.0, 999.0, 999.0, 999.0],
            "vac_ast": [1.0, 2.0, 3.0, 99.0, 99.0, 99.0],
        }
    )
    r = SC.star_cuts(df)
    assert r["n_rows"] == 3 and r["V_pts"] == 25.0 and r["V_ast"] == 2.5
