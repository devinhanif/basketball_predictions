# ruff: noqa: E501
"""``ondemand<N>`` is a stored snapshot kind: accepted by the writer, rejected as a schedule entry, and
never mistaken for the close or a t60/t5 benchmark by the readers of ``live_book_odds``."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import duckdb
import pytest

from nba.forward import scorecard
from nba.odds import live_close as lc
from nba.pulls.freshness import newest_pinnacle

TIP = datetime(2026, 10, 20, 23, 30)
DAY = date(2026, 10, 20)


@pytest.mark.parametrize("kind", ["ondemand1", "ondemand12", "ondemand999"])
def test_ondemand_kinds_are_valid_stored_kinds(kind: str) -> None:
    assert lc.valid_kind(kind) and lc.is_ondemand_kind(kind) and not lc.valid_schedule_kind(kind)


@pytest.mark.parametrize(
    "kind", ["ondemand", "ondemand0x", "ondemand1000", "Ondemand1", "ondemand-1", ""]
)
def test_malformed_ondemand_kinds_are_rejected(kind: str) -> None:
    assert not lc.valid_kind(kind)


@pytest.mark.parametrize("kind", ["close", "morning", "t5", "t60", "t480"])
def test_schedule_kinds_are_unchanged(kind: str) -> None:
    assert lc.valid_kind(kind) and lc.valid_schedule_kind(kind) and not lc.is_ondemand_kind(kind)


def test_an_ondemand_kind_can_never_be_a_schedule_entry() -> None:
    with pytest.raises(ValueError):
        lc.ScheduleEntry("ondemand1", 0, 5)


def test_done_games_for_close_ignores_ondemand_rows(tmp_path) -> None:  # type: ignore[no-untyped-def]
    con = lc.open_store(tmp_path / "m.duckdb")
    con.execute(
        "INSERT INTO live_capture_log VALUES ('G1','ondemand1',NULL,NULL,NULL,'e','ok',1,14,'')"
    )
    assert lc.done_games(con, "close", 2) == set()
    assert lc.done_games(con, "t60", 2) == set()
    con.execute(
        "INSERT INTO live_capture_log VALUES ('G1','close',NULL,NULL,NULL,'e','ok',1,14,'')"
    )
    assert lc.done_games(con, "close", 2) == {"G1"}
    con.close()


def _lines_db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE live_book_odds (game_id VARCHAR, player_id INT, market VARCHAR, book VARCHAR, "
        "side VARCHAR, point DOUBLE, price_american INT, received_at TIMESTAMP, "
        "snapshot_kind VARCHAR, tipoff TIMESTAMP, slate_date DATE, requested_at TIMESTAMP)"
    )

    def add(
        kind: str, mins_before_tip: float, over: int, under: int, book: str = "pinnacle"
    ) -> None:
        t = TIP - timedelta(minutes=mins_before_tip)
        for side, price in (("over", over), ("under", under)):
            con.execute(
                "INSERT INTO live_book_odds VALUES ('G1', 7, 'player_points', ?, ?, 24.5, ?, ?, ?, ?, ?, ?)",
                [book, side, price, t, kind, TIP, DAY, t],
            )

    add("t60", 60, -110, -110)
    add("ondemand1", 30, -150, 125)
    add("close", 3, -120, 100)
    return con


def test_scorecard_close_wins_over_a_later_labelled_ondemand() -> None:
    con = _lines_db()
    out = scorecard.load_closing_lines(con, ["G1"], DAY)
    assert out[("G1", 7, "pts")]["kind"] == "close"
    book = scorecard.load_book_lines(con, ["G1"], DAY, "pinnacle")
    assert book[("G1", 7, "pts")]["kind"] == "close"


def test_scorecard_fallback_ignores_ondemand_rows() -> None:
    """Without a close the benchmark falls back to the latest scheduled snapshot, never JARVIS's pulls."""
    con = _lines_db()
    con.execute("DELETE FROM live_book_odds WHERE snapshot_kind = 'close'")
    out = scorecard.load_closing_lines(con, ["G1"], DAY)
    row = out[("G1", 7, "pts")]
    assert (
        row["kind"] == "latest_pre_tip" and abs(row["p_over_raw"] - 110 / 210) < 1e-9
    )  # the t60 price
    book = scorecard.load_book_lines(con, ["G1"], DAY, "pinnacle")
    assert book[("G1", 7, "pts")]["kind"] == "latest_pre_tip"
    assert abs(book[("G1", 7, "pts")]["p_over_raw"] - 110 / 210) < 1e-9
    con.execute("DELETE FROM live_book_odds WHERE snapshot_kind = 't60'")
    assert (
        scorecard.load_closing_lines(con, ["G1"], DAY) == {}
    )  # only an ondemand row: no line at all
    assert scorecard.load_book_lines(con, ["G1"], DAY, "pinnacle") == {}


def test_freshness_takes_the_newest_row_of_any_kind() -> None:
    con = _lines_db()
    now = TIP - timedelta(minutes=10)
    got = newest_pinnacle(con, ["G1", "G2"], now)
    assert got["G1"].kind == "ondemand1" and "G2" not in got  # close (T-3) is after "now"
    later = newest_pinnacle(con, ["G1"], TIP - timedelta(minutes=1))
    assert later["G1"].kind == "close"
