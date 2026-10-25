"""Non-negotiable 5 for the capture alone: no order, portfolio or credential code, GET-only to an
allow-list of public paths, no database, and a restart never overwrites a raw file.

Ported from tests/papertrade/test_no_orders.py (branch agent/market-paper-trader) with the paper
trader removed, so the guard ships with nba/live when nba/live ships without the trader.
"""

from __future__ import annotations

import ast
import gzip
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from nba.live import kalshi_pull
from nba.live.capture import Capture
from nba.live.http import Fetcher
from nba.live.offer import log_offer

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "nba" / "live"
FIX = Path(__file__).parent / "fixtures"
DAY = datetime(2026, 10, 21).date()
FORBIDDEN = re.compile(
    r"portfolio|/orders|order_group|place_order|create_order|cancel_order|batch_order|amend_order"
    r"|/fills|/communications|/rfq|/quotes|exchange/|api[_-]?key|private[_-]?key|access[_-]?key"
    r"|KALSHI-ACCESS|authorization|bearer|\.post\(|\.put\(|\.delete\(|\.patch\(|method=",
    re.IGNORECASE,
)
OK_PATH = re.compile(
    r"^/trade-api/v2/(series|markets|markets/trades|markets/[A-Za-z0-9\-]+"
    r"|markets/[A-Za-z0-9\-]+/orderbook)$"
    r"|^/apis/site/v2/sports/basketball/nba/(scoreboard|summary)$"
)


def test_no_order_portfolio_or_credential_strings() -> None:
    bad = [
        f"{p.relative_to(ROOT)}:{i}: {line.strip()}"
        for p in PKG.rglob("*.py")
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if FORBIDDEN.search(line)
    ]
    assert not bad, "\n".join(bad)


def test_only_market_data_paths_exist() -> None:
    assert kalshi_pull.ALLOWED_PATHS == (
        "/series",
        "/markets",
        "/markets/trades",
        "/markets/{ticker}",
        "/markets/{ticker}/orderbook",
    )
    src = (PKG / "kalshi_pull.py").read_text()
    used = set(re.findall(r"\{BASE\}(/[A-Za-z_/{}]*)", src))
    assert used and used <= set(kalshi_pull.ALLOWED_PATHS)


def test_http_layer_is_get_only() -> None:
    src = (PKG / "http.py").read_text()
    assert ".get(" in src and not re.search(r"\.(post|put|delete|patch|request|send)\(", src)


def test_capture_never_imports_a_database_or_environment() -> None:
    """The capture writes raw files only: no duckdb, no nba.db, no os.environ / getenv."""
    banned = ("duckdb", "nba.db", "nba.daily", "nba.papertrade", "research", "os")
    bad: list[str] = []
    for p in PKG.rglob("*.py"):
        tree = ast.parse(p.read_text())
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            bad += [
                f"{p.name}: {n}"
                for n in names
                if any(n == b or n.startswith(b + ".") for b in banned)
            ]
        if re.search(r"environ|getenv", p.read_text()):
            bad.append(f"{p.name}: environment access")
    assert not bad, bad


def _strict_handler(seen: list[tuple[str, str]]) -> Any:
    ms = json.loads((FIX / "kalshi_markets_pages.json").read_text())["markets"]

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "GET", f"non-GET request: {req.method} {req.url}"
        assert req.url.host in ("api.elections.kalshi.com", "site.api.espn.com"), req.url
        assert OK_PATH.match(req.url.path), f"path outside the allow-list: {req.url.path}"
        assert "authorization" not in {k.lower() for k in req.headers}, "credential header sent"
        assert "cookie" not in {k.lower() for k in req.headers}, "cookie sent"
        seen.append((req.method, req.url.path))
        p = req.url.path
        if p.endswith("/scoreboard"):
            return httpx.Response(200, content=(FIX / "espn_scoreboard.json").read_bytes())
        if p.endswith("/summary") or p.endswith("/series"):
            return httpx.Response(200, json={"series": []})
        if p.endswith("/orderbook"):
            return httpx.Response(200, json={"orderbook_fp": {"yes_dollars": [], "no_dollars": []}})
        if p.endswith("/markets/trades"):
            assert req.url.params.get("ticker"), "trades must be asked per ticker"
            return httpx.Response(200, json={"trades": [], "cursor": ""})
        if p.endswith("/markets"):
            series = req.url.params.get("series_ticker", "")
            return httpx.Response(
                200, json={"markets": [m for m in ms if m["ticker"].startswith(series)]}
            )
        t = p.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"market": next(m for m in ms if m["ticker"] == t)})

    return handler


def _fetcher(seen: list[tuple[str, str]]) -> Fetcher:
    client = httpx.Client(transport=httpx.MockTransport(_strict_handler(seen)))
    return Fetcher(client=client, rate_per_s=1e6, sleep=lambda s: None)


FROZEN = datetime(2026, 10, 21, 23, 30, tzinfo=UTC)  # inside the fixture slate's window


def test_end_to_end_capture_and_offer_only_get_allowed_paths(tmp_path: Path) -> None:
    seen: list[tuple[str, str]] = []
    cap = Capture(
        DAY, tmp_path / "raw", tmp_path / "hb.json", fetcher=_fetcher(seen), now_fn=lambda: FROZEN
    )
    cap.sweep("start")
    cap.tick()
    ms = json.loads((FIX / "kalshi_markets_pages.json").read_text())["markets"]
    log_offer(
        tmp_path / "raw", combo=ms[0]["ticker"], offer=1.0, contracts=1.0,
        fetcher=_fetcher(seen), legs=[ms[1]["ticker"]], now_fn=lambda: FROZEN,
    )  # fmt: skip
    assert seen and {m for m, _ in seen} == {"GET"}
    written = {p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file()}
    assert all(str(p).startswith("raw/") or p.name == "hb.json" for p in written), written


def test_restart_on_same_day_never_overwrites_a_raw_file(tmp_path: Path) -> None:
    """Two captures (a restart, or a duplicate writer run in sequence) on one out dir and one
    frozen clock: every manifest row points at its own file and its sha256 still matches."""
    out = tmp_path / "raw"
    for _ in range(2):
        cap = Capture(DAY, out, tmp_path / "hb.json", fetcher=_fetcher([]), now_fn=lambda: FROZEN)
        cap.sweep("start")
        cap.tick()
        cap.beat("running")
    rows = [json.loads(x) for x in (out / "manifest.jsonl").read_text().splitlines()]
    paths = [r["path"] for r in rows]
    assert len(paths) == len(set(paths)), "a manifest path was written twice"
    for r in rows:
        body = gzip.decompress((out / r["path"]).read_bytes())
        assert hashlib.sha256(body).hexdigest() == r["sha256"], r["path"]
    hb = json.loads((tmp_path / "hb.json").read_text())
    assert hb["date"] == DAY.isoformat() and hb["job"] == "live_capture"
    assert hb["status"] == "running" and (out / "heartbeat.json").exists()
