"""F9: the row-existence leak shape is explicit, values stay NULL, never zero."""

from __future__ import annotations

import polars as pl
import pytest

from nba.data.side_tables import join_side_table


def _box() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": ["g1"] * 3,
            "player_id": [1, 2, 3],
            "minutes": [30.0, 0.8, None],  # starter, garbage-time, DNP
        }
    )


def test_absent_side_row_is_null_not_zero_and_flagged() -> None:
    hustle = pl.DataFrame({"game_id": ["g1"], "player_id": [1], "deflections": [3.0]})
    out = join_side_table(_box(), hustle, ["game_id", "player_id"], flag="has_hustle").sort(
        "player_id"
    )
    assert out["has_hustle"].to_list() == [True, False, False]
    assert out["deflections"].to_list() == [
        3.0,
        None,
        None,
    ]  # a planted low-minute player: NaN, not 0
    assert out.height == 3


def test_flag_separates_presence_from_value_so_the_leak_is_visible() -> None:
    hustle = pl.DataFrame({"game_id": ["g1"] * 2, "player_id": [1, 2], "deflections": [0.0, 0.0]})
    out = join_side_table(_box(), hustle, ["game_id", "player_id"], flag="has_hustle")
    # a real zero (player 2 has a row with 0.0) is distinguishable from absence (player 3)
    assert out.filter(pl.col("player_id") == 2)["deflections"].to_list() == [0.0]
    assert out.filter(pl.col("player_id") == 3)["deflections"].to_list() == [None]


def test_duplicate_side_keys_raise() -> None:
    dup = pl.DataFrame({"game_id": ["g1", "g1"], "player_id": [1, 1], "x": [1.0, 2.0]})
    with pytest.raises(ValueError, match="duplicate keys"):
        join_side_table(_box(), dup, ["game_id", "player_id"], flag="f")
