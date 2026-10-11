"""Read-only X API v2 adapter for allow-listed NBA beat reporters (docs/REPORTER_FEED.md).

Purpose: catch pre-tip facts the official injury report never carries, above all minutes
restrictions. Decided 2026-10-10 ("confirm everyone", item 3) and 2026-10-11 ~00:55 (item 4).

Guarantees
----------
* Read-only. The only paths this module can call are ``GET /2/users/by/username/:name`` and
  ``GET /2/users/:id/tweets``. No posting, no account mutation, ever.
* The bearer token comes from ``os.environ["X_BEARER_TOKEN"]`` only, is held in a wrapper whose
  every textual form is ``<secret>``, is sent only in the ``Authorization`` header, and is never
  logged, stored or included in an exception (``_scrub`` strips it from any server text).
  Without it the client refuses to construct, before any I/O.
* Allow-list. Handles come from ``configs/reporters.yaml`` (Devin's file, empty by default).
  Any other handle is refused with :class:`HandleNotAllowed`. Nothing in a post can add one.
* Money. Every post read is billed, so a per-run cap (``--budget-posts``) and a per-UTC-day ceiling
  are checked BEFORE each request against the ledger ``data/reporters/budget.json``; the request
  asks for at most the remaining allowance (``max_results``). One page per handle per poll.
* Rate limits. ``x-rate-limit-reset`` is honoured (bounded wait), at most ``MAX_ATTEMPTS`` tries
  per request with exponential backoff; the ledger counts every attempt.
* Raw first, write-once. Fetched posts are written as parquet under
  ``data/reporters/raw/<UTC date>/<handle>_<HHMMSS>Z.parquet`` with the fixed :data:`RAW_SCHEMA`
  before anything parses them; an existing file is never overwritten. Replay with ``parse-only``.

Nothing in the daily path imports this module; it is additive (CLAUDE.md "How we build").
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from nba.ingest.teams import TEAM_ABBREV_TO_ID

log = logging.getLogger("nba.reporters")

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST_PATH = ROOT / "configs" / "reporters.yaml"
DATA_DIR = ROOT / "data" / "reporters"
KEY_ENV = "X_BEARER_TOKEN"
API_BASE = "https://api.x.com/2"
#: The only two paths this client can call (both GET, both read-only).
ENDPOINTS = {
    "user_by_username": "/users/by/username/{username}",
    "user_tweets": "/users/{user_id}/tweets",
}
TWEET_FIELDS = "created_at,referenced_tweets,note_tweet"
MAX_RESULTS_MIN, MAX_RESULTS_MAX = 5, 100  # the API's own bounds for max_results
MAX_ATTEMPTS = 2
BACKOFF_BASE_S = 1.0
MAX_RATE_WAIT_S = 120.0
DEFAULT_TIMEOUT_S = 20.0
_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")

RAW_SCHEMA: dict[str, Any] = {
    "post_id": pl.Utf8,
    "handle": pl.Utf8,
    "created_at": pl.Datetime("us"),  # naive UTC
    "text": pl.Utf8,
    "fetched_at": pl.Datetime("us"),  # naive UTC, our clock
    "url": pl.Utf8,
}


# ----------------------------------------------------------------------------------------------
# Errors (messages never contain the token)
# ----------------------------------------------------------------------------------------------


class ReporterError(Exception):
    """Base class for this adapter."""


class KeyMissingError(ReporterError):
    """``X_BEARER_TOKEN`` is not set."""


class HandleNotAllowed(ReporterError):
    """A handle that is not in ``configs/reporters.yaml``."""


class BudgetExceeded(ReporterError):
    """The per-run cap or the per-day ceiling would be exceeded; nothing was requested."""


class RateLimited(ReporterError):
    """HTTP 429 persisted through the attempt budget."""


class ReporterHTTPError(ReporterError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status


class Unreachable(ReporterError):
    """Transport or 5xx errors through the attempt budget."""


# ----------------------------------------------------------------------------------------------
# Token
# ----------------------------------------------------------------------------------------------


class _Secret:
    """Holds the token; every textual form is ``<secret>``; never pickled or copied."""

    __slots__ = ("_v",)

    def __init__(self, value: str) -> None:
        self._v = value

    def reveal(self) -> str:
        return self._v

    def __repr__(self) -> str:
        return "<secret>"

    __str__ = __repr__

    def __reduce__(self) -> Any:
        raise TypeError("secret is not serialisable")


def read_token(environ: Mapping[str, str] | None = None) -> _Secret:
    env = os.environ if environ is None else environ
    value = (env.get(KEY_ENV) or "").strip()
    if not value:
        raise KeyMissingError(
            f"{KEY_ENV} is not set; put it in .env (chmod 600) and run with "
            "`uv run --env-file .env ...`. The token is read from the environment only."
        )
    return _Secret(value)


# ----------------------------------------------------------------------------------------------
# Allow-list
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reporter:
    handle: str
    team: str | None  # tricode or None (league-wide)
    name: str | None = None

    @property
    def team_id(self) -> int | None:
        return TEAM_ABBREV_TO_ID[self.team] if self.team else None

    @property
    def source(self) -> str:
        return f"x:{self.handle}"


@dataclass(frozen=True)
class Allowlist:
    reporters: dict[str, Reporter]  # keyed on handle.lower()
    posts_per_run: int = 200
    posts_per_day: int = 1000
    lookback_hours: float = 6.0

    def __contains__(self, handle: object) -> bool:
        return isinstance(handle, str) and handle.lower() in self.reporters

    def require(self, handle: str) -> Reporter:
        rep = self.reporters.get((handle or "").lower())
        if rep is None:
            raise HandleNotAllowed(
                f"handle {handle!r} is not in configs/reporters.yaml; refusing. "
                "Only Devin adds handles."
            )
        return rep

    def handles(self) -> list[str]:
        return [r.handle for r in self.reporters.values()]


def load_allowlist(path: Path | str = ALLOWLIST_PATH) -> Allowlist:
    """Parse ``configs/reporters.yaml``. Malformed rows are errors, not warnings."""
    raw = yaml.safe_load(Path(path).read_text()) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    reporters: dict[str, Reporter] = {}
    for row in raw.get("handles") or []:
        if not isinstance(row, dict) or not row.get("handle"):
            raise ValueError(f"{path}: every handles entry needs a 'handle': {row!r}")
        handle = str(row["handle"]).lstrip("@")
        if not _HANDLE_RE.match(handle):
            raise ValueError(f"{path}: {handle!r} is not a valid X handle")
        team = row.get("team")
        team = str(team).upper() if team not in (None, "") else None
        if team is not None and team not in TEAM_ABBREV_TO_ID:
            raise ValueError(f"{path}: unknown team tricode {team!r} for {handle!r}")
        if handle.lower() in reporters:
            raise ValueError(f"{path}: duplicate handle {handle!r}")
        name = row.get("name")
        reporters[handle.lower()] = Reporter(handle, team, str(name) if name else None)
    budget = raw.get("budget") or {}
    return Allowlist(
        reporters,
        posts_per_run=int(budget.get("posts_per_run", 200)),
        posts_per_day=int(budget.get("posts_per_day", 1000)),
        lookback_hours=float(raw.get("lookback_hours", 6)),
    )


# ----------------------------------------------------------------------------------------------
# Posts and the raw store
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Post:
    post_id: str
    handle: str
    created_at: datetime  # naive UTC
    text: str
    fetched_at: datetime  # naive UTC
    url: str


def _naive_utc(t: datetime) -> datetime:
    return t.astimezone(UTC).replace(tzinfo=None) if t.tzinfo is not None else t


def post_url(handle: str, post_id: str) -> str:
    return f"https://x.com/{handle}/status/{post_id}"


def posts_frame(posts: Iterable[Post]) -> pl.DataFrame:
    rows = [
        {
            "post_id": p.post_id,
            "handle": p.handle,
            "created_at": _naive_utc(p.created_at),
            "text": p.text,
            "fetched_at": _naive_utc(p.fetched_at),
            "url": p.url,
        }
        for p in posts
    ]
    return pl.DataFrame(rows, schema=RAW_SCHEMA)


def write_raw(raw_dir: Path, handle: str, posts: list[Post], fetched_at: datetime) -> Path | None:
    """Write-once parquet: ``<raw_dir>/<UTC date>/<handle>_<HHMMSS>Z.parquet``. Returns None for
    an empty page; raises ``FileExistsError`` rather than overwrite."""
    if not posts:
        return None
    fetched_at = _naive_utc(fetched_at)
    day_dir = raw_dir / fetched_at.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    final = day_dir / f"{handle}_{fetched_at.strftime('%H%M%S')}Z.parquet"
    if final.exists():
        raise FileExistsError(f"raw file exists, refusing to overwrite: {final}")
    tmp = final.with_name(final.name + ".tmp")
    posts_frame(posts).write_parquet(tmp)
    os.replace(tmp, final)
    return final


def read_raw(path: Path) -> list[Post]:
    df = pl.read_parquet(path).select(list(RAW_SCHEMA)).cast(pl.Schema(RAW_SCHEMA))
    return [Post(**row) for row in df.iter_rows(named=True)]


# ----------------------------------------------------------------------------------------------
# Budget ledger (posts are what is billed; requests are counted too)
# ----------------------------------------------------------------------------------------------


def utc_day(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%d")


class PostBudget:
    """``data/reporters/budget.json``: ``{"days": {day: {"posts": n, "requests": m}},
    "total_posts": N}``. ``per_run`` is this process's cap; ``per_day`` the ceiling across runs."""

    def __init__(self, path: Path, per_run: int, per_day: int) -> None:
        self.path = path
        self.per_run = int(per_run)
        self.per_day = int(per_day)
        self.run_posts = 0
        self.run_requests = 0

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {"days": {}, "total_posts": 0}
        if not isinstance(data, dict):
            return {"days": {}, "total_posts": 0}
        data.setdefault("days", {})
        data.setdefault("total_posts", 0)
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
        os.replace(tmp, self.path)

    def used_today(self, now: datetime) -> int:
        return int(self._load()["days"].get(utc_day(now), {}).get("posts", 0))

    def remaining(self, now: datetime) -> int:
        return max(0, min(self.per_run - self.run_posts, self.per_day - self.used_today(now)))

    def check(self, now: datetime, need: int = MAX_RESULTS_MIN) -> int:
        """Posts this run may still read; raises :class:`BudgetExceeded` if fewer than ``need``
        (the API will not return fewer than ``MAX_RESULTS_MIN`` per page)."""
        rem = self.remaining(now)
        if rem < need:
            raise BudgetExceeded(
                f"post budget exhausted: run {self.run_posts}/{self.per_run}, "
                f"day {self.used_today(now)}/{self.per_day} ({utc_day(now)} UTC); "
                f"{rem} left, a page needs {need}. Raise --budget-posts to continue."
            )
        return rem

    def charge(self, now: datetime, posts: int, requests: int = 0) -> None:
        data = self._load()
        day = data["days"].setdefault(utc_day(now), {"posts": 0, "requests": 0})
        day["posts"] = int(day.get("posts", 0)) + int(posts)
        day["requests"] = int(day.get("requests", 0)) + int(requests)
        data["total_posts"] = int(data.get("total_posts", 0)) + int(posts)
        self.run_posts += int(posts)
        self.run_requests += int(requests)
        self._save(data)


class HandleState:
    """``data/reporters/state.json``: cached user ids and the newest post time per handle."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, handle: str) -> dict[str, Any]:
        return dict(self._load().get(handle.lower()) or {})

    def update(self, handle: str, **fields: Any) -> None:
        data = self._load()
        row = dict(data.get(handle.lower()) or {})
        row.update({k: v for k, v in fields.items() if v is not None})
        data[handle.lower()] = row
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
        os.replace(tmp, self.path)


# ----------------------------------------------------------------------------------------------
# Client
# ----------------------------------------------------------------------------------------------


@dataclass
class PollResult:
    posts: list[Post] = field(default_factory=list)
    raw_paths: list[Path] = field(default_factory=list)
    per_handle: dict[str, int] = field(default_factory=dict)
    stopped: str | None = None  # budget / rate-limit message when the poll ended early


def _parse_created(s: str) -> datetime:
    return _naive_utc(datetime.fromisoformat(s.replace("Z", "+00:00")))


def parse_tweets_payload(body: Any, handle: str, fetched_at: datetime) -> list[Post]:
    """``/users/:id/tweets`` body -> posts (long posts use ``note_tweet.text``). Unknown
    shapes yield nothing; they are never raised on."""
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        return []
    out = []
    for row in body["data"]:
        if not isinstance(row, dict) or "id" not in row or "created_at" not in row:
            continue
        note = row.get("note_tweet")
        text = note.get("text") if isinstance(note, dict) and note.get("text") else row.get("text")
        try:
            created = _parse_created(str(row["created_at"]))
        except ValueError:
            continue
        pid = str(row["id"])
        out.append(Post(pid, handle, created, str(text or ""), fetched_at, post_url(handle, pid)))
    return out


class XClient:
    """GET-only client for the two endpoints above. ``repr`` shows no secret."""

    def __init__(
        self,
        allowlist: Allowlist,
        *,
        environ: Mapping[str, str] | None = None,
        data_dir: Path = DATA_DIR,
        budget_posts: int | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        transport: Any = None,
        base_url: str = API_BASE,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._token = read_token(environ)  # refuses here, before any I/O
        self.allowlist = allowlist
        self.data_dir = Path(data_dir)
        self.raw_dir = self.data_dir / "raw"
        self.budget = PostBudget(
            self.data_dir / "budget.json",
            allowlist.posts_per_run if budget_posts is None else budget_posts,
            allowlist.posts_per_day,
        )
        self.state = HandleState(self.data_dir / "state.json")
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self._transport = transport
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.requests_made = 0

    def __repr__(self) -> str:
        return (
            f"XClient(handles={len(self.allowlist.reporters)}, "
            f"budget_posts={self.budget.per_run}, token=<secret>)"
        )

    def now(self) -> datetime:
        t = self._clock()
        return t if t.tzinfo is not None else t.replace(tzinfo=UTC)

    def _scrub(self, text: str) -> str:
        return text.replace(self._token.reveal(), "<secret>")

    # -- HTTP ----------------------------------------------------------------------------------
    def _get(self, path: str, params: Mapping[str, Any]) -> Any:
        import httpx  # lazy: parsing and replay stay importable without the network layer

        url = self.base_url + path
        last = "no attempt made"
        with httpx.Client(transport=self._transport, timeout=self.timeout_s) as http:
            for attempt in range(MAX_ATTEMPTS):
                self.budget.charge(self.now(), 0, requests=1)
                self.requests_made += 1
                try:
                    resp = http.get(
                        url,
                        params=dict(params),
                        headers={"Authorization": f"Bearer {self._token.reveal()}"},
                    )
                except httpx.TransportError as exc:
                    last = f"transport error {type(exc).__name__}"
                    log.warning("reporters %s attempt %d: %s", path, attempt + 1, last)
                    self._backoff(attempt)
                    continue
                if resp.status_code == 429:
                    last = "HTTP 429"
                    log.warning("reporters %s attempt %d: rate limited", path, attempt + 1)
                    if attempt + 1 < MAX_ATTEMPTS:
                        self._wait_for_reset(resp.headers.get("x-rate-limit-reset"), attempt)
                    continue
                if resp.status_code >= 500:
                    last = f"HTTP {resp.status_code}"
                    log.warning("reporters %s attempt %d: %s", path, attempt + 1, last)
                    self._backoff(attempt)
                    continue
                if resp.status_code != 200:
                    raise ReporterHTTPError(resp.status_code, self._scrub(resp.text[:200]))
                try:
                    return resp.json()
                except ValueError as exc:
                    raise ReporterHTTPError(200, "response is not JSON") from exc
        if last == "HTTP 429":
            raise RateLimited(f"{path}: rate limited after {MAX_ATTEMPTS} attempts")
        raise Unreachable(f"{path}: {last} after {MAX_ATTEMPTS} attempts")

    def _backoff(self, attempt: int) -> None:
        if attempt + 1 < MAX_ATTEMPTS:
            self._sleep(BACKOFF_BASE_S * (2**attempt))

    def _wait_for_reset(self, reset_header: str | None, attempt: int) -> None:
        """Sleep until ``x-rate-limit-reset`` (epoch seconds), bounded; else exponential."""
        wait = BACKOFF_BASE_S * (2**attempt)
        if reset_header:
            try:
                wait = float(reset_header) - self.now().timestamp()
            except ValueError:
                log.warning("reporters: unparseable x-rate-limit-reset header")
        wait = max(BACKOFF_BASE_S, wait)
        if wait > MAX_RATE_WAIT_S:
            raise RateLimited(f"rate limit resets in {wait:.0f}s (> {MAX_RATE_WAIT_S:.0f}s cap)")
        self._sleep(wait)

    # -- endpoints -----------------------------------------------------------------------------
    def user_id(self, handle: str) -> str:
        rep = self.allowlist.require(handle)
        cached = self.state.get(rep.handle).get("user_id")
        if cached:
            return str(cached)
        body = self._get(ENDPOINTS["user_by_username"].format(username=rep.handle), {})
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict) or not data.get("id"):
            raise ReporterHTTPError(200, f"no user id in response for {rep.handle!r}")
        uid = str(data["id"])
        self.state.update(rep.handle, user_id=uid)
        return uid

    def fetch_posts(self, handle: str, start_time: datetime, max_results: int) -> list[Post]:
        """One page of ``handle``'s own posts since ``start_time`` (retweets and replies
        excluded on the wire; the parser defends again). Charges the ledger per post."""
        rep = self.allowlist.require(handle)
        max_results = max(MAX_RESULTS_MIN, min(MAX_RESULTS_MAX, int(max_results)))
        uid = self.user_id(rep.handle)
        params = {
            "max_results": max_results,
            "start_time": start_time.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tweet.fields": TWEET_FIELDS,
            "exclude": "retweets,replies",
        }
        fetched = self.now()
        body = self._get(ENDPOINTS["user_tweets"].format(user_id=uid), params)
        posts = parse_tweets_payload(body, rep.handle, _naive_utc(fetched))
        self.budget.charge(fetched, len(posts))
        return posts

    def poll(
        self, handles: Iterable[str] | None = None, *, lookback_hours: float | None = None
    ) -> PollResult:
        """One page per handle. Stops (not fails) when the budget runs out mid-poll."""
        res = PollResult()
        lookback = timedelta(hours=lookback_hours or self.allowlist.lookback_hours)
        names = list(handles) if handles is not None else self.allowlist.handles()
        for h in names:
            rep = self.allowlist.require(h)  # refuses before any request
            now = self.now()
            try:
                remaining = self.budget.check(now)
            except BudgetExceeded as exc:
                res.stopped = str(exc)
                log.warning("reporters: %s", exc)
                break
            last = self.state.get(rep.handle).get("last_created_at")
            start = now - lookback
            if last:
                start = max(start, datetime.fromisoformat(last).replace(tzinfo=UTC))
            posts = self.fetch_posts(rep.handle, start, remaining)
            res.per_handle[rep.handle] = len(posts)
            if posts:
                path = write_raw(self.raw_dir, rep.handle, posts, now)
                if path is not None:
                    res.raw_paths.append(path)
                newest = max(p.created_at for p in posts)
                self.state.update(rep.handle, last_created_at=newest.isoformat())
                res.posts.extend(posts)
            log.info("reporters: %s: %d posts", rep.handle, len(posts))
        return res


# ----------------------------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------------------------


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--allowlist", default=str(ALLOWLIST_PATH))
    p.add_argument("--facts-db", default=None, help="write claimed facts here (omit = print)")
    p.add_argument("--rosters", default=str(ROOT / "data" / "rosters"))
    p.add_argument("--schedule-cache", default=str(ROOT / "data" / "schedule_cache"))
    p.add_argument("--aliases", default=str(ROOT / "configs" / "kalshi_aliases.yaml"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="nba.ingest.reporters", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    poll = sub.add_parser("poll", help="one page per allow-listed handle (billed), then parse")
    _common(poll)
    poll.add_argument("--budget-posts", type=int, default=None, help="cap for this run")
    poll.add_argument("--lookback-hours", type=float, default=None)
    poll.add_argument("--handle", action="append", default=None, help="subset of the list")
    poll.add_argument("--data-dir", default=str(DATA_DIR))
    rep = sub.add_parser("parse-only", help="replay raw parquet files (no network, no cost)")
    _common(rep)
    rep.add_argument("--file", action="append", required=True)
    args = p.parse_args(argv)

    from nba.ingest import reporter_facts as rf  # lazy: duckdb + schedule only when used

    try:
        allow = load_allowlist(args.allowlist)
        if args.cmd == "poll":
            if not allow.reporters:
                print("configs/reporters.yaml lists no handles; nothing to poll.", file=sys.stderr)
                return 2
            client = XClient(allow, data_dir=Path(args.data_dir), budget_posts=args.budget_posts)
            result = client.poll(args.handle, lookback_hours=args.lookback_hours)
            posts = result.posts
            for path in result.raw_paths:
                print(f"raw: {path}")
            if result.stopped:
                print(f"stopped: {result.stopped}", file=sys.stderr)
        else:
            posts = [q for f in args.file for q in read_raw(Path(f))]
        ctx = rf.Context.load(
            allow,
            rosters_dir=Path(args.rosters),
            schedule_cache=Path(args.schedule_cache),
            aliases_path=Path(args.aliases),
            at=datetime.now(UTC),
        )
        claimed, counts = rf.claims_for_posts(posts, ctx)
        written = None
        if args.facts_db:
            con = rf.open_facts_db(Path(args.facts_db))
            try:
                written = rf.write_claimed_facts(con, claimed)
            finally:
                con.close()
        print(rf.format_report(claimed, counts, written))
        return 0
    except (KeyMissingError, HandleNotAllowed, BudgetExceeded, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"cannot read: {exc}", file=sys.stderr)
        return 2
    except (RateLimited, Unreachable, ReporterHTTPError) as exc:
        print(f"X API: {exc}", file=sys.stderr)
        return 4
    except rf.FactsDbError as exc:
        print(f"facts db: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
