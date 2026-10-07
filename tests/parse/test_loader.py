"""Tests for the idempotent possessions loader (nba/parse/loader.py)."""

from __future__ import annotations

import polars as pl

from nba.db.connect import connect
from nba.parse.loader import load_possessions
from nba.parse.possessions import parse_possessions
from tests.fixtures.loader import load_pbp


def test_load_possessions_is_idempotent() -> None:
    con = connect(":memory:")
    pbp = next(iter(load_pbp().values()))
    poss = parse_possessions(pbp)

    load_possessions(con, poss)
    count_1 = con.execute("SELECT COUNT(*) FROM possessions").fetchone()[0]
    assert count_1 == poss.height

    # Re-loading the same game must not duplicate rows (upsert on PK).
    load_possessions(con, poss)
    count_2 = con.execute("SELECT COUNT(*) FROM possessions").fetchone()[0]
    assert count_2 == count_1
    con.close()


def test_load_possessions_empty_frame_is_a_noop() -> None:
    con = connect(":memory:")
    empty = pl.DataFrame({"game_id": [], "poss_idx": [], "period": []})
    load_possessions(con, empty)  # must not raise
    count = con.execute("SELECT COUNT(*) FROM possessions").fetchone()[0]
    assert count == 0
    con.close()
