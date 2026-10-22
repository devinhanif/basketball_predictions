"""Non-negotiable 5: no order, portfolio or credential code in the capture or paper-trade code."""

from __future__ import annotations

import re
from pathlib import Path

from nba.live import kalshi_pull

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = [ROOT / "nba" / "live", ROOT / "nba" / "papertrade"]
FORBIDDEN = re.compile(
    r"portfolio|/orders|order_group|place_order|create_order|cancel_order|batch_order|amend_order"
    r"|/fills|/communications|/rfq|/quotes|exchange/|api[_-]?key|private[_-]?key|access[_-]?key"
    r"|KALSHI-ACCESS|authorization|bearer|\.post\(|\.put\(|\.delete\(|\.patch\(|method=",
    re.IGNORECASE,
)


def test_no_order_portfolio_or_credential_strings() -> None:
    bad = []
    for pkg in PACKAGES:
        for path in pkg.rglob("*.py"):
            for i, line in enumerate(path.read_text().splitlines(), 1):
                if FORBIDDEN.search(line):
                    bad.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not bad, "\n".join(bad)


def test_only_market_data_paths_exist() -> None:
    assert kalshi_pull.ALLOWED_PATHS == (
        "/series",
        "/markets",
        "/markets/trades",
        "/markets/{ticker}",
        "/markets/{ticker}/orderbook",
    )
    src = (ROOT / "nba" / "live" / "kalshi_pull.py").read_text()
    used = set(re.findall(r"\{BASE\}(/[A-Za-z_/{}]*)", src))
    normalised = {u.replace("{ticker}", "{ticker}") for u in used}
    assert normalised <= set(kalshi_pull.ALLOWED_PATHS)


def test_http_layer_is_get_only() -> None:
    src = (ROOT / "nba" / "live" / "http.py").read_text()
    assert ".get(" in src and not re.search(r"\.(post|put|delete|patch|request|send)\(", src)


# ----------------------------------------------------------------------------- behavioural (S8)
def test_end_to_end_only_get_requests_to_allowed_paths(tmp_path: Path) -> None:
    """Capture tick, scan (listing + order books), monitor and settle over a transport that fails
    the test on any non-GET request, any other host, or any path outside the allow-list."""
    import json
    from datetime import UTC, date, datetime

    import httpx

    from nba.live.capture import Capture
    from nba.live.http import Fetcher
    from nba.papertrade import config as C
    from nba.papertrade.forecasts import Forecasts
    from nba.papertrade.monitor import monitor_once
    from nba.papertrade.scan import fetch_markets, run_scan
    from nba.papertrade.settle import settle_positions
    from tests.papertrade.test_papertrade import ALIASES, DAY, NOW, forecasts

    fix = ROOT / "tests" / "live" / "fixtures"
    ms = json.loads((fix / "kalshi_markets_pages.json").read_text())["markets"]
    ok_path = re.compile(
        r"^/trade-api/v2/(series|markets|markets/trades|markets/[A-Za-z0-9\-]+"
        r"|markets/[A-Za-z0-9\-]+/orderbook)$"
        r"|^/apis/site/v2/sports/basketball/nba/(scoreboard|summary)$"
    )
    seen: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "GET", f"non-GET request: {req.method} {req.url}"
        assert req.url.host in ("api.elections.kalshi.com", "site.api.espn.com"), req.url
        assert ok_path.match(req.url.path), f"path outside the allow-list: {req.url.path}"
        assert "authorization" not in {k.lower() for k in req.headers}, "credential header sent"
        seen.append((req.method, req.url.path))
        p = req.url.path
        if p.endswith("/scoreboard"):
            return httpx.Response(200, content=(fix / "espn_scoreboard.json").read_bytes())
        if p.endswith("/summary") or p.endswith("/series"):
            return httpx.Response(200, json={"series": []})
        if p.endswith("/orderbook"):
            m = next(x for x in ms if x["ticker"] == p.split("/")[-2])
            yb, ya = float(m["yes_bid_dollars"]), float(m["yes_ask_dollars"])
            book = {
                "yes_dollars": [[f"{yb:.4f}", "9999.00"]],
                "no_dollars": [[f"{1 - ya:.4f}", "9999.00"]],
            }
            return httpx.Response(200, json={"orderbook_fp": book})
        if p.endswith("/markets/trades"):
            assert req.url.params.get("ticker"), "trades must be asked per ticker"
            return httpx.Response(200, json={"trades": [], "cursor": ""})
        if p.endswith("/markets"):
            series = req.url.params.get("series_ticker", "")
            return httpx.Response(
                200, json={"markets": [m for m in ms if m["ticker"].startswith(series)]}
            )
        t = p.rsplit("/", 1)[-1]
        return httpx.Response(
            200, json={"market": {**next(m for m in ms if m["ticker"] == t), "result": ""}}
        )

    fetcher = Fetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        rate_per_s=1e6,
        sleep=lambda s: None,
    )
    cap = Capture(
        DAY, tmp_path / "raw", tmp_path / "hb.json", fetcher=fetcher,
        now_fn=lambda: datetime(2026, 10, 21, 21, 0, tzinfo=UTC),
    )  # fmt: skip
    cap.sweep("start")
    cap.tick()
    from nba.live import kalshi_pull as kp

    cfg = C.load().with_rule_version("paper-trader-v2-shakedown")
    fetcher.begin_tick(100, 30.0)
    markets = fetch_markets(fetcher, DAY, [*cfg.prop_series, "KXNBAGAME"], None)
    res = run_scan(
        DAY, NOW, cfg, markets=markets, fc=forecasts(), aliases=ALIASES,
        db_path=tmp_path / "pt.duckdb", legs_file=tmp_path / "legs.json",
        book_fn=lambda t: kp.parse_body(kp.get_orderbook(fetcher, t)),
    )  # fmt: skip
    assert res.positions
    getter = lambda t: kp.get_market(fetcher, t)  # noqa: E731
    fetcher.begin_tick(100, 30.0)
    monitor_once(DAY, NOW, cfg, getter, tmp_path / "pt.duckdb", tmp_path / "legs.json")
    settle_positions(date(2026, 10, 22), NOW, cfg, getter, tmp_path / "pt.duckdb")
    assert seen and {m for m, _ in seen} == {"GET"}
    assert any(p.endswith("/orderbook") for _, p in seen) and Forecasts is not None
