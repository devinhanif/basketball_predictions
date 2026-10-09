"""Latest official injury report published BEFORE the earliest tip-off."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb

from nba.daily.schedule import ET
from nba.ingest.availability import (
    _flush_unmatched,
    build_name_resolver,
    official_report_url_candidates,
    probe_report_status,
    pull_official_injury_report,
)
from nba.ingest.cache import DEFAULT_DATA_DIR, RateLimiter

#: A probe returns True/"present" (exists), False/"absent" (definitely not there) or
#: "unknown" (timeout, 5xx, connection error). Only definite absence is ever cached.
ProbeFn = Callable[[str], bool | str]
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


#: A slot is cached as "no report" only once it is at least this old when probed: reports
#: land at/just after their slot time, so a newer miss may simply be early.
MISS_GRACE_MINUTES = 30
#: A cached definite miss is re-probed once it is this old (late-posted reports).
MISS_TTL_HOURS = 2


def _miss_cache_path(data_dir: Path, not_after_utc: datetime) -> Path:
    return (
        data_dir / "injury_probe_cache" / f"{to_report_dt(not_after_utc).date().isoformat()}.json"
    )


def _load_misses(path: Path) -> dict[str, datetime]:
    """slot ISO -> naive-UTC time the miss was recorded. Legacy list files carry no timestamp
    and are dropped (re-probed once)."""
    try:
        raw = json.loads(path.read_text())
        return {str(k): datetime.fromisoformat(str(v)) for k, v in raw.items()}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _save_misses(path: Path, misses: dict[str, datetime]) -> None:
    if not misses:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({k: v.isoformat() for k, v in sorted(misses.items())}))
    tmp.replace(path)


def _probe_state(probe: ProbeFn, url: str) -> str:
    r = probe(url)
    if r is True:
        return "present"
    if r is False:
        return "absent"
    return r if r in ("present", "absent") else "unknown"


def pull_latest_report(
    con: duckdb.DuckDBPyConnection,
    not_after_utc: datetime,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
    probe: ProbeFn | None = None,
    pull: PullFn | None = None,
    name_index: dict[str, int] | None = None,
    extra_games: Mapping[tuple[date, int, int], str] | None = None,
    extra_names: Sequence[tuple[str, int]] = (),
) -> datetime | None:
    """Probe slots newest-first; pull the first that exists. Returns its ET
    timestamp, or None if none found in the lookback window.

    Names are resolved by the team-aware ``NameResolver`` (full name from nba_api's bundled
    list, ties broken by team history) unless an explicit ``name_index`` is injected. The old
    default, ``build_merged_name_index``, built a last-name-keyed index from cached play-by-play
    that raises "ambiguous player names" on real data ('green', 'smith', ...), which made every
    live injury pull fail. Unresolved/unknown names are appended to
    ``<data_dir>/availability_backfill/unmatched.csv`` (an unmatched rate above 5% still raises).

    ``extra_games`` maps ``(ET date, home_id, away_id) -> game_id`` for the slate: upcoming games
    have no ``games`` row, so without it every pre-tip report row would carry a NULL game_id.
    ``extra_names`` (official-roster ``(name, id)`` pairs) lets debutants resolve: the bundled
    nba_api list lags new draft classes, so without it a rookie on the report is dropped."""
    if probe is None:
        probe = probe_report_status
    gid_kw: dict[str, object] = {"extra_games": extra_games} if extra_games else {}
    # per run-day negative cache: slots already known to have no report are not re-probed by
    # the next hourly run; newer slots (and slots too fresh to call missing) always are
    miss_path = _miss_cache_path(data_dir, not_after_utc)
    misses = _load_misses(miss_path)
    now_et = to_report_dt(not_after_utc)
    for slot in candidate_slots(not_after_utc):
        key = slot.isoformat()
        recorded = misses.get(key)
        if recorded is not None and not_after_utc - recorded < timedelta(hours=MISS_TTL_HOURS):
            continue
        hit_url: str | None = None
        all_absent = True
        for cand_url in official_report_url_candidates(slot):
            if rate_limiter is not None:
                rate_limiter.wait()
            state = _probe_state(probe, cand_url)
            if state == "present":
                hit_url = cand_url
                break
            if state != "absent":
                all_absent = False
        if hit_url is None:
            if all_absent and now_et - slot >= timedelta(minutes=MISS_GRACE_MINUTES):
                misses[key] = not_after_utc
            elif not all_absent:
                misses.pop(key, None)  # inconclusive probe: never trust an older miss either
            continue
        url = hit_url
        _save_misses(miss_path, misses)
        puller: PullFn = pull or pull_official_injury_report
        if name_index is not None:
            puller(
                con, slot, name_index=name_index, data_dir=data_dir,
                rate_limiter=rate_limiter, url=url, **gid_kw,
            )  # fmt: skip
            return slot
        resolver = build_name_resolver(
            con, data_dir, slot.date(), slot.date(), extra_names=extra_names
        )
        try:
            puller(
                con, slot, resolver=resolver, data_dir=data_dir,
                rate_limiter=rate_limiter, url=url, **gid_kw,
            )  # fmt: skip
        finally:
            _flush_unmatched(resolver, data_dir / "availability_backfill")
        return slot
    _save_misses(miss_path, misses)
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
