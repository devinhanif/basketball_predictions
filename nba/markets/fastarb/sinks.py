"""Write-once raw files, the ALERTS.md sink with a dedup key, and the heartbeat.

Nothing here opens a DuckDB file. ALERTS.md is appended with one O_APPEND write per line (the same
file the watchdog and the launchers append to). Alert text is phone-length and names the exact buys;
there is no stake, no size recommendation and no execution anywhere.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from nba.live.store import write_json_atomic
from nba.markets.fastarb.arb import ArbResult, LagResult
from nba.markets.fastarb.venues import PM_INTL

JOB = "fastarb"


class RawWriter:
    """``<root>/fastarb_<ET date>/<HHMMSS>_<venue>_<label>.json.gz``; never replaces a file."""

    def __init__(self, root: Path, day: str) -> None:
        self.dir = root / f"fastarb_{day}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.files = 0
        self.bytes_gz = 0

    def write(self, ts: datetime, venue: str, label: str, payload: Any) -> Path:
        base = f"{ts:%H%M%S}_{venue}_{label}"
        n = 0
        while True:
            path = self.dir / (f"{base}.json.gz" if n == 0 else f"{base}_{n}.json.gz")
            try:
                with open(path, "xb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as gz:
                    gz.write(json.dumps(payload, separators=(",", ":")).encode())
                break
            except FileExistsError:
                n += 1
        self.files += 1
        self.bytes_gz += path.stat().st_size
        return path


RESOLUTION_RISK = "RESOLUTION RISK: postponement/OT rules differ by venue, not verified"


def alert_key(*parts: object) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:8]


def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    """One O_APPEND line per record (candidates.jsonl: each candidate and its confirm)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(obj, sort_keys=True, default=str) + "\n")


class AlertSink:
    def __init__(self, alerts_path: Path, dedup_path: Path, dedup_minutes: float) -> None:
        self.alerts_path = alerts_path
        self.dedup_path = dedup_path
        self.window = timedelta(minutes=dedup_minutes)
        try:
            raw = json.loads(dedup_path.read_text())
        except (OSError, ValueError):
            raw = {}
        self.seen: dict[str, datetime] = {k: datetime.fromisoformat(v) for k, v in raw.items()}
        self.written = 0
        self.suppressed = 0

    def emit(self, key: str, text: str, now: datetime, stamp: str) -> bool:
        """Append ``- <stamp> [fastarb] <text> key=<key>`` unless ``key`` was written recently."""
        last = self.seen.get(key)
        if last is not None and now - last < self.window:
            self.suppressed += 1
            return False
        self.seen[key] = now
        self.seen = {k: v for k, v in self.seen.items() if now - v < 4 * self.window}
        write_json_atomic(self.dedup_path, {k: v.isoformat() for k, v in self.seen.items()})
        self.alerts_path.parent.mkdir(parents=True, exist_ok=True)
        line = f"- {stamp} [{JOB}] {text} key={key}\n"
        with open(self.alerts_path, "a") as f:
            f.write(line)
        self.written += 1
        return True


def _c(x: float) -> str:
    return f"{x * 100:.0f}c"


def arb_text(r: ArbResult, *, gap_s: float | None = None, rules_verified: bool = False) -> str:
    depth = f"depth ~{r.max_n}" if r.depth_known else "depth unknown"
    intl = " INTL-ONLY (not tradable from the US, no buy)" if PM_INTL in r.venues else ""
    return (
        f"ARB {r.prop.game.label()} {r.prop.label()}: BUY {r.yes_ask.short} @{r.yes_ask.price:.2f}"
        f" + BUY {r.no_ask.short} @{r.no_ask.price:.2f} = {r.yes_ask.price + r.no_ask.price:.2f};"
        f" net {r.margin * 100:+.1f}c/ct after fees at {r.n_eval} ct, {depth}{intl}"
        + (f"; legs re-fetched {gap_s:.1f}s apart" if gap_s is not None else "")
        + ("" if rules_verified else f" | {RESOLUTION_RISK}")
    )


def arb_key(r: ArbResult) -> str:
    return alert_key("arb", r.prop.game, r.prop.kind, r.prop.c, r.yes_ask.ref, r.no_ask.ref)


def lag_text(r: LagResult, news: str, minutes: float) -> str:
    return (
        f"LAG {r.prop.game.label()} {r.prop.label()}: {r.ask.short} @{r.ask.price:.2f} vs Pinnacle"
        f" no-vig {r.pinnacle_p:.3f} (edge {_c(r.edge)}, {_c(r.net_edge)} after fee) {minutes:.0f}m"
        f" after {news}"
    )


def lag_key(r: LagResult, news: str) -> str:
    return alert_key("lag", r.prop.game, r.prop.kind, r.prop.c, r.ask.ref, r.side, news)


def beat(path: Path, state: dict[str, Any]) -> None:
    write_json_atomic(path, {"job": JOB, **state})
