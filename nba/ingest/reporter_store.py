"""The reporter fact store: its own write-once DuckDB, apart from ``data/facts/facts.duckdb``.

Why a separate file (capture fixes 1 and 2, DECISIONS 2026-10-27 item e)
------------------------------------------------------------------------
* ``nba.facts build`` drops and recreates ``facts.duckdb`` every night. A reporter fact is not
  derivable from any table: it exists only because we read a post once. So it lives here, and the
  facts build reads this file in (``nba.facts.build.reporter_facts_frame``) and never writes it.
* ``known_at`` is OUR fetch time (``first_seen_at``), never the post's ``created_at``: a post we had
  not read was not known to us. ``created_at`` stays as its own column for diagnostics.

Tables
------
``reporter_facts``   the facts schema (docs/FACTS_STORE.md) plus ``post_id``, ``created_at``,
                     ``first_seen_at``, ``capture_mode``, ``parser_version``. Primary key
                     (subject, predicate, object, source, post_id); rows are inserted once and
                     never updated or deleted by this module (a test greps its source).
``poll_log``         one row per scheduled poll of a handle: ok | short:<why> | error:<kind> |
                     refused:<why>. Coverage is computed from it, never assumed.
``team_game_coverage`` one final row per team-game once its [T-90, T-60] window has closed.
``claim_rejects``    claims dropped by a filter (reason counts, and clock faults), insert-once.

The poller is the only writer; DuckDB is single-writer, so every function here opens, writes and
closes within one call of the caller's connection scope.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb
import polars as pl

DEFAULT_STORE = Path(__file__).resolve().parents[2] / "data" / "facts" / "reporter_facts.duckdb"
PREDICATES_PRETIP = ("reporter_minutes_cap", "reporter_status")
LIVE, BACKFILL = "live", "backfill"

SCHEMA = """
CREATE TABLE IF NOT EXISTS reporter_facts (
    fact_id        BIGINT,
    subject        VARCHAR NOT NULL,
    predicate      VARCHAR NOT NULL,
    object         VARCHAR NOT NULL,
    value          DOUBLE,
    value_text     VARCHAR,
    known_at       TIMESTAMP NOT NULL,   -- = first_seen_at: when WE first read the post (naive UTC)
    valid_from     TIMESTAMP,
    valid_to       TIMESTAMP,
    source         VARCHAR NOT NULL,
    confidence     DOUBLE,
    post_id        VARCHAR NOT NULL,
    created_at     TIMESTAMP,            -- the post's own time (X clock); never known-at
    first_seen_at  TIMESTAMP NOT NULL,
    capture_mode   VARCHAR NOT NULL,
    parser_version VARCHAR,
    PRIMARY KEY (subject, predicate, object, source, post_id)
);
CREATE TABLE IF NOT EXISTS poll_log (
    poll_id    BIGINT PRIMARY KEY,
    label      VARCHAR,       -- sweep | T-90 | T-70 | T-40 | manual
    handle     VARCHAR NOT NULL,
    team_id    BIGINT,
    started_at TIMESTAMP NOT NULL,   -- naive UTC; the clock first_seen_at uses
    ended_at   TIMESTAMP,
    status     VARCHAR NOT NULL,
    n_posts    INTEGER,
    pages      INTEGER,
    start_time TIMESTAMP,            -- the lower bound sent to X
    detail     VARCHAR
);
CREATE TABLE IF NOT EXISTS team_game_coverage (
    game_id      VARCHAR NOT NULL,
    team_id      BIGINT NOT NULL,
    tip          TIMESTAMP NOT NULL,
    window_lo    TIMESTAMP NOT NULL,
    window_hi    TIMESTAMP NOT NULL,
    required     VARCHAR,
    ok_handles   VARCHAR,
    covered      BOOLEAN NOT NULL,
    finalized_at TIMESTAMP NOT NULL,
    PRIMARY KEY (game_id, team_id)
);
CREATE TABLE IF NOT EXISTS claim_rejects (
    post_id     VARCHAR NOT NULL,
    handle      VARCHAR,
    reason      VARCHAR NOT NULL,
    detail      VARCHAR,
    detected_at TIMESTAMP NOT NULL,
    PRIMARY KEY (post_id, reason, detail)
);
"""

_FACT_COLS = (
    "fact_id, subject, predicate, object, value, value_text, known_at, valid_from, valid_to, "
    "source, confidence, post_id, created_at, first_seen_at, capture_mode, parser_version"
)


class StoreError(Exception):
    """The store is locked or unreadable."""


@dataclass(frozen=True)
class StoreFact:
    subject: str
    predicate: str
    object: str
    value: float | None
    value_text: str | None
    known_at: datetime  # = first_seen_at
    source: str
    confidence: float
    post_id: str
    created_at: datetime
    first_seen_at: datetime
    capture_mode: str
    parser_version: str


def open_store(
    path: Path | str = DEFAULT_STORE, *, read_only: bool = False
) -> duckdb.DuckDBPyConnection:
    path = Path(path)
    if read_only:
        if not path.exists():
            raise StoreError(f"{path} does not exist")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    try:
        con = duckdb.connect(str(path), read_only=read_only)
    except duckdb.IOException as exc:
        raise StoreError(f"LOCKED or unreadable: {exc}") from exc
    if not read_only:
        con.execute(SCHEMA)
    return con


def insert_facts(con: duckdb.DuckDBPyConnection, facts: Iterable[StoreFact]) -> tuple[int, int]:
    """Insert once; an existing key is left as it was. Returns (written, duplicates)."""
    written = dup = 0
    row = con.execute("SELECT coalesce(max(fact_id), 0) FROM reporter_facts").fetchone()
    fact_id = int(row[0]) if row else 0
    for f in facts:
        fact_id += 1
        before = con.execute("SELECT count(*) FROM reporter_facts").fetchone()
        con.execute(
            f"INSERT INTO reporter_facts ({_FACT_COLS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT DO NOTHING",
            [
                fact_id,
                f.subject,
                f.predicate,
                f.object,
                f.value,
                f.value_text,
                f.known_at,
                f.source,
                f.confidence,
                f.post_id,
                f.created_at,
                f.first_seen_at,
                f.capture_mode,
                f.parser_version,
            ],
        )
        after = con.execute("SELECT count(*) FROM reporter_facts").fetchone()
        if before and after and after[0] > before[0]:
            written += 1
        else:
            dup += 1
    return written, dup


def insert_rejects(
    con: duckdb.DuckDBPyConnection,
    rejects: Iterable[tuple[str, str | None, str, str]],
    detected_at: datetime,
) -> None:
    for post_id, handle, reason, detail in rejects:
        con.execute(
            "INSERT INTO claim_rejects VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            [post_id, handle, reason, detail, detected_at],
        )


# ----------------------------------------------------------------------------------------------
# poll_log and coverage
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PollRecord:
    label: str
    handle: str
    team_id: int | None
    started_at: datetime
    ended_at: datetime | None
    status: str
    n_posts: int = 0
    pages: int = 0
    start_time: datetime | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def log_poll(con: duckdb.DuckDBPyConnection, rec: PollRecord) -> int:
    row = con.execute("SELECT coalesce(max(poll_id), 0) + 1 FROM poll_log").fetchone()
    pid = int(row[0]) if row else 1
    con.execute(
        "INSERT INTO poll_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            pid,
            rec.label,
            rec.handle,
            rec.team_id,
            rec.started_at,
            rec.ended_at,
            rec.status,
            rec.n_posts,
            rec.pages,
            rec.start_time,
            rec.detail,
        ],
    )
    return pid


def read_polls(con: duckdb.DuckDBPyConnection, since: datetime | None = None) -> list[PollRecord]:
    sql = (
        "SELECT label, handle, team_id, started_at, ended_at, status, n_posts, pages, start_time, "
        "detail FROM poll_log"
    )
    params: list[object] = []
    if since is not None:
        sql += " WHERE started_at >= ?"
        params.append(since)
    rows = con.execute(sql + " ORDER BY poll_id", params).fetchall()
    return [PollRecord(*r) for r in rows]


@dataclass(frozen=True)
class Coverage:
    game_id: str
    team_id: int
    tip: datetime
    window_lo: datetime
    window_hi: datetime
    required: tuple[str, ...]
    ok_handles: tuple[str, ...]

    @property
    def covered(self) -> bool:
        return bool(self.required) and set(self.required) <= set(self.ok_handles)


def compute_coverage(
    game_id: str,
    team_id: int,
    tip: datetime,
    required: Sequence[str],
    polls: Iterable[PollRecord],
    *,
    lo_min: float = 90.0,
    hi_min: float = 60.0,
) -> Coverage:
    """A team-game is covered iff EVERY required handle has an ok poll whose start lies inside
    [tip - 90 min, tip - 60 min] (inclusive). A short page, an error or a refusal is not ok.
    ``required`` is the team's own handles, or the league-wide handles when it has none."""
    from datetime import timedelta

    lo, hi = tip - timedelta(minutes=lo_min), tip - timedelta(minutes=hi_min)
    got = sorted({p.handle for p in polls if p.ok and lo <= p.started_at <= hi})
    need = tuple(sorted(required))
    return Coverage(game_id, team_id, tip, lo, hi, need, tuple(h for h in got if h in set(need)))


def write_coverage(con: duckdb.DuckDBPyConnection, cov: Coverage, finalized_at: datetime) -> bool:
    """Insert the final row once; an existing row is never changed. True if inserted."""
    before = con.execute("SELECT count(*) FROM team_game_coverage").fetchone()
    con.execute(
        "INSERT INTO team_game_coverage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
        [
            cov.game_id,
            cov.team_id,
            cov.tip,
            cov.window_lo,
            cov.window_hi,
            ",".join(cov.required),
            ",".join(cov.ok_handles),
            cov.covered,
            finalized_at,
        ],
    )
    after = con.execute("SELECT count(*) FROM team_game_coverage").fetchone()
    return bool(before and after and after[0] > before[0])


# ----------------------------------------------------------------------------------------------
# first_seen_at: from the immutable raw store
# ----------------------------------------------------------------------------------------------


def capture_mode_of(path: Path) -> str:
    """``backfill`` when the file's root directory (``<root>/<UTC date>/<file>``) says so, as
    ``raw_backfill`` does: a search, archive or backfill pull is never a live capture."""
    return BACKFILL if "backfill" in path.parent.parent.name.lower() else LIVE


def first_seen_index(
    raw_dirs: Sequence[Path], since_day: str | None = None
) -> dict[str, tuple[datetime, str]]:
    """``post_id -> (earliest fetched_at over every raw file holding it, its capture mode)``.

    Computed from the immutable raw parquet at fact-write time and again on replay, so a replay can
    never give an earlier stamp than the original capture. ``since_day`` (``YYYY-MM-DD``) skips
    older ``<raw>/<UTC date>/`` directories."""
    best: dict[str, tuple[datetime, str]] = {}
    for root in raw_dirs:
        if not root.exists():
            continue
        for day_dir in sorted(d for d in root.iterdir() if d.is_dir()):
            if since_day is not None and day_dir.name < since_day:
                continue
            for f in sorted(day_dir.glob("*.parquet")):
                df = pl.read_parquet(f, columns=["post_id", "fetched_at"])
                mode = capture_mode_of(f)
                for pid, fetched in df.iter_rows():
                    cur = best.get(str(pid))
                    if cur is None or fetched < cur[0]:
                        best[str(pid)] = (fetched, mode)
    return best


def value_text(**fields: object) -> str:
    return json.dumps(fields, sort_keys=True, default=str, separators=(",", ":"))


def read_facts(
    con: duckdb.DuckDBPyConnection, predicate_prefix: str | None = None
) -> list[Mapping[str, object]]:
    sql = f"SELECT {_FACT_COLS} FROM reporter_facts"
    params: list[object] = []
    if predicate_prefix:
        sql += " WHERE predicate LIKE ?"
        params.append(predicate_prefix + "%")
    cur = con.execute(sql + " ORDER BY fact_id", params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
