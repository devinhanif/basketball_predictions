from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path

import duckdb
import polars as pl
import pytest

from nba.facts.build import alias_entities, build
from nba.facts.query import PostTipError, as_of, known_before_tip
from tests.facts.conftest import G1, G2, G_HOLD, TIP1, make_lineups, make_source


def _one(con, sql, *params):
    return con.execute(sql, list(params)).fetchall()


def test_schema_creates_tables_and_indexes(tmp_path: Path):
    from nba.facts.build import SCHEMA_PATH

    con = duckdb.connect(":memory:")
    con.execute(SCHEMA_PATH.read_text())
    assert {r[0] for r in con.execute("SHOW TABLES").fetchall()} == {"entities", "facts"}
    idx = {r[0] for r in con.execute("SELECT index_name FROM duckdb_indexes()").fetchall()}
    assert idx == {"facts_subject_pred_known", "facts_pred_known"}


def test_tips_and_known_at(built):
    con, _ = built
    # schedule publish proxy: 00:00 ET the day before (EDT = UTC-4) -> 04:00 UTC
    rows = _one(
        con,
        "SELECT valid_from, known_at, source FROM facts "
        "WHERE subject = ? AND predicate = 'tips_at' ORDER BY known_at",
        f"game:{G1}",
    )
    assert rows[0] == (TIP1, dt.datetime(2022, 10, 18, 4, 0), "schedule")
    assert rows[1][2] == "lineups_game_tips" and rows[1][1] == dt.datetime(2022, 10, 19, 13, 0)


def test_listed_status_uses_report_time_in_utc_and_official_source_only(built):
    con, _ = built
    rows = _one(
        con,
        "SELECT subject, object, value_text, known_at, source FROM facts "
        "WHERE predicate = 'listed_status' ORDER BY subject",
    )
    assert rows == [
        ("player:1", f"game:{G1}", "out", dt.datetime(2022, 10, 19, 15, 0), "official_report"),
        (
            "player:2",
            f"game:{G1}",
            "questionable",
            dt.datetime(2022, 10, 19, 21, 45),
            "official_report",
        ),
    ]


def test_holdout_season_is_never_loaded(built):
    con, _ = built
    assert _one(
        con,
        "SELECT count(*) FROM facts WHERE subject LIKE ? OR object LIKE ?",
        f"game:{G_HOLD}",
        f"game:{G_HOLD}",
    ) == [(0,)]
    assert _one(con, "SELECT count(*) FROM facts WHERE object = 'coach:502'") == [(0,)]


def test_static_coach_and_minutes_facts(built):
    con, counts = built
    pos = _one(
        con,
        "SELECT value_text, known_at FROM facts WHERE subject='player:1' AND predicate='position'",
    )
    assert pos == [("G", dt.datetime(2019, 10, 1))]
    assert _one(con, "SELECT count(*) FROM facts WHERE subject='player:3'") == [
        (0,)
    ]  # no first_season
    coach = _one(con, "SELECT object, confidence, valid_to FROM facts WHERE predicate='head_coach'")
    assert coach == [("coach:500", 0.5, dt.datetime(2023, 10, 1))]
    mins = _one(
        con,
        "SELECT subject, value, value_text, known_at FROM facts "
        "WHERE predicate='played_minutes' ORDER BY subject",
    )
    end = TIP1 + dt.timedelta(hours=2, minutes=30)
    assert mins == [("player:1", None, "DNP", end), ("player:2", 31.5, None, end)]
    assert counts["predicate"]["played_minutes"] == 2
    assert _one(con, "SELECT canonical_name FROM entities WHERE entity_id='coach:500'") == [
        ("Ann Coach",)
    ]


def test_every_fact_has_a_source(built):
    con, _ = built
    assert _one(con, "SELECT count(*) FROM facts WHERE source IS NULL OR known_at IS NULL") == [
        (0,)
    ]


def test_rebuild_is_idempotent(tmp_path: Path, schedule_dir: Path):
    out = tmp_path / "f.duckdb"
    kw = dict(schedule_dir=schedule_dir, odds_aliases=tmp_path / "a", kalshi_aliases=tmp_path / "b")
    c1 = build(make_source(), make_lineups(), out, **kw)
    c2 = build(make_source(), make_lineups(), out, **kw)
    assert c1 == c2


def test_since_filters_games(tmp_path: Path, schedule_dir: Path):
    out = tmp_path / "f.duckdb"
    c = build(
        make_source(),
        None,
        out,
        schedule_dir=schedule_dir,
        since="2022-10-20",
        odds_aliases=tmp_path / "a",
        kalshi_aliases=tmp_path / "b",
    )
    assert "listed_status" not in c["predicate"]  # only G1 had official rows
    assert c["predicate"]["tips_at"] == 1


def test_alias_entities(tmp_path: Path):
    o, k = tmp_path / "o.yaml", tmp_path / "k.yaml"
    o.write_text("aliases:\n  jalen brunson: 5\n  nic claxton: 6\n")
    k.write_text('aliases:\n  "Jalen Brunson": 5\n')
    ents = alias_entities(o, k)
    assert ents["player:5"][0] == "Jalen Brunson"
    assert ents["player:5"][1] == {
        "odds_aliases": ["jalen brunson"],
        "kalshi_aliases": ["Jalen Brunson"],
    }
    assert ents["player:6"][0] == "nic claxton"


def test_as_of_excludes_facts_known_after_t(built):
    con, _ = built
    t = dt.datetime(2022, 10, 19, 16, 0)
    df = as_of(con, G1, t)
    assert df["known_at"].max() <= t
    statuses = df.filter(pl.col("predicate") == "listed_status")
    assert statuses["subject"].to_list() == ["player:1"]  # player 2's report is 21:45 UTC
    # roster comes only from facts known by t: player 2 is absent entirely, statics included
    assert "player:2" not in set(df["subject"])
    assert {"player:1", f"game:{G1}", "team:10"} <= set(df["subject"])
    assert set(df.filter(pl.col("predicate") == "away_team")["object"]) == {"team:20"}
    assert "head_coach" in set(df["predicate"])  # team 10, season 2022 window contains the tip


def test_as_of_is_game_scoped(built):
    con, _ = built
    df = as_of(con, G2, dt.datetime(2022, 10, 20, 12, 0))
    # player 1's report for G1 and G2's other-source row must not leak into G2
    assert df.filter(pl.col("predicate") == "listed_status").height == 0


def test_post_tip_refused_unless_allowed(built):
    con, _ = built
    late = TIP1 + dt.timedelta(minutes=1)
    with pytest.raises(PostTipError):
        as_of(con, G1, late)
    assert as_of(con, G1, TIP1).height > 0  # at tip is allowed
    df = as_of(con, G1, TIP1 + dt.timedelta(hours=3), allow_post_tip=True)
    assert "played_minutes" in set(df["predicate"])
    with pytest.raises(PostTipError):
        as_of(con, "9999999999", TIP1)  # unknown tip cannot be verified
    with pytest.raises(PostTipError):
        known_before_tip(con, G1, -5)


def test_planted_future_lineup_snapshot_never_in_t30_query(built):
    con, _ = built
    df = known_before_tip(con, G1, 30)  # tip 23:30 -> t = 23:00
    starters = df.filter(pl.col("predicate") == "announced_starter")
    assert starters["subject"].to_list() == ["player:2"]  # 22:50 snapshot only
    assert starters["known_at"].max() == dt.datetime(2022, 10, 19, 22, 50)
    # the planted 23:45 snapshot (player 1, after tip) exists in the store but is invisible here
    assert _one(con, "SELECT count(*) FROM facts WHERE predicate='announced_starter'") == [(2,)]
    assert df["known_at"].max() <= TIP1 - dt.timedelta(minutes=30)


def test_facts_imports_nothing_from_research():
    root = Path(__file__).resolve().parents[2] / "nba" / "facts"
    for f in root.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any(n == "research" or n.startswith("research.") for n in names), f
