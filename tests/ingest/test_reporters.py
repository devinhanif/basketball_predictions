"""The X client on a mock transport: allow-list, token, budget, rate limits, raw store."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import pytest

from nba.ingest import reporters as r

TOKEN = "SECRET-TOKEN-9f8e7d6c5b4a"
ENV = {r.KEY_ENV: TOKEN}
NOW = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
ROOT_ALLOWLIST = Path(__file__).resolve().parents[2] / "configs" / "reporters.yaml"


def allowlist_yaml(
    tmp_path: Path, handles: list[dict[str, Any]] | None = None, **extra: Any
) -> Path:
    p = tmp_path / "reporters.yaml"
    rows = handles if handles is not None else [{"handle": "RocketsBeat", "team": "HOU"}]
    body: dict[str, Any] = {
        "handles": rows,
        "budget": {"posts_per_run": 200, "posts_per_day": 1000},
    }
    body.update(extra)
    p.write_text(json.dumps(body))  # JSON is YAML
    return p


def tweet(
    pid: str, text: str, created: str = "2026-10-21T19:30:00.000Z", note: str | None = None
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": pid,
        "text": text,
        "created_at": created,
        "edit_history_tweet_ids": [pid],
    }
    if note:
        row["note_tweet"] = {"text": note}
    return row


class Server:
    """A fake X API v2: records requests, serves scripted responses."""

    def __init__(
        self, posts: list[dict[str, Any]] | Callable[[int], httpx.Response] | None = None
    ) -> None:
        self.requests: list[httpx.Request] = []
        self.posts: list[dict[str, Any]] = posts if isinstance(posts, list) else []
        self.script = posts if callable(posts) else None

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if self.script is not None:
            return self.script(len(self.requests))
        if req.url.path.startswith("/2/users/by/username/"):
            name = req.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"data": {"id": "42", "username": name, "name": "x"}})
        if req.url.path.startswith("/2/users/42/tweets"):
            n = int(req.url.params.get("max_results", "10"))
            return httpx.Response(
                200,
                json={"data": self.posts[:n], "meta": {"result_count": min(n, len(self.posts))}},
                headers={"x-rate-limit-remaining": "99"},
            )
        return httpx.Response(404, json={"title": "Not Found"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def client(tmp_path: Path, server: Server, *, allow: Path | None = None, **kw: Any) -> r.XClient:
    allow = allow or allowlist_yaml(tmp_path)
    sleeps: list[float] = []
    c = r.XClient(
        r.load_allowlist(allow),
        environ=ENV,
        data_dir=tmp_path / "data",
        clock=kw.pop("clock", lambda: NOW),
        sleep=sleeps.append,
        transport=server.transport,
        **kw,
    )
    c.sleeps = sleeps  # type: ignore[attr-defined]
    return c


# ---------------------------------------------------------------------------------- allow-list


def test_repo_allowlist_loads_and_is_devins_checked_list() -> None:
    # Filled by Devin on 2026-10-15 from the paid handle check;
    # load_allowlist itself rejects bad tricodes and duplicates.
    allow = r.load_allowlist(ROOT_ALLOWLIST)
    assert len(allow.reporters) >= 1
    assert "shamscharania" in allow.reporters
    assert "#   - handle: ExampleBeatWriter" in ROOT_ALLOWLIST.read_text()


def test_allowlist_validation(tmp_path: Path) -> None:
    allow = r.load_allowlist(allowlist_yaml(tmp_path, [{"handle": "@RocketsBeat", "team": "hou"}]))
    rep = allow.require("rocketsbeat")
    assert (rep.handle, rep.team, rep.team_id, rep.source) == (
        "RocketsBeat",
        "HOU",
        1610612745,
        "x:RocketsBeat",
    )
    with pytest.raises(ValueError, match="unknown team"):
        r.load_allowlist(allowlist_yaml(tmp_path, [{"handle": "a", "team": "XXX"}]))
    with pytest.raises(ValueError, match="duplicate"):
        r.load_allowlist(allowlist_yaml(tmp_path, [{"handle": "a"}, {"handle": "A"}]))
    with pytest.raises(ValueError, match="not a valid"):
        r.load_allowlist(allowlist_yaml(tmp_path, [{"handle": "has space"}]))


def test_handle_not_on_list_is_refused_before_any_request(tmp_path: Path) -> None:
    srv = Server([tweet("1", "Alperen Sengun is OUT tonight")])
    c = client(tmp_path, srv)
    with pytest.raises(r.HandleNotAllowed):
        c.poll(["SomeOtherGuy"])
    with pytest.raises(r.HandleNotAllowed):
        c.fetch_posts("SomeOtherGuy", NOW - timedelta(hours=1), 10)
    assert srv.requests == [] and c.requests_made == 0
    assert "someotherguy" not in c.allowlist and "RocketsBeat" in c.allowlist


# -------------------------------------------------------------------------------------- token


def test_missing_token_refuses_to_construct(tmp_path: Path) -> None:
    srv = Server()
    with pytest.raises(r.KeyMissingError, match=r.KEY_ENV):
        r.XClient(r.load_allowlist(allowlist_yaml(tmp_path)), environ={}, transport=srv.transport)
    with pytest.raises(r.KeyMissingError):
        r.XClient(
            r.load_allowlist(allowlist_yaml(tmp_path)),
            environ={r.KEY_ENV: "   "},
            transport=srv.transport,
        )
    assert srv.requests == []


def test_token_never_appears_in_logs_files_or_errors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def script(n: int) -> httpx.Response:
        if n == 1:
            return httpx.Response(200, json={"data": {"id": "42"}})
        if n == 2:
            return httpx.Response(200, json={"data": [tweet("1", "Alperen Sengun is OUT tonight")]})
        return httpx.Response(401, text=f"unauthorized: bearer {TOKEN} rejected")

    srv = Server(script)
    c = client(tmp_path, srv)
    with caplog.at_level(logging.DEBUG):
        res = c.poll()
        with pytest.raises(r.ReporterHTTPError) as ei:
            c.fetch_posts("RocketsBeat", NOW, 10)
    assert res.per_handle == {"RocketsBeat": 1}
    assert TOKEN not in str(ei.value) and "<secret>" in str(ei.value)
    assert TOKEN not in caplog.text
    assert TOKEN not in repr(c) and "<secret>" in repr(c)
    assert TOKEN not in str(c._token) and TOKEN not in repr(c._token)
    files = [p for p in (tmp_path / "data").rglob("*") if p.is_file()]
    assert files, "raw parquet, budget.json and state.json should exist"
    for f in files:
        assert TOKEN.encode() not in f.read_bytes(), f
    # the token went out only as the Authorization header
    for req in srv.requests:
        assert req.headers["authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in str(req.url)
    with pytest.raises(TypeError):
        import pickle

        pickle.dumps(c._token)


# ---------------------------------------------------------------------------------- the poll


def test_poll_writes_raw_write_once_and_updates_state(tmp_path: Path) -> None:
    posts = [
        tweet("1001", "Udoka says Alperen Sengun is on a 24-minute restriction tonight."),
        tweet(
            "1002", "short", "2026-10-21T19:40:00.000Z", note="long post: Fred VanVleet will play."
        ),
    ]
    srv = Server(posts)
    clock = [NOW]
    c = client(tmp_path, srv, clock=lambda: clock[0])
    res = c.poll()
    assert res.stopped is None and res.per_handle == {"RocketsBeat": 2}
    assert len(res.raw_paths) == 1
    path = res.raw_paths[0]
    assert path == tmp_path / "data" / "raw" / "2026-10-21" / "RocketsBeat_200000Z.parquet"
    df = pl.read_parquet(path)
    assert dict(df.schema) == r.RAW_SCHEMA
    assert df["post_id"].to_list() == ["1001", "1002"]
    assert df["created_at"].to_list() == [
        datetime(2026, 10, 21, 19, 30),
        datetime(2026, 10, 21, 19, 40),
    ]
    assert df["text"].to_list()[1] == "long post: Fred VanVleet will play."  # note_tweet wins
    assert df["fetched_at"].to_list() == [datetime(2026, 10, 21, 20, 0)] * 2
    assert df["url"].to_list()[0] == "https://x.com/RocketsBeat/status/1001"
    assert [p.post_id for p in r.read_raw(path)] == ["1001", "1002"]
    # the request: one page, our fields, no retweets/replies on the wire, start_time = lookback
    req = [q for q in srv.requests if "tweets" in q.url.path][0]
    assert req.url.params["start_time"] == "2026-10-21T14:00:00Z"  # 6 h default lookback
    assert req.url.params["exclude"] == "retweets,replies"
    assert req.url.params["tweet.fields"] == r.TWEET_FIELDS
    assert req.url.params["max_results"] == "100"
    assert len(srv.requests) == 2  # users/by/username then users/:id/tweets
    # state: user id cached, newest post remembered; a second poll starts there and skips lookup
    st = json.loads((tmp_path / "data" / "state.json").read_text())
    assert st["rocketsbeat"] == {"last_created_at": "2026-10-21T19:40:00"}
    ids = json.loads((tmp_path / "data" / "user_ids.json").read_text())
    assert ids == {"rocketsbeat": "42"}
    clock[0] = NOW + timedelta(minutes=30)
    res2 = c.poll()
    assert len(srv.requests) == 3
    assert srv.requests[-1].url.params["start_time"] == "2026-10-21T19:40:00Z"
    assert res2.raw_paths == [path.with_name("RocketsBeat_203000Z.parquet")]
    # write-once: the same handle at the same second is refused, never overwritten
    with pytest.raises(FileExistsError):
        r.write_raw(tmp_path / "data" / "raw", "RocketsBeat", r.read_raw(path), NOW)
    assert pl.read_parquet(path).height == 2


def test_budget_cap_refuses_before_the_request(tmp_path: Path) -> None:
    posts = [tweet(str(i), f"Player{i} will play.") for i in range(10)]
    srv = Server(posts)
    allow = allowlist_yaml(
        tmp_path, [{"handle": "RocketsBeat", "team": "HOU"}, {"handle": "MavsBeat", "team": "DAL"}]
    )
    c = client(tmp_path, srv, allow=allow, budget_posts=7)
    res = c.poll()
    # first handle: max_results asks for the remaining 7, the server returns 7 and they are billed
    tweets_req = [q for q in srv.requests if "tweets" in q.url.path]
    assert len(tweets_req) == 1 and tweets_req[0].url.params["max_results"] == "7"
    assert res.per_handle == {"RocketsBeat": 7}
    assert res.stopped is not None and "budget exhausted" in res.stopped
    ledger = json.loads((tmp_path / "data" / "budget.json").read_text())
    assert ledger["days"]["2026-10-21"]["posts"] == 7 and ledger["total_posts"] == 7
    assert ledger["days"]["2026-10-21"]["requests"] == 2
    # fewer than a page (5) left -> refused, no request sent
    with pytest.raises(r.BudgetExceeded):
        c.budget.check(NOW)
    before = len(srv.requests)
    res2 = c.poll(["MavsBeat"])
    assert res2.stopped and res2.per_handle == {} and len(srv.requests) == before


def test_daily_ceiling_spans_runs(tmp_path: Path) -> None:
    allow = allowlist_yaml(tmp_path, budget={"posts_per_run": 100, "posts_per_day": 8})
    srv = Server([tweet(str(i), "x") for i in range(6)])
    c1 = client(tmp_path, srv, allow=allow)
    assert c1.poll().per_handle == {"RocketsBeat": 6}
    c2 = client(tmp_path, srv, allow=allow)  # a new process, same day: 2 left < a page
    res = c2.poll()
    assert res.per_handle == {} and res.stopped and "day 6/8" in res.stopped
    c3 = client(tmp_path, srv, allow=allow, clock=lambda: NOW + timedelta(days=1))
    assert c3.poll().per_handle == {"RocketsBeat": 6}


# ---------------------------------------------------------------------------------- rate limit


def test_429_honours_reset_header_then_succeeds(tmp_path: Path) -> None:
    reset = str(int(NOW.timestamp()) + 7)

    def script(n: int) -> httpx.Response:
        if n == 1:
            return httpx.Response(200, json={"data": {"id": "42"}})
        if n == 2:
            return httpx.Response(429, headers={"x-rate-limit-reset": reset})
        return httpx.Response(200, json={"data": [tweet("1", "Fred VanVleet will play.")]})

    srv = Server(script)
    c = client(tmp_path, srv)
    res = c.poll()
    assert res.per_handle == {"RocketsBeat": 1}
    assert c.sleeps == [7.0]  # type: ignore[attr-defined]
    assert len(srv.requests) == 3 and c.requests_made == 3


def test_429_twice_is_rate_limited_after_exactly_two_attempts(tmp_path: Path) -> None:
    srv = Server(
        lambda n: httpx.Response(429, headers={"x-rate-limit-reset": str(int(NOW.timestamp()) + 2)})
    )
    c = client(tmp_path, srv)
    with pytest.raises(r.RateLimited):
        c.user_id("RocketsBeat")
    assert len(srv.requests) == r.MAX_ATTEMPTS == 2
    assert c.sleeps == [2.0]  # type: ignore[attr-defined]


def test_reset_far_away_refuses_without_sleeping(tmp_path: Path) -> None:
    far = str(int(NOW.timestamp()) + 3600)
    srv = Server(lambda n: httpx.Response(429, headers={"x-rate-limit-reset": far}))
    c = client(tmp_path, srv)
    with pytest.raises(r.RateLimited, match="cap"):
        c.user_id("RocketsBeat")
    assert c.sleeps == [] and len(srv.requests) == 1  # type: ignore[attr-defined]


def test_5xx_backs_off_exponentially_then_gives_up(tmp_path: Path) -> None:
    srv = Server(lambda n: httpx.Response(503))
    c = client(tmp_path, srv)
    with pytest.raises(r.Unreachable):
        c.user_id("RocketsBeat")
    assert len(srv.requests) == 2 and c.sleeps == [1.0]  # type: ignore[attr-defined]
    ledger = json.loads((tmp_path / "data" / "budget.json").read_text())
    assert ledger["days"]["2026-10-21"] == {"posts": 0, "requests": 2}  # every attempt counted


def test_only_get_read_only_endpoints_exist() -> None:
    assert set(r.ENDPOINTS) == {"user_by_username", "users_by", "user_tweets"}
    src = Path(r.__file__).read_text()
    assert "http.get(" in src and ".post(" not in src and ".delete(" not in src


def test_parse_payload_tolerates_junk() -> None:
    assert r.parse_tweets_payload(None, "h", NOW) == []
    assert r.parse_tweets_payload({"data": "nope"}, "h", NOW) == []
    assert (
        r.parse_tweets_payload(
            {"data": [{"id": "1"}, 5, {"id": "2", "created_at": "bad"}]}, "h", NOW
        )
        == []
    )


def test_cli_poll_with_empty_list_is_a_refusal_without_network(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    allow = allowlist_yaml(tmp_path, [])
    rc = r.main(["poll", "--allowlist", str(allow), "--data-dir", str(tmp_path / "d")])
    assert rc == 2 and "no handles" in capsys.readouterr().err


def test_cli_poll_without_token_is_a_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(r.KEY_ENV, raising=False)
    rc = r.main(["poll", "--allowlist", str(allowlist_yaml(tmp_path)), "--data-dir", str(tmp_path)])
    assert rc == 2 and r.KEY_ENV in capsys.readouterr().err


# ------------------------------------------------------------ cost guards (slate, window, caps)

from nba.daily.schedule import ScheduledGame  # noqa: E402
from nba.ingest.teams import TEAM_ABBREV_TO_ID as TID  # noqa: E402

# NOW is 2026-10-21 20:00Z = 16:00 ET. HOU-DAL tips 23:30Z; BOS-NYK tips 01:00Z (Oct 21 ET 21:00).
HOU_DAL = ScheduledGame("g1", datetime(2026, 10, 21, 23, 30), TID["HOU"], TID["DAL"])
BOS_NYK = ScheduledGame("g2", datetime(2026, 10, 22, 1, 0), TID["BOS"], TID["NYK"])
GAMES = [HOU_DAL, BOS_NYK]
FOUR: list[dict[str, Any]] = [
    {"handle": "RocketsBeat", "team": "HOU"},
    {"handle": "LakersBeat", "team": "LAL"},
    {"handle": "BostonBeat", "team": "BOS"},
    {"handle": "LeagueWide", "team": None},
]


def test_slate_only_skips_teams_without_a_game(tmp_path: Path) -> None:
    allow = r.load_allowlist(allowlist_yaml(tmp_path, FOUR))
    now = datetime(2026, 10, 21, 23, 15, tzinfo=UTC)  # inside every window
    ok, skipped = r.plan_poll(allow, GAMES, now)
    assert ok == ["RocketsBeat", "BostonBeat", "LeagueWide"]
    assert "LAL does not play" in skipped["LakersBeat"]
    ok_all, _ = r.plan_poll(allow, GAMES, now, all_teams=True)
    assert "LakersBeat" in ok_all


def test_slate_only_poll_sends_no_request_for_idle_team(tmp_path: Path) -> None:
    allow = r.load_allowlist(allowlist_yaml(tmp_path, FOUR))
    srv = Server([tweet("1", "x")])
    c = client(tmp_path, srv, allow=allowlist_yaml(tmp_path, FOUR))
    ok, _ = r.plan_poll(allow, GAMES, NOW + timedelta(hours=2))
    c.poll(ok)
    paths = " ".join(q.url.path for q in srv.requests)
    assert "/users/by/username/LakersBeat" not in paths
    assert "/users/by/username/RocketsBeat" in paths


def test_no_slate_is_refused_before_any_request(tmp_path: Path) -> None:
    allow = r.load_allowlist(allowlist_yaml(tmp_path, FOUR))
    srv = Server([tweet("1", "x")])
    with pytest.raises(r.PollRefused, match="no games"):
        r.plan_poll(allow, [], NOW)
    with pytest.raises(r.PollRefused):
        r.plan_poll(allow, GAMES, NOW + timedelta(days=3))
    assert srv.requests == []


def test_window_refuses_outside_two_hours_before_tip(tmp_path: Path) -> None:
    allow = r.load_allowlist(allowlist_yaml(tmp_path, FOUR[:1]))
    early = HOU_DAL.tipoff.replace(tzinfo=UTC) - timedelta(minutes=121)
    late = HOU_DAL.tipoff.replace(tzinfo=UTC) + timedelta(minutes=1)
    for t in (early, late):
        with pytest.raises(r.PollRefused, match="pre-game window"):
            r.plan_poll(allow, GAMES, t)
    tip = HOU_DAL.tipoff.replace(tzinfo=UTC)
    for t in (
        tip - timedelta(minutes=120),
        tip - timedelta(minutes=90),
        tip - timedelta(minutes=40),
    ):
        assert r.plan_poll(allow, GAMES, t)[0] == ["RocketsBeat"]


def test_window_is_per_team_tip(tmp_path: Path) -> None:
    allow = r.load_allowlist(allowlist_yaml(tmp_path, FOUR))
    t = datetime(2026, 10, 21, 21, 45, tzinfo=UTC)  # HOU (tip 23:30Z) open; BOS opens 23:00Z
    ok, skipped = r.plan_poll(allow, GAMES, t)
    assert "RocketsBeat" in ok and "BostonBeat" not in ok and "BostonBeat" in skipped


def test_cli_poll_outside_window_exits_2_without_network(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from nba.ingest import reporters as mod

    monkeypatch.setenv(r.KEY_ENV, TOKEN)
    monkeypatch.setattr("nba.daily.schedule.load_schedule_cache", lambda *a, **k: GAMES)
    real = datetime

    class Frozen(real):  # type: ignore[misc, valid-type]
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return NOW - timedelta(hours=10)

    monkeypatch.setattr("nba.ingest.reporters.datetime", Frozen)
    calls: list[int] = []
    monkeypatch.setattr(mod.XClient, "_get", lambda *a, **k: calls.append(1))
    rc = r.main(["poll", "--allowlist", str(allowlist_yaml(tmp_path)), "--data-dir", str(tmp_path)])
    assert rc == 2 and calls == [] and "REFUSED" in capsys.readouterr().err


def monthly_ledger(tmp_path: Path, days: dict[str, dict[str, int]]) -> None:
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    (d / "budget.json").write_text(json.dumps({"days": days, "total_posts": 0}))


def test_month_post_cap_refuses_before_request(tmp_path: Path) -> None:
    allow = allowlist_yaml(
        tmp_path, budget={"posts_per_run": 200, "posts_per_day": 400, "posts_per_month": 8000}
    )
    # 7996 posts already this UTC month on earlier days; today none -> 4 left < one page (5)
    monthly_ledger(tmp_path, {"2026-10-01": {"posts": 7996, "requests": 90}})
    srv = Server([tweet("1", "x")])
    c = client(tmp_path, srv, allow=allow)
    res = c.poll()
    assert srv.requests == [] and res.stopped and "month 7996/8000" in res.stopped


def test_month_usd_cap_refuses_before_request(tmp_path: Path) -> None:
    allow = allowlist_yaml(
        tmp_path,
        budget={"posts_per_month": 99999, "usd_per_month": 1.0, "posts_per_day": 400},
    )
    monthly_ledger(tmp_path, {"2026-10-02": {"posts": 198, "requests": 5}})  # $0.99 of $1
    srv = Server([tweet("1", "x")])
    c = client(tmp_path, srv, allow=allow)
    res = c.poll()
    assert srv.requests == [] and res.stopped and "$0.99/$1.00" in res.stopped


def test_month_resets_on_the_next_utc_month(tmp_path: Path) -> None:
    monthly_ledger(tmp_path, {"2026-09-30": {"posts": 8000, "requests": 100}})
    srv = Server([tweet("1", "x")])
    c = client(tmp_path, srv)
    assert c.poll().per_handle == {"RocketsBeat": 1}


def test_cli_month_cap_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from nba.ingest import reporters as mod

    monkeypatch.setenv(r.KEY_ENV, TOKEN)
    now = datetime.now(UTC)
    tip = now.replace(tzinfo=None) + timedelta(minutes=60)
    game = ScheduledGame("g", tip, TID["HOU"], TID["DAL"])
    monkeypatch.setattr("nba.daily.schedule.load_schedule_cache", lambda *a, **k: [game])
    calls: list[int] = []
    monkeypatch.setattr(mod.XClient, "_get", lambda *a, **k: calls.append(1))
    ledger = {"days": {now.strftime("%Y-%m-01"): {"posts": 8000, "requests": 1}}}
    (tmp_path / "budget.json").write_text(json.dumps(ledger))
    rc = r.main(["poll", "--allowlist", str(allowlist_yaml(tmp_path)), "--data-dir", str(tmp_path)])
    out = capsys.readouterr()
    assert calls == [] and "month 8000/8000" in out.err
    # a day-1 UTC edge cannot make "today" differ from the month key here
    assert rc == 2


def test_repo_config_caps() -> None:
    a = r.load_allowlist(ROOT_ALLOWLIST)
    assert (a.posts_per_day, a.posts_per_month, a.usd_per_month) == (400, 8000, 40.0)
    assert a.window_minutes == 120


def test_budget_subcommand_prints_day_and_month(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    allow = allowlist_yaml(tmp_path)
    now = datetime.now(UTC)
    today, first = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-01")
    days = {first: {"posts": 100, "requests": 4}}
    days[today] = {"posts": days.get(today, {}).get("posts", 0) + 20, "requests": 3}
    (tmp_path / "budget.json").write_text(json.dumps({"days": days}))
    rc = r.main(["budget", "--allowlist", str(allow), "--data-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0 and "$" in out and "attempts" in out
    if today == first:
        assert "20/1000 posts" in out
    else:
        assert "20/1000 posts, 3 attempts, 0 user reads, est $0.10" in out
        assert "120/8000 posts, 7 attempts, 0 user reads, est $0.60/$40.00" in out


def test_cached_ids_make_zero_user_lookups_and_timeline_asks_for_no_user_objects(
    tmp_path: Path,
) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "user_ids.json").write_text(json.dumps({"rocketsbeat": "42"}))
    srv = Server([tweet("1", "x")])
    ticks = iter(NOW + timedelta(minutes=i) for i in range(1000))
    c = client(tmp_path, srv, clock=lambda: next(ticks))
    c.poll()
    c.poll()
    assert [q for q in srv.requests if "/users/by/" in q.url.path] == []
    assert len(srv.requests) == 2
    for q in srv.requests:
        assert "expansions" not in q.url.params and "user.fields" not in q.url.params
    ledger = json.loads((tmp_path / "data" / "budget.json").read_text())
    assert ledger["days"]["2026-10-21"].get("users", 0) == 0


def test_first_lookup_is_billed_once_then_cached(tmp_path: Path) -> None:
    srv = Server([tweet("1", "x")])
    c = client(tmp_path, srv)
    c.poll()
    later = NOW + timedelta(hours=1)
    c2 = client(tmp_path, srv, clock=lambda: later)  # a new process reads the cache file
    c2.poll()
    lookups = [q for q in srv.requests if "/users/by/" in q.url.path]
    assert len(lookups) == 1
    ledger = json.loads((tmp_path / "data" / "budget.json").read_text())
    assert ledger["days"]["2026-10-21"]["users"] == 1


def test_user_reads_count_toward_usd_month_and_lookup_is_refused_at_the_cap(
    tmp_path: Path,
) -> None:
    allow = allowlist_yaml(tmp_path, budget={"usd_per_month": 1.0, "posts_per_day": 400})
    # $0.995 spent (199 posts); one more user read ($0.01) would pass $1.00
    monthly_ledger(tmp_path, {"2026-10-02": {"posts": 199, "requests": 5, "users": 0}})
    srv = Server([tweet("1", "x")])
    c = client(tmp_path, srv, allow=allow)
    with pytest.raises(r.BudgetExceeded, match="user lookup refused"):
        c.user_id("RocketsBeat")
    assert srv.requests == []
    # user reads already billed reduce the posts that remain under the USD cap
    monthly_ledger(tmp_path, {"2026-10-02": {"posts": 0, "requests": 5, "users": 98}})  # $0.98
    c2 = client(tmp_path, srv, allow=allow)
    assert c2.budget.remaining(NOW) == 4  # $0.02 / $0.005


def test_budget_subcommand_shows_user_reads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    now = datetime.now(UTC)
    day = now.strftime("%Y-%m-%d")
    (tmp_path / "budget.json").write_text(
        json.dumps({"days": {day: {"posts": 10, "requests": 4, "users": 3}}})
    )
    rc = r.main(
        ["budget", "--allowlist", str(allowlist_yaml(tmp_path)), "--data-dir", str(tmp_path)]
    )
    out = capsys.readouterr().out
    assert rc == 0 and "3 user reads" in out and "est $0.08" in out and "users $0.03" in out


# ---- check-handles (one-off batch lookup) ------------------------------------------------------


def _users_by_server(found: dict[str, int]) -> Server:
    def script(_n: int) -> httpx.Response:
        users = [
            {
                "id": str(i),
                "username": u,
                "name": f"Name {u}",
                "description": "Covers the team. " * 20,
                "verified": False,
                "public_metrics": {"followers_count": f},
            }
            for i, (u, f) in enumerate(found.items())
        ]
        return httpx.Response(200, json={"data": users})

    return Server(script)


def _check(tmp_path: Path, handles: list[str], srv: Server, **kw: Any) -> int:
    cand = tmp_path / "cand.txt"
    cand.write_text("# c\n" + "\n".join(handles) + "\n")
    return r.check_handles(
        cand,
        allowlist_path=allowlist_yaml(tmp_path),
        data_dir=tmp_path / "rep",
        environ=ENV,
        transport=srv.transport,
        clock=lambda: NOW,
        **kw,
    )


def test_check_batch_shape_ledger_and_no_allowlist_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    al = allowlist_yaml(tmp_path)
    before = al.read_text()
    srv = _users_by_server({"KCJHoop": 1234})
    assert _check(tmp_path, ["KCJHoop", "kcjhoop", "ghost_handle"], srv) == 0
    assert len(srv.requests) == 1
    req = srv.requests[0]
    assert req.method == "GET" and req.url.path == "/2/users/by"
    assert req.url.params["usernames"] == "KCJHoop,ghost_handle"
    assert req.url.params["user.fields"] == r.USER_FIELDS
    out = capsys.readouterr().out
    assert "ghost_handle | NO" in out and "1234" in out
    assert TOKEN not in out
    rep = tmp_path / "rep"
    saved = json.loads((rep / "handle_check_2026-10-21.json").read_text())
    assert [u["exists"] for u in saved["users"]] == [True, False]
    ledger = json.loads((rep / "budget.json").read_text())
    assert ledger["days"]["2026-10-21"]["users"] == 1  # only the returned user is billed
    assert not (rep / "user_ids.json").exists()
    assert al.read_text() == before


def test_check_refuses_over_cap_and_existing_result(tmp_path: Path) -> None:
    srv = _users_by_server({})
    with pytest.raises(r.PollRefused):
        _check(tmp_path, [f"h{i}" for i in range(61)], srv)
    assert srv.requests == []
    assert _check(tmp_path, ["a1"], srv) == 0
    with pytest.raises(r.PollRefused):  # write-once
        _check(tmp_path, ["a1"], srv)
    assert len(srv.requests) == 1


def test_check_month_cap_refuses_before_request(tmp_path: Path) -> None:
    rep = tmp_path / "rep"
    rep.mkdir()
    (rep / "budget.json").write_text(
        json.dumps({"days": {"2026-10-02": {"posts": 0, "requests": 0, "users": 3999}}})
    )
    cand = tmp_path / "c.txt"
    cand.write_text("a1\nb2\n")
    srv = _users_by_server({})
    with pytest.raises(r.BudgetExceeded):
        r.check_handles(
            cand,
            allowlist_path=allowlist_yaml(tmp_path),
            data_dir=rep,
            environ=ENV,
            transport=srv.transport,
            clock=lambda: NOW,
        )
    assert srv.requests == []


def test_check_dry_run_sends_nothing_needs_no_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    srv = _users_by_server({})
    cand = tmp_path / "c.txt"
    cand.write_text("a1\n")
    rc = r.check_handles(
        cand,
        allowlist_path=allowlist_yaml(tmp_path),
        data_dir=tmp_path / "rep",
        dry_run=True,
        environ={},
        transport=srv.transport,
        clock=lambda: NOW,
    )
    assert rc == 0 and srv.requests == []
    assert "worst-case $0.010" in capsys.readouterr().out
    assert not (tmp_path / "rep").exists()


def test_check_missing_token_refuses(tmp_path: Path) -> None:
    cand = tmp_path / "c.txt"
    cand.write_text("a1\n")
    with pytest.raises(r.KeyMissingError):
        r.check_handles(
            cand, allowlist_path=allowlist_yaml(tmp_path), data_dir=tmp_path / "rep", environ={}
        )


def test_shipped_candidates_file_is_valid() -> None:
    hs = r.read_candidates(r.CANDIDATES_PATH)
    assert 30 <= len(hs) <= r.CHECK_MAX_HANDLES
    assert all(r._HANDLE_RE.match(h) for h in hs)
