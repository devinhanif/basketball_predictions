"""ESPN freshness poller on recorded/crafted bodies: parse, conditional GET, backoff, isolation."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from nba.live.espn_fresh import (
    CORE_PLAYS,
    SUMMARY,
    FreshPoller,
    freshness_report,
    last_play_core,
    last_play_summary,
    live_event_ids,
)

FIX = Path(__file__).parent / "fixtures"
SUMMARY_BODY: dict[str, Any] = json.loads((FIX / "espn_summary_trimmed.json").read_text())
SCOREBOARD = {
    "events": [
        {"id": "1", "status": {"type": {"state": "in"}}},
        {"id": "2", "status": {"type": {"state": "pre"}}},
        {"id": "3", "status": {"type": {"state": "post"}}},
    ]
}
T0 = datetime(2026, 10, 23, 0, 20, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.t = 0.0
        self.wall = T0

    def mono(self) -> float:
        return self.t

    def now(self) -> datetime:
        return self.wall

    def advance(self, s: float) -> None:
        self.t += s
        self.wall += timedelta(seconds=s)


def summary_with(n_new: int) -> dict[str, Any]:
    b: dict[str, Any] = json.loads(json.dumps(SUMMARY_BODY))
    for i in range(n_new):
        p = dict(b["plays"][-1])
        p["id"] = f"{p['id']}{i}"
        p["wallclock"] = "2026-10-23T00:19:50Z"
        b["plays"].append(p)
    return b


def make(
    tmp_path: Path, handler: Any, clock: Clock, interval_s: float = 18.0
) -> tuple[FreshPoller, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        resp: httpx.Response = handler(req)
        return resp

    client = httpx.Client(transport=httpx.MockTransport(wrapped), timeout=5.0)
    p = FreshPoller(
        date(2026, 10, 22),
        tmp_path,
        client=client,
        interval_s=interval_s,
        now_fn=clock.now,
        clock=clock.mono,
        sleep=lambda s: None,
    )
    return p, seen


def test_parsers() -> None:
    lp, state = last_play_summary(SUMMARY_BODY)
    assert state == "in" and lp is not None
    assert lp.wallclock is not None and lp.wallclock.tzinfo is not None and lp.n_plays == 4
    core = last_play_core({"count": 9, "items": [{"id": "5", "wallclock": "2026-10-23T00:19:50Z"}]})
    assert core is not None and core.play_id == "5" and core.n_plays == 9
    assert last_play_core({"items": []}) is None
    assert live_event_ids(SCOREBOARD) == ["1"]


def test_only_live_games_polled_and_unchanged_not_stored(tmp_path: Path) -> None:
    clock = Clock()
    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/scoreboard"):
            return httpx.Response(200, json=SCOREBOARD)
        if path.endswith("/summary"):
            return httpx.Response(200, json=summary_with(min(state["n"], 1)))
        if path.endswith("/plays"):
            if req.url.params.get("page"):
                return httpx.Response(
                    200,
                    json={
                        "count": 7,
                        "items": [{"id": "7", "wallclock": "2026-10-23T00:19:50Z"}],
                    },
                )
            return httpx.Response(200, json={"count": 7, "items": []})
        return httpx.Response(404)

    p, seen = make(tmp_path, handler, clock)
    p.cycle()
    clock.advance(18)
    p.cycle()  # same body again: logged, not stored
    clock.advance(18)
    state["n"] = 1
    p.cycle()  # a new play: logged and stored
    summaries = [r for r in seen if r.url.path.endswith("/summary")]
    assert len(summaries) == 3 and all(r.url.params["event"] == "1" for r in summaries)
    rows = [json.loads(x) for x in (tmp_path / "fresh.jsonl").read_text().splitlines()]
    s = [r for r in rows if r["endpoint"] == SUMMARY]
    assert [r["changed"] for r in s] == [True, False, True] and s[0]["first_poll"]
    assert s[0]["lag_s"] == 150.0  # fetched 00:20:00, newest play 00:17:30
    assert p.st.saved == 3  # summary x2 (first + changed), core plays x1 (same body twice)
    manifest = [
        json.loads(x) for x in (tmp_path / "fresh" / "manifest.jsonl").read_text().split("\n") if x
    ]
    assert {m["source"] for m in manifest} == {SUMMARY, CORE_PLAYS}
    assert "fetched_at" in manifest[0]["note"] and manifest[0]["note"]["play_wallclock"]
    assert p.heartbeat_fields()["fresh_live_games"] == 1


def test_conditional_get_and_304(tmp_path: Path) -> None:
    clock = Clock()
    sent: list[dict[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/scoreboard"):
            return httpx.Response(200, json=SCOREBOARD)
        if req.url.path.endswith("/summary"):
            sent.append(dict(req.headers))
            if req.headers.get("if-none-match") == '"abc"':
                return httpx.Response(304)
            return httpx.Response(200, json=SUMMARY_BODY, headers={"ETag": '"abc"'})
        return httpx.Response(404)

    p, _ = make(tmp_path, handler, clock)
    p.cycle()
    p.poll_summary("1")
    assert "if-none-match" not in sent[0] and sent[1]["if-none-match"] == '"abc"'
    assert p.st.not_modified == 1
    assert set(sent[1]) <= {
        "host",
        "accept",
        "accept-encoding",
        "connection",
        "user-agent",
        "if-none-match",
    }


def test_backoff_on_429_and_isolated_failures(tmp_path: Path) -> None:
    clock = Clock()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/scoreboard"):
            return httpx.Response(200, json=SCOREBOARD)
        if req.url.path.endswith("/summary"):
            return httpx.Response(429)
        raise httpx.ConnectError("down")

    p, seen = make(tmp_path, handler, clock)
    p.cycle()
    n1 = len(seen)
    clock.advance(18)
    p.cycle()  # inside the backoff window: no new summary or core requests
    assert len(seen) == n1
    clock.advance(400)
    p.cycle()
    assert len(seen) > n1
    assert p.st.errors >= 2 and p.st.backoffs >= 2
    assert (tmp_path / "fresh_errors.log").exists()


def test_run_never_raises_and_stops(tmp_path: Path) -> None:
    clock = Clock()
    p, _ = make(tmp_path, lambda r: httpx.Response(200, content=b"not json"), clock)
    calls = {"n": 0}

    def boom() -> None:
        calls["n"] += 1
        p.stop.set()
        raise RuntimeError("x")

    p.cycle = boom  # type: ignore[method-assign]
    p.run()
    assert calls["n"] == 1 and p.st.errors == 1


def test_freshness_report_shape(tmp_path: Path) -> None:
    rows = []
    t = T0
    for i, (changed, lag) in enumerate([(True, 5.0), (False, 23.0), (True, 12.0), (True, 30.0)]):
        rows.append(
            {
                "endpoint": SUMMARY,
                "game": "1",
                "fetched_at": (t + timedelta(seconds=18 * i)).isoformat(),
                "status": 200,
                "state": "in",
                "changed": changed,
                "first_poll": i == 0,
                "lag_s": lag,
            }
        )
    path = tmp_path / "fresh.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    rep = freshness_report(path)["endpoints"][SUMMARY]
    assert rep["n_fetches"] == 4 and rep["n_games"] == 1
    assert rep["first_seen_lag_s"] == {"n": 2, "median": 21.0, "p90": 30.0}
    assert rep["unchanged_pairs"] == 3 and abs(rep["unchanged_rate"] - 1 / 3) < 1e-9
    assert rep["update_gap_s"]["n"] == 2
