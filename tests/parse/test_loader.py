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


def test_load_possessions_round_trips_rebound_counts_and_the_invariant() -> None:
    """A parsed game stores n_oreb / n_dreb, and oreb is exactly n_oreb > 0."""
    from tests.parse.helpers import AWAY, HOME, PbpBuilder
    from tests.parse.test_ordering import _rebound, _shot

    b = PbpBuilder()
    _shot(b, 1, "PT10M00.00S", HOME, 1, False)
    _rebound(b, 1, "PT09M58.00S", HOME, 2)  # offensive
    _shot(b, 1, "PT09M56.00S", HOME, 2, False)
    _rebound(b, 1, "PT09M54.00S", HOME, 3)  # offensive
    _shot(b, 1, "PT09M52.00S", HOME, 3, False)
    _rebound(b, 1, "PT09M50.00S", AWAY, 11)  # defensive: ends the trip
    _shot(b, 1, "PT09M40.00S", AWAY, 11, True, score_home="0", score_away="2")
    _shot(b, 1, "PT09M20.00S", HOME, 1, False, score_home="0", score_away="2")
    poss = parse_possessions(b.df())
    con = connect(":memory:")
    load_possessions(con, poss)
    load_possessions(con, poss)  # idempotent
    got = con.execute(
        "SELECT off_team, oreb, n_oreb, n_dreb FROM possessions ORDER BY poss_idx"
    ).fetchall()
    bad = con.execute("SELECT count(*) FROM possessions WHERE oreb != (n_oreb > 0)").fetchone()
    con.close()
    assert got == [(HOME, True, 2, 1), (AWAY, False, 0, 0), (HOME, False, 0, 0)]
    assert bad is not None and bad[0] == 0
