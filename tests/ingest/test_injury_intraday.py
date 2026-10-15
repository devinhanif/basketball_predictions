"""Intraday injury-report re-pull: resume, season refusal, ledger integrity, scratch-only parse.

Fake http client, no network, no sleeping.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.ingest import injury_intraday as ii
from nba.ingest.availability import official_report_url, official_report_url_hour_only

FIXTURE_PDF = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "injury_report"
    / "Injury-Report_2026-10-07_09_45AM.pdf"
)
PDF = FIXTURE_PDF.read_bytes()
DAY = date(2024, 1, 17)
NAMES = [
    "Devin Carter", "Tucker DeVries", "Luka Garza", "Cade Cunningham", "Elijah Harkless",
    "Daniss Jenkins", "Isaiah Joe", "Isaac Jones", "Chaz Lanier", "Kyle Anderson",
    "Reece Beekman", "Stephen Curry", "Draymond Green", "Jonathan Kuminga", "Alex Caruso",
    "Ousmane Dieng", "Luguentz Dort", "Alex Ducas", "Aaron Wiggins",
]  # fmt: skip


class Resp:
    def __init__(self, status: int, content: bytes = b"") -> None:
        self.status_code = status
        self.content = content


class FakeClient:
    """Serves ``store`` (url -> bytes); 404 otherwise. ``fail`` forces 503 on every call."""

    def __init__(self, store: dict[str, bytes], fail: bool = False) -> None:
        self.store = store
        self.fail = fail
        self.calls: list[tuple[str, bool]] = []  # (url, ranged)
        self.on_call: Any = None

    def get(self, url: str, headers: dict[str, str] | None = None, timeout: float = 0) -> Resp:
        ranged = bool(headers and "Range" in headers)
        self.calls.append((url, ranged))
        if self.on_call is not None:
            self.on_call(len(self.calls))
        if self.fail:
            return Resp(503)
        body = self.store.get(url)
        if body is None:
            return Resp(404)
        return Resp(206 if ranged else 200, body[:4] if ranged else body)

    def full_gets(self, url: str) -> int:
        return sum(1 for u, r in self.calls if u == url and not r)


def hourly_store(day: date, hour_only: bool = False) -> dict[str, bytes]:
    out = {}
    for ts in ii.slot_times(day):
        u = official_report_url_hour_only(ts) if hour_only else official_report_url(ts)
        out[u] = PDF
    return out


def run(
    tmp: Path, client: FakeClient, stop: ii.Stopper | None = None, **kw: Any
) -> tuple[str, Any]:
    return ii.pull(
        seasons=[2023],
        data_dir=tmp,
        db_path=tmp / "unused.duckdb",
        client=client,  # type: ignore[arg-type]
        pacer=ii.Pacer(sleep=lambda s: None),
        stop=stop,
        sleep=lambda s: None,
        dates=[DAY],
        **kw,
    )


def test_resume_skips_existing_nonempty_and_refetches_empty(tmp_path: Path) -> None:
    raw = ii.raw_dir_of(tmp_path) / DAY.isoformat()
    raw.mkdir(parents=True)
    keep = raw / "Injury-Report_2024-01-17_12_00PM.pdf"
    keep.write_bytes(b"%PDF-sentinel")
    empty = raw / "Injury-Report_2024-01-17_01_00PM.pdf"
    empty.write_bytes(b"")
    client = FakeClient(hourly_store(DAY))
    state, ledger = run(tmp_path, client)
    assert state == "complete"
    assert keep.read_bytes() == b"%PDF-sentinel"  # write-once: untouched
    assert all(u != official_report_url(datetime(2024, 1, 17, 12)) for u, _ in client.calls)
    assert empty.stat().st_size > 0  # zero-size file does not count as done
    assert ledger["counts"]["skipped_on_disk"] == 1
    assert ledger["counts"]["present"] == 10
    # second run: everything is on disk, zero requests
    client2 = FakeClient(hourly_store(DAY))
    run(tmp_path, client2)
    assert client2.calls == []


def test_hour_only_filename_form_is_found(tmp_path: Path) -> None:
    client = FakeClient(hourly_store(DAY, hour_only=True))
    state, ledger = run(tmp_path, client)
    assert state == "complete" and ledger["counts"]["present"] == 10
    files = sorted(p.name for p in (ii.raw_dir_of(tmp_path) / "2024-01-17").glob("*.pdf"))
    assert files[0] == "Injury-Report_2024-01-17_01PM.pdf" and len(files) == 10
    # the form that resolved is tried first afterwards: 10 probes + 10 GETs + 1 wrong-form probe
    assert len(client.calls) == 21


def test_absent_slots_are_not_downloaded_and_are_remembered(tmp_path: Path) -> None:
    store = hourly_store(DAY)
    for ts in ii.slot_times(DAY)[5:]:
        del store[official_report_url(ts)]
    client = FakeClient(store)
    _, ledger = run(tmp_path, client)
    assert ledger["counts"]["present"] == 5 and ledger["counts"]["absent"] == 5
    assert all(r for _, r in client.calls if _ not in store)  # misses only ever probed
    client2 = FakeClient(store)
    run(tmp_path, client2)
    assert client2.calls == []  # definite absences are not re-probed


def test_quarter_hours_probe_only_hh_mm_form(tmp_path: Path) -> None:
    client = FakeClient({})
    run(tmp_path, client, quarter_hours=True)
    quarter = [u for u, _ in client.calls if "_15" in u or "_30" in u or "_45" in u]
    assert len(quarter) == 30
    assert len(client.calls) == 50  # 40 slots + 10 hour-only forms, as the pilot probe


def test_season_2025_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(ii.SeasonRefused):
        ii.check_seasons([2024, 2025])
    with pytest.raises(ii.SeasonRefused):
        ii.check_seasons([2026])
    with pytest.raises(ii.SeasonRefused):
        ii.check_dates([date(2025, 12, 1)])
    client = FakeClient({})
    with pytest.raises(ii.SeasonRefused):
        ii.pull(
            seasons=[2025],
            data_dir=tmp_path,
            db_path=tmp_path / "x",
            client=client,  # type: ignore[arg-type]
        )
    assert ii.main(["pull", "--seasons", "2024", "2025", "--data-dir", str(tmp_path)]) == 2
    assert (
        ii.main(["pull", "--seasons", "2023", "--dates", "2025-11-01", "--data-dir", str(tmp_path)])
        == 2
    )
    assert client.calls == []
    assert not ii.raw_dir_of(tmp_path).exists()  # refused before anything was written
    assert "refused" in capsys.readouterr().err


def test_stop_and_resume_keeps_ledger_consistent(tmp_path: Path) -> None:
    store = hourly_store(DAY)
    stop = ii.Stopper()
    client = FakeClient(store)
    client.on_call = lambda n: stop.request() if n == 7 else None
    state, ledger = run(tmp_path, client, stop)
    assert state == "stopped"
    on_disk = ii.raw_dir_of(tmp_path) / "2024-01-17"
    n1 = len(list(on_disk.glob("*.pdf")))
    assert 0 < n1 < 10
    saved = json.loads((ii.raw_dir_of(tmp_path) / ii.LEDGER_NAME).read_text())
    assert saved["state"] == "stopped" and saved["pid"] is None
    assert saved["counts"]["present"] == n1 == ii.slots_done(saved)
    assert (
        not list(on_disk.glob("*.part")) and not (ii.raw_dir_of(tmp_path) / ii.LOCK_NAME).exists()
    )
    # resume: finishes, never re-downloads a finished file
    client2 = FakeClient(store)
    state2, ledger2 = run(tmp_path, client2)
    assert state2 == "complete"
    assert len(list(on_disk.glob("*.pdf"))) == 10
    assert ledger2["counts"]["present"] == 10 and ledger2["counts"]["downloaded"] == 10
    for u, _ in client.calls:
        assert client2.full_gets(u) == 0 or client.full_gets(u) == 0
    assert sum(client.full_gets(u) + client2.full_gets(u) for u in store) == 10


def test_hard_stop_after_consecutive_failures(tmp_path: Path) -> None:
    client = FakeClient(hourly_store(DAY), fail=True)
    state, ledger = run(tmp_path, client, max_consecutive_failures=3)
    assert state == "hard_stop"
    assert ledger["counts"]["failures"] == 3 and len(ledger["failed_slots"]) == 3
    assert ledger["slots"][DAY.isoformat()] == {}  # a failure is never cached as a miss
    assert ii.load_ledger(ii.raw_dir_of(tmp_path))["state"] == "hard_stop"  # type: ignore[index]
    # recovery: the same slots are retried and succeed
    state2, ledger2 = run(tmp_path, FakeClient(hourly_store(DAY)))
    assert state2 == "complete" and ledger2["failed_slots"] == {}


def test_html_200_is_not_a_report(tmp_path: Path) -> None:
    client = FakeClient({u: b"<html>nope</html>" for u in hourly_store(DAY)})
    _, ledger = run(tmp_path, client)
    assert ledger["counts"]["present"] == 0 and ledger["counts"]["absent"] == 10


def test_corrupt_ledger_is_not_overwritten(tmp_path: Path) -> None:
    raw = ii.raw_dir_of(tmp_path)
    raw.mkdir(parents=True)
    (raw / ii.LEDGER_NAME).write_text("{not json")
    with pytest.raises(ii.LedgerCorrupt):
        run(tmp_path, FakeClient({}))
    assert (raw / ii.LEDGER_NAME).read_text() == "{not json"


def test_lock_refuses_second_puller(tmp_path: Path) -> None:
    raw = ii.raw_dir_of(tmp_path)
    ii.acquire_lock(raw)
    with pytest.raises(RuntimeError, match="another pull"):
        ii.acquire_lock(raw)


def test_status_shows_counts_and_eta(tmp_path: Path) -> None:
    run(tmp_path, FakeClient(hourly_store(DAY)))
    text = ii.status_text(ii.load_ledger(ii.raw_dir_of(tmp_path)))
    assert "done/total slots: 10/10" in text and "failures: 0" in text and "ETA: 0 min" in text
    assert ii.status_text(None).startswith("no ledger")
    assert ii.main(["status", "--data-dir", str(tmp_path)]) == 0


def test_dry_run_counts_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = ii.main(["pull", "--seasons", "2023", "--dates", "2024-01-17", "2024-02-22",
                  "--dry-run", "--data-dir", str(tmp_path)])  # fmt: skip
    out = capsys.readouterr().out
    assert rc == 0 and "2 dates, 10 slots per date, 20 slots" in out
    assert not ii.raw_dir_of(tmp_path).exists()


# --------------------------------------------------------------------------------------- parse


def _source_db(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT, "
        "home_team INT, away_team INT)"
    )
    con.execute("INSERT INTO games VALUES ('g1', DATE '2024-01-17', 2023, 1, 2)")
    con.execute("INSERT INTO games VALUES ('g2', DATE '2025-11-01', 2025, 1, 2)")
    con.execute("CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT, team_id INT)")
    for i in range(len(NAMES)):
        con.execute("INSERT INTO player_game_stats VALUES ('g1', ?, 1)", [i + 1])
        con.execute("INSERT INTO player_game_stats VALUES ('g2', ?, 1)", [i + 1])
    con.close()


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_parse_builds_scratch_only_and_leaves_source_untouched(tmp_path: Path) -> None:
    src = tmp_path / "nba.duckdb"
    _source_db(src)
    before = _sha(src)
    day_dir = ii.raw_dir_of(tmp_path) / "2024-01-17"
    day_dir.mkdir(parents=True)
    (day_dir / "Injury-Report_2024-01-17_05PM.pdf").write_bytes(PDF)
    (day_dir / "Injury-Report_2024-01-17_12_00PM.pdf").write_bytes(PDF)
    scratch = tmp_path / "scratch" / "injury_intraday.duckdb"
    stats = ii.parse_to_scratch(
        data_dir=tmp_path, scratch_db=scratch, source_db=src,
        names=[(n, i + 1) for i, n in enumerate(NAMES)],
    )  # fmt: skip
    assert stats["parsed"] == 2 and stats["errors"] == 0 and stats["rows"] > 0
    assert _sha(src) == before  # production db byte-identical
    con = duckdb.connect(str(scratch), read_only=True)
    reps = con.execute(
        "SELECT file, report_dt, known_at, status FROM injury_intraday_reports ORDER BY report_dt"
    ).fetchall()
    assert [r[1] for r in reps] == [datetime(2024, 1, 17, 12), datetime(2024, 1, 17, 17)]
    assert all(r[1] == r[2] and r[3] == "ok" for r in reps)
    times = {r[0] for r in con.execute("SELECT as_of FROM player_availability").fetchall()}
    assert times == {datetime(2024, 1, 17, 12), datetime(2024, 1, 17, 17)}
    assert con.execute("SELECT max(game_date) FROM games").fetchone()[0] <= date(2024, 1, 17)  # type: ignore[index]
    assert con.execute("SELECT count(*) FROM injury_intraday_team_sections").fetchone()[0] > 0  # type: ignore[index]
    con.close()
    # idempotent: a second parse does nothing and does not duplicate rows
    stats2 = ii.parse_to_scratch(
        data_dir=tmp_path, scratch_db=scratch, source_db=src,
        names=[(n, i + 1) for i, n in enumerate(NAMES)],
    )  # fmt: skip
    assert stats2["parsed"] == 0 and stats2["skipped"] == 2
    con = duckdb.connect(str(scratch), read_only=True)
    assert con.execute("SELECT count(*) FROM player_availability").fetchone()[0] == stats["rows"]  # type: ignore[index]
    con.close()


def test_parse_refuses_production_db_as_target_and_late_dates(tmp_path: Path) -> None:
    src = tmp_path / "nba.duckdb"
    _source_db(src)
    with pytest.raises(ValueError, match="production"):
        ii.parse_to_scratch(data_dir=tmp_path, scratch_db=src, source_db=src)
    with pytest.raises(ValueError, match="production"):
        ii.parse_to_scratch(
            data_dir=tmp_path, scratch_db=tmp_path / "x" / "nba.duckdb", source_db=src
        )
    late = ii.raw_dir_of(tmp_path) / "2025-11-01"
    late.mkdir(parents=True)
    (late / "Injury-Report_2025-11-01_05PM.pdf").write_bytes(PDF)
    with pytest.raises(ii.SeasonRefused):
        ii.parse_to_scratch(
            data_dir=tmp_path, scratch_db=tmp_path / "s.duckdb", source_db=src,
            names=[(n, i + 1) for i, n in enumerate(NAMES)],
        )  # fmt: skip


def test_parse_logs_bad_pdf_and_continues(tmp_path: Path) -> None:
    src = tmp_path / "nba.duckdb"
    _source_db(src)
    day_dir = ii.raw_dir_of(tmp_path) / "2024-01-17"
    day_dir.mkdir(parents=True)
    (day_dir / "Injury-Report_2024-01-17_01PM.pdf").write_bytes(b"%PDF-broken")
    (day_dir / "Injury-Report_2024-01-17_05PM.pdf").write_bytes(PDF)
    stats = ii.parse_to_scratch(
        data_dir=tmp_path, scratch_db=tmp_path / "s.duckdb", source_db=src,
        names=[(n, i + 1) for i, n in enumerate(NAMES)],
    )  # fmt: skip
    assert stats["errors"] == 1 and stats["parsed"] == 1


def test_filename_roundtrip() -> None:
    for ts in ii.slot_times(DAY):
        for u in ii.candidate_urls(ts):
            assert ii.parse_report_filename(u.rsplit("/", 1)[-1]) == ts
    with pytest.raises(ValueError):
        ii.parse_report_filename("nope.pdf")
