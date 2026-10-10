"""Backfill post-game nba_api sources for the games already in ``games``.

Sources (``SOURCES``): tracking, hustle, officials, matchups, shots, coaches.
All are POST-GAME data -- see the leakage-contract comment in nba/db/schema.sql
and docs/NEW_DATA_SOURCES.md.

Two-phase design (DuckDB is single-writer, so the multi-hour network phase
must not hold a write lock on nba.duckdb):

1. ``fetch_source`` -- rate-limited, retried, resumable. Writes one parquet per
   key under ``data/<source>/`` and records progress in a small per-source
   sidecar DuckDB (``data/<source>/ingest_log.duckdb``). Never touches
   nba.duckdb. Empty/malformed responses are NEVER cached as done.
2. ``load_source`` -- loads cached parquet into nba.duckdb in short batched
   transactions (delete-then-insert per batch, so idempotent).

nba_api is imported lazily so this module is safe to import offline.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from nba.ingest.boxscores import _parse_minutes
from nba.ingest.cache import (
    DEFAULT_DATA_DIR,
    CircuitBreaker,
    RateLimiter,
    cache_path_for,
    fetch_cached,
    insert_rows,
    open_db,
    yield_to,
)

SOURCES = ["tracking", "hustle", "officials", "shots", "matchups", "coaches"]

_MAX_FETCH_ATTEMPTS = 4
_RETRY_BACKOFF_S = 2.0  # doubles each attempt

SHOT_SEASON_TYPES = {"RS": "Regular Season", "PO": "Playoffs", "PI": "PlayIn"}

_TRACKING_COLS = [
    "position",
    "comment",
    "minutes",
    "speed",
    "distance",
    "rebound_chances_offensive",
    "rebound_chances_defensive",
    "rebound_chances_total",
    "touches",
    "secondary_assists",
    "free_throw_assists",
    "passes",
    "assists",
    "contested_field_goals_made",
    "contested_field_goals_attempted",
    "contested_field_goal_percentage",
    "uncontested_field_goals_made",
    "uncontested_field_goals_attempted",
    "uncontested_field_goals_percentage",
    "field_goal_percentage",
    "defended_at_rim_field_goals_made",
    "defended_at_rim_field_goals_attempted",
    "defended_at_rim_field_goal_percentage",
]
_HUSTLE_COLS = [
    "position",
    "comment",
    "minutes",
    "points",
    "contested_shots",
    "contested_shots2pt",
    "contested_shots3pt",
    "deflections",
    "charges_drawn",
    "screen_assists",
    "screen_assist_points",
    "loose_balls_recovered_offensive",
    "loose_balls_recovered_defensive",
    "loose_balls_recovered_total",
    "offensive_box_outs",
    "defensive_box_outs",
    "box_out_player_team_rebounds",
    "box_out_player_rebounds",
    "box_outs",
]
_MATCHUP_COLS = [
    "matchup_minutes",
    "partial_possessions",
    "percentage_defender_total_time",
    "percentage_offensive_total_time",
    "percentage_total_time_both_on",
    "switches_on",
    "player_points",
    "team_points",
    "matchup_assists",
    "matchup_potential_assists",
    "matchup_turnovers",
    "matchup_blocks",
    "matchup_field_goals_made",
    "matchup_field_goals_attempted",
    "matchup_field_goals_percentage",
    "matchup_three_pointers_made",
    "matchup_three_pointers_attempted",
    "matchup_three_pointers_percentage",
    "help_blocks",
    "help_field_goals_made",
    "help_field_goals_attempted",
    "help_field_goals_percentage",
    "matchup_free_throws_made",
    "matchup_free_throws_attempted",
    "shooting_fouls",
]
_SHOT_COLS = [
    "game_id",
    "game_event_id",
    "player_id",
    "team_id",
    "period",
    "clock_seconds",
    "loc_x",
    "loc_y",
    "shot_distance",
    "shot_zone_basic",
    "shot_zone_area",
    "shot_zone_range",
    "action_type",
    "shot_type",
    "made",
]
_COACH_COLS = ["season", "team_id", "coach_id", "name", "coach_type", "is_assistant"]
_OFFICIAL_COLS = ["game_id", "official_id", "name", "jersey"]

#: source -> (target table, columns, delete/conflict key columns)
TABLES: dict[str, tuple[str, list[str], list[str]]] = {
    "tracking": (
        "player_game_tracking",
        ["game_id", "player_id", "team_id", *_TRACKING_COLS],
        ["game_id"],
    ),
    "hustle": (
        "player_game_hustle",
        ["game_id", "player_id", "team_id", *_HUSTLE_COLS],
        ["game_id"],
    ),
    "officials": ("game_officials", _OFFICIAL_COLS, ["game_id"]),
    "matchups": (
        "player_game_matchups",
        ["game_id", "off_player_id", "def_player_id", "team_id", "def_team_id", *_MATCHUP_COLS],
        ["game_id"],
    ),
    "shots": ("shots", _SHOT_COLS, ["game_id"]),
    "coaches": ("team_coaches", _COACH_COLS, ["season", "team_id"]),
}


class MalformedResponseError(RuntimeError):
    """The endpoint answered but the frame lacks the expected columns."""


def snake(name: str) -> str:
    """camelCase / UPPER_SNAKE -> snake_case (``reboundChancesOffensive`` -> ...)."""
    return re.sub(r"(?<!^)(?<![A-Z_])(?=[A-Z])", "_", name).lower()


def _snake_frame(raw: pl.DataFrame) -> pl.DataFrame:
    return raw.rename({c: snake(c) for c in raw.columns})


def _require(df: pl.DataFrame, cols: list[str], what: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise MalformedResponseError(f"{what}: missing columns {missing}")


def _minutes_col(values: list[Any]) -> list[float | None]:
    return [_parse_minutes(v) if isinstance(v, str) else v for v in values]


def _normalize_player_frame(
    raw: pl.DataFrame, game_id: str, cols: list[str], what: str
) -> pl.DataFrame:
    """Shared by tracking and hustle (identical identity columns)."""
    if raw.is_empty():
        return pl.DataFrame()
    df = _snake_frame(raw).rename({"person_id": "player_id"})
    _require(df, ["player_id", "team_id", *cols], what)
    data: dict[str, list[Any]] = {
        "game_id": [game_id] * len(df),
        "player_id": df["player_id"].to_list(),
        "team_id": df["team_id"].to_list(),
    }
    for c in cols:
        data[c] = _minutes_col(df[c].to_list()) if c == "minutes" else df[c].to_list()
    return pl.DataFrame(data, strict=False)


def normalize_tracking(raw: pl.DataFrame, game_id: str) -> pl.DataFrame:
    return _normalize_player_frame(raw, game_id, _TRACKING_COLS, "tracking")


def normalize_hustle(raw: pl.DataFrame, game_id: str) -> pl.DataFrame:
    return _normalize_player_frame(raw, game_id, _HUSTLE_COLS, "hustle")


def normalize_officials(frames: list[pl.DataFrame], game_id: str) -> pl.DataFrame:
    """Pick the officials frame out of BoxScoreSummaryV3's dataset list.

    Identified by columns (``personId`` + ``name`` and no ``teamId``) rather
    than position. BoxScoreSummaryV2 returns empty Officials for games on or
    after 2025-04-10; V3 is populated for every season.
    """
    for f in frames:
        cols = set(f.columns)
        if {"personId", "name", "jerseyNum"} <= cols and "teamId" not in cols:
            if f.is_empty():
                return pl.DataFrame()
            return pl.DataFrame(
                {
                    "game_id": [game_id] * len(f),
                    "official_id": f["personId"].to_list(),
                    "name": f["name"].to_list(),
                    "jersey": [None if v is None else str(v) for v in f["jerseyNum"].to_list()],
                },
                strict=False,
            )
    raise MalformedResponseError("officials: no officials frame in BoxScoreSummaryV3 response")


_MATCHUP_RENAMES = {"person_id_off": "off_player_id", "person_id_def": "def_player_id"}


def normalize_matchups(raw: pl.DataFrame, game_id: str) -> pl.DataFrame:
    """``team_id`` is the OFFENSIVE player's team; ``def_team_id`` is filled at load time."""
    if raw.is_empty():
        return pl.DataFrame()
    df = _snake_frame(raw).rename(_MATCHUP_RENAMES)
    # matchup_minutes arrives as 'M:SS'; the numeric twin is matchup_minutes_sort (seconds).
    _require(df, ["off_player_id", "def_player_id", "team_id", *_MATCHUP_COLS], "matchups")
    data: dict[str, list[Any]] = {
        "game_id": [game_id] * len(df),
        "off_player_id": df["off_player_id"].to_list(),
        "def_player_id": df["def_player_id"].to_list(),
        "team_id": df["team_id"].to_list(),
        "def_team_id": [None] * len(df),
    }
    for c in _MATCHUP_COLS:
        vals = df[c].to_list()
        data[c] = _minutes_col(vals) if c == "matchup_minutes" else vals
    return pl.DataFrame(data, strict=False).unique(
        subset=["game_id", "off_player_id", "def_player_id"], keep="first", maintain_order=True
    )


def normalize_shots(raw: pl.DataFrame) -> pl.DataFrame:
    if raw.is_empty():
        return pl.DataFrame()
    df = _snake_frame(raw)
    _require(
        df,
        [
            "game_id",
            "game_event_id",
            "player_id",
            "team_id",
            "period",
            "minutes_remaining",
            "seconds_remaining",
            "loc_x",
            "loc_y",
            "shot_distance",
            "shot_zone_basic",
            "shot_zone_area",
            "shot_zone_range",
            "action_type",
            "shot_type",
            "shot_made_flag",
        ],
        "shots",
    )
    return df.select(
        "game_id",
        "game_event_id",
        "player_id",
        "team_id",
        "period",
        (pl.col("minutes_remaining") * 60 + pl.col("seconds_remaining")).alias("clock_seconds"),
        "loc_x",
        "loc_y",
        "shot_distance",
        "shot_zone_basic",
        "shot_zone_area",
        "shot_zone_range",
        "action_type",
        "shot_type",
        (pl.col("shot_made_flag") == 1).alias("made"),
    ).unique(subset=["game_id", "game_event_id"], keep="first", maintain_order=True)


def normalize_coaches(raw: pl.DataFrame) -> pl.DataFrame:
    """CommonTeamRoster's Coaches dataset (``get_data_frames()[1]``)."""
    if raw.is_empty():
        return pl.DataFrame()
    df = _snake_frame(raw)
    _require(
        df, ["season", "team_id", "coach_id", "coach_name", "coach_type", "is_assistant"], "coaches"
    )
    return df.select(
        pl.col("season").cast(pl.Int64),
        "team_id",
        "coach_id",
        pl.col("coach_name").alias("name"),
        "coach_type",
        "is_assistant",
    ).unique(subset=["season", "team_id", "coach_id"], keep="first", maintain_order=True)


# --------------------------------------------------------------------------
# network fetchers (lazy nba_api imports)
# --------------------------------------------------------------------------


def _frames(endpoint: Any) -> list[pl.DataFrame]:
    return [pl.from_pandas(d) for d in endpoint.get_data_frames()]


def _fetch_tracking(game_id: str) -> pl.DataFrame:
    from nba_api.stats.endpoints import boxscoreplayertrackv3

    return normalize_tracking(
        _frames(boxscoreplayertrackv3.BoxScorePlayerTrackV3(game_id=game_id))[0], game_id
    )


def _fetch_hustle(game_id: str) -> pl.DataFrame:
    from nba_api.stats.endpoints import boxscorehustlev2

    return normalize_hustle(_frames(boxscorehustlev2.BoxScoreHustleV2(game_id=game_id))[0], game_id)


def _fetch_officials(game_id: str) -> pl.DataFrame:
    from nba_api.stats.endpoints import boxscoresummaryv3

    return normalize_officials(
        _frames(boxscoresummaryv3.BoxScoreSummaryV3(game_id=game_id)), game_id
    )


def _fetch_matchups(game_id: str) -> pl.DataFrame:
    from nba_api.stats.endpoints import boxscorematchupsv3

    return normalize_matchups(
        _frames(boxscorematchupsv3.BoxScoreMatchupsV3(game_id=game_id))[0], game_id
    )


def shot_key(team_id: int, season_str: str, season_type: str) -> str:
    return f"{team_id}_{season_str}_{season_type}"


def _fetch_shots(team_id: int, season_str: str, season_type: str) -> pl.DataFrame:
    from nba_api.stats.endpoints import shotchartdetail

    ep = shotchartdetail.ShotChartDetail(
        team_id=team_id,
        player_id=0,
        season_nullable=season_str,
        season_type_all_star=SHOT_SEASON_TYPES[season_type],
        context_measure_simple="FGA",
    )
    return normalize_shots(_frames(ep)[0])


def _fetch_coaches(team_id: int, season_str: str) -> pl.DataFrame:
    from nba_api.stats.endpoints import commonteamroster

    return normalize_coaches(_frames(commonteamroster.CommonTeamRoster(team_id, season_str))[1])


def _with_retry(
    fn: Callable[[], pl.DataFrame], *, sleep: Callable[[float], None] = time.sleep
) -> pl.DataFrame:
    """Retry on exception or empty frame with exponential backoff.

    Returns the last (possibly empty) frame; the caller's
    ``fetch_cached(allow_empty=...)`` decides whether empty is a failure.
    """
    last: pl.DataFrame | None = None
    err: Exception | None = None
    for attempt in range(_MAX_FETCH_ATTEMPTS):
        try:
            last = fn()
            err = None
            if not last.is_empty():
                return last
        except Exception as exc:  # network/timeouts/malformed -> retry
            err = exc
        if attempt < _MAX_FETCH_ATTEMPTS - 1:
            sleep(_RETRY_BACKOFF_S * (2**attempt))
    if err is not None:
        raise err
    assert last is not None
    return last


# --------------------------------------------------------------------------
# scope + fetch driver
# --------------------------------------------------------------------------


@dataclass
class FetchSummary:
    source: str
    total: int = 0
    cached: int = 0
    fetched: int = 0
    failed: list[str] = field(default_factory=list)
    stopped_early: bool = False


def scope_game_ids(nba_db: str | Path) -> list[str]:
    """Every game_id in ``games`` (read-only; never writes nba.duckdb)."""
    con = duckdb.connect(str(nba_db), read_only=True)
    try:
        return [r[0] for r in con.execute("SELECT game_id FROM games ORDER BY game_id").fetchall()]
    finally:
        con.close()


def scope_team_seasons(nba_db: str | Path) -> list[tuple[int, int]]:
    """Distinct (team_id, season_int) pairs present in ``games``."""
    con = duckdb.connect(str(nba_db), read_only=True)
    try:
        return [
            (r[0], r[1])
            for r in con.execute(
                """
                SELECT team_id, season FROM (
                  SELECT home_team AS team_id, season FROM games
                  UNION SELECT away_team, season FROM games)
                ORDER BY season, team_id
                """
            ).fetchall()
        ]
    finally:
        con.close()


def season_str(season_int: int) -> str:
    return f"{season_int}-{(season_int + 1) % 100:02d}"


def sidecar_path(source: str, data_dir: Path = DEFAULT_DATA_DIR) -> Path:
    return data_dir / source / "ingest_log.duckdb"


Work = tuple[str, Callable[[], pl.DataFrame], bool]


def build_keys(source: str, nba_db: str | Path) -> list[Work]:
    """(key, fetch_fn, allow_empty) for every unit of work in scope for ``source``."""
    out: list[Work] = []
    if source in ("tracking", "hustle", "officials", "matchups"):
        fetchers: dict[str, Callable[[str], pl.DataFrame]] = {
            "tracking": _fetch_tracking,
            "hustle": _fetch_hustle,
            "officials": _fetch_officials,
            "matchups": _fetch_matchups,
        }
        fn = fetchers[source]
        for gid in scope_game_ids(nba_db):
            out.append((gid, partial(fn, gid), False))
    elif source == "shots":
        for team_id, season in scope_team_seasons(nba_db):
            for st in SHOT_SEASON_TYPES:
                # Regular season is never legitimately empty; playoffs / play-in
                # are for teams that did not qualify. Coverage vs `games` is
                # audited after loading (see shots_fga_reconciliation).
                out.append(
                    (
                        shot_key(team_id, season_str(season), st),
                        partial(_fetch_shots, team_id, season_str(season), st),
                        st != "RS",
                    )
                )
    elif source == "coaches":
        for team_id, season in scope_team_seasons(nba_db):
            out.append(
                (
                    f"{team_id}_{season_str(season)}",
                    partial(_fetch_coaches, team_id, season_str(season)),
                    False,
                )
            )
    else:
        raise ValueError(f"unknown source {source!r}")
    return out


def filter_seasons(source: str, keys: list[Work], seasons: list[int]) -> list[Work]:
    """Keep game-id keyed work whose season (``00TYYxxxxx``) is in ``seasons``.

    Team-season keyed sources (shots, coaches) are returned unfiltered.
    """
    if source in ("shots", "coaches"):
        return keys
    yy = {f"{s % 100:02d}" for s in seasons}
    return [w for w in keys if w[0][3:5] in yy]


def order_newest_first(source: str, keys: list[Work]) -> list[Work]:
    """Newest season first for game-id keyed sources (``00TYYxxxxx``: YY = season start).

    Sorting on the 2-digit season then the id keeps playoffs (``004...``) inside their
    own season instead of ahead of every regular season. Team-season keyed sources
    (shots, coaches) are left in scope order.
    """
    if source in ("shots", "coaches"):
        return keys
    return sorted(keys, key=lambda w: (w[0][3:5], w[0]), reverse=True)


def fetch_source(
    source: str,
    nba_db: str | Path,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
    limit: int | None = None,
    newest_first: bool = False,
    log_every: int = 100,
    sleep: Callable[[float], None] = time.sleep,
    breaker_failures: int = 3,
    breaker_cooldown_s: float = 600.0,
    breaker_max_trips: int = 6,
    breaker_cooldown_cap_s: float | None = None,
    seasons: list[int] | None = None,
) -> FetchSummary:
    """Fetch every in-scope key for ``source`` into the parquet cache. Resumable.

    Circuit breaker: stats.nba.com throttles bursts by timing out every request. After
    ``breaker_failures`` consecutive failures, pause ``breaker_cooldown_s``; after
    ``breaker_max_trips`` pauses, stop early (failed keys stay un-done, so a rerun resumes).
    """
    keys = build_keys(source, nba_db)
    if seasons is not None:
        keys = filter_seasons(source, keys, seasons)
    if newest_first:
        keys = order_newest_first(source, keys)
    if limit is not None:
        keys = keys[:limit]
    summary = FetchSummary(source=source, total=len(keys))
    sidecar_path(source, data_dir).parent.mkdir(parents=True, exist_ok=True)
    log = open_db(sidecar_path(source, data_dir))
    try:
        t0 = time.monotonic()
        brk = CircuitBreaker(
            failures=breaker_failures,
            cooldown_s=breaker_cooldown_s,
            max_trips=breaker_max_trips,
            sleep=sleep,
            cooldown_cap_s=breaker_cooldown_cap_s,
        )
        for i, (key, fn, allow_empty) in enumerate(keys, 1):
            done = log.execute(
                "SELECT 1 FROM ingest_log WHERE source=? AND key=? AND status='done'",
                [source, key],
            ).fetchone()
            if done is not None and cache_path_for(source, key, data_dir).exists():
                summary.cached += 1
                continue
            yield_to()
            try:
                fetch_cached(
                    log,
                    source,
                    key,
                    partial(_with_retry, fn, sleep=sleep),
                    data_dir=data_dir,
                    rate_limiter=rate_limiter,
                    allow_empty=allow_empty,
                )
                summary.fetched += 1
                brk.success()
            except Exception as exc:  # logged failed; continue
                summary.failed.append(key)
                print(f"FAILED {source}[{key}]: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
                if brk.failure():
                    summary.stopped_early = True
                    break
            if i % log_every == 0:
                rate = summary.fetched / max(time.monotonic() - t0, 1e-9)
                print(
                    f"{source}: {i}/{len(keys)} cached={summary.cached} "
                    f"fetched={summary.fetched} failed={len(summary.failed)} {rate:.2f}/s",
                    flush=True,
                )
    finally:
        log.close()
    return summary


# --------------------------------------------------------------------------
# load phase (short write windows into nba.duckdb)
# --------------------------------------------------------------------------


def load_frames(con: duckdb.DuckDBPyConnection, source: str, df: pl.DataFrame) -> int:
    """Idempotently load a normalized frame: delete the frame's key groups, insert."""
    if df.is_empty():
        return 0
    table, columns, del_key = TABLES[source]
    if source == "officials":  # reviewed id merges (configs/officials_id_merges.yaml, T202)
        from nba.ingest.referees import canonical_official_frame

        df = canonical_official_frame(df)
    keys = df.select(del_key).unique().rows()
    cond = " AND ".join(f"{k} = ?" for k in del_key)
    con.execute("BEGIN")
    try:
        con.executemany(f"DELETE FROM {table} WHERE {cond}", keys)
        insert_rows(con, table, columns, df)
        if source == "matchups":
            con.execute(
                """
                UPDATE player_game_matchups m
                SET def_team_id = CASE WHEN m.team_id = g.home_team THEN g.away_team
                                       ELSE g.home_team END
                FROM games g WHERE g.game_id = m.game_id AND m.def_team_id IS NULL
                """
            )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return len(df)


def load_source(
    con: duckdb.DuckDBPyConnection,
    source: str,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    batch_files: int = 250,
) -> int:
    """Load every cached parquet for ``source`` whose keys are in scope; returns rows."""
    files = sorted((data_dir / source).glob("*.parquet"))
    in_scope: set[str] | None = None
    if source not in ("shots", "coaches"):
        in_scope = {r[0] for r in con.execute("SELECT game_id FROM games").fetchall()}
    total = 0
    for i in range(0, len(files), batch_files):
        frames = [pl.read_parquet(f) for f in files[i : i + batch_files]]
        frames = [f for f in frames if not f.is_empty()]
        if not frames:
            continue
        df = pl.concat(frames, how="diagonal_relaxed")
        if source == "shots":
            games = {r[0] for r in con.execute("SELECT game_id FROM games").fetchall()}
            df = df.filter(pl.col("game_id").is_in(games))
        elif in_scope is not None:
            df = df.filter(pl.col("game_id").is_in(in_scope))
        total += load_frames(con, source, df)
    return total


# --------------------------------------------------------------------------
# audits
# --------------------------------------------------------------------------


def coverage(con: duckdb.DuckDBPyConnection) -> list[tuple[str, int, int, int]]:
    """(table, season, games_covered, rows) per per-game table and season."""
    out: list[tuple[str, int, int, int]] = []
    specs = {
        "player_game_tracking": "game_id",
        "player_game_hustle": "game_id",
        "game_officials": "game_id",
        "player_game_matchups": "game_id",
        "shots": "game_id",
    }
    for table, gcol in specs.items():
        rows = con.execute(
            f"""
            SELECT g.season, count(DISTINCT t.{gcol}), count(*)
            FROM {table} t JOIN games g ON g.game_id = t.{gcol}
            GROUP BY g.season ORDER BY g.season
            """
        ).fetchall()
        out.extend((table, r[0], r[1], r[2]) for r in rows)
    for r in con.execute(
        "SELECT season, count(DISTINCT team_id), count(*) FROM team_coaches "
        "GROUP BY season ORDER BY season"
    ).fetchall():
        out.append(("team_coaches(teams)", r[0], r[1], r[2]))
    return out


def shots_fga_reconciliation(con: duckdb.DuckDBPyConnection) -> dict[str, float]:
    """Per-(game, team) shot-chart attempts vs box-score FGA.

    Compared only on (game, team) pairs where both exist; also reports games
    with box-score FGA but no shots at all.
    """
    row = con.execute(
        """
        WITH s AS (SELECT game_id, team_id, count(*) AS shot_fga FROM shots GROUP BY 1, 2),
             b AS (SELECT game_id, team_id, sum(fga) AS box_fga FROM player_game_stats
                   WHERE fga IS NOT NULL GROUP BY 1, 2)
        SELECT count(*), sum((shot_fga <> box_fga)::INT), avg(abs(shot_fga - box_fga)),
               avg(shot_fga - box_fga)
        FROM s JOIN b USING (game_id, team_id)
        """
    ).fetchone()
    assert row is not None
    n, mism, mae, bias = row
    missing = con.execute(
        """
        SELECT count(DISTINCT b.game_id) FROM player_game_stats b
        WHERE b.fga IS NOT NULL AND b.game_id NOT IN (SELECT game_id FROM shots)
        """
    ).fetchone()
    assert missing is not None
    return {
        "team_games_compared": float(n or 0),
        "mismatch_rate": float((mism or 0) / n) if n else float("nan"),
        "mean_abs_diff": float(mae) if mae is not None else float("nan"),
        "mean_signed_diff": float(bias) if bias is not None else float("nan"),
        "games_with_boxscore_but_no_shots": float(missing[0]),
    }
