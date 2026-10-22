"""Gate replay: run the scan screen over a captured slate (read-only) and report the real fill rate.

For each scan time (75 min before the first tip, 45 min before each later window) it takes the
newest captured Kalshi page at or before that time and the stored forecasts as of that time, then
runs the same ``run_scan`` into a throwaway DuckDB file. Prices are the listing quotes from the
capture (no order books were captured at that hour), so fills are an upper bound on book fills.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from nba.live import kalshi_pull as kp
from nba.papertrade import db
from nba.papertrade.config import PaperConfig
from nba.papertrade.forecasts import Forecasts
from nba.papertrade.runner import scan_times
from nba.papertrade.scan import run_scan


def snapshot_markets(raw: Path, series: list[str], at: datetime, day: date) -> list[dict[str, Any]]:
    """Newest captured page per series at or before ``at`` (naive UTC), filtered to the slate."""
    stamp = at.strftime("%Y%m%dT%H%M%S")
    out: list[dict[str, Any]] = []
    for s in series:
        for pdir in sorted((raw / "kalshi").glob(f"{s}_p*")):
            files = [f for f in sorted(pdir.glob("*.json.gz")) if f.name[:15] <= stamp]
            if not files:
                continue
            body = json.loads(gzip.decompress(files[-1].read_bytes()))
            kept, _ = kp.filter_for_date([m for m in body.get("markets", [])], day)
            out += kept
    return out


def replay_day(
    day: date,
    raw: Path,
    tips: list[datetime],
    cfg: PaperConfig,
    aliases: dict[str, int],
    load_fc: Any,
    tmp_db: Path,
) -> dict[str, Any]:
    """``load_fc(now) -> Forecasts``. Returns slot fills and screen counts per scan."""
    series = [*cfg.prop_series, "KXNBAGAME"]
    scans = []
    for t in scan_times(tips):
        at = t.replace(tzinfo=None) if t.tzinfo else t
        markets = snapshot_markets(raw, series, at, day)
        fc: Forecasts = load_fc(at)
        res = run_scan(
            day, at, cfg, markets=markets, fc=fc, aliases=aliases,
            db_path=tmp_db, legs_file=tmp_db.with_name("legs.json"),
        )  # fmt: skip
        scans.append(
            {
                "at_utc": at.replace(tzinfo=UTC).isoformat(),
                "markets": res.n_markets,
                "passing_legs": res.n_candidates,
                "new_positions": len(res.positions),
                "verdict": res.verdict,
                "unmatched_names": len(res.unmatched),
                "slots_without_plan": res.notes.get("slots_without_plan", []),
                "skipped_markets": res.notes.get("skipped_markets", {}),
            }
        )
    with db.reader(tmp_db) as con:
        by_slot = dict(
            con.execute(
                "SELECT slot, count(*) FROM positions WHERE role = 'strategy' GROUP BY slot"
            ).fetchall()
        )
        placebo = con.execute("SELECT count(*) FROM positions WHERE role = 'placebo'").fetchone()
        reasons = dict(
            con.execute("SELECT reason, count(*) FROM candidates GROUP BY reason").fetchall()
        )
    return {
        "date": day.isoformat(),
        "scans": scans,
        "strategy_positions_by_slot": by_slot,
        "placebo_positions": int(placebo[0]) if placebo else 0,
        "candidate_reasons_all_scans": reasons,
    }
