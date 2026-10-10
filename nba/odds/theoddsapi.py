"""Read-only client and parsers for theoddsapi.com (``https://api.theoddsapi.com``).

Guarantees
----------
* Read-only: the only paths this module can call are ``/me/``, ``/odds/``, ``/props/`` (GET).
  There is no order, account-mutation or portfolio path, and there never will be.
* The key comes from ``os.environ["ODDS_API_KEY"]`` only, is sent in the ``x-api-key`` header (never
  a query parameter), and is held in a wrapper whose ``repr``/``str`` is ``<secret>``. It never
  appears in logs, exceptions, raw cache files or this client's ``repr``.
* Raw first: every 200 response is written (gzip JSON, atomically) to
  ``data/odds/raw/theoddsapi/<YYYY-MM-DD>/<endpoint>_<params-hash>_<utc-ts>.json.gz`` BEFORE it is
  parsed, so any parser change is replayable from disk. A response for the same (endpoint, params)
  fetched less than ``reuse_window_s`` ago is reused instead of re-requested (resumable reruns).
* Budget: a per-UTC-day request ledger (every HTTP attempt counts) refuses past
  ``daily_budget`` (our own ceiling, below the plan's cap) and when the server-reported
  ``X-RateLimit-Remaining`` falls below ``reserve_remaining``.
* An out-of-season or empty cache is ``success: false`` with ``data: null`` and a message. That is
  a normal "no data" outcome, not an error.

Parsers turn ``/odds/`` and ``/props/`` payloads (and the flat ``/historical/odds`` row shape) into
flat polars rows. Unknown shapes are counted in ``reasons`` and skipped, never raised. Names that do
not resolve to our ids stay NULL (never guessed) and are counted in ``reasons`` and ``unresolved``.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from nba.markets.implied import devig_two_way

log = logging.getLogger("nba.odds")

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "odds.yaml"
TEAM_MARKETS = ROOT / "configs" / "team_markets.yaml"
SOURCE = "theoddsapi"
KEY_ENV = "ODDS_API_KEY"
KEY_HEADER = "x-api-key"
#: The only paths this client can call (all GET, all read-only).
ENDPOINT_PATHS = {"me": "/me/", "odds": "/odds/", "props": "/props/"}


class OddsError(Exception):
    """Base class; messages never contain the API key."""


class OddsKeyMissingError(OddsError):
    """``ODDS_API_KEY`` is unset or empty."""


class OddsBudgetExceeded(OddsError):
    """Our daily ceiling, the server-reported remaining quota, or an HTTP 429 stopped the call."""


class OddsUnreachable(OddsError):
    """Transport failure or 5xx after the bounded retries."""


class OddsHTTPError(OddsError):
    """A non-retriable 4xx (bad key, plan, parameter) or a non-JSON 200."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status


@dataclass(frozen=True)
class OddsConfig:
    base_url: str = "https://api.theoddsapi.com"
    sport_key: str = "basketball_nba"
    regions: str = "us"
    timeout_s: float = 20.0
    max_attempts: int = 3
    retry_backoff_s: float = 2.0
    min_interval_s: float = 0.5
    daily_budget: int = 2000
    reserve_remaining: int = 50
    game_markets: tuple[str, ...] = ("h2h", "spreads", "totals")
    prop_markets: tuple[str, ...] = (
        "player_points",
        "player_rebounds",
        "player_assists",
        "player_threes",
    )
    reuse_window_s: float = 60.0
    raw_dir: Path = ROOT / "data" / "odds" / "raw" / "theoddsapi"
    budget_dir: Path = ROOT / "data" / "odds" / "budget"
    out_db: Path = ROOT / "data" / "markets" / "market_asof.duckdb"


def _repo_path(value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def load_config(path: Path | None = None) -> OddsConfig:
    """``configs/odds.yaml`` merged over the defaults; unknown keys are ignored."""
    p = path or CONFIG_PATH
    raw: dict[str, Any] = {}
    if p.exists():
        loaded = yaml.safe_load(p.read_text()) or {}
        if isinstance(loaded, dict):
            raw = loaded
    base = OddsConfig()
    out = base
    simple = (
        *("base_url", "sport_key", "regions", "timeout_s", "max_attempts", "retry_backoff_s"),
        *("min_interval_s", "daily_budget", "reserve_remaining", "reuse_window_s"),
    )
    updates: dict[str, Any] = {k: type(getattr(base, k))(raw[k]) for k in simple if k in raw}
    for k in ("game_markets", "prop_markets"):
        if k in raw:
            updates[k] = tuple(str(x) for x in raw[k])
    for k in ("raw_dir", "budget_dir", "out_db"):
        if k in raw:
            updates[k] = _repo_path(raw[k])
    if updates:
        out = replace(base, **updates)
    return out


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
        raise OddsKeyMissingError(
            f"{KEY_ENV} is not set; export it (for example `uv run --env-file .env ...`). "
            "The key is read from the environment only."
        )
    return _Secret(value)


# ----------------------------------------------------------------------------------------------
# Budget ledger
# ----------------------------------------------------------------------------------------------


def utc_day(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%d")


class BudgetLedger:
    """Per-UTC-day request counter plus the last server-reported remaining quota."""

    def __init__(self, directory: Path, ceiling: int, reserve_remaining: int = 0) -> None:
        self.directory = directory
        self.ceiling = ceiling
        self.reserve_remaining = reserve_remaining

    def _path(self, day: str) -> Path:
        return self.directory / f"{day}.json"

    def _load(self, day: str) -> dict[str, Any]:
        p = self._path(day)
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            return {"used": 0, "server_remaining": None}
        return data if isinstance(data, dict) else {"used": 0, "server_remaining": None}

    def _save(self, day: str, data: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self._path(day).with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, self._path(day))

    def used(self, now: datetime) -> int:
        return int(self._load(utc_day(now)).get("used") or 0)

    def server_remaining(self, now: datetime) -> int | None:
        v = self._load(utc_day(now)).get("server_remaining")
        return int(v) if v is not None else None

    def charge(self, now: datetime) -> int:
        """Count one HTTP attempt; raises :class:`OddsBudgetExceeded` instead of exceeding."""
        day = utc_day(now)
        data = self._load(day)
        used = int(data.get("used") or 0)
        if used >= self.ceiling:
            raise OddsBudgetExceeded(
                f"daily request budget exhausted: {used}/{self.ceiling} used for {day} (UTC)"
            )
        rem = data.get("server_remaining")
        if rem is not None and int(rem) < self.reserve_remaining:
            raise OddsBudgetExceeded(
                f"server reports {int(rem)} requests remaining (< reserve {self.reserve_remaining})"
            )
        data["used"] = used + 1
        self._save(day, data)
        return used + 1

    def note_remaining(self, now: datetime, remaining: int) -> None:
        day = utc_day(now)
        data = self._load(day)
        data["server_remaining"] = int(remaining)
        self._save(day, data)


# ----------------------------------------------------------------------------------------------
# Client
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RawResponse:
    endpoint: str
    status: int
    body: Any
    fetched_at: datetime  # aware UTC, our clock (the file's fetch time when reused)
    headers: dict[str, str]
    path: Path | None
    from_cache: bool


def params_hash(params: Mapping[str, Any]) -> str:
    blob = json.dumps({k: str(v) for k, v in sorted(params.items())}, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:10]


def _ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _ts_from_name(name: str) -> datetime | None:
    m = re.search(r"_(\d{8}T\d{6}Z)\.json\.gz$", name)
    if m is None:
        return None
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


class TheOddsApiClient:
    """GET-only client. ``repr`` shows no secret."""

    def __init__(
        self,
        config: OddsConfig | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config or load_config()
        self._key = read_key(environ)  # fails here, before any I/O
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self.ledger = BudgetLedger(
            self.config.budget_dir, self.config.daily_budget, self.config.reserve_remaining
        )
        self._last_call: float | None = None
        self.requests_made = 0  # HTTP attempts by this instance

    def __repr__(self) -> str:
        return (
            f"TheOddsApiClient(base_url={self.config.base_url!r}, "
            f"daily_budget={self.config.daily_budget}, key=<secret>)"
        )

    def now(self) -> datetime:
        return self._clock()

    def _scrub(self, text: str) -> str:
        return text.replace(self._key.reveal(), "<secret>")

    # -- cache ---------------------------------------------------------------------------------
    def _day_dir(self, now: datetime) -> Path:
        return self.config.raw_dir / utc_day(now)

    def _find_recent(self, endpoint: str, phash: str, now: datetime) -> Path | None:
        d = self._day_dir(now)
        if self.config.reuse_window_s <= 0 or not d.exists():
            return None
        best: tuple[datetime, Path] | None = None
        for f in d.glob(f"{endpoint}_{phash}_*.json.gz"):
            ts = _ts_from_name(f.name)
            if ts is None or ts > now:
                continue
            if (now - ts).total_seconds() <= self.config.reuse_window_s and (
                best is None or ts > best[0]
            ):
                best = (ts, f)
        return best[1] if best else None

    @staticmethod
    def load_raw(path: Path) -> RawResponse:
        """Read a cached raw file back (the replay path)."""
        with gzip.open(path, "rt") as fh:
            blob = json.load(fh)
        fetched = datetime.fromisoformat(str(blob["fetched_at"]))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=UTC)
        return RawResponse(
            endpoint=str(blob.get("endpoint", "")),
            status=int(blob.get("status", 200)),
            body=blob.get("body"),
            fetched_at=fetched.astimezone(UTC),
            headers={str(k): str(v) for k, v in (blob.get("headers") or {}).items()},
            path=path,
            from_cache=True,
        )

    def _write_raw(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        phash: str,
        status: int,
        headers: dict[str, str],
        body: Any,
        fetched: datetime,
    ) -> Path:
        d = self._day_dir(fetched)
        d.mkdir(parents=True, exist_ok=True)
        final = d / f"{endpoint}_{phash}_{_ts(fetched)}.json.gz"
        tmp = final.with_name(final.name + ".tmp")
        blob = {
            "endpoint": endpoint,
            "path": ENDPOINT_PATHS[endpoint],
            "params": {k: str(v) for k, v in params.items()},
            "status": status,
            "headers": headers,
            "fetched_at": fetched.astimezone(UTC).isoformat(),
            "body": body,
        }
        with gzip.open(tmp, "wt") as fh:
            json.dump(blob, fh)
        os.replace(tmp, final)
        return final

    # -- HTTP ----------------------------------------------------------------------------------
    def _throttle(self) -> None:
        if self._last_call is not None:
            wait = self.config.min_interval_s - (time.monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = time.monotonic()

    def get(
        self, endpoint: str, params: Mapping[str, Any] | None = None, *, reuse: bool = True
    ) -> RawResponse:
        """One GET to ``endpoint`` in ``ENDPOINT_PATHS`` (cache first, then bounded retries)."""
        import httpx  # lazy: parsing/tests stay importable without touching the network layer

        if endpoint not in ENDPOINT_PATHS:
            raise ValueError(f"unknown endpoint {endpoint!r}")
        q = {k: v for k, v in (params or {}).items() if v is not None}
        phash = params_hash(q)
        now = self.now()
        if reuse:
            hit = self._find_recent(endpoint, phash, now)
            if hit is not None:
                log.info("odds cache hit: %s", hit.name)
                return self.load_raw(hit)
        url = self.config.base_url.rstrip("/") + ENDPOINT_PATHS[endpoint]
        last_err = "no attempt made"
        for attempt in range(max(1, self.config.max_attempts)):
            self.ledger.charge(self.now())
            self.requests_made += 1
            self._throttle()
            try:
                resp = httpx.get(
                    url,
                    params=q,
                    headers={KEY_HEADER: self._key.reveal()},
                    timeout=self.config.timeout_s,
                )
            except httpx.TransportError as exc:
                last_err = f"transport error {type(exc).__name__}"
                log.warning("odds %s attempt %d: %s", endpoint, attempt + 1, last_err)
                self._backoff(attempt)
                continue
            rl = {
                k.lower(): v for k, v in resp.headers.items() if k.lower().startswith("x-ratelimit")
            }
            self._note_headers(rl)
            if resp.status_code == 429:
                raise OddsBudgetExceeded("server returned HTTP 429 (quota reached)")
            if resp.status_code >= 500:
                last_err = f"HTTP {resp.status_code}"
                log.warning("odds %s attempt %d: %s", endpoint, attempt + 1, last_err)
                self._backoff(attempt)
                continue
            if resp.status_code != 200:
                raise OddsHTTPError(resp.status_code, self._scrub(resp.text[:200]))
            try:
                body = resp.json()
            except ValueError as exc:
                raise OddsHTTPError(200, "response is not JSON") from exc
            fetched = self.now().astimezone(UTC)
            path = self._write_raw(endpoint, q, phash, 200, rl, body, fetched)  # raw before parse
            return RawResponse(endpoint, 200, body, fetched, rl, path, False)
        raise OddsUnreachable(f"{endpoint}: {last_err} after {self.config.max_attempts} attempts")

    def _backoff(self, attempt: int) -> None:
        if attempt < self.config.max_attempts - 1:
            self._sleep(self.config.retry_backoff_s * (attempt + 1))

    def _note_headers(self, rl: Mapping[str, str]) -> None:
        raw = rl.get("x-ratelimit-remaining")
        if raw is None:
            return
        try:
            self.ledger.note_remaining(self.now(), int(raw))
        except ValueError:
            log.warning("odds: unparseable X-RateLimit-Remaining header")

    # -- endpoints -----------------------------------------------------------------------------
    def me(self) -> RawResponse:
        return self.get("me", reuse=False)

    def odds(
        self,
        markets: Iterable[str] | None = None,
        *,
        commence_from: str | None = None,
        commence_to: str | None = None,
        odds_format: str = "american",
    ) -> RawResponse:
        return self.get(
            "odds",
            {
                "sport_key": self.config.sport_key,
                "markets": ",".join(markets or self.config.game_markets),
                "regions": self.config.regions,
                "oddsFormat": odds_format,
                "commenceTimeFrom": commence_from,
                "commenceTimeTo": commence_to,
            },
        )

    def props(
        self, markets: Iterable[str] | None = None, *, event_id: str | None = None
    ) -> RawResponse:
        return self.get(
            "props",
            {
                "sport_key": self.config.sport_key,
                "markets": ",".join(markets or self.config.prop_markets),
                "regions": self.config.regions,
                "event_id": event_id,
            },
        )


# ----------------------------------------------------------------------------------------------
# Envelope
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Envelope:
    success: bool
    data: Any
    message: str | None
    meta: dict[str, Any]

    @property
    def empty(self) -> bool:
        """Out-of-season / empty cache (``success: false``, ``data: null``) or no events."""
        return (not self.success) or self.data in (None, [], {})


def read_envelope(body: Any) -> Envelope:
    if not isinstance(body, dict):
        return Envelope(False, None, "unexpected non-object body", {})
    meta = body.get("meta")
    msg = body.get("message")
    return Envelope(
        bool(body.get("success")),
        body.get("data"),
        str(msg) if msg is not None else None,
        meta if isinstance(meta, dict) else {},
    )


# ----------------------------------------------------------------------------------------------
# Name resolution (reuses the reviewed Kalshi alias table and the franchise names; never guesses)
# ----------------------------------------------------------------------------------------------


def _norm_team(name: str) -> str:
    return " ".join(name.lower().replace(".", " ").split())


@dataclass
class NameResolver:
    teams: dict[str, int] = field(default_factory=dict)  # normalized franchise name -> team_id
    players: dict[str, int] = field(default_factory=dict)  # normalized name -> player_id

    @classmethod
    def default(cls) -> NameResolver:
        from nba.kalshi.aliases import load_reviewed_aliases

        teams: dict[str, int] = {}
        raw = yaml.safe_load(TEAM_MARKETS.read_text())
        for tid, meta in raw["teams"].items():
            nm = _norm_team(str(meta["name"]))
            teams[nm] = int(tid)
            if nm.startswith("los angeles "):  # vendors write "LA Clippers"
                teams["la " + nm[len("los angeles ") :]] = int(tid)
        return cls(teams=teams, players=load_reviewed_aliases())

    def team_id(self, name: str | None) -> int | None:
        return self.teams.get(_norm_team(name)) if name else None

    def player_id(self, name: str | None) -> int | None:
        """Exact match on the reviewed alias table only (no fuzzy matching: unresolved is NULL)."""
        if not name:
            return None
        from nba.parse.availability import normalize_name

        return self.players.get(normalize_name(name))


# ----------------------------------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------------------------------

ROW_SCHEMA: dict[str, Any] = {
    "source": pl.String,
    "captured_at": pl.Datetime("us"),
    "source_updated_at": pl.Datetime("us"),
    "book": pl.String,
    "event_id": pl.String,
    "home_team": pl.String,
    "away_team": pl.String,
    "home_team_id": pl.Int64,
    "away_team_id": pl.Int64,
    "start_time": pl.Datetime("us"),
    "market": pl.String,
    "outcome_name": pl.String,
    "player_name": pl.String,
    "player_id": pl.Int64,
    "side": pl.String,
    "outcome_team_id": pl.Int64,
    "point": pl.Float64,
    "price_american": pl.Int64,
    "implied_prob_raw": pl.Float64,
    "implied_prob": pl.Float64,
}
_PROP_RE = re.compile(r"^(?P<player>.+?)\s+(?P<side>over|under)$", re.IGNORECASE)


@dataclass
class ParseResult:
    rows: pl.DataFrame
    reasons: Counter[str] = field(default_factory=Counter)
    unresolved: Counter[str] = field(default_factory=Counter)  # distinct unresolved names

    def reasons_line(self) -> str:
        return ", ".join(f"{k}={v}" for k, v in sorted(self.reasons.items())) or "none"


def american_to_prob(price: float) -> float:
    return -price / (-price + 100.0) if price < 0 else 100.0 / (price + 100.0)


def to_american(value: Any) -> tuple[int | None, bool]:
    """``(american, was_decimal)``; ``None`` when the value is not a usable price.

    American odds never lie strictly inside (-100, 100), so a number in (1, 100) is unambiguously a
    decimal price and is converted; anything else unusable returns ``None``.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, False
    v = float(value)
    if v != v or v in (float("inf"), float("-inf")):
        return None, False
    if abs(v) >= 100.0:
        return int(round(v)), False
    if 1.0 < v < 100.0:
        return (int(round((v - 1.0) * 100.0)) if v >= 2.0 else int(round(-100.0 / (v - 1.0)))), True
    return None, False


def _utc_naive(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _s(value: Any) -> str | None:
    return str(value) if value is not None and value != "" else None


def _iter_records(data: Any, reasons: Counter[str]) -> Iterable[dict[str, Any]]:
    """Flatten the supported payload shapes into raw per-outcome records."""
    items: list[Any]
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = [data]
    else:
        reasons["unknown_data_shape"] += 1
        return
    for ev in items:
        if not isinstance(ev, dict):
            reasons["unknown_event_shape"] += 1
            continue
        if "outcome_name" in ev:  # flat row (historical shape)
            yield {
                "event_id": ev.get("event_id") or ev.get("id"),
                "home": ev.get("home_team"),
                "away": ev.get("away_team"),
                "start": ev.get("start_time") or ev.get("commence_time"),
                "book": ev.get("book") or ev.get("bookmaker"),
                "market": ev.get("market"),
                "name": ev.get("outcome_name"),
                "description": ev.get("description"),
                "price": ev.get("price"),
                "point": ev.get("point"),
                "updated": ev.get("captured_at") or ev.get("last_update") or ev.get("updated_at"),
            }
            continue
        head = {
            "event_id": ev.get("event_id") or ev.get("id"),
            "home": ev.get("home_team"),
            "away": ev.get("away_team"),
            "start": ev.get("start_time") or ev.get("commence_time"),
        }
        if isinstance(ev.get("books"), list):  # documented live shape
            for b in ev["books"]:
                if not isinstance(b, dict) or not isinstance(b.get("outcomes"), list):
                    reasons["unknown_book_shape"] += 1
                    continue
                for o in b["outcomes"]:
                    if not isinstance(o, dict):
                        reasons["unknown_outcome_shape"] += 1
                        continue
                    yield {
                        **head,
                        "book": b.get("book") or b.get("key"),
                        "market": b.get("market"),
                        "name": o.get("name"),
                        "description": o.get("description"),
                        "price": o.get("price"),
                        "point": o.get("point"),
                        "updated": b.get("updated_at") or b.get("last_update"),
                    }
        elif isinstance(ev.get("bookmakers"), list):  # the-odds-api-style alternative
            for b in ev["bookmakers"]:
                if not isinstance(b, dict) or not isinstance(b.get("markets"), list):
                    reasons["unknown_book_shape"] += 1
                    continue
                for m in b["markets"]:
                    if not isinstance(m, dict) or not isinstance(m.get("outcomes"), list):
                        reasons["unknown_market_shape"] += 1
                        continue
                    for o in m["outcomes"]:
                        if not isinstance(o, dict):
                            reasons["unknown_outcome_shape"] += 1
                            continue
                        yield {
                            **head,
                            "book": b.get("key") or b.get("book"),
                            "market": m.get("key") or m.get("market"),
                            "name": o.get("name"),
                            "description": o.get("description"),
                            "price": o.get("price"),
                            "point": o.get("point"),
                            "updated": m.get("last_update") or b.get("last_update"),
                        }
        else:
            reasons["unknown_event_shape"] += 1


def _side_and_player(
    market: str, name: str, description: str | None, home: str | None, away: str | None
) -> tuple[str | None, str | None]:
    """``(side, player_name)``; side None means the outcome could not be parsed."""
    low = name.strip().lower()
    if market.startswith("player_"):
        if description and low in ("over", "under"):
            return low, description.strip()
        m = _PROP_RE.match(name.strip())
        if m is None:
            return None, None
        return m["side"].lower(), m["player"].strip()
    if low in ("over", "under"):
        return low, None
    if home and _norm_team(name) == _norm_team(home):
        return "home", None
    if away and _norm_team(name) == _norm_team(away):
        return "away", None
    return name.strip(), None


def parse_payload(
    body: Any, captured_at: datetime, resolver: NameResolver | None = None
) -> ParseResult:
    """Parse one ``/odds/`` or ``/props/`` response body (envelope included) into flat rows.

    ``captured_at`` is OUR clock at fetch time (aware or naive-UTC). The payload's own timestamp is
    kept as ``source_updated_at``. ``implied_prob_raw`` is the vigged price; ``implied_prob`` is
    the proportional de-vig within a two-sided market (same book, event, market, player, line and
    snapshot) and NULL when the market does not have exactly both sides.
    """
    reasons: Counter[str] = Counter()
    unresolved: Counter[str] = Counter()
    env = read_envelope(body)
    cap = captured_at.astimezone(UTC).replace(tzinfo=None) if captured_at.tzinfo else captured_at
    if env.empty:
        reasons["empty_feed"] += 1
        return ParseResult(pl.DataFrame(schema=ROW_SCHEMA), reasons, unresolved)
    res = resolver or NameResolver.default()
    out: list[dict[str, Any]] = []
    for rec in _iter_records(env.data, reasons):
        event_id, market = _s(rec["event_id"]), _s(rec["market"])
        book, name = _s(rec["book"]), _s(rec["name"])
        if event_id is None or market is None or book is None or name is None:
            reasons["missing_field"] += 1
            continue
        price, was_decimal = to_american(rec["price"])
        if price is None:
            reasons["bad_price"] += 1
            continue
        if was_decimal:
            reasons["decimal_price_converted"] += 1
        point: float | None = None
        if rec["point"] is not None:
            try:
                point = float(rec["point"])
            except (TypeError, ValueError):
                reasons["bad_point"] += 1
                continue
        home, away = _s(rec["home"]), _s(rec["away"])
        side, player = _side_and_player(market, name, _s(rec["description"]), home, away)
        if side is None:
            reasons["unparsed_prop_outcome"] += 1
            continue
        h_id, a_id = res.team_id(home), res.team_id(away)
        for nm, tid in ((home, h_id), (away, a_id)):
            if nm and tid is None:
                reasons["unresolved_team"] += 1
                unresolved[f"team:{nm}"] += 1
        o_team: int | None = None
        if side not in ("over", "under"):
            o_team = h_id if side == "home" else a_id if side == "away" else res.team_id(side)
        p_id = res.player_id(player)
        if player and p_id is None:
            reasons["unresolved_player"] += 1
            unresolved[f"player:{player}"] += 1
        out.append(
            {
                "source": SOURCE,
                "captured_at": cap,
                "source_updated_at": _utc_naive(rec["updated"]),
                "book": book,
                "event_id": event_id,
                "home_team": home,
                "away_team": away,
                "home_team_id": h_id,
                "away_team_id": a_id,
                "start_time": _utc_naive(rec["start"]),
                "market": market,
                "outcome_name": name,
                "player_name": player,
                "player_id": p_id,
                "side": side,
                "outcome_team_id": o_team,
                "point": point,
                "price_american": price,
                "implied_prob_raw": american_to_prob(price),
                "implied_prob": None,
            }
        )
    _devig(out)
    frame = pl.DataFrame(out, schema=ROW_SCHEMA) if out else pl.DataFrame(schema=ROW_SCHEMA)
    return ParseResult(frame, reasons, unresolved)


def _devig(rows: list[dict[str, Any]]) -> None:
    """Fill ``implied_prob`` in place for every two-sided group that has both sides exactly once."""
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for r in rows:
        pt = r["point"]
        if r["market"] == "spreads" and pt is not None:
            pt = abs(pt)  # home -3.5 pairs with away +3.5
        key = (r["book"], r["event_id"], r["market"], r["player_name"], pt, r["source_updated_at"])
        groups.setdefault(key, []).append(r)
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
