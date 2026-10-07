"""Pull per-team-per-game advanced box score stats (team_game_advanced) from nba_api.

Real offensive/defensive rating, pace, and four-factors replace the
FGA/FTA/OREB box-score proxy for team efficiency features (see CLAUDE.md,
"team efficiency features"). Cached and resumable per game_id, same pattern
as ``nba/ingest/boxscores.py``. nba_api is imported lazily so this module is
safe to import with no network access.

Two nba_api endpoints are combined per game:
- ``BoxScoreAdvancedV3``: offensiveRating, defensiveRating, netRating, pace,
  effectiveFieldGoalPercentage, offensiveReboundPercentage.
- ``BoxScoreFourFactorsV3``: freeThrowAttemptRate, teamTurnoverPercentage
  (not available, or not in a directly comparable scale, on AdvancedV3 --
  AdvancedV3's ``turnoverRatio`` is a per-100-possession ratio, not the 0-1
  fraction used here).
Both endpoints' team-level dataframe is ``get_data_frames()[1]`` (index 0 is
per-player). Verified live against game 0022200001: 2 rows, one per team,
off/def rating ~119-130, pace ~97.5 -- sane NBA ranges.
"""

from __future__ import annotations

import time
from pathlib import Path

import duckdb
import polars as pl

from nba.ingest.cache import DEFAULT_DATA_DIR, RateLimiter, fetch_cached, upsert_rows

SOURCE = "team-advanced"

#: Max attempts for the live fetch before giving up, same backoff pattern as
#: ``nba/ingest/boxscores.py``.
_MAX_FETCH_ATTEMPTS = 3
_RETRY_BACKOFF_S = 1.0

_SCHEMA = [
    "game_id",
    "team_id",
    "off_rating",
    "def_rating",
    "net_rating",
    "pace",
    "efg_pct",
    "tov_pct",
    "oreb_pct",
    "ft_rate",
]


def _fetch_team_advanced_for_game(game_id: str, rate_limiter: RateLimiter | None) -> pl.DataFrame:
    """Fetch and merge the two live V3 team-level frames for one game."""
    from nba_api.stats.endpoints import (  # lazy import
        boxscoreadvancedv3,
        boxscorefourfactorsv3,
    )

    adv = boxscoreadvancedv3.BoxScoreAdvancedV3(game_id=game_id)
    adv_team = pl.from_pandas(adv.get_data_frames()[1])

    if rate_limiter is not None:
        rate_limiter.wait()

    ff = boxscorefourfactorsv3.BoxScoreFourFactorsV3(game_id=game_id)
    ff_team = pl.from_pandas(ff.get_data_frames()[1])

    return _normalize_team_advanced_frames(adv_team, ff_team, game_id)


def _fetch_team_advanced_with_retry(game_id: str, rate_limiter: RateLimiter | None) -> pl.DataFrame:
    """Retry the live fetch a few times before giving up (see boxscores.py)."""
    last: pl.DataFrame | None = None
    for attempt in range(_MAX_FETCH_ATTEMPTS):
        last = _fetch_team_advanced_for_game(game_id, rate_limiter)
        if not last.is_empty():
            return last
        if attempt < _MAX_FETCH_ATTEMPTS - 1:
            time.sleep(_RETRY_BACKOFF_S)
    assert last is not None
    return last


def _normalize_team_advanced_frames(
    adv_team: pl.DataFrame, ff_team: pl.DataFrame, game_id: str
) -> pl.DataFrame:
    """Map the two raw V3 team-level frames to ``_SCHEMA``.

    ``adv_team`` columns used: ``teamId``, ``offensiveRating``,
    ``defensiveRating``, ``netRating``, ``pace``,
    ``effectiveFieldGoalPercentage``, ``offensiveReboundPercentage``.
    ``ff_team`` columns used: ``teamId``, ``freeThrowAttemptRate``,
    ``teamTurnoverPercentage``. Both are 2-row (one per team) frames joined
    on ``teamId``.
    """
    if adv_team.is_empty() or ff_team.is_empty():
        return pl.DataFrame(schema={c: pl.Null for c in _SCHEMA})
    joined = adv_team.select(
        [
            "teamId",
            "offensiveRating",
            "defensiveRating",
            "netRating",
            "pace",
            "effectiveFieldGoalPercentage",
            "offensiveReboundPercentage",
        ]
    ).join(
        ff_team.select(["teamId", "freeThrowAttemptRate", "teamTurnoverPercentage"]),
        on="teamId",
    )
    out = joined.select(
        pl.lit(game_id).alias("game_id"),
        pl.col("teamId").alias("team_id"),
        pl.col("offensiveRating").alias("off_rating"),
        pl.col("defensiveRating").alias("def_rating"),
        pl.col("netRating").alias("net_rating"),
        pl.col("pace").alias("pace"),
        pl.col("effectiveFieldGoalPercentage").alias("efg_pct"),
        pl.col("teamTurnoverPercentage").alias("tov_pct"),
        pl.col("offensiveReboundPercentage").alias("oreb_pct"),
        pl.col("freeThrowAttemptRate").alias("ft_rate"),
    )
    return out


def pull_game_team_advanced(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
) -> pl.DataFrame:
    """Fetch (or load cached) team advanced stats for one game; upsert into table.

    A played game's advanced box score is never legitimately empty, so the
    fetch is retried a few times on an empty response and, failing that,
    marked 'failed' rather than silently cached as 'done'
    (``allow_empty=False``), same contract as ``pull_game_boxscore``.
    """
    df = fetch_cached(
        con,
        SOURCE,
        game_id,
        lambda: _fetch_team_advanced_with_retry(game_id, rate_limiter),
        data_dir=data_dir,
        rate_limiter=rate_limiter,
        allow_empty=False,
    )
    upsert_team_game_advanced(con, df)
    return df


def upsert_team_game_advanced(con: duckdb.DuckDBPyConnection, df: pl.DataFrame) -> None:
    """Idempotently load a team-advanced DataFrame into ``team_game_advanced``."""
    upsert_rows(con, "team_game_advanced", _SCHEMA, ["game_id", "team_id"], df)
