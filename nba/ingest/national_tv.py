"""Pull per-date national-TV broadcaster info into ``games.national_tv``.

Per-game BoxScoreSummaryV3 is one nba_api call per game (~1,230+ calls for a
full season). ``ScoreboardV2``'s ``GameHeader`` frame returns every game
played on a given date in a single call, so this module is one call per
*date* (~170-180 calls/season) instead of one call per game -- the same
national-TV information, far fewer calls.

Verified live (ScoreboardV2(game_date='2023-12-25')): the ``GameHeader``
frame (``get_data_frames()[0]``) has columns including ``GAME_ID`` and
``NATL_TV_BROADCASTER_ABBREVIATION`` (e.g. ``'ESPN'``, ``'ABC/ESPN'``, or
``''`` when there is no national broadcast).

Cached and resumable per date (cache source ``"national-tv"``). nba_api is
imported lazily so this module is safe to import with no network access.
"""

from __future__ import annotations

import time
from pathlib import Path

import duckdb
import polars as pl

from nba.ingest.cache import DEFAULT_DATA_DIR, RateLimiter, fetch_cached

SOURCE = "national-tv"

#: Max attempts for the live fetch before giving up (see boxscores.py /
#: team_advanced.py -- same short-backoff retry pattern).
_MAX_FETCH_ATTEMPTS = 3
_RETRY_BACKOFF_S = 1.0

_SCHEMA = ["game_id", "national_tv"]


def is_national(broadcaster: str | None) -> bool:
    """A game is on national TV iff the broadcaster field is non-null/non-empty."""
    return broadcaster is not None and broadcaster != ""


def ensure_national_tv_column(con: duckdb.DuckDBPyConnection) -> None:
    """Idempotent migration: add ``games.national_tv`` if it doesn't exist yet.

    Safe to call on every connection open (DuckDB's ``ADD COLUMN IF NOT
    EXISTS`` is a no-op when the column is already present), so existing
    on-disk DBs created before this module existed migrate automatically.
    """
    con.execute("ALTER TABLE games ADD COLUMN IF NOT EXISTS national_tv VARCHAR")


def _fetch_scoreboard_for_date(date: str) -> pl.DataFrame:
    """Hit nba_api's ScoreboardV2 for one date string, e.g. '2023-12-25'."""
    from nba_api.stats.endpoints import scoreboardv2  # lazy import

    sb = scoreboardv2.ScoreboardV2(game_date=date)
    header = pl.from_pandas(sb.get_data_frames()[0])
    return _normalize_scoreboard_header(header)


def _fetch_scoreboard_with_retry(date: str) -> pl.DataFrame:
    """Retry the live fetch a few times before giving up (see boxscores.py).

    A date resolved from the ``games`` table always had at least one game
    played on it, so an empty response means a transient/throttled hit, not
    a legitimately empty day.
    """
    last: pl.DataFrame | None = None
    for attempt in range(_MAX_FETCH_ATTEMPTS):
        last = _fetch_scoreboard_for_date(date)
        if not last.is_empty():
            return last
        if attempt < _MAX_FETCH_ATTEMPTS - 1:
            time.sleep(_RETRY_BACKOFF_S)
    assert last is not None
    return last


def _normalize_scoreboard_header(header: pl.DataFrame) -> pl.DataFrame:
    """Map a raw ScoreboardV2 ``GameHeader`` frame to ``_SCHEMA``.

    Empty-string broadcaster values are normalized to ``None`` so
    ``national_tv`` is NULL (unknown/non-national) rather than ``''``.
    """
    if header.is_empty():
        return pl.DataFrame(schema={c: pl.Utf8 for c in _SCHEMA})
    out = header.select(
        pl.col("GAME_ID").alias("game_id"),
        pl.when(pl.col("NATL_TV_BROADCASTER_ABBREVIATION") == "")
        .then(None)
        .otherwise(pl.col("NATL_TV_BROADCASTER_ABBREVIATION"))
        .alias("national_tv"),
    )
    return out


def pull_national_tv_for_date(
    con: duckdb.DuckDBPyConnection,
    date: str,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
) -> pl.DataFrame:
    """Fetch (or load cached) national-TV info for one date; update ``games``.

    ``date`` is a 'YYYY-MM-DD' string. Only updates rows already present in
    ``games`` (matched by ``game_id``); games not yet ingested are silently
    skipped since there is no row to attach the broadcaster info to -- the
    ``games`` puller owns row creation.
    """
    ensure_national_tv_column(con)
    df = fetch_cached(
        con,
        SOURCE,
        date,
        lambda: _fetch_scoreboard_with_retry(date),
        data_dir=data_dir,
        rate_limiter=rate_limiter,
        allow_empty=False,
    )
    update_national_tv(con, df)
    return df


def update_national_tv(con: duckdb.DuckDBPyConnection, df: pl.DataFrame) -> None:
    """Update ``games.national_tv`` for every (game_id, national_tv) row in ``df``.

    Plain per-row ``UPDATE`` (not an upsert into a new table): ``games`` rows
    must already exist (created by ``nba.ingest.games``), so there is
    nothing to insert here, only to set.
    """
    if df.is_empty():
        return
    rows = df.select(["game_id", "national_tv"]).rows()
    con.executemany("UPDATE games SET national_tv = ? WHERE game_id = ?", [(t, g) for g, t in rows])


def game_dates_for_season(con: duckdb.DuckDBPyConnection, season: str) -> list[str]:
    """Distinct 'YYYY-MM-DD' game_date strings for a season string, e.g. '2023-24'.

    Reads from the ``games`` table (same ``season`` int-encoding as
    ``nba.ingest.games.season_to_int``), so results are limited to dates on
    which an already-ingested game was actually played.
    """
    from nba.ingest.games import season_to_int

    rows = con.execute(
        "SELECT DISTINCT CAST(game_date AS VARCHAR) FROM games WHERE season = ? ORDER BY 1",
        [season_to_int(season)],
    ).fetchall()
    return [r[0] for r in rows]
