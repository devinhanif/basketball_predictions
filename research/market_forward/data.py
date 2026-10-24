"""Read the capture and assemble the scored contracts (LONGSHOT Sections 1 and 5, UNDERS Sections 1
and 6).

Inputs are read from a COPY of ``data/kalshi/kalshi.duckdb`` (opened ``read_only=True``), the
committed
schedule parquet and the stored raw settled pages. Nothing here writes, and no order or account
route exists here.

Pure assembly (:func:`assemble`) is separated from I/O so the self-tests and the dry run feed the
same code.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

from research.market_forward.spec import (
    ENTRY_LEAD_MIN,
    ENTRY_MAX_AGE_MIN,
    MAX_SPREAD_C,
    MIN_BID_C,
    OCCURRENCE_TO_TIP_MIN,
    RuleSpec,
    kind_of,
)
from research.market_forward.spec import SCHEDULE_SHA256 as SCHEDULE_SHA256  # re-export

ET = ZoneInfo("America/New_York")
SETTLED = ("yes", "no", "scalar")
LIVE_DB = Path("data/kalshi/kalshi.duckdb")


@dataclass(frozen=True)
class MarketRec:
    ticker: str
    series: str
    event: str
    stat: str | None
    threshold: float | None
    player_id: int | None
    result: str | None
    settled_ts: datetime | None = None  # naive UTC; None = not stamped


@dataclass(frozen=True)
class Quote:
    ts: datetime  # naive UTC
    yes_bid: float | None
    yes_ask: float | None


#: (ticker, entry cutoff = tip - 60 min) pairs -> the latest live row at or before the cutoff.
QuoteFn = Callable[[Sequence[tuple[str, datetime]]], dict[str, Quote]]


@dataclass
class RawInfo:
    """What the stored raw pages say: occurrence per event, result and scalar value per ticker."""

    occurrence: dict[str, datetime] = field(default_factory=dict)
    result: dict[str, str] = field(default_factory=dict)
    result_day: dict[str, date] = field(default_factory=dict)  # page dir of first settled sighting
    first_day: date | None = None
    last_day: date | None = None
    scalar_micro: dict[str, int] = field(default_factory=dict)
    expiration_value: dict[str, str] = field(default_factory=dict)
    files_read: int = 0


def parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)


def et_date(naive_utc: datetime) -> date:
    return naive_utc.replace(tzinfo=UTC).astimezone(ET).date()


def dollars_to_micro(s: str | float | int) -> int:
    """'0.1600' -> 160000, exactly (Kalshi sends fixed-point strings)."""
    return int(Decimal(str(s)) * 1_000_000)


def _read_page(path: Path) -> list[dict[str, object]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        page = json.load(fh)
    ms = page.get("markets", []) if isinstance(page, dict) else page
    return [m for m in ms if isinstance(m, dict)]


def load_raw(
    raw_root: Path,
    days: Iterable[date],
    series: Iterable[str],
    kinds: Sequence[str] = ("settled",),
    info: RawInfo | None = None,
) -> RawInfo:
    """Scan ``raw_root/<YYYY-MM-DD>/*_<SERIES>_<kind>_*.json.gz`` pages.

    First settled sighting of a ticker wins (a settled market is immutable); occurrence is taken
    from
    any page of the event.
    """
    info = info or RawInfo()
    ser = tuple(series)
    for d in days:
        day_dir = raw_root / d.isoformat()
        if not day_dir.is_dir():
            continue
        info.first_day = d if info.first_day is None else min(info.first_day, d)
        info.last_day = d if info.last_day is None else max(info.last_day, d)
        for s in ser:
            for kind in kinds:
                for p in sorted(day_dir.glob(f"*_{s}_{kind}_*.json.gz")):
                    info.files_read += 1
                    for m in _read_page(p):
                        ev = str(m.get("event_ticker", ""))
                        occ = m.get("occurrence_datetime")
                        if ev and occ and ev not in info.occurrence:
                            info.occurrence[ev] = parse_utc(str(occ))
                        t = str(m.get("ticker", ""))
                        res = str(m.get("result") or "")
                        if kind == "settled" and t and res in SETTLED and t not in info.result:
                            info.result[t] = res
                            info.result_day[t] = d
                            ev_val = m.get("expiration_value")
                            if ev_val is not None:
                                info.expiration_value[t] = str(ev_val)
                            sv = m.get("settlement_value_dollars")
                            if res == "scalar" and sv not in (None, ""):
                                info.scalar_micro[t] = dollars_to_micro(str(sv))
    return info


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class ScheduleRefused(RuntimeError):
    """The schedule parquet is not the pinned file (C2)."""


def load_schedule_tips(path: Path, expected_sha256: str | None = None) -> set[datetime]:
    """Scheduled tips (naive UTC): column ``gameDateTimeUTC`` of ``live_tips_2026-27.parquet``.

    Refuses unless the file hashes to the pinned ``SCHEDULE_SHA256`` (C2).
    """
    want = SCHEDULE_SHA256 if expected_sha256 is None else expected_sha256
    got = file_sha256(path)
    if got != want:
        raise ScheduleRefused(f"schedule {path} sha256 {got[:8]} != pinned {want[:8]}")
    df = pl.read_parquet(path)
    return {parse_utc(str(v)) for v in df["gameDateTimeUTC"].to_list()}


def open_copy(db_path: Path) -> duckdb.DuckDBPyConnection:
    """Open a COPY of the capture read-only; refuse the live launchd-owned file."""
    live = (Path(__file__).resolve().parents[2] / LIVE_DB).resolve()
    if db_path.resolve() == live:
        raise PermissionError(
            "refusing to read data/kalshi/kalshi.duckdb directly (launchd writer); pass a COPY"
        )
    return duckdb.connect(str(db_path), read_only=True)


def read_markets(con: duckdb.DuckDBPyConnection, series: Sequence[str]) -> list[MarketRec]:
    ph = ",".join("?" for _ in series)
    rows = con.execute(
        "SELECT ticker, series_ticker, event_ticker, stat, threshold, player_id, result, "
        "settled_ts "
        f"FROM kalshi_markets WHERE series_ticker IN ({ph})",
        list(series),
    ).fetchall()
    return [
        MarketRec(
            str(t),
            str(s),
            str(e),
            None if st is None else str(st),
            None if th is None else float(th),
            None if pid is None else int(pid),
            None if r in (None, "") else str(r),
            None if sts is None else sts.replace(tzinfo=None),
        )
        for t, s, e, st, th, pid, r, sts in rows
    ]


def duckdb_quotes(con: duckdb.DuckDBPyConnection) -> QuoteFn:
    """Latest ``source='live'`` row at or before each cutoff (ASOF join). Never a later row."""

    def fn(pairs: Sequence[tuple[str, datetime]]) -> dict[str, Quote]:
        if not pairs:
            return {}
        probe = pl.DataFrame(
            {"ticker": [p[0] for p in pairs], "cut": [p[1] for p in pairs]},
            schema={"ticker": pl.Utf8, "cut": pl.Datetime("us")},
        )
        con.register("fw_probe", probe)
        try:
            rows = con.execute(
                "SELECT m.ticker, p.ts, p.yes_bid, p.yes_ask FROM fw_probe m "
                "ASOF LEFT JOIN (SELECT ticker, ts, yes_bid, yes_ask FROM kalshi_prices "
                "WHERE source = 'live') p ON m.ticker = p.ticker AND m.cut >= p.ts"
            ).fetchall()
        finally:
            con.unregister("fw_probe")
        return {
            str(t): Quote(ts, None if b is None else float(b), None if a is None else float(a))
            for t, ts, b, a in rows
            if ts is not None
        }

    return fn


_LEAD_LETTERS = re.compile(r"^[A-Z]+")


def ladder_key(series: str, ticker: str, event: str) -> str:
    """The nested-rung ladder a contract belongs to (guard 8.6(b))."""
    suffix = ticker[len(event) + 1 :]
    if series in ("KXNBASPREAD", "KXNBATEAMTOTAL"):
        m = _LEAD_LETTERS.match(suffix)
        return f"{series}|{event}|{m.group(0) if m else suffix}"
    if series == "KXNBATOTAL":
        return f"{series}|{event}"
    if series == "KXNBAGAME":
        return ticker  # each side is its own ladder (ambiguity, listed in the hand-off)
    return ticker.rsplit("-", 1)[0]  # one player-stat: everything but the threshold segment


@dataclass
class Universe:
    """The scored contracts and every count the report prints."""

    df: pl.DataFrame
    counts: Counter[str]
    game_counts: Counter[str]
    coverage_by_date: dict[date, tuple[int, int]]  # date -> (with entry, settled markets)
    scalar_total: int
    scalar_missing: list[str]
    null_player_props: list[str]
    unmatched_games: list[str]
    n_dnp: int
    tips: dict[str, datetime] = field(default_factory=dict)
    no_schedule_by_date: Counter[date] = field(default_factory=Counter)
    settled_by: datetime | None = None
    db_sha256: str | None = None
    raw_range: tuple[date, date] | None = None


def _et_tip(occ: datetime) -> datetime:
    return occ - timedelta(minutes=OCCURRENCE_TO_TIP_MIN)


def assemble(
    spec: RuleSpec,
    markets: Sequence[MarketRec],
    raw: RawInfo,
    schedule_tips: set[datetime],
    quote_fn: QuoteFn,
    *,
    window_start: date,
    cutoff: date,
    settled_by: datetime | None = None,
) -> Universe:
    """Apply the frozen universe, entry, eligibility and game-level settlement rules.

    ``settled_by`` (naive UTC; C1) is the as-of-read settlement horizon: a result counts only if
    stamped ``settled_ts <= settled_by`` (DB) or found on a raw page dated at or before that ET
    day. ``None`` means unrestricted (synthetic worlds and the pre-window preflight only).
    """
    settled_day = None if settled_by is None else et_date(settled_by)
    no_sched_by_date: Counter[date] = Counter()
    counts: Counter[str] = Counter()
    gcounts: Counter[str] = Counter()
    by_game: dict[str, list[MarketRec]] = {}
    game_tip: dict[str, datetime] = {}
    unmatched: list[str] = []
    skipped_games: set[str] = set()
    for m in markets:
        if m.series not in spec.all_series:
            continue
        game = m.event.split("-", 1)[1]
        by_game.setdefault(game, []).append(m)
    for game, ms in by_game.items():
        occ = next((raw.occurrence[m.event] for m in ms if m.event in raw.occurrence), None)
        if occ is None:
            gcounts["no_occurrence"] += 1
            unmatched.append(game)
            skipped_games.add(game)
            continue
        tip = _et_tip(occ)
        d = et_date(tip)
        if tip not in schedule_tips:
            gcounts["no_schedule_match"] += 1
            counts["no_schedule_match_contracts"] += len(ms)
            no_sched_by_date[d] += 1
            unmatched.append(game)
            skipped_games.add(game)
            continue
        if d < window_start:
            gcounts["before_window"] += 1
            skipped_games.add(game)
            continue
        if d > cutoff:
            gcounts["after_cutoff"] += 1
            skipped_games.add(game)
            continue
        game_tip[game] = tip

    # entry snapshots for every in-range market
    pairs: list[tuple[str, datetime]] = []
    for game, tip in game_tip.items():
        cut = tip - timedelta(minutes=ENTRY_LEAD_MIN)
        pairs.extend((m.ticker, cut) for m in by_game[game])
    quotes = quote_fn(pairs)

    cov: dict[date, list[int]] = {}
    scalar_total = 0
    scalar_missing: list[str] = []
    rows: list[dict[str, object]] = []
    null_players: list[str] = []
    n_dnp = 0
    for game, tip in game_tip.items():
        d = et_date(tip)
        cut = tip - timedelta(minutes=ENTRY_LEAD_MIN)
        eligible: list[tuple[MarketRec, int, int, float, str]] = []
        unsettled_eligible = 0
        for m in by_game[game]:
            res = None
            if m.result and (
                settled_by is None or (m.settled_ts is not None and m.settled_ts <= settled_by)
            ):
                res = m.result
            if res is None:
                rr = raw.result.get(m.ticker)
                rd = raw.result_day.get(m.ticker)
                if rr is not None and (
                    settled_day is None or (rd is not None and rd <= settled_day)
                ):
                    res = rr
            if res not in SETTLED:
                res = None
            q = quotes.get(m.ticker)
            age = None if q is None else (cut - q.ts).total_seconds() / 60.0
            has_entry = q is not None and age is not None and age <= ENTRY_MAX_AGE_MIN
            if res is not None:
                c = cov.setdefault(d, [0, 0])
                c[1] += 1
                c[0] += int(has_entry)
                if res == "scalar":
                    scalar_total += 1
                    if m.ticker not in raw.scalar_micro:
                        scalar_missing.append(m.ticker)
            counts["markets_in_window"] += 1
            if not has_entry or q is None or age is None:
                counts["no_entry_snapshot"] += 1
                continue
            if q.yes_bid is None or round(100 * q.yes_bid) < MIN_BID_C:
                counts["bid_zero"] += 1
                continue
            if q.yes_ask is None:
                counts["null_ask"] += 1
                continue
            bid_c, ask_c = round(100 * q.yes_bid), round(100 * q.yes_ask)
            if ask_c < bid_c or ask_c == 0 or ask_c - bid_c > MAX_SPREAD_C:  # m7
                counts["wide_spread"] += 1
                continue
            if spec.max_mid_sum is not None and bid_c + ask_c > spec.max_mid_sum:
                counts["above_15c"] += 1
                continue
            counts["eligible"] += 1
            if res is None:
                unsettled_eligible += 1
            eligible.append((m, bid_c, ask_c, age, res or ""))
        if unsettled_eligible:
            gcounts["unsettled_game"] += 1
            counts["unsettled_game_contracts"] += len(eligible)
            continue
        if not eligible:
            gcounts["no_eligible_contract"] += 1
            continue
        gcounts["scored_games"] += 1
        for m, bid_c, ask_c, age, res in eligible:
            if res == "scalar":
                micro = raw.scalar_micro.get(m.ticker)
                if micro is None:
                    counts["scalar_no_value"] += 1
                    continue
                n_dnp += 1
            else:
                micro = 1_000_000 if res == "yes" else 0
            kind = kind_of(m.series)
            if kind == "prop" and not m.stat:
                stat = "unknown"  # m3: counted, never "" (which would pool with game series)
                counts["null_stat_prop"] += 1
            else:
                stat = m.stat or ""
            if m.series in ("KXNBAPRA", "KXNBAPR", "KXNBAPA", "KXNBARA"):
                stat_axis = "combo"
            else:
                stat_axis = stat
            if kind_of(m.series) in ("prop", "combo") and m.player_id is None:
                null_players.append(m.ticker)
            rows.append(
                {
                    "ticker": m.ticker,
                    "series": m.series,
                    "kind": kind_of(m.series),
                    "event": m.event,
                    "game": game,
                    "date": d,
                    "stat": stat_axis,
                    "ladder": ladder_key(m.series, m.ticker, m.event),
                    "player_id": m.player_id,
                    "threshold": m.threshold,
                    "bid_c": bid_c,
                    "ask_c": ask_c,
                    "age_min": age,
                    "y_u": micro,
                    "dnp": res == "scalar",
                    "in_pool": m.series in spec.decision_series,
                }
            )
    schema = {
        "ticker": pl.Utf8,
        "series": pl.Utf8,
        "kind": pl.Utf8,
        "event": pl.Utf8,
        "game": pl.Utf8,
        "date": pl.Date,
        "stat": pl.Utf8,
        "ladder": pl.Utf8,
        "player_id": pl.Int64,
        "threshold": pl.Float64,
        "bid_c": pl.Int64,
        "ask_c": pl.Int64,
        "age_min": pl.Float64,
        "y_u": pl.Int64,
        "dnp": pl.Boolean,
        "in_pool": pl.Boolean,
    }
    df = pl.DataFrame(rows, schema=schema)
    return Universe(
        df=df,
        counts=counts,
        game_counts=gcounts,
        coverage_by_date={k: (v[0], v[1]) for k, v in cov.items()},
        scalar_total=scalar_total,
        scalar_missing=scalar_missing,
        null_player_props=null_players,
        unmatched_games=unmatched,
        n_dnp=n_dnp,
        tips=dict(game_tip),
        no_schedule_by_date=no_sched_by_date,
        settled_by=settled_by,
    )
