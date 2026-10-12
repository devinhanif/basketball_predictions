"""Parser behaviour for the markets added 2026-10-12: period markets, team totals, 3-way moneyline,
yes/no and first-basket player markets. Synthetic bodies in the vendor's documented shape; the
recorded real shapes are checked by the first live pull (raw is cached before parse)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from nba.odds.the_odds_api import parse_event_odds
from nba.odds.theoddsapi import NameResolver

HOME, AWAY = "Philadelphia 76ers", "New York Knicks"
RES = NameResolver(teams={}, players={"tyrese maxey": 1630178})
REQ = datetime(2026, 10, 20, 22, 30, tzinfo=UTC)


def o(name: str, price: int, point: float | None = None, desc: str | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"name": name, "price": price}
    if point is not None:
        d["point"] = point
    if desc is not None:
        d["description"] = desc
    return d


def body(markets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "timestamp": "2026-10-20T22:29:00Z",
        "data": {
            "id": "ev1",
            "home_team": HOME,
            "away_team": AWAY,
            "bookmakers": [{"key": "fanduel", "markets": markets}],
        },
    }


def parse(markets: list[dict[str, Any]]) -> Any:
    return parse_event_odds(
        body(markets), season=2026, game_id="G1", snapshot_kind="t60", requested_at=REQ,
        raw_file="r", resolver=RES,
    )  # fmt: skip


def test_team_totals_keep_both_teams_and_devig_per_team() -> None:
    res = parse(
        [
            {
                "key": "team_totals_q1",
                "outcomes": [
                    o("Over", -110, 28.5, HOME),
                    o("Under", -110, 28.5, HOME),
                    o("Over", -105, 28.5, AWAY),
                    o("Under", -125, 28.5, AWAY),
                ],  # fmt: skip
            }
        ]
    )
    df = res.rows
    assert df.height == 4 and res.reasons["duplicate_row_dropped"] == 0
    assert res.unresolved == {} and df["player_id"].null_count() == 4  # teams never hit players
    assert df["player_name"].null_count() == 4
    away = df.filter(df["outcome_name"].str.contains("New York"))
    assert sorted(away["side"].to_list()) == ["over", "under"]
    assert abs(float(away["implied_prob"].sum()) - 1.0) < 1e-9
    home = df.filter(df["outcome_name"].str.contains("Philadelphia"))
    assert all(abs(p - 0.5) < 1e-9 for p in home["implied_prob"].to_list())


def test_period_spread_devigs_across_signs_and_three_way_keeps_draw() -> None:
    res = parse(
        [
            {
                "key": "alternate_spreads_h1",
                "outcomes": [o(HOME, -140, -2.5), o(AWAY, 120, 2.5)],
            },
            {
                "key": "h2h_3_way_q1",
                "outcomes": [o(HOME, -130), o(AWAY, 220), o("Draw", 900)],
            },
        ]
    )
    df = res.rows
    sp = df.filter(df["market"] == "alternate_spreads_h1")
    assert sorted(sp["side"].to_list()) == ["away", "home"]
    assert sp["implied_prob"].null_count() == 0
    tw = df.filter(df["market"] == "h2h_3_way_q1")
    assert sorted(tw["side"].to_list()) == ["away", "draw", "home"]
    assert tw["implied_prob"].null_count() == 3  # three-way: no two-way de-vig claimed


def test_player_q1_alternate_yesno_and_first_basket_shapes() -> None:
    res = parse(
        [
            {
                "key": "player_points_q1",
                "outcomes": [
                    o("Over", -115, 7.5, "Tyrese Maxey"),
                    o("Under", -105, 7.5, "Tyrese Maxey"),
                ],
            },
            {
                "key": "player_double_double",
                "outcomes": [
                    o("Yes", 300, None, "Tyrese Maxey"),
                    o("No", -400, None, "Tyrese Maxey"),
                ],
            },
            {"key": "player_first_basket", "outcomes": [o("Tyrese Maxey", 1100)]},
            {
                "key": "player_method_of_first_basket",
                "outcomes": [o("Three Pointer", 250, None, "Tyrese Maxey")],
            },
            {"key": "player_points_alternate", "outcomes": [o("Over", 150, 30.5, "Tyrese Maxey")]},
        ]
    )
    df = res.rows
    assert df.height == 7 and res.reasons["unparsed_outcome"] == 0
    assert set(df["player_id"].to_list()) == {1630178}  # all resolved by the same alias table
    by = {m: df.filter(df["market"] == m) for m in df["market"].unique().to_list()}
    assert sorted(by["player_double_double"]["side"].to_list()) == ["no", "yes"]
    assert by["player_double_double"]["implied_prob"].null_count() == 0
    assert by["player_first_basket"]["side"].to_list() == ["yes"]
    assert by["player_method_of_first_basket"]["side"].to_list() == ["outcome"]
    assert by["player_method_of_first_basket"]["outcome_name"].to_list() == ["Three Pointer"]
    assert by["player_points_alternate"]["implied_prob"].null_count() == 1  # one-sided: no de-vig


def test_exchange_lay_market_is_kept_but_not_devigged() -> None:
    """Real preseason payload (2026-10-12): betfair_ex_eu returns h2h_lay next to h2h. A lay price
    is not the back price, so the pair must not be de-vigged as a back market."""
    res = parse([{"key": "h2h_lay", "outcomes": [o(HOME, -108), o(AWAY, 112)]}])
    df = res.rows
    assert df.height == 2
    assert set(df["market"]) == {"h2h_lay"}
    assert df["implied_prob"].null_count() == 2
    assert df["implied_prob_raw"].null_count() == 0
