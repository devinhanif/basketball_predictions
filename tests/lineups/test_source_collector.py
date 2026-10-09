"""Parser and poll-logic tests on the recorded feed (no network)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from nba.lineups.collector import poll_once, should_fetch
from nba.lineups.source import (
    CONFIRMED,
    EXPECTED,
    RawFetch,
    lineups_url,
    parse_daily_lineups,
    tip_from_status_text,
)
from nba.lineups.store import (
    connect_lineups,
    latest_snapshot_before,
    tips_for_date,
    upsert_tip,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "lineups" / "daily_lineups_20261009_0851z.json"
D = date(2026, 10, 9)
TIP_CHI = datetime(2026, 10, 10, 0, 0)  # 8:00 pm ET Oct 9


def _fetch_of(body: bytes, at: datetime, status: int = 200) -> RawFetch:
    return RawFetch("test://x", status, body, at, None, None)


def test_url() -> None:
    assert lineups_url(D).endswith("/js/data/leaders/00_daily_lineups_20261009.json")


def test_parse_recorded_feed() -> None:
    rows = parse_daily_lineups(FIXTURE.read_bytes())
    assert {r.game_id for r in rows} == {"0012600012", "0012600037"}
    dal = [r for r in rows if r.team_abbr == "DAL"]
    assert len(dal) == 20 and {r.lineup_status for r in dal} == {CONFIRMED}
    assert sum(r.announced_starter for r in dal) == 5
    chi = [r for r in rows if r.team_abbr == "CHI"]
    assert len(chi) == 5 and {r.lineup_status for r in chi} == {EXPECTED}
    assert all(r.is_home for r in chi) and not any(r.is_home for r in rows if r.team_abbr == "MEM")
    # the feed clock is US-Eastern: 08:43 ET on 2026-10-09 (EDT) is 12:43 UTC
    assert dal[0].source_ts == datetime(2026, 10, 9, 12, 43)
    assert rows[0].game_status == 2 and chi[0].game_status == 1
    assert chi[0].game_status_text == "8:00 pm ET"


@pytest.mark.parametrize("bad", [b"<html>Access Denied</html>", b"{}", b'{"games": 3}', b"[]"])
def test_parse_rejects_garbage(bad: bytes) -> None:
    with pytest.raises(ValueError):
        parse_daily_lineups(bad)


def test_tip_from_status_text() -> None:
    assert tip_from_status_text("8:00 pm ET", D) == TIP_CHI
    assert tip_from_status_text("12:30 PM ET", D) == datetime(2026, 10, 9, 16, 30)
    assert tip_from_status_text("2nd Qtr", D) is None
    assert tip_from_status_text("Final", D) is None


def test_should_fetch_window_and_backoff() -> None:
    tip = TIP_CHI
    tips = {"g": tip}
    assert should_fetch(tips, None, tip - timedelta(minutes=61)) == (False, "no_game_in_window")
    assert should_fetch(tips, None, tip - timedelta(minutes=60))[0]
    assert should_fetch(tips, None, tip - timedelta(minutes=1))[0]
    assert should_fetch(tips, None, tip + timedelta(minutes=4))[0]  # grace
    assert not should_fetch(tips, None, tip + timedelta(minutes=5))[0]
    last = tip - timedelta(minutes=30)
    assert should_fetch(tips, last, last + timedelta(seconds=100)) == (False, "min_interval")
    assert should_fetch(tips, last, last + timedelta(minutes=5))[0]
    # no tips known: bootstrap at most every 30 minutes
    t0 = datetime(2026, 10, 9, 14, 0)
    assert should_fetch({}, None, t0) == (True, "bootstrap_no_tips_known")
    assert not should_fetch({}, t0, t0 + timedelta(minutes=10))[0]
    assert should_fetch({}, t0, t0 + timedelta(minutes=31))[0]


def test_poll_bootstraps_tips_and_stores_everything(tmp_path: Path) -> None:
    lcon = connect_lineups(tmp_path / "l.duckdb")
    body = FIXTURE.read_bytes()
    calls: list[date] = []

    def fetch(d: date) -> RawFetch:
        calls.append(d)
        return _fetch_of(body, datetime(2026, 10, 9, 13, 0))

    now = datetime(2026, 10, 9, 13, 0)
    res = poll_once(lcon, D, now=now, fetch=fetch, root=tmp_path)
    assert res.action == "fetched" and res.n_games == 2 and res.n_rows == 48
    # the only not-yet-started game teaches us its tip from the status text
    assert tips_for_date(lcon, D) == {"0012600037": TIP_CHI}
    assert len(list((tmp_path / "raw" / "2026-10-09").glob("*.json"))) == 1  # raw kept
    # tips are known now and tip-60 is 7 h away: no more fetches
    again = poll_once(lcon, D, now=now + timedelta(minutes=40), fetch=fetch, root=tmp_path)
    assert again.action == "skipped" and again.reason == "no_game_in_window"
    assert len(calls) == 1
    # inside the window: fetch, every 5 minutes, never faster than the min interval
    t = TIP_CHI - timedelta(minutes=55)
    assert (
        poll_once(lcon, D, now=t, fetch=lambda d: _fetch_of(body, t), root=tmp_path).action
        == "fetched"
    )
    soon = t + timedelta(seconds=60)
    assert (
        poll_once(lcon, D, now=soon, fetch=lambda d: _fetch_of(body, soon), root=tmp_path).reason
        == "min_interval"
    )
    n = lcon.execute("SELECT count(*) FROM snapshot_log").fetchone()
    assert n == (2,)


def test_schedule_tip_beats_status_text_and_snapshot_cutoff(tmp_path: Path) -> None:
    lcon = connect_lineups(tmp_path / "l.duckdb")
    upsert_tip(
        lcon, "0012600037", D, TIP_CHI + timedelta(minutes=5), "schedule", datetime(2026, 10, 9)
    )
    upsert_tip(lcon, "0012600037", D, TIP_CHI, "status_text", datetime(2026, 10, 9, 1))
    assert tips_for_date(lcon, D)["0012600037"] == TIP_CHI + timedelta(minutes=5)
    body = FIXTURE.read_bytes()
    t1, t2 = datetime(2026, 10, 9, 23, 0), datetime(2026, 10, 9, 23, 40)
    for t in (t1, t2):
        poll_once(
            lcon,
            D,
            now=t,
            fetch=lambda d, t=t: _fetch_of(body + t.isoformat().encode()[:0], t),
            root=tmp_path,
            force=True,
        )
    # strictly before: a snapshot fetched exactly at the cutoff is not visible
    snap = latest_snapshot_before(lcon, "0012600037", t2)
    assert snap is not None and snap[1] == t1
    assert latest_snapshot_before(lcon, "0012600037", t1) is None


def test_http_error_and_parse_error_are_recorded_not_stored_as_data(tmp_path: Path) -> None:
    lcon = connect_lineups(tmp_path / "l.duckdb")
    t = datetime(2026, 10, 9, 13, 0)
    r = poll_once(lcon, D, now=t, fetch=lambda d: _fetch_of(b"nope", t, 404), root=tmp_path)
    assert r.reason == "http_404" and r.http_status == 404
    t2 = t + timedelta(hours=1)
    r2 = poll_once(lcon, D, now=t2, fetch=lambda d: _fetch_of(b"<html/>", t2), root=tmp_path)
    assert r2.reason == "parse_error"
    assert lcon.execute("SELECT count(*) FROM lineup_snapshots").fetchone() == (0,)
    notes = [
        x[0] for x in lcon.execute("SELECT note FROM snapshot_log ORDER BY fetched_at").fetchall()
    ]
    assert notes[0] == "http_404" and notes[1].startswith("parse_error")


def test_record_is_idempotent(tmp_path: Path) -> None:
    from nba.lineups.store import record_snapshot

    lcon = connect_lineups(tmp_path / "l.duckdb")
    raw = _fetch_of(FIXTURE.read_bytes(), datetime(2026, 10, 9, 13, 0))
    rows = parse_daily_lineups(raw.body)
    a = record_snapshot(lcon, raw, D, rows, None)
    b = record_snapshot(lcon, raw, D, rows, None)
    assert a == b
    assert lcon.execute("SELECT count(*) FROM lineup_snapshots").fetchone() == (48,)


def test_read_only_missing_file_is_empty_store(tmp_path: Path) -> None:
    con = connect_lineups(tmp_path / "absent.duckdb", read_only=True)
    assert con.execute("SELECT count(*) FROM lineup_snapshots").fetchone() == (0,)
    assert not (tmp_path / "absent.duckdb").exists()


_ = (UTC, json, duckdb)
