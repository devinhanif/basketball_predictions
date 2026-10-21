"""Preseason box scores into their own tables (never the tables models read).

``nba.ingest.games`` drops preseason (game_id prefix '001') on purpose and that filter is
untouched. This module pulls those games separately:

1. ``fetch``: LeagueGameFinder(season_type_nullable='Pre Season') per season, then
   BoxScoreTraditionalV3 per game. Every raw response is written once as parquet under a raw
   directory (``games_<season>.parquet``, ``box_<game_id>.parquet``); an existing file is never
   rewritten or refetched; an empty response is never written (so a rerun retries it).
   No database is needed for this stage.
2. ``load``: build ``preseason_games`` and ``preseason_player_game_stats`` from the raw files in
   one short transaction. Column semantics follow ``player_game_stats`` (DNP: NULL minutes).
3. ``manifest``: table row counts plus a sha256 per raw file, as JSON.

Preseason rows are NOT outcomes for any model. Season is the season start year ('2025-26' -> 2025).
nba_api is imported lazily so importing this module needs no network.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

import duckdb
import polars as pl

from nba.db.connect import DEFAULT_DB_PATH, connect_with_retry
from nba.ingest.boxscores import _normalize_boxscore_v3_frame
from nba.ingest.cache import RateLimiter

PRESEASON_PREFIX = "001"
MIN_VALID_TEAM_ID = 1610612737
MAX_VALID_TEAM_ID = 1610612766
DEFAULT_SEASONS = (2022, 2023, 2024, 2025, 2026)
_MAX_ATTEMPTS = 3
_STOP_AFTER_CONSECUTIVE_FAILURES = 5

GAMES_COLUMNS = [
    "game_id",
    "game_date",
    "season",
    "home_team",
    "away_team",
    "home_pts",
    "away_pts",
    "loaded_at",
]
STATS_COLUMNS = [
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
    "fga",
    "fta",
    "starter",
    "loaded_at",
]

DDL = """
CREATE TABLE IF NOT EXISTS preseason_games (
    game_id VARCHAR PRIMARY KEY,
    game_date DATE,
    season INT,
    home_team INT,
    away_team INT,
    home_pts INT,
    away_pts INT,
    loaded_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS preseason_player_game_stats (
    game_id VARCHAR,
    player_id INT,
    team_id INT,
    minutes FLOAT,
    pts INT,
    reb INT,
    ast INT,
    fg3m INT,
    stl INT,
    blk INT,
    tov INT,
    fga INT,
    fta INT,
    starter BOOLEAN,
    loaded_at TIMESTAMP
);
"""


def season_string(season: int) -> str:
    """2025 -> '2025-26'."""
    return f"{season}-{(season + 1) % 100:02d}"


def games_path(raw_dir: Path, season: int) -> Path:
    return raw_dir / f"games_{season}.parquet"


def box_path(raw_dir: Path, game_id: str) -> Path:
    return raw_dir / f"box_{game_id}.parquet"


def write_once(df: pl.DataFrame, path: Path) -> bool:
    """Write ``df`` to ``path`` unless it exists or is empty. Returns True if written."""
    if path.exists() or df.is_empty():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.part")
    df.write_parquet(tmp)
    tmp.rename(path)
    return True


def _fetch_games_raw(season: int) -> pl.DataFrame:
    from nba_api.stats.endpoints import leaguegamefinder  # lazy import

    finder = leaguegamefinder.LeagueGameFinder(
        season_nullable=season_string(season),
        season_type_nullable="Pre Season",
        league_id_nullable="00",
        timeout=60,
    )
    return pl.from_pandas(finder.get_data_frames()[0])


def _fetch_box_raw(game_id: str) -> pl.DataFrame:
    from nba_api.stats.endpoints import boxscoretraditionalv3  # lazy import

    box = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id, timeout=60)
    return pl.from_pandas(box.get_data_frames()[0])


def _with_retry(fn: Callable[[], pl.DataFrame], sleep: Callable[[float], None]) -> pl.DataFrame:
    """Up to ``_MAX_ATTEMPTS`` tries; an empty frame counts as a failed try (returned empty)."""
    last: pl.DataFrame = pl.DataFrame()
    for attempt in range(_MAX_ATTEMPTS):
        try:
            last = fn()
        except Exception as exc:  # network / throttle: retry, then give up
            print(
                f"  attempt {attempt + 1} error: {type(exc).__name__}: {str(exc)[:100]}", flush=True
            )
            last = pl.DataFrame()
        if not last.is_empty():
            return last
        if attempt < _MAX_ATTEMPTS - 1:
            sleep(5.0 * (attempt + 1))
    return last


def normalize_games(raw: pl.DataFrame, season: int) -> pl.DataFrame:
    """One row per preseason game between two NBA franchises (both ids in the valid range).

    LeagueGameFinder returns one row per team per game; the home side is the row whose team
    abbreviation is the one after '@' in the other row's MATCHUP, or the row with 'vs.'.
    Games with a non-NBA side (international exhibitions) are dropped here and counted by
    :func:`fetch_games_dropped`.
    """
    if raw.is_empty():
        return pl.DataFrame(schema={c: pl.Null for c in GAMES_COLUMNS[:-1]})
    pre = raw.filter(pl.col("GAME_ID").str.starts_with(PRESEASON_PREFIX))
    is_home = (
        pl.when(pl.col("MATCHUP").str.contains("@"))
        .then(
            pl.col("MATCHUP").str.split("@").list.last().str.strip_chars()
            == pl.col("TEAM_ABBREVIATION")
        )
        .otherwise(True)
    )
    home = pre.filter(is_home).unique(subset=["GAME_ID"], keep="first")
    away = pre.filter(~is_home).unique(subset=["GAME_ID"], keep="first")
    out = home.join(away, on="GAME_ID", suffix="_away").select(
        pl.col("GAME_ID").alias("game_id"),
        pl.col("GAME_DATE").cast(pl.Utf8).str.to_date().alias("game_date"),
        pl.lit(season).cast(pl.Int32).alias("season"),
        pl.col("TEAM_ID").alias("home_team"),
        pl.col("TEAM_ID_away").alias("away_team"),
        pl.col("PTS").alias("home_pts"),
        pl.col("PTS_away").alias("away_pts"),
    )
    return out.filter(
        pl.col("home_team").is_between(MIN_VALID_TEAM_ID, MAX_VALID_TEAM_ID)
        & pl.col("away_team").is_between(MIN_VALID_TEAM_ID, MAX_VALID_TEAM_ID)
    ).sort("game_id")


def fetch_season(
    raw_dir: Path,
    season: int,
    *,
    limiter: RateLimiter,
    games_fetch: Callable[[int], pl.DataFrame] = _fetch_games_raw,
    box_fetch: Callable[[str], pl.DataFrame] = _fetch_box_raw,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Fetch (or reuse) one season's game list and box scores into ``raw_dir``.

    Returns counts: games listed, boxes cached before, fetched now, failed ids.
    """
    gp = games_path(raw_dir, season)
    if gp.exists():
        raw_games = pl.read_parquet(gp)
        listed_new = False
    else:
        limiter.wait()
        raw_games = _with_retry(lambda: games_fetch(season), sleep)
        listed_new = write_once(raw_games, gp)
    games = normalize_games(raw_games, season)
    ids = games["game_id"].to_list()
    cached = fetched = 0
    failed: list[str] = []
    streak = 0
    for i, gid in enumerate(ids, 1):
        bp = box_path(raw_dir, gid)
        if bp.exists():
            cached += 1
            continue
        limiter.wait()
        df = _with_retry(lambda gid=gid: box_fetch(gid), sleep)  # type: ignore[misc]
        if df.is_empty():
            failed.append(gid)
            streak += 1
            print(f"FAILED box {gid} (empty or error)", flush=True)
            if streak >= _STOP_AFTER_CONSECUTIVE_FAILURES:
                print("stopping early after consecutive failures; rerun resumes", flush=True)
                break
            continue
        streak = 0
        write_once(df, bp)
        fetched += 1
        if i % 25 == 0:
            print(f"PROGRESS {season} {i}/{len(ids)} fetched={fetched}", flush=True)
    dropped = 0 if raw_games.is_empty() else _count_dropped(raw_games, len(ids))
    return {
        "season": season,
        "games_listed_raw_rows": raw_games.height,
        "games_kept": len(ids),
        "games_dropped_non_nba_side": dropped,
        "list_written_now": listed_new,
        "boxes_cached": cached,
        "boxes_fetched": fetched,
        "failed": failed,
    }


def _count_dropped(raw: pl.DataFrame, kept: int) -> int:
    n = raw.filter(pl.col("GAME_ID").str.starts_with(PRESEASON_PREFIX))["GAME_ID"].n_unique()
    return n - kept


_MINUTES_OK = r"^(\d+:\d{1,2}|PT\d+(\.\d+)?M\d+(\.\d+)?S|\d+(\.\d+)?)$"


def _blank_bad_minutes(raw: pl.DataFrame) -> pl.DataFrame:
    """Unparseable or empty minutes (e.g. '-1:0-57' in game 0012300003) become NULL (unknown).

    The raw file is untouched; the cleaned table carries NULL, which a ``minutes > 0`` filter
    excludes, rather than a guessed value.
    """
    return raw.with_columns(
        pl.when(pl.col("minutes").cast(pl.Utf8).str.contains(_MINUTES_OK))
        .then(pl.col("minutes"))
        .otherwise(None)
        .alias("minutes")
    )


def build_tables(
    raw_dir: Path, seasons: list[int], loaded_at: dt.datetime
) -> tuple[pl.DataFrame, pl.DataFrame, list[str]]:
    """Clean tables from the raw files. Returns (games, player stats, game ids without a box)."""
    games_parts: list[pl.DataFrame] = []
    stat_parts: list[pl.DataFrame] = []
    missing: list[str] = []
    for season in seasons:
        gp = games_path(raw_dir, season)
        if not gp.exists():
            continue
        games = normalize_games(pl.read_parquet(gp), season)
        games_parts.append(games)
        for gid in games["game_id"].to_list():
            bp = box_path(raw_dir, gid)
            if not bp.exists():
                missing.append(gid)
                continue
            norm = _normalize_boxscore_v3_frame(_blank_bad_minutes(pl.read_parquet(bp)), gid)
            if not norm.is_empty():
                stat_parts.append(norm.select(STATS_COLUMNS[:-1]))
    games_all = (
        pl.concat(games_parts)
        if games_parts
        else pl.DataFrame(schema={c: pl.Null for c in GAMES_COLUMNS[:-1]})
    )
    stats_all = (
        pl.concat(stat_parts)
        if stat_parts
        else pl.DataFrame(schema={c: pl.Null for c in STATS_COLUMNS[:-1]})
    )
    # only games that have a box get a games row, so the two tables join one-to-many cleanly
    have_box = set(stats_all["game_id"].unique().to_list()) if not stats_all.is_empty() else set()
    games_all = games_all.filter(pl.col("game_id").is_in(list(have_box)))
    games_all = games_all.with_columns(pl.lit(loaded_at).alias("loaded_at"))
    stats_all = stats_all.with_columns(pl.lit(loaded_at).alias("loaded_at"))
    return games_all, stats_all, missing


def load_tables(
    con: duckdb.DuckDBPyConnection, games: pl.DataFrame, stats: pl.DataFrame
) -> dict[str, int]:
    """Idempotent load in one transaction (delete-then-insert by game_id). New tables only."""
    con.execute("BEGIN TRANSACTION")
    try:
        for stmt in DDL.split(";"):
            if stmt.strip():
                con.execute(stmt)
        ids = games["game_id"].to_list()
        if ids:
            ph = ", ".join(["?"] * len(ids))
            con.execute(f"DELETE FROM preseason_player_game_stats WHERE game_id IN ({ph})", ids)
            con.execute(f"DELETE FROM preseason_games WHERE game_id IN ({ph})", ids)
            g_ph = ", ".join(["?"] * len(GAMES_COLUMNS))
            con.executemany(
                f"INSERT INTO preseason_games ({', '.join(GAMES_COLUMNS)}) VALUES ({g_ph})",
                games.select(GAMES_COLUMNS).rows(),
            )
            s_ph = ", ".join(["?"] * len(STATS_COLUMNS))
            con.executemany(
                f"INSERT INTO preseason_player_game_stats ({', '.join(STATS_COLUMNS)}) "
                f"VALUES ({s_ph})",
                stats.select(STATS_COLUMNS).rows(),
            )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return table_counts(con)


def table_counts(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in ("preseason_games", "preseason_player_game_stats"):
        row = con.execute(f"SELECT count(*) FROM {t}").fetchone()
        out[t] = int(row[0]) if row else 0
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def raw_manifest(raw_dir: Path) -> dict[str, object]:
    files = sorted(raw_dir.glob("*.parquet"))
    entries = {p.name: sha256_file(p) for p in files}
    tree = hashlib.sha256("\n".join(f"{k} {v}" for k, v in entries.items()).encode()).hexdigest()
    return {
        "n_files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
        "tree_sha256": tree,
        "files": entries,
    }


def gate_counts(con: duckdb.DuckDBPyConnection) -> dict[str, object]:
    """Minimum-data gate numbers (read-only). Regular-season identifiers only for 2024/2025.

    ``changers``: players whose first regular-season played game (minutes > 0, game_id '002...')
    of season S is for a team other than their last played regular-season team of S-1.
    ``with_pre2``: of those, players with >= 2 preseason games with minutes > 0 for the new team.
    """
    per_season = con.execute(
        """
        SELECT g.season, count(DISTINCT g.game_id) AS games,
               count(*) FILTER (WHERE s.minutes > 0) AS player_games_played,
               count(DISTINCT s.player_id) FILTER (WHERE s.minutes > 0) AS players
        FROM preseason_games g JOIN preseason_player_game_stats s USING (game_id)
        GROUP BY g.season ORDER BY g.season
        """
    ).fetchall()
    changers: dict[int, dict[str, int]] = {}
    for season in (2024, 2025):
        row = con.execute(
            """
            WITH reg AS (
                SELECT s.player_id, s.team_id, g.game_date, g.season, g.game_id
                FROM player_game_stats s JOIN games g USING (game_id)
                WHERE s.minutes > 0 AND g.game_id LIKE '002%' AND g.season IN (?, ?)
            ),
            first_cur AS (
                SELECT player_id, arg_min(team_id, (game_date, game_id)) AS new_team
                FROM reg WHERE season = ? GROUP BY player_id
            ),
            last_prev AS (
                SELECT player_id, arg_max(team_id, (game_date, game_id)) AS old_team
                FROM reg WHERE season = ? GROUP BY player_id
            ),
            ch AS (
                SELECT f.player_id, f.new_team FROM first_cur f JOIN last_prev l USING (player_id)
                WHERE f.new_team <> l.old_team
            ),
            pre AS (
                SELECT s.player_id, s.team_id, count(*) AS n
                FROM preseason_player_game_stats s JOIN preseason_games g USING (game_id)
                WHERE s.minutes > 0 AND g.season = ?
                GROUP BY s.player_id, s.team_id
            )
            SELECT (SELECT count(*) FROM ch),
                   (SELECT count(*) FROM ch JOIN pre ON pre.player_id = ch.player_id
                        AND pre.team_id = ch.new_team WHERE pre.n >= 2),
                   (SELECT count(*) FROM ch JOIN pre ON pre.player_id = ch.player_id
                        AND pre.team_id = ch.new_team WHERE pre.n >= 1)
            """,
            [season - 1, season, season, season - 1, season],
        ).fetchone()
        assert row is not None
        changers[season] = {
            "changers": int(row[0]),
            "with_ge2_preseason_games_new_team": int(row[1]),
            "with_ge1_preseason_game_new_team": int(row[2]),
        }
    return {
        "per_season": [
            {
                "season": int(r[0]),
                "games": int(r[1]),
                "player_games_minutes_gt0": int(r[2]),
                "distinct_players": int(r[3]),
            }
            for r in per_season
        ],
        "summer_team_changers": changers,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m nba.ingest.preseason")
    p.add_argument(
        "--seasons",
        nargs="+",
        type=int,
        default=list(DEFAULT_SEASONS),
        help="season start years, e.g. 2022 2026",
    )
    p.add_argument("--raw-dir", required=True, help="write-once raw parquet directory")
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p.add_argument("--fetch", action="store_true", help="pull raw files (network, no DB)")
    p.add_argument("--load", action="store_true", help="build and load the two tables (DB write)")
    p.add_argument("--gate", action="store_true", help="print gate counts (DB read-only)")
    p.add_argument("--manifest-out", default=None, help="write counts + raw shas JSON here")
    p.add_argument("--lock-wait-s", type=float, default=900.0)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raw_dir = Path(args.raw_dir)
    seasons = list(args.seasons)
    if args.fetch:
        limiter = RateLimiter(1.0)
        for season in seasons:
            print(
                json.dumps(fetch_season(raw_dir, season, limiter=limiter), default=str), flush=True
            )
    counts: dict[str, int] | None = None
    if args.load:
        games, stats, missing = build_tables(
            raw_dir, seasons, dt.datetime.now(dt.UTC).replace(tzinfo=None)
        )
        print(f"built games={games.height} stats={stats.height} games_without_box={len(missing)}")
        con = connect_with_retry(
            args.db, max_wait_s=args.lock_wait_s, first_delay_s=30.0, max_delay_s=30.0
        )
        try:
            counts = load_tables(con, games, stats)
        finally:
            con.close()
        print(json.dumps({"loaded_counts": counts}))
    if args.gate or args.manifest_out:
        con = duckdb.connect(args.db, read_only=True)
        try:
            counts = table_counts(con)
            gate = gate_counts(con) if args.gate else None
        finally:
            con.close()
        if gate is not None:
            print(json.dumps({"gate": gate}, indent=1))
        if args.manifest_out:
            payload = {
                "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                "table_counts": counts,
                "raw": raw_manifest(raw_dir),
                "gate": gate,
            }
            Path(args.manifest_out).write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
