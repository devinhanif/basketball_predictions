"""One pre-tip capture of the day's NBA odds slate into the as-of market store.

Calls ``/odds/`` once (h2h, spreads, totals together) and ``/props/`` once (the four prop markets),
caches both raw responses first, parses them, and appends rows to the table ``odds_snapshots`` in
``data/markets/market_asof.duckdb`` (the file ``nba.markets.capture`` writes
``market_at_prediction`` to; no existing table is touched). ``--dry-run`` still calls the API and
caches the raw responses but writes nothing to DuckDB.

Idempotent: a row is skipped when the table already holds the same (captured minute, event, book,
market, outcome, point). A rerun inside the client's ``reuse_window_s`` reuses the cached response
(same ``captured_at``), so it costs no request and appends nothing.

Missingness is data: an empty / out-of-season feed is reported with its message and exits 0.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

from nba.odds.theoddsapi import (
    NameResolver,
    OddsError,
    OddsKeyMissingError,
    RawResponse,
    TheOddsApiClient,
    load_config,
    parse_payload,
    read_envelope,
)

log = logging.getLogger("nba.odds")
ET = ZoneInfo("America/New_York")

DDL = """
CREATE TABLE IF NOT EXISTS odds_snapshots (
    source VARCHAR,
    captured_at TIMESTAMP,        -- our clock at fetch (naive UTC)
    capture_minute TIMESTAMP,     -- captured_at truncated to the minute (idempotency key part)
    source_updated_at TIMESTAMP,  -- the book's own update stamp when the feed gives one
    slate_date DATE,              -- ET date of the slate this capture was requested for
    book VARCHAR,
    event_id VARCHAR,
    home_team VARCHAR,
    away_team VARCHAR,
    home_team_id INT,             -- NULL when the name did not resolve (never guessed)
    away_team_id INT,
    start_time TIMESTAMP,         -- scheduled tip (naive UTC)
    market VARCHAR,
    outcome_name VARCHAR,
    player_name VARCHAR,
    player_id INT,
    side VARCHAR,                 -- over | under | home | away | team name
    outcome_team_id INT,
    point DOUBLE,
    price_american INT,
    implied_prob_raw DOUBLE,      -- vigged
    implied_prob DOUBLE,          -- proportional de-vig within a two-sided market, else NULL
    raw_file VARCHAR              -- cached raw response this row was parsed from
);
"""
_KEY_COLS = ("capture_minute", "event_id", "book", "market", "outcome_name", "point")
_INSERT_COLS = [
    "source", "captured_at", "capture_minute", "source_updated_at", "slate_date", "book",
    "event_id", "home_team", "away_team", "home_team_id", "away_team_id", "start_time", "market",
    "outcome_name", "player_name", "player_id", "side", "outcome_team_id", "point",
    "price_american", "implied_prob_raw", "implied_prob", "raw_file",
]  # fmt: skip


def ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(DDL)


def slate_window_utc(slate: date) -> tuple[datetime, datetime]:
    """[ET midnight of ``slate``, ET midnight of the next day) as aware UTC datetimes."""
    start = datetime.combine(slate, dtime(0, 0), tzinfo=ET)
    end = datetime.combine(slate + timedelta(days=1), dtime(0, 0), tzinfo=ET)
    return start.astimezone(UTC), end.astimezone(UTC)


def _z(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_rows(
    con: duckdb.DuckDBPyConnection, rows: pl.DataFrame, slate: date, raw_file: str
) -> int:
    """Append parsed rows not already stored; returns the number inserted."""
    ensure_table(con)
    if rows.height == 0:
        return 0
    frame = rows.with_columns(
        capture_minute=pl.col("captured_at").dt.truncate("1m"),
        slate_date=pl.lit(slate, dtype=pl.Date),
        raw_file=pl.lit(raw_file),
    ).select(_INSERT_COLS)
    frame = frame.unique(subset=list(_KEY_COLS), keep="first", maintain_order=True)
    con.register("odds_new", frame.to_arrow())
    try:
        on = " AND ".join(f"o.{c} IS NOT DISTINCT FROM n.{c}" for c in _KEY_COLS)
        before = con.execute("SELECT count(*) FROM odds_snapshots").fetchone()
        cols = ", ".join(f"n.{c}" for c in _INSERT_COLS)
        con.execute(
            f"INSERT INTO odds_snapshots SELECT {cols} FROM odds_new n "
            f"WHERE NOT EXISTS (SELECT 1 FROM odds_snapshots o WHERE {on})"
        )
        after = con.execute("SELECT count(*) FROM odds_snapshots").fetchone()
    finally:
        con.unregister("odds_new")
    assert before is not None and after is not None
    return int(after[0]) - int(before[0])


def open_store(path: Path, retries: int = 6, wait_s: float = 5.0) -> duckdb.DuckDBPyConnection:
    """Writable connection to the as-of market file (single writer: retry while it is locked)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for _ in range(max(1, retries)):
        try:
            return duckdb.connect(str(path))
        except duckdb.IOException as exc:
            last = exc
            time.sleep(wait_s)
    assert last is not None
    raise last


@dataclass
class CaptureReport:
    slate: date
    dry_run: bool
    requests_used: int = 0
    game_rows: int = 0
    prop_rows: int = 0
    inserted: int = 0
    reasons: Counter[str] = field(default_factory=Counter)
    unresolved: Counter[str] = field(default_factory=Counter)
    messages: list[str] = field(default_factory=list)
    unreachable: bool = False
    store_failed: bool = False
    raw_files: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return self.game_rows + self.prop_rows == 0

    def line(self) -> str:
        rs = ", ".join(f"{k}={v}" for k, v in sorted(self.reasons.items())) or "none"
        return (
            f"odds capture {self.slate} dry_run={self.dry_run}: requests={self.requests_used} "
            f"game_rows={self.game_rows} prop_rows={self.prop_rows} inserted={self.inserted} "
            f"reasons[{rs}]"
        )


def _ingest_one(
    resp: RawResponse,
    kind: str,
    window: tuple[datetime, datetime],
    resolver: NameResolver,
    report: CaptureReport,
) -> pl.DataFrame:
    env = read_envelope(resp.body)
    if env.empty:
        report.messages.append(f"{kind}: no data ({env.message or 'empty list'})")
    res = parse_payload(resp.body, resp.fetched_at, resolver)
    report.reasons.update(res.reasons)
    report.unresolved.update(res.unresolved)
    lo, hi = (w.astimezone(UTC).replace(tzinfo=None) for w in window)
    rows = res.rows
    if kind == "props" and rows.height:  # /props/ has no date filter: keep the slate's events
        before = rows.height
        rows = rows.filter(pl.col("start_time").is_between(lo, hi, closed="left"))
        if before - rows.height:
            report.reasons["outside_slate_window"] += before - rows.height
    if resp.path is not None:
        report.raw_files.append(resp.path.name)
    return rows


def run_capture(
    slate: date,
    *,
    dry_run: bool = False,
    client: TheOddsApiClient | None = None,
    out_db: Path | None = None,
    resolver: NameResolver | None = None,
) -> CaptureReport:
    """Fetch, cache, parse and (unless ``dry_run``) append one slate. Raises only the key error."""
    cli = client or TheOddsApiClient()
    cfg = cli.config
    report = CaptureReport(slate=slate, dry_run=dry_run)
    res = resolver or NameResolver.default()
    window = slate_window_utc(slate)
    start_requests = cli.requests_made
    frames: list[tuple[str, pl.DataFrame, str]] = []
    for kind in ("games", "props"):
        try:
            if kind == "games":
                resp = cli.odds(commence_from=_z(window[0]), commence_to=_z(window[1]))
            else:
                resp = cli.props()
        except OddsKeyMissingError:
            raise
        except OddsError as exc:
            report.unreachable = True
            report.messages.append(f"{kind}: {type(exc).__name__}: {exc}")
            log.warning("odds %s failed: %s", kind, exc)
            continue
        rows = _ingest_one(resp, kind, window, res, report)
        raw_name = resp.path.name if resp.path else ""
        frames.append((kind, rows, raw_name))
        if kind == "games":
            report.game_rows = rows.height
        else:
            report.prop_rows = rows.height
    report.requests_used = cli.requests_made - start_requests
    if not dry_run and any(f.height for _, f, _ in frames):
        try:
            con = open_store(out_db or cfg.out_db)
            try:
                for _kind, rows, raw_name in frames:
                    report.inserted += append_rows(con, rows, slate, raw_name)
            finally:
                con.close()
        except (duckdb.Error, OSError) as exc:
            report.store_failed = True
            report.messages.append(f"store: {type(exc).__name__}: {exc}")
            log.warning("odds store failed (raw responses are cached and replayable): %s", exc)
    return report


def summary_lines(rep: CaptureReport) -> list[str]:
    out = [rep.line()]
    out += [f"  note: {m}" for m in rep.messages]
    if rep.unresolved:
        top = ", ".join(f"{k} x{v}" for k, v in rep.unresolved.most_common(8))
        out.append(f"  unresolved names (NULL ids): {top}")
    return out


__all__ = [
    "CaptureReport",
    "append_rows",
    "ensure_table",
    "load_config",
    "open_store",
    "run_capture",
    "summary_lines",
    "slate_window_utc",
]
