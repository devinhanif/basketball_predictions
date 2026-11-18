"""Kalshi player-prop price history for the app's Market lens (display only).

Display data only: nothing here is a forecast, an edge or a signal. No alert, EV, JARVIS or
paper-trade path reads it (calm calls, no swing-chasing), it carries no model number, and it
derives no indicator (no momentum, no trend). Prices and volumes as captured.

Inputs are COPIES of stores that already exist; this module sends no request of any kind (it
imports no HTTP client) and opens DuckDB read-only:

* the live capture's raw ``/markets`` pages (``data/raw/live_<date>/kalshi/<SERIES>_p<n>/``,
  one page about every 60 s inside the capture window) with ``manifest.jsonl`` for the receive
  time of each page;
* the live capture's public trade prints (``kalshi_trades/<date>/*.json.gz``), the only source of
  true candles (Kalshi's own ``created_time``);
* the 15-minute snapshot store's ``kalshi_prices`` rows (``source='live'``), all day.

Two quote sources are never blended: each market carries one quote series per source with its
nominal resolution, its measured gaps and its provenance. Quote series are run-length encoded:
consecutive polls with identical bid, ask, last, volume and open interest collapse into one row
``[t_first, t_last, n_polls, ...]``; a row never spans a gap. Candles are built only from prints.

Contract: docs/contracts/kalshi_prop_history_v1.md.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import statistics
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from nba.kalshi.aliases import load_reviewed_aliases
from nba.kalshi.parse import SkippedMarket, parse_market
from nba.kalshi.thresholds import SERIES_STAT

SCHEMA_VERSION = 1
KIND = "kalshi_prop_history"
#: Prop series in scope: the player-prop series the parser knows (``SERIES_STAT``). The ``*PROT``,
#: ``ESCALATOR``, ``PTSLEADER``, ``2D``/``3D`` series are other products and are not exported.
PROP_SERIES: tuple[str, ...] = tuple(SERIES_STAT)
#: Slates before this date are refused (season 2025 is the frozen holdout; this tool is for the
#: current season's slates only).
FIRST_SLATE = date(2026, 10, 20)

QUOTE_FIELDS = ["t_first", "t_last", "n_polls", "bid", "ask", "last", "volume", "open_interest"]
CANDLE_FIELDS = ["t", "o", "h", "l", "c", "volume", "n_trades"]
CANDLE_BUCKET_S = 60
#: Size budget per game file (measured 2026-11-16, 8 games, 2,340 markets: largest 3.15 MB raw,
#: 0.49 MB gzipped). Over budget is flagged in the index, never truncated.
GAME_BUDGET_BYTES = 4_000_000
GAME_BUDGET_GZ_BYTES = 750_000
#: Nominal poll period per quote source, and the hole that counts as a gap (2.5 periods).
NOMINAL_S = {"capture": 60, "snapshot": 900}
GAP_FACTOR = 2.5
#: The capture's window ends 4 h after the last tip (``nba.live.capture.Budget.tail_h``).
CAPTURE_TAIL_H = 4.0

PROVENANCE = {
    "capture": (
        "live capture: public GET /markets?series_ticker=S&status=open pages, about one a minute "
        "from 2 h before the first tip to 4 h after the last; t is our receive time"
    ),
    "snapshot": (
        "15-minute snapshot job: public GET /markets top of book into kalshi_prices "
        "(source='live'); t is the snapshot run's timestamp, not the page's receive time"
    ),
    "prints": (
        "live capture: public GET /markets/trades prints, one row per trade id; candle time is "
        "Kalshi's created_time"
    ),
}

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1
    )
}
_EVENT_RE = re.compile(r"^(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})(?P<teams>[A-Z]+)$")
_STAMP_RE = re.compile(r"^(\d{8}T\d{6}Z)")


class LiveStoreRefused(RuntimeError):
    """Raised when asked to open a store a live job writes to (copy it first)."""


# ---------------------------------------------------------------------------------------------
# parsing helpers (pure)


def event_code(event_ticker: str) -> str | None:
    """``KXNBAPTS-26NOV16MEMWAS`` -> ``26NOV16MEMWAS`` (the game part shared by every series)."""
    parts = event_ticker.split("-")
    return parts[1] if len(parts) >= 2 and _EVENT_RE.match(parts[1]) else None


def event_date(code: str) -> date | None:
    m = _EVENT_RE.match(code)
    if not m or m.group("mon") not in _MONTHS:
        return None
    return date(2000 + int(m.group("yy")), _MONTHS[m.group("mon")], int(m.group("dd")))


def event_teams(code: str) -> tuple[str | None, str | None]:
    """Kalshi packs away+home abbreviations without a separator; split only when unambiguous
    (two or three letters each, total five or six). Otherwise ``(None, None)``: never guessed."""
    m = _EVENT_RE.match(code)
    teams = m.group("teams") if m else ""
    if len(teams) == 6:
        return teams[:3], teams[3:]
    return None, None


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _r4(v: Any) -> float | None:
    x = _f(v)
    return None if x is None else round(x, 4)


def _epoch(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def _iso(ts: int | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


def stamp_from_name(name: str) -> datetime | None:
    m = _STAMP_RE.match(name)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


# ---------------------------------------------------------------------------------------------
# readers (files only; no network)


@dataclass
class QuotePoint:
    ticker: str
    t: int
    bid: float | None
    ask: float | None
    last: float | None
    volume: float | None
    open_interest: float | None


@dataclass
class Print:
    trade_id: str
    ticker: str
    t_us: int  # Kalshi created_time, microseconds since epoch
    yes_price: float
    count: float


@dataclass
class SlateInputs:
    day: date
    capture_points: list[QuotePoint] = field(default_factory=list)
    capture_page_times: dict[str, list[int]] = field(default_factory=dict)  # series -> poll times
    snapshot_points: list[QuotePoint] = field(default_factory=list)
    snapshot_times: list[int] = field(default_factory=list)
    prints: list[Print] = field(default_factory=list)
    markets: dict[str, dict[str, Any]] = field(default_factory=dict)  # ticker -> raw market
    tips: dict[str, datetime] = field(default_factory=dict)  # event code -> scheduled tip
    unreadable: list[str] = field(default_factory=list)
    capture_as_of: int | None = None
    snapshot_as_of: int | None = None


def _manifest_times(capture_dir: Path) -> dict[str, datetime]:
    out: dict[str, datetime] = {}
    mf = capture_dir / "manifest.jsonl"
    if not mf.exists():
        return out
    with mf.open() as fh:
        for line in fh:
            try:
                row = json.loads(line)
                out[str(row["path"])] = datetime.fromisoformat(str(row["received_at"]))
            except (ValueError, KeyError, TypeError):
                continue
    return out


def _on_slate(ticker: str, event_ticker: str | None, day: date) -> bool:
    code = event_code(event_ticker or "-".join(ticker.split("-")[:2]))
    return code is not None and event_date(code) == day


def read_capture(capture_dir: Path, day: date, inputs: SlateInputs) -> None:
    """Every prop page of the capture: one QuotePoint per (market, page). The page's time is the
    manifest's ``received_at`` (microseconds), else the file name's UTC second."""
    received = _manifest_times(capture_dir)
    root = capture_dir / "kalshi"
    if not root.exists():
        return
    for series_dir in sorted(root.iterdir()):
        series = series_dir.name.rsplit("_p", 1)[0]
        if series not in PROP_SERIES or not series_dir.is_dir():
            continue
        for f in sorted(series_dir.glob("*.json.gz")):
            rel = str(f.relative_to(capture_dir))
            when = received.get(rel) or stamp_from_name(f.name)
            if when is None:
                inputs.unreadable.append(rel)
                continue
            try:
                body = json.loads(gzip.decompress(f.read_bytes()))
            except (OSError, ValueError, EOFError):
                inputs.unreadable.append(rel)
                continue
            t = _epoch(when)
            inputs.capture_page_times.setdefault(series, []).append(t)
            inputs.capture_as_of = max(inputs.capture_as_of or t, t)
            for m in body.get("markets") or []:
                tk = str(m.get("ticker") or "")
                if not tk or not _on_slate(tk, m.get("event_ticker"), day):
                    continue
                inputs.markets[tk] = m  # newest page wins (sorted by name = time)
                inputs.capture_points.append(
                    QuotePoint(
                        tk,
                        t,
                        _f(m.get("yes_bid_dollars")),
                        _f(m.get("yes_ask_dollars")),
                        _f(m.get("last_price_dollars")),
                        _f(m.get("volume_fp")),
                        _f(m.get("open_interest_fp")),
                    )
                )


def read_prints(capture_dir: Path, day: date, inputs: SlateInputs) -> None:
    """Prop prints from the capture's trade files, one per trade id."""
    seen: set[str] = set()
    for f in sorted((capture_dir / "kalshi_trades").glob("*/*.json.gz")):
        try:
            body = json.loads(gzip.decompress(f.read_bytes()))
        except (OSError, ValueError, EOFError):
            inputs.unreadable.append(str(f.relative_to(capture_dir)))
            continue
        for tk, rows in body.items():
            if tk.split("-")[0] not in PROP_SERIES or not _on_slate(tk, None, day):
                continue
            for r in rows:
                tid = str(r.get("trade_id") or "")
                price, count = _f(r.get("yes_price_dollars")), _f(r.get("count_fp"))
                created = r.get("created_time")
                if not tid or tid in seen or price is None or count is None or not created:
                    continue
                seen.add(tid)
                ct = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                us = int(ct.timestamp()) * 1_000_000 + ct.microsecond
                inputs.prints.append(Print(tid, tk, us, price, count))


def read_scoreboard(capture_dir: Path, inputs: SlateInputs) -> None:
    """Scheduled tips from the newest stored ESPN scoreboard page (ESPN ``date``: the scheduled
    start, not the actual tip). Keyed by Kalshi's away+home code."""
    to_kalshi = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"}
    pages = sorted((capture_dir / "espn_scoreboard").glob("*/*.json.gz"))
    if not pages:
        return
    try:
        body = json.loads(gzip.decompress(pages[-1].read_bytes()))
    except (OSError, ValueError, EOFError):
        inputs.unreadable.append(str(pages[-1].relative_to(capture_dir)))
        return
    for ev in body.get("events") or []:
        try:
            comp = ev["competitions"][0]
            teams = {c["homeAway"]: c["team"]["abbreviation"] for c in comp["competitors"]}
            tip = datetime.fromisoformat(str(ev["date"]).replace("Z", "+00:00"))
        except (KeyError, IndexError, ValueError, TypeError):
            continue
        away = to_kalshi.get(teams.get("away", ""), teams.get("away", ""))
        home = to_kalshi.get(teams.get("home", ""), teams.get("home", ""))
        if tip.tzinfo is None:
            tip = tip.replace(tzinfo=UTC)
        inputs.tips[f"{away}{home}"] = tip.astimezone(UTC)


def refuse_live_db(db: Path) -> None:
    """The 15-minute snapshot job owns ``data/kalshi/kalshi.duckdb``; a second connection can block
    it. This tool opens a copy only: any checkout's ``data/kalshi/*.duckdb``, ``data/*.duckdb``
    or ``nba.duckdb`` is refused by path shape (the client, which can reach the network, is not
    imported)."""
    r = db.resolve()
    if (
        (r.parent.name == "kalshi" and r.parent.parent.name == "data")
        or r.parent.name == "data"
        or r.name == "nba.duckdb"
    ):
        raise LiveStoreRefused(
            f"{db} is a live snapshot store; copy it (after a snapshot run has finished) and "
            "pass the copy"
        )


def read_snapshot(db: Path, day: date, inputs: SlateInputs) -> None:
    """``kalshi_prices`` rows (``source='live'``) for the slate's prop markets, from a COPY of the
    snapshot store, opened read-only. Markets the capture never saw are added from
    ``kalshi_markets`` (title and event ticker) so they still get a row."""
    import duckdb

    refuse_live_db(db)
    con = duckdb.connect(str(db), read_only=True)
    try:
        code = f"{day.strftime('%y')}{day.strftime('%b').upper()}{day.strftime('%d')}"
        like = [f"{s}-{code}%" for s in PROP_SERIES]
        where = " OR ".join(["p.ticker LIKE ?"] * len(like))
        rows = con.execute(
            "SELECT p.ticker, p.ts, p.yes_bid, p.yes_ask, p.last, p.volume, p.open_interest, "
            "m.title, m.event_ticker FROM kalshi_prices p LEFT JOIN kalshi_markets m "
            f"ON m.ticker = p.ticker WHERE p.source = 'live' AND ({where}) ORDER BY p.ticker, p.ts",
            like,
        ).fetchall()
    finally:
        con.close()
    times: set[int] = set()
    for tk, ts, bid, ask, last, vol, oi, title, ev in rows:
        if not _on_slate(str(tk), ev, day):
            continue
        t = _epoch(ts)
        times.add(t)
        inputs.snapshot_points.append(
            # the store keeps prices as FLOAT (float32): round back to Kalshi's 4 decimals
            QuotePoint(str(tk), t, _r4(bid), _r4(ask), _r4(last), _f(vol), _f(oi))
        )
        if tk not in inputs.markets:
            inputs.markets[str(tk)] = {"ticker": tk, "title": title, "event_ticker": ev}
    inputs.snapshot_times = sorted(times)
    inputs.snapshot_as_of = max(times) if times else None


# ---------------------------------------------------------------------------------------------
# series building (pure)


def gaps(times: Sequence[int], threshold_s: float) -> list[list[int]]:
    """Holes longer than ``threshold_s`` between consecutive observation times."""
    ts = sorted(set(times))
    return [[a, b] for a, b in zip(ts, ts[1:], strict=False) if b - a > threshold_s]


def run_length(points: Sequence[QuotePoint], threshold_s: float) -> list[list[Any]]:
    """Collapse consecutive identical quotes into ``[t_first, t_last, n_polls, bid, ask, last,
    volume, open_interest]``; a hole longer than ``threshold_s`` always starts a new row, so a row
    never claims a value across a gap."""
    out: list[list[Any]] = []
    prev_t: int | None = None
    for p in sorted(points, key=lambda p: p.t):
        if prev_t is not None and p.t == prev_t:
            continue  # the same page twice (should not happen): one observation
        vals = [p.bid, p.ask, p.last, p.volume, p.open_interest]
        if out and out[-1][3:] == vals and prev_t is not None and p.t - prev_t <= threshold_s:
            out[-1][1] = p.t
            out[-1][2] += 1
        else:
            out.append([p.t, p.t, 1, *vals])
        prev_t = p.t
    return out


def quote_series(source: str, points: Sequence[QuotePoint]) -> dict[str, Any] | None:
    if not points:
        return None
    nominal = NOMINAL_S[source]
    thr = nominal * GAP_FACTOR
    ts = sorted({p.t for p in points})
    diffs = [b - a for a, b in zip(ts, ts[1:], strict=False)]
    return {
        "source": source,
        "kind": "quote_points",
        "nominal_resolution_s": nominal,
        "gap_threshold_s": thr,
        "first_seen": ts[0],
        "last_seen": ts[-1],
        "n_polls": len(ts),
        "median_gap_s": statistics.median(diffs) if diffs else None,
        "max_gap_s": max(diffs) if diffs else None,
        "gaps": gaps(ts, thr),
        "rows": run_length(points, thr),
    }


def candles(prints: Sequence[Print], bucket_s: int = CANDLE_BUCKET_S) -> list[list[Any]]:
    """True OHLC of executed yes prices per bucket (Kalshi's ``created_time``). Ties within one
    microsecond are broken by trade id (the print order is not recoverable). No print, no row."""
    buckets: dict[int, list[Print]] = {}
    for p in sorted(prints, key=lambda p: (p.t_us, p.trade_id)):
        buckets.setdefault(p.t_us // 1_000_000 // bucket_s * bucket_s, []).append(p)
    out: list[list[Any]] = []
    for t in sorted(buckets):
        ps = buckets[t]
        prices = [p.yes_price for p in ps]
        out.append(
            [
                t,
                prices[0],
                max(prices),
                min(prices),
                prices[-1],
                round(sum(p.count for p in ps), 2),
                len(ps),
            ]
        )
    return out


def print_coverage(prints: Sequence[Print], capture_points: Sequence[QuotePoint]) -> dict[str, Any]:
    """Prints held against the contracts the book says traded, over the capture's span of this
    market (first to last poll). ``None`` ratio when the book shows no volume change."""
    pts = sorted((p for p in capture_points if p.volume is not None), key=lambda p: p.t)
    if len(pts) < 2:
        return {"print_volume": None, "book_volume_delta": None, "print_coverage": None}
    lo, hi = pts[0].t, pts[-1].t
    pv = sum(p.count for p in prints if lo * 1_000_000 < p.t_us <= hi * 1_000_000)
    bd = (pts[-1].volume or 0.0) - (pts[0].volume or 0.0)
    return {
        "print_volume": round(pv, 2),
        "book_volume_delta": round(bd, 2),
        "print_coverage": round(pv / bd, 4) if bd > 0 else None,
    }


def describe_market(raw: Mapping[str, Any], aliases: Mapping[str, int]) -> dict[str, Any]:
    """Ticker, series, stat, threshold and player through the existing parser and the reviewed
    alias table. An unmatched name keeps ``player_id`` null and says why; nothing is guessed."""
    tk = str(raw.get("ticker"))
    series = tk.split("-")[0]
    title = raw.get("title")
    cs = raw.get("custom_strike") if isinstance(raw.get("custom_strike"), dict) else {}
    out: dict[str, Any] = {
        "ticker": tk,
        "series": series,
        "event_ticker": raw.get("event_ticker"),
        "title": title,
        "stat": SERIES_STAT.get(series),
        "threshold": None,
        "floor_strike": _f(raw.get("floor_strike")),
        "player_name": None,
        "player_id": None,
        "kalshi_player_uuid": (cs or {}).get("basketball_player"),
        "resolved": False,
        "unresolved_reason": None,
    }
    unmatched: list[str] = []
    try:
        row = parse_market(dict(raw), aliases=dict(aliases), unmatched=unmatched)
    except SkippedMarket:
        out["unresolved_reason"] = "title_unparsed"
        return out
    out["threshold"] = row["threshold"]
    out["stat"] = row["stat"]
    if unmatched:
        out["player_name"] = unmatched[0]
        out["unresolved_reason"] = "name_not_in_reviewed_alias_table"
        return out
    out["player_id"] = row["player_id"]
    m = re.match(r"^([^:]+?):", str(title or ""))
    out["player_name"] = " ".join(m.group(1).split()) if m else None
    out["resolved"] = True
    return out


# ---------------------------------------------------------------------------------------------
# export


def _group(items: Iterable[Any], key: str) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for it in items:
        out.setdefault(getattr(it, key), []).append(it)
    return out


def build_games(
    inputs: SlateInputs, aliases: Mapping[str, int], built_at: datetime
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """One payload per game (event code) and the slate index (without file sizes)."""
    cap = _group(inputs.capture_points, "ticker")
    snap = _group(inputs.snapshot_points, "ticker")
    prt = _group(inputs.prints, "ticker")
    tickers = sorted(set(inputs.markets) | set(cap) | set(snap) | set(prt))
    by_game: dict[str, list[dict[str, Any]]] = {}
    unresolved: list[dict[str, Any]] = []
    for tk in tickers:
        raw = inputs.markets.get(tk) or {"ticker": tk, "event_ticker": None, "title": None}
        desc = describe_market(raw, aliases)
        code = event_code(str(desc["event_ticker"] or "-".join(tk.split("-")[:2]))) or "unknown"
        series = [
            s
            for s in (
                quote_series("capture", cap.get(tk, [])),
                quote_series("snapshot", snap.get(tk, [])),
            )
            if s
        ]
        ps = prt.get(tk, [])
        desc["quotes"] = series
        desc["candles"] = {
            "source": "prints",
            "kind": "trade_candles",
            "bucket_s": CANDLE_BUCKET_S,
            "n_trades": len(ps),
            **print_coverage(ps, cap.get(tk, [])),
            "rows": candles(ps),
        }
        if not desc["resolved"]:
            unresolved.append(
                {k: desc[k] for k in ("ticker", "title", "player_name", "unresolved_reason")}
            )
        by_game.setdefault(code, []).append(desc)

    last_tip = max(inputs.tips.values()) if inputs.tips else None
    final = last_tip is not None and built_at >= last_tip + timedelta(hours=CAPTURE_TAIL_H)
    sources = {
        "capture": {
            "provenance": PROVENANCE["capture"],
            "nominal_resolution_s": NOMINAL_S["capture"],
            "as_of": _iso(inputs.capture_as_of),
            "poll_gaps": {
                s: gaps(ts, NOMINAL_S["capture"] * GAP_FACTOR)
                for s, ts in sorted(inputs.capture_page_times.items())
            },
        },
        "snapshot": {
            "provenance": PROVENANCE["snapshot"],
            "nominal_resolution_s": NOMINAL_S["snapshot"],
            "as_of": _iso(inputs.snapshot_as_of),
            "poll_gaps": gaps(inputs.snapshot_times, NOMINAL_S["snapshot"] * GAP_FACTOR),
        },
        "prints": {"provenance": PROVENANCE["prints"], "bucket_s": CANDLE_BUCKET_S},
    }
    common = {
        "schema": SCHEMA_VERSION,
        "display_only": True,
        "built_at": built_at.isoformat().replace("+00:00", "Z"),
        "status": "final" if final else "partial",
        "slate_date": inputs.day.isoformat(),
        "time_unit": "unix seconds, UTC",
        "price_unit": "dollars per contract, 0 to 1, yes side",
    }
    games: dict[str, dict[str, Any]] = {}
    for code, markets in sorted(by_game.items()):
        away, home = event_teams(code)
        tip = inputs.tips.get(f"{away}{home}") if away else None
        games[code] = {
            **common,
            "kind": KIND,
            "event_code": code,
            "away": away,
            "home": home,
            "scheduled_tip": tip.isoformat().replace("+00:00", "Z") if tip else None,
            "tip_source": "ESPN scoreboard scheduled start (not the actual tip)",
            "quote_fields": QUOTE_FIELDS,
            "candle_fields": CANDLE_FIELDS,
            "sources": sources,
            "n_markets": len(markets),
            "markets": markets,
        }
    index = {
        **common,
        "kind": f"{KIND}_index",
        "sources": {
            k: {kk: vv for kk, vv in v.items() if kk != "poll_gaps"} for k, v in sources.items()
        },
        "series_in_scope": list(PROP_SERIES),
        "n_markets": sum(len(m) for m in by_game.values()),
        "n_resolved": sum(1 for ms in by_game.values() for m in ms if m["resolved"]),
        "games": [
            {
                "event_code": code,
                "file": f"{code}.json",
                "n_markets": g["n_markets"],
                "scheduled_tip": g["scheduled_tip"],
            }
            for code, g in games.items()
        ],
        "unresolved": unresolved,
        "unreadable_inputs": sorted(inputs.unreadable),
    }
    return games, index


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, separators=(",", ":"), sort_keys=False).encode()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return len(data)


def write_slate(
    out_dir: Path, games: Mapping[str, dict[str, Any]], index: dict[str, Any]
) -> dict[str, Any]:
    """``<out>/<slate>/<event_code>.json`` per game and ``index.json`` with each file's bytes and
    gzipped bytes (measured, not estimated)."""
    day_dir = out_dir / str(index["slate_date"])
    sizes = {}
    for code, payload in games.items():
        n = _write_atomic(day_dir / f"{code}.json", payload)
        gz = len(gzip.compress((day_dir / f"{code}.json").read_bytes(), 6))
        sizes[code] = (n, gz)
    for g in index["games"]:
        g["bytes"], g["gz_bytes"] = sizes[g["event_code"]]
        g["over_budget"] = g["bytes"] > GAME_BUDGET_BYTES or g["gz_bytes"] > GAME_BUDGET_GZ_BYTES
    index["budget"] = {"game_bytes": GAME_BUDGET_BYTES, "game_gz_bytes": GAME_BUDGET_GZ_BYTES}
    _write_atomic(day_dir / "index.json", index)
    return index


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m nba.kalshi.prop_history",
        description="Build the display-only prop price history for one slate from COPIES of the "
        "capture's raw pages and the snapshot store. Sends no request.",
    )
    p.add_argument("--date", required=True, help="slate date (ET), YYYY-MM-DD")
    p.add_argument("--capture-dir", type=Path, help="copy of data/raw/live_<date>")
    p.add_argument("--snapshot-db", type=Path, help="COPY of data/kalshi/kalshi.duckdb")
    p.add_argument("--out", type=Path, required=True, help="output directory")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    day = date.fromisoformat(args.date)
    if day < FIRST_SLATE:
        print(
            f"refused: {day} is before {FIRST_SLATE} (current-season slates only)", file=sys.stderr
        )
        return 2
    if args.capture_dir is None and args.snapshot_db is None:
        print("need --capture-dir and/or --snapshot-db", file=sys.stderr)
        return 2
    inputs = SlateInputs(day)
    if args.capture_dir is not None:
        read_capture(args.capture_dir, day, inputs)
        read_prints(args.capture_dir, day, inputs)
        read_scoreboard(args.capture_dir, inputs)
    if args.snapshot_db is not None:
        try:
            read_snapshot(args.snapshot_db, day, inputs)
        except LiveStoreRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
    games, index = build_games(inputs, load_reviewed_aliases(), datetime.now(UTC))
    index = write_slate(args.out, games, index)
    summary = {
        "slate_date": index["slate_date"],
        "status": index["status"],
        "games": len(index["games"]),
        "n_markets": index["n_markets"],
        "n_resolved": index["n_resolved"],
        "unresolved": len(index["unresolved"]),
        "unreadable_inputs": len(index["unreadable_inputs"]),
        "max_game_bytes": max((g["bytes"] for g in index["games"]), default=0),
        "max_game_gz_bytes": max((g["gz_bytes"] for g in index["games"]), default=0),
        "total_bytes": sum(g["bytes"] for g in index["games"]),
    }
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
