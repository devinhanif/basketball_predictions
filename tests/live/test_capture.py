"""Capture on recorded fixtures: write-once store, manifest, request budget, skips, heartbeat."""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from nba.live import kalshi_pull as kp
from nba.live.capture import Budget, Capture, orderbook_targets, parse_scoreboard, window
from nba.live.http import Fetcher
from nba.live.store import RawStore

FIX = Path(__file__).parent / "fixtures"
SCOREBOARD = (FIX / "espn_scoreboard.json").read_bytes()
MARKETS = json.loads((FIX / "kalshi_markets_pages.json").read_text())["markets"]
DAY = date(2026, 10, 21)


def make_fetcher(handler: Any) -> Fetcher:
    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)
    return Fetcher(client=client, rate_per_s=1e6, sleep=lambda s: None)


def routes(scoreboard: bytes = SCOREBOARD, ob_status: int = 200) -> Any:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        seen.append(url)
        path = req.url.path
        if path.endswith("/scoreboard"):
            return httpx.Response(200, content=scoreboard)
        if path.endswith("/summary"):
            return httpx.Response(
                200, json={"boxscore": {"teams": []}, "plays": [], "winprobability": []}
            )
        if path.endswith("/series"):
            return httpx.Response(
                200, json={"series": [{"ticker": "KXNBAGAME"}, {"ticker": "KXNBAMVP"}]}
            )
        if path.endswith("/orderbook"):
            if ob_status != 200:
                return httpx.Response(ob_status, json={"error": "auth"})
            return httpx.Response(
                200, json={"orderbook_fp": {"yes_dollars": [["0.5000", "10.00"]], "no_dollars": []}}
            )
        if path.endswith("/markets"):
            series = req.url.params.get("series_ticker", "")
            ms = [m for m in MARKETS if m["ticker"].startswith(series)]
            other = dict(ms[0]) if ms else None
            if other:
                other["ticker"] += "X"
                other["event_ticker"] = other["event_ticker"].replace("26OCT21", "26OCT25")
                ms = [*ms, other]
            if series == "KXNBAMVP":  # a futures series: undated events
                ms = [{"ticker": "KXNBAMVP-27-X", "event_ticker": "KXNBAMVP-27", "title": "x"}]
            return httpx.Response(200, json={"markets": ms, "cursor": ""})
        return httpx.Response(404)

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


def read_gz(path: Path) -> Any:
    return json.loads(gzip.decompress(path.read_bytes()))


def test_store_is_write_once_and_manifest_hashes(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    t = datetime(2026, 10, 21, 20, 0, 0, tzinfo=UTC)
    p1 = store.save("src", "k", b'{"a":1}', http_status=200, received_at=t)
    p2 = store.save("src", "k", b'{"a":2}', http_status=200, received_at=t)
    assert p1 != p2 and p1.exists() and p2.exists()
    assert read_gz(p1) == {"a": 1}
    lines = [json.loads(x) for x in store.manifest_path.read_text().splitlines()]
    assert len(lines) == 2
    assert lines[0]["sha256"] == hashlib.sha256(b'{"a":1}').hexdigest()
    assert lines[0]["bytes"] == 7 and lines[0]["http_status"] == 200
    assert lines[0]["received_at"].startswith("2026-10-21T20:00:00")


def test_fetcher_timeout_budget_and_bounded_retries() -> None:
    calls = []

    def boom(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500)

    f = make_fetcher(boom)
    assert f.client.timeout.read == 10.0
    r = f.get("https://x.test/a")
    assert r is not None and r.status == 500 and len(calls) == 3  # bounded retries, then returned
    f.begin_tick(budget=2, deadline_s=100)
    assert f.get("https://x.test/b") is None  # budget spent mid-retries
    assert f.tick_requests == 2


def test_parse_scoreboard_and_window() -> None:
    games = parse_scoreboard(SCOREBOARD)
    assert {g.key for g in games} == {("CHA", "BKN"), ("GSW", "LAL")}  # ESPN GS -> Kalshi GSW
    start, end = window(games, Budget())  # type: ignore[misc]
    assert start == min(g.tip for g in games) - timedelta(hours=2)
    assert end == max(g.tip for g in games) + timedelta(hours=4)
    assert window([], Budget()) is None


def test_orderbook_priority_and_skip_list() -> None:
    games = [g for g in parse_scoreboard(SCOREBOARD) if g.key == ("GSW", "LAL")]
    legs = {"KXNBAPTS-26OCT21GSWLAL-GSWSCURRY30-25"}
    order, skipped = orderbook_targets(MARKETS, games, legs, limit=2)
    assert order[0] in legs  # a paper-position leg comes first
    assert order[1].startswith("KXNBAGAME-26OCT21GSWLAL")  # then game winners of live games
    assert len(skipped) >= 1  # the rest is listed, not silently dropped
    assert not any("INDNOP" in t for t in order + skipped)  # only in-progress games


def test_tick_writes_filtered_raw_heartbeat_and_orderbooks(tmp_path: Path) -> None:
    handler = routes()
    live = SCOREBOARD.replace(b'"state": "pre"', b'"state": "in"')
    handler = routes(live)
    legs_file = tmp_path / "legs.json"
    legs_file.write_text(json.dumps({"tickers": ["KXNBAPTS-26OCT21GSWLAL-GSWSCURRY30-25"]}))
    hb = tmp_path / "hb" / "live_capture.json"
    cap = Capture(DAY, tmp_path / "raw", hb, fetcher=make_fetcher(handler), legs_file=legs_file)
    info = cap.tick()
    assert info["requests"] <= cap.budget.max_requests_per_tick
    files = {p.parent.parent.name for p in (tmp_path / "raw").rglob("*.json.gz")}
    assert {"espn_scoreboard", "espn_summary", "kalshi", "kalshi_rules", "kalshi_series"} <= files
    page = next((tmp_path / "raw" / "kalshi" / "KXNBAGAME_p0").glob("*.json.gz"))
    body = read_gz(page)
    assert all("26OCT21" in m["event_ticker"] for m in body["markets"])  # other dates dropped
    assert all("rules_primary" not in m for m in body["markets"])  # saved once in kalshi_rules
    manifest = [
        json.loads(x) for x in (tmp_path / "raw" / "manifest.jsonl").read_text().splitlines()
    ]
    assert any(m.get("note", {}).get("dropped_other_dates") for m in manifest)
    assert "KXNBAMVP" in cap.st.undated  # futures-style series skipped from then on
    books = next((tmp_path / "raw" / "kalshi_orderbook").rglob("*.json.gz"))
    assert "KXNBAPTS-26OCT21GSWLAL-GSWSCURRY30-25" in read_gz(books)
    cap.beat("running")
    beat = json.loads(hb.read_text())
    assert beat["job"] == "live_capture" and beat["status"] == "running" and beat["ticks"] == 0


def test_request_budget_logs_what_was_skipped(tmp_path: Path) -> None:
    live = SCOREBOARD.replace(b'"state": "pre"', b'"state": "in"')
    cap = Capture(
        DAY, tmp_path / "raw", tmp_path / "hb.json", fetcher=make_fetcher(routes(live)),
        budget=Budget(max_requests_per_tick=6),
    )  # fmt: skip
    info = cap.tick()
    assert info["requests"] <= 6
    assert info["skipped"]  # something was cut and it is named
    assert (tmp_path / "raw" / "manifest.jsonl").exists()


def test_orderbook_401_disables_without_credentials(tmp_path: Path) -> None:
    live = SCOREBOARD.replace(b'"state": "pre"', b'"state": "in"')
    handler = routes(live, ob_status=401)
    cap = Capture(DAY, tmp_path / "raw", tmp_path / "hb.json", fetcher=make_fetcher(handler))
    cap.tick()
    assert cap.st.orderbook_blocked
    assert "disabled" in (tmp_path / "raw" / "errors.log").read_text()
    n_before = sum(u.endswith("/orderbook") for u in handler.seen)
    cap.tick()
    assert sum(u.endswith("/orderbook") for u in handler.seen) == n_before  # no more attempts


def test_no_games_day_exits_zero(tmp_path: Path) -> None:
    cap = Capture(
        DAY, tmp_path / "raw", tmp_path / "hb.json",
        fetcher=make_fetcher(routes(b'{"events": []}')),
    )  # fmt: skip
    assert cap.run() == 0
    assert json.loads((tmp_path / "hb.json").read_text())["status"] == "no_games"


def test_loop_survives_a_failing_tick(tmp_path: Path) -> None:
    cap = Capture(
        DAY, tmp_path / "raw", tmp_path / "hb.json", fetcher=make_fetcher(routes()),
        now_fn=lambda: datetime(2026, 10, 21, 21, 0, tzinfo=UTC),
    )  # fmt: skip

    def bad() -> dict[str, Any]:
        raise RuntimeError("boom")

    cap.tick = bad  # type: ignore[method-assign]
    assert cap.run(once=True) == 0
    assert cap.st.errors == 1 and "boom" in (tmp_path / "raw" / "errors.log").read_text()


def test_event_date_and_filter() -> None:
    assert kp.event_date("KXNBAGAME-26OCT21CHABKN") == DAY
    assert kp.event_date("KXNBAMVP-27") is None
    assert kp.event_teams("KXNBAGAME-26OCT21CHABKN") == ("CHA", "BKN")
