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
import os
import time
from collections.abc import Callable, Mapping, Sequence
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
    trade_rows,
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


def _by_ticker(df: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """One pass over the frame (not one filter per ticker)."""
    if df.is_empty():
        return {}
    parts = df.partition_by("ticker", as_dict=True)
    return {str(k[0] if isinstance(k, tuple) else k): v for k, v in parts.items()}


def build_export(
    q: pl.DataFrame,
    t: pl.DataFrame,
    *,
    day: date,
    bucket_s: int,
    built_at: datetime,
    status: str,
    recon: Mapping[str, Any] | None = None,
    part: str = "game",
) -> dict[str, Any]:
    """The ``kalshi_candles15`` v1 payload (docs/contracts/kalshi_candles15_v1.md). ``part`` is
    ``game`` (game winners: quotes and trade candles) or ``lines`` (spreads and totals: quotes)."""
    pts_by = _by_ticker(quote_points(q, bucket_s))
    cds_by = _by_ticker(trade_candles(t, bucket_s)) if part == "game" else {}
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
        .sort("ticker")
        if not q.is_empty()
        else pl.DataFrame()
    )
    empty_p = quote_points(q.clear(), bucket_s)
    empty_c = trade_candles(t.clear(), bucket_s)
    markets: list[dict[str, Any]] = []
    for m in meta.iter_rows(named=True) if not meta.is_empty() else []:
        tk = m["ticker"]
        p = pts_by.get(tk, empty_p)
        c = cds_by.get(tk, empty_c)
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
        "part": part,
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
            "Candles bucket on Kalshi's trade time, quotes on our receive time: two clocks.",
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
    lines_q: pl.DataFrame | None = None,
) -> Path:
    """Atomic. ``latest.json`` always (game winners only: quotes plus trade candles);
    ``<day>.json`` and, from ``lines_q``, ``<day>_lines.json`` (spreads and totals) once final."""
    payload = build_export(
        q, t, day=day, bucket_s=bucket_s, built_at=built_at, status=status, recon=recon
    )
    path = export_dir / f"{day.isoformat()}.json"
    if status != "live":  # the dated file is the finished night; live builds refresh latest only
        write_json_atomic(path, payload)
        if lines_q is not None and not lines_q.is_empty():
            lines = build_export(
                lines_q,
                t.clear(),
                day=day,
                bucket_s=bucket_s,
                built_at=built_at,
                status=status,
                part="lines",
            )
            write_json_atomic(export_dir / f"{day.isoformat()}_lines.json", lines)
    write_json_atomic(export_dir / "latest.json", payload)
    return path


class CaptureTrades:
    """Read-only, incremental reader of the live capture's raw trade store
    (``<root>/live_<ET date>/kalshi_trades/*/*.json.gz``: ``{ticker: [trade, ...]}`` per file).
    The capture is the single source of the prints; this job requests none of its own. Files that
    cannot be read are listed in ``bad`` and skipped, never fatal."""

    PREFIX = "KXNBAGAME-"

    def __init__(self, raw_root: Path, day: date) -> None:
        self.dir = raw_root / f"live_{day.isoformat()}" / "kalshi_trades"
        self.seen: set[str] = set()
        self.frames: list[pl.DataFrame] = []
        self.bad: list[str] = []
        self.files = 0

    def refresh(self) -> int:
        """Read files not read before; returns the number of new prints parsed."""
        new = 0
        for f in sorted(self.dir.glob("*/*.json.gz")):
            if str(f) in self.seen:
                continue
            self.seen.add(str(f))
            try:
                payload = json.loads(gzip.decompress(f.read_bytes()))
            except (OSError, EOFError, ValueError):
                self.bad.append(f.name)
                continue
            if not isinstance(payload, dict):
                self.bad.append(f.name)
                continue
            try:
                stamp = datetime.strptime(f.name[:15], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
            except ValueError:
                stamp = datetime.fromtimestamp(f.stat().st_mtime, UTC)
            rows: list[dict[str, Any]] = []
            for ticker, trades in payload.items():
                if str(ticker).startswith(self.PREFIX) and isinstance(trades, list):
                    rows += trade_rows([t for t in trades if isinstance(t, dict)], stamp)
            self.files += 1
            if rows:
                self.frames.append(trades_frame(rows))
                new += len(rows)
        return new

    def frame(self) -> pl.DataFrame:
        if not self.frames:
            return pl.DataFrame(schema=TRADE_SCHEMA)
        return pl.concat(self.frames).unique(subset=["trade_id"], keep="first", maintain_order=True)


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
        "volume_official_all": float(off["volume"].sum() or 0.0),
        "minute_coverage": (n / off.height) if off.height else None,
        "volume_coverage": (
            float(both["volume_ours"].sum() or 0.0) / float(off["volume"].sum())
            if n and off["volume"].sum()
            else (0.0 if off.height else None)
        ),
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
    trades: pl.DataFrame,
    capture: CaptureTrades | None = None,
    export_dir: Path | None = None,
    beat: Callable[[], object] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Pull official 1-minute candles once per reconcile ticker (write-once, fsynced raw; a re-run
    fetches only what is missing; waits for the request budget rather than skipping), derive the
    15 s tables, and reconcile the capture's prints against them. ``beat`` runs between tickers
    so the heartbeat stays fresh through a slow network."""
    q = store.load("quotes", QUOTE_SCHEMA)
    prefixes = tuple(f"{s}-" for s in cfg.reconcile_series)
    tickers = sorted(
        {
            tk
            for tk in [*q["ticker"].to_list(), *trades["ticker"].to_list()]
            if tk.startswith(prefixes)
        }
    )
    raw_dir = store.day_dir / "official_1m"
    raw_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    frames: list[pl.DataFrame] = []
    for tk in tickers:
        if beat is not None:
            beat()
        path = raw_dir / f"{tk}.json.gz"
        if path.exists():
            payload = json.loads(gzip.decompress(path.read_bytes()))
        else:
            for _ in range(120):  # wait for the sliding budget (1 s steps), never exceed it
                if budget.take():
                    break
                sleep(1.0)
                if beat is not None:
                    beat()
            else:
                errors.append(f"{tk}: request budget not freed in 2 min; re-run finalize")
                continue
            before = client.requests
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
            finally:
                budget.charge(client.requests - before - 1)
            tmp = path.with_name(path.name + ".tmp")
            with tmp.open("wb") as fh:
                fh.write(gzip.compress(json.dumps(payload).encode()))
                fh.flush()
                os.fsync(fh.fileno())
            tmp.rename(path)
        frames.append(parse_official(tk, payload))
    official = pl.concat(frames) if frames else pl.DataFrame(schema=OFFICIAL_SCHEMA)
    derived = store.day_dir / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    official.write_parquet(derived / "official_1m.parquet")
    trade_candles(trades, cfg.bucket_s).write_parquet(derived / "trade_candles_15s.parquet")
    quote_points(q, cfg.bucket_s).write_parquet(derived / "quote_points_15s.parquet")
    recon = reconcile(official, minute_rollup(trades), int(window_start.timestamp()))
    ours_v, off_v = recon["volume_ours"], recon["volume_official"]
    recon.update(
        {
            "tickers": len(tickers),
            "errors": errors,
            "built_at": now.isoformat(),
            "trade_source": "live capture raw store (data/raw/live_<date>/kalshi_trades)",
            "capture_files": capture.files if capture else None,
            "capture_files_unreadable": list(capture.bad) if capture else [],
            "capture_prints": trades.height,
            "volume_ratio_on_compared_minutes": (ours_v / off_v) if off_v else None,
        }
    )
    write_json_atomic(derived / "reconciliation.json", recon)
    if export_dir is not None:
        write_export(
            export_dir,
            day,
            q,
            trades,
            bucket_s=cfg.bucket_s,
            built_at=now,
            status="final",
            recon=recon,
        )
    return recon
