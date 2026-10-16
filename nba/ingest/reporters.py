"""Read-only X API v2 adapter for allow-listed NBA beat reporters (docs/REPORTER_FEED.md).

Purpose: catch pre-tip facts the official injury report never carries, above all minutes
restrictions. Decided 2026-10-10 ("confirm everyone", item 3) and 2026-10-11 ~00:55 (item 4).

Guarantees
----------
* Read-only. The only paths this module can call are ``GET /2/users/by/username/:name``,
  ``GET /2/users/by`` (batch, used only by the one-off ``check-handles``) and
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
* Month caps. ``posts_per_month`` and ``usd_per_month`` (posts x ``usd_per_post`` + user reads x
  ``usd_per_user``) are checked BEFORE each request against the same ledger, by UTC month;
  exceeded means exit 2.
* Slate-only and pre-game window. ``poll`` only reads handles whose team plays today (ET) unless
  ``--all-teams``; league-wide handles are always eligible. A handle is read only inside
  ``[its team's tip - 120 min, its team's tip]`` (league-wide: slate first tip - 120 min to the
  slate's last tip); outside the window the run is refused (exit 2) before any request.
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
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
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
#: The only paths this client can call (all GET, all read-only).
ENDPOINTS = {
    "user_by_username": "/users/by/username/{username}",
    "users_by": "/users/by",
    "user_tweets": "/users/{user_id}/tweets",
}
TWEET_FIELDS = "created_at,referenced_tweets,note_tweet"
USER_FIELDS = "name,description,verified,created_at,public_metrics,most_recent_tweet_id"
CHECK_MAX_HANDLES = 60  # hard cap for the one-off check-handles command
USERS_BY_BATCH = 100  # the API's own bound for usernames per request
CANDIDATES_PATH = ROOT / "configs" / "reporter_candidates.txt"
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


class PollRefused(ReporterError):
    """No slate, no schedule, or no handle inside its pre-game window; nothing was requested."""


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
    posts_per_day: int = 400
    lookback_hours: float = 6.0
    posts_per_month: int = 8000
    usd_per_month: float = 40.0
    usd_per_post: float = 0.005
    usd_per_user: float = 0.010
    window_minutes: float = 120.0

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
        posts_per_day=int(budget.get("posts_per_day", 400)),
        lookback_hours=float(raw.get("lookback_hours", 6)),
        posts_per_month=int(budget.get("posts_per_month", 8000)),
        usd_per_month=float(budget.get("usd_per_month", 40)),
        usd_per_post=float(budget.get("usd_per_post", 0.005)),
        usd_per_user=float(budget.get("usd_per_user", 0.010)),
        window_minutes=float(raw.get("window_minutes", 120)),
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
    "total_posts": N}``. ``per_run`` is this process's cap; ``per_day`` the ceiling across runs;
    ``per_month`` and ``usd_per_month`` the UTC-month ceilings (USD = posts x ``usd_per_post``)."""

    def __init__(
        self,
        path: Path,
        per_run: int,
        per_day: int,
        per_month: int = 8000,
        usd_per_month: float = 40.0,
        usd_per_post: float = 0.005,
        usd_per_user: float = 0.010,
    ) -> None:
        self.path = path
        self.per_run = int(per_run)
        self.per_day = int(per_day)
        self.per_month = int(per_month)
        self.usd_per_month = float(usd_per_month)
        self.usd_per_post = float(usd_per_post)
        self.usd_per_user = float(usd_per_user)
        self.run_users = 0
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

    def _month_sum(self, now: datetime, key: str) -> int:
        prefix = utc_day(now)[:7]
        days = self._load()["days"]
        return sum(int(v.get(key, 0)) for d, v in days.items() if d.startswith(prefix))

    def used_month(self, now: datetime) -> int:
        return self._month_sum(now, "posts")

    def requests_today(self, now: datetime) -> int:
        return int(self._load()["days"].get(utc_day(now), {}).get("requests", 0))

    def requests_month(self, now: datetime) -> int:
        return self._month_sum(now, "requests")

    def users_today(self, now: datetime) -> int:
        return int(self._load()["days"].get(utc_day(now), {}).get("users", 0))

    def users_month(self, now: datetime) -> int:
        return self._month_sum(now, "users")

    def usd(self, posts: int, users: int = 0) -> float:
        return round(posts * self.usd_per_post + users * self.usd_per_user, 4)

    def month_usd(self, now: datetime) -> float:
        return self.usd(self.used_month(now), self.users_month(now))

    def month_post_cap(self, now: datetime) -> int:
        """Posts per UTC month still readable under BOTH the post cap and the USD cap (user
        reads already billed this month use up USD first); this is a cap on the month total."""
        used = self.used_month(now)
        if self.usd_per_post <= 0:
            return 0
        spare = self.usd_per_month - self.month_usd(now)
        by_usd = used + int(max(0.0, spare) / self.usd_per_post + 1e-9)
        return min(self.per_month, by_usd)

    def remaining(self, now: datetime) -> int:
        return max(
            0,
            min(
                self.per_run - self.run_posts,
                self.per_day - self.used_today(now),
                self.month_post_cap(now) - self.used_month(now),
            ),
        )

    def check(self, now: datetime, need: int = MAX_RESULTS_MIN) -> int:
        """Posts this run may still read; raises :class:`BudgetExceeded` if fewer than ``need``
        (the API will not return fewer than ``MAX_RESULTS_MIN`` per page)."""
        rem = self.remaining(now)
        if rem < need:
            month = self.used_month(now)
            raise BudgetExceeded(
                f"post budget exhausted: run {self.run_posts}/{self.per_run}, "
                f"day {self.used_today(now)}/{self.per_day} ({utc_day(now)} UTC), "
                f"month {month}/{self.per_month} posts and "
                f"${self.month_usd(now):.2f}/${self.usd_per_month:.2f} (posts and users); "
                f"{rem} left, a page needs {need}. Raise --budget-posts to continue."
            )
        return rem

    def check_user(self, now: datetime) -> None:
        """Refuse BEFORE a user lookup if one more user read would pass the USD month cap."""
        if self.month_usd(now) + self.usd_per_user > self.usd_per_month + 1e-9:
            raise BudgetExceeded(
                f"user lookup refused: month ${self.month_usd(now):.2f} + ${self.usd_per_user:.3f} "
                f"> ${self.usd_per_month:.2f} cap"
            )

    def check_users(self, now: datetime, n: int) -> None:
        """Refuse BEFORE a batch lookup if ``n`` user reads (worst case: all found) would pass
        the USD month cap."""
        if self.month_usd(now) + n * self.usd_per_user > self.usd_per_month + 1e-9:
            raise BudgetExceeded(
                f"batch user lookup refused: month ${self.month_usd(now):.2f} + "
                f"{n} x ${self.usd_per_user:.3f} > ${self.usd_per_month:.2f} cap"
            )

    def charge(self, now: datetime, posts: int, requests: int = 0, users: int = 0) -> None:
        data = self._load()
        day = data["days"].setdefault(utc_day(now), {"posts": 0, "requests": 0})
        if users:
            day["users"] = int(day.get("users", 0)) + int(users)
        self.run_users += int(users)
        day["posts"] = int(day.get("posts", 0)) + int(posts)
        day["requests"] = int(day.get("requests", 0)) + int(requests)
        data["total_posts"] = int(data.get("total_posts", 0)) + int(posts)
        self.run_posts += int(posts)
        self.run_requests += int(requests)
        self._save(data)


class UserIdCache:
    """``data/reporters/user_ids.json``: handle (lower) -> numeric user id. A lookup is a billed
    user read, so it happens once per handle, ever; legacy ids in ``state.json`` are honoured."""

    def __init__(self, path: Path, legacy: HandleState | None = None) -> None:
        self.path = path
        self.legacy = legacy

    def _load(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def get(self, handle: str) -> str | None:
        got = self._load().get(handle.lower())
        if got is None and self.legacy is not None:
            old = self.legacy.get(handle).get("user_id")
            got = str(old) if old else None
        return got

    def put(self, handle: str, uid: str) -> None:
        data = self._load()
        data[handle.lower()] = uid
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
        os.replace(tmp, self.path)


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
    skipped: dict[str, str] = field(default_factory=dict)  # handle -> why not polled
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


# ----------------------------------------------------------------------------------------------
# Slate-only polling and the pre-game window
# ----------------------------------------------------------------------------------------------


def plan_poll(
    allowlist: Allowlist,
    games: Iterable[Any],
    now: datetime,
    handles: Iterable[str] | None = None,
    *,
    all_teams: bool = False,
) -> tuple[list[str], dict[str, str]]:
    """Which handles may be read right now. ``games`` are ``ScheduledGame``-like (naive-UTC
    ``tipoff``, ``home_team``, ``away_team``). Returns ``(handles, skipped)``; raises
    :class:`PollRefused` when there is no slate or nothing is inside its window.

    * default: a team handle is eligible only if its team plays today (ET); ``all_teams`` lifts
      this (the window below still applies, using the slate span for teams without a game).
    * league-wide handles (team null) are always eligible.
    * window: a team handle in ``[its tip - window, its tip]``; league-wide and no-game handles
      in ``[slate first tip - window, slate last tip]``. Outside it the handle is skipped, and
      if no handle is left the run is refused. Nothing here makes a request.
    """
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    now = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    today = now.astimezone(et).date()
    slate = [g for g in games if g.tipoff.replace(tzinfo=UTC).astimezone(et).date() == today]
    if not slate:
        raise PollRefused(f"no games on the {today} ET slate (or no schedule); nothing to poll")
    window = timedelta(minutes=allowlist.window_minutes)
    tips = [g.tipoff.replace(tzinfo=UTC) for g in slate]
    first, last = min(tips), max(tips)
    team_tip: dict[int, datetime] = {}
    for g in slate:
        t = g.tipoff.replace(tzinfo=UTC)
        for tid in (g.home_team, g.away_team):
            team_tip[tid] = t
    names = list(handles) if handles is not None else allowlist.handles()
    ok: list[str] = []
    skipped: dict[str, str] = {}
    for h in names:
        rep = allowlist.require(h)  # refuses before any request
        tid = rep.team_id
        if tid is not None and tid not in team_tip and not all_teams:
            skipped[rep.handle] = f"{rep.team} does not play on {today} ET"
            continue
        if tid is not None and tid in team_tip:
            lo, hi = team_tip[tid] - window, team_tip[tid]
        else:
            lo, hi = first - window, last
        if not lo <= now <= hi:
            skipped[rep.handle] = f"outside window {lo:%H:%MZ}-{hi:%H:%MZ} (now {now:%H:%MZ})"
            continue
        ok.append(rep.handle)
    if not ok:
        raise PollRefused(
            "no handle is inside its pre-game window; refusing before any request: "
            + "; ".join(f"{k}: {v}" for k, v in skipped.items())
        )
    return ok, skipped


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
            allowlist.posts_per_month,
            allowlist.usd_per_month,
            allowlist.usd_per_post,
            allowlist.usd_per_user,
        )
        self.state = HandleState(self.data_dir / "state.json")
        self.user_ids = UserIdCache(self.data_dir / "user_ids.json", self.state)
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
        cached = self.user_ids.get(rep.handle)
        if cached:
            return cached
        self.budget.check_user(self.now())  # refuses before the lookup is sent
        body = self._get(ENDPOINTS["user_by_username"].format(username=rep.handle), {})
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict) or not data.get("id"):
            raise ReporterHTTPError(200, f"no user id in response for {rep.handle!r}")
        uid = str(data["id"])
        self.user_ids.put(rep.handle, uid)
        self.budget.charge(self.now(), 0, users=1)  # one user resource read = $usd_per_user
        return uid

    def users_lookup(self, handles: list[str]) -> list[dict[str, Any]]:
        """One-off ``GET /2/users/by`` for candidate handles (NOT allow-listed, nothing is added
        to the allow-list or the user-id cache). Cap, month USD cap checked before the request;
        each returned user is billed as one user read. Returns one row per input handle."""
        if len(handles) > CHECK_MAX_HANDLES:
            raise PollRefused(f"{len(handles)} handles > hard cap {CHECK_MAX_HANDLES}; refusing")
        for h in handles:
            if not _HANDLE_RE.match(h):
                raise ValueError(f"{h!r} is not a valid X handle")
        rows: list[dict[str, Any]] = []
        for i in range(0, len(handles), USERS_BY_BATCH):
            batch = handles[i : i + USERS_BY_BATCH]
            self.budget.check_users(self.now(), len(batch))
            body = self._get(
                ENDPOINTS["users_by"],
                {"usernames": ",".join(batch), "user.fields": USER_FIELDS},
            )
            data = body.get("data") if isinstance(body, dict) else None
            found = {
                str(u.get("username", "")).lower(): u
                for u in (data if isinstance(data, list) else [])
                if isinstance(u, dict)
            }
            self.budget.charge(self.now(), 0, users=len(found))
            for h in batch:
                u = found.get(h.lower())
                rows.append(_user_row(h, u))
        return rows

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
# Schedule override (preseason) and the live-test report
# ----------------------------------------------------------------------------------------------


def load_schedule_parquet(path: Path | str, teams: Iterable[str] | None = None) -> list[Any]:
    """Games from a raw schedule parquet (``gameDateTimeUTC``, ``*_teamTricode``), as
    ``ScheduledGame`` rows with naive-UTC tips. ``teams`` (tricodes) keeps only games that involve
    one of them. Raises ``ValueError`` on an unknown team, a missing column or an empty result."""
    from nba.daily.schedule import ScheduledGame

    keep = {t.upper() for t in teams} if teams else None
    if keep is not None:
        bad = sorted(keep - set(TEAM_ABBREV_TO_ID))
        if bad:
            raise ValueError(f"unknown team(s) {bad}")
    cols = ["gameId", "gameDateTimeUTC", "homeTeam_teamTricode", "awayTeam_teamTricode"]
    try:
        df = pl.read_parquet(path).select(cols)
    except pl.exceptions.ColumnNotFoundError as exc:
        raise ValueError(f"schedule {path}: missing column ({exc})") from exc
    out: list[Any] = []
    for gid, tip, home, away in df.iter_rows():
        if tip is None or home not in TEAM_ABBREV_TO_ID or away not in TEAM_ABBREV_TO_ID:
            continue
        if keep is not None and not ({home, away} & keep):
            continue
        out.append(
            ScheduledGame(
                str(gid),
                _naive_utc(datetime.fromisoformat(str(tip).replace("Z", "+00:00"))),
                TEAM_ABBREV_TO_ID[home],
                TEAM_ABBREV_TO_ID[away],
            )
        )
    if not out:
        raise ValueError(f"schedule {path}: no games" + (f" for {sorted(keep)}" if keep else ""))
    return out


BUCKETS = (("T-120..-60", 60, 120), ("T-60..-30", 30, 60), ("T-30..0", 0, 30))


def _bucket(minutes_before: float) -> str:
    for name, lo, hi in BUCKETS:
        if lo <= minutes_before < hi or (hi == 120 and minutes_before == 120):
            return name
    return "outside"


def report_test(
    data_dir: Path,
    games: list[Any],
    allow: Allowlist,
    ctx: Any,
    day: str | None = None,
) -> str:
    """Read-only summary of the raw posts of one UTC day (default today): per handle and
    window, posts, parsed claims by type and the minutes-before-tip of each claim."""
    from nba.ingest import reporter_facts as rf

    day = day or datetime.now(UTC).strftime("%Y-%m-%d")
    files = sorted((data_dir / "raw" / day).glob("*.parquet"))
    posts = [q for f in files for q in read_raw(f)]
    team_tip: dict[int, datetime] = {}
    for g in games:
        for tid in (g.home_team, g.away_team):
            team_tip[tid] = g.tipoff
    first = min(g.tipoff for g in games)
    claimed, _ = rf.claims_for_posts(posts, ctx)
    tip_of = {}
    for p in posts:
        rep = allow.require(p.handle)
        tip_of[p.post_id] = team_tip.get(rep.team_id, first) if rep.team_id else first
    stats: dict[tuple[str, str], dict[str, Any]] = {}

    def cell(h: str, b: str) -> dict[str, Any]:
        return stats.setdefault((h, b), {"posts": 0, "claims": Counter(), "mins": []})

    for p in posts:
        mb = (tip_of[p.post_id] - p.created_at).total_seconds() / 60
        cell(p.handle, _bucket(mb))["posts"] += 1
    for cf in claimed:
        tip = cf.game.tipoff if cf.game is not None else tip_of[cf.post.post_id]
        mb = (tip - cf.post.created_at).total_seconds() / 60
        c = cell(cf.post.handle, _bucket(mb))
        c["claims"][cf.claim.claim_type] += 1
        c["mins"].append(round(mb))
    lines = [f"reporter live test: {len(posts)} raw posts from {len(files)} files on {day} (UTC)"]
    order = {name: i for i, (name, _, _) in enumerate(BUCKETS)}
    for (h, b), c in sorted(stats.items(), key=lambda kv: (kv[0][0], order.get(kv[0][1], 9))):
        claims = ", ".join(f"{k}={v}" for k, v in sorted(c["claims"].items())) or "none"
        mins = ",".join(str(m) for m in sorted(c["mins"], reverse=True)) or "-"
        lines.append(
            f"{h:<18}{b:<11} posts={c['posts']:<3} claims: {claims}; min_before_tip: {mins}"
        )
    if not stats:
        lines.append("no posts")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------------------------


def budget_report(allow: Allowlist, data_dir: Path, now: datetime | None = None) -> str:
    """Today's and this UTC month's posts, HTTP attempts and estimated USD vs the caps."""
    now = now or datetime.now(UTC)
    b = PostBudget(
        data_dir / "budget.json",
        allow.posts_per_run,
        allow.posts_per_day,
        allow.posts_per_month,
        allow.usd_per_month,
        allow.usd_per_post,
        allow.usd_per_user,
    )
    day, month = b.used_today(now), b.used_month(now)
    ud, um = b.users_today(now), b.users_month(now)
    return "\n".join(
        [
            f"day   {utc_day(now)} UTC: {day}/{b.per_day} posts, "
            f"{b.requests_today(now)} attempts, {ud} user reads, est ${b.usd(day, ud):.2f} "
            f"(posts ${b.usd(day):.2f} + users ${b.usd(0, ud):.2f})",
            f"month {utc_day(now)[:7]} UTC: {month}/{b.per_month} posts, "
            f"{b.requests_month(now)} attempts, {um} user reads, est ${b.usd(month, um):.2f}/"
            f"${b.usd_per_month:.2f} (posts ${b.usd(month):.2f} + users ${b.usd(0, um):.2f})",
            f"rates ${b.usd_per_post}/post, ${b.usd_per_user}/user read "
            "(configs/reporters.yaml; not in cost.yaml)",
        ]
    )


def _user_row(handle: str, u: Mapping[str, Any] | None) -> dict[str, Any]:
    if u is None:
        return {"handle": handle, "exists": False}
    pm = u.get("public_metrics") or {}
    return {
        "handle": handle,
        "exists": True,
        "id": u.get("id"),
        "username": u.get("username"),
        "name": u.get("name"),
        "description": u.get("description"),
        "verified": u.get("verified"),
        "created_at": u.get("created_at"),
        "followers": pm.get("followers_count") if isinstance(pm, dict) else None,
        "most_recent_tweet_id": u.get("most_recent_tweet_id"),
    }


def read_candidates(path: Path | str) -> list[str]:
    """Handles one per line, ``#`` comments allowed; deduplicated case-insensitively."""
    out: list[str] = []
    seen: set[str] = set()
    for line in Path(path).read_text().splitlines():
        h = line.split("#", 1)[0].strip().lstrip("@")
        if h and h.lower() not in seen:
            seen.add(h.lower())
            out.append(h)
    return out


def format_check_table(rows: list[dict[str, Any]]) -> str:
    def cell(v: Any, n: int) -> str:
        return str(v if v is not None else "").replace("\n", " ").replace("|", "/")[:n]

    lines = ["handle | exists | name | bio (120) | verified | followers"]
    for r in rows:
        lines.append(
            " | ".join(
                [
                    cell(r["handle"], 15),
                    "yes" if r["exists"] else "NO",
                    cell(r.get("name"), 40),
                    cell(r.get("description"), 120),
                    cell(r.get("verified"), 5),
                    cell(r.get("followers"), 10),
                ]
            )
        )
    return "\n".join(lines)


def check_handles(
    candidates: Path | str,
    *,
    allowlist_path: Path | str = ALLOWLIST_PATH,
    data_dir: Path = DATA_DIR,
    dry_run: bool = False,
    tag: str | None = None,
    environ: Mapping[str, str] | None = None,
    transport: Any = None,
    clock: Callable[[], datetime] | None = None,
) -> int:
    """One-off, read-only candidate-handle check. Writes only the write-once result file and
    the usage ledger; never the allow-list or ``user_ids.json``."""
    handles = read_candidates(candidates)
    if not handles:
        raise ValueError(f"{candidates}: no handles")
    if len(handles) > CHECK_MAX_HANDLES:
        raise PollRefused(f"{len(handles)} handles > hard cap {CHECK_MAX_HANDLES}; refusing")
    for h in handles:
        if not _HANDLE_RE.match(h):
            raise ValueError(f"{h!r} is not a valid X handle")
    allow = load_allowlist(allowlist_path)
    now = (clock or (lambda: datetime.now(UTC)))()
    if tag is not None and not re.fullmatch(r"[a-z0-9]{1,16}", tag):
        raise ValueError(f"tag {tag!r} must be 1-16 lowercase letters or digits")
    suffix = f"_{tag}" if tag else ""
    out = Path(data_dir) / f"handle_check_{utc_day(now)}{suffix}.json"
    est = len(handles) * allow.usd_per_user
    if dry_run:
        print(f"dry-run: GET {API_BASE}{ENDPOINTS['users_by']} usernames={','.join(handles)}")
        print(f"{len(handles)} handles, 1 request, worst-case ${est:.3f} -> {out}")
        return 0
    if out.exists():
        raise PollRefused(f"{out} exists; the result file is write-once")
    client = XClient(
        replace(allow, reporters={}),  # the check never reads the allow-list's handles
        environ=environ,
        data_dir=Path(data_dir),
        transport=transport,
        clock=clock,
    )
    rows = client.users_lookup(handles)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": now.isoformat(), "n": len(rows), "users": rows}
    with out.open("x") as f:
        json.dump(payload, f, indent=1)
    print(format_check_table(rows))
    print(f"wrote {out}; billed {sum(r['exists'] for r in rows)} user reads")
    return 0


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--allowlist", default=str(ALLOWLIST_PATH))
    p.add_argument("--facts-db", default=None, help="write claimed facts here (omit = print)")
    p.add_argument("--rosters", default=str(ROOT / "data" / "rosters"))
    p.add_argument("--schedule-cache", default=str(ROOT / "data" / "schedule_cache"))
    p.add_argument("--aliases", default=str(ROOT / "configs" / "kalshi_aliases.yaml"))
    p.add_argument(
        "--nicknames", default=str(ROOT / "configs" / "reporter_nicknames.yaml"), help="reviewed"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="nba.ingest.reporters", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    poll = sub.add_parser("poll", help="one page per allow-listed handle (billed), then parse")
    _common(poll)
    poll.add_argument("--budget-posts", type=int, default=None, help="cap for this run")
    poll.add_argument("--lookback-hours", type=float, default=None)
    poll.add_argument("--handle", action="append", default=None, help="subset of the list")
    poll.add_argument("--data-dir", default=str(DATA_DIR))
    poll.add_argument(
        "--all-teams",
        action="store_true",
        help="also poll handles whose team has no game today (the window still applies)",
    )
    poll.add_argument("--schedule", default=None, help="schedule parquet overriding the cache")
    poll.add_argument("--teams", nargs="+", default=None, help="tricodes; restrict the slate")
    rt = sub.add_parser("report-test", help="read-only: today's raw posts by handle and window")
    _common(rt)
    rt.add_argument("--data-dir", default=str(DATA_DIR))
    rt.add_argument("--schedule", required=True)
    rt.add_argument("--teams", nargs="+", default=None)
    rt.add_argument("--date", default=None, help="UTC day of the raw dir (default today)")
    bud = sub.add_parser("budget", help="today's and this month's posts, attempts, est. USD")
    bud.add_argument("--allowlist", default=str(ALLOWLIST_PATH))
    bud.add_argument("--data-dir", default=str(DATA_DIR))
    rep = sub.add_parser("parse-only", help="replay raw parquet files (no network, no cost)")
    _common(rep)
    rep.add_argument("--file", action="append", required=True)
    rep.add_argument("--schedule", default=None, help="schedule parquet overriding the cache")
    rep.add_argument("--teams", nargs="+", default=None, help="tricodes; restrict the schedule")
    chk = sub.add_parser(
        "check-handles", help="one-off: batch-verify candidate handles (billed $/user read)"
    )
    chk.add_argument("--candidates", default=str(CANDIDATES_PATH))
    chk.add_argument("--allowlist", default=str(ALLOWLIST_PATH))
    chk.add_argument("--data-dir", default=str(DATA_DIR))
    chk.add_argument("--dry-run", action="store_true", help="print the batch and cost; no request")
    chk.add_argument("--tag", default=None, help="suffix: a second write-once file the same day")
    args = p.parse_args(argv)

    if args.cmd == "check-handles":
        try:
            return check_handles(
                args.candidates,
                allowlist_path=args.allowlist,
                data_dir=Path(args.data_dir),
                dry_run=args.dry_run,
                tag=args.tag,
            )
        except (KeyMissingError, BudgetExceeded, PollRefused, ValueError) as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        except OSError as exc:
            print(f"cannot read: {exc}", file=sys.stderr)
            return 2
        except (RateLimited, Unreachable, ReporterHTTPError) as exc:
            print(f"X API: {exc}", file=sys.stderr)
            return 4

    if args.cmd == "budget":
        try:
            print(budget_report(load_allowlist(args.allowlist), Path(args.data_dir)))
        except (ValueError, OSError) as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        return 0

    from nba.ingest import reporter_facts as rf  # lazy: duckdb + schedule only when used

    try:
        allow = load_allowlist(args.allowlist)
        if args.cmd == "report-test":
            games = load_schedule_parquet(args.schedule, args.teams)
            ctx0 = rf.Context.load(
                allow,
                rosters_dir=Path(args.rosters),
                schedule_cache=Path(args.schedule_cache),
                aliases_path=Path(args.aliases),
                nicknames_path=Path(args.nicknames),
                at=datetime.now(UTC),
            )
            ctx0 = replace(ctx0, schedule=games)
            print(report_test(Path(args.data_dir), games, allow, ctx0, args.date))
            return 0
        if args.cmd == "poll":
            if not allow.reporters:
                print("configs/reporters.yaml lists no handles; nothing to poll.", file=sys.stderr)
                return 2
            read_token()  # a missing key refuses before the schedule is even read
            now = datetime.now(UTC)
            from nba.daily.schedule import load_schedule_cache

            if args.schedule:
                games = load_schedule_parquet(args.schedule, args.teams)
            else:
                games = load_schedule_cache(rf.season_for(now), Path(args.schedule_cache)) or []
                if args.teams:
                    keep = {TEAM_ABBREV_TO_ID[t.upper()] for t in args.teams}
                    games = [g for g in games if {g.home_team, g.away_team} & keep]
            todo, skipped = plan_poll(allow, games, now, args.handle, all_teams=args.all_teams)
            for h, why in skipped.items():
                print(f"skipped {h}: {why}", file=sys.stderr)
            client = XClient(allow, data_dir=Path(args.data_dir), budget_posts=args.budget_posts)
            result = client.poll(todo, lookback_hours=args.lookback_hours)
            result.skipped = skipped
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
            nicknames_path=Path(args.nicknames),
            at=datetime.now(UTC),
        )
        if args.cmd == "poll" and args.schedule:
            ctx = replace(ctx, schedule=games)
        elif args.cmd == "parse-only" and args.schedule:
            ctx = replace(ctx, schedule=load_schedule_parquet(args.schedule, args.teams))
        claimed, counts = rf.claims_for_posts(posts, ctx)
        written = None
        if args.facts_db:
            con = rf.open_facts_db(Path(args.facts_db))
            try:
                written = rf.write_claimed_facts(con, claimed)
            finally:
                con.close()
        print(rf.format_report(claimed, counts, written))
        return 2 if args.cmd == "poll" and result.stopped else 0
    except (KeyMissingError, HandleNotAllowed, BudgetExceeded, PollRefused, ValueError) as exc:
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
