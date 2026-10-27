"""nba.ops.coverage on synthetic stores: each check's OK / WARN / FAIL branch and the exit codes."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import polars as pl
import pytest

from nba.ops import coverage as cv

D = date(2026, 10, 20)
TIP = datetime(2026, 10, 20, 23, 0)  # naive UTC = 19:00 ET
HOME, AWAY, OTHER = 1610612737, 1610612738, 1610612739
GID = "0022600002"
UTC_TZ = ZoneInfo("UTC")


def _paths(root: Path) -> cv.Paths:
    return cv.Paths.under(root, D)


def _mk(root: Path, *, in_games: bool = True, real_tip: bool = True) -> cv.Paths:
    p = _paths(root)
    for d in (p.schedule_cache, p.schedule_dir, p.rosters, p.ops, p.market_db.parent):
        d.mkdir(parents=True, exist_ok=True)
    (p.schedule_cache / "schedule_2026-27.json").write_text(
        json.dumps([[GID, "2026-10-20T23:00:00", HOME, AWAY]])
    )
    if real_tip:
        pl.DataFrame(
            {
                "gameId": [GID],
                "gameDateTimeUTC": ["2026-10-20T23:00:00Z"],
                "gameDateTimeEst": ["2026-10-20T19:00:00Z"],
            }
        ).write_parquet(p.schedule_dir / "live_tips_2026-27.parquet")
    for team, base in ((HOME, 100), (AWAY, 200)):
        pl.DataFrame(
            {"team_id": [team] * 14, "player_id": list(range(base, base + 14))}
        ).write_parquet(p.rosters / f"{team}.parquet")
    pl.DataFrame({"team_id": [OTHER] * 14, "player_id": list(range(300, 314))}).write_parquet(
        p.rosters / f"{OTHER}.parquet"
    )
    n = duckdb.connect(str(p.nba_db))
    n.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INTEGER, home_team INTEGER,"
        " away_team INTEGER)"
    )
    if in_games:
        n.execute("INSERT INTO games VALUES (?, ?, 2026, ?, ?)", [GID, D, HOME, AWAY])
    n.execute(
        "CREATE TABLE player_availability (player_id INTEGER, as_of TIMESTAMP, game_id VARCHAR,"
        " status VARCHAR)"
    )
    n.execute(
        "CREATE TABLE forward_predictions (run_id VARCHAR, made_at TIMESTAMP, game_id VARCHAR,"
        " tipoff TIMESTAMP, model_name VARCHAR, target VARCHAR, player_id INTEGER)"
    )
    n.execute(
        "CREATE TABLE forward_scores (scored_at TIMESTAMP, game_id VARCHAR, model_name VARCHAR)"
    )
    n.close()
    m = duckdb.connect(str(p.market_db))
    m.execute(
        "CREATE TABLE live_book_odds (game_id VARCHAR, snapshot_kind VARCHAR,"
        " received_at TIMESTAMP,"
        " tipoff TIMESTAMP, commence_time TIMESTAMP, market VARCHAR, player_id INTEGER,"
        " side VARCHAR, point DOUBLE, price_american INTEGER)"
    )
    m.close()
    return p


def _w(p: cv.Paths, sql: str, args: list[object] | None = None) -> None:
    c = duckdb.connect(str(p.nba_db))
    c.execute(sql, args or [])
    c.close()


def _forecast(p: cv.Paths, player: int, made: datetime, model: str = cv.PROPS_MODEL) -> None:
    _w(
        p,
        "INSERT INTO forward_predictions VALUES ('r', ?, ?, ?, ?, 'pts', ?)",
        [made, GID, TIP, model, player],
    )


def _quote(p: cv.Paths, player: int, received: datetime, market: str = "player_points") -> None:
    m = duckdb.connect(str(p.market_db))
    m.execute(
        "INSERT INTO live_book_odds VALUES (?, 'close', ?, ?, ?, ?, ?, 'over', 20.5, -110)",
        [GID, received, TIP, TIP, market, player],
    )
    m.close()


def _by(res: list[cv.Result], prefix: str) -> list[cv.Result]:
    return [r for r in res if r.check.startswith(prefix)]


def _run(p: cv.Paths) -> list[cv.Result]:
    return cv.run(p, log_tz=UTC_TZ, sleep=lambda s: None)


# --- a. schedule


def test_schedule_ok_missing_and_proxy(tmp_path: Path) -> None:
    p = _mk(tmp_path)
    assert _by(_run(p), "schedule")[0].status == cv.OK
    assert _by(_run(p), "tips")[0].status == cv.OK
    p2 = _mk(tmp_path / "b", in_games=False, real_tip=False)
    res = _run(p2)
    assert _by(res, "schedule")[0].status == cv.FAIL
    assert GID in _by(res, "schedule")[0].text
    assert _by(res, "tips")[0].status == cv.WARN


def test_schedule_mismatch_fails(tmp_path: Path) -> None:
    p = _mk(tmp_path)
    _w(p, "UPDATE games SET home_team = ?", [AWAY])
    assert _by(_run(p), "schedule")[0].status == cv.FAIL


def test_no_games_is_ok_and_unreadable_cache_fails(tmp_path: Path) -> None:
    p = _mk(tmp_path)
    r = cv.run(cv.Paths.under(tmp_path, date(2026, 10, 25)), log_tz=UTC_TZ)
    assert [x.status for x in r] == [cv.OK]
    (p.schedule_cache / "schedule_2026-27.json").unlink()
    assert cv.run(p, log_tz=UTC_TZ)[0].status == cv.FAIL


# --- b. roster


def test_roster_files_and_placement(tmp_path: Path) -> None:
    p = _mk(tmp_path)
    _forecast(p, 100, TIP - timedelta(minutes=90))  # home
    _forecast(p, 999, TIP - timedelta(minutes=90))  # in no roster
    res = _run(p)
    assert _by(res, "roster_files")[0].status == cv.OK
    pl_ = _by(res, "roster_placement")[0]
    assert pl_.status == cv.WARN and pl_.data["placed"] == 1 and pl_.data["unknown"] == 1
    _forecast(p, 300, TIP - timedelta(minutes=90))  # rostered on a team not in the game
    assert _by(_run(p), "roster_placement")[0].status == cv.FAIL
    (p.rosters / f"{AWAY}.parquet").unlink()
    assert _by(_run(p), "roster_files")[0].status == cv.FAIL


def test_placement_uses_latest_row_only(tmp_path: Path) -> None:
    p = _mk(tmp_path)
    _forecast(p, 300, TIP - timedelta(minutes=180))  # stale misplacement ...
    _forecast(p, 300, TIP - timedelta(minutes=90))  # ... same key, later row, still misplaced
    assert _by(_run(p), "roster_placement")[0].data["misplaced"] == 1


# --- c. injury + pretip run line


def test_injury_branches(tmp_path: Path) -> None:
    p = _mk(tmp_path)
    assert _by(_run(p), "injury")[0].status == cv.FAIL  # no rows
    tip_et = datetime(2026, 10, 20, 19, 0)
    _w(
        p,
        "INSERT INTO player_availability VALUES (1, ?, ?, 'out')",
        [tip_et - timedelta(minutes=45), GID],
    )
    r = _by(_run(p), "injury")[0]
    assert r.status == cv.WARN
    _w(
        p,
        "INSERT INTO player_availability VALUES (2, ?, ?, 'out')",
        [tip_et - timedelta(minutes=75), GID],
    )
    _w(
        p,
        "INSERT INTO player_availability VALUES (3, ?, ?, 'available')",
        [tip_et - timedelta(minutes=75), GID],
    )
    r = _by(_run(p), "injury")[0]
    assert r.status == cv.OK and r.data["out"] == 1 and round(r.data["minutes_before_tip"]) == 75


PRETIP = """[2026-10-20 12:20:00] start predict
run abc123 date=2026-10-20 slate=1 predicted=1 rows=4 deduped=734 injury=ok (2026-10-20T13:45:00.1)
models: {'roster_source': 'official rosters for 30/30 teams; cap', 'x': 'y'}
"""


def test_pretip_run_line_parsed(tmp_path: Path) -> None:
    runs = cv.parse_pretip_runs(PRETIP)
    assert runs[0]["deduped"] == 734 and runs[0]["injury"] == "ok"
    assert runs[0]["roster_source"].startswith("official rosters for 30/30")
    p = _mk(tmp_path)
    p.log("pretip").write_text(PRETIP)
    res = _run(p)
    assert _by(res, "pretip_run")[0].status == cv.OK
    assert _by(res, "deduped")[0].data["deduped"] == 734
    p.log("pretip").write_text(
        PRETIP.replace("official rosters for 30/30 teams; cap", "official FAILED -> recent")
    )
    assert _by(_run(p), "pretip_run")[0].status == cv.WARN


# --- d. lines and pairing


def test_pairing_branches(tmp_path: Path) -> None:
    p = _mk(tmp_path)
    assert _by(_run(p), "lines")[0].status == cv.WARN  # no snapshots
    q = TIP - timedelta(minutes=60)
    for pid in (100, 101, 102, 103, 104):
        _quote(p, pid, q)
    _quote(p, 100, q, market="player_points_alternate")  # alt ladder is not a main line
    for pid in (100, 101, 102, 103, 104):  # forecasts BEFORE the quote: nothing pairs
        _forecast(p, pid, q - timedelta(minutes=5))
    res = _run(p)
    assert _by(res, "lines")[0].status == cv.OK
    assert _by(res, "pairing")[0].status == cv.FAIL and _by(res, "pairing")[0].data["paired"] == 0
    for pid in (100, 101, 102):  # a later forecast pairs three of five
        _forecast(p, pid, q + timedelta(minutes=5))
    r = _by(_run(p), "pairing")[0]
    assert r.status == cv.WARN and r.data["paired"] == 3 and r.data["quoted"] == 5
    for pid in (103, 104):
        _forecast(p, pid, q + timedelta(minutes=5))
    assert _by(_run(p), "pairing")[0].status == cv.OK


def _stamp(p: cv.Paths, made: datetime, model: str = cv.PROPS_MODEL) -> None:
    c = duckdb.connect(str(p.nba_db))
    c.execute(
        "CREATE TABLE IF NOT EXISTS forward_model_stamps"
        " (run_id VARCHAR, model_name VARCHAR, made_at TIMESTAMP)"
    )
    c.execute("INSERT INTO forward_model_stamps VALUES ('s', ?, ?)", [model, made])
    c.close()


def test_deduped_forecast_pairs_against_the_last_confirming_run(tmp_path: Path) -> None:
    # 2026-10-26 slate: ONE stored write at 12:00Z, lines only from T-90, later pretip runs wrote
    # (write-on-change) but stamped forward_model_stamps. Before the fix: 0 of N paired -> FAIL.
    p = _mk(tmp_path)
    first_line = TIP - timedelta(minutes=63)
    for pid in range(100, 153):
        _forecast(p, pid, TIP.replace(hour=12))
        _quote(p, pid, first_line)
    r = _by(_run(p), "pairing")[0]
    assert (
        r.status == cv.FAIL and r.data["paired"] == 0
    )  # no stamps: the old behaviour is the fallback
    _stamp(p, first_line + timedelta(minutes=2))  # a pretip run after the first quote re-confirmed
    r = _by(_run(p), "pairing")[0]
    assert r.status == cv.OK and r.data["paired"] == 53 and r.data["quoted"] == 53
    assert r.data["paired_stored_made_at"] == 0


def test_confirming_run_must_be_before_tip_and_the_props_model(tmp_path: Path) -> None:
    p = _mk(tmp_path)
    _forecast(p, 100, TIP.replace(hour=12))
    _quote(p, 100, TIP - timedelta(minutes=60))
    _stamp(
        p, TIP - timedelta(minutes=30), model="rung0_mov_elo"
    )  # another model's run: not a confirmation
    _stamp(p, TIP + timedelta(minutes=5))  # after tip: never a confirmation
    assert _by(_run(p), "pairing")[0].data["paired"] == 0
    _stamp(p, TIP - timedelta(minutes=30))
    assert _by(_run(p), "pairing")[0].data["paired"] == 1


def test_a_run_before_the_first_quote_does_not_pair(tmp_path: Path) -> None:
    p = _mk(tmp_path)
    _forecast(p, 100, TIP.replace(hour=12))
    _quote(p, 100, TIP - timedelta(minutes=60))
    _stamp(p, TIP - timedelta(minutes=90))
    assert _by(_run(p), "pairing")[0].data["paired"] == 0


def test_quotes_after_tip_do_not_count(tmp_path: Path) -> None:
    p = _mk(tmp_path)
    _quote(p, 100, TIP + timedelta(minutes=1))
    _forecast(p, 100, TIP - timedelta(minutes=30))
    r = _by(_run(p), "pairing")[0]
    assert r.data["quoted"] == 0


# --- e. forecasts


def test_forecast_timing(tmp_path: Path) -> None:
    p = _mk(tmp_path)
    assert _by(_run(p), "forecasts")[0].status == cv.FAIL  # none at all
    _forecast(p, 100, TIP - timedelta(minutes=80))
    r = _by(_run(p), "forecasts")[0]
    assert r.status == cv.OK and round(r.data["minutes_before_tip"]) == 80
    _forecast(p, 101, TIP + timedelta(minutes=2))
    assert _by(_run(p), "forecasts")[0].status == cv.FAIL


# --- f. jobs


def _startlines(times: list[str]) -> str:
    return "".join(f"[2026-10-20 {t}] start predict\n" for t in times)


def test_pretip_gap_detection(tmp_path: Path) -> None:
    p = _mk(tmp_path)
    p.log("pretip").write_text(_startlines(["19:20:00", "19:50:00", "20:20:00"]))
    assert _by(_run(p), "jobs_pretip")[0].status == cv.OK
    p.log("pretip").write_text(_startlines(["19:20:00", "20:20:00", "20:50:00"]))
    r = _by(_run(p), "jobs_pretip")[0]
    assert r.status == cv.FAIL and "did not run" in r.text and len(r.data["gaps"]) == 1


def test_alerts_and_other_logs(tmp_path: Path) -> None:
    p = _mk(tmp_path)
    p.log("pretip").write_text(
        _startlines(["15:20:00"]) + "[2026-10-20 15:21:00] ALERT: predict failed (rc=5)\n"
    )
    p.log("lineups").write_text("[2026-10-20 15:00:00] run-t30 rc=0\n")
    p.log("app_export").write_text(
        "[2026-10-20 15:00:00] start export for x\n[2026-10-20 15:01:00] skipped (1)\n"
    )
    res = _run(p)
    a = _by(res, "alerts")[0]
    assert a.status == cv.WARN and "predict failed (rc=5)" in a.text
    assert _by(res, "jobs_lineups")[0].data["ticks"] == 1
    assert _by(res, "jobs_app_export")[0].data == {"starts": 1, "skipped": 1}
    assert _by(_run(_mk(tmp_path / "z")), "alerts")[0].status == cv.OK


def test_alert_echo_does_not_feed_back(tmp_path: Path) -> None:
    """The job script appends the check's output to the pretip log. Echoed ALERT lines must not
    count as new alerts (2026-10-21..23: the log doubled per run to 9.9 GB, then OOM)."""
    p = _mk(tmp_path)
    p.log("pretip").write_text(
        _startlines(["15:20:00"]) + "[2026-10-20 15:21:00] ALERT: predict failed (rc=5)\n"
    )
    counts = []
    for _ in range(3):
        res = _run(p)
        a = _by(res, "alerts")[0]
        counts.append(len(a.data["alerts"]))
        with p.log("pretip").open("a") as f:  # what `step coverage` does with the printed report
            f.write(a.text.replace("\n    ", "\n    pretip_x.log: ") + "\n")
    assert counts == [1, 1, 1]
    assert len(_by(_run(p), "alerts")[0].data["alerts"]) == 1


def test_alert_list_is_capped(tmp_path: Path) -> None:
    p = _mk(tmp_path)
    p.log("pretip").write_text(
        _startlines(["15:20:00"])
        + "".join(f"[2026-10-20 15:{m:02d}:00] ALERT: x{m}\n" for m in range(60))
    )
    a = _by(_run(p), "alerts")[0]
    assert len(a.data["alerts"]) == 60 and "... 10 more" in a.text


# --- g. scoring


def test_scoring_before_final(tmp_path: Path) -> None:
    p = _mk(tmp_path)
    assert _by(_run(p), "scoring")[0].status == cv.OK
    _w(p, "INSERT INTO forward_scores VALUES (?, ?, 'm')", [TIP + timedelta(hours=5), GID])
    assert _by(_run(p), "scoring")[0].status == cv.OK
    _w(p, "INSERT INTO forward_scores VALUES (?, ?, 'early')", [TIP + timedelta(hours=1), GID])
    r = _by(_run(p), "scoring")[0]
    assert r.status == cv.FAIL and "early" in r.text


# --- driver


def test_exit_codes_and_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _mk(tmp_path)
    _forecast(p, 100, TIP - timedelta(minutes=80))
    tip_et = datetime(2026, 10, 20, 19, 0)
    _w(
        p,
        "INSERT INTO player_availability VALUES (2, ?, ?, 'out')",
        [tip_et - timedelta(minutes=75), GID],
    )
    assert cv.main(["--date", "2026-10-20", "--root", str(tmp_path), "--log-tz", "UTC"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("== coverage 2026-10-20 (") and "FAIL" not in out
    _w(p, "INSERT INTO forward_scores VALUES (?, ?, 'early')", [TIP + timedelta(hours=1), GID])
    assert (
        cv.main(["--date", "2026-10-20", "--root", str(tmp_path), "--json", "--log-tz", "UTC"]) == 3
    )
    doc = json.loads(capsys.readouterr().out)
    assert doc["exit"] == 3 and any(c["status"] == "FAIL" for c in doc["checks"])


def test_lock_retry_then_gives_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[float] = []

    def boom(*a: object, **k: object) -> None:
        raise duckdb.IOException("Could not set lock on file")

    monkeypatch.setattr(cv.duckdb, "connect", boom)
    with pytest.raises(duckdb.IOException):
        cv.connect_ro(tmp_path / "x.duckdb", sleep=calls.append, retry_s=30, max_s=90)
    assert calls == [30, 30, 30]
