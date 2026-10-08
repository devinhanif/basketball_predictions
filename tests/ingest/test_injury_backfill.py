"""Backfill tests for the official injury-report PDFs. Fake http client, no network."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from nba.db.connect import connect
from nba.ingest.availability import (
    SOURCE_OFFICIAL_REPORT_BACKFILL,
    backfill_official_injury_reports,
    backfill_slots,
    find_report_before,
    official_report_url,
    official_report_url_candidates,
    official_report_url_hour_only,
    probe_report_url,
)
from nba.ingest.cache import is_cached
from nba.parse.availability import build_name_index

FIXTURE_PDF = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "injury_report"
    / "Injury-Report_2026-10-07_09_45AM.pdf"
)
PDF = FIXTURE_PDF.read_bytes()
NAMES = [
    "Devin Carter", "Tucker DeVries", "Luka Garza", "Cade Cunningham", "Elijah Harkless",
    "Daniss Jenkins", "Isaiah Joe", "Isaac Jones", "Chaz Lanier", "Kyle Anderson",
    "Reece Beekman", "Stephen Curry", "Draymond Green", "Jonathan Kuminga", "Alex Caruso",
    "Ousmane Dieng", "Luguentz Dort", "Alex Ducas", "Aaron Wiggins",
]  # fmt: skip
NAME_INDEX = build_name_index([(n, i + 1) for i, n in enumerate(NAMES)])


class FakeResp:
    def __init__(self, status: int, content: bytes) -> None:
        self.status_code = status
        self.content = content

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    def __init__(self, objects: dict[str, bytes], fail_substr: str | None = None) -> None:
        self.objects = objects
        self.fail_substr = fail_substr
        self.calls: list[str] = []

    def get(self, url: str, **kw: Any) -> FakeResp:
        self.calls.append(url)
        if self.fail_substr and self.fail_substr in url:
            raise ConnectionError("boom")
        if url in self.objects:
            return FakeResp(200, self.objects[url])
        return FakeResp(404, b"")


def test_slots_never_after_anchor_and_latest_first() -> None:
    anchor = datetime(2024, 1, 5, 17, 45)
    slots = backfill_slots(anchor)
    assert slots[0] == anchor
    assert all(s <= anchor for s in slots)
    assert slots == sorted(slots, reverse=True)


def test_candidate_fallback_order() -> None:
    dt = datetime(2022, 10, 19, 13, 0)
    assert official_report_url_candidates(dt) == [
        official_report_url(dt),
        official_report_url_hour_only(dt),
    ]
    assert official_report_url_hour_only(dt).endswith("2022-10-19_01PM.pdf")


def test_probe_requires_pdf_magic() -> None:
    c = FakeClient({"a": b"%PDF-1", "b": b"<html>"})
    assert probe_report_url("a", client=c)  # type: ignore[arg-type]
    assert not probe_report_url("b", client=c)  # type: ignore[arg-type]
    assert not probe_report_url("missing", client=c)  # type: ignore[arg-type]
    assert not probe_report_url("x", client=FakeClient({}, fail_substr="x"))  # type: ignore[arg-type]


def test_find_report_falls_back_to_hour_only_and_earlier_slot() -> None:
    anchor = datetime(2022, 10, 19, 17, 45)
    target = datetime(2022, 10, 19, 16, 30)
    url = official_report_url_hour_only(datetime(2022, 10, 19, 16, 0))
    c = FakeClient({url: PDF})
    hit = find_report_before(anchor, client=c)  # type: ignore[arg-type]
    assert hit == (datetime(2022, 10, 19, 16, 0), url)
    assert target > hit[0]  # latest-first: nothing between was published
    assert find_report_before(anchor, client=FakeClient({}), lookback_h=1) is None  # type: ignore[arg-type]


def _make_con() -> Any:
    con = connect(":memory:")
    return con


def test_backfill_as_of_before_anchor_resumable_and_isolated(tmp_path: Path) -> None:
    con = _make_con()
    d1, d2, d3 = date(2024, 1, 5), date(2024, 1, 6), date(2024, 1, 7)
    objs = {
        official_report_url(datetime(2024, 1, 5, 11, 30)): PDF,
        official_report_url_hour_only(datetime(2024, 1, 5, 17, 0)): PDF,
        # d2: one report, serves both anchors? no: only one pre-11:45 report exists
        official_report_url(datetime(2024, 1, 6, 10, 0)): PDF,
        # d3: nothing published
    }
    c = FakeClient(objs)
    res = backfill_official_injury_reports(
        con,
        dates=[d1, d2, d3],
        name_index=NAME_INDEX,
        data_dir=tmp_path,
        http_client=c,  # type: ignore[arg-type]
        progress_every=0,
    )
    assert (res.dates_done, res.dates_no_report, res.dates_failed) == (2, 1, 0)
    # d1 -> two distinct reports; d2 -> same report found for both anchors, loaded once
    assert res.reports_loaded == 3
    as_ofs = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT as_of FROM player_availability "
            "WHERE source='nba_official_report' ORDER BY 1"
        ).fetchall()
    ]
    assert as_ofs == [
        datetime(2024, 1, 5, 11, 30),
        datetime(2024, 1, 5, 17, 0),
        datetime(2024, 1, 6, 10, 0),
    ]
    # strictly before every anchor of its date's tip bucket (<= anchor, anchors end :45)
    assert all(a.minute != 45 or a < a.replace(minute=45, second=1) for a in as_ofs)
    assert all(a.hour < 18 for a in as_ofs)
    assert is_cached(con, SOURCE_OFFICIAL_REPORT_BACKFILL, "2024-01-05")

    # resumable: second run does no network at all
    n_calls = len(c.calls)
    res2 = backfill_official_injury_reports(
        con,
        dates=[d1, d2, d3],
        name_index=NAME_INDEX,
        data_dir=tmp_path,
        http_client=c,  # type: ignore[arg-type]
        progress_every=0,
    )
    assert res2.dates_skipped_cached == 3 and len(c.calls) == n_calls


def test_failure_isolated_and_retried(tmp_path: Path) -> None:
    con = _make_con()
    bad, good = date(2024, 2, 1), date(2024, 2, 2)
    objs = {
        official_report_url(datetime(2024, 2, 1, 11, 30)): b"%PDF-not-really-a-pdf",
        official_report_url(datetime(2024, 2, 2, 11, 30)): PDF,
    }
    res = backfill_official_injury_reports(
        con,
        dates=[bad, good],
        name_index=NAME_INDEX,
        data_dir=tmp_path,
        http_client=FakeClient(objs),  # type: ignore[arg-type]
        anchors=("11:45",),
        progress_every=0,
    )
    assert res.dates_failed == 1 and res.dates_done == 1
    assert not is_cached(con, SOURCE_OFFICIAL_REPORT_BACKFILL, "2024-02-01")
    assert is_cached(con, SOURCE_OFFICIAL_REPORT_BACKFILL, "2024-02-02")


def test_dry_run_writes_nothing_and_games_table_dates(tmp_path: Path) -> None:
    con = _make_con()
    con.execute(
        "INSERT INTO games VALUES ('g1', DATE '2024-03-01', 2023, 1, 2, 100, 99, NULL),"
        " ('g2', DATE '2024-03-01', 2023, 3, 4, 100, 99, NULL),"
        " ('g3', DATE '2024-03-09', 2023, 3, 4, 100, 99, NULL)"
    )
    objs = {official_report_url(datetime(2024, 3, 1, 11, 45)): PDF}
    res = backfill_official_injury_reports(
        con,
        start=date(2024, 3, 1),
        end=date(2024, 3, 31),
        data_dir=tmp_path,
        http_client=FakeClient(objs),  # type: ignore[arg-type]
        dry_run=True,
        limit=1,
        progress_every=0,
    )
    assert res.dates_total == 1 and list(res.found) == ["2024-03-01"]
    assert con.execute("SELECT count(*) FROM player_availability").fetchone() == (0,)
    assert not (tmp_path / "availability_official").exists()


# ---- team-aware resolver ----------------------------------------------------
from nba.ingest.availability import build_name_resolver  # noqa: E402
from nba.parse.availability import NameResolver, team_id_from_report_team  # noqa: E402

BOS, DET = 1610612738, 1610612765


def _hist(*items: tuple[int, str, int]) -> dict[int, list[tuple[date, int]]]:
    out: dict[int, list[tuple[date, int]]] = {}
    for pid, d, t in items:
        out.setdefault(pid, []).append((date.fromisoformat(d), t))
    return out


def test_team_name_mapping() -> None:
    assert team_id_from_report_team("BostonCeltics") == BOS
    assert team_id_from_report_team("Portland Trail Blazers") is not None
    assert team_id_from_report_team("Nope") is None


def test_same_last_name_different_teams_and_no_build_raise() -> None:
    names = [("Jaylen Brown", 1), ("Kobe Brown", 2), ("Jalen Williams", 3), ("Jalen Williams", 4)]
    h = _hist((3, "2024-01-01", BOS), (4, "2024-01-01", DET))
    r = NameResolver(names, h)  # same-full-name duplicate: build must not raise
    assert r.lookup("Jaylen Brown", None, None) == 1
    # two players with the identical full name: team-as-of breaks the tie
    assert r.lookup("Jalen Williams", DET, date(2024, 1, 10)) == 4
    assert r.lookup("Jalen Williams", BOS, date(2024, 1, 10)) == 3
    # unresolvable without a usable team -> None, not an exception
    assert r.lookup("Jalen Williams", None, date(2024, 1, 10)) is None
    assert r.lookup("Jalen Williams", BOS, date(2025, 6, 1)) is None  # outside window


def test_suffix_handling() -> None:
    r = NameResolver([("Gary Trent", 10), ("Gary Trent Jr.", 11), ("Wendell Carter Jr.", 12)])
    assert r.lookup("Gary Trent Jr.", None, None) == 11
    assert r.lookup("Gary Trent", None, None) == 10
    assert r.lookup("Wendell Carter", None, None) == 12  # report omits suffix
    assert r.lookup("Wendell Carter Jr", None, None) == 12


def test_diacritics_and_alias() -> None:
    r = NameResolver([("Nikola Jokić", 20), ("Nic Claxton", 21)], aliases={"Nicolas Claxton": 21})
    assert r.lookup("Nikola Jokic", None, None) == 20
    assert r.lookup("Nicolas Claxton", None, None) == 21


def test_build_resolver_pool_restricted_to_seen_players() -> None:
    con = connect(":memory:")
    con.execute("INSERT INTO games VALUES ('g1', DATE '2024-01-01', 2023, 1, 2, 1, 1, NULL)")
    con.execute(
        "INSERT INTO player_game_stats (game_id, player_id, team_id) VALUES ('g1', 1, 1610612738)"
    )
    r = build_name_resolver(
        con, Path("."), date(2024, 1, 1), date(2024, 1, 1),
        names=[("Seen Guy", 1), ("Old Retired", 99)],
    )  # fmt: skip
    assert r.lookup("Seen Guy", None, None) == 1
    assert r.lookup("Old Retired", None, None) is None


def test_backfill_with_resolver_logs_unmatched_and_continues(tmp_path: Path) -> None:
    con = _make_con()
    # resolver knows only 17 of 19 players -> 10.5% unmatched > 5% -> date fails loudly
    # two ambiguous names (each has 2 candidates, no team history) -> counted
    r = NameResolver(
        [(n, i + 1) for i, n in enumerate(NAMES[:17])]
        + [(NAMES[17], 100), (NAMES[17], 101), (NAMES[18], 102), (NAMES[18], 103)]
    )
    objs = {official_report_url(datetime(2024, 2, 2, 11, 30)): PDF}
    res = backfill_official_injury_reports(
        con, dates=[date(2024, 2, 2)], resolver=r, data_dir=tmp_path,
        http_client=FakeClient(objs), anchors=("11:45",), progress_every=0,
    )  # type: ignore[arg-type]  # fmt: skip
    assert res.dates_failed == 1
    assert (tmp_path / "availability_backfill" / "unmatched.csv").read_text().count("\n") == 3
    # one unmatched of 19 (5.3%)... use 18 known -> under threshold: row skipped, date ok
    r2 = NameResolver([(n, i + 1) for i, n in enumerate(NAMES[:18])])
    con2 = _make_con()
    res2 = backfill_official_injury_reports(
        con2, dates=[date(2024, 2, 2)], resolver=r2, data_dir=tmp_path / "b",
        http_client=FakeClient(objs), anchors=("11:45",), progress_every=0, max_unmatched_rate=0.1,
    )  # type: ignore[arg-type]  # fmt: skip
    assert (res2.dates_done, res2.names_matched, res2.names_unmatched) == (1, 18, 1)


OLD_PDF = FIXTURE_PDF.parent / "Injury-Report_2022-10-19_11AM.pdf"


def test_old_layout_pdf_parses_and_skips_not_yet_submitted() -> None:
    import pdfplumber

    from nba.parse.availability import _parse_report_pages

    with pdfplumber.open(OLD_PDF) as pdf:
        recs = _parse_report_pages(pdf)
    assert len(recs) == 55
    first = recs[0]
    assert first.player == "Bagley III, Marvin" and first.status == "Out"
    assert first.matchup == "ORL@DET" and "Pistons" in (first.team or "")
    assert all("NOT YET" not in (r.reason or "") for r in recs)
    assert all(
        r.status in {"Out", "Questionable", "Probable", "Doubtful", "Available"} for r in recs
    )


def test_known_unseen_names_do_not_count_toward_unmatched_rate(tmp_path: Path) -> None:
    con = _make_con()
    r = NameResolver(
        [(n, i + 1) for i, n in enumerate(NAMES[:17])], known_unseen=NAMES[17:]
    )  # two real players with no data in the pool
    objs = {official_report_url(datetime(2024, 2, 2, 11, 30)): PDF}
    res = backfill_official_injury_reports(
        con, dates=[date(2024, 2, 2)], resolver=r, data_dir=tmp_path,
        http_client=FakeClient(objs), anchors=("11:45",), progress_every=0,
    )  # type: ignore[arg-type]  # fmt: skip
    assert (res.dates_done, res.names_matched, res.names_unmatched) == (1, 17, 2)
    assert "not_in_pool" in (tmp_path / "availability_backfill" / "unmatched.csv").read_text()
