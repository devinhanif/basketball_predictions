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


def test_repo_allowlist_is_empty_with_a_commented_example() -> None:
    allow = r.load_allowlist(ROOT_ALLOWLIST)
    assert allow.reporters == {}
    text = ROOT_ALLOWLIST.read_text()
    assert "#   - handle: ExampleBeatWriter" in text and "handles: []" in text


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
    assert st["rocketsbeat"] == {"user_id": "42", "last_created_at": "2026-10-21T19:40:00"}
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


def test_only_two_read_only_endpoints_exist() -> None:
    assert set(r.ENDPOINTS) == {"user_by_username", "user_tweets"}
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
