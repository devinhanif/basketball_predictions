"""Read-only HISTORICAL client and parsers for the-odds-api.com (``https://api.the-odds-api.com/v4``).

This is NOT ``nba.odds.theoddsapi`` (theoddsapi.com, the live benchmark feed). Different vendor,
different key (``THE_ODDS_API_KEY``), different auth (``apiKey`` query parameter), different cache
and ledger. Nothing here is shared with that adapter except pure helpers (price maths, the name
resolver).

Guarantees
----------
* Read-only: the only paths are ``/historical/sports/{sport}/events`` and
  ``/historical/sports/{sport}/events/{eventId}/odds`` (GET). No order or account path exists.
* The key comes from ``os.environ["THE_ODDS_API_KEY"]`` only. It is sent as the ``apiKey`` query
  parameter (the vendor offers nothing else), held in a wrapper whose ``repr``/``str`` is
  ``<secret>``, removed from every stored param set, and scrubbed from every exception and log line.
  ``httpx`` INFO logging (which echoes full URLs) is silenced when a client is built.
* Raw first: every 200 response is gzipped (atomically) to
  ``<raw_dir>/<season>/<game_id>/<endpoint>_<snapshot-kind>_<regions>_<markets-hash>.json.gz``
  BEFORE it is parsed or counted. The path is a pure function of the request, so a rerun finds the
  file and costs 0 credits (and 0 requests).
* Credit ledger: every paid call is appended (cost taken from ``x-requests-last``) to a JSONL file
  beside a totals JSON. A call is refused BEFORE it is made when cumulative spend would exceed
  ``max_credits`` or when the server-reported remaining would fall below ``min_server_remaining``.
* Leak guard: the vendor returns the snapshot AT OR BEFORE the requested ``date``. Parsers keep both
  ``requested_at`` and the returned ``snapshot_ts`` so that ``snapshot_ts <= requested_at`` can be
  audited downstream. Nothing in this module reads model predictions.
"""

from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import logging
import os
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import yaml

from nba.markets.implied import devig_two_way
from nba.odds.theoddsapi import NameResolver, american_to_prob

log = logging.getLogger("nba.odds.history")

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "odds_history.yaml"
SOURCE = "the_odds_api"
KEY_ENV = "THE_ODDS_API_KEY"
KEY_PARAM = "apiKey"
SNAPSHOT_KINDS = ("t60", "t5")


# ----------------------------------------------------------------------------------------------
# Errors (messages never contain the key)
# ----------------------------------------------------------------------------------------------


class HistoryError(Exception):
    """Base class."""


class KeyMissingError(HistoryError):
    """``THE_ODDS_API_KEY`` is unset or empty."""


class CreditBudgetExceeded(HistoryError):
    """Spend cap, server floor, or exhausted 429 retries stopped the call. Fatal to a run."""


class AuthError(HistoryError):
    """HTTP 401/403 (bad key, plan, out of credits). Fatal to a run."""


class UnreachableError(HistoryError):
    """Transport failure or 5xx after bounded retries. Per-item (a rerun retries it)."""


class RequestRejected(HistoryError):
    """Another non-200 (for example 422 for a bad parameter). Per-item."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status


class EventMatchError(HistoryError):
    """The events list did not yield exactly one event for our game (loud, counted, per-item)."""

    def __init__(self, game_id: str, n_matches: int, detail: str) -> None:
        super().__init__(f"game {game_id}: {n_matches} matching events ({detail})")
        self.game_id = game_id
        self.n_matches = n_matches


# ----------------------------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoryConfig:
    base_url: str = "https://api.the-odds-api.com/v4"
    sport_key: str = "basketball_nba"
    regions: tuple[str, ...] = ("us", "eu", "us_ex")
    prop_markets: tuple[str, ...] = (
        "player_points",
        "player_rebounds",
        "player_assists",
        "player_threes",
    )
    game_markets: tuple[str, ...] = ("h2h", "spreads", "totals")
    odds_format: str = "american"
    max_credits: int = 2_000_000
    min_server_remaining: int = 100_000
    cost_per_market_region: int = 10
    cost_events_list: int = 1
    min_interval_s: float = 0.35
    timeout_s: float = 30.0
    max_attempts: int = 3
    retry_backoff_s: float = 2.0
    max_429_waits: int = 6
    backoff_429_s: float = 5.0
    snapshots: tuple[tuple[str, int], ...] = (("t60", 60), ("t5", 5))
    event_match_window_h: float = 12.0
    seasons: tuple[int, ...] = (2023, 2024, 2025)
    raw_dir: Path = ROOT / "data" / "odds" / "raw" / "the_odds_api"
    ledger_path: Path = ROOT / "data" / "odds" / "budget" / "the_odds_api_ledger.json"
    state_path: Path = ROOT / "data" / "odds" / "history_pull_state.jsonl"
    out_db: Path = ROOT / "data" / "odds" / "odds_history.duckdb"
    games_db: Path = ROOT / "nba.duckdb"
    schedule_dir: Path = ROOT / "data" / "schedule"

    def markets_for(self, phase: str) -> tuple[str, ...]:
        if phase == "props":
            return self.prop_markets
        if phase == "games":
            return self.game_markets
        raise ValueError(f"unknown phase {phase!r} (props|games)")

    def expected_odds_cost(self, phase: str) -> int:
        return self.cost_per_market_region * len(self.markets_for(phase)) * len(self.regions)

    def minutes_before_tip(self, kind: str) -> int:
        for k, m in self.snapshots:
            if k == kind:
                return m
        raise ValueError(f"unknown snapshot kind {kind!r}")


def _repo_path(value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def load_config(path: Path | None = None) -> HistoryConfig:
    """``configs/odds_history.yaml`` merged over the defaults; unknown keys are ignored."""
    p = path or CONFIG_PATH
    raw: dict[str, Any] = {}
    if p.exists():
        loaded = yaml.safe_load(p.read_text()) or {}
        if isinstance(loaded, dict):
            raw = loaded
    base = HistoryConfig()
    upd: dict[str, Any] = {}
    for k in ("base_url", "sport_key", "odds_format"):
        if k in raw:
            upd[k] = str(raw[k])
    for k in (
        "max_credits",
        "min_server_remaining",
        "cost_per_market_region",
        "cost_events_list",
        "max_attempts",
        "max_429_waits",
    ):
        if k in raw:
            upd[k] = int(raw[k])
    for k in (
        "min_interval_s",
        "timeout_s",
        "retry_backoff_s",
        "backoff_429_s",
        "event_match_window_h",
    ):
        if k in raw:
            upd[k] = float(raw[k])
    for k in ("regions", "prop_markets", "game_markets"):
        if k in raw:
            upd[k] = tuple(str(x) for x in raw[k])
    if "seasons" in raw:
        upd["seasons"] = tuple(int(x) for x in raw["seasons"])
    if "snapshots" in raw:
        upd["snapshots"] = tuple((str(k), int(v)) for k, v in raw["snapshots"].items())
    for k in ("raw_dir", "ledger_path", "state_path", "out_db", "games_db", "schedule_dir"):
        if k in raw:
            upd[k] = _repo_path(raw[k])
    return replace(base, **upd) if upd else base


# ----------------------------------------------------------------------------------------------
# Secret handling
# ----------------------------------------------------------------------------------------------


class _Secret:
    """Holds the key; every textual form is ``<secret>``."""

    __slots__ = ("_v",)

    def __init__(self, value: str) -> None:
        self._v = value

    def reveal(self) -> str:
        return self._v

    def __repr__(self) -> str:
        return "<secret>"

    __str__ = __repr__

    def __reduce__(self) -> Any:  # never pickle/copy the key by accident
        raise TypeError("secret is not serialisable")


def read_key(environ: Mapping[str, str] | None = None) -> _Secret:
    env = os.environ if environ is None else environ
    value = (env.get(KEY_ENV) or "").strip()
    if not value:
        raise KeyMissingError(
            f"{KEY_ENV} is not set; export it (for example `uv run --env-file .env ...`). "
            "The key is read from the environment only."
        )
    return _Secret(value)


def redact(text: str, key: str | None = None) -> str:
    """Remove the key (when known) and any ``apiKey=...`` query value from ``text``."""
    import re

    out = text.replace(key, "<secret>") if key else text
    return re.sub(r"(apiKey=)[^&\s'\"]+", r"\1<secret>", out)


# ----------------------------------------------------------------------------------------------
# Credit ledger
# ----------------------------------------------------------------------------------------------


class CreditLedger:
    """Cumulative credit spend for this pull (totals JSON + one JSONL line per paid call).

    ``check`` is called BEFORE a request; ``record`` AFTER a 200. Both take a file lock so two
    pulls running at once (for example two seasons) cannot lose updates.
    """

    def __init__(self, path: Path, max_credits: int, min_server_remaining: int) -> None:
        self.path = path
        self.calls_path = path.with_suffix(".jsonl")
        self.max_credits = max_credits
        self.min_server_remaining = min_server_remaining

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_suffix(".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def totals(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = {}
        out: dict[str, Any] = data if isinstance(data, dict) else {}
        out.setdefault("spent", 0)
        out.setdefault("n_calls", 0)
        out.setdefault("server_remaining", None)
        out.setdefault("server_used", None)
        return out

    def spent(self) -> int:
        return int(self.totals()["spent"])

    def server_remaining(self) -> int | None:
        v = self.totals()["server_remaining"]
        return int(v) if v is not None else None

    def check(self, expected_cost: int) -> None:
        """Raise :class:`CreditBudgetExceeded` instead of letting a call overshoot a guard."""
        with self._locked():
            t = self.totals()
        spent = int(t["spent"])
        if spent + expected_cost > self.max_credits:
            raise CreditBudgetExceeded(
                f"credit cap: spent {spent} + next call {expected_cost} > max_credits "
                f"{self.max_credits}"
            )
        rem = t["server_remaining"]
        if rem is not None and int(rem) - expected_cost < self.min_server_remaining:
            raise CreditBudgetExceeded(
                f"server reports {int(rem)} credits remaining; a {expected_cost}-credit call would "
                f"go below the floor {self.min_server_remaining}"
            )

    def note_headers(self, remaining: int | None, used: int | None) -> None:
        """Remember the server's counters even when the response was not a paid 200."""
        if remaining is None and used is None:
            return
        with self._locked():
            t = self.totals()
            if remaining is not None:
                t["server_remaining"] = remaining
            if used is not None:
                t["server_used"] = used
            self._write(t)

    def record(self, entry: Mapping[str, Any]) -> None:
        """Append one call (``cost`` required) and update the totals."""
        with self._locked():
            t = self.totals()
            t["spent"] = int(t["spent"]) + int(entry["cost"])
            t["n_calls"] = int(t["n_calls"]) + 1
            if entry.get("server_remaining") is not None:
                t["server_remaining"] = int(entry["server_remaining"])
            if entry.get("server_used") is not None:
                t["server_used"] = int(entry["server_used"])
            t["max_credits"] = self.max_credits
            t["updated_at"] = datetime.now(UTC).isoformat()
            with open(self.calls_path, "a") as fh:
                fh.write(json.dumps(dict(entry), sort_keys=True) + "\n")
            self._write(t)

    def _write(self, totals: Mapping[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(totals), sort_keys=True))
        os.replace(tmp, self.path)


# ----------------------------------------------------------------------------------------------
# Raw cache + client
# ----------------------------------------------------------------------------------------------


def markets_hash(markets: Iterable[str]) -> str:
    return hashlib.sha1(",".join(sorted(markets)).encode()).hexdigest()[:8]


def raw_path(
    cfg: HistoryConfig,
    season: int,
    game_id: str,
    endpoint: str,
    snapshot_kind: str,
    regions: Sequence[str] = (),
    markets: Sequence[str] = (),
) -> Path:
    """Deterministic cache path: a pure function of the request (so reruns cost nothing)."""
    reg = "-".join(sorted(regions)) if regions else "none"
    mh = markets_hash(markets) if markets else "none"
    name = f"{endpoint}_{snapshot_kind}_{reg}_{mh}.json.gz"
    return cfg.raw_dir / str(season) / game_id / name


def iso_z(dt: datetime) -> str:
    """``2025-01-15T23:00:00Z`` from a naive-UTC or aware datetime."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: Any) -> datetime | None:
    """ISO ``...Z`` string to naive UTC; None for anything unusable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _int_header(headers: Mapping[str, str], name: str) -> int | None:
    v = headers.get(name)
    if v is None:
        return None
    try:
        return int(float(v))
    except ValueError:
        return None


@dataclass(frozen=True)
class Fetched:
    body: Any
    path: Path
    cost: int  # credits charged by THIS call (0 when read from the cache)
    from_cache: bool
    headers: dict[str, str]


def load_raw(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt") as fh:
        blob = json.load(fh)
    if not isinstance(blob, dict):
        raise ValueError(f"raw file {path.name} is not an object")
    return blob


class HistoricalClient:
    """GET-only client for the two historical endpoints. ``repr`` shows no secret."""

    def __init__(
        self,
        config: HistoryConfig | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        http_get: Callable[..., Any] | None = None,
        need_key: bool = True,
    ) -> None:
        self.config = config or load_config()
        self._key: _Secret | None = read_key(environ) if need_key else None
        self._sleep = sleep
        self._http_get = http_get
        self.ledger = CreditLedger(
            self.config.ledger_path, self.config.max_credits, self.config.min_server_remaining
        )
        self._last_call: float | None = None
        self.requests_made = 0
        self.cache_hits = 0
        self.credits_spent = 0
        # httpx logs full request URLs at INFO, and the URL carries the key as a query value.
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.WARNING)

    def __repr__(self) -> str:
        return f"HistoricalClient(base_url={self.config.base_url!r}, key=<secret>)"

    def _scrub(self, text: str) -> str:
        return redact(text, self._key.reveal() if self._key else None)

    # -- public endpoints ----------------------------------------------------------------------
    def events(
        self, *, season: int, game_id: str, snapshot_kind: str, at: datetime, refresh: bool = False
    ) -> Fetched:
        """Events list as of ``at`` (the snapshot at or before it). Cost 1."""
        path = raw_path(self.config, season, game_id, "events", snapshot_kind)
        url = f"/historical/sports/{self.config.sport_key}/events"
        params = {"date": iso_z(at)}
        ctx = {"season": season, "game_id": game_id, "snapshot_kind": snapshot_kind}
        return self._fetch(path, url, params, self.config.cost_events_list, ctx, refresh)

    def event_odds(
        self,
        *,
        season: int,
        game_id: str,
        snapshot_kind: str,
        at: datetime,
        event_id: str,
        markets: Sequence[str],
        refresh: bool = False,
    ) -> Fetched:
        """Per-event odds as of ``at``. Cost 10 x markets x regions (actual from the header)."""
        regions = self.config.regions
        path = raw_path(
            self.config, season, game_id, "odds", snapshot_kind, regions=regions, markets=markets
        )
        url = f"/historical/sports/{self.config.sport_key}/events/{event_id}/odds"
        params = {
            "regions": ",".join(regions),
            "markets": ",".join(markets),
            "oddsFormat": self.config.odds_format,
            "date": iso_z(at),
        }
        expected = self.config.cost_per_market_region * len(markets) * len(regions)
        ctx = {
            "season": season,
            "game_id": game_id,
            "snapshot_kind": snapshot_kind,
            "event_id": event_id,
        }
        return self._fetch(path, url, params, expected, ctx, refresh)

    # -- internals -----------------------------------------------------------------------------
    def cached(self, path: Path) -> bool:
        return path.exists()

    def _fetch(
        self,
        path: Path,
        url_path: str,
        params: dict[str, str],
        expected_cost: int,
        ctx: dict[str, Any],
        refresh: bool,
    ) -> Fetched:
        if path.exists() and not refresh:
            blob = load_raw(path)
            self.cache_hits += 1
            return Fetched(blob.get("body"), path, 0, True, dict(blob.get("headers") or {}))
        body, headers, cost = self._request(url_path, params, expected_cost)
        blob = {
            "endpoint": url_path,
            "params": params,  # never contains the key
            "status": 200,
            "headers": headers,
            "fetched_at": datetime.now(UTC).isoformat(),
            "cost": cost,
            **ctx,
            "body": body,
        }
        text = json.dumps(blob)
        if self._key is not None and self._key.reveal() in text:
            raise HistoryError("refusing to write a raw file that contains the API key")
        self._write_raw(path, text)  # raw before parse, before anything else uses the body
        self.ledger.record(
            {
                "ts": blob["fetched_at"],
                "endpoint": url_path.rsplit("/", 1)[-1],
                "path": str(path.relative_to(self.config.raw_dir)),
                "cost": cost,
                "server_remaining": _int_header(headers, "x-requests-remaining"),
                "server_used": _int_header(headers, "x-requests-used"),
                **{k: v for k, v in ctx.items() if k in ("season", "game_id", "snapshot_kind")},
            }
        )
        self.credits_spent += cost
        return Fetched(body, path, cost, False, headers)

    @staticmethod
    def _write_raw(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with gzip.open(tmp, "wt") as fh:
            fh.write(text)
        os.replace(tmp, path)

    def _throttle(self) -> None:
        if self._last_call is not None:
            wait = self.config.min_interval_s - (time.monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = time.monotonic()

    def _request(
        self, url_path: str, params: Mapping[str, str], expected_cost: int
    ) -> tuple[Any, dict[str, str], int]:
        if self._key is None:
            raise KeyMissingError(f"{KEY_ENV} is required to make a request")
        self.ledger.check(expected_cost)  # refuse BEFORE spending
        get = self._http_get or httpx.get
        url = self.config.base_url.rstrip("/") + url_path
        full = {**params, KEY_PARAM: self._key.reveal()}
        last_err = "no attempt made"
        attempts = 0
        waits_429 = 0
        while attempts < max(1, self.config.max_attempts):
            self._throttle()
            self.requests_made += 1
            try:
                resp = get(url, params=full, timeout=self.config.timeout_s)
            except httpx.TransportError as exc:
                attempts += 1
                last_err = f"transport error {type(exc).__name__}"
                log.warning("history request attempt %d: %s", attempts, last_err)
                self._backoff(attempts)
                continue
            headers = {
                k.lower(): str(v)
                for k, v in resp.headers.items()
                if k.lower().startswith("x-requests") or k.lower() == "retry-after"
            }
            self.ledger.note_headers(
                _int_header(headers, "x-requests-remaining"),
                _int_header(headers, "x-requests-used"),
            )
            status = int(resp.status_code)
            if status == 429:
                waits_429 += 1
                if waits_429 > self.config.max_429_waits:
                    raise CreditBudgetExceeded("HTTP 429 persisted after the backoff schedule")
                ra = _int_header(headers, "retry-after")
                wait = (
                    float(ra)
                    if ra
                    else min(120.0, self.config.backoff_429_s * 2 ** (waits_429 - 1))
                )
                log.warning("history HTTP 429; backing off %.0fs (%d)", wait, waits_429)
                self._sleep(wait)
                continue
            if status >= 500:
                attempts += 1
                last_err = f"HTTP {status}"
                log.warning("history request attempt %d: %s", attempts, last_err)
                self._backoff(attempts)
                continue
            if status in (401, 403):
                raise AuthError(f"HTTP {status}: {self._scrub(str(resp.text)[:200])}")
            if status != 200:
                raise RequestRejected(status, self._scrub(str(resp.text)[:200]))
            try:
                body = resp.json()
            except ValueError as exc:
                raise RequestRejected(200, "response is not JSON") from exc
            used = _int_header(headers, "x-requests-last")
            return body, headers, (used if used is not None else expected_cost)
        raise UnreachableError(f"{last_err} after {self.config.max_attempts} attempts")

    def _backoff(self, attempt: int) -> None:
        if attempt < self.config.max_attempts:
            self._sleep(self.config.retry_backoff_s * attempt)


# ----------------------------------------------------------------------------------------------
# Event matching
# ----------------------------------------------------------------------------------------------


def default_resolver() -> NameResolver:
    """``NameResolver.default()`` plus both spellings of the two Los Angeles franchises.

    This vendor writes "Los Angeles Clippers"; ``configs/team_markets.yaml`` says "LA Clippers"
    (and "Los Angeles Lakers"). Exact names only, never fuzzy.
    """
    res = NameResolver.default()
    for name, tid in list(res.teams.items()):
        if name.startswith("la "):
            res.teams.setdefault("los angeles " + name[3:], tid)
        elif name.startswith("los angeles "):
            res.teams.setdefault("la " + name[len("los angeles ") :], tid)
    return res


@dataclass(frozen=True)
class GameRef:
    """One of our games with a REAL scheduled tip-off (naive UTC)."""

    game_id: str
    season: int
    home_team: int
    away_team: int
    tipoff_utc: datetime


@dataclass(frozen=True)
class EventRef:
    event_id: str
    commence_time: datetime
    home_team: str
    away_team: str


def parse_events(body: Any) -> list[EventRef]:
    """The ``data`` list of a historical events response (malformed entries are skipped)."""
    data = body.get("data") if isinstance(body, dict) else None
    out: list[EventRef] = []
    if not isinstance(data, list):
        return out
    for ev in data:
        if not isinstance(ev, dict):
            continue
        ct = parse_ts(ev.get("commence_time"))
        eid = ev.get("id")
        if ct is None or not eid:
            continue
        out.append(
            EventRef(str(eid), ct, str(ev.get("home_team") or ""), str(ev.get("away_team") or ""))
        )
    return out


def match_event(
    events: Sequence[EventRef],
    game: GameRef,
    resolver: NameResolver,
    window_h: float = 12.0,
) -> EventRef:
    """The single event whose teams are ours (home AND away) and whose commence_time is within
    ``window_h`` hours of our tip. Raises :class:`EventMatchError` on 0 or >=2 matches."""
    lo = game.tipoff_utc - timedelta(hours=window_h)
    hi = game.tipoff_utc + timedelta(hours=window_h)
    hits = [
        e
        for e in events
        if lo <= e.commence_time <= hi
        and resolver.team_id(e.home_team) == game.home_team
        and resolver.team_id(e.away_team) == game.away_team
    ]
    if len(hits) == 1:
        return hits[0]
    in_window = sum(1 for e in events if lo <= e.commence_time <= hi)
    raise EventMatchError(
        game.game_id,
        len(hits),
        f"{len(events)} events listed, {in_window} within +-{window_h:g}h of tip "
        f"{game.tipoff_utc:%Y-%m-%d %H:%MZ}",
    )


# ----------------------------------------------------------------------------------------------
# Parsing per-event odds into flat rows
# ----------------------------------------------------------------------------------------------

ROW_SCHEMA: dict[str, Any] = {
    "source": pl.String,
    "season": pl.Int64,
    "game_id": pl.String,
    "event_id": pl.String,
    "snapshot_kind": pl.String,
    "requested_at": pl.Datetime("us"),
    "snapshot_ts": pl.Datetime("us"),
    "book": pl.String,
    "market": pl.String,
    "outcome_name": pl.String,
    "player_name": pl.String,
    "player_id": pl.Int64,
    "side": pl.String,
    "point": pl.Float64,
    "price_american": pl.Int64,
    "implied_prob_raw": pl.Float64,
    "implied_prob": pl.Float64,
    "last_update": pl.Datetime("us"),
    "raw_file": pl.String,
}

#: Dedupe key. The task's key omits ``player_name``; without it every player sharing a line in one
#: book/market would collapse into one row, so it is part of the key here.
DEDUPE_KEY = (
    "game_id",
    "snapshot_kind",
    "book",
    "market",
    "outcome_name",
    "player_name",
    "side",
    "point",
)


@dataclass
class ParseResult:
    rows: pl.DataFrame
    reasons: Counter[str] = field(default_factory=Counter)
    unresolved: Counter[str] = field(default_factory=Counter)  # distinct unresolved player names
    books: Counter[str] = field(default_factory=Counter)  # rows per book

    def reasons_line(self) -> str:
        return ", ".join(f"{k}={v}" for k, v in sorted(self.reasons.items())) or "none"


def _price(value: Any) -> int | None:
    """American price as an int; None for anything that is not one (|price| >= 100)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    if v != v or abs(v) < 100.0 or abs(v) == float("inf"):
        return None
    return int(round(v))


def _side(
    market: str, name: str, description: str | None, home: str, away: str
) -> tuple[str | None, str | None]:
    """``(side, player_name)``; side None means the outcome is not understood."""
    low = name.strip().lower()
    if market.startswith("player_"):
        if low in ("over", "under") and description:
            return low, description.strip()
        return None, None
    if low in ("over", "under"):
        return low, None
    if name.strip() and name.strip() == home:
        return "home", None
    if name.strip() and name.strip() == away:
        return "away", None
    return None, None


def parse_event_odds(
    body: Any,
    *,
    season: int,
    game_id: str,
    snapshot_kind: str,
    requested_at: datetime,
    raw_file: str,
    resolver: NameResolver,
) -> ParseResult:
    """Flatten one per-event historical odds response.

    ``implied_prob_raw`` is the vigged price; ``implied_prob`` is the proportional de-vig within a
    two-sided pair (same book, market, player and line) and NULL when both sides are not present
    exactly once. Unknown shapes are counted in ``reasons`` and skipped, never raised. Player names
    that miss the reviewed alias table keep ``player_id`` NULL and are counted in ``unresolved``.
    """
    reasons: Counter[str] = Counter()
    unresolved: Counter[str] = Counter()
    empty = ParseResult(pl.DataFrame(schema=ROW_SCHEMA), reasons, unresolved)
    if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
        reasons["empty_or_unknown_body"] += 1
        return empty
    snap = parse_ts(body.get("timestamp"))
    ev = body["data"]
    event_id = str(ev.get("id") or "")
    home, away = str(ev.get("home_team") or ""), str(ev.get("away_team") or "")
    books = ev.get("bookmakers")
    if not event_id or not isinstance(books, list) or not books:
        reasons["no_bookmakers"] += 1
        return empty
    req = requested_at.astimezone(UTC).replace(tzinfo=None) if requested_at.tzinfo else requested_at
    if snap is not None and snap > req:
        reasons["snapshot_after_request"] += 1  # leak flag: should never happen
    out: list[dict[str, Any]] = []
    for b in books:
        if not isinstance(b, dict) or not isinstance(b.get("markets"), list):
            reasons["unknown_book_shape"] += 1
            continue
        bkey = str(b.get("key") or "")
        if not bkey:
            reasons["missing_book_key"] += 1
            continue
        for m in b["markets"]:
            if not isinstance(m, dict) or not isinstance(m.get("outcomes"), list):
                reasons["unknown_market_shape"] += 1
                continue
            mkey = str(m.get("key") or "")
            upd = parse_ts(m.get("last_update")) or parse_ts(b.get("last_update"))
            for o in m["outcomes"]:
                if not isinstance(o, dict):
                    reasons["unknown_outcome_shape"] += 1
                    continue
                name = str(o.get("name") or "")
                price = _price(o.get("price"))
                if not mkey or not name or price is None:
                    reasons["bad_outcome"] += 1
                    continue
                desc = o.get("description")
                side, player = _side(mkey, name, str(desc) if desc else None, home, away)
                if side is None:
                    reasons["unparsed_outcome"] += 1
                    continue
                point: float | None = None
                if o.get("point") is not None:
                    try:
                        point = float(o["point"])
                    except (TypeError, ValueError):
                        reasons["bad_point"] += 1
                        continue
                pid = resolver.player_id(player) if player else None
                if player and pid is None:
                    reasons["unresolved_player"] += 1
                    unresolved[player] += 1
                out.append(
                    {
                        "source": SOURCE,
                        "season": season,
                        "game_id": game_id,
                        "event_id": event_id,
                        "snapshot_kind": snapshot_kind,
                        "requested_at": req,
                        "snapshot_ts": snap,
                        "book": bkey,
                        "market": mkey,
                        "outcome_name": name,
                        "player_name": player,
                        "player_id": pid,
                        "side": side,
                        "point": point,
                        "price_american": price,
                        "implied_prob_raw": american_to_prob(price),
                        "implied_prob": None,
                        "last_update": upd,
                        "raw_file": raw_file,
                    }
                )
    _devig(out)
    out = _dedupe(out, reasons)
    frame = pl.DataFrame(out, schema=ROW_SCHEMA) if out else pl.DataFrame(schema=ROW_SCHEMA)
    books_c: Counter[str] = Counter(r["book"] for r in out)
    return ParseResult(frame, reasons, unresolved, books_c)


def _devig(rows: list[dict[str, Any]]) -> None:
    """Fill ``implied_prob`` in place for every pair that has both sides exactly once."""
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for r in rows:
        pt = r["point"]
        if r["market"] == "spreads" and pt is not None:
            pt = abs(pt)  # home -6 pairs with away +6
        groups.setdefault((r["book"], r["market"], r["player_name"], pt), []).append(r)
    for grp in groups.values():
        if len(grp) != 2:
            continue
        sides = {g["side"] for g in grp}
        if sides not in ({"over", "under"}, {"home", "away"}):
            continue
        a, b = grp
        p = devig_two_way(a["implied_prob_raw"], b["implied_prob_raw"])
        if p is None:
            continue
        a["implied_prob"] = p
        b["implied_prob"] = 1.0 - p


def _dedupe(rows: list[dict[str, Any]], reasons: Counter[str]) -> list[dict[str, Any]]:
    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in rows:
        k = tuple(r[c] for c in DEDUPE_KEY)
        if k in seen:
            reasons["duplicate_row_dropped"] += 1
        seen[k] = r
    return list(seen.values())
