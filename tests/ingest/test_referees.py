"""Referee-assignments collector on a recorded page fixture (no network)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from nba.ingest import referees as r

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "referees" / "assignments_2026-10-09.html"
OFFICIALS = [
    (1, "Jacyn Goble", "68"),
    (2, "Andy Nagy", "18"),
    (3, "Matt Myers", "43"),
    (4, "James Williams", "60"),
    (5, "James Williams", "11"),  # same-name official: jersey must disambiguate
    (6, "Eric Lewis", "42"),
]


def test_parse_fixture_nba_only() -> None:
    page_date, games = r.parse_assignments(FIX.read_text())
    assert page_date == date(2026, 10, 9)
    assert [(g.away_name, g.home_name) for g in games] == [
        ("Houston", "Dallas"),
        ("Memphis", "Chicago"),
    ]  # WNBA table excluded
    g = games[0]
    assert (g.away_team_id, g.home_team_id) == (1610612745, 1610612742)
    assert g.officials["crew_chief"] == ("Jacyn Goble", "68")
    assert g.officials["alternate"] == ("", "")
    assert games[1].officials["umpire"] == ("Gina Catanzariti", "75")  # unlinked name still parsed


def _store(tmp_path: Path, html: str, t: datetime) -> tuple[object, r.CollectResult]:
    con = r.open_refs_db(tmp_path / "refs.duckdb")
    idx = r.OfficialIndex.from_rows(OFFICIALS)
    res = r.store_snapshot(con, html, fetched_at=t, index=idx, raw_dir=tmp_path / "raw")
    return con, res


def test_store_snapshot_maps_ids_and_flags_unmapped(tmp_path: Path) -> None:
    con, res = _store(tmp_path, FIX.read_text(), datetime(2026, 10, 9, 13, 5))
    assert res.stored and res.n_games == 2 and res.unmatched_teams == []
    assert res.unmatched_officials == ["Gina Catanzariti (#75)"]  # not in history: kept, flagged
    row = con.execute(  # type: ignore[attr-defined]
        "SELECT crew_chief_id, referee_id, umpire_id, alternate FROM ref_assignments "
        "WHERE home_team = 'Dallas'"
    ).fetchone()
    assert row == (1, 2, 3, None)
    row = con.execute(  # type: ignore[attr-defined]
        "SELECT crew_chief_id, umpire, umpire_id FROM ref_assignments WHERE home_team = 'Chicago'"
    ).fetchone()
    assert row == (4, "Gina Catanzariti", None)  # same-name official resolved by jersey #60


def test_identical_page_not_restored_but_changed_page_is(tmp_path: Path) -> None:
    html = FIX.read_text()
    con, _ = _store(tmp_path, html, datetime(2026, 10, 9, 13, 5))
    idx = r.OfficialIndex.from_rows(OFFICIALS)
    again = r.store_snapshot(
        con, html, fetched_at=datetime(2026, 10, 9, 15, 0), index=idx, raw_dir=tmp_path / "raw"
    )
    assert not again.stored
    changed = html.replace("Andy Nagy (#18)", "Eric Lewis (#42)")
    third = r.store_snapshot(
        con, changed, fetched_at=datetime(2026, 10, 9, 16, 0), index=idx, raw_dir=tmp_path / "raw"
    )
    assert third.stored
    assert con.execute("SELECT count(*) FROM ref_snapshots").fetchone() == (2,)  # type: ignore[attr-defined]
    assert con.execute("SELECT count(*) FROM ref_assignments").fetchone() == (4,)  # type: ignore[attr-defined]


def test_team_names() -> None:
    assert r.team_id_from_page_name("LA Clippers") == 1610612746
    assert r.team_id_from_page_name("Los Angeles Lakers") == 1610612747
    assert r.team_id_from_page_name("Oklahoma City") == 1610612760
    assert r.team_id_from_page_name("Seattle") is None


def test_remap_fills_late_ids(tmp_path: Path) -> None:
    con = r.open_refs_db(tmp_path / "refs.duckdb")
    r.store_snapshot(
        con,
        FIX.read_text(),
        fetched_at=datetime(2026, 10, 9, 13, 5),
        index=r.OfficialIndex.from_rows([]),
        raw_dir=tmp_path / "raw",
    )
    assert con.execute(
        "SELECT count(*) FROM ref_assignments WHERE crew_chief_id IS NULL"
    ).fetchone() == (2,)
    n = r.remap_official_ids(con, r.OfficialIndex.from_rows(OFFICIALS))
    assert n == 5
    assert con.execute(
        "SELECT crew_chief_id FROM ref_assignments ORDER BY crew_chief_id"
    ).fetchall() == [(1,), (4,)]


# --- reviewed official-id merges (configs/officials_id_merges.yaml, T202)

REAL_MERGES = {196295108: 1629171, 11629177: 1629177, 29012: 1629175, 29032: 1630886}


def test_merge_config_is_the_four_reviewed_pairs() -> None:
    assert r.load_id_merges() == REAL_MERGES


def test_merge_config_rejects_chain_and_repeat(tmp_path: Path) -> None:
    p = tmp_path / "m.yaml"
    p.write_text("merges:\n  - {merged_id: 2, kept_id: 1}\n  - {merged_id: 3, kept_id: 2}\n")
    try:
        r.load_id_merges(p)
    except ValueError as e:
        assert "chain" in str(e)
    else:  # pragma: no cover
        raise AssertionError("chain not rejected")
    p.write_text("merges:\n  - {merged_id: 2, kept_id: 1}\n  - {merged_id: 2, kept_id: 5}\n")
    try:
        r.load_id_merges(p)
    except ValueError as e:
        assert "bad" in str(e)
    else:  # pragma: no cover
        raise AssertionError("repeat not rejected")


def test_index_never_returns_a_merged_id() -> None:
    rows = [
        (1630886, "Intae Hwang", "73  "),
        (1630886, "Intae Hwang", "96  "),
        (29032, "Intae Hwang", "96  "),  # minority id re-appears in a stale cache
        (29012, "Brent Haskill", "92  "),
        (1629175, "Brent Haskill", "92  "),
    ]
    idx = r.OfficialIndex.from_rows(rows)
    assert idx.resolve("Intae Hwang", "") == 1630886  # one candidate now: no jersey needed
    assert idx.resolve("Intae Hwang", "96") == 1630886
    assert idx.resolve("Brent Haskill", "92") == 1629175
    assert all(len(v) == 1 for v in idx.by_name.values())


def test_canonical_frame_rewrites_and_keeps_other_rows() -> None:
    import polars as pl

    df = pl.DataFrame(
        {
            "game_id": ["a", "a", "b", "c", "c"],
            "official_id": [29012, 5, 1629175, 29012, 1629175],  # c lists both ids (impossible)
            "name": ["Brent Haskill", "X", "Brent Haskill", "Brent Haskill", "Brent Haskill"],
            "jersey": ["92", "1", "92", "92", "92"],
        }
    )
    out = r.canonical_official_frame(df)
    assert out.filter(pl.col("game_id") == "a")["official_id"].to_list() == [1629175, 5]
    assert out.filter(pl.col("game_id") == "c").height == 1  # PK-safe
    assert out.height == 4
    assert r.canonical_official_frame(df, {}).equals(df)  # no merges: identity
