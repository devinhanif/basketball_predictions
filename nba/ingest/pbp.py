"""Pull raw play-by-play from nba_api, cached and resumable per game_id.

Raw play-by-play has no dedicated DuckDB table in the schema (see
CLAUDE.md) -- it is cached to parquet under data/pbp/ and consumed by the
possession/stint parser (nba/parse/), a later milestone. This module only
handles the fetch/cache/resume concern.

Endpoint: ``PlayByPlayV3`` (not V2). V2 is dead -- it raises ``KeyError
('resultSet')`` for current games (verified live against game
0022500500: the NBA API no longer returns a usable response for V2; see
https://github.com/swar/nba_api/issues/591). V3's player-level frame
(``get_data_frames()[0]``) is populated for the same game (488 rows,
sane event count for a full game).
"""

from __future__ import annotations

import time
from pathlib import Path

import duckdb
import polars as pl

from nba.ingest.cache import DEFAULT_DATA_DIR, RateLimiter, fetch_cached

SOURCE = "pbp"

#: Max attempts for the live fetch before giving up (see boxscores.py /
#: team_advanced.py -- same short-backoff retry pattern).
_MAX_FETCH_ATTEMPTS = 3
_RETRY_BACKOFF_S = 1.0

#: Columns kept from the raw V3 frame, renamed to what the possession/stint
#: parser (nba/parse/, a later milestone) will need: period/clock for
#: sequencing, team_id/player_id for lineup tracking, action type/subtype
#: for event classification, score columns for score_diff, and shot
#: metadata for outcome/shot_zone. Verified live against game 0022500500.
_PBP_SCHEMA = [
    "game_id",
    "action_number",
    "period",
    "clock",
    "team_id",
    "player_id",
    "player_name",
    "action_type",
    "sub_type",
    "description",
    "score_home",
    "score_away",
    "points_total",
    "shot_result",
    "shot_value",
    "shot_distance",
    "is_field_goal",
    "location",
]

_RAW_TO_SCHEMA = {
    "gameId": "game_id",
    "actionNumber": "action_number",
    "period": "period",
    "clock": "clock",
    "teamId": "team_id",
    "personId": "player_id",
    "playerName": "player_name",
    "actionType": "action_type",
    "subType": "sub_type",
    "description": "description",
    "scoreHome": "score_home",
    "scoreAway": "score_away",
    "pointsTotal": "points_total",
    "shotResult": "shot_result",
    "shotValue": "shot_value",
    "shotDistance": "shot_distance",
    "isFieldGoal": "is_field_goal",
    "location": "location",
}


def _normalize_pbp_v3_frame(raw: pl.DataFrame, game_id: str) -> pl.DataFrame:
    """Map a raw PlayByPlayV3 frame to ``_PBP_SCHEMA``.

    V3 column names (verified live against game 0022500500): ``gameId``,
    ``actionNumber``, ``clock`` (ISO-8601 duration, e.g. ``'PT11M39.00S'``),
    ``period``, ``teamId``, ``teamTricode``, ``personId``, ``playerName``,
    ``scoreHome``/``scoreAway`` (string, '' before the first score),
    ``pointsTotal``, ``location`` ('h'/'v'/''), ``description``,
    ``actionType``/``subType`` (event classification, e.g. 'Made Shot' /
    'Turnover'), ``shotResult`` ('Made'/'Missed'/''), ``isFieldGoal``,
    ``shotValue``, ``shotDistance``.
    """
    if raw.is_empty():
        return pl.DataFrame(schema={c: pl.Null for c in _PBP_SCHEMA})
    out = raw.rename(_RAW_TO_SCHEMA).select(list(_RAW_TO_SCHEMA.values()))
    out = out.with_columns(pl.lit(game_id).alias("game_id"))
    return out.select(_PBP_SCHEMA)


def _fetch_pbp_for_game(game_id: str) -> pl.DataFrame:
    """Fetch one game's play-by-play via PlayByPlayV3 (see module docstring)."""
    from nba_api.stats.endpoints import playbyplayv3  # lazy import

    pbp = playbyplayv3.PlayByPlayV3(game_id=game_id)
    raw = pl.from_pandas(pbp.get_data_frames()[0])
    return _normalize_pbp_v3_frame(raw, game_id)


def _fetch_pbp_with_retry(game_id: str) -> pl.DataFrame:
    """Retry the live V3 fetch a few times before giving up.

    A played game's play-by-play is never legitimately empty, so a transient
    empty/timeout response gets a few short-backoff retries before the
    caller's ``fetch_cached(..., allow_empty=False)`` marks it failed.
    """
    last: pl.DataFrame | None = None
    for attempt in range(_MAX_FETCH_ATTEMPTS):
        last = _fetch_pbp_for_game(game_id)
        if not last.is_empty():
            return last
        if attempt < _MAX_FETCH_ATTEMPTS - 1:
            time.sleep(_RETRY_BACKOFF_S)
    assert last is not None
    return last


def pull_game_pbp(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
) -> pl.DataFrame:
    """Fetch (or load cached) raw play-by-play for one game. Caches only; no DB load.

    A played game's play-by-play is never legitimately empty, so the fetch
    is retried a few times on an empty response and, failing that, marked
    'failed' rather than silently cached as 'done' (``allow_empty=False``),
    same contract as ``pull_game_boxscore``.
    """
    return fetch_cached(
        con,
        SOURCE,
        game_id,
        lambda: _fetch_pbp_with_retry(game_id),
        data_dir=data_dir,
        rate_limiter=rate_limiter,
        allow_empty=False,
    )
