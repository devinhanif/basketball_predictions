"""Read-only pull of public Polymarket (international) NBA trade data with wallet addresses.

Purpose (DECISIONS 2026-10-27, SHARP_WALLETS_PERSIST): raw material for a descriptive check. This
module only FETCHES and STORES; it computes nothing about wallets (no PnL, no ranking).

Hard guarantees:
  - GET only, on public unauthenticated routes of gamma-api / data-api / clob (market data).
    No header carries a credential; no order, portfolio or account route is known to this module.
  - Season 2025 (2025-26) is refused: every date is validated against ``SEASON_2024_LAST_DAY`` and
    trade queries carry ``end = 2025-07-01T00:00Z`` so no later trade can be returned.
  - Raw output is write-once: a shard that exists is never rewritten; reruns resume.
  - ``httpx`` is imported lazily so the module imports without network access.

Stages (run in order; each is resumable):
  markets  list NBA events/markets, keep those whose game date is inside the window
  trades   one parquet shard per market (every trade, with proxyWallet)
  prices   one parquet shard per outcome token (CLOB price history around the game)

Two windows (selection data can be checked before report data is fetched):
  selection  games 2024-10-22 .. 2025-01-31
  report     games 2025-02-01 .. 2025-06-30
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from nba.ingest.cache import RateLimiter

GAMMA_URL = "https://gamma-api.polymarket.com"
DATA_URL = "https://data-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
NBA_TAG_ID = 745  # docs.polymarket.com/market-data/fetching-markets (verify via /tags/slug/nba)

#: Window bounds (game date, UTC). Season 2024-25 ran 2024-10-22 .. 2025-06-22.
WINDOWS: dict[str, tuple[date, date]] = {
    "selection": (date(2024, 10, 1), date(2025, 1, 31)),
    "report": (date(2025, 2, 1), date(2025, 6, 30)),
}
#: Nothing after this day is ever requested or kept (season 2025 = 2025-26 is the frozen holdout).
SEASON_2024_LAST_DAY = date(2025, 6, 30)
TRADE_END_TS = int(datetime(2025, 7, 1, tzinfo=UTC).timestamp())

PAGE_LIMIT_MARKETS = 100
PAGE_LIMIT_TRADES = 1000  # documented max 10000 (clamped); offset max 10000 (400 beyond)
OFFSET_CAP = 10000
MAX_ATTEMPTS = 5
MIN_INTERVAL_S = 0.25  # 4 req/s; documented ceilings are 200-300 per 10 s (>= 20-30 req/s)
USER_AGENT = "nba-prediction-research/1 (read-only public market data; contact: repo maintainer)"

GetJson = Callable[[str, dict[str, Any]], Any]


class HoldoutRefused(ValueError):
    """Raised when a request would touch season 2025 (2025-26)."""


def check_window(
    name: str, start: date | None = None, end: date | None = None
) -> tuple[date, date]:
    """Validate a window name and optional narrowing; refuse anything past 2025-06-30."""
    if name not in WINDOWS:
        raise ValueError(f"unknown window {name!r}; choose from {sorted(WINDOWS)}")
    lo, hi = WINDOWS[name]
    start = start or lo
    end = end or hi
    if end > SEASON_2024_LAST_DAY or start > SEASON_2024_LAST_DAY:
        raise HoldoutRefused(
            f"{end} is after {SEASON_2024_LAST_DAY}: season 2025 (2025-26) is the frozen holdout"
        )
    if start < lo or end > hi or start > end:
        raise ValueError(f"[{start}, {end}] is not inside the {name} window [{lo}, {hi}]")
    return start, end


# ---------------------------------------------------------------- HTTP (GET only, no credentials)


def make_http_get(limiter: RateLimiter | None = None) -> GetJson:
    """Return a ``get_json(url, params)`` using httpx.get with retry/backoff. No auth, ever."""
    import httpx

    limiter = limiter or RateLimiter(MIN_INTERVAL_S)

    def get_json(url: str, params: dict[str, Any]) -> Any:
        delay = 2.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            limiter.wait()
            try:
                r = httpx.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=60.0)
            except httpx.TransportError:
                if attempt == MAX_ATTEMPTS:
                    raise
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504) and attempt < MAX_ATTEMPTS:
                retry_after = r.headers.get("Retry-After")
                time.sleep(float(retry_after) if retry_after else delay)
                delay *= 2
                continue
            r.raise_for_status()
        raise RuntimeError("unreachable")

    return get_json


# ---------------------------------------------------------------- helpers


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    s = str(value).replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _maybe_json_list(value: Any) -> list[Any]:
    """Gamma returns outcomes / clobTokenIds as JSON-encoded strings."""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            out = json.loads(value)
        except ValueError:
            return []
        return out if isinstance(out, list) else []
    return []


def _items(payload: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """Tolerant page parser (keyset response envelope was not verifiable offline)."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for k in keys:
            v = payload.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


def _next_cursor(payload: Any) -> str | None:
    if isinstance(payload, dict):
        c = payload.get("next_cursor") or payload.get("nextCursor")
        if not c and isinstance(payload.get("pagination"), dict):
            c = payload["pagination"].get("next_cursor")
        return str(c) if c else None
    return None


def _write_once(path: Path, table: pa.Table) -> bool:
    """Atomic write-once parquet. Returns False (and does nothing) if the shard exists."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.rename(path)
    return True


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------- stage: markets

MARKET_SCHEMA = pa.schema(
    [
        ("condition_id", pa.string()),
        ("market_id", pa.string()),
        ("slug", pa.string()),
        ("question", pa.string()),
        ("sports_market_type", pa.string()),
        ("line", pa.float64()),
        ("outcomes", pa.string()),
        ("clob_token_ids", pa.string()),
        ("game_start_time", pa.string()),
        ("closed_time", pa.string()),
        ("end_date", pa.string()),
        ("game_date", pa.string()),
        ("event_id", pa.string()),
        ("event_slug", pa.string()),
        ("event_title", pa.string()),
        ("volume", pa.float64()),
        ("fetched_at", pa.string()),
    ]
)


def _f(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def iter_event_pages(get_json: GetJson, *, max_pages: int = 5000) -> Iterator[Any]:
    """Page through every closed NBA event (gamma keyset pagination via next_cursor)."""
    params: dict[str, Any] = {"tag_id": NBA_TAG_ID, "closed": "true", "limit": PAGE_LIMIT_MARKETS}
    seen_cursors: set[str] = set()
    for _ in range(max_pages):
        page = get_json(f"{GAMMA_URL}/events/keyset", params)
        yield page
        cur = _next_cursor(page)
        if not cur or cur in seen_cursors:
            return
        seen_cursors.add(cur)
        params = {**params, "after_cursor": cur}


def market_rows(
    pages: Iterable[Any], start: date, end: date, fetched_at: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Flatten event pages to market rows inside [start, end] by game date.

    Markets with no usable date are returned by slug in ``undated`` (never guessed in).
    """
    rows: list[dict[str, Any]] = []
    undated: list[str] = []
    seen: set[str] = set()
    for page in pages:
        for ev in _items(page, ("events", "data", "items")):
            for m in ev.get("markets") or []:
                cid = m.get("conditionId")
                if not cid or cid in seen:
                    continue
                gst = _parse_ts(m.get("gameStartTime") or ev.get("startTime"))
                ed = _parse_ts(m.get("endDate") or ev.get("endDate"))
                when = gst or ed
                if when is None:
                    undated.append(str(m.get("slug") or cid))
                    continue
                gd = when.astimezone(UTC).date()
                if not (start <= gd <= end):
                    continue
                seen.add(cid)
                rows.append(
                    {
                        "condition_id": cid,
                        "market_id": str(m.get("id") or ""),
                        "slug": m.get("slug"),
                        "question": m.get("question"),
                        "sports_market_type": m.get("sportsMarketType"),
                        "line": _f(m.get("line")),
                        "outcomes": json.dumps(_maybe_json_list(m.get("outcomes"))),
                        "clob_token_ids": json.dumps(_maybe_json_list(m.get("clobTokenIds"))),
                        "game_start_time": m.get("gameStartTime"),
                        "closed_time": m.get("closedTime"),
                        "end_date": m.get("endDate"),
                        "game_date": gd.isoformat(),
                        "event_id": str(ev.get("id") or ""),
                        "event_slug": ev.get("slug"),
                        "event_title": ev.get("title"),
                        "volume": _f(m.get("volumeNum", m.get("volume"))),
                        "fetched_at": fetched_at,
                    }
                )
    return rows, undated


def stage_markets(get_json: GetJson, out: Path, start: date, end: date) -> dict[str, Any]:
    target = out / "markets.parquet"
    if target.exists():
        n = pq.read_metadata(target).num_rows
        return {"markets": n, "skipped": "markets.parquet exists"}
    fetched_at = _now()
    raw_dir = out / "raw_events"
    pages: list[Any] = []
    for i, page in enumerate(iter_event_pages(get_json)):
        raw = raw_dir / f"events_page_{i:05d}.json"
        raw.parent.mkdir(parents=True, exist_ok=True)
        if not raw.exists():
            raw.write_text(json.dumps(page))
        pages.append(page)
    rows, undated = market_rows(pages, start, end, fetched_at)
    _write_once(target, pa.Table.from_pylist(rows, schema=MARKET_SCHEMA))
    if undated:
        (out / "markets_undated.json").write_text(json.dumps(sorted(undated)))
    by_type: dict[str, int] = {}
    for r in rows:
        k = str(r["sports_market_type"])
        by_type[k] = by_type.get(k, 0) + 1
    return {"markets": len(rows), "by_type": by_type, "undated": len(undated), "pages": len(pages)}


# ---------------------------------------------------------------- stage: trades

TRADE_SCHEMA = pa.schema(
    [
        ("condition_id", pa.string()),
        ("proxy_wallet", pa.string()),
        ("side", pa.string()),
        ("asset", pa.string()),
        ("outcome", pa.string()),
        ("outcome_index", pa.int64()),
        ("size", pa.float64()),
        ("price", pa.float64()),
        ("timestamp", pa.int64()),
        ("transaction_hash", pa.string()),
        ("name", pa.string()),
        ("pseudonym", pa.string()),
        ("slug", pa.string()),
        ("fetched_at", pa.string()),
    ]
)


def _trade_key(t: dict[str, Any]) -> tuple[Any, ...]:
    return (
        t.get("transactionHash"),
        t.get("asset"),
        t.get("proxyWallet"),
        t.get("side"),
        t.get("price"),
        t.get("size"),
        t.get("timestamp"),
    )


def fetch_market_trades(
    get_json: GetJson,
    condition_id: str,
    *,
    taker_only: bool,
    limit: int = PAGE_LIMIT_TRADES,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every trade for one market, newest first, de-duplicated.

    Pages by offset until a short page; when the offset cap is reached the window slides
    (``end`` = oldest timestamp seen) so each slice gets a fresh offset budget. Overlapping
    rows are removed by key. ``truncated`` is set if a slide makes no progress.
    """
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    end_ts = TRADE_END_TS
    requests = 0
    truncated = False
    while True:
        offset = 0
        new_in_slice = 0
        slice_full = False
        oldest = end_ts
        while True:
            if offset > OFFSET_CAP:
                slice_full = True
                break
            page = get_json(
                f"{DATA_URL}/trades",
                {
                    "market": condition_id,
                    "limit": limit,
                    "offset": offset,
                    "takerOnly": "true" if taker_only else "false",
                    "end": end_ts,
                },
            )
            requests += 1
            rows = _items(page, ("data", "trades"))
            for t in rows:
                k = _trade_key(t)
                if k in seen:
                    continue
                seen.add(k)
                out.append(t)
                new_in_slice += 1
                ts = t.get("timestamp")
                if isinstance(ts, int) and ts < oldest:
                    oldest = ts
            if len(rows) < limit:
                return out, {"requests": requests, "truncated": truncated}
            offset += len(rows)
        if not slice_full:  # pragma: no cover - loop exits via return or slice_full
            return out, {"requests": requests, "truncated": truncated}
        if new_in_slice == 0 or oldest >= end_ts:
            return out, {"requests": requests, "truncated": True}
        end_ts = oldest


def trade_table(trades: list[dict[str, Any]], condition_id: str, fetched_at: str) -> pa.Table:
    rows = [
        {
            "condition_id": condition_id,
            "proxy_wallet": t.get("proxyWallet"),
            "side": t.get("side"),
            "asset": t.get("asset"),
            "outcome": t.get("outcome"),
            "outcome_index": t.get("outcomeIndex"),
            "size": _f(t.get("size")),
            "price": _f(t.get("price")),
            "timestamp": t.get("timestamp"),
            "transaction_hash": t.get("transactionHash"),
            "name": t.get("name"),
            "pseudonym": t.get("pseudonym"),
            "slug": t.get("slug"),
            "fetched_at": fetched_at,
        }
        for t in trades
    ]
    return pa.Table.from_pylist(rows, schema=TRADE_SCHEMA)


def stage_trades(
    get_json: GetJson, out: Path, *, taker_only: bool, max_markets: int | None = None
) -> dict[str, Any]:
    markets = pq.read_table(out / "markets.parquet").column("condition_id").to_pylist()
    done = skipped = failed_n = reqs = trunc = n_trades = 0
    failures: list[dict[str, str]] = []
    for cid in markets:
        shard = out / "trades" / f"{cid}.parquet"
        if shard.exists():
            skipped += 1
            continue
        if max_markets is not None and done >= max_markets:
            break
        try:
            trades, info = fetch_market_trades(get_json, cid, taker_only=taker_only)
        except Exception as exc:  # network failure: record, continue; rerun resumes
            failed_n += 1
            failures.append({"condition_id": cid, "error": repr(exc)[:200]})
            continue
        _write_once(shard, trade_table(trades, cid, _now()))
        done += 1
        reqs += info["requests"]
        trunc += int(info["truncated"])
        n_trades += len(trades)
    if failures:
        (out / "trades_failures.json").write_text(json.dumps(failures))
    return {
        "markets_total": len(markets),
        "fetched_now": done,
        "already_done": skipped,
        "failed": failed_n,
        "truncated": trunc,
        "requests": reqs,
        "trades_now": n_trades,
    }


# ---------------------------------------------------------------- stage: prices

PRICE_SCHEMA = pa.schema(
    [
        ("condition_id", pa.string()),
        ("token_id", pa.string()),
        ("t", pa.int64()),
        ("p", pa.float64()),
        ("fetched_at", pa.string()),
    ]
)
PRICE_FIDELITY_MIN = 5
PRICE_PRE_H = 24
PRICE_POST_H = 6


def price_window(market: dict[str, Any]) -> tuple[int, int] | None:
    gst = _parse_ts(market.get("game_start_time")) or _parse_ts(market.get("end_date"))
    if gst is None:
        return None
    close = _parse_ts(market.get("closed_time"))
    hi = min(
        (close or gst + timedelta(hours=PRICE_POST_H)),
        datetime.fromtimestamp(TRADE_END_TS, UTC),
    )
    lo = gst - timedelta(hours=PRICE_PRE_H)
    return int(lo.timestamp()), int(hi.timestamp())


def stage_prices(get_json: GetJson, out: Path, *, max_tokens: int | None = None) -> dict[str, Any]:
    markets = pq.read_table(out / "markets.parquet").to_pylist()
    done = skipped = failed_n = reqs = 0
    failures: list[dict[str, str]] = []
    for m in markets:
        win = price_window(m)
        if win is None:
            continue
        for tok in _maybe_json_list(m["clob_token_ids"]):
            shard = out / "prices" / f"{tok}.parquet"
            if shard.exists():
                skipped += 1
                continue
            if max_tokens is not None and done >= max_tokens:
                break
            try:
                page = get_json(
                    f"{CLOB_URL}/prices-history",
                    {
                        "market": tok,
                        "startTs": win[0],
                        "endTs": win[1],
                        "fidelity": PRICE_FIDELITY_MIN,
                    },
                )
            except Exception as exc:
                failed_n += 1
                failures.append({"token_id": str(tok), "error": repr(exc)[:200]})
                continue
            reqs += 1
            hist = page.get("history", []) if isinstance(page, dict) else []
            fa = _now()
            rows = [
                {
                    "condition_id": m["condition_id"],
                    "token_id": str(tok),
                    "t": int(h["t"]),
                    "p": float(h["p"]),
                    "fetched_at": fa,
                }
                for h in hist
            ]
            _write_once(shard, pa.Table.from_pylist(rows, schema=PRICE_SCHEMA))
            done += 1
    if failures:
        (out / "prices_failures.json").write_text(json.dumps(failures))
    return {"fetched_now": done, "already_done": skipped, "failed": failed_n, "requests": reqs}


# ---------------------------------------------------------------- README / manifest / CLI

README = """# Polymarket (international) NBA trades with wallets -- raw, write-once

Window: {window} ({start} .. {end}, game date UTC). Season 2025 (2025-26) is refused by the script.
Created: {created}. Script: nba/markets/polymarket_wallets.py (GET only, no keys, no credentials).

## Sources (public, unauthenticated)
- Markets: {gamma}/events/keyset?tag_id={tag}&closed=true  (NBA tag id {tag})
- Trades:  {data}/trades?market=<conditionId>&takerOnly={taker}&end=<unix>&limit&offset
  (docs: docs.polymarket.com/api-reference/core/get-trades-for-a-user-or-markets)
- Prices:  {clob}/prices-history?market=<token_id>&startTs&endTs&fidelity={fid}

## Files
- markets.parquet           one row per market (condition_id, tokens, type, line, dates)
- raw_events/*.json         raw gamma pages as received
- trades/<condition_id>.parquet   every trade: proxy_wallet, side, asset, outcome, size, price,
                                  timestamp (unix s), transaction_hash, name, pseudonym
- prices/<token_id>.parquet       CLOB history (t unix s, p), game start -{pre}h to close or +{post}h
- manifest.json             per-stage counts, fetched_at, failures; *_failures.json if any

## Notes
- takerOnly={taker}. True = taker side of each fill only (documented default).
- Pagination: offset capped at 10000; the script slides the `end` window and de-duplicates.
  A market with `truncated` in the manifest hit a same-timestamp wall; listed, not patched.
- Raw is immutable: shards are never rewritten. Nothing here is analysed or scored.
- Terms: Polymarket's public data APIs; see the report for the ToS caveats. Do not redistribute.
"""


def _write_readme(out: Path, window: str, start: date, end: date, taker_only: bool) -> None:
    p = out / "README.md"
    if p.exists():
        return
    out.mkdir(parents=True, exist_ok=True)
    p.write_text(
        README.format(
            window=window,
            start=start,
            end=end,
            created=_now(),
            gamma=GAMMA_URL,
            data=DATA_URL,
            clob=CLOB_URL,
            tag=NBA_TAG_ID,
            taker=str(taker_only).lower(),
            fid=PRICE_FIDELITY_MIN,
            pre=PRICE_PRE_H,
            post=PRICE_POST_H,
        )
    )


def _update_manifest(out: Path, stage: str, info: dict[str, Any]) -> None:
    p = out / "manifest.json"
    m: dict[str, Any] = json.loads(p.read_text()) if p.exists() else {}
    m.setdefault("runs", []).append({"stage": stage, "at": _now(), **info})
    p.write_text(json.dumps(m, indent=2))


def run(
    window: str,
    stage: str,
    out_root: Path,
    run_date: str,
    *,
    get_json: GetJson | None = None,
    start: date | None = None,
    end: date | None = None,
    taker_only: bool = False,
    max_items: int | None = None,
) -> dict[str, Any]:
    s, e = check_window(window, start, end)
    out = out_root / f"polymarket_wallets_{run_date}" / window
    out.mkdir(parents=True, exist_ok=True)
    _write_readme(out, window, s, e, taker_only)
    get = get_json or make_http_get()
    result: dict[str, Any] = {"out": str(out)}
    stages = ["markets", "trades", "prices"] if stage == "all" else [stage]
    for st in stages:
        if st == "markets":
            info = stage_markets(get, out, s, e)
        elif not (out / "markets.parquet").exists():
            raise FileNotFoundError("run the markets stage first")
        elif st == "trades":
            info = stage_trades(get, out, taker_only=taker_only, max_markets=max_items)
        elif st == "prices":
            info = stage_prices(get, out, max_tokens=max_items)
        else:
            raise ValueError(f"unknown stage {st!r}")
        _update_manifest(out, st, info)
        result[st] = info
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    ap.add_argument("--window", required=True, choices=sorted(WINDOWS))
    ap.add_argument("--stage", default="markets", choices=["markets", "trades", "prices", "all"])
    ap.add_argument("--out-root", type=Path, default=Path("data/raw"))
    ap.add_argument("--run-date", default=date.today().isoformat(), help="reuse to resume")
    ap.add_argument("--start", type=date.fromisoformat, default=None)
    ap.add_argument("--end", type=date.fromisoformat, default=None)
    ap.add_argument("--taker-only", action="store_true", help="documented default; else all fills")
    ap.add_argument("--max-items", type=int, default=None, help="cap this run (smoke test)")
    a = ap.parse_args(argv)
    try:
        res = run(
            a.window,
            a.stage,
            a.out_root,
            a.run_date,
            start=a.start,
            end=a.end,
            taker_only=a.taker_only,
            max_items=a.max_items,
        )
    except HoldoutRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
