"""Per-minute feature log for the PAPER swing arms (no signals, no scoring tonight).

Two write-once parquet kinds under ``data/derived/ride_features/<date>/``:
- ``games_<UTC ts>.parquet``: one row per live game per minute: score state, our and ESPN win prob,
  the game-winner price, our-prob minus price gap, scoring-run features from the play list.
- ``candles_<UTC ts>.parquet``: 1-minute OHLC + volume + taker buy/sell imbalance from the captured
  public trades, for minutes older than the current one (a tick of lag for late captured trades).
Raw and derived files are never overwritten; an existing path is skipped.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from nba.ride.capture_view import CaptureView, GameSnap, Play, read_json
from nba.ride.winprob import seconds_left

STAMP_FMT = "%Y%m%dT%H%M%SZ"


def elapsed_secs(period: int, clock_secs: float) -> float:
    """Game seconds played (regulation 720 s quarters, 300 s overtimes)."""
    if period <= 4:
        return (period - 1) * 720.0 + (720.0 - clock_secs)
    return 2880.0 + (period - 5) * 300.0 + (300.0 - clock_secs)


def run_features(
    plays: tuple[Play, ...], windows_min: tuple[int, ...] = (3, 5)
) -> dict[str, float]:
    """Points by side and margin change over the last N game minutes / last 10 scoring plays."""
    out: dict[str, float] = {}
    if not plays:
        return out
    last = plays[-1]
    now_e = elapsed_secs(last.period, last.clock_secs)
    for w in windows_min:
        past = [p for p in plays if elapsed_secs(p.period, p.clock_secs) <= now_e - 60.0 * w]
        base = past[-1] if past else Play(1, 720.0, 0, 0, None, False, "")
        out[f"home_pts_last{w}m"] = float(last.home - base.home)
        out[f"away_pts_last{w}m"] = float(last.away - base.away)
        out[f"margin_chg_last{w}m"] = float((last.home - last.away) - (base.home - base.away))
    scoring = [p for p in plays if p.scoring]
    if len(scoring) >= 10:
        b = scoring[-10]
        out["margin_chg_last10_scores"] = float((last.home - last.away) - (b.home - b.away))
    return out


def game_row(
    snap: GameSnap, now: datetime, ours_home: float, home_ask: float | None, home_bid: float | None
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ts": now,
        "event_id": snap.event_id,
        "away": snap.away,
        "home": snap.home,
        "period": snap.period,
        "clock_secs": snap.clock_secs,
        "secs_left": seconds_left(snap.period, snap.clock_secs),
        "margin": snap.margin,
        "ours_home": ours_home,
        "espn_home": snap.espn_home_wp,
        "home_ask": home_ask,
        "home_bid": home_bid,
        "gap_ours_minus_ask": None if home_ask is None else ours_home - home_ask,
        "file_age_s": (now - snap.file_time).total_seconds(),
    }
    row.update(run_features(snap.plays))
    return row


def candles_from_trades(trades: dict[str, dict[int, list[dict[str, Any]]]]) -> pd.DataFrame:
    """trades: ticker -> minute-start unix -> trade dicts. One row per (ticker, minute)."""
    rows = []
    for ticker, by_min in trades.items():
        for minute, raw in sorted(by_min.items()):
            trs = sorted(raw, key=lambda t: str(t.get("created_time", "")))
            px = [float(t["yes_price_dollars"]) for t in trs]
            cnt = [float(t.get("count_fp", 1.0)) for t in trs]
            buy = sum(c for t, c in zip(trs, cnt, strict=True) if t.get("taker_side") == "yes")
            sell = sum(c for t, c in zip(trs, cnt, strict=True) if t.get("taker_side") == "no")
            tot = buy + sell
            rows.append(
                {
                    "ticker": ticker,
                    "minute": datetime.fromtimestamp(minute, UTC),
                    "open": px[0],
                    "high": max(px),
                    "low": min(px),
                    "close": px[-1],
                    "volume": sum(cnt),
                    "n_trades": len(trs),
                    "taker_yes_vol": buy,
                    "taker_no_vol": sell,
                    "imbalance": (buy - sell) / tot if tot else 0.0,
                }
            )
    return pd.DataFrame(rows)


def write_once(df: pd.DataFrame, path: Path) -> bool:
    if df.empty or path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, index=False)
    tmp.rename(path)
    return True


class FeatureLog:
    def __init__(self, view: CaptureView, out_dir: Path, date_key: str) -> None:
        self.view, self.out, self.date_key = view, out_dir, date_key
        self.pending: dict[str, dict[int, list[dict[str, Any]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self.seen_ids: set[str] = set()
        self.read_files: set[Path] = set()
        self.emitted_upto = 0  # unix minute already written
        for p in out_dir.glob("candles_*.parquet"):
            try:
                t = datetime.strptime(p.stem[len("candles_") :], STAMP_FMT).replace(tzinfo=UTC)
            except ValueError:
                continue
            self.emitted_upto = max(self.emitted_upto, int(t.timestamp()))

    def ingest_trades(self, now: datetime) -> int:
        n = 0
        for ts, path in self.view.files("kalshi_trades", self.date_key):
            if ts > now or path in self.read_files:
                continue
            self.read_files.add(path)
            body = read_json(path)
            if not isinstance(body, dict):
                continue
            for ticker, trs in body.items():
                for t in trs or []:
                    tid = str(t.get("trade_id", ""))
                    if tid in self.seen_ids:
                        continue
                    self.seen_ids.add(tid)
                    try:
                        c = datetime.fromisoformat(str(t["created_time"]).replace("Z", "+00:00"))
                    except (KeyError, ValueError):
                        continue
                    self.pending[ticker][int(c.timestamp()) // 60 * 60].append(t)
                    n += 1
        return n

    def flush_candles(self, now: datetime) -> bool:
        """Write candles of minutes strictly before now-1min; each minute is written once."""
        cutoff = int((now - timedelta(minutes=1)).timestamp()) // 60 * 60
        ready: dict[str, dict[int, list[dict[str, Any]]]] = {}
        top = self.emitted_upto
        for ticker, by_min in self.pending.items():
            for minute in [m for m in by_min if self.emitted_upto < m < cutoff]:
                ready.setdefault(ticker, {})[minute] = by_min.pop(minute)
                top = max(top, minute)
            for minute in [m for m in by_min if m <= self.emitted_upto]:
                by_min.pop(
                    minute
                )  # late trade for an already-written minute: dropped, not rewritten
        df = candles_from_trades(ready)
        if df.empty:
            return False
        stamp = datetime.fromtimestamp(top, UTC).strftime(STAMP_FMT)
        ok = write_once(df, self.out / f"candles_{stamp}.parquet")
        self.emitted_upto = top
        return ok

    def write_games(self, rows: list[dict[str, Any]], now: datetime) -> bool:
        if not rows:
            return False
        return write_once(pd.DataFrame(rows), self.out / f"games_{now.strftime(STAMP_FMT)}.parquet")
