"""Live closing-line capture (the-odds-api.com): schema, no post-tip write, idempotence, key
stripping, window selection, flag-off no-op. A fake ``http_get`` and tmp files only: no network,
no ``nba.duckdb``."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import polars as pl
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
        games_db=tmp_path / "nba_missing.duckdb",
        raw_dir=tmp_path / "raw",
        ledger_dir=tmp_path / "budget",
        # the original two-point schedule (close tolerance 10 min); the full one is tested below
        schedule=(lc.ScheduleEntry("close", 0, 10), lc.ScheduleEntry("t60", 55, 65)),
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
        lc.select_window(games, now, "t5", cfg)  # not in the default two-entry schedule


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


# ---- pre-lineup slate: team ids from the official schedule (2026-10-26 incident) ---------------

BOS, MIA = 1610612738, 1610612748
RES4 = NameResolver(
    teams={
        "philadelphia 76ers": PHI,
        "new york knicks": NYK,
        "boston celtics": BOS,
        "miami heat": MIA,
    },
    players={},
)


@pytest.fixture(autouse=True)
def _no_real_schedule_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import nba.daily.schedule as sched

    monkeypatch.setattr(sched, "DEFAULT_SCHEDULE_CACHE_DIR", tmp_path / "no_cache")


def _prelineup(tmp_path: Path) -> tuple[Path, Path]:
    tmp_path = tmp_path / "prelineup"
    tmp_path.mkdir()
    tips = tmp_path / "lineups.duckdb"
    con = duckdb.connect(str(tips))
    con.execute(LINEUPS_DDL)  # no lineup_snapshots rows: lineups have not posted
    for gid in ("0022600001", "0022600002"):  # two games share the tip
        con.execute("INSERT INTO game_tips VALUES (?, ?, ?, 'schedule', ?)", [gid, SLATE, TIP, TIP])
    con.close()
    games = tmp_path / "nba.duckdb"
    con = duckdb.connect(str(games))
    con.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INT, home_team INT,"
        " away_team INT)"
    )
    con.execute(
        "INSERT INTO games VALUES ('0022600001', ?, 2026, ?, ?), ('0022600002', ?, 2026, ?, ?)",
        [SLATE, PHI, NYK, SLATE, BOS, MIA],
    )
    con.close()
    return tips, games


def _events_plus10() -> list[Any]:
    def ev(i: str, h: str, a: str) -> dict[str, str]:
        return {"id": i, "commence_time": "2026-10-20T23:40:00Z", "home_team": h, "away_team": a}

    return parse_events(
        {
            "data": [
                ev("a", "Philadelphia 76ers", "New York Knicks"),
                ev("b", "Boston Celtics", "Miami Heat"),
            ]
        }
    )


def test_prelineup_ids_filled_from_schedule_and_match_at_plus_10(tmp_path: Path) -> None:
    tips, games = _prelineup(tmp_path)
    # the old behaviour: ids 0, vendor +10 min is outside the 2-minute window -> unmatched
    old = lc.read_slate(tips, SLATE)
    assert [(g.home_team, g.away_team) for g in old] == [(0, 0), (0, 0)]
    ev = _events_plus10()
    assert all(lc.match_live_event(ev, g, 2, RES4, 30) is None for g in old)
    # the fix: schedule ids, team-matched inside the 30-minute window despite the shared tip
    new = lc.read_slate(tips, SLATE, games)
    assert [(g.home_team, g.away_team) for g in new] == [(PHI, NYK), (BOS, MIA)]
    got = [lc.match_live_event(ev, g, 2, RES4, 30) for g in new]
    assert [e.event_id if e else None for e in got] == ["a", "b"]
    # the window is still enforced: a 5-minute window refuses a +10 offset
    assert lc.match_live_event(ev, new[0], 2, RES4, 5) is None


def test_lineup_ids_win_over_schedule(cfg: lc.LiveConfig, tmp_path: Path) -> None:
    _, games = _prelineup(tmp_path)
    assert lc.read_slate(cfg.tips_db, SLATE, games) == [lc.LiveGame("G1", TIP, PHI, NYK)]


def test_schedule_lookup_failure_keeps_old_behaviour(tmp_path: Path) -> None:
    tips, _ = _prelineup(tmp_path)
    bad = tmp_path / "bad.duckdb"
    bad.write_text("not a duckdb file")
    for db in (tmp_path / "nope.duckdb", bad):
        out = lc.read_slate(tips, SLATE, db)
        assert [(g.home_team, g.away_team) for g in out] == [(0, 0), (0, 0)]


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


# ---- schedule, preseason skip, credit estimate --------------------------------------------------

LIVE_YAML = lc.CONFIG_PATH  # the real configs/odds_live.yaml: parsed here, never sent anywhere
EXPECTED = [
    "close",
    "t5",
    "t10",
    "t15",
    "t20",
    "t30",
    "t45",
    "t60",
    "t90",
    "t120",
    "t180",
    "morning",
]


def test_real_schedule_config_parses() -> None:
    c = lc.load_live_config()
    assert sorted(e.kind for e in c.schedule) == sorted(EXPECTED)
    by = {e.kind: e for e in c.schedule}
    assert by["t60"].lo == 55 and by["t60"].hi == 65 and not by["t60"].extended  # unchanged
    assert by["morning"].at_et == dtime(10, 0) and by["morning"].grace_min == 60
    assert (by["t180"].lo, by["t180"].hi) == (177.5, 182.5)
    assert c.plan.monthly_credits == 5_000_000 and c.plan.name == "START 5M"
    assert len(c.active_schedule()) == 12  # the full schedule fits the recorded plan
    assert [e.kind for e in lc.due_order(c.schedule)][0] == "close"
    assert len(c.markets) == 89 and c.regions == ("us", "eu", "us_ex")
    assert c.dropped_groups() == ()  # every group stays ON: the budget rule is met


def test_bad_schedule_rejected(tmp_path: Path) -> None:
    for bad in ("[{kind: soon, window: [1, 2]}]", "[{kind: t5, window: [9, 2]}]"):
        p = tmp_path / "x.yaml"
        p.write_text(f"schedule: {bad}\n")
        with pytest.raises(ValueError):
            lc.load_live_config(p)


def test_five_minute_ticks_hit_each_point_once() -> None:
    c = lc.load_live_config()
    tip = datetime(2026, 10, 20, 23, 30)
    hits: dict[str, int] = {}
    for k in range(0, 24 * 12):  # a tick every 5 minutes, from 24 h before the tip, aligned
        now = (tip - timedelta(minutes=5 * k)).replace(tzinfo=UTC)
        g = lc.LiveGame("G1", tip, PHI, NYK)
        for e in c.schedule:
            if e.at_et is None and lc.select_window([g], now, e.kind, c):
                hits[e.kind] = hits.get(e.kind, 0) + 1
    # t60 keeps the wide original window (55, 60, 65: one pull, the rest dedupe)
    assert hits == {k: (3 if k == "t60" else 1) for k in EXPECTED if k != "morning"}


def test_morning_window_is_et_clock_and_never_after_tip() -> None:
    c = lc.load_live_config()
    g = lc.LiveGame("G1", TIP, PHI, NYK)  # 19:30 EDT
    et = ZoneInfo("America/New_York")

    def now(h: int, m: int) -> datetime:
        return datetime(2026, 10, 20, h, m, tzinfo=et)

    assert lc.select_window([g], now(9, 55), "morning", c, SLATE) == []
    assert lc.select_window([g], now(10, 0), "morning", c, SLATE) == [g]
    assert lc.select_window([g], now(10, 59), "morning", c, SLATE) == [g]
    assert lc.select_window([g], now(11, 0), "morning", c, SLATE) == []
    early = lc.LiveGame("G0", datetime(2026, 10, 20, 14, 5), PHI, NYK)  # 10:05 EDT, already tipped
    assert lc.select_window([early], now(10, 10), "morning", c, SLATE) == []


def test_every_kind_refused_at_or_after_tip(cfg: lc.LiveConfig) -> None:
    c = lc.load_live_config()
    c = replace(cfg, schedule=c.schedule)
    for e in c.schedule:
        for late in (0, -1, -30):  # tip == now, and after
            http = FakeHttp()
            rep = run(c, http, at(late), kind=e.kind)
            assert http.n_odds == 0 and rep.inserted == 0
    assert not c.out_db.exists()  # nothing was even opened
    g = lc.LiveGame("G1", TIP, PHI, NYK)
    con = lc.open_store(cfg.out_db)
    try:
        for kind in ("t5", "t180", "morning"):  # the writer enforces it for the new kinds too
            with pytest.raises(lc.PostTipError):
                lc.write_snapshot(
                    con, pl.DataFrame(), game=g, kind=kind, slate=SLATE, requested_at=TIP,
                    received_at=TIP, commence_time=TIP, raw_file="x",
                )  # fmt: skip
        with pytest.raises(ValueError):
            lc.write_snapshot(
                con, pl.DataFrame(), game=g, kind="t5x", slate=SLATE, requested_at=TIP,
                received_at=TIP - timedelta(minutes=9), commence_time=TIP, raw_file="x",
            )  # fmt: skip
    finally:
        con.close()


def test_new_kind_is_stamped_and_independent(cfg: lc.LiveConfig) -> None:
    c = replace(cfg, schedule=lc.load_live_config().schedule)
    http = FakeHttp()
    rep = run(c, http, at(30.0), kind="t30")
    assert rep.inserted > 0 and http.n_odds == 1
    kinds = rows(c, "SELECT DISTINCT snapshot_kind FROM live_book_odds")
    assert kinds == [("t30",)]
    stamps = rows(
        c, "SELECT min(requested_at), min(received_at), min(last_update) FROM live_book_odds"
    )[0]
    assert all(v is not None for v in stamps) and stamps[1] < TIP
    run(c, http, at(30.0), kind="t30")
    assert http.n_odds == 1  # idempotent
    run(c, http, at(20.0), kind="t20")
    assert http.n_odds == 2


def test_run_schedule_tick_runs_only_due_kinds_and_flag_off_is_noop(cfg: lc.LiveConfig) -> None:
    c = replace(cfg, schedule=lc.load_live_config().schedule)
    http = FakeHttp()
    reps = lc.run_schedule_tick(
        SLATE, cfg=c, environ=ENV_ON, now_fn=at(45.0), resolver=RES, sleep=lambda _s: None,
        http_get=http,
    )  # fmt: skip
    assert http.n_odds == 1 and [r.kind for r in reps if r.inserted] == ["t45"]
    off = lc.run_schedule_tick(SLATE, cfg=c, environ={}, now_fn=at(45.0), http_get=http)
    assert all(r.disabled for r in off) and http.n_odds == 1


def test_preseason_and_allstar_games_are_not_captured(cfg: lc.LiveConfig) -> None:
    con = duckdb.connect(str(cfg.tips_db))
    for gid in ("0012600001", "0032600001", "0022600001"):
        con.execute("INSERT INTO game_tips VALUES (?, ?, ?, 'schedule', ?)", [gid, SLATE, TIP, TIP])
    con.close()
    ids = {g.game_id for g in lc.read_slate(cfg.tips_db, SLATE)}
    assert ids == {"G1", "0022600001"}  # 001 preseason and 003 all-star dropped


def test_credit_estimator_and_gating(cfg: lc.LiveConfig) -> None:
    c = lc.load_live_config()
    assert c.expected_cost() == 267  # 89 markets x 3 regions (us, eu, us_ex) x 1
    assert lc.estimate_credits(c, 1) == 12 * 267 == 3_204
    assert lc.estimate_credits(c, 215) == 688_860
    assert lc.estimate_credits(c, 215) <= 0.5 * c.plan.monthly_credits
    assert lc.estimate_credits(c, 1230) == 3_940_920
    assert lc.estimate_credits(c, 15) == 48_060 <= c.daily_max_credits  # a 15-game night fits
    assert lc.estimate_credits(c, 16) <= c.daily_max_credits  # and so does a 16th game
    assert 2 * lc.estimate_credits(c, 15) > c.daily_max_credits  # but a full retry of it does not
    # a plan too small for the alternates drops them FIRST and keeps every schedule point
    # (900,000 x 0.5 = 450,000: the full 89-market set costs 688,860, so alternates go first)
    tight = replace(c, plan=replace(c.plan, name="START 1M", monthly_credits=900_000))
    assert len(tight.markets) == 7 + 30 + 3 + 11 + 5 and len(tight.active_schedule()) == 12
    assert set(tight.dropped_groups()) == {
        "game_additional",
        "periods_alternate",
        "player_alternate",
    }
    assert not any("alternate" in m for m in tight.markets)
    # a plan too small even without the alternates drops the extended schedule points next
    small = replace(c, plan=replace(c.plan, name="START 20K", monthly_credits=20_000))
    assert sorted(e.kind for e in small.active_schedule()) == ["close", "t60"]
    assert not small.markets or not any("alternate" in m for m in small.markets)
    # ... and 2M at the 0.5 rule keeps everything
    mid = replace(c, plan=replace(c.plan, name="START 2M", monthly_credits=2_000_000))
    assert len(mid.active_schedule()) == 12 and mid.dropped_groups() == ()


def test_market_groups_switch_off_and_no_duplicates() -> None:
    c = lc.load_live_config()
    names = [g.name for g in c.market_groups]
    assert "core" in names and "player_q1" in names and len(set(names)) == len(names)
    assert len(c.enabled_markets) == len(set(c.enabled_markets)) == 89
    assert set(c.hist.game_markets + c.hist.prop_markets) <= set(c.markets)  # nothing lost
    off = replace(
        c,
        market_groups=tuple(
            replace(g, enabled=False) if g.name == "player_extra" else g for g in c.market_groups
        ),
    )
    assert len(off.markets) == 89 - 11 and "player_blocks" not in off.markets
    assert off.expected_cost() == 3 * (89 - 11)


def test_market_group_parsing(tmp_path: Path) -> None:
    p = tmp_path / "x.yaml"
    p.write_text(
        "market_groups:\n  a: {markets: [h2h, totals]}\n  b: {markets: [totals, spreads], "
        "enabled: false}\n  c: {markets: [player_blocks], alternate: true}\n"
    )
    c = lc.load_live_config(p)
    assert c.markets == ("h2h", "totals", "player_blocks")  # b is off; totals once
    assert [g.alternate for g in c.market_groups] == [False, False, True]


def test_fixture_dry_run_over_the_full_schedule_pulls_nothing(cfg: lc.LiveConfig) -> None:
    c = replace(cfg, schedule=lc.load_live_config().schedule)
    http = FakeHttp()
    reps = lc.run_schedule_tick(
        SLATE, dry_run=True, cfg=c, environ=ENV_ON, now_fn=at(90.0), resolver=RES,
        sleep=lambda _s: None, http_get=http,
    )  # fmt: skip
    assert http.n_odds == 0
    assert rows(c, "SELECT count(*) FROM live_book_odds")[0] == (0,)
    assert [r.kind for r in reps if r.statuses] == ["t90"]


def test_live_request_asks_for_every_enabled_market_and_never_after_tip(
    cfg: lc.LiveConfig,
) -> None:
    c = replace(cfg, market_groups=lc.load_live_config().market_groups)
    http = FakeHttp()
    rep = run(c, http, at(6))
    assert rep.pulled == 1 and not rep.degraded
    (params,) = [p for u, p in http.calls if u.endswith("/odds")]
    asked = params["markets"].split(",")
    assert asked == list(c.markets) and len(asked) == 89
    assert {"player_points_q1", "team_totals_h2", "alternate_spreads_q3"} <= set(asked)
    late = FakeHttp()  # the post-tip refusal is unchanged with the big market list
    rep2 = lc.run_live_capture(
        SLATE, "close", environ=ENV_ON, now_fn=lambda: TIP.replace(tzinfo=UTC), cfg=c,
        resolver=RES, sleep=lambda _s: None, http_get=late,
    )  # fmt: skip
    assert late.n_odds == 0 and rep2.pulled == 0


def test_locked_games_db_keeps_old_behaviour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tips, games = _prelineup(tmp_path)

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("could not set lock on file")  # not a duckdb.Error subclass

    real = duckdb.connect

    def connect(p: Any, **k: Any) -> Any:
        return boom() if str(p) == str(games) else real(p, **k)

    monkeypatch.setattr(duckdb, "connect", connect)
    out = lc.read_slate(tips, SLATE, games)
    assert [(g.home_team, g.away_team) for g in out] == [(0, 0), (0, 0)]


def test_swapped_home_away_from_schedule_gives_no_match(tmp_path: Path) -> None:
    tips, games = _prelineup(tmp_path)
    con = duckdb.connect(str(games))
    con.execute(
        "UPDATE games SET home_team = ?, away_team = ? WHERE game_id = '0022600001'", [NYK, PHI]
    )
    con.close()
    new = lc.read_slate(tips, SLATE, games)
    assert lc.match_live_event(_events_plus10(), new[0], 2, RES4, 30) is None
