"""Live score for halftime check-ins, read from the live capture's own files (read-only).

The capture (``data/raw/live_<ET date>/``) writes an ESPN scoreboard snapshot each minute and a
heartbeat. JARVIS reads the newest snapshot; if the heartbeat is older than ``stale_after_min`` the
check-in says the feed is stale rather than quote a score. No network, no write.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from nba.jarvis.config import JarvisConfig

ESPN_ABBR = {"UTAH": "UTA", "NY": "NYK", "GS": "GSW", "SA": "SAS", "NO": "NOP", "WSH": "WAS"}
STALE_AFTER_MIN = 6.0


@dataclass(frozen=True)
class LiveGame:
    away: str
    home: str
    away_score: int
    home_score: int
    period: int
    clock: str
    status: str  # STATUS_SCHEDULED | STATUS_IN_PROGRESS | STATUS_HALFTIME | STATUS_FINAL
    snapshot_ts: datetime | None

    @property
    def halftime(self) -> bool:
        return self.status == "STATUS_HALFTIME"

    @property
    def matchup(self) -> str:
        return f"{self.away}@{self.home}"


def live_dir(cfg: JarvisConfig, slate: date) -> Path:
    return cfg.root / "data" / "raw" / f"live_{slate.isoformat()}"


def feed_age_min(cfg: JarvisConfig, slate: date, now: datetime) -> float | None:
    hb = live_dir(cfg, slate) / "heartbeat.json"
    if not hb.exists():
        return None
    try:
        ts = datetime.fromisoformat(json.loads(hb.read_text())["last_tick_ts"])
    except (ValueError, KeyError):
        return None
    return (now.replace(tzinfo=UTC) - ts).total_seconds() / 60.0


def latest_scoreboard(cfg: JarvisConfig, slate: date) -> list[LiveGame]:
    d = live_dir(cfg, slate) / "espn_scoreboard" / slate.isoformat()
    files = sorted(d.glob("*.json.gz")) if d.exists() else []
    if not files:
        return []
    f = files[-1]
    try:
        with gzip.open(f) as fh:
            body = json.loads(fh.read())
        snap = datetime.strptime(f.name[:15], "%Y%m%dT%H%M%S")
    except (OSError, ValueError):
        return []
    out = []
    for ev in body.get("events", []):
        comp = ev["competitions"][0]
        by = {c["homeAway"]: c for c in comp["competitors"]}
        if "home" not in by or "away" not in by:
            continue

        def ab(c: dict) -> str:  # type: ignore[type-arg]
            a = str(c["team"]["abbreviation"])
            return ESPN_ABBR.get(a, a)

        st = ev.get("status", {})
        out.append(
            LiveGame(
                ab(by["away"]),
                ab(by["home"]),
                int(by["away"].get("score") or 0),
                int(by["home"].get("score") or 0),
                int(st.get("period") or 0),
                str(st.get("displayClock", "")),
                str(st.get("type", {}).get("name", "")),
                snap,
            )  # fmt: skip
        )
    return out
