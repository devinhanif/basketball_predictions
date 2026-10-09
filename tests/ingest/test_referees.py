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
