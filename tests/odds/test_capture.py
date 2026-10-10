"""Capture: raw-first, idempotent append, out-of-season handling, CLI exit codes. No network."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import httpx
import pytest

import nba.odds.theoddsapi as api
from nba.odds.__main__ import main
from nba.odds.capture import ensure_table, run_capture, slate_window_utc
from nba.odds.theoddsapi import KEY_ENV, NameResolver, OddsConfig, TheOddsApiClient
from tests.odds.conftest import TEST_KEY, FakeHttp, FakeResponse, probe_body, synthetic

SLATE = date(2026, 10, 20)  # ET date; the synthetic events tip 2026-10-21T00:00Z / 02:30Z
RES = NameResolver(
    teams={"san antonio spurs": 1, "oklahoma city thunder": 2, "la clippers": 3},
    players={"miles mcbride": 1630540},
)


def _route(http: FakeHttp) -> None:
    http.routes = {
        "/odds/": lambda: FakeResponse(200, synthetic("odds_live_synthetic")),
        "/props/": lambda: FakeResponse(200, synthetic("props_live_synthetic")),
    }


def _count(db: Path) -> int:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return int(con.execute("SELECT count(*) FROM odds_snapshots").fetchone()[0])  # type: ignore[index]
    finally:
        con.close()


def test_slate_window_is_the_et_day_in_utc() -> None:
    lo, hi = slate_window_utc(SLATE)  # EDT = UTC-4
    assert lo == datetime(2026, 10, 20, 4, tzinfo=UTC) and hi == datetime(
        2026, 10, 21, 4, tzinfo=UTC
    )
    lo, hi = slate_window_utc(date(2026, 11, 1))  # DST ends: the ET day is 25 hours long
    assert (hi - lo).total_seconds() == 25 * 3600


def test_capture_appends_and_is_idempotent(
    cfg: OddsConfig, client: TheOddsApiClient, http: FakeHttp
) -> None:
    _route(http)
    rep = run_capture(SLATE, client=client, resolver=RES)
    assert rep.requests_used == 2 and not rep.unreachable and not rep.store_failed
    assert rep.game_rows > 0 and rep.prop_rows > 0
    # /props/ has no date filter: the 2026-10-25 event is dropped locally and counted
    assert rep.reasons["outside_slate_window"] == 1
    assert rep.inserted == rep.game_rows + rep.prop_rows
    n = _count(cfg.out_db)
    assert n == rep.inserted
    # same minute again (reuse disabled so it re-requests): every key already stored -> 0 new rows
    client.config = OddsConfig(**{**cfg.__dict__, "reuse_window_s": 0.0})
    rep2 = run_capture(SLATE, client=client, resolver=RES)
    assert rep2.requests_used == 2 and rep2.inserted == 0 and _count(cfg.out_db) == n
    con = duckdb.connect(str(cfg.out_db), read_only=True)
    try:
        cols = {r[0] for r in con.execute("DESCRIBE odds_snapshots").fetchall()}
        assert {
            "capture_minute",
            "player_id",
            "implied_prob",
            "implied_prob_raw",
            "raw_file",
        } <= cols
        k = con.execute(
            "SELECT count(*) FROM (SELECT capture_minute, event_id, book, market, outcome_name, "
            "point FROM odds_snapshots GROUP BY ALL HAVING count(*) > 1)"
        ).fetchone()
        assert k == (0,)
        assert con.execute(
            "SELECT count(*) FROM odds_snapshots WHERE source <> 'theoddsapi'"
        ).fetchone() == (0,)
    finally:
        con.close()


def test_later_minute_is_a_new_snapshot(
    cfg: OddsConfig, monkeypatch: pytest.MonkeyPatch, http: FakeHttp
) -> None:
    monkeypatch.setenv(KEY_ENV, TEST_KEY)
    _route(http)
    t = [datetime(2026, 10, 20, 20, 0, 5, tzinfo=UTC)]
    c = TheOddsApiClient(cfg, clock=lambda: t[0], sleep=lambda _s: None)
    a = run_capture(SLATE, client=c, resolver=RES)
    t[0] = datetime(2026, 10, 20, 20, 5, 5, tzinfo=UTC)
    b = run_capture(SLATE, client=c, resolver=RES)
    assert a.inserted > 0 and b.inserted == a.inserted and _count(cfg.out_db) == 2 * a.inserted


def test_dry_run_hits_api_and_caches_raw_but_writes_no_db(
    cfg: OddsConfig, client: TheOddsApiClient, http: FakeHttp
) -> None:
    _route(http)
    rep = run_capture(SLATE, dry_run=True, client=client, resolver=RES)
    assert rep.requests_used == 2 and rep.game_rows > 0 and rep.inserted == 0
    assert not cfg.out_db.exists()
    assert len(list(cfg.raw_dir.glob("*/*.json.gz"))) == 2


def test_out_of_season_feed_is_not_an_error(
    cfg: OddsConfig, client: TheOddsApiClient, http: FakeHttp
) -> None:
    empty = probe_body("props_live")  # success:false, data:null, message "out of season"
    http.routes = {
        "/odds/": lambda: FakeResponse(200, empty),
        "/props/": lambda: FakeResponse(200, empty),
    }
    rep = run_capture(SLATE, client=client, resolver=RES)
    assert rep.empty and not rep.unreachable and rep.inserted == 0
    assert any("out of season" in m for m in rep.messages)
    assert not cfg.out_db.exists()  # nothing to store -> the DB is not even opened


def test_feed_unreachable_is_reported_not_raised(client: TheOddsApiClient, http: FakeHttp) -> None:
    http.script = [httpx.ConnectError("x")] * 6
    rep = run_capture(SLATE, client=client, resolver=RES)
    assert rep.unreachable and rep.empty and rep.requests_used == 6


def _patch_cfg(monkeypatch: pytest.MonkeyPatch, cfg: OddsConfig) -> None:
    monkeypatch.setattr(api, "load_config", lambda path=None: cfg)
    monkeypatch.setenv(KEY_ENV, TEST_KEY)
    monkeypatch.setattr(NameResolver, "default", classmethod(lambda cls: RES))


def test_cli_exit_codes(
    cfg: OddsConfig,
    http: FakeHttp,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cfg(monkeypatch, cfg)
    empty = probe_body("props_live")
    http.routes = {
        "/odds/": lambda: FakeResponse(200, empty),
        "/props/": lambda: FakeResponse(200, empty),
    }
    assert main(["capture", "--date", "2026-10-20"]) == 0
    assert "feed empty" in capsys.readouterr().out

    http.routes = {}
    http.script = [httpx.ConnectError("down")] * 10
    monkeypatch.setattr("time.sleep", lambda _s: None)
    assert main(["capture", "--date", "2026-10-21", "--dry-run"]) == 4

    monkeypatch.delenv(KEY_ENV)
    assert main(["capture", "--date", "2026-10-20"]) == 4
    assert TEST_KEY not in capsys.readouterr().err

    monkeypatch.setenv(KEY_ENV, TEST_KEY)
    monkeypatch.setattr(
        "nba.odds.__main__.run_capture", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bug"))
    )
    assert main(["capture", "--date", "2026-10-20"]) == 5  # never raises out of main


def test_cli_budget_refusal_is_informational(
    cfg: OddsConfig, http: FakeHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    _patch_cfg(monkeypatch, replace(cfg, daily_budget=0))
    assert main(["capture", "--date", "2026-10-20"]) == 4
    assert http.calls == []


def test_cli_status_prints_account(
    cfg: OddsConfig,
    http: FakeHttp,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_cfg(monkeypatch, cfg)
    http.script = [FakeResponse(200, probe_body("me"))]
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "tier=business" in out and "daily_limit=6667" in out and "used=1" in out
    assert TEST_KEY not in out


def test_ensure_table_does_not_touch_other_tables(tmp_path: Path) -> None:
    con = duckdb.connect(str(tmp_path / "m.duckdb"))
    con.execute("CREATE TABLE market_at_prediction (x INT)")
    con.execute("INSERT INTO market_at_prediction VALUES (1)")
    ensure_table(con)
    ensure_table(con)
    assert con.execute("SELECT count(*) FROM market_at_prediction").fetchone() == (1,)
    assert con.execute("SELECT count(*) FROM odds_snapshots").fetchone() == (0,)
    con.close()


def test_recorded_live_payload_appends_once(
    cfg: OddsConfig, client: TheOddsApiClient, http: FakeHttp
) -> None:
    http.routes = {
        "/odds/": lambda: FakeResponse(200, probe_body("odds_live_recorded")),
        "/props/": lambda: FakeResponse(200, probe_body("props_live")),
    }
    rep = run_capture(SLATE, client=client, resolver=RES)
    assert rep.game_rows == 26 and rep.prop_rows == 0 and rep.inserted == 26
    assert _count(cfg.out_db) == 26
