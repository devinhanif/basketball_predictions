"""Price history for tracked Kalshi tickers: raw capture (read-only) plus this API's samples.

The capture is gzip JSON, one file per series per tick, at
``<dir>/kalshi/<SERIES>_p<N>/<UTC ts>.json.gz`` holding ``{"markets": [...]}``
with ``*_dollars`` prices. The directory comes from ``APP_LIVE_CAPTURE_DIR`` and is
only ever opened for reading. Files are parsed once per (file, ticker set) and kept
in memory; later requests parse only new files. Without the variable, history
starts when the first price is sampled.
"""

from __future__ import annotations

import gzip
import json
import os
import re
from collections import defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

Json = dict[str, Any]

ENV = "APP_LIVE_CAPTURE_DIR"
MAX_SAMPLES = 2000
MAX_POINTS = 600
FILE_RE = re.compile(r"^(\d{8}T\d{6})Z\.json\.gz$")

_samples: dict[str, deque[Json]] = defaultdict(lambda: deque(maxlen=MAX_SAMPLES))
_capture: dict[str, dict[str, Json]] = defaultdict(dict)  # ticker -> ts -> point
_seen: dict[str, set[str]] = defaultdict(set)  # "series|ticker-set" -> files parsed


def reset() -> None:
    _samples.clear()
    _capture.clear()
    _seen.clear()


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None  # noqa: PLR0124


def record_samples(markets: list[Json], at: str | None = None) -> None:
    """Remember the current bid and ask of each found market (called after a fresh fetch)."""
    t = at or datetime.now(UTC).isoformat(timespec="seconds")
    for m in markets:
        if not m.get("found") or (m["yes_bid"] is None and m["yes_ask"] is None):
            continue
        dq = _samples[m["ticker"]]
        if dq and dq[-1]["t"] == t:
            continue
        dq.append({"t": t, "yes_bid": m["yes_bid"], "yes_ask": m["yes_ask"], "src": "sample"})


def capture_dir() -> Path | None:
    raw = os.environ.get(ENV)
    return Path(raw) if raw else None


def _ts(name: str) -> str | None:
    m = FILE_RE.match(name)
    if not m:
        return None
    return (
        datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")
        .replace(tzinfo=UTC)
        .isoformat(timespec="seconds")
    )


def load_capture(tickers: list[str]) -> int:
    """Parse capture files not yet read for these tickers. Returns the number of files parsed."""
    root = capture_dir()
    if root is None or not (root / "kalshi").is_dir():
        return 0
    parsed = 0
    by_series: dict[str, list[str]] = defaultdict(list)
    for t in tickers:
        by_series[t.split("-")[0]].append(t)
    for series, wanted in by_series.items():
        key = series + "|" + ",".join(sorted(wanted))
        want = set(wanted)
        for d in sorted((root / "kalshi").glob(f"{series}_p*")):
            for f in sorted(d.iterdir()):
                ts = _ts(f.name)
                token = f"{d.name}/{f.name}"
                if ts is None or token in _seen[key]:
                    continue
                _seen[key].add(token)
                try:
                    with gzip.open(f, "rt", encoding="utf-8") as fh:
                        body = json.load(fh)
                except (OSError, ValueError, EOFError):
                    continue  # a half-written or damaged file is skipped, never fatal
                parsed += 1
                for m in body.get("markets") or []:
                    if m.get("ticker") in want:
                        _capture[m["ticker"]][ts] = {
                            "t": ts,
                            "yes_bid": _f(m.get("yes_bid_dollars")),
                            "yes_ask": _f(m.get("yes_ask_dollars")),
                            "src": "capture",
                        }
    return parsed


def history(tickers: list[str]) -> Json:
    load_capture(tickers)
    out: dict[str, list[Json]] = {}
    for t in tickers:
        pts = {p["t"]: p for p in _capture.get(t, {}).values()}
        for p in _samples.get(t, ()):
            pts.setdefault(p["t"], p)
        rows = [pts[k] for k in sorted(pts)]
        if len(rows) > MAX_POINTS:
            step = len(rows) / MAX_POINTS
            rows = [rows[int(i * step)] for i in range(MAX_POINTS - 1)] + [rows[-1]]
        out[t] = rows
    return {
        "capture": capture_dir() is not None,
        "source": "Kalshi public prices (raw capture where present, else sampled here)",
        "series": out,
    }
