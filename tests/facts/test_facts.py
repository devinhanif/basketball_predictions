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
    coach = _one(
        con,
        "SELECT object, confidence, valid_from, known_at, valid_to FROM facts "
        "WHERE predicate='head_coach' AND subject IN ('team:10', 'team:20') ORDER BY subject",
    )
    # known at the real tip of the first game he ran (not 1 October); a coach with no staff id
    # gets a name-keyed entity
    assert coach == [
        ("coach:500", 0.9, TIP1, TIP1, dt.datetime(2023, 10, 1)),
        ("coach:n:cyother", 0.9, TIP1, TIP1, dt.datetime(2023, 10, 1)),
    ]
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
    assert {"player:1", f"game:{G1}"} <= set(df["subject"])  # team:10 has only a coach fact, at tip
    assert set(df.filter(pl.col("predicate") == "away_team")["object"]) == {"team:20"}
    # a stint's first game: the coach is not known before that game's tip
    assert "head_coach" not in set(df["predicate"])
    at_tip = as_of(con, G1, TIP1)
    assert "coach:500" in set(at_tip.filter(pl.col("predicate") == "head_coach")["object"])


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


G26 = "0022600001"
TIP26 = dt.datetime(2026, 10, 21, 1, 30)  # 21:30 ET on 20 Oct (EDT)


def _sched_cache(tmp_path: Path) -> Path:
    import json

    d = tmp_path / "cache"
    d.mkdir(exist_ok=True)
    rows = [
        [G26, TIP26.isoformat(), 30, 40],
        ["0022500009", "2026-01-05T00:00:00", 10, 30],  # a 2025 id inside the 2026 file: ignored
    ]
    (d / "schedule_2026-27.json").write_text(json.dumps(rows))
    return d


def _build_sched(
    tmp_path: Path,
    schedule_dir: Path,
    seasons: tuple[int, ...],
    full: tuple[int, ...] = (),
):
    out = tmp_path / f"f{len(seasons)}.duckdb"
    src = make_source()
    # the planted traps: a 2026 box score, report, and lineup snapshot for the 2026 game
    src.execute("INSERT INTO games VALUES (?, '2026-10-20', 2026, 30, 40, NULL, NULL, NULL)", [G26])
    src.execute("INSERT INTO player_game_stats VALUES (?, 5, 30, 12.0)", [G26])
    src.execute("INSERT INTO players_static VALUES (4,'C',82,2025),(6,'G',77,2026)")
    src.execute("INSERT INTO team_coaches VALUES (2026,30,600,'New Coach','Head Coach',1)")
    src.execute(
        "INSERT INTO player_availability VALUES (5,'2026-10-20 11:00:00',?,'OUT','x',"
        "'nba_official_report',NULL)",
        [G26],
    )
    lc = make_lineups()
    lc.execute(
        "INSERT INTO lineup_snapshots VALUES ('s', ?, '2026-10-20', ?, 1, 'x', 30, 'AAA', "
        "TRUE, 5, 'n', 'Confirmed', TRUE, 'G', 'Active', NULL)",
        [dt.datetime(2026, 10, 20, 12, 0), G26],
    )
    build(
        src,
        lc,
        out,
        schedule_dir=schedule_dir,
        odds_aliases=tmp_path / "none.yaml",
        kalshi_aliases=tmp_path / "none2.yaml",
        schedule_seasons=seasons,
        schedule_cache=_sched_cache(tmp_path),
        seasons=full,
    )
    return duckdb.connect(str(out), read_only=True)


def test_schedule_seasons_adds_only_schedule_predicates(tmp_path: Path, schedule_dir: Path):
    con = _build_sched(tmp_path, schedule_dir, (2026,))
    rows = _one(
        con,
        "SELECT predicate, object, source, known_at, valid_from FROM facts "
        "WHERE subject = ? OR object = ? ORDER BY predicate",
        f"game:{G26}",
        f"game:{G26}",
    )
    assert [r[0] for r in rows] == ["away_team", "home_team", "tips_at"]
    assert {r[2] for r in rows} == {"schedule"}
    assert rows[0][1] == "team:40" and rows[1][1] == "team:30"
    # 00:00 ET on 19 Oct (day before the 20 Oct ET game) = 04:00 UTC
    assert {r[3] for r in rows} == {dt.datetime(2026, 10, 19, 4, 0)}
    assert rows[2][4] == TIP26
    assert _one(con, "SELECT count(*) FROM facts WHERE subject LIKE 'game:00225%'") == [(0,)]
    assert _one(
        con, "SELECT count(*) FROM facts WHERE object = 'player:5' OR subject='player:5'"
    ) == [(0,)]


def test_season_2025_and_2026_absent_by_default(tmp_path: Path, schedule_dir: Path):
    con = _build_sched(tmp_path, schedule_dir, ())
    assert _one(con, "SELECT count(*) FROM facts WHERE subject LIKE 'game:00226%'") == [(0,)]
    assert _one(con, "SELECT count(*) FROM facts WHERE subject LIKE 'game:00225%'") == [(0,)]


def test_listing_2026_does_not_admit_2025(tmp_path: Path, schedule_dir: Path):
    con = _build_sched(tmp_path, schedule_dir, (2026,))
    assert _one(con, "SELECT count(*) FROM facts WHERE subject LIKE 'game:00225%'") == [(0,)]


def test_schedule_seasons_leaves_other_counts_unchanged(tmp_path: Path, schedule_dir: Path):
    base = _build_sched(tmp_path, schedule_dir, ())
    more = _build_sched(tmp_path, schedule_dir, (2026,))
    q = "SELECT predicate, count(*) FROM facts GROUP BY 1 ORDER BY 1"
    b, m = dict(_one(base, q)), dict(_one(more, q))
    for k in b:
        assert m[k] - b[k] == (1 if k in {"tips_at", "home_team", "away_team"} else 0), k


def test_missing_schedule_source_fails_loudly(tmp_path: Path, schedule_dir: Path):
    with pytest.raises(FileNotFoundError):
        build(
            make_source(),
            None,
            tmp_path / "x.duckdb",
            schedule_dir=schedule_dir,
            odds_aliases=tmp_path / "n.yaml",
            kalshi_aliases=tmp_path / "n2.yaml",
            schedule_seasons=(2031,),
            schedule_cache=tmp_path / "empty",
        )


def test_explicit_seasons_load_2026_in_full_and_no_2025_fact(tmp_path: Path, schedule_dir: Path):
    con = _build_sched(tmp_path, schedule_dir, (2026,), full=(2022, 2026))
    preds = {
        r[0]
        for r in _one(con, "SELECT DISTINCT predicate FROM facts WHERE subject = ?", f"game:{G26}")
    }
    assert preds == {"away_team", "home_team", "tips_at"}
    # 2026 report, starter and box score are now loaded
    for pred in ("listed_status", "announced_starter"):  # played_minutes needs a real tip
        q = "SELECT count(*) FROM facts WHERE predicate = ? AND object = ?"
        assert _one(con, q, pred, f"game:{G26}") == [(1,)], pred
    assert _one(con, "SELECT count(*) FROM facts WHERE object = 'coach:600'") == [(1,)]
    # a 2022 game is still there, schedule facts for 2026 are not duplicated
    assert _one(con, "SELECT count(*) FROM facts WHERE subject = ?", f"game:{G1}")[0][0] > 0
    q = "SELECT count(*) FROM facts WHERE predicate = 'tips_at' AND subject = ?"
    assert _one(con, q, f"game:{G26}") == [(1,)]
    # holdout 2025: no fact of any predicate mentions its game, coach, report player or static
    assert _one(
        con,
        "SELECT count(*) FROM facts WHERE subject LIKE 'game:00225%' OR object LIKE 'game:00225%' "
        "OR object = 'coach:502' OR subject IN ('player:9', 'player:4')",
    ) == [(0,)]
    assert _one(
        con, "SELECT count(*) FROM facts WHERE known_at >= '2025-10-01' AND known_at < '2026-10-01'"
    ) == [(0,)]


def test_coach_change_after_a_game_never_appears_for_it(tmp_path: Path, schedule_dir: Path):
    """Planted future: team 10 changes coach at a later game; as-of reads for G1 never see him."""
    src = make_source()
    late = dt.datetime(2022, 12, 1, 0, 0)
    src.execute("INSERT INTO games VALUES ('0022200050','2022-11-30',2022,10,20,1,2,NULL)")
    src.execute(
        "INSERT INTO team_coach_games VALUES ('0022200050',10,'New Boss',900,'t',?),"
        "('0022200050',20,'Cy Other',NULL,'t',?)",
        [late, late],
    )
    out = tmp_path / "f_change.duckdb"
    build(
        src,
        make_lineups(),
        out,
        schedule_dir=schedule_dir,
        odds_aliases=tmp_path / "none.yaml",
        kalshi_aliases=tmp_path / "none2.yaml",
    )
    con = duckdb.connect(str(out), read_only=True)
    rows = _one(
        con,
        "SELECT object, valid_from, valid_to FROM facts WHERE predicate='head_coach' "
        "AND subject='team:10' ORDER BY known_at",
    )
    assert rows == [("coach:500", TIP1, late), ("coach:900", late, dt.datetime(2023, 10, 1))]
    df = as_of(con, G1, TIP1)
    assert "coach:900" not in set(df["object"].drop_nulls())
    assert _one(
        con, "SELECT count(*) FROM facts WHERE object='coach:900' AND known_at < ?", late
    ) == [(0,)]
    con.close()
