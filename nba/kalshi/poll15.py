"""15-second Kalshi NBA price poller for the app's candle chart (public data, display only).

``python -m nba.kalshi.poll15 run --date D`` polls tonight's markets from ``lead_min`` before
the first tip until every dated game market has closed (hard stop: last tip +
``tail_cap_h``). Per poll:

* one ``GET /markets?series_ticker=S&status=open`` page per due series (game winners every 15 s,
  spreads/totals every 60 s): yes bid/ask, last, cumulative volume, open interest, slate date;
* ``GET /markets/trades`` for the few game tickers whose cumulative volume rose (bounded: 8
  tickers x 2 pages a poll, a budget reserve left for the quote polls). The 15 s candles are
  built from these prints united with the live capture's (``data/raw/live_<date>/kalshi_trades``,
  read-only), one row per trade id; the reconciliation reports the coverage of each source.

Read-only by construction: it uses ``KalshiClient`` (plain unauthenticated GETs; no order,
portfolio or credential path exists). Raw is write-once parquet, one file per poll and series
(a crash loses at most the poll in flight; a restart reloads the night and never rewrites a
file). Candles are derived (``nba.kalshi.candles15``). Nothing here is read by an alert, EV or
signal path.

Backoff: a 429, 5xx or transport error opens a cool-down (exponential, jittered, capped) during
which no request is sent; a sliding 60 s request budget and a minimum spacing hold regardless.
Kalshi documents no limit for unauthenticated reads, so these self-limits are the contract
(docs/KALSHI_CANDLES_15S.md).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import re
import sys
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl
import yaml

from nba.ingest.cache import RateLimiter
from nba.kalshi.client import KALSHI_DATA_DIR, KalshiClient

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "configs" / "kalshi_candles.yaml"
DEFAULT_STORE = KALSHI_DATA_DIR / "candles15"
DEFAULT_EXPORT_DIR = ROOT / "data" / "export" / "kalshi_candles15"
#: parent of the live capture's raw trees (``live_<ET date>/kalshi_trades``); read-only here
DEFAULT_CAPTURE_ROOT = ROOT / "data" / "raw"
JOB = "kalshi_candles15"
HEARTBEAT = f"{JOB}.json"
LAUNCH_HEARTBEAT = "kalshi-candles-launch.json"
FINISHED = frozenset({"done", "no_games"})
#: the launcher starts the poller this long before the polling window opens (it waits by itself)
START_EARLY = timedelta(minutes=10)

_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    )
}
_EVENT_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})$")

ROW_KEYS = {"quotes": ["series", "ticker", "fetched_at"], "trades": ["trade_id"]}
QUOTE_SCHEMA: dict[str, Any] = {
    "fetched_at": pl.Datetime("us", "UTC"),  # when the response was received (our clock)
    "request_at": pl.Datetime("us", "UTC"),  # when the request was sent
    "server_date": pl.Utf8,  # HTTP Date header: Kalshi's clock, whole seconds
    "series": pl.Utf8,
    "ticker": pl.Utf8,
    "event_ticker": pl.Utf8,
    "title": pl.Utf8,
    "status": pl.Utf8,
    "yes_bid": pl.Float64,
    "yes_ask": pl.Float64,
    "yes_bid_size": pl.Float64,
    "yes_ask_size": pl.Float64,
    "last": pl.Float64,
    "volume": pl.Float64,  # cumulative contracts
    "open_interest": pl.Float64,
    "market_updated": pl.Utf8,  # Kalshi updated_time (documented as metadata updates, not prices)
}
TRADE_SCHEMA: dict[str, Any] = {
    "fetched_at": pl.Datetime("us", "UTC"),
    "ticker": pl.Utf8,
    "trade_id": pl.Utf8,
    "created_time": pl.Datetime("us", "UTC"),  # Kalshi's own execution time
    "yes_price": pl.Float64,
    "no_price": pl.Float64,
    "count": pl.Float64,
    "taker_side": pl.Utf8,
}


# ------------------------------------------------------------------ config
@dataclass(frozen=True)
class PollConfig:
    series: dict[str, int]
    bucket_s: int = 15
    lead_min: float = 30.0
    tail_cap_h: float = 4.0
    #: quiet polls count only this long after the last tip (no NBA game ends sooner) ...
    quiet_after_tip_min: float = 150.0
    #: ... and the night ends after this many minutes of them, and at least `quiet_polls_to_finish`
    quiet_min: float = 10.0
    quiet_polls_to_finish: int = 3
    min_interval_s: float = 0.5
    max_requests_per_minute: int = 60
    #: own bounded /markets/trades pulls for the game series (the prints behind the candles)
    trades_enabled: bool = True
    trades_series: tuple[str, ...] = ("KXNBAGAME",)
    trades_max_tickers: int = 8
    trades_max_pages: int = 2
    #: trade pulls leave this many budget slots free for the quote polls of the same minute
    trades_budget_reserve: int = 10
    jitter_s: float = 1.0
    backoff_base_s: float = 15.0
    backoff_max_s: float = 240.0
    export_every_polls: int = 4
    export_series: tuple[str, ...] = ("KXNBAGAME",)
    reconcile_series: tuple[str, ...] = ("KXNBAGAME",)
    heartbeat_stale_s: float = 180.0
    #: watchdog: no game-series poll with a slate row for this long (inside the live window) ...
    last_success_max_s: float = 600.0
    #: ... or this many failed polls in a row
    max_consecutive_failures: int = 8

    @property
    def fast_s(self) -> int:
        return min(self.series.values())


def load_config(path: Path = DEFAULT_CONFIG) -> PollConfig:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict) or not isinstance(raw.get("series"), dict) or not raw["series"]:
        raise ValueError(f"{path}: 'series' must be a non-empty mapping series -> seconds")
    return PollConfig(
        series={str(k): int(v) for k, v in raw["series"].items()},
        bucket_s=int(raw.get("bucket_s", 15)),
        lead_min=float(raw.get("lead_min", 30)),
        tail_cap_h=float(raw.get("tail_cap_h", 4)),
        quiet_after_tip_min=float(raw.get("quiet_after_tip_min", 150)),
        quiet_min=float(raw.get("quiet_min", 10)),
        quiet_polls_to_finish=int(raw.get("quiet_polls_to_finish", 3)),
        min_interval_s=float(raw.get("min_interval_s", 0.5)),
        max_requests_per_minute=int(raw.get("max_requests_per_minute", 60)),
        trades_enabled=bool((raw.get("trades") or {}).get("enabled", True)),
        trades_series=tuple((raw.get("trades") or {}).get("series", ["KXNBAGAME"])),
        trades_max_tickers=int((raw.get("trades") or {}).get("max_tickers_per_poll", 8)),
        trades_max_pages=int((raw.get("trades") or {}).get("max_pages", 2)),
        trades_budget_reserve=int((raw.get("trades") or {}).get("budget_reserve", 10)),
        jitter_s=float(raw.get("jitter_s", 1.0)),
        backoff_base_s=float(raw.get("backoff_base_s", 15)),
        backoff_max_s=float(raw.get("backoff_max_s", 240)),
        export_every_polls=int(raw.get("export_every_polls", 4)),
        export_series=tuple(raw.get("export_series", ["KXNBAGAME"])),
        reconcile_series=tuple((raw.get("reconcile") or {}).get("series", ["KXNBAGAME"])),
        heartbeat_stale_s=float(raw.get("heartbeat_stale_s", 180)),
        last_success_max_s=float(raw.get("last_success_max_s", 600)),
        max_consecutive_failures=int(raw.get("max_consecutive_failures", 8)),
    )


# ------------------------------------------------------------------ parsing (pure)
def event_date(event_ticker: str) -> date | None:
    """``KXNBAGAME-26NOV09SASPHX`` -> 2026-11-09 (the ET game date; None for undated tickers)."""
    m = _EVENT_DATE.search(event_ticker)
    if m is None or m[2] not in _MONTHS:
        return None
    try:
        return date(2000 + int(m[1]), _MONTHS[m[2]], int(m[3]))
    except ValueError:
        return None


def _f(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def quote_rows(
    markets: Sequence[Mapping[str, Any]],
    *,
    series: str,
    day: date,
    fetched_at: datetime,
    request_at: datetime,
    server_date: str | None,
) -> list[dict[str, Any]]:
    """One row per open market of the slate date. Prices are dollars (0-1); a one-sided book is
    reported by Kalshi as 0.0000 and kept as is (the spread stays visible)."""
    out: list[dict[str, Any]] = []
    for m in markets:
        if event_date(str(m.get("event_ticker", ""))) != day:
            continue
        out.append(
            {
                "fetched_at": fetched_at,
                "request_at": request_at,
                "server_date": server_date,
                "series": series,
                "ticker": str(m.get("ticker", "")),
                "event_ticker": str(m.get("event_ticker", "")),
                "title": str(m.get("title", "")),
                "status": str(m.get("status", "")),
                "yes_bid": _f(m.get("yes_bid_dollars")),
                "yes_ask": _f(m.get("yes_ask_dollars")),
                "yes_bid_size": _f(m.get("yes_bid_size_fp")),
                "yes_ask_size": _f(m.get("yes_ask_size_fp")),
                "last": _f(m.get("last_price_dollars")),
                "volume": _f(m.get("volume_fp")),
                "open_interest": _f(m.get("open_interest_fp")),
                "market_updated": str(m.get("updated_time", "")) or None,
            }
        )
    return out


def trade_rows(trades: Sequence[Mapping[str, Any]], fetched_at: datetime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in trades:
        created = _ts(t.get("created_time"))
        if created is None or not t.get("trade_id"):
            continue
        out.append(
            {
                "fetched_at": fetched_at,
                "ticker": str(t.get("ticker", "")),
                "trade_id": str(t["trade_id"]),
                "created_time": created,
                "yes_price": _f(t.get("yes_price_dollars")),
                "no_price": _f(t.get("no_price_dollars")),
                "count": _f(t.get("count_fp")),
                "taker_side": str(t.get("taker_outcome_side") or t.get("taker_side") or "") or None,
            }
        )
    return out


# ------------------------------------------------------------------ raw store (write-once)
def write_parquet_durable(df: pl.DataFrame, path: Path) -> None:
    """tmp name, fsync, rename: after a power cut a renamed file is whole or absent."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as fh:
        df.write_parquet(fh, compression="zstd")
        fh.flush()
        os.fsync(fh.fileno())
    tmp.rename(path)


class RawStore:
    """``<root>/<day>/<kind>/<stamp>_<tag>.parquet``; a file is written once (fsync, tmp + rename),
    never replaced. A file that cannot be read back is renamed ``.bad`` and reported, never fatal.
    Derived tables live under ``<root>/<day>/derived/`` and are rebuildable."""

    def __init__(self, root: Path, day: date) -> None:
        self.day_dir = root / day.isoformat()
        self.files = 0
        self.bytes = 0
        self.quarantined: list[str] = []

    def write(
        self, kind: str, at: datetime, tag: str, rows: list[dict[str, Any]], schema: dict[str, Any]
    ) -> Path | None:
        if not rows:
            return None
        directory = self.day_dir / kind
        directory.mkdir(parents=True, exist_ok=True)
        stamp = f"{at:%Y%m%dT%H%M%S}_{at.microsecond:06d}Z_{tag}"
        path = directory / f"{stamp}.parquet"
        n = 0
        while path.exists():  # write-once: never replace
            n += 1
            path = directory / f"{stamp}_{n}.parquet"
        write_parquet_durable(pl.DataFrame(rows, schema=schema), path)
        self.files += 1
        self.bytes += path.stat().st_size
        return path

    def _read(self, f: Path) -> pl.DataFrame | None:
        try:
            return pl.read_parquet(f)
        except Exception:  # truncated or zero-length (power cut, full disk): set it aside
            with contextlib.suppress(OSError):
                f.rename(f.with_name(f.name + ".bad"))
            self.quarantined.append(f.name)
            return None

    def load(self, kind: str, schema: dict[str, Any]) -> pl.DataFrame:
        """Every readable row of a kind: the per-poll files plus the compacted file, de-duplicated
        on the kind's key (a compaction that has not pruned its inputs never double counts)."""
        files = sorted((self.day_dir / kind).glob("*.parquet"))
        compacted = self.day_dir / "compacted" / f"{kind}.parquet"
        if compacted.exists():
            files.insert(0, compacted)
        frames = [df for df in (self._read(f) for f in files) if df is not None]
        if not frames:
            return pl.DataFrame(schema=schema)
        df = pl.concat(frames, how="vertical_relaxed")
        return df.unique(subset=ROW_KEYS[kind], keep="first", maintain_order=True)

    def compact(self, kind: str, schema: dict[str, Any], *, prune: bool = False) -> dict[str, Any]:
        """Merge a kind's per-poll files into ``compacted/<kind>.parquet`` (about 5x smaller for the
        small quote files). Verified by reading it back; per-poll files are deleted only with
        ``prune`` and only after the row count matches."""
        files = sorted((self.day_dir / kind).glob("*.parquet"))
        before = sum(f.stat().st_size for f in files)
        df = self.load(kind, schema)
        out = self.day_dir / "compacted" / f"{kind}.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".tmp")
        with tmp.open("wb") as fh:
            df.write_parquet(fh, compression="zstd")
            fh.flush()
            os.fsync(fh.fileno())
        ok = pl.read_parquet(tmp).height == df.height
        if ok:
            tmp.replace(out)
        else:
            tmp.unlink()
        pruned = 0
        if ok and prune:
            for f in files:
                if f.exists():  # a quarantined file was renamed away by load()
                    f.unlink()
                    pruned += 1
        return {
            "kind": kind,
            "rows": df.height,
            "files": len(files),
            "bytes_before": before,
            "bytes_after": out.stat().st_size if ok else None,
            "verified": ok,
            "pruned_files": pruned,
            "quarantined": list(self.quarantined),
        }


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, default=str))
    tmp.replace(path)


# ------------------------------------------------------------------ request budget
class RequestBudget:
    """Sliding 60 s request cap over HTTP requests actually sent (client retries included)."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = per_minute
        self.clock = clock
        self._stamps: deque[float] = deque()

    def _trim(self, now: float) -> None:
        while self._stamps and now - self._stamps[0] >= 60.0:
            self._stamps.popleft()

    def take(self, reserve: int = 0) -> bool:
        """Reserve one request; False when it would exceed the budget, or when fewer than
        ``reserve`` slots would stay free (low priority: room is left for quote polls)."""
        now = self.clock()
        self._trim(now)
        if len(self._stamps) + reserve >= self.per_minute:
            return False
        self._stamps.append(now)
        return True

    def charge(self, n: int) -> None:
        """Account for requests already sent (a client retry); never refused."""
        now = self.clock()
        self._trim(now)
        self._stamps.extend([now] * max(0, n))


class BudgetExhausted(Exception):
    """The sliding request budget is spent; the poll is skipped, never queued."""


# ------------------------------------------------------------------ poller
@dataclass
class _State:
    polls: int = 0
    errors: int = 0
    n429: int = 0
    consecutive_failures: int = 0
    cooldown_until: datetime | None = None
    quiet: int = 0
    quiet_since: datetime | None = None
    last_success: datetime | None = None
    last_ok_poll: datetime | None = None
    trade_rows: int = 0
    last_error: str = ""
    quote_rows: int = 0
    skipped_budget: int = 0
    last_poll: dict[str, datetime] = field(default_factory=dict)
    volumes: dict[str, float] = field(default_factory=dict)
    pending: dict[str, float] = field(default_factory=dict)  # ticker -> volume jump awaiting a pull
    last_trade_ts: dict[str, int] = field(default_factory=dict)
    seen_ids: dict[str, set[str]] = field(default_factory=dict)


class Poller:
    def __init__(
        self,
        cfg: PollConfig,
        client: KalshiClient,
        day: date,
        tips: Sequence[datetime],
        store: RawStore,
        heartbeat: Path,
        *,
        export_dir: Path | None = None,
        capture_root: Path | None = None,
        now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep_fn: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        budget: RequestBudget | None = None,
    ) -> None:
        self.cfg, self.client, self.day, self.store = cfg, client, day, store
        self.heartbeat, self.export_dir = heartbeat, export_dir
        self.now, self.sleep = now_fn, sleep_fn
        self.rng = rng or random.Random()
        self.budget = budget or RequestBudget(cfg.max_requests_per_minute)
        self.tips = sorted(tips)
        self.st = _State()
        self.start_ts = self.now()
        #: the night's quote rows as polars frames per series (columnar, not dicts)
        self.qframes: dict[str, list[pl.DataFrame]] = {}
        #: this job's own prints (``/markets/trades``), columnar
        self.tframes: list[pl.DataFrame] = []
        self.capture_root = capture_root
        self.capture: Any = None  # candles15.CaptureTrades, created on first use
        self.status = "waiting"
        self.finalize_note = ""
        if self.tips:
            self.window_start = self.tips[0] - timedelta(minutes=cfg.lead_min)
            self.window_end = self.tips[-1] + timedelta(hours=cfg.tail_cap_h)
        else:
            self.window_start = self.window_end = self.start_ts

    # -------------------------------------------------------------- heartbeat / logging
    def beat(self, status: str | None = None) -> None:
        if status:
            self.status = status
        st = self.st
        payload = {
            "job": JOB,
            "date": self.day.isoformat(),
            "status": self.status,
            "start_ts": self.start_ts.isoformat(),
            "last_tick_ts": self.now().isoformat(),
            "last_success_ts": st.last_success.isoformat() if st.last_success else None,
            "last_ok_poll_ts": st.last_ok_poll.isoformat() if st.last_ok_poll else None,
            "trade_rows": st.trade_rows,
            "polls": st.polls,
            "requests": self.client.requests,
            "errors": st.errors,
            "n429": st.n429,
            "consecutive_failures": st.consecutive_failures,
            "cooldown_until": st.cooldown_until.isoformat() if st.cooldown_until else None,
            "quote_rows": st.quote_rows,
            "skipped_budget": st.skipped_budget,
            "raw_files": self.store.files,
            "raw_bytes": self.store.bytes,
            "quarantined": len(self.store.quarantined),
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "last_error": st.last_error,
        }
        with contextlib.suppress(OSError):  # a full disk must not kill the poller
            write_json_atomic(self.heartbeat, payload)

    def log_error(self, what: str) -> None:
        self.st.errors += 1
        self.st.last_error = what[:200]
        with contextlib.suppress(OSError):  # a full disk must not raise inside an except block
            self.store.day_dir.mkdir(parents=True, exist_ok=True)
            with (self.store.day_dir / "errors.log").open("a") as fh:
                fh.write(f"{self.now().isoformat()} {what}\n")

    # -------------------------------------------------------------- requests
    def _call(self, fn: Callable[[], dict[str, Any]], reserve: int = 0) -> dict[str, Any]:
        if not self.budget.take(reserve):
            self.st.skipped_budget += 1
            raise BudgetExhausted
        before = self.client.requests
        try:
            return fn()
        finally:  # the client may have retried: every request it sent counts against the budget
            self.budget.charge(self.client.requests - before - 1)

    def _fail(self, exc: Exception) -> None:
        """Open a jittered exponential cool-down; no request is sent until it ends."""
        st = self.st
        st.consecutive_failures += 1
        code = getattr(getattr(exc, "response", None), "status_code", None)
        if code == 429:
            st.n429 += 1
        self.log_error(f"{type(exc).__name__} http={code}: {str(exc)[:120]}")
        wait = min(
            self.cfg.backoff_max_s, self.cfg.backoff_base_s * 2 ** (st.consecutive_failures - 1)
        )
        wait *= 1.0 + self.rng.uniform(0.0, 0.25)
        st.cooldown_until = self.now() + timedelta(seconds=wait)

    def poll_series(self, series: str) -> list[dict[str, Any]] | None:
        """All open markets of the series for the slate date (None on failure)."""
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(3):
            request_at = self.now()
            try:
                page = self._call(
                    lambda c=cursor: self.client.fetch_markets_page(  # type: ignore[misc]
                        tier="live", series_ticker=series, status="open", limit=1000, cursor=c
                    )
                )
            except BudgetExhausted:
                return None
            except Exception as exc:  # network / HTTP: back off, keep the process alive
                self._fail(exc)
                return None
            fetched_at = self.now()
            markets = [m for m in page.get("markets", []) if isinstance(m, dict)]
            rows += quote_rows(
                markets,
                series=series,
                day=self.day,
                fetched_at=fetched_at,
                request_at=request_at,
                server_date=self.client.last_server_date,
            )
            cursor = str(page.get("cursor") or "") or None
            if not cursor:
                break
        self.st.consecutive_failures = 0
        return rows

    # -------------------------------------------------------------- one tick
    def due(self, series: str, now: datetime) -> bool:
        last = self.st.last_poll.get(series)
        if last is None:
            return True
        every = self.cfg.series[series]
        # a tick may start early by the jitter: never skip a slot for it
        return (now - last).total_seconds() >= max(every * 0.5, every - self.cfg.jitter_s - 1.0)

    def _note_quiet(self, now: datetime, rows: list[dict[str, Any]]) -> None:
        """Empty game-series lists count as "quiet" only once no game can still be on."""
        st = self.st
        if rows:
            st.last_success = now
            st.quiet, st.quiet_since = 0, None
            return
        if now >= self.tips[-1] + timedelta(minutes=self.cfg.quiet_after_tip_min):
            st.quiet += 1
            st.quiet_since = st.quiet_since or now

    def tick(self) -> None:
        now = self.now()
        st = self.st
        game_series = next(iter(self.cfg.series))
        for series in self.cfg.series:
            if not self.due(series, now):
                continue
            if st.cooldown_until and self.now() < st.cooldown_until:
                break
            rows = self.poll_series(series)
            st.last_poll[series] = now
            if rows is None:
                continue
            if series == game_series:
                st.last_ok_poll = now  # any successful game poll, empty or not (watchdog)
                self._note_quiet(now, rows)
            if rows:
                self.store.write("quotes", self.now(), series, rows, QUOTE_SCHEMA)
                self.qframes.setdefault(series, []).append(pl.DataFrame(rows, schema=QUOTE_SCHEMA))
                st.quote_rows += len(rows)
            if self.cfg.trades_enabled and series in self.cfg.trades_series and rows:
                self.pull_trades(rows)
        st.polls += 1
        if self.cfg.export_every_polls and st.polls % self.cfg.export_every_polls == 0:
            self.export()

    # -------------------------------------------------------------- own prints
    def pull_trades(self, rows: list[dict[str, Any]]) -> None:
        """Prints for the game tickers whose cumulative volume rose (largest jump first, at most
        ``trades_max_tickers`` a poll, two pages each, budget reserve left for the quote polls)."""
        st = self.st
        for r in rows:
            vol = r["volume"]
            if vol is None:
                continue
            prev = st.volumes.get(r["ticker"])
            if prev is not None and vol > prev:
                st.pending[r["ticker"]] = st.pending.get(r["ticker"], 0.0) + (vol - prev)
            st.volumes[r["ticker"]] = max(vol, prev or 0.0)
        order = sorted(st.pending, key=lambda t: (-st.pending[t], t))[: self.cfg.trades_max_tickers]
        batch: list[dict[str, Any]] = []
        done: list[str] = []
        try:
            for ticker in order:
                if st.cooldown_until and self.now() < st.cooldown_until:
                    break
                got, complete = self._pull_ticker_trades(ticker)
                batch += got
                if complete:
                    done.append(ticker)
        finally:  # one file per poll; ids are remembered only after the write
            if batch:
                fetched_at = self.now()
                self.store.write("trades", fetched_at, "poll", batch, TRADE_SCHEMA)
                self.tframes.append(pl.DataFrame(batch, schema=TRADE_SCHEMA))
                st.trade_rows += len(batch)
                self._remember_trades(batch)
            for ticker in done:
                st.pending.pop(ticker, None)

    def _pull_ticker_trades(self, ticker: str) -> tuple[list[dict[str, Any]], bool]:
        """(new prints, complete). Incomplete = a page cap or a failed/denied request: the prints
        older than the newest fetched one may be missing, which is logged, never papered over."""
        st = self.st
        min_ts = st.last_trade_ts.get(ticker, int(self.window_start.timestamp()))
        seen = st.seen_ids.get(ticker, set())
        new: list[Mapping[str, Any]] = []
        cursor: str | None = None
        complete = True
        for page_n in range(self.cfg.trades_max_pages):
            try:
                page = self._call(
                    lambda c=cursor: self.client.fetch_trades_page(  # type: ignore[misc]
                        ticker, min_ts=min_ts, cursor=c
                    ),
                    reserve=self.cfg.trades_budget_reserve,
                )
            except BudgetExhausted:
                return [], False
            except Exception as exc:
                self._fail(exc)
                return [], False
            new += [t for t in page.get("trades", []) if t.get("trade_id") not in seen]
            cursor = str(page.get("cursor") or "") or None
            if not cursor:
                break
            if page_n == self.cfg.trades_max_pages - 1:
                complete = False
                self.log_error(
                    f"trades {ticker}: page cap hit, older prints than the fetched ones are missing"
                )
        return trade_rows(new, self.now()), complete

    def _remember_trades(self, rows: Sequence[Mapping[str, Any]]) -> None:
        for r in rows:
            t = r["ticker"]
            self.st.seen_ids.setdefault(t, set()).add(r["trade_id"])
            ts = int(r["created_time"].timestamp())
            self.st.last_trade_ts[t] = max(self.st.last_trade_ts.get(t, 0), ts)

    def own_trades(self) -> pl.DataFrame:
        return pl.concat(self.tframes) if self.tframes else pl.DataFrame(schema=TRADE_SCHEMA)

    def all_trades(self) -> pl.DataFrame:
        """Own prints united with the live capture's, one row per trade id (own first)."""
        frames = [self.own_trades(), self.capture_trades()]
        return pl.concat(
            [f for f in frames if not f.is_empty()] or [pl.DataFrame(schema=TRADE_SCHEMA)]
        ).unique(subset=["trade_id"], keep="first", maintain_order=True)

    def _quotes(self, series: Sequence[str]) -> pl.DataFrame:
        frames = [f for s in series for f in self.qframes.get(s, [])]
        return pl.concat(frames) if frames else pl.DataFrame(schema=QUOTE_SCHEMA)

    def capture_trades(self) -> pl.DataFrame:
        """The live capture's KXNBAGAME prints for the slate date (read-only, incremental)."""
        from nba.kalshi import candles15

        if self.capture is None:
            root = self.capture_root or DEFAULT_CAPTURE_ROOT
            self.capture = candles15.CaptureTrades(root, self.day)
        self.capture.refresh()
        out: pl.DataFrame = self.capture.frame()
        return out

    def export(self, final: bool = False) -> None:
        """Rebuild the display export from the night's rows. Display only: a failure here is logged
        and never stops the polling."""
        if self.export_dir is None:
            return
        try:
            from nba.kalshi import candles15

            candles15.write_export(
                self.export_dir,
                self.day,
                self._quotes(self.cfg.export_series),
                self.all_trades(),
                bucket_s=self.cfg.bucket_s,
                built_at=self.now(),
                status="final" if final else "live",
                recon=self.read_reconciliation(),
                lines_q=self._quotes([s for s in self.qframes if s not in self.cfg.export_series])
                if final
                else None,
            )
        except Exception as exc:
            self.log_error(f"export failed: {type(exc).__name__}: {str(exc)[:120]}")

    def read_reconciliation(self) -> dict[str, Any] | None:
        path = self.store.day_dir / "derived" / "reconciliation.json"
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    # -------------------------------------------------------------- resume
    def restore(self) -> None:
        """Reload the night so a restart never rewrites a file. Unreadable files are quarantined
        (renamed ``.bad``) and logged; the rest of the night is kept."""
        q = self.store.load("quotes", QUOTE_SCHEMA)
        self.qframes = {}
        for (series,), part in q.partition_by("series", as_dict=True).items():
            self.qframes[str(series)] = [part]
        self.st.quote_rows = q.height
        for tk, vol in q.group_by("ticker").agg(pl.col("volume").max()).iter_rows():
            if vol is not None:
                self.st.volumes[str(tk)] = float(vol)
        t = self.store.load("trades", TRADE_SCHEMA)
        self.tframes = [t] if not t.is_empty() else []
        self.st.trade_rows = t.height
        self._remember_trades(t.to_dicts())
        for name in self.store.quarantined:
            self.log_error(f"quarantined unreadable raw file {name} (renamed .bad)")

    # -------------------------------------------------------------- main loop
    def finished(self, now: datetime) -> str | None:
        if now > self.window_end:
            return "window cap"
        qs = self.st.quiet_since
        if (
            qs is not None
            and self.st.quiet >= self.cfg.quiet_polls_to_finish
            and (now - qs).total_seconds() >= self.cfg.quiet_min * 60
        ):
            return "no open game market left"
        return None

    def run(
        self, *, once: bool = False, start_now: bool = False, max_polls: int | None = None
    ) -> int:
        if not self.tips:
            self.beat("no_games")
            return 0
        self.restore()
        while not start_now and self.now() < self.window_start and not once:
            self.beat("waiting")
            self.sleep(min(30.0, max(1.0, (self.window_start - self.now()).total_seconds())))
        self.beat("running")
        slot = self.now()
        while True:
            now = self.now()
            why = self.finished(now)
            if why:
                self.finalize(why)
                return 0
            if self.st.cooldown_until and now < self.st.cooldown_until:
                self.beat("backoff")
                self.sleep(min(5.0, (self.st.cooldown_until - now).total_seconds() + 0.1))
                continue
            try:
                self.tick()
            except Exception as exc:  # a bad tick must not end the night
                self.log_error(f"tick {type(exc).__name__}: {str(exc)[:160]}")
            self.beat("running")
            if once or (max_polls is not None and self.st.polls >= max_polls):
                return 0
            slot += timedelta(seconds=self.cfg.fast_s)
            after = self.now()
            if slot < after:  # overran: skip missed slots, never burst to catch up
                slot = after + timedelta(seconds=self.cfg.fast_s)
            wait = (slot - after).total_seconds() + self.rng.uniform(0.0, self.cfg.jitter_s)
            self.sleep(max(0.0, wait))

    def finalize(self, why: str) -> None:
        """End of night: official 1-minute candles and reconciliation, final export, compaction
        (about 3 files a night instead of about 2,300), final beat. Beats between steps."""
        self.beat("finalizing")
        try:
            from nba.kalshi import candles15

            own, cap = self.own_trades(), self.capture_trades()
            trades = self.all_trades()
            candles15.finalize_day(
                self.client,
                self.store,
                self.day,
                self.cfg,
                self.window_start,
                self.now(),
                self.budget,
                trades=trades,
                sources={"own": own, "capture": cap},
                capture=self.capture,
                export_dir=None,
                beat=self.beat,
            )
        except Exception as exc:
            self.log_error(f"finalize failed: {type(exc).__name__}: {str(exc)[:160]}")
        self.export(final=True)  # after the reconciliation so the file carries it
        try:
            self.store.compact("quotes", QUOTE_SCHEMA, prune=True)
            self.store.compact("trades", TRADE_SCHEMA, prune=True)
        except Exception as exc:
            self.log_error(f"compaction failed: {type(exc).__name__}: {str(exc)[:160]}")
        self.finalize_note = why
        self.beat("done")


# ------------------------------------------------------------------ plan / watchdog (pure)
def plan(
    now: datetime,
    tips: Sequence[datetime],
    hb: Mapping[str, Any] | None,
    day: date,
    cfg: PollConfig,
) -> tuple[str, str]:
    """``(decision, detail)``: no_games | over | done | early | start (the launcher's lock decides
    whether a run is already going)."""
    if not tips:
        return "no_games", f"no regular-season game on {day}"
    first, last = min(tips), max(tips)
    end = last + timedelta(hours=cfg.tail_cap_h)
    if now > end:
        return "over", f"window ended {end:%H:%MZ}"
    if hb and hb.get("date") == day.isoformat() and hb.get("status") in FINISHED:
        return "done", f"poller {hb.get('status')} for {day}"
    opens = first - timedelta(minutes=cfg.lead_min) - START_EARLY
    if now < opens:
        return "early", f"start at {opens:%H:%MZ} (first tip {first:%H:%MZ})"
    return "start", f"first tip {first:%H:%MZ}, window to {end:%H:%MZ}"


def problem(
    now: datetime,
    tips: Sequence[datetime],
    hb: Mapping[str, Any] | None,
    hb_time: datetime | None,
    day: date,
    cfg: PollConfig,
) -> str | None:
    """Why the poller is not doing its job inside its window, or None. For the independent
    watchdog (``nba.ops.watchdog``): "a job that cannot start cannot alert". A fresh heartbeat is
    not enough: a poller in permanent backoff, or one getting empty 200s, beats too."""
    if not tips:
        return None
    first, last = min(tips), max(tips)
    opens = first - timedelta(minutes=cfg.lead_min)
    closes = last + timedelta(hours=cfg.tail_cap_h)
    if not (opens + timedelta(minutes=10) <= now <= closes):
        return None
    if hb is None or hb.get("date") != day.isoformat():
        seen = "no heartbeat" if hb is None else f"last heartbeat is for {hb.get('date')}"
        return f"{seen} (window opened {opens:%H:%MZ})"
    if hb.get("status") in FINISHED:
        return None
    if hb_time is None or (now - hb_time).total_seconds() > cfg.heartbeat_stale_s:
        return f"heartbeat stale, status {hb.get('status')}"
    fails = int(hb.get("consecutive_failures") or 0)
    if fails >= cfg.max_consecutive_failures:
        return f"{fails} failed polls in a row ({hb.get('last_error') or 'no error text'})"
    # while a game can still be on, the game series must keep returning slate rows
    if first <= now <= last + timedelta(minutes=cfg.quiet_after_tip_min):
        ok = _ts(hb.get("last_success_ts"))
        # once a slate row has been seen, a successful empty poll (a short night: the last market
        # closed early) is a poller that is fine; before that, the night's start is the reference
        since = (_ts(hb.get("last_ok_poll_ts")) or ok) if ok else _ts(hb.get("start_ts"))
        since = since or now
        if (now - since).total_seconds() > cfg.last_success_max_s:
            what = "no slate row yet" if ok is None else f"last slate row {since:%H:%MZ}"
            mins = int((now - since).total_seconds() // 60)
            return f"no successful game poll for {mins} min ({what})"
    return None


# ------------------------------------------------------------------ CLI
def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _tips(data_dir: Path, day: date) -> list[datetime]:
    from nba.ops.watchdog import todays_tips

    return list(todays_tips(data_dir, day).values())


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.kalshi.poll15", description=__doc__)
    ap.add_argument("cmd", choices=["run", "plan", "finalize", "export", "compact"])
    ap.add_argument(
        "--date", type=date.fromisoformat, default=None, help="ET slate date (default today)"
    )
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--store", type=Path, default=DEFAULT_STORE, help="raw + derived root")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--export-dir", type=Path, default=DEFAULT_EXPORT_DIR)
    ap.add_argument(
        "--capture-root",
        type=Path,
        default=DEFAULT_CAPTURE_ROOT,
        help="parent of live_<date>/ (the live capture's raw trees; read-only)",
    )
    ap.add_argument("--heartbeat", type=Path, default=None)
    ap.add_argument("--now", default=None, help="ISO time (plan tests)")
    ap.add_argument("--once", action="store_true", help="one tick now, no waiting (smoke test)")
    ap.add_argument(
        "--prune", action="store_true", help="compact: delete per-poll files once verified"
    )
    ap.add_argument("--max-polls", type=int, default=None, help="stop after N polls (dry run)")
    ap.add_argument("--start-now", action="store_true", help="do not wait for the window start")
    ap.add_argument(
        "--tip",
        action="append",
        default=[],
        help="UTC tip ISO time; bypasses the schedule (dry run)",
    )
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    day = a.date or datetime.now(ET).date()
    hb_path = a.heartbeat or a.data_dir / "ops" / "heartbeat" / HEARTBEAT

    try:
        tips = [t for t in (_ts(x) for x in a.tip) if t] or _tips(a.data_dir, day)
    except Exception as exc:  # unreadable schedule
        if a.cmd == "plan":
            print(f"unknown {type(exc).__name__}: {exc}")
            return 0
        print(f"schedule unreadable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if a.cmd == "plan":
        now = _ts(a.now) if a.now else datetime.now(UTC)
        assert now is not None
        decision, detail = plan(now, tips, _read_json(hb_path), day, cfg)
        print(f"{decision} {detail}")
        return 0

    limiter = RateLimiter(min_interval_s=cfg.min_interval_s)
    client = KalshiClient(rate_limiter=limiter, max_attempts=2, timeout_s=10.0)
    store = RawStore(a.store, day)
    if a.cmd == "compact":
        for kind, schema in (("quotes", QUOTE_SCHEMA), ("trades", TRADE_SCHEMA)):
            print(json.dumps(store.compact(kind, schema, prune=a.prune), sort_keys=True))
        return 0
    poller = Poller(
        cfg,
        client,
        day,
        tips,
        store,
        hb_path,
        export_dir=a.export_dir,
        capture_root=a.capture_root,
    )
    if a.cmd == "run":
        return poller.run(once=a.once, start_now=a.start_now, max_polls=a.max_polls)
    poller.restore()
    if a.cmd == "finalize":
        poller.finalize("manual")
    else:
        poller.export(final=True)
    return 0


if __name__ == "__main__":
    with contextlib.suppress(BrokenPipeError):
        sys.exit(main())
