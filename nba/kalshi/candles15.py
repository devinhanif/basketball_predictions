"""Derived 15-second candles, the display export and the 1-minute reconciliation (display only).

Inputs are the write-once raw files of ``nba.kalshi.poll15`` (quote polls and trade prints).
Everything here is rebuildable from them; no alert, EV or signal path reads any of it (calm
calls: no swing-chasing).

Two candle kinds, never blended:

* ``candles`` -- TRUE candles from executed trades (Kalshi's own ``created_time``): yes-price
  open/high/low/close and contracts per 15 s bucket; a bucket with no trade has no candle (the chart
  holds the previous close flat; nothing is invented).
* ``quotes`` -- the polled book, one point per bucket (the last poll in the bucket): yes
  bid/ask/mid/last, cumulative volume, open interest. With a 15 s poll that is about one
  sample per bucket, so a quote "candle" would be degenerate; the quote series is exported as
  points, with ``n_polls`` per bucket.

Reconciliation compares our trade-built 1-minute rollup with Kalshi's official 1-minute candles
(``period_interval=1``, the finest official period) for the same tickers and minutes.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from nba.kalshi.client import KalshiClient
from nba.kalshi.poll15 import (
    QUOTE_SCHEMA,
    TRADE_SCHEMA,
    PollConfig,
    RawStore,
    RequestBudget,
    _f,
    write_json_atomic,
)

SCHEMA_VERSION = 1
QUOTE_FIELDS = ["t", "bid", "ask", "mid", "last", "volume", "open_interest", "n_polls"]
CANDLE_FIELDS = ["t", "o", "h", "l", "c", "volume", "n_trades"]
OFFICIAL_SCHEMA: dict[str, Any] = {
    "ticker": pl.Utf8,
    "end_ts": pl.Int64,
    "o": pl.Float64,
    "h": pl.Float64,
    "l": pl.Float64,
    "c": pl.Float64,
    "volume": pl.Float64,
    "open_interest": pl.Float64,
    "yes_bid_close": pl.Float64,
    "yes_ask_close": pl.Float64,
}


def quotes_frame(rows: Sequence[Mapping[str, Any]]) -> pl.DataFrame:
    return pl.DataFrame(list(rows), schema=QUOTE_SCHEMA)


def trades_frame(rows: Sequence[Mapping[str, Any]]) -> pl.DataFrame:
    return pl.DataFrame(list(rows), schema=TRADE_SCHEMA)


def _epoch_s(col: str) -> pl.Expr:
    return (pl.col(col).dt.epoch("us") // 1_000_000).alias("t")


def quote_points(q: pl.DataFrame, bucket_s: int) -> pl.DataFrame:
    """Per ticker and bucket: the LAST poll in the bucket (bid/ask/mid/last/volume/OI) and the poll
    count. ``mid`` is null on a one-sided book (Kalshi reports an empty side as 0.0000)."""
    if q.is_empty():
        return pl.DataFrame(
            schema={
                "ticker": pl.Utf8,
                "t": pl.Int64,
                "bid": pl.Float64,
                "ask": pl.Float64,
                "mid": pl.Float64,
                "last": pl.Float64,
                "volume": pl.Float64,
                "open_interest": pl.Float64,
                "n_polls": pl.UInt32,
            }
        )
    two_sided = (pl.col("yes_bid") > 0) & (pl.col("yes_ask") > 0)
    return (
        q.sort("ticker", "fetched_at")
        .with_columns(pl.col("fetched_at").dt.truncate(f"{bucket_s}s").alias("bucket"))
        .with_columns(
            (pl.col("bucket").dt.epoch("us") // 1_000_000).alias("t"),
            pl.when(two_sided)
            .then((pl.col("yes_bid") + pl.col("yes_ask")) / 2)
            .otherwise(None)
            .alias("mid"),
        )
        .group_by("ticker", "t", maintain_order=True)
        .agg(
            pl.col("yes_bid").last().alias("bid"),
            pl.col("yes_ask").last().alias("ask"),
            pl.col("mid").last().alias("mid"),
            pl.col("last").last().alias("last"),
            pl.col("volume").last().alias("volume"),
            pl.col("open_interest").last().alias("open_interest"),
            pl.len().alias("n_polls"),
        )
        .sort("ticker", "t")
    )


def trade_candles(t: pl.DataFrame, bucket_s: int) -> pl.DataFrame:
    """True OHLC of executed yes prices per ticker and bucket. Duplicate trade ids (a trade seen on
    two pulls) count once; within one ``created_time`` the print order is not recoverable, so the
    open/close of a bucket whose boundary prints share a microsecond is a tie broken by trade id."""
    if t.is_empty():
        return pl.DataFrame(
            schema={
                "ticker": pl.Utf8,
                "t": pl.Int64,
                "o": pl.Float64,
                "h": pl.Float64,
                "l": pl.Float64,
                "c": pl.Float64,
                "volume": pl.Float64,
                "n_trades": pl.UInt32,
            }
        )
    return (
        t.unique(subset=["trade_id"], keep="first")
        .sort("ticker", "created_time", "trade_id")
        .with_columns(
            (pl.col("created_time").dt.truncate(f"{bucket_s}s").dt.epoch("us") // 1_000_000).alias(
                "t"
            )
        )
        .group_by("ticker", "t", maintain_order=True)
        .agg(
            pl.col("yes_price").first().alias("o"),
            pl.col("yes_price").max().alias("h"),
            pl.col("yes_price").min().alias("l"),
            pl.col("yes_price").last().alias("c"),
            pl.col("count").sum().alias("volume"),
            pl.len().alias("n_trades"),
        )
        .sort("ticker", "t")
    )


def _rows(df: pl.DataFrame, fields: list[str]) -> list[list[Any]]:
    return [list(r) for r in df.select(fields).iter_rows()]


def build_export(
    q: pl.DataFrame,
    t: pl.DataFrame,
    *,
    day: date,
    bucket_s: int,
    built_at: datetime,
    status: str,
    recon: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The ``kalshi_candles15`` v1 payload (docs/contracts/kalshi_candles15_v1.md)."""
    pts = quote_points(q, bucket_s)
    cds = trade_candles(t, bucket_s)
    meta = (
        q.sort("fetched_at")
        .group_by("ticker")
        .agg(
            pl.col("series").last(),
            pl.col("event_ticker").last(),
            pl.col("title").last(),
            pl.col("fetched_at").min().alias("first_poll"),
            pl.col("fetched_at").max().alias("last_poll"),
            pl.len().alias("n_polls"),
        )
        if not q.is_empty()
        else pl.DataFrame()
    )
    markets: list[dict[str, Any]] = []
    for m in meta.sort("ticker").iter_rows(named=True) if not meta.is_empty() else []:
        tk = m["ticker"]
        p = pts.filter(pl.col("ticker") == tk)
        c = cds.filter(pl.col("ticker") == tk)
        gaps = p["t"].diff().drop_nulls()
        max_gap = gaps.max() if len(gaps) else None
        markets.append(
            {
                "ticker": tk,
                "series": m["series"],
                "event_ticker": m["event_ticker"],
                "title": m["title"],
                "first_poll": m["first_poll"].isoformat().replace("+00:00", "Z"),
                "last_poll": m["last_poll"].isoformat().replace("+00:00", "Z"),
                "n_polls": m["n_polls"],
                "n_quote_points": p.height,
                "n_candles": c.height,
                "n_trades": int(c["n_trades"].sum() or 0),
                "max_gap_s": max_gap if isinstance(max_gap, int) else None,  # missing polls show
                "quotes": _rows(p, QUOTE_FIELDS),
                "candles": _rows(c, CANDLE_FIELDS),
            }
        )
    return {
        "schema": SCHEMA_VERSION,
        "kind": "kalshi_candles15",
        "display_only": True,
        "built_at": built_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "status": status,
        "slate_date": day.isoformat(),
        "bucket_s": bucket_s,
        "time_unit": "t is the bucket start in unix seconds (UTC)",
        "price_unit": "dollars per contract, 0 to 1, yes side",
        "quote_fields": QUOTE_FIELDS,
        "candle_fields": CANDLE_FIELDS,
        "notes": [
            "Candles are executed trades only; no trade in a bucket, no candle (hold last close).",
            "Quotes are the polled book (last poll in a bucket); mid is null on a one-sided book.",
            "Display data. Not an alert, EV or signal input, and not a forecast.",
        ],
        "reconciliation": dict(recon) if recon else None,
        "markets": markets,
    }


def write_export(
    export_dir: Path,
    day: date,
    q: pl.DataFrame,
    t: pl.DataFrame,
    *,
    bucket_s: int,
    built_at: datetime,
    status: str,
    recon: Mapping[str, Any] | None = None,
) -> Path:
    """Atomic: ``latest.json`` always, ``<day>.json`` once final (a failed build keeps the old)."""
    payload = build_export(
        q, t, day=day, bucket_s=bucket_s, built_at=built_at, status=status, recon=recon
    )
    path = export_dir / f"{day.isoformat()}.json"
    if (
        status != "live"
    ):  # the dated file is the finished night; live builds only refresh latest.json
        write_json_atomic(path, payload)
    write_json_atomic(export_dir / "latest.json", payload)
    return path


# ------------------------------------------------------------------ official 1-minute candles
def _pf(block: Mapping[str, Any] | None, key: str) -> float | None:
    """Live tier names fields ``close_dollars``; the historical tier ``close``."""
    if not block:
        return None
    v = block.get(f"{key}_dollars", block.get(key))
    return _f(v)


def parse_official(ticker: str, payload: Mapping[str, Any]) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for c in payload.get("candlesticks", []) or []:
        price = c.get("price") or {}
        rows.append(
            {
                "ticker": ticker,
                "end_ts": int(c["end_period_ts"]),
                "o": _pf(price, "open"),
                "h": _pf(price, "high"),
                "l": _pf(price, "low"),
                "c": _pf(price, "close"),
                "volume": _f(c.get("volume_fp", c.get("volume"))),
                "open_interest": _f(c.get("open_interest_fp", c.get("open_interest"))),
                "yes_bid_close": _pf(c.get("yes_bid"), "close"),
                "yes_ask_close": _pf(c.get("yes_ask"), "close"),
            }
        )
    return pl.DataFrame(rows, schema=OFFICIAL_SCHEMA)


def minute_rollup(t: pl.DataFrame) -> pl.DataFrame:
    """Our trades as Kalshi 1-minute periods: ``end_ts`` is the inclusive period end, so a trade at
    second s belongs to ``ceil(s / 60) * 60``."""
    if t.is_empty():
        return pl.DataFrame(
            schema={
                "ticker": pl.Utf8,
                "end_ts": pl.Int64,
                "o": pl.Float64,
                "h": pl.Float64,
                "l": pl.Float64,
                "c": pl.Float64,
                "volume": pl.Float64,
            }
        )
    return (
        t.unique(subset=["trade_id"], keep="first")
        .sort("ticker", "created_time", "trade_id")
        .with_columns(
            (((pl.col("created_time").dt.epoch("us") + 59_999_999) // 60_000_000) * 60).alias(
                "end_ts"
            )
        )
        .group_by("ticker", "end_ts", maintain_order=True)
        .agg(
            pl.col("yes_price").first().alias("o"),
            pl.col("yes_price").max().alias("h"),
            pl.col("yes_price").min().alias("l"),
            pl.col("yes_price").last().alias("c"),
            pl.col("count").sum().alias("volume"),
        )
    )


def reconcile(official: pl.DataFrame, ours: pl.DataFrame, since_ts: int) -> dict[str, Any]:
    """Minutes in which Kalshi shows a trade price, from ``since_ts`` on: do our trade-built
    OHLC and volume agree? Counts are minutes (ticker x minute); no statistical claim is made."""
    off = official.filter((pl.col("end_ts") - 60 >= since_ts) & pl.col("c").is_not_null())
    mine = ours.filter(pl.col("end_ts") - 60 >= since_ts)
    both = off.join(mine, on=["ticker", "end_ts"], how="inner", suffix="_ours")
    tol = 1e-6

    def agree(col: str) -> int:
        hit = both.select(((pl.col(col) - pl.col(f"{col}_ours")).abs() <= tol).sum()).item()
        return int(hit or 0)

    n = both.height
    ours_only = mine.join(
        off.select("ticker", "end_ts"), on=["ticker", "end_ts"], how="anti"
    ).height
    return {
        "official_traded_minutes": off.height,
        "compared_minutes": n,
        "official_minutes_missing_ours": off.height - n,
        "ours_minutes_missing_official": ours_only,
        "agree_open": agree("o") if n else 0,
        "agree_high": agree("h") if n else 0,
        "agree_low": agree("l") if n else 0,
        "agree_close": agree("c") if n else 0,
        "volume_official": float(both["volume"].sum() or 0.0) if n else 0.0,
        "volume_ours": float(both["volume_ours"].sum() or 0.0) if n else 0.0,
        "note": "counts of ticker-minutes; official = GET .../candlesticks period_interval=1",
    }


def finalize_day(
    client: KalshiClient,
    store: RawStore,
    day: date,
    cfg: PollConfig,
    window_start: datetime,
    now: datetime,
    budget: RequestBudget,
    *,
    export_dir: Path | None,
) -> dict[str, Any]:
    """Pull official 1-minute candles once per reconcile ticker (write-once raw; a re-run fetches
    only what is missing), derive the 15 s tables, reconcile, and write the final export."""
    q = store.load("quotes", QUOTE_SCHEMA)
    t = store.load("trades", TRADE_SCHEMA)
    tickers = sorted(
        set(q.filter(pl.col("series").is_in(list(cfg.reconcile_series)))["ticker"].to_list())
    )
    raw_dir = store.day_dir / "official_1m"
    raw_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    frames: list[pl.DataFrame] = []
    for tk in tickers:
        path = raw_dir / f"{tk}.json.gz"
        if path.exists():
            payload = json.loads(gzip.decompress(path.read_bytes()))
        else:
            if not budget.take():
                errors.append(f"{tk}: request budget spent; re-run finalize")
                continue
            try:
                payload = client.fetch_candlesticks(
                    tk,
                    tk.split("-")[0],
                    tier="live",
                    start_ts=int(window_start.timestamp()),
                    end_ts=int(now.timestamp()),
                    period_interval=1,
                )
            except Exception as exc:
                errors.append(f"{tk}: {type(exc).__name__}: {str(exc)[:100]}")
                continue
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(gzip.compress(json.dumps(payload).encode()))
            tmp.rename(path)
        frames.append(parse_official(tk, payload))
    official = pl.concat(frames) if frames else pl.DataFrame(schema=OFFICIAL_SCHEMA)
    derived = store.day_dir / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    official.write_parquet(derived / "official_1m.parquet")
    trade_candles(t, cfg.bucket_s).write_parquet(derived / "trade_candles_15s.parquet")
    quote_points(q, cfg.bucket_s).write_parquet(derived / "quote_points_15s.parquet")
    recon = reconcile(official, minute_rollup(t), int(window_start.timestamp()))
    recon.update({"tickers": len(tickers), "errors": errors, "built_at": now.isoformat()})
    write_json_atomic(derived / "reconciliation.json", recon)
    if export_dir is not None:
        write_export(
            export_dir, day, q, t, bucket_s=cfg.bucket_s, built_at=now, status="final", recon=recon
        )
    return recon
