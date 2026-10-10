"""Parser tests on the recorded probe payloads and the hand-written live-shape fixtures."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import polars as pl

from nba.odds.theoddsapi import (
    ROW_SCHEMA,
    NameResolver,
    american_to_prob,
    parse_payload,
    read_envelope,
    to_american,
)
from tests.odds.conftest import probe_body, synthetic


def _sum(s: pl.Series) -> float:
    return float(s.sum())


CAP = datetime(2026, 10, 20, 20, 0, 5, tzinfo=UTC)
RES = NameResolver(
    teams={
        "san antonio spurs": 1610612759,
        "oklahoma city thunder": 1610612760,
        "la clippers": 1610612746,
        "new york knicks": 1610612752,
        "cleveland cavaliers": 1610612739,
    },
    players={"miles mcbride": 1630540},
)


def test_probe_envelopes() -> None:
    me = read_envelope(probe_body("me"))
    assert me.success and me.data["tier"] == "business" and me.data["daily_limit"] == 6667
    empty = read_envelope(probe_body("props_live"))
    assert not empty.success and empty.data is None and empty.empty
    assert empty.message and "out of season" in empty.message
    assert read_envelope("garbage").empty  # tolerant of a non-object body


def test_empty_and_out_of_season_feeds_parse_to_typed_empty_frame() -> None:
    for name in ("props_live", "hist_2023"):
        res = parse_payload(probe_body(name), CAP, RES)
        assert res.rows.height == 0
        assert dict(res.rows.schema) == ROW_SCHEMA
        assert res.reasons["empty_feed"] == 1
    assert parse_payload(None, CAP, RES).rows.height == 0  # never raises


def test_historical_flat_h2h_rows() -> None:
    res = parse_payload(probe_body("hist_2026_may"), CAP, RES)
    df = res.rows
    assert df.height == 5 and dict(df.schema) == ROW_SCHEMA
    assert df["source"].unique().to_list() == ["theoddsapi"]
    # captured_at is OUR clock (naive UTC); the book's stamp is kept separately
    assert df["captured_at"].unique().to_list() == [datetime(2026, 10, 20, 20, 0, 5)]
    assert df["source_updated_at"].null_count() == 0
    assert set(df["side"].to_list()) == {"home", "away"}
    kn = df.filter(pl.col("outcome_name") == "New York Knicks")
    assert kn["side"].unique().to_list() == ["home"]
    assert kn["outcome_team_id"].unique().to_list() == [1610612752]
    assert df["home_team_id"].unique().to_list() == [1610612752]
    # two-sided books are de-vigged to sum 1; fanduel has one side only -> NULL, raw kept
    for book in ("tipico_de", "pinnacle"):
        b = df.filter(pl.col("book") == book)
        assert b.height == 2 and abs(_sum(b["implied_prob"]) - 1.0) < 1e-12
        assert _sum(b["implied_prob_raw"]) > 1.0  # the vig
    fd = df.filter(pl.col("book") == "fanduel")
    assert fd["implied_prob"].null_count() == 1 and fd["implied_prob_raw"].null_count() == 0
    assert df["start_time"][0] == datetime(2026, 5, 20, 0, 10)


def test_historical_player_props_split_name_and_side() -> None:
    res = parse_payload(probe_body("hist_props_2026_jun"), CAP, RES)
    df = res.rows
    mc = df.filter(pl.col("player_name") == "Miles McBride").sort("side")
    assert mc["side"].to_list() == ["over", "under"]
    assert mc["point"].to_list() == [2.5, 2.5]
    assert mc["price_american"].to_list() == [-134, 111]
    assert mc["player_id"].unique().to_list() == [1630540]
    assert abs(_sum(mc["implied_prob"]) - 1.0) < 1e-12
    over = mc.filter(pl.col("side") == "over")["implied_prob"][0]
    assert over > 0.5
    # Mikal Bridges has one side in this 5-row page -> no de-vig; Josh Hart has both -> de-vigged
    others = df.filter(pl.col("player_name") != "Miles McBride")
    assert df.filter(pl.col("player_name") == "Mikal Bridges")["implied_prob"].null_count() == 1
    hart = df.filter(pl.col("player_name") == "Josh Hart")
    assert hart.height == 2 and abs(_sum(hart["implied_prob"]) - 1.0) < 1e-12
    # and NO guessed ids: RES only knows McBride
    assert others["player_id"].null_count() == others.height  # RES knows only McBride
    assert res.reasons["unresolved_player"] == others.height
    assert "player:Mikal Bridges" in res.unresolved


def test_live_shape_games_markets_and_unknown_shapes_skipped() -> None:
    res = parse_payload(synthetic("odds_live_synthetic"), CAP, RES)
    df = res.rows
    assert res.reasons["unknown_event_shape"] == 2  # a string and {"foo": 1}: logged, not raised
    spreads = df.filter(pl.col("market") == "spreads").sort("point")
    assert spreads["point"].to_list() == [-3.5, 3.5]
    assert spreads["side"].to_list() == ["home", "away"]
    assert abs(_sum(spreads["implied_prob"]) - 1.0) < 1e-12
    tot = df.filter(pl.col("market") == "totals")
    assert set(tot["side"].to_list()) == {"over", "under"}
    assert tot["player_name"].null_count() == tot.height
    assert tot.filter(pl.col("side") == "under")["implied_prob"][0] > 0.5
    dk = df.filter(pl.col("book") == "draftkings")
    assert dk.height == 1 and dk["implied_prob"][0] is None
    assert df["source_updated_at"].null_count() == 2  # the fanduel decimal-price rows had no stamp


def test_decimal_prices_converted_and_unknown_team_left_null() -> None:
    res = parse_payload(synthetic("odds_live_synthetic"), CAP, RES)
    fd = res.rows.filter(pl.col("book") == "fanduel").sort("price_american")
    assert fd["price_american"].to_list() == [-125, 110]  # 1.8 -> -125, 2.1 -> +110
    assert res.reasons["decimal_price_converted"] == 2
    # "LA Clippers" resolves through the LA alias; the fictional visitor does not (NULL, counted)
    assert fd["home_team_id"].unique().to_list() == [1610612746]
    assert fd["away_team_id"].null_count() == 2
    assert res.reasons["unresolved_team"] == 2
    assert "team:Brooklyn Nets Fictional" in res.unresolved
    assert fd["outcome_team_id"].null_count() == 1  # the visitor row only


def test_live_props_both_name_conventions_and_garbage() -> None:
    res = parse_payload(synthetic("props_live_synthetic"), CAP, RES)
    df = res.rows
    pts = df.filter(
        (pl.col("market") == "player_points") & (pl.col("player_name") == "Miles McBride")
    )
    reb = df.filter(pl.col("market") == "player_rebounds")
    # "Name Over" and {name: "Over", description: player} both land on the same fields
    assert pts.height == 2 and reb.height == 2
    assert reb["player_name"].unique().to_list() == ["Miles McBride"]
    assert sorted(reb["side"].to_list()) == ["over", "under"]
    assert reb["player_id"].unique().to_list() == [1630540]
    assert res.reasons["unparsed_prop_outcome"] == 1  # "garbage"
    nobody = df.filter(pl.col("player_name") == "Nobody Known")
    assert (
        nobody["player_id"].null_count() == 2
        and abs(_sum(nobody["implied_prob"]) - 2.0 * 0.5 * 1) < 1e-9
    )


def test_price_helpers() -> None:
    assert to_american(-134) == (-134, False)
    assert to_american(2.0) == (100, True)
    assert to_american(0) == (None, False) and to_american("x") == (None, False)
    assert to_american(True) == (None, False)
    assert abs(american_to_prob(-100) - 0.5) < 1e-12 and abs(american_to_prob(100) - 0.5) < 1e-12
    assert american_to_prob(-200) > 0.66 and american_to_prob(300) == 0.25


def test_bad_rows_are_counted_not_raised() -> None:
    body: dict[str, Any] = {
        "success": True,
        "data": [
            {"event_id": "x", "book": "b", "market": "h2h", "outcome_name": "A", "price": "n/a"},
            {"event_id": "x", "book": "b", "market": "totals", "outcome_name": "Over",
             "price": -110, "point": "abc"},
            {"book": "b", "market": "h2h", "outcome_name": "A", "price": -110},
            {"event_id": "y", "home_team": "A", "books": [None, {"book": "q"}]},
        ],
    }  # fmt: skip
    res = parse_payload(body, CAP, RES)
    assert res.rows.height == 0
    assert res.reasons["bad_price"] == 1 and res.reasons["bad_point"] == 1
    assert res.reasons["missing_field"] == 1 and res.reasons["unknown_book_shape"] == 2


def test_recorded_live_odds_response_2026_10_10() -> None:
    """A real preseason /odds/ page (one event, 13 book-market blocks) in the live shape."""
    res = parse_payload(probe_body("odds_live_recorded"), CAP, NameResolver.default())
    df = res.rows
    assert df.height == 26
    assert not res.reasons  # nothing skipped, nothing unknown, every team name resolved
    assert df["home_team_id"].null_count() == 0 and df["away_team_id"].null_count() == 0
    assert set(df["market"].unique().to_list()) == {"h2h", "spreads", "totals"}
    assert df["implied_prob"].null_count() == 0  # every block is two-sided
    for _, g in df.group_by(["book", "market"]):
        assert abs(_sum(g["implied_prob"]) - 1.0) < 1e-12
    assert df["source_updated_at"].null_count() == 0
    assert df.unique(subset=["book", "market", "outcome_name", "point"]).height == df.height
