"""Pull per-player box score lines (player_game_stats) from nba_api.

Cached and resumable per game_id. nba_api is imported lazily so this module
is safe to import with no network access.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import duckdb
import polars as pl

from nba.ingest.cache import DEFAULT_DATA_DIR, RateLimiter, fetch_cached, insert_rows

SOURCE = "boxscore"

#: Max attempts for the live box-score fetch before giving up (see
#: ``_fetch_boxscore_with_retry``). A transient empty/timeout response gets a
#: few short-backoff retries before being marked failed.
_MAX_FETCH_ATTEMPTS = 3
_RETRY_BACKOFF_S = 1.0

#: ISO-8601 duration as returned by some BoxScoreTraditionalV3 responses,
#: e.g. 'PT34M12.00S' or 'PT0M00.00S'.
_ISO8601_MINUTES_RE = re.compile(r"^PT(?P<min>\d+(?:\.\d+)?)M(?P<sec>\d+(?:\.\d+)?)S$")

_STATS_SCHEMA = [
    "game_id",
    "player_id",
    "team_id",
    "minutes",
    "pts",
    "reb",
    "ast",
    "fg3m",
    "stl",
    "blk",
    "tov",
    "starter",
    "fgm",
    "fga",
    "fg3a",
    "ftm",
    "fta",
    "oreb",
    "dreb",
    "pf",
]

#: Columns added after the initial player_game_stats rollout (full
#: traditional box score: FGA/FTA/OREB etc., needed for the possession-count
#: reconciliation gate and a real points model). Migrated in on every
#: connection via ``ensure_player_game_stats_columns`` -- see
#: nba/ingest/national_tv.py's ``ensure_national_tv_column`` for the same
#: pattern.
_MIGRATED_INT_COLUMNS = ["fgm", "fga", "fg3a", "ftm", "fta", "oreb", "dreb", "pf"]


def ensure_player_game_stats_columns(con: duckdb.DuckDBPyConnection) -> None:
    """Idempotent migration: add the extended box-score columns if missing.

    Safe to call on every connection open (DuckDB's ``ADD COLUMN IF NOT
    EXISTS`` is a no-op when the column is already present), so existing
    on-disk DBs created before these columns existed migrate automatically.
    """
    for col in _MIGRATED_INT_COLUMNS:
        con.execute(f"ALTER TABLE player_game_stats ADD COLUMN IF NOT EXISTS {col} INT")


def _parse_minutes(raw: str | None) -> float | None:
    """Normalize nba_api's various minutes formats to float minutes.

    Handles:
    - ``None`` / ``''`` (DNP) -> ``None``
    - ``'MM:SS'`` (e.g. BoxScoreTraditionalV2, most of V3) -> float minutes
    - ISO-8601 duration ``'PTxxMyy.yyS'`` (seen from some V3 responses) -> float minutes
    - a bare number string (e.g. ``'12'``) -> float minutes
    """
    if raw is None or raw == "":
        return None
    iso_match = _ISO8601_MINUTES_RE.match(raw)
    if iso_match:
        return float(iso_match.group("min")) + float(iso_match.group("sec")) / 60.0
    if ":" in raw:
        mins, secs = raw.split(":")
        return float(mins) + float(secs) / 60.0
    return float(raw)


def _fetch_boxscore_for_game(game_id: str) -> pl.DataFrame:
    """Fetch one game's player box score via BoxScoreTraditionalV3.

    V3 replaces V2, which returns empty dataframes for the large majority of
    current-season (2025-26) games while V3 is populated for the same games.
    """
    from nba_api.stats.endpoints import boxscoretraditionalv3  # lazy import

    box = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id)
    raw = pl.from_pandas(box.get_data_frames()[0])
    return _normalize_boxscore_v3_frame(raw, game_id)


def _fetch_boxscore_with_retry(game_id: str) -> pl.DataFrame:
    """Retry the live V3 fetch a few times before giving up.

    A transient empty/timeout response (throttling, endpoint hiccup) gets up
    to ``_MAX_FETCH_ATTEMPTS`` tries with a short backoff before the caller's
    ``fetch_cached(..., allow_empty=False)`` marks it failed.
    """
    last: pl.DataFrame | None = None
    for attempt in range(_MAX_FETCH_ATTEMPTS):
        last = _fetch_boxscore_for_game(game_id)
        if not last.is_empty():
            return last
        if attempt < _MAX_FETCH_ATTEMPTS - 1:
            time.sleep(_RETRY_BACKOFF_S)
    assert last is not None
    return last


def _normalize_boxscore_v3_frame(raw: pl.DataFrame, game_id: str) -> pl.DataFrame:
    """Map a raw BoxScoreTraditionalV3 player frame to ``_STATS_SCHEMA``.

    V3 column names (verified live against real games):
    ``personId``, ``teamId``, ``position``, ``minutes`` ('MM:SS' or, on some
    responses, ISO-8601 'PTxxMyy.yyS'; '' for DNP), ``points``,
    ``reboundsTotal``, ``assists``, ``threePointersMade``, ``steals``,
    ``blocks``, ``turnovers``, ``fieldGoalsMade``, ``fieldGoalsAttempted``,
    ``threePointersAttempted``, ``freeThrowsMade``, ``freeThrowsAttempted``,
    ``reboundsOffensive``, ``reboundsDefensive``, ``foulsPersonal`` (verified
    live against game 0022500500: fga=13, fta=0, oreb=0, dreb=3 for one
    player row -- sane ranges, no FGA/FTA/OREB column renames since the
    original V2->V3 migration). ``starter`` is derived the same way as the
    old V2 logic: non-empty ``position`` means the player started.
    """
    if raw.is_empty():
        return pl.DataFrame(schema={c: pl.Null for c in _STATS_SCHEMA})
    minutes = [_parse_minutes(m) for m in raw["minutes"].to_list()]
    starters = [bool(pos) for pos in raw["position"].to_list()]
    out = pl.DataFrame(
        {
            "game_id": [game_id] * len(raw),
            "player_id": raw["personId"].to_list(),
            "team_id": raw["teamId"].to_list(),
            "minutes": minutes,
            "pts": raw["points"].to_list(),
            "reb": raw["reboundsTotal"].to_list(),
            "ast": raw["assists"].to_list(),
            "fg3m": raw["threePointersMade"].to_list(),
            "stl": raw["steals"].to_list(),
            "blk": raw["blocks"].to_list(),
            "tov": raw["turnovers"].to_list(),
            "starter": starters,
            "fgm": raw["fieldGoalsMade"].to_list(),
            "fga": raw["fieldGoalsAttempted"].to_list(),
            "fg3a": raw["threePointersAttempted"].to_list(),
            "ftm": raw["freeThrowsMade"].to_list(),
            "fta": raw["freeThrowsAttempted"].to_list(),
            "oreb": raw["reboundsOffensive"].to_list(),
            "dreb": raw["reboundsDefensive"].to_list(),
            "pf": raw["foulsPersonal"].to_list(),
        }
    )
    return out


def pull_game_boxscore(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
) -> pl.DataFrame:
    """Fetch (or load cached) box score for one game and upsert into player_game_stats.

    A played game's box score is never legitimately empty, so the fetch is
    retried a few times on an empty response and, failing that, marked
    'failed' rather than silently cached as 'done' (``allow_empty=False``).
    """
    ensure_player_game_stats_columns(con)
    df = fetch_cached(
        con,
        SOURCE,
        game_id,
        lambda: _fetch_boxscore_with_retry(game_id),
        data_dir=data_dir,
        rate_limiter=rate_limiter,
        allow_empty=False,
    )
    upsert_player_game_stats(con, df)
    return df


def upsert_player_game_stats(con: duckdb.DuckDBPyConnection, df: pl.DataFrame) -> None:
    """Idempotently load a box score DataFrame into ``player_game_stats``.

    The table has no primary key in the schema, so we delete-then-insert
    per game_id to stay idempotent across re-runs against the same cache.
    """
    if df.is_empty():
        return
    game_ids = df["game_id"].unique().to_list()
    placeholders = ", ".join(["?"] * len(game_ids))
    con.execute(f"DELETE FROM player_game_stats WHERE game_id IN ({placeholders})", game_ids)
    insert_rows(con, "player_game_stats", _STATS_SCHEMA, df)
