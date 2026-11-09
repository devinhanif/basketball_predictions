# ruff: noqa: E501
"""The pull-request store: ``data/ops/pulls.duckdb``, table ``pull_requests``.

A consumer (JARVIS) never fetches; it files a request here. The orchestrator tick (``nba.pulls.tick``)
serves it. Two processes write this file, so every write is a short transaction on a connection that is
opened and closed inside the call, retried while the other writer holds the file. Readers open it
read-only. Times are naive UTC (the project's store convention).

One OPEN request per (source, game): :func:`request_pull` returns the existing one instead of filing a
second. A request that was refused for budget or failed inside ``cooldown_min`` also blocks a new one,
so a consumer ticking every 2 minutes cannot turn one refusal into a storm of rows.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb

from nba.pulls.config import PullsConfig, load_pulls_config

STATUSES = ("open", "done", "refused_budget", "refused_post_tip", "failed", "expired")
TERMINAL = tuple(s for s in STATUSES if s != "open")
_COOLDOWN_STATUSES = ("refused_budget", "failed")

DDL = """
CREATE TABLE IF NOT EXISTS pull_requests (
    request_id VARCHAR,
    source VARCHAR,                -- "pinnacle" in P1
    scope_game_id VARCHAR,
    slate_date DATE,               -- ET date of the slate
    need_by_utc TIMESTAMP,         -- a request not served by then expires
    max_age_min DOUBLE,            -- the oldest the consumer will accept
    requester VARCHAR,             -- e.g. "jarvis"
    reason VARCHAR,
    created_at_utc TIMESTAMP,
    status VARCHAR,                -- open|done|refused_budget|refused_post_tip|failed|expired
    status_at_utc TIMESTAMP,
    detail VARCHAR
);
"""
_COLS = (
    "request_id, source, scope_game_id, slate_date, need_by_utc, max_age_min, requester, reason, "
    "created_at_utc, status, status_at_utc, detail"
)


@dataclass(frozen=True)
class PullRequest:
    request_id: str
    source: str
    scope_game_id: str
    slate_date: date
    need_by_utc: datetime
    max_age_min: float
    requester: str
    reason: str
    created_at_utc: datetime
    status: str
    status_at_utc: datetime
    detail: str


@dataclass(frozen=True)
class RequestResult:
    request: PullRequest
    created: bool  # False: an open (or just-refused) request for this game already existed


def utc_naive(dt: datetime | None = None) -> datetime:
    t = dt or datetime.now(UTC)
    return t.astimezone(UTC).replace(tzinfo=None) if t.tzinfo else t


def _row(r: tuple[object, ...]) -> PullRequest:
    return PullRequest(*r)  # type: ignore[arg-type]


class PullStore:
    def __init__(self, path: Path, *, retries: int = 6, wait_s: float = 0.5) -> None:
        self.path = Path(path)
        self.retries = max(1, retries)
        self.wait_s = wait_s

    @contextmanager
    def _writer(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """A short-lived read-write connection; retried while another process holds the file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        last: Exception | None = None
        con: duckdb.DuckDBPyConnection | None = None
        for _ in range(self.retries):
            try:
                con = duckdb.connect(str(self.path))
                break
            except duckdb.IOException as exc:
                last = exc
                time.sleep(self.wait_s)
        if con is None:
            assert last is not None
            raise last
        try:
            con.execute(DDL)
            yield con
        finally:
            con.close()

    def _reader(self) -> duckdb.DuckDBPyConnection | None:
        if not self.path.exists():
            return None
        last: Exception | None = None
        for _ in range(self.retries):
            try:
                return duckdb.connect(str(self.path), read_only=True)
            except duckdb.IOException as exc:
                last = exc
                time.sleep(self.wait_s)
        assert last is not None
        raise last

    # -------------------------------------------------------------------------------- write

    def request(
        self,
        *,
        source: str,
        scope_game_id: str,
        slate_date: date,
        need_by_utc: datetime,
        max_age_min: float,
        requester: str,
        reason: str,
        now: datetime | None = None,
        cooldown_min: float = 0.0,
    ) -> RequestResult:
        t = utc_naive(now)
        with self._writer() as con:
            con.execute("BEGIN")
            try:
                cur = con.execute(
                    f"SELECT {_COLS} FROM pull_requests WHERE source = ? AND scope_game_id = ? "  # noqa: S608
                    "AND status = 'open' ORDER BY created_at_utc LIMIT 1",
                    [source, scope_game_id],
                ).fetchone()
                if cur is None and cooldown_min > 0:
                    ph = ", ".join("?" for _ in _COOLDOWN_STATUSES)
                    cur = con.execute(
                        f"SELECT {_COLS} FROM pull_requests WHERE source = ? "  # noqa: S608
                        f"AND scope_game_id = ? AND status IN ({ph}) AND status_at_utc >= ? "
                        "ORDER BY status_at_utc DESC LIMIT 1",
                        [source, scope_game_id, *_COOLDOWN_STATUSES,
                         t - timedelta(minutes=cooldown_min)],
                    ).fetchone()  # fmt: skip
                if cur is not None:
                    con.execute("COMMIT")
                    return RequestResult(_row(cur), False)
                rid = f"pr-{t:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
                con.execute(
                    f"INSERT INTO pull_requests ({_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",  # noqa: S608
                    [rid, source, scope_game_id, slate_date, utc_naive(need_by_utc), max_age_min,
                     requester, reason[:300], t, "open", t, ""],
                )  # fmt: skip
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            row = con.execute(
                f"SELECT {_COLS} FROM pull_requests WHERE request_id = ?",  # noqa: S608
                [rid],
            ).fetchone()
        assert row is not None
        return RequestResult(_row(row), True)

    def set_status(self, request_id: str, status: str, now: datetime, detail: str = "") -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown pull request status {status!r}")
        with self._writer() as con:
            con.execute(
                "UPDATE pull_requests SET status = ?, status_at_utc = ?, detail = ? "
                "WHERE request_id = ?",
                [status, utc_naive(now), detail[:300], request_id],
            )

    # -------------------------------------------------------------------------------- read

    def _select(self, where: str, params: list[object]) -> list[PullRequest]:
        con = self._reader()
        if con is None:
            return []
        try:
            rows = con.execute(
                f"SELECT {_COLS} FROM pull_requests {where}",  # noqa: S608
                params,
            ).fetchall()
        except duckdb.CatalogException:  # file exists, table not yet created
            return []
        finally:
            con.close()
        return [_row(r) for r in rows]

    def open_requests(self, source: str | None = None) -> list[PullRequest]:
        where = "WHERE status = 'open'" + (" AND source = ?" if source else "")
        return self._select(
            where + " ORDER BY need_by_utc, created_at_utc", [source] if source else []
        )

    def list_requests(
        self, *, since: datetime | None = None, status: str | None = None, limit: int = 200
    ) -> list[PullRequest]:
        clauses: list[str] = []
        params: list[object] = []
        if since is not None:
            clauses.append("created_at_utc >= ?")
            params.append(utc_naive(since))
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return self._select(f"{where} ORDER BY created_at_utc DESC LIMIT {int(limit)}", params)


def request_pull(
    *,
    source: str,
    scope_game_id: str,
    slate_date: date,
    requester: str,
    reason: str,
    max_age_min: float | None = None,
    need_by_utc: datetime | None = None,
    now: datetime | None = None,
    cfg: PullsConfig | None = None,
    store: PullStore | None = None,
) -> RequestResult:
    """File one pull request (the consumer API). Deduped: one open request per (source, game), and a
    refused/failed one inside ``cooldown_min`` is returned instead of a new row. Never fetches."""
    c = cfg or load_pulls_config()
    t = utc_naive(now)
    st = store or PullStore(c.requests_db)
    return st.request(
        source=source,
        scope_game_id=scope_game_id,
        slate_date=slate_date,
        need_by_utc=need_by_utc or t + timedelta(minutes=c.request_need_by_min),
        max_age_min=c.pinnacle_alert_max_age_min if max_age_min is None else max_age_min,
        requester=requester,
        reason=reason,
        now=t,
        cooldown_min=c.cooldown_min,
    )
