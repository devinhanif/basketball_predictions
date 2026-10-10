"""Pull cold-start inputs into ``players_static`` (position/height/weight/draft/age).

CLAUDE.md cold-start modeling: rookie priors need ``draft_pick`` (the single
strongest input), archetype priors need ``position``/``height_in``/
``weight_lb``, and age curves need ``birth_date``. The feature builders that
consume this (``nba.features.player_possession_features`` position prior,
``nba.coldstart``) degrade gracefully to league buckets when it's empty, so
filling it is a strict improvement, never a correctness dependency.

nba_api's ``CommonPlayerInfo`` is one call per player (~0.6s rate-limited x
~900 players ~= 10-15 min). Each player's raw frame is cached to
``data/players_static/<player_id>.parquet`` and the pull is resumable by
file existence alone -- deliberately NOT routed through the ``ingest_log``/
``fetch_cached`` bookkeeping, so the long network pull holds no write lock on
``nba.duckdb`` and can run alongside concurrent read-only evals. The parsed
rows load into ``players_static`` in a single short upsert at the end.

nba_api is imported lazily so this module is safe to import without network.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import duckdb
import polars as pl

from nba.ingest.cache import DEFAULT_DATA_DIR, RateLimiter

SOURCE = "players-static"

#: Columns of the ``players_static`` table this puller populates. ``college_stats``
#: (per-100 college numbers) has no free nba_api source, left NULL for now.
_SCHEMA = [
    "player_id",
    "position",
    "height_in",
    "weight_lb",
    "birth_date",
    "draft_year",
    "draft_pick",
    "college",
    "first_season",
    "draft_status",
]

_MAX_FETCH_ATTEMPTS = 3
_RETRY_BACKOFF_S = 1.0


def parse_height_to_inches(height: str | None) -> float | None:
    """'6-7' -> 79.0 inches. None/''/malformed -> None."""
    if not height or "-" not in height:
        return None
    feet_s, _, inches_s = height.partition("-")
    try:
        return float(int(feet_s) * 12 + int(inches_s))
    except ValueError:
        return None


def _parse_int(value: object) -> int | None:
    """nba_api draft fields are strings like '11' or 'Undrafted'/''. -> int|None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or not s.lstrip("-").isdigit():
        return None
    return int(s)


def _parse_float(value: object) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse_draft_status(draft_year: object, draft_number: object) -> str:
    """'drafted' | 'undrafted' | 'unknown' from the raw CommonPlayerInfo text.

    'undrafted' needs positive evidence (the literal text 'Undrafted' in DRAFT_NUMBER or
    DRAFT_YEAR); a numeric pick or draft year is 'drafted'; blanks stay 'unknown'.
    """
    raw = [str(v).strip() for v in (draft_year, draft_number) if v is not None]
    if any(r.lower() == "undrafted" for r in raw):
        return "undrafted"
    if any(r.isdigit() for r in raw):
        return "drafted"
    return "unknown"


def _normalize_player_info(frame: pl.DataFrame, player_id: int) -> pl.DataFrame:
    """Map a raw ``CommonPlayerInfo`` frame to ``_SCHEMA`` (one row)."""
    empty = pl.DataFrame(
        {
            "player_id": [player_id],
            "position": [None],
            "height_in": [None],
            "weight_lb": [None],
            "birth_date": [None],
            "draft_year": [None],
            "draft_pick": [None],
            "college": [None],
            "first_season": [None],
            "draft_status": ["unknown"],
        },
        schema={
            "player_id": pl.Int64,
            "position": pl.Utf8,
            "height_in": pl.Float64,
            "weight_lb": pl.Float64,
            "birth_date": pl.Date,
            "draft_year": pl.Int64,
            "draft_pick": pl.Int64,
            "college": pl.Utf8,
            "first_season": pl.Int64,
            "draft_status": pl.Utf8,
        },
    )
    if frame.is_empty():
        return empty
    row = frame.to_dicts()[0]

    def _get(key: str) -> object:
        # CommonPlayerInfo column names are upper-case; be tolerant of case.
        return row.get(key, row.get(key.lower()))

    position = _get("POSITION")
    position = str(position).strip() if position not in (None, "") else None
    birthdate_raw = _get("BIRTHDATE")
    birth_date = None
    if birthdate_raw:
        # e.g. '1998-02-28T00:00:00'
        birth_date = str(birthdate_raw).split("T", 1)[0] or None
    college_raw = _get("SCHOOL")
    college = str(college_raw).strip() if college_raw not in (None, "") else None

    return pl.DataFrame(
        {
            "player_id": [player_id],
            "position": [position],
            "height_in": [parse_height_to_inches(_get("HEIGHT"))],  # type: ignore[arg-type]
            "weight_lb": [_parse_float(_get("WEIGHT"))],
            "birth_date": [birth_date],
            "draft_year": [_parse_int(_get("DRAFT_YEAR"))],
            "draft_pick": [_parse_int(_get("DRAFT_NUMBER"))],
            "college": [college],
            "first_season": [_parse_int(_get("FROM_YEAR"))],
            "draft_status": [parse_draft_status(_get("DRAFT_YEAR"), _get("DRAFT_NUMBER"))],
        },
        schema=empty.schema,
    )


def _fetch_player_info(player_id: int) -> pl.DataFrame:
    """Hit nba_api's CommonPlayerInfo for one player; normalize to ``_SCHEMA``."""
    from nba_api.stats.endpoints import commonplayerinfo  # lazy import

    info = commonplayerinfo.CommonPlayerInfo(player_id=player_id)
    raw = pl.from_pandas(info.get_data_frames()[0])
    return _normalize_player_info(raw, player_id)


def _fetch_with_retry(player_id: int) -> pl.DataFrame:
    last: pl.DataFrame | None = None
    for attempt in range(_MAX_FETCH_ATTEMPTS):
        try:
            last = _fetch_player_info(player_id)
            if not last.is_empty():
                return last
        except Exception:
            if attempt == _MAX_FETCH_ATTEMPTS - 1:
                raise
        if attempt < _MAX_FETCH_ATTEMPTS - 1:
            time.sleep(_RETRY_BACKOFF_S)
    assert last is not None
    return last


def player_ids_needing_pull(con: duckdb.DuckDBPyConnection) -> list[int]:
    """Distinct player_ids seen in box scores but not yet in ``players_static``."""
    rows = con.execute(
        "SELECT DISTINCT pgs.player_id FROM player_game_stats pgs "
        "LEFT JOIN players_static ps ON ps.player_id = pgs.player_id "
        "WHERE ps.player_id IS NULL ORDER BY 1"
    ).fetchall()
    return [int(r[0]) for r in rows]


def pull_players_static(
    player_ids: list[int],
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
    progress_every: int = 100,
) -> pl.DataFrame:
    """Fetch (or load cached) ``CommonPlayerInfo`` for each id; return parsed rows.

    Resumable by file existence: a player whose
    ``data/players_static/<id>.parquet`` already exists is loaded from disk,
    never refetched. Does NOT touch ``nba.duckdb`` -- caller loads the
    returned frame via :func:`load_players_static` in one short write.
    """
    out_dir = data_dir / "players_static"
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[pl.DataFrame] = []
    for i, pid in enumerate(player_ids, 1):
        path = out_dir / f"{pid}.parquet"
        if path.exists():
            frames.append(pl.read_parquet(path))
        else:
            if rate_limiter is not None:
                rate_limiter.wait()
            frame = _fetch_with_retry(pid)
            frame.write_parquet(path)
            frames.append(frame)
        if progress_every and i % progress_every == 0:
            print(f"...players_static {i}/{len(player_ids)}", flush=True)
    if not frames:
        return pl.DataFrame(schema={c: pl.Utf8 for c in _SCHEMA})
    return pl.concat(frames, how="vertical_relaxed")


def load_players_static(con: duckdb.DuckDBPyConnection, df: pl.DataFrame) -> int:
    """Upsert parsed rows into ``players_static`` (keyed on player_id). Returns count."""
    if df.is_empty():
        return 0
    # Frames cached before the F9b columns existed lack them: both stay NULL, meaning
    # "not pulled with these fields yet"; 'unknown' is reserved for a pull that was blank.
    if "first_season" not in df.columns:
        df = df.with_columns(pl.lit(None, dtype=pl.Int64).alias("first_season"))
    if "draft_status" not in df.columns:
        df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias("draft_status"))
    con.register("ps_new", df.select(_SCHEMA))
    con.execute("DELETE FROM players_static WHERE player_id IN (SELECT player_id FROM ps_new)")
    cols = ", ".join(_SCHEMA)
    con.execute(f"INSERT INTO players_static ({cols}) SELECT {cols} FROM ps_new")
    con.unregister("ps_new")
    return df.height


#: Max players pulled per daily run (stats.nba.com quota is ~600 requests/hour/IP).
AUTOFILL_CAP = 120


def autofill_players_static(
    con: duckdb.DuckDBPyConnection,
    player_ids: list[int],
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
    cap: int = AUTOFILL_CAP,
    fetch: Callable[[int], pl.DataFrame] | None = None,
) -> dict[str, int]:
    """Pull ``CommonPlayerInfo`` for rostered players lacking a row and upsert them.

    Per-player parquet cache (``data/players_static/<id>.parquet``) means each player is
    fetched at most once ever. At most ``cap`` ids per call. Never raises: a failed fetch
    is logged and counted, and the player keeps the league-default prior. The upsert is one
    short transaction after all network calls. Returns counts for the run summary.
    """
    ids = sorted({int(p) for p in player_ids})
    todo, skipped = ids[:cap], max(len(ids) - cap, 0)
    out_dir = data_dir / "players_static"
    frames: list[pl.DataFrame] = []
    pulled = failed = 0
    for pid in todo:
        path = out_dir / f"{pid}.parquet"
        try:
            if path.exists():
                frames.append(pl.read_parquet(path))
                continue
            if rate_limiter is not None:
                rate_limiter.wait()
            frame = (fetch or _fetch_with_retry)(pid)
            out_dir.mkdir(parents=True, exist_ok=True)
            frame.write_parquet(path)
            frames.append(frame)
            pulled += 1
        except Exception as exc:
            failed += 1
            print(f"players_static autofill FAILED [{pid}]: {type(exc).__name__}: {exc}"[:200])
    loaded = 0
    if frames:
        try:
            con.execute("BEGIN")
            try:
                loaded = load_players_static(con, pl.concat(frames, how="vertical_relaxed"))
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
        except Exception as exc:
            failed += len(frames)
            print(f"players_static autofill load FAILED: {type(exc).__name__}: {exc}"[:200])
    return {
        "requested": len(ids),
        "pulled": pulled,
        "loaded": loaded,
        "failed": failed,
        "over_cap": skipped,
    }


def cached_missing_status_fields(ids: list[int], *, data_dir: Path = DEFAULT_DATA_DIR) -> list[int]:
    """Ids whose cached frame is absent or predates ``first_season``/``draft_status``."""
    out_dir = data_dir / "players_static"
    need: list[int] = []
    for pid in sorted({int(p) for p in ids}):
        path = out_dir / f"{pid}.parquet"
        if not path.exists() or not {"first_season", "draft_status"} <= set(
            pl.read_parquet_schema(path)
        ):
            need.append(pid)
    return need


def refresh_status_fields(
    ids: list[int],
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
    fetch: Callable[[int], pl.DataFrame] | None = None,
    progress_every: int = 50,
) -> dict[str, int]:
    """Re-pull ``CommonPlayerInfo`` ONLY for ids whose cache lacks the F9b fields.

    Overwrites that player's parquet with the new-schema frame (resumable: a refreshed
    file is skipped on rerun). Never raises on a single failure; caller upserts after.
    """
    todo = cached_missing_status_fields(ids, data_dir=data_dir)
    out_dir = data_dir / "players_static"
    out_dir.mkdir(parents=True, exist_ok=True)
    pulled = failed = 0
    for i, pid in enumerate(todo, 1):
        try:
            if rate_limiter is not None:
                rate_limiter.wait()
            (fetch or _fetch_with_retry)(pid).write_parquet(out_dir / f"{pid}.parquet")
            pulled += 1
        except Exception as exc:
            failed += 1
            print(f"players_static refresh FAILED [{pid}]: {type(exc).__name__}: {exc}"[:200])
        if progress_every and i % progress_every == 0:
            print(f"...refresh {i}/{len(todo)}", flush=True)
    return {"requested": len(set(ids)), "needed": len(todo), "pulled": pulled, "failed": failed}


def backfill_f9b_prereq(db_path: Path | None = None, *, min_interval_s: float = 2.5) -> None:
    """F9b prerequisite: refresh cache for players lacking the new fields, then upsert.

    Targets active players (>=20 games, season 2023) plus every player with a NULL
    draft_pick or draft_year. Holds ``data/ops/lock`` (mkdir; removed on exit) so the
    background queue yields. Reads the DB read-only for target selection; the upsert is
    one short write after all network calls. Run: ``python -m nba.ingest.players_static``.
    """
    from nba.db.connect import DEFAULT_DB_PATH, connect

    path = db_path or DEFAULT_DB_PATH
    ro = duckdb.connect(str(path), read_only=True)
    ids = [
        int(r[0])
        for r in ro.execute(
            "SELECT player_id FROM players_static WHERE draft_pick IS NULL OR draft_year IS NULL "
            "UNION SELECT pgs.player_id FROM player_game_stats pgs JOIN games g USING(game_id) "
            "WHERE g.season = 2023 GROUP BY 1 HAVING count(*) >= 20 ORDER BY 1"
        ).fetchall()
    ]
    ro.close()
    lock = DEFAULT_DATA_DIR / "ops" / "lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.mkdir()  # raises if held by someone else
    try:
        print(refresh_status_fields(ids, rate_limiter=RateLimiter(min_interval_s)), flush=True)
    finally:
        lock.rmdir()
    frames = [pl.read_parquet(DEFAULT_DATA_DIR / "players_static" / f"{p}.parquet") for p in ids]
    con = connect(path)
    try:
        con.execute("BEGIN")
        n = load_players_static(con, pl.concat(frames, how="vertical_relaxed"))
        con.execute("COMMIT")
    finally:
        con.close()
    print("upserted", n)


if __name__ == "__main__":
    backfill_f9b_prereq()
