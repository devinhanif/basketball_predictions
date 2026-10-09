"""Alias review builder: classification, confidence policy, read-only behaviour."""

from __future__ import annotations

from typing import Any

import duckdb

from nba.kalshi.alias_review import (
    PlayerRecord,
    build_name_index,
    build_review,
    classify_name,
    collect_kalshi_names,
    open_read_only,
    propose,
    rookie_candidates,
)

RECS = [
    PlayerRecord(1, "Jane Doe", True, "BOS", "2026-06-01", 50),
    PlayerRecord(2, "Nikola Jokić", True, "DEN", "2026-06-01", 70),
    PlayerRecord(3, "Twin Name", True, "NYK", "2026-06-01", 40),
    PlayerRecord(4, "Twin Name", False, None, None, 10),
    PlayerRecord(5, "Retired Guy", False, None, None, 400),
    PlayerRecord(6, "New Rookie", True, None, None, 0),
]


def test_classify_matched_unmatched_ambiguous() -> None:
    al = {"alan smith": 1, "alan smyth": 2}
    assert classify_name("Alan Smith", al)[0] == "matched"
    assert classify_name("Zed Q", al)[0] == "unmatched"
    assert classify_name("Alan Smit", al)[0] == "ambiguous"


def test_high_confidence_only_exact_unique_current() -> None:
    idx = build_name_index(RECS)
    assert propose("Jane Doe", idx)[0]["confidence"] == "high"
    # accent-normalized match is never high
    assert propose("Nikola Jokic", idx)[0]["confidence"] == "medium"
    # duplicate normalized name
    assert {p["confidence"] for p in propose("Twin Name", idx)} == {"low"}
    # unique but no latest-season games
    assert propose("Retired Guy", idx)[0]["confidence"] == "medium"
    assert propose("Nobody Here", idx) == []


def test_rookies_listing() -> None:
    out = rookie_candidates(RECS, static_ids=set(), flagged_ids=set())
    assert [r["player_id"] for r in out] == [6]
    assert rookie_candidates(RECS, {6}, set()) == []


def _kalshi_con() -> Any:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE kalshi_markets (ticker VARCHAR, series_ticker VARCHAR, title VARCHAR, "
        "player_id INT, settled_ts TIMESTAMP, close_time TIMESTAMP)"
    )
    con.execute(
        "INSERT INTO kalshi_markets VALUES "
        "('a','KXNBAPTS','Jane Doe: 10+ points',1,NULL,NULL),"
        "('b','KXNBAPTS','Zed Q: 10+ points',NULL,NULL,NULL),"
        "('c','KXNBAGAME','Boston wins',NULL,NULL,NULL)"
    )
    return con


def test_collect_names_ignores_non_prop_series() -> None:
    names = collect_kalshi_names(_kalshi_con())
    assert set(names) == {"Jane Doe", "Zed Q"}
    assert names["Jane Doe"].n_with_player_id == 1


def test_build_review_counts() -> None:
    names = collect_kalshi_names(_kalshi_con())
    rev = build_review(names, {"jane doe": 1}, RECS, set(), "t")
    c = rev["counts"]
    assert (c["matched"], c["unmatched"], c["ambiguous"]) == (1, 1, 0)
    assert rev["unmatched"][0]["proposals"] == []


def test_open_read_only_cannot_write(tmp_path: Any) -> None:
    p = tmp_path / "x.duckdb"
    duckdb.connect(str(p)).close()
    con = open_read_only(p)
    try:
        con.execute("CREATE TABLE t (a INT)")
        raise AssertionError("write succeeded")
    except duckdb.Error:
        pass
