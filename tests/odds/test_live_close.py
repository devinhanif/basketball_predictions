"""Live closing-line capture (the-odds-api.com): schema, no post-tip write, idempotence, key
stripping, window selection, flag-off no-op. A fake ``http_get`` and tmp files only: no network,
no ``nba.duckdb``."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.lineups.store import DDL as LINEUPS_DDL
from nba.odds import live_close as lc
from nba.odds.__main__ import main
from nba.odds.the_odds_api import HistoryConfig, parse_event_odds, parse_events
from nba.odds.theoddsapi import NameResolver

KEY = "live-test-key-ABCDEF0123456789"
PHI, NYK = 1610612755, 1610612752
SLATE = date(2026, 10, 20)
TIP = datetime(2026, 10, 20, 23, 30)  # naive UTC, 7:30 pm EDT
RES = NameResolver(teams={"philadelphia 76ers": PHI, "new york knicks": NYK}, players={})
ENV_ON = {"NBA_CLOSE_CAPTURE": "1", "THE_ODDS_API_KEY": KEY}


@dataclass
class FakeResp:
    status_code: int = 200
    payload: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return json.dumps(self.payload)

    def json(self) -> Any:
        return self.payload


def event_odds_body(commence: str = "2026-10-20T23:30:00Z") -> dict[str, Any]:
    def mk(key: str, outs: list[dict[str, Any]]) -> dict[str, Any]:
        return {"key": key, "last_update": "2026-10-20T23:22:00Z", "outcomes": outs}

    return {
        "id": "ev1",
        "commence_time": commence,
        "home_team": "Philadelphia 76ers",
        "away_team": "New York Knicks",
        "bookmakers": [
            {
                "key": "pinnacle",
                "markets": [
                    mk(
                        "h2h",
                        [
                            {"name": "Philadelphia 76ers", "price": -150},
                            {"name": "New York Knicks", "price": 130},
                        ],
                    ),
                    mk(
                        "player_points",
                        [
                            {
                                "name": "Over",
                                "description": "Tyrese Maxey",
                                "price": -115,
                                "point": 27.5,
                            },
                            {
                                "name": "Under",
                                "description": "Tyrese Maxey",
                                "price": -105,
                                "point": 27.5,
                            },
                        ],
                    ),
                ],
            }
        ],
    }


class FakeHttp:
    def __init__(
        self, odds: FakeResp | None = None, commence: str = "2026-10-20T23:30:00Z"
    ) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.odds = odds or FakeResp(
            200,
            event_odds_body(commence),
            {"x-requests-last": "14", "x-requests-remaining": "4999000"},
        )
        self.events = FakeResp(
            200,
            [
                {
                    "id": "ev1",
                    "commence_time": commence,
                    "home_team": "Philadelphia 76ers",
                    "away_team": "New York Knicks",
                }
            ],
            {"x-requests-last": "0", "x-requests-remaining": "4999014"},
        )

    def __call__(self, url: str, **kw: Any) -> FakeResp:
        self.calls.append((url, dict(kw.get("params") or {})))
        return self.odds if url.endswith("/odds") else self.events

    @property
    def n_odds(self) -> int:
        return sum(1 for u, _ in self.calls if u.endswith("/odds"))


@pytest.fixture
def cfg(tmp_path: Path) -> lc.LiveConfig:
    tips = tmp_path / "lineups.duckdb"
    con = duckdb.connect(str(tips))
    con.execute(LINEUPS_DDL)
    con.execute("INSERT INTO game_tips VALUES ('G1', ?, ?, 'schedule', ?)", [SLATE, TIP, TIP])
    for team, home in ((PHI, True), (NYK, False)):
        con.execute(
            "INSERT INTO lineup_snapshots (game_date, game_id, team_id, is_home) VALUES (?,?,?,?)",
            [SLATE, "G1", team, home],
        )
    con.close()
    hist = HistoryConfig(min_interval_s=0.0, retry_backoff_s=0.0, ledger_path=tmp_path / "h.json")
    return lc.LiveConfig(
        hist=hist,
        out_db=tmp_path / "market_asof.duckdb",
        tips_db=tips,
        raw_dir=tmp_path / "raw",
        ledger_dir=tmp_path / "budget",
    )


def at(minutes_before_tip: float) -> Any:
    t = (TIP - timedelta(minutes=minutes_before_tip)).replace(tzinfo=UTC)
    return lambda: t


def run(cfg: lc.LiveConfig, http: FakeHttp, now_fn: Any, **kw: Any) -> lc.LiveReport:
    return lc.run_live_capture(
        SLATE, kw.pop("kind", "close"), environ=ENV_ON, now_fn=now_fn, cfg=cfg, resolver=RES,
        sleep=lambda _s: None, http_get=http, **kw,
    )  # fmt: skip


def rows(cfg: lc.LiveConfig, sql: str) -> list[tuple[Any, ...]]:
    con = duckdb.connect(str(cfg.out_db), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ---- schema ------------------------------------------------------------------------------------


def test_schema_and_row_content(cfg: lc.LiveConfig) -> None:
    rep = run(cfg, FakeHttp(), at(6))
    assert rep.pulled == 1 and rep.inserted == 4 and not rep.degraded
    q = (
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'live_book_odds' ORDER BY ordinal_position"
    )
    cols = [r[0] for r in rows(cfg, q)]
    assert cols == lc._ROW_COLS
    (r,) = rows(
        cfg,
        "SELECT source, game_id, snapshot_kind, requested_at, received_at, tipoff, commence_time, "
        "book, market, player_name, side, point, price_american FROM live_book_odds "
        "WHERE market = 'player_points' AND side = 'over'",
    )
    assert r[:3] == ("the_odds_api_live", "G1", "close")
    assert r[3] == r[4] == TIP - timedelta(minutes=6) and r[5] == r[6] == TIP
    assert r[7:] == ("pinnacle", "player_points", "Tyrese Maxey", "over", 27.5, -115)
    (log,) = rows(cfg, "SELECT status, n_rows, credits FROM live_capture_log")
    assert log == ("ok", 4, 14)


def test_credit_rule_and_guard(cfg: lc.LiveConfig) -> None:
    assert cfg.markets == cfg.hist.game_markets + cfg.hist.prop_markets
    assert cfg.expected_cost() == 1 * 7 * 2  # (3 game + 4 prop markets) x (us, eu), 1 credit each
    tight = replace(cfg, daily_max_credits=10)
    http = FakeHttp()
    rep = run(tight, http, at(6))
    assert rep.degraded and http.n_odds == 0 and rep.inserted == 0
    assert rows(tight, "SELECT status FROM live_capture_log") == [("credit_guard",)]


# ---- known-at: no write after tip --------------------------------------------------------------


def test_write_snapshot_refuses_at_or_after_tip(cfg: lc.LiveConfig) -> None:
    con = lc.open_store(cfg.out_db)
    g = lc.LiveGame("G1", TIP, PHI, NYK)
    frame = parse_event_odds(
        {"timestamp": "2026-10-20T23:20:00Z", "data": event_odds_body()},
        season=0, game_id="G1", snapshot_kind="close", requested_at=datetime(2026, 10, 20, 23, 20),
        raw_file="x", resolver=RES,
    ).rows  # fmt: skip
    for recv in (TIP, TIP + timedelta(seconds=1)):
        with pytest.raises(lc.PostTipError):
            lc.write_snapshot(con, frame, game=g, kind="close", slate=SLATE, requested_at=TIP,
                              received_at=recv, commence_time=TIP, raw_file="x")  # fmt: skip
    # vendor commence earlier than our tip also refuses
    with pytest.raises(lc.PostTipError):
        lc.write_snapshot(con, frame, game=g, kind="close", slate=SLATE, requested_at=TIP,
                          received_at=TIP - timedelta(minutes=1),
                          commence_time=TIP - timedelta(minutes=2), raw_file="x")  # fmt: skip
    n = con.execute("SELECT count(*) FROM live_book_odds").fetchone()
    con.close()
    assert n == (0,)


def test_response_arriving_after_tip_is_not_stored(cfg: lc.LiveConfig) -> None:
    t = TIP.replace(tzinfo=UTC)
    # clock reads: window check, client day, request time, response time (after tip)
    it = iter(
        [
            t - timedelta(minutes=5),
            t - timedelta(minutes=5),
            t - timedelta(seconds=2),
            t + timedelta(seconds=3),
        ]
    )
    rep = run(cfg, FakeHttp(), lambda: next(it))
    assert rep.inserted == 0 and rep.statuses["post_tip_refused"] == 1
    assert rows(cfg, "SELECT count(*) FROM live_book_odds") == [(0,)]
    assert list(cfg.raw_dir.glob("G1/*.json.gz"))  # the raw response is still kept


def test_game_already_tipped_is_never_requested(cfg: lc.LiveConfig) -> None:
    http = FakeHttp()
    rep = run(cfg, http, at(-1))  # one minute after tip
    assert rep.due == 0 and http.calls == []


def test_vendor_commence_before_now_is_refused_before_the_request(cfg: lc.LiveConfig) -> None:
    http = FakeHttp(commence="2026-10-20T23:20:00Z")  # vendor says it tipped 10 min ago... at T-6
    rep = run(cfg, http, at(6))
    # event is outside the 30-min match window? no: 10 min off, matched; but commence < now
    assert http.n_odds == 0 and rep.statuses["post_tip_refused"] == 1


# ---- idempotence -------------------------------------------------------------------------------


def test_second_tick_is_a_no_op(cfg: lc.LiveConfig) -> None:
    http = FakeHttp()
    first = run(cfg, http, at(8))
    second = run(cfg, http, at(3))
    assert first.inserted == 4 and second.inserted == 0 and second.pulled == 0
    assert http.n_odds == 1 and sum(1 for u, _ in http.calls if u.endswith("/events")) == 1
    assert rows(cfg, "SELECT count(*) FROM live_book_odds") == [(4,)]


def test_kinds_are_independent_and_write_snapshot_is_idempotent(cfg: lc.LiveConfig) -> None:
    http = FakeHttp()
    run(cfg, http, at(8), kind="close")
    run(cfg, http, at(60), kind="t60")
    assert rows(
        cfg, "SELECT snapshot_kind, count(*) FROM live_book_odds GROUP BY 1 ORDER BY 1"
    ) == [
        ("close", 4),
        ("t60", 4),
    ]
    con = lc.open_store(cfg.out_db)
    g = lc.LiveGame("G1", TIP, PHI, NYK)
    frame = con.execute("SELECT * FROM live_book_odds WHERE snapshot_kind = 'close'").pl()
    again = lc.write_snapshot(
        con, frame.drop(["source", "game_id", "slate_date", "snapshot_kind", "requested_at",
                         "received_at", "tipoff", "commence_time", "raw_file"]),
        game=g, kind="close", slate=SLATE, requested_at=at(8)(), received_at=at(8)(),
        commence_time=TIP, raw_file="y",
    )  # fmt: skip
    con.close()
    assert again == 0


def test_failed_pull_retries_then_stops_at_the_attempt_cap(cfg: lc.LiveConfig) -> None:
    bad = FakeResp(422, {"message": "bad"}, {})
    http = FakeHttp(odds=bad)
    for _ in range(3):
        run(cfg, http, at(8))
    assert http.n_odds == cfg.max_attempts_per_game == 2
    assert rows(cfg, "SELECT status FROM live_capture_log") == [("error",), ("error",)]


# ---- key stripping -----------------------------------------------------------------------------


def test_key_never_stored_or_reported(cfg: lc.LiveConfig, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    http = FakeHttp()
    rep = run(cfg, http, at(8))
    assert all(p.get("apiKey") == KEY for _, p in http.calls)  # sent on the wire only
    blobs = []
    for p in cfg.raw_dir.glob("G1/*.json.gz"):
        with gzip.open(p, "rt") as fh:
            blobs.append(fh.read())
    assert blobs and all(KEY not in b and "apiKey" not in b for b in blobs)
    for f in list(cfg.ledger_dir.glob("*")):
        assert KEY not in f.read_text()
    assert KEY not in caplog.text and KEY not in "\n".join(rep.lines())
    stored = rows(cfg, "SELECT raw_file FROM live_book_odds") + rows(
        cfg, "SELECT note FROM live_capture_log"
    )
    assert all(KEY not in str(r) for r in stored)
    cli = lc.LiveClient(cfg, environ=ENV_ON, http_get=http)
    assert KEY not in repr(cli) and KEY not in str(cli)


def test_key_in_error_body_is_scrubbed(cfg: lc.LiveConfig) -> None:
    leaky = FakeResp(401, {"message": f"bad apiKey={KEY}"}, {})
    rep = run(cfg, FakeHttp(odds=leaky), at(8))
    text = "\n".join(rep.lines())
    assert rep.degraded and KEY not in text
    assert all(KEY not in str(r) for r in rows(cfg, "SELECT * FROM live_capture_log"))


def test_missing_key_is_degraded_not_a_crash(cfg: lc.LiveConfig) -> None:
    rep = lc.run_live_capture(
        SLATE, "close", environ={"NBA_CLOSE_CAPTURE": "1"}, now_fn=at(8), cfg=cfg, resolver=RES,
        http_get=FakeHttp(),
    )  # fmt: skip
    assert rep.degraded and rep.inserted == 0


# ---- window selection --------------------------------------------------------------------------


def test_window_selection(cfg: lc.LiveConfig) -> None:
    def g(mins: float) -> lc.LiveGame:
        return lc.LiveGame(
            f"g{mins}", datetime(2026, 10, 20, 23, 0) + timedelta(minutes=mins), PHI, NYK
        )

    now = datetime(2026, 10, 20, 23, 0, tzinfo=UTC)
    games = [g(-1), g(0), g(0.5), g(5), g(10), g(10.01), g(55), g(65), g(70)]
    close = {x.game_id for x in lc.select_window(games, now, "close", cfg)}
    assert close == {"g0.5", "g5", "g10"}  # tip == now is already tipped; > +10 min is too early
    t60 = {x.game_id for x in lc.select_window(games, now, "t60", cfg)}
    assert t60 == {"g55", "g65"}
    with pytest.raises(ValueError):
        lc.select_window(games, now, "t5", cfg)


def test_slate_reader_uses_real_tips_only(cfg: lc.LiveConfig, tmp_path: Path) -> None:
    assert lc.read_slate(cfg.tips_db, SLATE) == [lc.LiveGame("G1", TIP, PHI, NYK)]
    assert lc.read_slate(cfg.tips_db, date(2026, 10, 21)) == []
    assert lc.read_slate(tmp_path / "missing.duckdb", SLATE) == []


def test_no_game_in_window_touches_nothing(cfg: lc.LiveConfig) -> None:
    http = FakeHttp()
    rep = run(cfg, http, at(30))
    assert rep.due == 0 and http.calls == [] and not cfg.out_db.exists()


def test_match_needs_both_teams(cfg: lc.LiveConfig) -> None:
    ev = parse_events(
        {
            "data": [
                {
                    "id": "e",
                    "commence_time": "2026-10-20T23:30:00Z",
                    "home_team": "New York Knicks",
                    "away_team": "Philadelphia 76ers",
                }
            ]
        }  # fmt: skip
    )
    g = lc.LiveGame("G1", TIP, PHI, NYK)
    assert lc.match_live_event(ev, g, 1, RES, 30) is None  # home/away swapped
    unseen = lc.LiveGame("G1", TIP, 0, 0)
    assert lc.match_live_event(ev, unseen, 1, RES, 30) is not None
    assert lc.match_live_event(ev, unseen, 2, RES, 30) is None  # two games share the tip


# ---- flag off ----------------------------------------------------------------------------------


def test_flag_off_is_a_no_op(cfg: lc.LiveConfig) -> None:
    http = FakeHttp()
    for env in ({}, {"NBA_CLOSE_CAPTURE": "0"}, {"NBA_CLOSE_CAPTURE": "true"}):
        rep = lc.run_live_capture(
            SLATE, "close", environ=env, now_fn=at(6), cfg=cfg, resolver=RES, http_get=http
        )
        assert rep.disabled and rep.inserted == 0 and "disabled" in rep.lines()[0]
    assert http.calls == [] and not cfg.out_db.exists() and not cfg.raw_dir.exists()
    assert not cfg.ledger_dir.exists()


def test_cli_flag_off_prints_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.delenv("NBA_CLOSE_CAPTURE", raising=False)
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    rc = main(["capture", "--kind", "close", "--date", "2026-10-20"])
    out = capsys.readouterr().out
    assert rc == 0 and "disabled" in out and list(tmp_path.iterdir()) == []
