"""Schema 2 of the nightly export: posted lines and book prices, team ids, steals and blocks.

The export (app/fixtures/export_small) was built by the real `python -m nba.export build` with
`--odds-db` from a SYNTHETIC capture store: invented player ids, invented prices, dates in
Oct-Nov 2024. Where a test needs a damaged export it edits a private copy and reseals it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from conftest import EXPORT_DAY, EXPORT_SMALL, edit_db
from fastapi.testclient import TestClient

from parlayhitters_api.export_store import ExportRefused, ExportStore, line_entries, main_line
from parlayhitters_api.main import create_app, get_store, reset_store

STAR = 990001
SIDE_KICK_TEAM_PLAYER = 990005  # fanduel prices him a point higher than draftkings


def _db() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(EXPORT_SMALL / "export.duckdb"), read_only=True)


def _tonight(c: TestClient) -> dict[str, Any]:
    out: dict[str, Any] = c.get(f"/api/slates/{EXPORT_DAY}").json()["games"][0]
    return out


def _star_gid(c: TestClient) -> str:
    for g in c.get(f"/api/slates/{EXPORT_DAY}").json()["games"]:
        names = [p["player_id"] for p in c.get(f"/api/games/{g['game_id']}").json()["players"]]
        if STAR in names:
            return str(g["game_id"])
    raise AssertionError("the star is not on tonight's slate")


def _quote(book: str, side: str, line: float, price: int | None, alt: bool = False,
           unusable: bool = False) -> dict[str, Any]:  # fmt: skip
    return {"book": book, "line": line, "side": side, "price": price, "unusable": unusable,
            "reason": None, "alt": alt, "minutes_before_tip": 200.0,
            "snapshot_utc": "2024-11-12T20:40:00Z"}  # fmt: skip


# ------------------------------------------------------------------- the posted main line


def test_main_line_is_the_one_most_books_carry() -> None:
    qs = [_quote(b, "over", 24.5, -110) for b in ("draftkings", "fanduel", "betmgm")]
    qs += [_quote("kalshi", "over", 25.5, -105)]
    assert main_line(qs) == 24.5


def test_a_tie_goes_to_the_line_nearest_the_median_then_the_lower() -> None:
    qs = [_quote("draftkings", "over", 24.5, -110), _quote("fanduel", "over", 25.5, -110)]
    assert main_line(qs) == 24.5
    qs += [_quote("betmgm", "over", 26.5, -110)]  # three lines, one book each: the middle one
    assert main_line(qs) == 25.5


def test_alternate_and_unusable_quotes_never_make_the_main_line() -> None:
    qs = [_quote("novig", "over", 30.5, 900, alt=True) for _ in range(3)]
    qs += [_quote("polymarket", "under", 20.5, None, unusable=True)] * 4
    qs += [_quote("draftkings", "over", 24.5, -110)]
    assert main_line(qs) == 24.5
    assert main_line(qs[:-1]) is None  # nothing usable and main: no posted line at all


def test_line_entries_prefer_one_book_with_both_sides_and_keep_alt_lines() -> None:
    qs = [
        _quote("draftkings", "over", 24.5, -115),
        _quote("draftkings", "under", 24.5, -105),
        _quote("fanduel", "over", 24.5, -110),
        _quote("fanduel", "under", 24.5, -110),
        _quote("novig", "over", 28.5, 450, alt=True),
    ]
    e = {x["point"]: x for x in line_entries(qs, 24.5)}
    assert e[24.5]["main"] and e[24.5]["over_book"] == e[24.5]["under_book"] == "draftkings"
    assert (e[24.5]["over_price"], e[24.5]["under_price"]) == (-115, -105)
    assert not e[28.5]["main"] and e[28.5]["over_price"] == 450 and e[28.5]["under_price"] is None


# ------------------------------------------------------------------------- loading schema 2


def test_schema_1_and_2_both_load_and_3_does_not(export_dir: Path) -> None:
    assert ExportStore(export_dir).manifest["schema_version"] == 2
    from conftest import downgrade_to_v1, seal

    downgrade_to_v1(export_dir)
    assert ExportStore(export_dir).manifest["schema_version"] == 1
    seal(export_dir, schema_version=3)
    with pytest.raises(ExportRefused, match="schema_version"):
        ExportStore(export_dir)


def test_a_schema_2_export_without_its_lines_table_is_refused(export_dir: Path) -> None:
    edit_db(export_dir, "DROP TABLE prop_lines")
    with pytest.raises(ExportRefused, match="prop_lines"):
        ExportStore(export_dir)


def test_meta_describes_lines_from_books_and_exchanges(export_client: TestClient) -> None:
    m = export_client.get("/api/meta").json()
    text = " ".join(m["stubs"])
    assert "Kalshi" in text and "not usable" in text and "points, rebounds, assists" in text
    assert "no sportsbook lines" not in text
    assert m["export"]["row_counts"]["prop_lines"] > 0


def test_an_export_with_no_quotes_says_why(export_dir: Path, export_client: TestClient) -> None:
    edit_db(export_dir, "DELETE FROM prop_lines")
    stubs = " ".join(export_client.get("/api/meta").json()["stubs"])
    assert "no sportsbook lines" in stubs
    g = _tonight(export_client)
    prop = export_client.get(f"/api/games/{g['game_id']}").json()["players"][0]["props"]["pts"]
    assert prop["line_source"] == "reference" and prop["over_price"] is None


# --------------------------------------------------------------- lines on the game and slate


def test_props_carry_the_posted_line_and_one_books_prices(export_client: TestClient) -> None:
    gid = _star_gid(export_client)
    star = next(
        p
        for p in export_client.get(f"/api/games/{gid}").json()["players"]
        if p["player_id"] == STAR
    )
    for stat, prop in star["props"].items():
        assert prop["line_source"] == "posted" and prop["price_book"] == "draftkings", stat
        assert prop["over_price"] <= -100 and prop["under_price"] <= -100
        assert 0 < prop["market_p_over"] < 1
    with _db() as con:
        run = con.execute(
            "SELECT run_id FROM prop_grids WHERE game_id = ? AND player_id = ? AND stat = 'pts' "
            "AND is_latest",
            [gid, STAR],
        ).fetchone()
        assert run is not None
        dk = con.execute(
            "SELECT line, price_american FROM prop_lines WHERE run_id = ? AND game_id = ? AND "
            "player_id = ? AND stat = 'pts' AND book = 'draftkings' AND side = 'over' "
            "AND NOT is_alternate",
            [run[0], gid, STAR],
        ).fetchone()
    assert dk is not None
    assert (star["props"]["pts"]["line"], star["props"]["pts"]["over_price"]) == dk
    assert star["props"]["pts"]["line_snapshot_utc"].endswith("Z")


def test_the_latest_run_is_priced_with_its_own_quotes_not_an_older_ones(
    export_client: TestClient,
) -> None:
    """Tonight has two runs; the T-30 run saw the T-60 re-quote (drift +5 on the over)."""
    gid = _star_gid(export_client)
    star = next(
        p
        for p in export_client.get(f"/api/games/{gid}").json()["players"]
        if p["player_id"] == STAR
    )
    with _db() as con:
        rows = con.execute(
            "SELECT snapshot_kind, price_american FROM prop_lines WHERE game_id = ? AND "
            "player_id = ? AND stat = 'pts' AND book = 'draftkings' AND side = 'over' "
            "AND NOT is_alternate ORDER BY made_at DESC",
            [gid, STAR],
        ).fetchall()
    assert rows[0][0] == "t60"  # the newest run's quote
    assert star["props"]["pts"]["over_price"] == rows[0][1]


def test_when_books_disagree_the_posted_line_is_the_majority(export_client: TestClient) -> None:
    gid = next(
        g["game_id"]
        for g in export_client.get(f"/api/slates/{EXPORT_DAY}").json()["games"]
        if SIDE_KICK_TEAM_PLAYER
        in [
            p["player_id"]
            for p in export_client.get(f"/api/games/{g['game_id']}").json()["players"]
        ]
    )
    prop = next(
        p
        for p in export_client.get(f"/api/games/{gid}").json()["players"]
        if p["player_id"] == SIDE_KICK_TEAM_PLAYER
    )["props"]["pts"]
    with _db() as con:
        lines = {
            b: ln
            for b, ln in con.execute(
                "SELECT book, line FROM prop_lines WHERE game_id = ? AND player_id = ? AND "
                "stat = 'pts' AND NOT is_alternate AND side = 'over' AND made_at = "
                "(SELECT max(made_at) FROM prop_lines WHERE game_id = ? AND player_id = ?)",
                [gid, SIDE_KICK_TEAM_PLAYER, gid, SIDE_KICK_TEAM_PLAYER],
            ).fetchall()
        }
    assert lines["fanduel"] == lines["draftkings"] + 1  # the disagreement exists in the data
    assert prop["line"] == lines["draftkings"] and prop["price_book"] == "draftkings"


def test_key_props_and_the_slate_show_the_posted_line(export_client: TestClient) -> None:
    g = _tonight(export_client)
    for kp in g["key_props"]:
        assert kp["line_source"] == "posted" and kp["over_price"] is not None
        assert 0 < kp["market_p_over"] < 1


def test_all_quotes_unusable_falls_back_to_a_reference_line(
    export_dir: Path, export_client: TestClient
) -> None:
    edit_db(
        export_dir,
        f"UPDATE prop_lines SET unusable = true, unusable_reason = 'x' WHERE player_id = {STAR} "
        "AND stat = 'pts'",
    )
    gid = _star_gid(export_client)
    star = next(
        p
        for p in export_client.get(f"/api/games/{gid}").json()["players"]
        if p["player_id"] == STAR
    )
    assert star["props"]["pts"]["line_source"] == "reference"
    assert star["props"]["pts"]["over_price"] is None
    assert star["props"]["reb"]["line_source"] == "posted"  # other stats are untouched


# ----------------------------------------------------------------- unusable and alt prices


def test_an_unusable_quote_is_never_shown_as_a_price(export_client: TestClient) -> None:
    gid = _star_gid(export_client)
    star_line = next(
        p
        for p in export_client.get(f"/api/games/{gid}").json()["players"]
        if p["player_id"] == STAR
    )["props"]["pts"]["line"]
    raw = export_client.get(f"/api/breakdown/{gid}/{STAR}/pts", params={"line": star_line})
    b = raw.json()
    books = {x["book"]: x for x in b["prices"]["books"]}
    assert {"draftkings", "fanduel", "polymarket"} <= set(books)
    pm = books["polymarket"]
    assert pm["over_price"] == 120 and not pm["over_unusable"]
    assert pm["under_price"] is None and pm["under_unusable"] is True
    assert "-3233" not in raw.text
    assert "-3233" not in export_client.get(f"/api/games/{gid}").text
    # the price on screen is a real book's pair, never the placeholder
    assert b["prices"]["over_price"] is not None and b["prices"]["under_price"] is not None
    assert "DraftKings" in b["prices"]["say"]


def test_exchange_books_are_listed_with_their_own_names(export_client: TestClient) -> None:
    gid = _star_gid(export_client)
    with _db() as con:
        kalshi_line = con.execute(
            "SELECT line FROM prop_lines WHERE game_id = ? AND player_id = ? AND stat = 'pts' "
            "AND book = 'kalshi' AND side = 'over'",
            [gid, STAR],
        ).fetchone()
    assert kalshi_line is not None
    b = export_client.get(
        f"/api/breakdown/{gid}/{STAR}/pts", params={"line": kalshi_line[0]}
    ).json()
    assert "kalshi" in {x["book"] for x in b["prices"]["books"]}


def test_alternate_ladder_prices_appear_at_their_lines(export_client: TestClient) -> None:
    gid = _star_gid(export_client)
    with _db() as con:
        alt = con.execute(
            "SELECT line, price_american FROM prop_lines WHERE game_id = ? AND player_id = ? "
            "AND stat = 'pts' AND book = 'novig' AND is_alternate AND made_at = "
            "(SELECT max(made_at) FROM prop_lines WHERE game_id = ? AND player_id = ?) "
            "ORDER BY line",
            [gid, STAR, gid, STAR],
        ).fetchall()
    assert len(alt) == 2
    line, price = alt[0]
    b = export_client.get(f"/api/breakdown/{gid}/{STAR}/pts", params={"line": line}).json()
    row = next(r for r in b["ladder"] if r["line"] == line)
    assert row["over_price"] == price and row["under_price"] is None
    novig = next(x for x in b["prices"]["books"] if x["book"] == "novig")
    assert novig["alternate"] is True and novig["over_price"] == price
    assert b["prices"]["on_posted_line"] is False and b["prices"]["posted_line"] is not None


def test_lines_exist_only_for_the_four_forecast_stats(export_client: TestClient) -> None:
    gid = _star_gid(export_client)
    b = export_client.get(f"/api/breakdown/{gid}/{STAR}/stl").json()
    assert b["prices"]["posted_line"] is None and b["prices"]["books"] == []
    assert b["model"]["available"] is False
    for stat in ("pts", "reb", "ast", "fg3m"):
        assert (
            export_client.get(f"/api/breakdown/{gid}/{STAR}/{stat}").json()["prices"]["posted_line"]
            is not None
        )


def test_breakdown_starts_at_the_posted_line(export_client: TestClient) -> None:
    gid = _star_gid(export_client)
    prop = next(
        p
        for p in export_client.get(f"/api/games/{gid}").json()["players"]
        if p["player_id"] == STAR
    )["props"]["pts"]
    b = export_client.get(f"/api/breakdown/{gid}/{STAR}/pts").json()
    assert b["line"] == prop["line"] == b["prices"]["posted_line"]
    assert b["line_source"] == "posted"
    assert "reference line" not in b["dissection"]["text"] and b["dissection"]["guard_ok"]


# ---------------------------------------------------------------------------- swipe and results


def _live_client(directory: Path, now: datetime, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("APP_EXPORT_DIR", str(directory))
    reset_store()
    app = create_app()
    app.dependency_overrides[get_store] = lambda: ExportStore(directory, now=lambda: now)
    return TestClient(app)


def test_the_swipe_deck_is_made_of_posted_lines_before_tip(
    export_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = _live_client(export_dir, datetime(2024, 11, 12, 20, 0, tzinfo=UTC), monkeypatch)
    deck = c.get(f"/api/slates/{EXPORT_DAY}/swipe", params={"seed": 3, "n": 60}).json()
    assert len(deck["cards"]) == 60
    for card in deck["cards"]:
        assert card["book"] == "draftkings" and card["line"] % 1 == 0.5
        assert card["over_price"] <= -100 and card["under_price"] <= -100
        assert set(card) == {
            "prop_id", "game_id", "player_id", "player", "team", "opponent", "stat", "line",
            "over_price", "under_price", "book", "tip_utc",
        }  # fmt: skip
    # after tip nothing is dealt
    late = _live_client(export_dir, datetime(2024, 11, 13, 1, 0, tzinfo=UTC), monkeypatch)
    assert late.get(f"/api/slates/{EXPORT_DAY}/swipe").json()["cards"] == []


def test_settled_rows_carry_the_line_the_price_and_the_actual(export_client: TestClient) -> None:
    res = export_client.get("/api/slates/2024-11-11/results").json()
    assert res["rows"], "played games with posted lines settle"
    for r in res["rows"]:
        assert r["actual"] is not None and 0 < r["market_p_over"] < 1
        assert r["over_price"] <= -100


# ------------------------------------------------------------------ team ids, steals, blocks


def test_the_exporters_team_id_places_a_player(export_dir: Path, export_client: TestClient) -> None:
    gid = _star_gid(export_client)
    g = export_client.get(f"/api/slates/{EXPORT_DAY}").json()["games"][0]
    before = next(
        p
        for p in export_client.get(f"/api/games/{gid}").json()["players"]
        if p["player_id"] == STAR
    )
    other = g["home"]["id"] if before["side"] == "away" else g["away"]["id"]
    edit_db(
        export_dir,
        f"UPDATE prop_grids SET team_id = {other}, team_source = 'box' WHERE player_id = {STAR} "
        f"AND game_id = '{gid}'",
    )
    after = next(
        p
        for p in export_client.get(f"/api/games/{gid}").json()["players"]
        if p["player_id"] == STAR
    )
    assert after["side"] != before["side"]


def test_a_forecast_player_with_no_team_anywhere_is_counted_not_guessed(
    export_dir: Path, export_client: TestClient
) -> None:
    gid = _star_gid(export_client)
    edit_db(
        export_dir,
        f"UPDATE prop_grids SET team_id = NULL WHERE player_id = {STAR}",
        f"DELETE FROM box_scores WHERE player_id = {STAR}",
        f"DELETE FROM injury_report WHERE player_id = {STAR}",
    )
    names = [p["player_id"] for p in export_client.get(f"/api/games/{gid}").json()["players"]]
    assert STAR not in names
    assert "could not be placed" in " ".join(export_client.get("/api/meta").json()["stubs"])


def test_steals_and_blocks_are_counted_from_the_box(export_client: TestClient) -> None:
    gid = _star_gid(export_client)
    b = export_client.get(f"/api/breakdown/{gid}/{STAR}/stocks").json()
    assert [t["stat"] for t in b["tabs"]][-3:] == ["blk", "stl", "stocks"] or {
        "blk",
        "stl",
        "stocks",
    } <= {t["stat"] for t in b["tabs"]}
    with _db() as con:
        rows = con.execute(
            "SELECT stl + blk FROM box_scores WHERE player_id = ? AND minutes > 0 "
            "AND game_date < DATE '2024-11-12' ORDER BY game_date DESC, game_id DESC",
            [STAR],
        ).fetchall()
    assert [c["value"] for c in b["chips"]] == [r[0] for r in rows[:10]]
    assert b["model"]["available"] is False  # no forecast for combos or for steals and blocks


def test_a_game_with_null_steals_is_left_out_not_counted_as_zero(
    export_dir: Path, export_client: TestClient
) -> None:
    gid = _star_gid(export_client)
    with _db() as con:
        newest = con.execute(
            "SELECT game_id FROM box_scores WHERE player_id = ? AND minutes > 0 AND "
            "game_date < DATE '2024-11-12' ORDER BY game_date DESC LIMIT 1",
            [STAR],
        ).fetchone()
    assert newest is not None
    edit_db(
        export_dir,
        f"UPDATE box_scores SET stl = NULL WHERE player_id = {STAR} AND game_id = '{newest[0]}'",
    )
    stl = export_client.get(f"/api/breakdown/{gid}/{STAR}/stl").json()
    stocks = export_client.get(f"/api/breakdown/{gid}/{STAR}/stocks").json()
    pts = export_client.get(f"/api/breakdown/{gid}/{STAR}/pts").json()
    assert newest[0] not in {c["game_id"] for c in stl["chips"]}
    assert newest[0] not in {c["game_id"] for c in stocks["chips"]}
    assert newest[0] in {c["game_id"] for c in pts["chips"]}  # points still use that game
    assert stl["last10"]["n"] == 10 and stl["prior_games"] == pts["prior_games"] - 1
