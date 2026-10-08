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

import json
import time
from datetime import UTC, datetime
from pathlib import Path

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
)
from nba.parse.availability import (
    AVAILABILITY_SCHEMA,
    build_name_index_from_pbp_frames,
    build_name_index_from_static_players,
    normalize_inactive_frame,
    parse_manual_announcements,
    parse_official_injury_report,
)

SOURCE_INACTIVE = "availability"
SOURCE_MANUAL = "availability-manual"
SOURCE_OFFICIAL_REPORT = "availability-official-report"

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


def _official_report_filename(report_dt: datetime) -> str:
    return f"Injury-Report_{report_dt.strftime('%Y-%m-%d_%I_%M%p')}.pdf"


def _official_report_raw_path(report_dt: datetime, data_dir: Path) -> Path:
    return data_dir / "availability_official" / _official_report_filename(report_dt)


def _download_official_report_with_retry(url: str) -> bytes:
    """Plain unauthenticated GET for the PDF, retried like
    ``_fetch_inactive_with_retry``/``boxscores.py`` (no auth, read-only --
    see the module/CLAUDE.md no-trading-credentials rule)."""
    import httpx  # lazy: keep this module import-safe with no network access

    for attempt in range(_MAX_FETCH_ATTEMPTS):
        try:
            response = httpx.get(url, timeout=15.0)
            response.raise_for_status()
            return response.content
        except Exception:
            if attempt == _MAX_FETCH_ATTEMPTS - 1:
                raise
            time.sleep(_RETRY_BACKOFF_S)
    raise AssertionError("unreachable")  # loop always returns or raises


def pull_official_injury_report(
    con: duckdb.DuckDBPyConnection,
    report_dt: datetime,
    *,
    name_index: dict[str, int],
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
) -> pl.DataFrame:
    """Download (or load cached), parse, and load one official injury-report
    PDF snapshot into ``player_availability`` with
    ``source='nba_official_report'``.

    Resumable/idempotent by filename (``ingest_log`` under
    ``SOURCE_OFFICIAL_REPORT``, keyed on ``official_report_url``'s filename
    -- same shape as every other puller's ``ingest_log`` bookkeeping): a
    report already downloaded is never re-fetched, it's re-parsed from the
    cached PDF bytes on disk. Loading into DuckDB is a separate idempotent
    delete-then-insert keyed on ``(source='nba_official_report',
    as_of=report_dt)`` so re-running this function for the same
    ``report_dt`` never duplicates rows.

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
    url = official_report_url(report_dt)
    raw_path = _official_report_raw_path(report_dt, data_dir)
    key = raw_path.name

    if not is_cached(con, SOURCE_OFFICIAL_REPORT, key):
        if rate_limiter is not None:
            rate_limiter.wait()
        try:
            content = _download_official_report_with_retry(url)
        except Exception:
            mark_failed(con, SOURCE_OFFICIAL_REPORT, key)
            raise
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(content)
        mark_done(con, SOURCE_OFFICIAL_REPORT, key, raw_path)

    df = parse_official_injury_report(raw_path, name_index, report_dt=report_dt, con=con)

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
