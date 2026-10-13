"""History DB repair: ``games.national_tv`` (2019-21) and ``players_static`` (birth dates).

Both gaps failed the minimum-data gate of docs/prereg/PPLAY_MINUTES_VALID.md (checks 13, 14):
the history ``games.national_tv`` was empty and ``players_static`` had no rows, so a 2019-21 player
had a birth date only if he also appeared in the post-2022 player table, a NULL pattern that
depends on the future (play rate 0.62 when missing vs 0.86 present).

* national_tv comes from the raw schedules already held (``data/history/schedule/raw_*.parquet``),
  the first national TV broadcaster abbreviation, the same field the modern ScoreboardV2 pull
  stores. Schedule facts are known before tip. 2015-18 games have no held schedule and stay NULL.
* players_static rows come from the per-player ``CommonPlayerInfo`` cache
  (``data/players_static/<id>.parquet``). Ids with no cache file are fetched by ``fetch`` (one
  call each, resumable, never refetched); an empty response is never cached.

    uv run python -m nba.ingest.history_static_fix            # dry run, read-only
    uv run python -m nba.ingest.history_static_fix --apply    # heavy lock, backup, one transaction
    uv run python -m nba.ingest.history_static_fix fetch --apply   # network, then apply
"""

from __future__ import annotations

import argparse
import contextlib
import shutil
import signal
import sys
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from nba.ingest.cache import DEFAULT_DATA_DIR, RateLimiter
from nba.ingest.history_injury import HISTORY_DB, HISTORY_DIR
from nba.ingest.players_static import _SCHEMA, load_players_static, pull_players_static

HEAVY_LOCK = DEFAULT_DATA_DIR / "ops" / "heavy.lock"
BACKUP_DIR = DEFAULT_DATA_DIR / "backups"
#: History seasons the P(play) models train on (start year of the season).
FIRST_SEASON, LAST_SEASON = 2019, 2021
#: Earliest history season held (2013-14); the fetch and the apply cover FIRST_HELD..LAST_SEASON so
#: every played row of every history season can carry a position (SEQ_LEARNING_CURVE_2 gate D4).
FIRST_HELD = 2013
_NATIONAL_SUFFIX = "broadcasterAbbreviation"


def national_tv_from_schedules(schedule_dir: Path = HISTORY_DIR / "schedule") -> pl.DataFrame:
    """(game_id, national_tv) for every scheduled game with a national TV broadcaster.

    Takes the first national TV broadcaster (``nationalBroadcasters*_broadcasterAbbreviation``,
    index 0 when the file is indexed; a second slot is an overtime channel such as TNTOT). Empty
    strings become absent, so a game with no national broadcaster has no row (NULL in ``games``).
    """
    frames: list[pl.DataFrame] = []
    for path in sorted(schedule_dir.glob("raw_*.parquet")):
        d = pl.read_parquet(path)
        cols = [
            c
            for c in d.columns
            if c.startswith("nationalBroadcasters_") and c.endswith(_NATIONAL_SUFFIX)
        ]
        first = next((c for c in cols if "_0_" in c), cols[0] if cols else None)
        if first is None:
            continue
        frames.append(
            d.select(
                pl.col("gameId").cast(pl.Utf8).alias("game_id"),
                pl.col(first).cast(pl.Utf8).str.strip_chars().alias("national_tv"),
            ).filter(pl.col("national_tv").is_not_null() & (pl.col("national_tv") != ""))
        )
    if not frames:
        return pl.DataFrame(schema={"game_id": pl.Utf8, "national_tv": pl.Utf8})
    return pl.concat(frames).unique(subset="game_id", keep="first", maintain_order=True)


def box_player_ids(
    con: duckdb.DuckDBPyConnection, first: int = FIRST_SEASON, last: int = LAST_SEASON
) -> list[int]:
    rows = con.execute(
        "SELECT DISTINCT s.player_id FROM player_game_stats s JOIN games g USING (game_id) "
        "WHERE g.season BETWEEN ? AND ? ORDER BY 1",
        [first, last],
    ).fetchall()
    return [int(r[0]) for r in rows]


def _cache_path(pid: int, data_dir: Path) -> Path:
    return data_dir / "players_static" / f"{pid}.parquet"


def _cached(pid: int, data_dir: Path) -> bool:
    p = _cache_path(pid, data_dir)
    if not p.exists() or not pl.read_parquet_schema(p):
        return False
    return int(pl.scan_parquet(p).select(pl.len()).collect().item()) > 0


def uncached_ids(ids: list[int], data_dir: Path = DEFAULT_DATA_DIR) -> list[int]:
    """Ids with no usable ``CommonPlayerInfo`` cache file (these need a network fetch)."""
    return [p for p in ids if not _cached(p, data_dir)]


def cached_static_rows(ids: list[int], data_dir: Path = DEFAULT_DATA_DIR) -> pl.DataFrame:
    """One parsed row per cached id (older caches lack first_season/draft_status: left NULL)."""
    frames = [pl.read_parquet(_cache_path(p, data_dir)) for p in ids if _cached(p, data_dir)]
    if not frames:
        return pl.DataFrame()
    df = pl.concat(frames, how="diagonal_relaxed").unique(subset="player_id", keep="first")
    return df.with_columns([pl.lit(None).alias(c) for c in _SCHEMA if c not in df.columns])


def coverage(con: duckdb.DuckDBPyConnection) -> dict[str, dict[str, float]]:
    """Per history season: share of box rows with national_tv / a birth date (rows incl. DNP)."""
    out: dict[str, dict[str, float]] = {}
    q = (
        "SELECT g.season, count(*), "
        "avg(CASE WHEN g.national_tv IS NOT NULL AND g.national_tv <> '' THEN 1.0 ELSE 0.0 END), "
        "avg(CASE WHEN p.birth_date IS NOT NULL THEN 1.0 ELSE 0.0 END) "
        "FROM player_game_stats s JOIN games g USING (game_id) "
        "LEFT JOIN players_static p ON p.player_id = s.player_id "
        "WHERE g.season <= 2024 GROUP BY 1 ORDER BY 1"
    )
    for season, n, tv, bd in con.execute(q).fetchall():
        out[str(season)] = {
            "rows": float(n),
            "national_tv": round(tv, 4),
            "birth_date": round(bd, 4),
        }
    return out


def plan(
    con: duckdb.DuckDBPyConnection,
    data_dir: Path = DEFAULT_DATA_DIR,
    schedule_dir: Path = HISTORY_DIR / "schedule",
    first_season: int = FIRST_HELD,
) -> dict[str, Any]:
    tv = national_tv_from_schedules(schedule_dir)
    games = {r[0] for r in con.execute("SELECT game_id FROM games").fetchall()}
    tv_in = tv.filter(pl.col("game_id").is_in(list(games)))
    box_ids = box_player_ids(con)
    wide = box_player_ids(con, first_season, LAST_SEASON)
    static = cached_static_rows(wide, data_dir)
    return {
        "tv": tv_in,
        "tv_schedule_rows": tv.height,
        "tv_matched_games": tv_in.height,
        "static": static,
        "box_ids_2019_21": len(box_ids),
        "uncached_2019_21": uncached_ids(box_ids, data_dir),
        "box_ids_wide": len(wide),
        "uncached_wide": uncached_ids(wide, data_dir),
    }


@contextlib.contextmanager
def heavy_lock(lock: Path = HEAVY_LOCK, retry_s: float = 120.0) -> Iterator[None]:
    """mkdir lock; retry every ``retry_s``; removed on exit or SIGTERM/SIGINT (own lock only)."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            print(f"heavy.lock held; retry in {retry_s:.0f}s", flush=True)
            time.sleep(retry_s)

    def _release(*_: Any) -> None:
        shutil.rmtree(lock, ignore_errors=True)
        raise SystemExit(143)

    old = {s: signal.signal(s, _release) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        yield
    finally:
        for s, h in old.items():
            signal.signal(s, h)
        shutil.rmtree(lock, ignore_errors=True)


def load_players_static_keep(con: duckdb.DuckDBPyConnection, df: pl.DataFrame) -> int:
    """Upsert like ``load_players_static`` but never blank a held value.

    ``load_players_static`` is delete+insert, so a cache row with an empty position replaced a
    position loaded from another source (2026-10-13: a waiting apply blanked 13 hand-sourced
    positions). Here every column keeps its old value when the new one is NULL/empty, and an id
    listed in ``players_static_position_source`` keeps its sourced position.
    """
    cols = [r[0] for r in con.execute("DESCRIBE players_static").fetchall()]
    con.execute("CREATE OR REPLACE TEMP TABLE ps_old AS SELECT * FROM players_static")
    n = load_players_static(con, df) if df.height else 0
    has_side = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'players_static_position_source'"
    ).fetchone()[0]  # type: ignore[index]
    for c in cols:
        if c == "player_id":
            continue
        empty = f"(p.{c} IS NULL OR trim(CAST(p.{c} AS VARCHAR)) = '')"
        cond = (
            f"{empty} OR p.player_id IN (SELECT player_id FROM players_static_position_source)"
            if (c == "position" and has_side)
            else empty
        )
        con.execute(
            f"UPDATE players_static p SET {c} = o.{c} FROM ps_old o WHERE p.player_id = o.player_id "
            f"AND ({cond}) AND o.{c} IS NOT NULL AND trim(CAST(o.{c} AS VARCHAR)) <> ''"
        )
    con.execute("DROP TABLE ps_old")
    return n


def apply_plan(
    db: Path, p: dict[str, Any], *, backup_dir: Path = BACKUP_DIR, stamp: str | None = None
) -> dict[str, Any]:
    """Back up, then update games.national_tv and upsert players_static in one transaction.

    Gate (rolls back on failure): games and player_game_stats row counts unchanged; every
    national_tv cell written equals the schedule value; no non-NULL cell was overwritten.
    """
    from nba.db.connect import connect_with_retry

    stamp = stamp or datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"nba_history_pre_static_fix_{stamp}.duckdb"
    shutil.copy2(db, backup)
    w = connect_with_retry(db)
    try:
        n_games = w.execute("SELECT count(*) FROM games").fetchone()[0]  # type: ignore[index]
        n_box = w.execute("SELECT count(*) FROM player_game_stats").fetchone()[0]  # type: ignore[index]
        pre_tv = w.execute("SELECT count(*) FROM games WHERE national_tv IS NOT NULL").fetchone()[0]  # type: ignore[index]
        before = coverage(w)
        w.execute("BEGIN")
        w.register("tv_new", p["tv"].to_arrow())
        w.execute(
            "UPDATE games SET national_tv = t.national_tv FROM tv_new t "
            "WHERE games.game_id = t.game_id AND games.national_tv IS NULL"
        )
        mismatch = w.execute(
            "SELECT count(*) FROM games g JOIN tv_new t USING (game_id) "
            "WHERE g.national_tv IS DISTINCT FROM t.national_tv"
        ).fetchone()[0]  # type: ignore[index]
        w.unregister("tv_new")
        loaded = load_players_static_keep(w, p["static"]) if p["static"].height else 0
        n_games2 = w.execute("SELECT count(*) FROM games").fetchone()[0]  # type: ignore[index]
        n_box2 = w.execute("SELECT count(*) FROM player_game_stats").fetchone()[0]  # type: ignore[index]
        post_tv = w.execute("SELECT count(*) FROM games WHERE national_tv IS NOT NULL").fetchone()[
            0
        ]  # type: ignore[index]
        after = coverage(w)
        ok = (
            n_games == n_games2
            and n_box == n_box2
            and mismatch == 0
            and pre_tv <= post_tv <= pre_tv + p["tv"].height
        )
        w.execute("COMMIT" if ok else "ROLLBACK")
        return {
            "backup": str(backup),
            "ok": ok,
            "players_static_loaded": loaded,
            "before": before,
            "after": after if ok else before,
        }
    finally:
        w.close()


def fetch_missing(
    db: Path,
    data_dir: Path = DEFAULT_DATA_DIR,
    min_interval_s: float = 2.5,
    first_season: int = FIRST_HELD,
    dry_run: bool = False,
) -> int:
    """Pull ``CommonPlayerInfo`` for box players of ``first_season..2021`` with no cache.

    Network; resumable (the per-player cache is never refetched); an empty response is not cached.
    ``dry_run`` only prints the id count and the minutes at ``min_interval_s`` per id.
    """
    ro = duckdb.connect(str(db), read_only=True)
    ids = uncached_ids(box_player_ids(ro, first_season, LAST_SEASON), data_dir)
    ro.close()
    print(
        f"uncached players, seasons {first_season}-{LAST_SEASON}: {len(ids)} "
        f"(about {len(ids) * min_interval_s / 60:.0f} min at {min_interval_s} s/id)",
        flush=True,
    )
    if dry_run or not ids:
        return 0
    limiter = RateLimiter(min_interval_s)
    frames: list[pl.DataFrame] = []
    failed: list[int] = []
    for pid in ids:
        try:  # one player at a time: stats.nba.com returns an empty body for some ids
            frames.append(pull_players_static([pid], data_dir=data_dir, rate_limiter=limiter))
        except Exception as e:  # noqa: BLE001 - recorded and reported, never imputed
            print(f"player {pid}: fetch failed ({type(e).__name__}); left NULL", flush=True)
            failed.append(pid)
    df = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
    got = int(df.filter(pl.col("birth_date").is_not_null()).height) if df.height else 0
    print(f"fetched {df.height}, with birth date {got}, failed {failed}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.ingest.history_static_fix")
    ap.add_argument("cmd", nargs="?", choices=["fetch"], default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", type=Path, default=HISTORY_DB)
    ap.add_argument(
        "--first-season",
        type=int,
        default=FIRST_HELD,
        help="first history season (start year) to fetch/load positions for; default 2013",
    )
    ap.add_argument("--dry-run", action="store_true", help="with fetch: count ids, no network")
    args = ap.parse_args(argv)
    if args.cmd == "fetch":
        rc = fetch_missing(args.db, first_season=args.first_season, dry_run=args.dry_run)
        if rc != 0 or not args.apply or args.dry_run:
            return rc
    ro = duckdb.connect(str(args.db), read_only=True)
    p = plan(ro, first_season=args.first_season)
    before = coverage(ro)
    ro.close()
    print(
        "schedule rows with national tv",
        p["tv_schedule_rows"],
        "matched to history games",
        p["tv_matched_games"],
    )
    print(
        "static rows from cache",
        p["static"].height,
        "uncached (seasons from",
        args.first_season,
        ")",
        len(p["uncached_wide"]),
    )
    print("coverage before", before)
    if not args.apply:
        print("dry run only")
        return 0
    with heavy_lock():
        res = apply_plan(args.db, p)
    print(res)
    return 0 if res["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
