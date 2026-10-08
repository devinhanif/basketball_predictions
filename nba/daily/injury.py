"""Latest official injury report published BEFORE the earliest tip-off."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

from nba.daily.schedule import ET
from nba.ingest.availability import (
    build_merged_name_index,
    official_report_url_candidates,
    pull_official_injury_report,
)
from nba.ingest.cache import DEFAULT_DATA_DIR, RateLimiter

ProbeFn = Callable[[str], bool]
PullFn = Callable[..., object]

LOOKBACK_HOURS = 12
SLOT_MINUTES = 15
MAX_REPORT_AGE_HOURS = 36


def to_report_dt(ts_utc: datetime) -> datetime:
    """Naive-UTC instant -> naive US-Eastern wall clock (the report filename clock)."""
    return ts_utc.replace(tzinfo=UTC).astimezone(ET).replace(tzinfo=None)


def candidate_slots(not_after_utc: datetime) -> list[datetime]:
    """Report timestamps (naive ET) at 15-min slots, newest first, all STRICTLY
    before ``not_after_utc`` (the earlier of now and the first tip-off)."""
    limit = to_report_dt(not_after_utc)
    floored = limit.replace(minute=(limit.minute // SLOT_MINUTES) * SLOT_MINUTES, second=0)
    if floored >= limit:
        floored -= timedelta(minutes=SLOT_MINUTES)
    n = LOOKBACK_HOURS * 60 // SLOT_MINUTES
    return [floored - timedelta(minutes=SLOT_MINUTES * i) for i in range(n)]


def pull_latest_report(
    con: duckdb.DuckDBPyConnection,
    not_after_utc: datetime,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
    probe: ProbeFn | None = None,
    pull: PullFn | None = None,
    name_index: dict[str, int] | None = None,
) -> datetime | None:
    """Probe slots newest-first; pull the first that exists. Returns its ET
    timestamp, or None if none found in the lookback window."""
    if probe is None:
        from nba.ingest.availability import probe_report_url as probe_fn

        probe = probe_fn
    for slot in candidate_slots(not_after_utc):
        for url in official_report_url_candidates(slot):
            if rate_limiter is not None:
                rate_limiter.wait()
            if not probe(url):
                continue
            idx = name_index if name_index is not None else build_merged_name_index(data_dir)
            puller = pull or pull_official_injury_report
            puller(con, slot, name_index=idx, data_dir=data_dir, rate_limiter=rate_limiter, url=url)
            return slot
    return None


def out_players(con: duckdb.DuckDBPyConnection, as_of_utc: datetime) -> set[int]:
    """Players marked 'out' in the single latest report (as_of <= ``as_of_utc``, <=36h old).

    ``player_availability.as_of`` is the report's ET wall clock; compare in ET.
    """
    cutoff = to_report_dt(as_of_utc)
    try:
        row = con.execute(
            "SELECT max(as_of) FROM player_availability "
            "WHERE source = 'nba_official_report' AND as_of <= ?",
            [cutoff],
        ).fetchone()
        latest = row[0] if row else None
        if latest is None or latest < cutoff - timedelta(hours=MAX_REPORT_AGE_HOURS):
            return set()
        rows = con.execute(
            "SELECT DISTINCT player_id FROM player_availability "
            "WHERE source = 'nba_official_report' AND as_of = ? AND status = 'out' "
            "AND player_id IS NOT NULL",
            [latest],
        ).fetchall()
    except duckdb.CatalogException:
        return set()
    return {int(r[0]) for r in rows}
