"""Official injury-report PDFs for the history seasons (2019-20 .. 2021-22).

Two phases, because the history DB has no games / box scores until the stats.nba.com queue
reaches them:

* ``fetch`` -- calendar-driven (no DB needed). Downloads the PDFs from the ak-static CDN
  (official.nba.com's own host, NOT stats.nba.com, so it does not touch that quota) at
  >= 2 s/request into ``data/history/availability_official/``. One manifest per date under
  ``data/history/availability_backfill/<date>.json`` makes it resumable; a done date is never
  re-probed. Every hour-only snapshot found is kept (``as_of`` = its publish hour), so a
  consumer can gate on the real tip (``as_of <= tip``) exactly as in production.
* ``load`` -- parses cached PDFs into the HISTORY DB's ``player_availability``
  (``source='nba_official_report'``, delete-then-insert per ``as_of``) with a history-aware
  :class:`~nba.parse.availability.NameResolver` (team-as-of from history box scores). Refuses to
  run until the history DB has box scores (fail loud, never a silent partial resolve).

Cadence (probed 2019-11-15, 2020-08-05, 2021-01-15, 2021-11-15, 2022-03-15): always the
hour-only filename (``Injury-Report_YYYY-MM-DD_01PM.pdf``). 2019-20/2020-21: 3 snapshots/day
(01PM, 05PM, 08PM; bubble: 11AM, 02PM, 05PM). 2021-22: hourly 06AM-11PM. So each date is probed
at seed hours first; only if the seeds show the hourly regime are the remaining hours probed.

Never writes ``nba.duckdb``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import httpx

from nba.ingest.availability import (
    _OFFICIAL_REPORT_BASE_URL,
    ensure_player_availability_table,
    official_report_url_hour_only,
)
from nba.ingest.cache import DEFAULT_DATA_DIR, RateLimiter, insert_rows
from nba.parse.availability import AVAILABILITY_SCHEMA, NameResolver, parse_official_injury_report

HISTORY_DIR = DEFAULT_DATA_DIR / "history"
HISTORY_DB = HISTORY_DIR / "nba_history.duckdb"
RAW_SUBDIR = "availability_official"
MANIFEST_SUBDIR = "availability_backfill"

#: Date windows per season int (inclusive), with the COVID stoppage skipped for 2019.
SEASON_WINDOWS: dict[int, list[tuple[date, date]]] = {
    2019: [(date(2019, 10, 20), date(2020, 3, 12)), (date(2020, 7, 20), date(2020, 10, 12))],
    2020: [(date(2020, 12, 10), date(2021, 7, 23))],
    2021: [(date(2021, 10, 10), date(2022, 6, 17))],
}
#: Probed first for every date; the rest of the day only if these show the hourly regime.
SEED_HOURS: tuple[int, ...] = (11, 13, 14, 17, 20)
ALL_HOURS: tuple[int, ...] = tuple(range(6, 24))
_ABSENT_STATUS = (403, 404)


def season_dates(season: int) -> list[date]:
    out: list[date] = []
    for lo, hi in SEASON_WINDOWS[season]:
        out += [lo + timedelta(days=i) for i in range((hi - lo).days + 1)]
    return out


def is_hourly_regime(hit_hours: Iterable[int]) -> bool:
    """11AM, 1PM and 2PM all published => hourly cadence (early eras never have all three)."""
    s = set(hit_hours)
    return {11, 13, 14} <= s


def report_dt_from_filename(name: str) -> datetime:
    """Inverse of ``official_report_url_hour_only`` (``Injury-Report_2021-11-15_09AM.pdf``)."""
    stem = name.removesuffix(".pdf").removeprefix("Injury-Report_")
    return datetime.strptime(stem, "%Y-%m-%d_%I%p")


@dataclass
class FetchResult:
    dates_total: int = 0
    dates_skipped: int = 0
    dates_done: int = 0
    dates_failed: int = 0
    dates_no_report: int = 0
    pdfs_saved: int = 0
    requests: int = 0
    failures: list[str] = field(default_factory=list)


class _TransientError(RuntimeError):
    pass


def _get_pdf(client: httpx.Client, url: str) -> bytes | None:
    """PDF bytes, None when the object is absent, ``_TransientError`` on anything else."""
    try:
        resp = client.get(url, timeout=20.0)
    except httpx.HTTPError as exc:
        raise _TransientError(f"{type(exc).__name__}: {exc}") from exc
    if resp.status_code in _ABSENT_STATUS:
        return None
    if resp.status_code != 200:
        raise _TransientError(f"HTTP {resp.status_code} for {url}")
    # Some CDNs answer 200 with an HTML placeholder for a missing object.
    return bytes(resp.content) if resp.content[:4] == b"%PDF" else None


def fetch_date(
    d: date,
    *,
    client: httpx.Client,
    raw_dir: Path,
    manifest_dir: Path,
    rate_limiter: RateLimiter,
    counter: list[int],
) -> list[str]:
    """Probe + download one date; write its manifest. Returns saved/cached PDF basenames."""

    def grab(hour: int) -> bool:
        url = official_report_url_hour_only(datetime(d.year, d.month, d.day, hour))
        name = url.rsplit("/", 1)[-1]
        path = raw_dir / name
        if path.exists():
            return True
        rate_limiter.wait()
        counter[0] += 1
        content = _get_pdf(client, url)
        if content is None:
            return False
        path.write_bytes(content)
        return True

    hit_hours = [h for h in SEED_HOURS if grab(h)]
    if is_hourly_regime(hit_hours):
        hit_hours = [h for h in ALL_HOURS if h in hit_hours or grab(h)]
    names = [
        official_report_url_hour_only(datetime(d.year, d.month, d.day, h)).rsplit("/", 1)[-1]
        for h in hit_hours
    ]
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"{d.isoformat()}.json").write_text(
        json.dumps(
            {
                "date": d.isoformat(),
                "reports": names,
                "regime": "hourly" if is_hourly_regime(hit_hours) else "sparse",
                "base_url": _OFFICIAL_REPORT_BASE_URL,
                "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )
    )
    return names


def fetch_seasons(
    seasons: Sequence[int],
    *,
    data_dir: Path = HISTORY_DIR,
    rate_limit_s: float = 2.0,
    client: httpx.Client | None = None,
    dates: Sequence[date] | None = None,
    progress_every: int = 10,
    max_consecutive_errors: int = 8,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchResult:
    """Resumable fetch of every date in ``seasons`` (or an explicit ``dates`` list)."""
    raw_dir = data_dir / RAW_SUBDIR
    manifest_dir = data_dir / MANIFEST_SUBDIR
    raw_dir.mkdir(parents=True, exist_ok=True)
    todo = list(dates) if dates is not None else [d for s in seasons for d in season_dates(s)]
    res = FetchResult(dates_total=len(todo))
    limiter = RateLimiter(rate_limit_s)
    owns = client is None
    c = client if client is not None else httpx.Client()
    counter = [0]
    consecutive = 0
    t0 = time.monotonic()
    try:
        for i, d in enumerate(todo, 1):
            if (manifest_dir / f"{d.isoformat()}.json").exists():
                res.dates_skipped += 1
                continue
            try:
                names = fetch_date(
                    d,
                    client=c,
                    raw_dir=raw_dir,
                    manifest_dir=manifest_dir,
                    rate_limiter=limiter,
                    counter=counter,
                )
                consecutive = 0
                res.pdfs_saved += len(names)
                if names:
                    res.dates_done += 1
                else:
                    res.dates_no_report += 1
            except _TransientError as exc:
                res.dates_failed += 1
                res.failures.append(f"{d}: {exc}"[:200])
                consecutive += 1
                print(f"FAILED {d}: {exc}"[:240], flush=True)
                if consecutive >= max_consecutive_errors:
                    print("too many consecutive errors; stopping (rerun resumes)", flush=True)
                    break
                sleep(30.0 * consecutive)
            if progress_every and i % progress_every == 0:
                el = max(time.monotonic() - t0, 1e-9)
                rate = counter[0] / el
                left = sum(
                    1 for x in todo[i:] if not (manifest_dir / f"{x.isoformat()}.json").exists()
                )
                print(
                    f"PROGRESS history-injury {i}/{len(todo)} done={res.dates_done} "
                    f"no_report={res.dates_no_report} failed={res.dates_failed} "
                    f"pdfs={res.pdfs_saved} req={counter[0]} rate={rate:.2f}/s "
                    f"dates_left={left}",
                    flush=True,
                )
    finally:
        res.requests = counter[0]
        if owns:
            c.close()
    return res


# --------------------------------------------------------------------------
# load (history DB only)
# --------------------------------------------------------------------------


def build_history_resolver(
    con: duckdb.DuckDBPyConnection,
    data_dir: Path = HISTORY_DIR,
    *,
    names: Sequence[tuple[str, int]] | None = None,
) -> NameResolver:
    """History-aware resolver: team-as-of from the HISTORY DB's box scores.

    Raises ``RuntimeError`` if the history DB has no ``player_game_stats`` rows yet (box scores
    not ingested): resolving without team-as-of would silently drop ambiguous names.
    """
    from nba.ingest.availability import build_name_resolver

    n = con.execute("SELECT count(*) FROM player_game_stats").fetchone()
    if not n or int(n[0]) == 0:
        raise RuntimeError(
            "history DB has no player_game_stats yet (hist:boxscore not run); "
            "PDFs stay cached, rerun `load` after box scores exist"
        )
    return build_name_resolver(con, data_dir, names=names, pad_days=60)


def load_cached_reports(
    con: duckdb.DuckDBPyConnection,
    *,
    data_dir: Path = HISTORY_DIR,
    resolver: NameResolver | None = None,
    start: date | None = None,
    end: date | None = None,
    max_unmatched_rate: float = 0.05,
    progress_every: int = 200,
) -> dict[str, int]:
    """Parse every cached PDF in [start, end] into ``player_availability`` (history DB)."""
    ensure_player_availability_table(con)
    if resolver is None:
        resolver = build_history_resolver(con, data_dir)
    files = sorted((data_dir / RAW_SUBDIR).glob("Injury-Report_*.pdf"))
    stats = {"files": 0, "rows": 0, "failed": 0}
    for i, path in enumerate(files, 1):
        rdt = report_dt_from_filename(path.name)
        if (start and rdt.date() < start) or (end and rdt.date() > end):
            continue
        try:
            df = parse_official_injury_report(
                path,
                {},
                report_dt=rdt,
                con=con,
                resolver=resolver,
                max_unmatched_rate=max_unmatched_rate,
            )
        except Exception as exc:
            stats["failed"] += 1
            print(f"FAILED {path.name}: {type(exc).__name__}: {str(exc)[:160]}", flush=True)
            continue
        con.execute(
            "DELETE FROM player_availability WHERE source = 'nba_official_report' AND as_of = ?",
            [rdt],
        )
        if not df.is_empty():
            insert_rows(con, "player_availability", AVAILABILITY_SCHEMA, df)
        stats["files"] += 1
        stats["rows"] += len(df)
        if progress_every and i % progress_every == 0:
            print(
                f"LOAD {i}/{len(files)} rows={stats['rows']} failed={stats['failed']}", flush=True
            )
    return stats


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.ingest.history_injury")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download PDFs for history seasons (CDN, ~1 req/2s)")
    f.add_argument("--season", type=int, action="append", choices=sorted(SEASON_WINDOWS))
    f.add_argument("--rate-limit-s", type=float, default=2.0)
    ld = sub.add_parser("load", help="parse cached PDFs into the history DB")
    ld.add_argument("--db", type=Path, default=HISTORY_DB)
    ld.add_argument("--max-unmatched-rate", type=float, default=0.05)
    args = ap.parse_args(argv)
    if args.cmd == "fetch":
        seasons = args.season or [2021, 2020, 2019]
        res = fetch_seasons(seasons, rate_limit_s=max(args.rate_limit_s, 1.0))
        print(
            f"history-injury fetch: dates={res.dates_total} skipped={res.dates_skipped} "
            f"done={res.dates_done} no_report={res.dates_no_report} failed={res.dates_failed} "
            f"pdfs={res.pdfs_saved} requests={res.requests}"
        )
        return 1 if res.dates_failed else 0
    from nba.db.connect import connect

    hcon = connect(args.db)
    out = load_cached_reports(hcon, max_unmatched_rate=args.max_unmatched_rate)
    print(f"history-injury load: {out}")
    return 1 if out["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
