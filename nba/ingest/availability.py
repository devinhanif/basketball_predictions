"""Pull/load player availability (injury/inactive) status into ``player_availability``.

Two sources feed this table (see the leakage-contract comment on
``player_availability`` in ``nba/db/schema.sql`` and
``docs/INJURY_FEED_2026-10-08.md`` for the full design rationale):

1. ``nba_inactive_list`` -- nba_api's ``BoxScoreSummaryV2`` InactivePlayers
   result set, pulled per game_id exactly like ``nba/ingest/boxscores.py``.
   **Not forward-looking**: it only exists once the game itself is in the
   NBA Stats API, i.e. at/after that game's own tip-off. Useful as a
   historical as-of signal for OTHER (later) games, e.g. cold-start /
   role-change detection -- never as a predictor of the game it describes,
   and never usable to forecast a future game.
2. ``manual_announced`` -- human-curated injury-report snapshots (JSON
   records with only a player name, no stable id). This path exists so any
   human-curated snapshot can plug in without a schema change.
3. ``nba_official_report`` -- the NBA's real official pregame injury report
   (a PDF published outside nba_api, see ``pull_official_injury_report``
   below and ``docs/INJURY_FEED_2026-10-08.md``). This is the genuinely
   forward-looking source: teams report by 5pm local the day before a game,
   with updates up to ~30 minutes before tip. Like ``manual_announced``,
   names only (no stable id) -- resolved via
   ``nba.parse.availability.resolve_player_id``, fails loudly on unmatched
   names.

All three paths store a raw snapshot for replay (JSON under
``data/availability_raw/`` / ``data/availability_manual/``, raw PDF bytes
under ``data/availability_official/`` respectively) in addition to the
normalized rows loaded into DuckDB, and are resumable: the nba_api path
reuses ``fetch_cached``'s ``ingest_log`` bookkeeping (never refetches a
cached game_id); the manual and official-report paths use the same
``ingest_log`` table keyed by file path / report filename (never
reprocesses an already-loaded file, never re-downloads an already-fetched
report).

nba_api is imported lazily so this module is safe to import with no network
access; so is ``httpx`` (official-report download) and ``pdfplumber``
(official-report parsing, via ``nba.parse.availability``).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
import polars as pl

from nba.ingest.cache import (
    DEFAULT_DATA_DIR,
    RateLimiter,
    fetch_cached,
    insert_rows,
    is_cached,
    mark_done,
    mark_failed,
    open_db,
)
from nba.parse.availability import (
    AVAILABILITY_SCHEMA,
    NameResolver,
    build_name_index_from_pbp_frames,
    build_name_index_from_static_players,
    normalize_inactive_frame,
    parse_manual_announcements,
    parse_official_injury_report,
)

if TYPE_CHECKING:
    import httpx

SOURCE_INACTIVE = "availability"
SOURCE_MANUAL = "availability-manual"
SOURCE_OFFICIAL_REPORT = "availability-official-report"
SOURCE_OFFICIAL_REPORT_BACKFILL = "availability-official-report-backfill"

#: Max attempts for the live fetch before giving up (see boxscores.py --
#: same short-backoff retry pattern).
_MAX_FETCH_ATTEMPTS = 3
_RETRY_BACKOFF_S = 1.0

#: URL template for the official injury-report PDF (verified against the
#: real published filenames, see the task note this module was built from):
#: ``Injury-Report_YYYY-MM-DD_HH_MMAM.pdf`` with a zero-padded 12-hour time.
_OFFICIAL_REPORT_BASE_URL = "https://ak-static.cms.nba.com/referee/injury"


def _raw_json_path(game_id: str, data_dir: Path) -> Path:
    return data_dir / "availability_raw" / f"{game_id}.json"


def _fetch_inactive_for_game(game_id: str, *, data_dir: Path) -> pl.DataFrame:
    """Fetch one game's InactivePlayers (+ tip-off date) via BoxScoreSummaryV2.

    ``get_data_frames()`` result sets (verified against nba_api's
    BoxScoreSummaryV2 docs/source): index 0 is GameSummary (has
    ``GAME_DATE_EST``), index 3 is InactivePlayers (has ``PLAYER_ID``,
    ``FIRST_NAME``, ``LAST_NAME`` -- no injury reason on this result set).
    Raw frames are stashed to JSON before normalization so the snapshot can
    be replayed without a network call.
    """
    from nba_api.stats.endpoints import boxscoresummaryv2  # lazy import

    box = boxscoresummaryv2.BoxScoreSummaryV2(game_id=game_id)
    frames = box.get_data_frames()
    game_summary_raw = frames[0]
    inactive_raw = frames[3]

    raw_path = _raw_json_path(game_id, data_dir)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        json.dumps(
            {
                "game_summary": game_summary_raw.to_dict(orient="records"),
                "inactive_players": inactive_raw.to_dict(orient="records"),
            },
            default=str,
        )
    )

    tipoff = _tipoff_date_from_game_summary(game_summary_raw)
    pulled_at = datetime.now(UTC).replace(tzinfo=None)
    return normalize_inactive_frame(
        pl.from_pandas(inactive_raw),
        game_id=game_id,
        tipoff_as_of=tipoff,
        pulled_at=pulled_at,
    )


def _tipoff_date_from_game_summary(game_summary_raw: object) -> str:
    """Extract the 'YYYY-MM-DD' tip-off date from a raw GameSummary frame.

    Approximated at date granularity (nba_api does not expose a reliable
    cross-season tip-off *time* field on this result set) -- see the
    leakage-contract comment on ``player_availability`` in schema.sql and
    docs/INJURY_FEED_2026-10-08.md for why this is still a safe (if coarse)
    as_of bound.
    """
    import pandas as pd  # nba_api's own frames are pandas; no new dependency

    assert isinstance(game_summary_raw, pd.DataFrame)
    if game_summary_raw.empty:
        raise ValueError("empty GameSummary frame -- cannot determine tip-off date")
    raw = str(game_summary_raw.iloc[0]["GAME_DATE_EST"])
    return raw[:10]


def _fetch_inactive_with_retry(game_id: str, *, data_dir: Path) -> pl.DataFrame:
    """Retry the live fetch a few times before giving up (see boxscores.py).

    A game with zero inactive players is a legitimate response (not every
    game has inactives), so this only retries on an outright empty/error
    *frame set*, not on an empty-but-valid InactivePlayers list.
    """
    for attempt in range(_MAX_FETCH_ATTEMPTS):
        try:
            return _fetch_inactive_for_game(game_id, data_dir=data_dir)
        except Exception:
            if attempt == _MAX_FETCH_ATTEMPTS - 1:
                raise
            time.sleep(_RETRY_BACKOFF_S)
    raise AssertionError("unreachable")  # loop always returns or raises


def ensure_player_availability_table(con: duckdb.DuckDBPyConnection) -> None:
    """Idempotent safety net: schema.sql already creates the table; this is a
    no-op guard for connections opened before this module existed in a given
    session (mirrors the ``ensure_*`` pattern in boxscores.py/national_tv.py).
    """
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS player_availability (
            player_id INT, as_of TIMESTAMP, game_id VARCHAR, status VARCHAR,
            reason VARCHAR, source VARCHAR, pulled_at TIMESTAMP
        )
        """
    )


def _replace_rows(
    con: duckdb.DuckDBPyConnection, df: pl.DataFrame, *, game_id: str, source: str
) -> None:
    """Idempotent delete-then-insert keyed on (game_id, source), same pattern
    as ``upsert_player_game_stats`` (the table has no primary key)."""
    con.execute(
        "DELETE FROM player_availability WHERE game_id = ? AND source = ?", [game_id, source]
    )
    if df.is_empty():
        return
    insert_rows(con, "player_availability", AVAILABILITY_SCHEMA, df)


def pull_game_availability(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
) -> pl.DataFrame:
    """Fetch (or load cached) inactive-player list for one game; load into
    ``player_availability`` with ``source='nba_inactive_list'``.

    Never refetches an already-'done' (source, game_id) pair (resumability
    via ``fetch_cached``'s ``ingest_log`` bookkeeping, exactly like
    ``pull_game_boxscore``). A game with zero inactive players is a
    legitimate, cacheable empty result (``allow_empty=True``).
    """
    ensure_player_availability_table(con)
    df = fetch_cached(
        con,
        SOURCE_INACTIVE,
        game_id,
        lambda: _fetch_inactive_with_retry(game_id, data_dir=data_dir),
        data_dir=data_dir,
        rate_limiter=rate_limiter,
        allow_empty=True,
    )
    _replace_rows(con, df, game_id=game_id, source="nba_inactive_list")
    return df


def load_manual_availability_file(
    con: duckdb.DuckDBPyConnection,
    json_path: Path,
    name_index: dict[str, int],
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> pl.DataFrame:
    """Load one manually curated injury-report JSON snapshot into
    ``player_availability`` with ``source='manual_announced'``.

    Resumable/idempotent by file path: an already-loaded file (tracked in
    ``ingest_log`` under ``SOURCE_MANUAL``) is skipped and returns an empty
    frame rather than reprocessing (no network call to dedupe against, so
    this is pure bookkeeping, same contract as ``is_cached``/``mark_done``).
    Raises ``nba.parse.availability.UnmatchedPlayerNameError`` loudly if any
    record's player name can't be confidently resolved -- a row is never
    silently dropped or guessed.
    """
    ensure_player_availability_table(con)
    key = str(json_path)
    if is_cached(con, SOURCE_MANUAL, key):
        return pl.DataFrame(schema=dict.fromkeys(AVAILABILITY_SCHEMA, pl.Null))

    records = json.loads(json_path.read_text())
    pulled_at = datetime.now(UTC).replace(tzinfo=None)
    df = parse_manual_announcements(records, name_index, pulled_at=pulled_at)

    # Snapshot-for-replay: copy the raw input alongside the cache bookkeeping
    # path (mirrors the nba_inactive_list raw-JSON stash, same replay intent).
    snapshot_dir = data_dir / "availability_manual"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / json_path.name
    snapshot_path.write_text(json_path.read_text())

    if not df.is_empty():
        insert_rows(con, "player_availability", AVAILABILITY_SCHEMA, df)
    mark_done(con, SOURCE_MANUAL, key, snapshot_path)
    return df


def official_report_url(report_dt: datetime) -> str:
    """Build the official injury-report PDF URL for a given report timestamp.

    Filename pattern (verified against a real published report):
    ``Injury-Report_YYYY-MM-DD_HH_MMAM.pdf`` where the time is 12-hour,
    zero-padded, e.g. ``09_45AM``, ``03_00PM`` -- i.e. strftime
    ``%Y-%m-%d_%I_%M%p``. Reports publish on a cadence (roughly :30/:45/:00
    past the hour leading to tip); this function does not snap ``report_dt``
    to that cadence -- the caller supplies the exact published timestamp.
    """
    stamp = report_dt.strftime("%Y-%m-%d_%I_%M%p")
    return f"{_OFFICIAL_REPORT_BASE_URL}/Injury-Report_{stamp}.pdf"


def official_report_url_hour_only(report_dt: datetime) -> str:
    """Older-season filename tokenization: hour-only, no minutes segment.

    Verified against the real CDN across several seasons (see
    ``backfill_official_injury_reports``'s docstring for the dated
    examples): e.g. 2022-10-19's earliest report is
    ``Injury-Report_2022-10-19_01PM.pdf`` -- no ``_00`` minutes segment --
    while newer reports (and some dates as recent as 2025-12-26) use the
    ``HH_MM`` form ``official_report_url`` emits. Neither cutover date is
    reliable, so any caller enumerating historical reports must try both.
    """
    stamp = report_dt.strftime("%Y-%m-%d_%I%p")
    return f"{_OFFICIAL_REPORT_BASE_URL}/Injury-Report_{stamp}.pdf"


def official_report_url_candidates(report_dt: datetime) -> list[str]:
    """Both known filename tokenizations for one report timestamp.

    Order: ``HH_MM`` (the current/most common form, and the only one
    ``official_report_url`` emits) first, then hour-only (older seasons).
    """
    return [official_report_url(report_dt), official_report_url_hour_only(report_dt)]


def probe_report_url(
    url: str, *, client: httpx.Client | None = None, timeout: float = 10.0
) -> bool:
    """Cheap existence check for a candidate official-report URL.

    A tiny ranged GET (first 4 bytes) rather than a full download -- cheap
    enough to probe several candidate time/filename-format combinations
    per date without materially adding to pull volume. Checks BOTH a
    success status code AND the ``%PDF`` magic header, because some CDNs
    return HTTP 200 with an HTML error/placeholder page instead of a real
    404 for a missing object -- a plain status check alone would silently
    "find" garbage. Never raises: any network error during the probe
    (timeout, DNS, TLS, etc.) is treated as "does not exist" so the caller
    falls back to the next candidate instead of aborting.

    ``client`` lets callers (tests, and ``backfill_official_injury_reports``
    for connection reuse) inject an ``httpx.Client``-like object; one is
    created and closed per call otherwise.
    """
    import httpx  # lazy: keep this module import-safe with no network access

    owns_client = client is None
    c = client if client is not None else httpx.Client()
    try:
        try:
            resp = c.get(url, headers={"Range": "bytes=0-3"}, timeout=timeout)
        except Exception:
            return False
        if resp.status_code not in (200, 206):
            return False
        return bool(resp.content[:4] == b"%PDF")
    finally:
        if owns_client:
            c.close()


def _download_official_report_with_retry(url: str, *, client: httpx.Client | None = None) -> bytes:
    """Plain unauthenticated GET for the PDF, retried like
    ``_fetch_inactive_with_retry``/``boxscores.py`` (no auth, read-only --
    see the module/CLAUDE.md no-trading-credentials rule). ``client`` lets
    callers inject an ``httpx.Client``-like object (tests, connection
    reuse); one is created and closed per call otherwise."""
    import httpx  # lazy: keep this module import-safe with no network access

    owns_client = client is None
    c = client if client is not None else httpx.Client()
    try:
        for attempt in range(_MAX_FETCH_ATTEMPTS):
            try:
                response = c.get(url, timeout=15.0)
                response.raise_for_status()
                return bytes(response.content)
            except Exception:
                if attempt == _MAX_FETCH_ATTEMPTS - 1:
                    raise
                time.sleep(_RETRY_BACKOFF_S)
        raise AssertionError("unreachable")  # loop always returns or raises
    finally:
        if owns_client:
            c.close()


def pull_official_injury_report(
    con: duckdb.DuckDBPyConnection,
    report_dt: datetime,
    *,
    name_index: dict[str, int] | None = None,
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
    resolver: NameResolver | None = None,
    max_unmatched_rate: float = 0.05,
    url: str | None = None,
    http_client: httpx.Client | None = None,
) -> pl.DataFrame:
    """Download (or load cached), parse, and load one official injury-report
    PDF snapshot into ``player_availability`` with
    ``source='nba_official_report'``.

    Resumable/idempotent by filename (``ingest_log`` under
    ``SOURCE_OFFICIAL_REPORT``, keyed on the URL's own basename -- same
    shape as every other puller's ``ingest_log`` bookkeeping): a report
    already downloaded is never re-fetched, it's re-parsed from the cached
    PDF bytes on disk. Loading into DuckDB is a separate idempotent
    delete-then-insert keyed on ``(source='nba_official_report',
    as_of=report_dt)`` so re-running this function for the same
    ``report_dt`` never duplicates rows.

    ``url`` overrides the URL built from ``report_dt`` (defaults to
    ``official_report_url(report_dt)``, the ``HH_MM`` filename form) --
    used by ``backfill_official_injury_reports`` when the
    hour-only filename form (``official_report_url_hour_only``) is the one
    that actually resolved for an older date. ``http_client`` lets callers
    (same backfill function, and tests) inject an ``httpx.Client``-like
    object instead of opening a fresh connection per report.

    No auth, read-only GET only (see the module docstring and
    CLAUDE.md's "no trading credentials" constraint, which this module
    satisfies trivially -- it has nothing to do with markets). Raises
    ``nba.parse.availability.UnmatchedPlayerNameError`` loudly (listing
    every unmatched name at once) rather than silently dropping a player.

    ``con`` (this function's own connection, already required for loading)
    is forwarded to ``parse_official_injury_report`` so each row's
    ``game_id`` is resolved from the report's own ``GameDate``/``Matchup``
    columns against the ``games`` table -- best-effort, ``None`` when the
    game isn't in ``games`` yet or the matchup can't be parsed, never an
    error (see ``nba.parse.availability.resolve_game_id``).
    """
    ensure_player_availability_table(con)
    report_url = url if url is not None else official_report_url(report_dt)
    key = report_url.rsplit("/", 1)[-1]
    raw_path = data_dir / "availability_official" / key

    if not is_cached(con, SOURCE_OFFICIAL_REPORT, key):
        if rate_limiter is not None:
            rate_limiter.wait()
        try:
            content = _download_official_report_with_retry(report_url, client=http_client)
        except Exception:
            mark_failed(con, SOURCE_OFFICIAL_REPORT, key)
            raise
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(content)
        mark_done(con, SOURCE_OFFICIAL_REPORT, key, raw_path)

    if resolver is None and name_index is None:
        raise ValueError("provide name_index or resolver")
    df = parse_official_injury_report(
        raw_path,
        name_index or {},
        report_dt=report_dt,
        con=con,
        resolver=resolver,
        max_unmatched_rate=max_unmatched_rate,
    )

    con.execute(
        "DELETE FROM player_availability WHERE source = ? AND as_of = ?",
        ["nba_official_report", report_dt],
    )
    if not df.is_empty():
        insert_rows(con, "player_availability", AVAILABILITY_SCHEMA, df)
    return df


def build_name_index_from_cached_pbp(data_dir: Path = DEFAULT_DATA_DIR) -> dict[str, int]:
    """Build the name -> player_id lookup from every cached raw pbp parquet file.

    There is no dedicated player-name table in this schema; play-by-play
    (``nba.ingest.pbp``) is the only already-ingested source carrying both
    ``player_name`` and ``player_id`` together, so the manual-announcement
    path resolves names against whatever has been pulled so far. Games not
    yet pulled simply aren't in the index (and names from them will fail
    loudly via ``resolve_player_id`` rather than silently matching nothing).
    """
    pbp_dir = data_dir / "pbp"
    frames: dict[str, pl.DataFrame] = {}
    if pbp_dir.exists():
        for path in sorted(pbp_dir.glob("*.parquet")):
            frames[path.stem] = pl.read_parquet(path)
    return build_name_index_from_pbp_frames(frames)


def build_merged_name_index(
    data_dir: Path = DEFAULT_DATA_DIR, *, active_only: bool = True
) -> dict[str, int]:
    """Build the official-injury-report name resolver: static active players
    UNION cached-pbp, static taking precedence.

    Closes the common "valid report player isn't in cached play-by-play"
    gap (follow-up from the initial injury-feed build): a currently
    rostered player can be on tonight's official report without ever
    having appeared in a play-by-play file this project has pulled yet.
    ``build_name_index_from_static_players`` is the authoritative,
    network-free source for *who is currently an active NBA player*, so it
    wins on any name present in both; the pbp index still contributes names
    for players who have since left the active list (retired this
    offseason, etc.) but whose historical injury-report rows should still
    resolve. Static-players import failure (e.g. ``nba_api`` genuinely
    unavailable) degrades gracefully to pbp-only rather than raising --
    this is a coverage improvement, not a hard dependency.
    """
    pbp_index = build_name_index_from_cached_pbp(data_dir)
    try:
        static_index = build_name_index_from_static_players(active_only=active_only)
    except ImportError:
        static_index = {}
    merged = dict(pbp_index)
    merged.update(static_index)  # static (precedence) overwrites any pbp collision
    return merged


# --------------------------------------------------------------------------
# Historical backfill
# --------------------------------------------------------------------------

#: Default "anchor" times (ET, HH:MM). For each game date we look for the
#: LATEST published report at or before each anchor. The ``games`` table has
#: no tip-off time, so anchors stand in for tip-off buckets: 11:45 covers
#: noon/matinee tips, 17:45 covers the usual >= 7pm ET tips (the NBA's
#: late-afternoon report lands ~5:30pm). Consumers must still enforce
#: ``as_of < the game's own tip-off`` (the schema's leakage contract); the
#: anchors only guarantee every stored report precedes the bucket's tip.
DEFAULT_ANCHORS: tuple[str, ...] = ("11:45", "17:45")
#: Published-minute cadence (roughly :30/:45/:00 per the feed doc), probed
#: latest-first within each hour.
_SLOT_MINUTES: tuple[int, ...] = (45, 30, 15, 0)
#: How far back from an anchor to search before giving up on that anchor.
DEFAULT_LOOKBACK_H = 3

BACKFILL_SEASONS: tuple[str, ...] = ("2022-23", "2023-24", "2024-25", "2025-26")


@dataclass
class BackfillResult:
    """Summary of one ``backfill_official_injury_reports`` run."""

    dates_total: int = 0
    dates_skipped_cached: int = 0
    dates_done: int = 0
    dates_failed: int = 0
    dates_no_report: int = 0
    reports_loaded: int = 0
    names_matched: int = 0
    names_unmatched: int = 0
    probes: int = 0
    found: dict[str, list[str]] = field(default_factory=dict)  # date -> urls (dry-run too)


def backfill_slots(anchor: datetime, lookback_h: int = DEFAULT_LOOKBACK_H) -> list[datetime]:
    """Candidate report timestamps at or before ``anchor``, latest first.

    Never returns a timestamp after ``anchor`` (the as-of guarantee).
    """
    out: list[datetime] = []
    top = anchor.replace(minute=0, second=0, microsecond=0)
    for h in range(lookback_h + 1):
        base = top - timedelta(hours=h)
        for m in _SLOT_MINUTES:
            ts = base + timedelta(minutes=m)
            if ts <= anchor:
                out.append(ts)
    return out


def find_report_before(
    anchor: datetime,
    *,
    client: httpx.Client,
    rate_limiter: RateLimiter | None = None,
    lookback_h: int = DEFAULT_LOOKBACK_H,
    probe_counter: list[int] | None = None,
) -> tuple[datetime, str] | None:
    """Latest existing report at/before ``anchor``: (report_dt, resolved url).

    Walks slots latest-first, trying both filename forms per top-of-hour
    slot (``official_report_url_candidates`` order; only ``HH_MM`` for other
    minutes), probing with a ranged GET.
    """
    for ts in backfill_slots(anchor, lookback_h):
        # The hour-only filename cannot encode minutes, so it is only a valid
        # candidate for top-of-hour slots (else a :45 slot would "find" the
        # :00 report and mislabel its as_of).
        urls = official_report_url_candidates(ts)
        for url in urls if ts.minute == 0 else urls[:1]:
            if rate_limiter is not None:
                rate_limiter.wait()
            if probe_counter is not None:
                probe_counter[0] += 1
            if probe_report_url(url, client=client):
                return ts, url
    return None


def game_dates_between(con: duckdb.DuckDBPyConnection, start: date, end: date) -> list[date]:
    """Distinct game dates in ``games`` within [start, end], ascending."""
    rows = con.execute(
        "SELECT DISTINCT game_date FROM games WHERE game_date BETWEEN ? AND ? ORDER BY 1",
        [start, end],
    ).fetchall()
    return [r[0] for r in rows]


def _parse_anchor(d: date, hhmm: str) -> datetime:
    hh, mm = hhmm.split(":")
    return datetime(d.year, d.month, d.day, int(hh), int(mm))


def _alias_file(data_dir: Path) -> Path:
    return data_dir / "availability_backfill" / "aliases.csv"


def build_name_resolver(
    con: duckdb.DuckDBPyConnection,
    data_dir: Path = DEFAULT_DATA_DIR,
    start: date | None = None,
    end: date | None = None,
    *,
    names: Sequence[tuple[str, int]] | None = None,
    pad_days: int = 400,
) -> NameResolver:
    """Team-aware resolver restricted to players seen in ``player_game_stats``
    within [start, end] +- ``pad_days`` (~season +- 1). Never raises on
    ambiguity (that is a resolve-time matter). Full names come from nba_api's
    bundled static player list (local, no network) unless ``names`` is given.
    Reviewed aliases are read from ``data_dir/availability_backfill/aliases.csv``
    (columns ``alias,player_id``) if present.
    """
    lo = (start or date(1900, 1, 1)) - timedelta(days=pad_days)
    hi = (end or date(2100, 1, 1)) + timedelta(days=pad_days)
    rows = con.execute(
        "SELECT s.player_id, g.game_date, s.team_id FROM player_game_stats s "
        "JOIN games g USING (game_id) WHERE g.game_date BETWEEN ? AND ?",
        [lo, hi],
    ).fetchall()
    history: dict[int, list[tuple[date, int]]] = {}
    for pid, d, tid in rows:
        history.setdefault(int(pid), []).append((d, int(tid)))
    if names is None:
        from nba_api.stats.static import players as static_players

        names = [(str(p["full_name"]), int(p["id"])) for p in static_players.get_players()]
    pool = [(n, i) for n, i in names if i in history]
    aliases: dict[str, int] = {}
    af = _alias_file(data_dir)
    if af.exists():
        for rec in pl.read_csv(af).iter_rows(named=True):
            aliases[str(rec["alias"])] = int(rec["player_id"])
    unseen = [n for n, i in names if i not in history]
    return NameResolver(pool, history, aliases, known_unseen=unseen)


def _flush_unmatched(resolver: NameResolver | None, manifest_dir: Path) -> None:
    """Append and clear the resolver's unmatched-names log (unmatched.csv)."""
    if resolver is None or not resolver.unmatched:
        return
    manifest_dir.mkdir(parents=True, exist_ok=True)
    path = manifest_dir / "unmatched.csv"
    new = not path.exists()
    with path.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["name", "team", "date", "reason"])
        w.writerows(resolver.unmatched)
    resolver.unmatched.clear()


def backfill_official_injury_reports(
    con: duckdb.DuckDBPyConnection,
    *,
    dates: Sequence[date] | None = None,
    start: date | None = None,
    end: date | None = None,
    name_index: dict[str, int] | None = None,
    resolver: NameResolver | None = None,
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
    http_client: httpx.Client | None = None,
    anchors: Sequence[str] = DEFAULT_ANCHORS,
    lookback_h: int = DEFAULT_LOOKBACK_H,
    limit: int | None = None,
    dry_run: bool = False,
    progress_every: int = 25,
    max_unmatched_rate: float = 0.05,
) -> BackfillResult:
    """Backfill historical official injury-report PDFs, per game date.

    Dates come from ``dates`` or, if omitted, the ``games`` table within
    [``start``, ``end``]. Per date, for each anchor in ``anchors`` the
    LATEST report published at or before that anchor is located (probing
    both filename forms, see ``find_report_before``), downloaded (cached
    PDFs under ``data_dir/availability_official`` are never refetched),
    parsed and loaded via ``pull_official_injury_report``. Every stored
    ``as_of`` is therefore <= its anchor, strictly before the tip-off
    bucket the anchor represents (no leakage); distinct anchors resolving
    to the same report are loaded once.

    Resumable: each completed date writes a manifest JSON under
    ``data_dir/availability_backfill`` and is marked in ``ingest_log``
    (``SOURCE_OFFICIAL_REPORT_BACKFILL``, key = ISO date); done dates are
    skipped. A date with a failure (network, unmatched player name, ...)
    is ``mark_failed`` and the run continues; failed dates are retried on
    the next run. Dates with no report found are recorded as done with an
    empty manifest (the feed did not exist / was not published then).
    ``dry_run`` probes only: no download, no DB or disk writes.
    """
    import httpx

    if dates is None:
        if start is None or end is None:
            raise ValueError("provide dates, or both start and end")
        dates = game_dates_between(con, start, end)
    date_list = list(dates)
    if limit is not None:
        date_list = date_list[:limit]
    result = BackfillResult(dates_total=len(date_list))
    if not dry_run:
        ensure_player_availability_table(con)
        if name_index is None and resolver is None:
            resolver = build_name_resolver(
                con, data_dir, min(date_list, default=None), max(date_list, default=None)
            )
    owns_client = http_client is None
    client = http_client if http_client is not None else httpx.Client()
    manifest_dir = data_dir / "availability_backfill"
    probe_counter = [0]
    try:
        for i, d in enumerate(date_list, 1):
            key = d.isoformat()
            if not dry_run and is_cached(con, SOURCE_OFFICIAL_REPORT_BACKFILL, key):
                result.dates_skipped_cached += 1
                continue
            try:
                hits: dict[datetime, str] = {}
                for hhmm in anchors:
                    hit = find_report_before(
                        _parse_anchor(d, hhmm),
                        client=client,
                        rate_limiter=rate_limiter,
                        lookback_h=lookback_h,
                        probe_counter=probe_counter,
                    )
                    if hit is not None:
                        hits.setdefault(hit[0], hit[1])
                result.found[key] = [hits[ts] for ts in sorted(hits)]
                if dry_run:
                    continue
                for ts in sorted(hits):
                    df = pull_official_injury_report(
                        con,
                        ts,
                        name_index=name_index,
                        resolver=resolver,
                        max_unmatched_rate=max_unmatched_rate,
                        data_dir=data_dir,
                        rate_limiter=rate_limiter,
                        url=hits[ts],
                        http_client=client,
                    )
                    result.reports_loaded += 1
                    result.names_matched += len(df)
                manifest_dir.mkdir(parents=True, exist_ok=True)
                manifest = manifest_dir / f"{key}.json"
                manifest.write_text(
                    json.dumps({"date": key, "reports": [hits[ts] for ts in sorted(hits)]})
                )
                mark_done(con, SOURCE_OFFICIAL_REPORT_BACKFILL, key, manifest)
                result.names_unmatched += len(resolver.unmatched) if resolver else 0
                _flush_unmatched(resolver, manifest_dir)
                if hits:
                    result.dates_done += 1
                else:
                    result.dates_no_report += 1
            except Exception as exc:
                result.names_unmatched += len(resolver.unmatched) if resolver else 0
                _flush_unmatched(resolver, manifest_dir)
                result.dates_failed += 1
                if not dry_run:
                    mark_failed(con, SOURCE_OFFICIAL_REPORT_BACKFILL, key)
                print(f"backfill[{key}]: FAILED {type(exc).__name__}: {str(exc)[:200]}")
            if progress_every and i % progress_every == 0:
                print(
                    f"backfill: {i}/{len(date_list)} dates, done={result.dates_done} "
                    f"no_report={result.dates_no_report} failed={result.dates_failed} "
                    f"cached={result.dates_skipped_cached} reports={result.reports_loaded} "
                    f"probes={probe_counter[0]}",
                    flush=True,
                )
    finally:
        result.probes = probe_counter[0]
        if owns_client:
            client.close()
    return result


def build_backfill_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m nba.ingest.availability")
    sub = parser.add_subparsers(dest="command", required=True)
    bp = sub.add_parser("backfill", help="backfill historical official injury-report PDFs")
    bp.add_argument("--start", required=True, help="YYYY-MM-DD")
    bp.add_argument("--end", required=True, help="YYYY-MM-DD")
    bp.add_argument("--rate-limit-s", type=float, default=0.3)
    bp.add_argument("--limit", type=int, default=None, help="only the first N game dates")
    bp.add_argument("--dry-run", action="store_true", help="probe only; no downloads or writes")
    bp.add_argument("--anchor", action="append", dest="anchors", help="HH:MM ET; repeatable")
    bp.add_argument("--max-unmatched-rate", type=float, default=0.05)
    bp.add_argument("--progress-every", type=int, default=25)
    bp.add_argument("--db", default=None, help="DuckDB path (default: project nba.duckdb)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    from nba.db.connect import connect

    args = build_backfill_parser().parse_args(argv)
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if args.dry_run:
        # Dry run never writes: open read-only (single-writer safe).
        from nba.db.connect import DEFAULT_DB_PATH

        con = connect(args.db or DEFAULT_DB_PATH, read_only=True)
    else:
        con = open_db(args.db)
    res = backfill_official_injury_reports(
        con,
        start=start,
        end=end,
        rate_limiter=RateLimiter(min_interval_s=args.rate_limit_s),
        anchors=tuple(args.anchors) if args.anchors else DEFAULT_ANCHORS,
        limit=args.limit,
        dry_run=args.dry_run,
        progress_every=args.progress_every,
        max_unmatched_rate=args.max_unmatched_rate,
    )
    print(
        f"backfill: dates={res.dates_total} done={res.dates_done} no_report={res.dates_no_report} "
        f"failed={res.dates_failed} cached={res.dates_skipped_cached} "
        f"reports={res.reports_loaded} matched_rows={res.names_matched} "
        f"unmatched={res.names_unmatched} probes={res.probes}"
    )
    if args.dry_run:
        for k, urls in res.found.items():
            print(k, [u.rsplit("/", 1)[-1] for u in urls])
    return 1 if res.dates_failed else 0


if __name__ == "__main__":
    sys.exit(main())
