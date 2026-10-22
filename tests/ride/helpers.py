"""Build a tiny synthetic capture directory (same layout as data/raw/live_<date>/)."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

T0 = datetime(2026, 10, 23, 1, 30, 0, tzinfo=UTC)


def put(root: Path, source: str, key: str, ts: datetime, body: Any) -> Path:
    d = root / source / key
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{ts:%Y%m%dT%H%M%S}Z.json.gz"
    with gzip.open(p, "wb") as f:
        f.write(json.dumps(body).encode())
    return p


def summary(
    home_score: int,
    away_score: int,
    period: int,
    clock: str,
    espn_home_wp: float,
    state: str = "in",
) -> dict[str, Any]:
    teams = [
        {"homeAway": "home", "score": str(home_score), "team": {"id": "20", "abbreviation": "PHI"}},
        {"homeAway": "away", "score": str(away_score), "team": {"id": "5", "abbreviation": "CLE"}},
    ]
    return {
        "header": {
            "competitions": [
                {
                    "status": {"period": period, "displayClock": clock, "type": {"state": state}},
                    "competitors": teams,
                }
            ]
        },
        "plays": [
            {
                "id": "100",
                "period": {"number": period},
                "clock": {"displayValue": clock},
                "homeScore": home_score,
                "awayScore": away_score,
                "scoringPlay": True,
                "wallclock": "2026-10-23T01:29:00Z",
                "team": {"id": "20"},
            }
        ],
        "winprobability": [
            {"playId": "90", "homeWinPercentage": 0.5},
            {"playId": "100", "homeWinPercentage": espn_home_wp},
            {"playId": "999", "homeWinPercentage": 0.123},  # post-game junk entry: must be ignored
        ],
    }


def markets(ask: float = 0.97, bid: float = 0.96) -> dict[str, Any]:
    def m(team: str, a: float, b: float) -> dict[str, Any]:
        return {
            "ticker": f"KXNBAGAME-26OCT22CLEPHI-{team}",
            "event_ticker": "KXNBAGAME-26OCT22CLEPHI",
            "yes_ask_dollars": f"{a:.4f}",
            "yes_bid_dollars": f"{b:.4f}",
            "yes_ask_size_fp": "100.00",
            "yes_bid_size_fp": "50.00",
        }

    return {"markets": [m("PHI", ask, bid), m("CLE", 1 - bid, 1 - ask)], "cursor": ""}


def heartbeat(root: Path, last: datetime, status: str = "running") -> None:
    (root / "heartbeat.json").write_text(
        json.dumps({"status": status, "last_tick_ts": last.isoformat()})
    )


def build(
    root: Path,
    ts: datetime = T0,
    *,
    hs: int = 112,
    as_: int = 96,
    period: int = 4,
    clock: str = "3:10",
    wp: float = 0.985,
) -> None:
    put(root, "espn_summary", "401", ts, summary(hs, as_, period, clock, wp))
    put(root, "kalshi", "KXNBAGAME_p0", ts, markets())
    heartbeat(root, ts)
