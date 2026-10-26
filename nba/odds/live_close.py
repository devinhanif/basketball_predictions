"""LIVE per-game odds capture from the-odds-api.com on a configured pre-tip schedule.

The schedule (``configs/odds_live.yaml: schedule``) is a list: a morning pull (first line of the
day), T-180 ... T-5, and the close. Each entry is one snapshot kind; the default in code is the
original pair (t60, close). Estimated credits: :func:`estimate_credits`.

Off unless ``NBA_CLOSE_CAPTURE=1``. Devin, 2026-10-11: the live season's benchmark is the CLOSING
line. Read-only (GET only), the key comes from ``THE_ODDS_API_KEY`` and is never stored or logged.
See docs/CLOSING_LINE_CAPTURE.md.

One call is one "tick" (the lineups job runs every 5 minutes): for every game of the ET slate that
has a REAL scheduled tip (``game_tips`` in the lineups DB) inside the kind's window and no stored
snapshot of that kind yet, pull ``/sports/basketball_nba/events/{id}/odds`` once for all game and
prop markets and write the parsed rows to ``live_book_odds`` in ``data/markets/market_asof.duckdb``.

Known-at discipline: the clock is read before the request (``requested_at``) and after the response
(``received_at``). A row is written only if ``received_at`` is strictly before BOTH our scheduled
tip and the vendor's ``commence_time``; otherwise the pull is logged ``post_tip_refused`` and
nothing is stored. The raw response is cached (gzipped, key-free) before it is parsed.

Credits: ``/events`` is free; ``/events/{id}/odds`` costs markets x regions (1 credit each). A daily
ledger refuses any call that would pass ``daily_max_credits`` or the server floor.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import polars as pl
import yaml

from nba.daily.schedule import is_scorable_game_id
from nba.odds.the_odds_api import (
    AuthError,
    CreditBudgetExceeded,
    CreditLedger,
    EventRef,
    HistoricalClient,
    HistoryConfig,
    HistoryError,
    _int_header,
    default_resolver,
    iso_z,
    parse_event_odds,
    parse_events,
    redact,
)
from nba.odds.the_odds_api import load_config as load_history_config
from nba.odds.theoddsapi import NameResolver

log = logging.getLogger("nba.odds.live")
ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "odds_live.yaml"
FLAG_ENV = "NBA_CLOSE_CAPTURE"
SOURCE = "the_odds_api_live"
_KIND_RE = re.compile(r"^(close|morning|t[0-9]{1,4})$")


def valid_kind(kind: str) -> bool:
    """Snapshot kinds: ``close``, ``morning`` or ``t<minutes before tip>`` (t60, t180, ...)."""
    return bool(_KIND_RE.match(kind))


DDL = """
CREATE TABLE IF NOT EXISTS live_book_odds (
    source VARCHAR,
    game_id VARCHAR,
    slate_date DATE,              -- ET date of the slate the capture was requested for
    snapshot_kind VARCHAR,        -- morning | t180 .. t5 | close (configs/odds_live.yaml schedule)
    requested_at TIMESTAMP,       -- our clock before the request (naive UTC)
    received_at TIMESTAMP,        -- our clock after the response (naive UTC); always < tipoff
    tipoff TIMESTAMP,             -- our real scheduled tip (naive UTC)
    commence_time TIMESTAMP,      -- the vendor's commence_time (naive UTC)
    event_id VARCHAR,
    book VARCHAR,
    market VARCHAR,
    outcome_name VARCHAR,
    player_name VARCHAR,
    player_id INT,                -- NULL when the name did not resolve (never guessed)
    side VARCHAR,                 -- over | under | home | away
    point DOUBLE,
    price_american INT,
    implied_prob_raw DOUBLE,      -- vigged
    implied_prob DOUBLE,          -- proportional de-vig within a two-sided pair, else NULL
    last_update TIMESTAMP,        -- the book's own stamp when given
    raw_file VARCHAR
);
CREATE TABLE IF NOT EXISTS live_capture_log (
    game_id VARCHAR,
    snapshot_kind VARCHAR,
    requested_at TIMESTAMP,
    received_at TIMESTAMP,
    tipoff TIMESTAMP,
    event_id VARCHAR,
    status VARCHAR,               -- ok|empty|post_tip_refused|unmatched|error|credit_guard
    n_rows INT,
    credits INT,
    note VARCHAR
);
"""
_DONE_STATUSES = ("ok", "empty", "post_tip_refused")
_ROW_COLS = [
    "source", "game_id", "slate_date", "snapshot_kind", "requested_at", "received_at", "tipoff",
    "commence_time", "event_id", "book", "market", "outcome_name", "player_name", "player_id",
    "side", "point", "price_american", "implied_prob_raw", "implied_prob", "last_update",
    "raw_file",
]  # fmt: skip


class PostTipError(RuntimeError):
    """A write was attempted at or after tip (ours or the vendor's). Never worked around."""


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return (env.get(FLAG_ENV) or "").strip() == "1"


# ----------------------------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ScheduleEntry:
    """One capture point. Relative: pull when ``lo <= minutes to tip <= hi``. Morning: pull once the
    ET wall clock is in ``[at_et, at_et + grace_min)`` of the slate date (first line of the day)."""

    kind: str
    lo: float = 0.0
    hi: float = 0.0
    at_et: dtime | None = None
    grace_min: int = 60
    extended: bool = False  # gated off when the estimate exceeds the plan budget rule

    def __post_init__(self) -> None:
        if not valid_kind(self.kind):
            raise ValueError(f"bad snapshot kind {self.kind!r} (morning|t<min>|close)")
        if self.at_et is None and not (0 <= self.lo <= self.hi):
            raise ValueError(f"{self.kind}: need 0 <= lo <= hi, got [{self.lo}, {self.hi}]")


DEFAULT_SCHEDULE: tuple[ScheduleEntry, ...] = (
    ScheduleEntry("close", 0, 5),
    ScheduleEntry("t60", 55, 65),
)


@dataclass(frozen=True)
class PlanBudget:
    """The vendor plan as recorded in the repo (docs/DECISIONS.md 2026-10-10; the-odds-api.com)."""

    name: str = "START 5M"
    monthly_credits: int = 5_000_000
    price_usd_month: int = 119
    games_per_month: int = 215  # peak regular-season month (about 1,230 games over 6 months)
    max_plan_fraction: float = 0.5  # extended entries stay ON only while the estimate fits this


@dataclass(frozen=True)
class MarketGroup:
    """A switchable set of market keys (``configs/odds_live.yaml: market_groups``).

    ``alternate`` groups are the first thing dropped when the full set would not fit the plan's
    budget rule (:meth:`LiveConfig.markets`); ``enabled: false`` switches a group off by hand.
    """

    name: str
    markets: tuple[str, ...]
    enabled: bool = True
    alternate: bool = False


@dataclass(frozen=True)
class LiveConfig:
    hist: HistoryConfig
    regions: tuple[str, ...] = ("us", "eu")
    cost_per_market_region: int = 1
    daily_max_credits: int = 600
    min_server_remaining: int = 100_000
    schedule: tuple[ScheduleEntry, ...] = DEFAULT_SCHEDULE
    plan: PlanBudget = field(default_factory=PlanBudget)
    market_groups: tuple[MarketGroup, ...] = ()  # empty = the odds_history game + prop markets
    match_window_min: float = 30.0
    max_attempts_per_game: int = 2
    out_db: Path = ROOT / "data" / "markets" / "market_asof.duckdb"
    tips_db: Path = ROOT / "data" / "lineups" / "lineups.duckdb"
    games_db: Path = ROOT / "nba.duckdb"  # read-only; official schedule teams for pre-lineup games
    raw_dir: Path = ROOT / "data" / "odds" / "raw" / "the_odds_api_live"
    ledger_dir: Path = ROOT / "data" / "odds" / "budget"

    def _union(self, groups: tuple[MarketGroup, ...]) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for g in groups:
            for m in g.markets:
                seen.setdefault(m)
        return tuple(seen)

    @property
    def enabled_markets(self) -> tuple[str, ...]:
        """Markets of every enabled group (de-duplicated, config order); before the budget rule."""
        if not self.market_groups:
            return (*self.hist.game_markets, *self.hist.prop_markets)
        return self._union(tuple(g for g in self.market_groups if g.enabled))

    @property
    def markets(self) -> tuple[str, ...]:
        """Markets requested per call. The enabled set, unless the FULL schedule over that set
        exceeds ``plan.max_plan_fraction`` of the plan's monthly credits: then the ``alternate``
        groups are dropped first (before any schedule point is: see :meth:`active_schedule`)."""
        on = self.enabled_markets
        if not self.market_groups:
            return on
        cost = self.cost_per_market_region * len(self.regions)
        budget = self.plan.max_plan_fraction * self.plan.monthly_credits
        if self.plan.games_per_month * len(self.schedule) * len(on) * cost <= budget:
            return on
        return self._union(tuple(g for g in self.market_groups if g.enabled and not g.alternate))

    def dropped_groups(self) -> tuple[str, ...]:
        """Names of enabled groups the budget rule turned off (empty when everything fits)."""
        kept = set(self.markets)
        return tuple(g.name for g in self.market_groups if g.enabled and not set(g.markets) <= kept)

    def expected_cost(self) -> int:
        """Upper-bound credits for one per-event odds call (all markets x all regions)."""
        return self.cost_per_market_region * len(self.markets) * len(self.regions)

    def entry(self, kind: str) -> ScheduleEntry:
        for e in self.schedule:
            if e.kind == kind:
                return e
        raise ValueError(f"snapshot kind {kind!r} is not in the schedule")

    def active_schedule(self) -> tuple[ScheduleEntry, ...]:
        """The schedule that runs: all entries, or without the ``extended`` ones when the full
        schedule's monthly estimate exceeds ``plan.max_plan_fraction`` of the plan's credits."""
        if estimate_credits(self, self.plan.games_per_month, self.schedule) <= (
            self.plan.max_plan_fraction * self.plan.monthly_credits
        ):
            return self.schedule
        return tuple(e for e in self.schedule if not e.extended)


def estimate_credits(
    cfg: LiveConfig, games: int, schedule: tuple[ScheduleEntry, ...] | None = None
) -> int:
    """Upper-bound credits for ``games`` games: entries x per-call cost (``/events`` is free)."""
    entries = cfg.schedule if schedule is None else schedule
    return games * len(entries) * cfg.expected_cost()


def due_order(entries: tuple[ScheduleEntry, ...]) -> list[ScheduleEntry]:
    """Run order inside a tick: the close first (it cannot wait), then latest-to-earliest."""
    return sorted(entries, key=lambda e: (e.kind != "close", e.lo if e.at_et is None else 1e9))


def _p(value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


def _entry(x: Mapping[str, Any]) -> ScheduleEntry:
    at = x.get("at_et")
    t = dtime.fromisoformat(str(at)) if at is not None else None
    lo, hi = x.get("window", (0, 0))
    return ScheduleEntry(
        kind=str(x["kind"]),
        lo=float(lo),
        hi=float(hi),
        at_et=t,
        grace_min=int(x.get("grace_min", 60)),
        extended=bool(x.get("extended", False)),
    )


def load_live_config(path: Path | None = None, hist_path: Path | None = None) -> LiveConfig:
    """``configs/odds_live.yaml`` over the defaults; markets/base_url from odds_history.yaml."""
    p = path or CONFIG_PATH
    raw: dict[str, Any] = {}
    if p.exists():
        loaded = yaml.safe_load(p.read_text()) or {}
        raw = loaded if isinstance(loaded, dict) else {}
    cfg = LiveConfig(hist=load_history_config(hist_path))
    upd: dict[str, Any] = {}
    if "regions" in raw:
        upd["regions"] = tuple(str(x) for x in raw["regions"])
    for k in (
        "cost_per_market_region",
        "daily_max_credits",
        "min_server_remaining",
        "max_attempts_per_game",
    ):
        if k in raw:
            upd[k] = int(raw[k])
    if "match_window_min" in raw:
        upd["match_window_min"] = float(raw["match_window_min"])
    if "schedule" in raw:
        upd["schedule"] = tuple(_entry(x) for x in raw["schedule"])
    if "market_groups" in raw:
        upd["market_groups"] = tuple(
            MarketGroup(
                name=str(name),
                markets=tuple(str(m) for m in (spec or {}).get("markets", ())),
                enabled=bool((spec or {}).get("enabled", True)),
                alternate=bool((spec or {}).get("alternate", False)),
            )
            for name, spec in raw["market_groups"].items()
        )
    if "plan" in raw:
        pl_ = {k: v for k, v in raw["plan"].items() if k in PlanBudget.__dataclass_fields__}
        upd["plan"] = PlanBudget(**pl_)
    for k in ("out_db", "tips_db", "raw_dir", "ledger_dir"):
        if k in raw:
            upd[k] = _p(raw[k])
    return replace(cfg, **upd) if upd else cfg


# ----------------------------------------------------------------------------------------------
# Games and window selection
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveGame:
    game_id: str
    tipoff: datetime  # naive UTC, real scheduled tip
    home_team: int  # 0 when the lineups feed has not yet shown the game
    away_team: int


def naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt


def _opt_naive(dt: datetime | None) -> datetime | None:
    return naive_utc(dt) if dt is not None else None


def schedule_teams(games_db: Path | None, game_ids: list[str]) -> dict[str, tuple[int, int]]:
    """``{game_id: (home, away)}`` from the official schedule: ``games`` in the production DB
    (read-only; a lock or any error degrades to nothing), then the daily schedule cache."""
    out: dict[str, tuple[int, int]] = {}
    if games_db is not None and game_ids and games_db.exists():
        try:
            con = duckdb.connect(str(games_db), read_only=True)
            try:
                marks = ",".join("?" * len(game_ids))
                rows = con.execute(
                    f"SELECT game_id, home_team, away_team FROM games WHERE game_id IN ({marks})",
                    game_ids,
                ).fetchall()
            finally:
                con.close()
            out = {str(g): (int(h), int(a)) for g, h, a in rows if h and a}
        except duckdb.Error as exc:  # locked by a live writer, or no table: keep ids at 0
            log.warning("schedule team lookup unavailable (%s)", type(exc).__name__)
    missing = [g for g in game_ids if g not in out]
    if missing:
        try:
            from nba.daily.schedule import cached_game_teams

            seasons = {2000 + int(g[3:5]) for g in missing if len(g) >= 5 and g[3:5].isdigit()}
            cached = cached_game_teams(seasons)
            out.update({g: cached[g] for g in missing if g in cached})
        except Exception as exc:  # noqa: BLE001 - a cache problem never blocks capture
            log.warning("schedule cache lookup unavailable (%s)", type(exc).__name__)
    return out


def read_slate(tips_db: Path, slate: date, games_db: Path | None = None) -> list[LiveGame]:
    """Games of the ET ``slate`` with a real tip in ``game_tips`` (read-only; none if absent).

    Team ids come from ``lineup_snapshots``; where those are missing (before lineups post) and
    ``games_db`` is given, they are filled from the official schedule so the vendor match is
    team-based (30 min window), not tip-time-only (vendor tips sit ~10 min after ours)."""
    if not tips_db.exists():
        return []
    con = duckdb.connect(str(tips_db), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT t.game_id, t.tipoff,
                   max(CASE WHEN s.is_home THEN s.team_id END),
                   max(CASE WHEN NOT s.is_home THEN s.team_id END)
            FROM game_tips t LEFT JOIN lineup_snapshots s USING (game_id)
            WHERE t.game_date = ? AND t.tipoff IS NOT NULL
            GROUP BY t.game_id, t.tipoff ORDER BY t.tipoff, t.game_id
            """,
            [slate],
        ).fetchall()
    except duckdb.Error:  # tables not created yet
        return []
    finally:
        con.close()
    kept = [r for r in rows if is_scorable_game_id(str(r[0]))]  # no 001 preseason / 003 ASG
    need = [str(g) for g, _t, h, a in kept if not (h and a)]
    sched = schedule_teams(games_db, need) if need else {}
    out: list[LiveGame] = []
    for g, t, h, a in kept:
        hh, aa = (int(h), int(a)) if (h and a) else sched.get(str(g), (0, 0))
        out.append(LiveGame(str(g), naive_utc(t), hh, aa))
    return out


def _slate_day(tip: datetime) -> date:
    """ET slate date of a tip: tips before 06:00 ET belong to the previous day's slate."""
    t = tip.replace(tzinfo=UTC).astimezone(ET)
    return t.date() - timedelta(days=1) if t.hour < 6 else t.date()


def select_window(
    games: list[LiveGame], now: datetime, kind: str, cfg: LiveConfig, slate: date | None = None
) -> list[LiveGame]:
    """Games due for ``kind`` at ``now`` (never one that has tipped: ``tip > now`` always).

    Relative entries: ``now + lo <= tip <= now + hi`` minutes. ``morning``: the ET clock is within
    ``[at_et, at_et + grace_min)`` of the slate day (the slate date is the games' own ET date).
    """
    e = cfg.entry(kind)
    n = naive_utc(now)
    live = [g for g in games if g.tipoff > n]
    if e.at_et is None:
        return [
            g for g in live
            if n + timedelta(minutes=e.lo) <= g.tipoff <= n + timedelta(minutes=e.hi)
        ]  # fmt: skip
    now_et = n.replace(tzinfo=UTC).astimezone(ET)
    out = []
    for g in live:
        day = slate or _slate_day(g.tipoff)
        start = datetime.combine(day, e.at_et, tzinfo=ET)
        if start <= now_et < start + timedelta(minutes=e.grace_min):
            out.append(g)
    return out


# ----------------------------------------------------------------------------------------------
# Store
# ----------------------------------------------------------------------------------------------


def open_store(path: Path, retries: int = 6, wait_s: float = 5.0) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for _ in range(max(1, retries)):
        try:
            con = duckdb.connect(str(path))
            con.execute(DDL)
            return con
        except duckdb.IOException as exc:
            last = exc
            time.sleep(wait_s)
    assert last is not None
    raise last


def done_games(con: duckdb.DuckDBPyConnection, kind: str, max_attempts: int) -> set[str]:
    """Games that need no further pull of ``kind``: a stored snapshot, a terminal outcome, or the
    attempt cap reached by failures."""
    out = {
        str(r[0])
        for r in con.execute(
            "SELECT DISTINCT game_id FROM live_book_odds WHERE snapshot_kind = ?", [kind]
        ).fetchall()
    }
    ph = ", ".join("?" for _ in _DONE_STATUSES)
    out |= {
        str(r[0])
        for r in con.execute(
            "SELECT DISTINCT game_id FROM live_capture_log WHERE snapshot_kind = ? "  # noqa: S608
            f"AND status IN ({ph})",
            [kind, *_DONE_STATUSES],
        ).fetchall()
    }
    out |= {
        str(r[0])
        for r in con.execute(
            "SELECT game_id FROM live_capture_log WHERE snapshot_kind = ? "
            "GROUP BY game_id HAVING count(*) >= ?",
            [kind, max_attempts],
        ).fetchall()
    }
    return out


def write_snapshot(
    con: duckdb.DuckDBPyConnection,
    rows: pl.DataFrame,
    *,
    game: LiveGame,
    kind: str,
    slate: date,
    requested_at: datetime,
    received_at: datetime,
    commence_time: datetime,
    raw_file: str,
) -> int:
    """Append one snapshot. The only writer of ``live_book_odds``.

    Raises :class:`PostTipError` when ``received_at`` is not strictly before both tips. Idempotent:
    a (game, kind) that already has rows is left alone (returns 0).
    """
    if not valid_kind(kind):
        raise ValueError(f"unknown snapshot kind {kind!r}")
    recv = naive_utc(received_at)
    if recv >= game.tipoff or recv >= naive_utc(commence_time):
        raise PostTipError(
            f"{game.game_id} {kind}: received {recv:%H:%M:%S}Z is not before tip "
            f"{game.tipoff:%H:%M:%S}Z / commence {commence_time:%H:%M:%S}Z; nothing written"
        )
    if rows.height == 0:
        return 0
    frame = rows.with_columns(
        source=pl.lit(SOURCE),
        game_id=pl.lit(game.game_id),
        slate_date=pl.lit(slate, dtype=pl.Date),
        snapshot_kind=pl.lit(kind),
        requested_at=pl.lit(naive_utc(requested_at), dtype=pl.Datetime("us")),
        received_at=pl.lit(recv, dtype=pl.Datetime("us")),
        tipoff=pl.lit(game.tipoff, dtype=pl.Datetime("us")),
        commence_time=pl.lit(naive_utc(commence_time), dtype=pl.Datetime("us")),
        raw_file=pl.lit(raw_file),
    ).select(_ROW_COLS)
    con.execute("BEGIN")
    try:
        have = con.execute(
            "SELECT count(*) FROM live_book_odds WHERE game_id = ? AND snapshot_kind = ?",
            [game.game_id, kind],
        ).fetchone()
        if have is not None and int(have[0]) > 0:
            con.execute("ROLLBACK")
            return 0
        con.register("live_new", frame.to_arrow())
        try:
            con.execute("INSERT INTO live_book_odds SELECT * FROM live_new")
        finally:
            con.unregister("live_new")
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return frame.height


# ----------------------------------------------------------------------------------------------
# Client
# ----------------------------------------------------------------------------------------------


class LiveClient(HistoricalClient):
    """GET-only client for the two live endpoints; reuses the historical client's request loop
    (bounded retries, 429 backoff, key scrubbing) with a daily credit ledger."""

    def __init__(
        self,
        cfg: LiveConfig,
        *,
        environ: Mapping[str, str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        http_get: Callable[..., Any] | None = None,
        day: date | None = None,
    ) -> None:
        super().__init__(cfg.hist, environ=environ, sleep=sleep, http_get=http_get)
        self.live = cfg
        d = day or datetime.now(ET).date()
        self.ledger = CreditLedger(
            cfg.ledger_dir / f"the_odds_api_live_{d.isoformat()}.json",
            cfg.daily_max_credits,
            cfg.min_server_remaining,
        )

    def __repr__(self) -> str:
        return f"LiveClient(base_url={self.live.hist.base_url!r}, key=<secret>)"

    def key_text(self) -> str:
        return self._key.reveal() if self._key else ""

    def _book(self, endpoint: str, headers: Mapping[str, str], cost: int, **ctx: Any) -> int:
        paid = _int_header(headers, "x-requests-last")
        cost = paid if paid is not None else cost
        self.ledger.record(
            {
                "ts": datetime.now(UTC).isoformat(),
                "endpoint": endpoint,
                "cost": cost,
                "server_remaining": _int_header(headers, "x-requests-remaining"),
                "server_used": _int_header(headers, "x-requests-used"),
                **ctx,
            }
        )
        self.credits_spent += cost
        return cost

    def live_events(self, commence_from: datetime, commence_to: datetime) -> tuple[Any, int]:
        """Upcoming events in a commence-time range. Free (0 credits)."""
        params = {
            "dateFormat": "iso",
            "commenceTimeFrom": iso_z(commence_from),
            "commenceTimeTo": iso_z(commence_to),
        }
        body, headers, _ = self._request(f"/sports/{self.config.sport_key}/events", params, 0)
        return body, self._book("events", headers, 0)

    def live_event_odds(self, event_id: str) -> tuple[Any, int, dict[str, str]]:
        params = {
            "regions": ",".join(self.live.regions),
            "markets": ",".join(self.live.markets),
            "oddsFormat": self.config.odds_format,
            "dateFormat": "iso",
        }
        url = f"/sports/{self.config.sport_key}/events/{event_id}/odds"
        expected = self.live.expected_cost()
        body, headers, _ = self._request(url, params, expected)
        cost = self._book("event_odds", headers, expected, event_id=event_id)
        return body, cost, params  # params never contain the key


def write_raw(cfg: LiveConfig, key: str, game: LiveGame, kind: str, blob: dict[str, Any]) -> Path:
    """Gzip the raw response (raw before parse). Refuses to write a blob containing the key."""
    text = json.dumps(blob)
    if key and key in text:
        raise HistoryError("refusing to write a raw file that contains the API key")
    stamp = str(blob["requested_at"]).replace(":", "").replace("-", "")[:15]
    path = cfg.raw_dir / game.game_id / f"odds_{kind}_{stamp}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return path


# ----------------------------------------------------------------------------------------------
# Event matching and the run
# ----------------------------------------------------------------------------------------------


def match_live_event(
    events: list[EventRef], game: LiveGame, rivals: int, resolver: NameResolver, window_min: float
) -> EventRef | None:
    """The single vendor event for ``game``: teams match (when our team ids are known) and
    commence_time within ``window_min`` of our tip. Without team ids, only an event within 2 min
    of the tip counts, and only when no other due game shares that tip (``rivals`` == 1)."""
    known = bool(game.home_team and game.away_team)
    win = timedelta(minutes=window_min if known else 2)
    hits = [e for e in events if abs(e.commence_time - game.tipoff) <= win]
    if known:
        hits = [
            e
            for e in hits
            if resolver.team_id(e.home_team) == game.home_team
            and resolver.team_id(e.away_team) == game.away_team
        ]
    elif rivals != 1:
        return None
    return hits[0] if len(hits) == 1 else None


@dataclass
class LiveReport:
    slate: date
    kind: str
    disabled: bool = False
    dry_run: bool = False
    due: int = 0
    pulled: int = 0
    inserted: int = 0
    credits: int = 0
    statuses: Counter[str] = field(default_factory=Counter)
    messages: list[str] = field(default_factory=list)
    degraded: bool = False  # key missing / auth / credit guard / unreachable / store: exit 4

    def lines(self) -> list[str]:
        if self.disabled:
            return [f"odds live capture: disabled ({FLAG_ENV} != 1); no-op"]
        st = ", ".join(f"{k}={v}" for k, v in sorted(self.statuses.items())) or "none"
        out = [
            f"odds live capture {self.kind} {self.slate} dry_run={self.dry_run}: due={self.due} "
            f"pulled={self.pulled} inserted={self.inserted} credits={self.credits} [{st}]"
        ]
        return out + [f"  note: {m}" for m in self.messages]


def run_live_capture(
    slate: date,
    kind: str,
    *,
    dry_run: bool = False,
    environ: Mapping[str, str] | None = None,
    now_fn: Callable[[], datetime] | None = None,
    cfg: LiveConfig | None = None,
    client: LiveClient | None = None,
    resolver: NameResolver | None = None,
    sleep: Callable[[float], None] = time.sleep,
    http_get: Callable[..., Any] | None = None,
) -> LiveReport:
    """One tick. A no-op (no file, key or network touched) unless the flag is set."""
    rep = LiveReport(slate=slate, kind=kind, dry_run=dry_run)
    if not enabled(environ):
        rep.disabled = True
        return rep
    clock = now_fn or (lambda: datetime.now(UTC))
    c = cfg or load_live_config()
    games = select_window(read_slate(c.tips_db, slate, c.games_db), clock(), kind, c, slate)
    rep.due = len(games)
    if not games:  # the common tick: nothing in the window, no key read, no request
        return rep
    con: duckdb.DuckDBPyConnection | None = None
    try:
        con = open_store(c.out_db)
        skip = done_games(con, kind, c.max_attempts_per_game)
        pending = [g for g in games if g.game_id not in skip]
        if not pending:
            return rep
        cli = client or LiveClient(
            c, environ=environ, sleep=sleep, http_get=http_get, day=clock().astimezone(ET).date()
        )
        res = resolver or default_resolver()
        _pull_all(rep, con, cli, res, pending, slate, kind, c, clock)
    except HistoryError as exc:  # key missing / auth / guard / transport before any game
        rep.degraded = True
        rep.messages.append(f"{type(exc).__name__}: {redact(str(exc))}")
    except (duckdb.Error, OSError) as exc:
        rep.degraded = True
        rep.messages.append(f"store: {type(exc).__name__}: {exc}")
    finally:
        if con is not None:
            con.close()
    return rep


def _pull_all(
    rep: LiveReport,
    con: duckdb.DuckDBPyConnection,
    cli: LiveClient,
    res: NameResolver,
    pending: list[LiveGame],
    slate: date,
    kind: str,
    c: LiveConfig,
    clock: Callable[[], datetime],
) -> None:
    key = cli.key_text()
    pad = timedelta(minutes=c.match_window_min)
    body, _ = cli.live_events(
        min(g.tipoff for g in pending) - pad, max(g.tipoff for g in pending) + pad
    )
    events = parse_events({"data": body})
    for g in pending:
        rivals = sum(1 for o in pending if o.tipoff == g.tipoff)
        ev = match_live_event(events, g, rivals, res, c.match_window_min)
        if ev is None:
            _record(rep, con, g, kind, "unmatched", dry=rep.dry_run, note="no unique vendor event")
            continue
        req = clock()
        if naive_utc(req) >= g.tipoff or naive_utc(req) >= ev.commence_time:
            _record(rep, con, g, kind, "post_tip_refused", dry=rep.dry_run, event_id=ev.event_id)
            continue
        if rep.dry_run:
            rep.statuses["dry_run_would_pull"] += 1
            continue
        try:
            odds, credits, params = cli.live_event_odds(ev.event_id)
        except (AuthError, CreditBudgetExceeded) as exc:
            rep.degraded = True
            msg = redact(str(exc), key)
            rep.messages.append(f"{type(exc).__name__}: {msg}")
            _record(rep, con, g, kind, "credit_guard", dry=False, note=msg)
            return  # stop the tick; the next one retries inside the window
        except HistoryError as exc:
            rep.degraded = True
            msg = redact(str(exc), key)
            rep.messages.append(f"{g.game_id}: {type(exc).__name__}: {msg}")
            _record(rep, con, g, kind, "error", dry=False, event_id=ev.event_id, note=msg)
            continue
        recv = clock()
        rep.pulled += 1
        rep.credits += credits
        blob = {
            "endpoint": f"events/{ev.event_id}/odds",
            "params": params,
            "requested_at": naive_utc(req).isoformat(),
            "received_at": naive_utc(recv).isoformat(),
            "cost": credits,
            "game_id": g.game_id,
            "snapshot_kind": kind,
            "body": odds,
        }
        raw = write_raw(c, key, g, kind, blob)  # raw first, even if the write below is refused
        parsed = parse_event_odds(
            {"timestamp": iso_z(req), "data": odds},
            season=0,
            game_id=g.game_id,
            snapshot_kind=kind,
            requested_at=req,
            raw_file=raw.name,
            resolver=res,
        )
        for reason, n in parsed.reasons.items():
            rep.statuses[f"parse_{reason}"] += n
        try:
            n_ins = write_snapshot(
                con,
                parsed.rows,
                game=g,
                kind=kind,
                slate=slate,
                requested_at=req,
                received_at=recv,
                commence_time=ev.commence_time,
                raw_file=raw.name,
            )
        except PostTipError as exc:
            _record(
                rep, con, g, kind, "post_tip_refused", dry=False, event_id=ev.event_id,
                credits=credits, note=str(exc), req=req, recv=recv,
            )  # fmt: skip
            continue
        rep.inserted += n_ins
        _record(
            rep, con, g, kind, "ok" if parsed.rows.height else "empty", dry=False,
            event_id=ev.event_id, credits=credits, n_rows=parsed.rows.height, req=req, recv=recv,
        )  # fmt: skip


def _record(
    rep: LiveReport,
    con: duckdb.DuckDBPyConnection,
    g: LiveGame,
    kind: str,
    status: str,
    *,
    dry: bool,
    event_id: str | None = None,
    credits: int = 0,
    n_rows: int = 0,
    note: str = "",
    req: datetime | None = None,
    recv: datetime | None = None,
) -> None:
    rep.statuses[status] += 1
    if dry:
        return
    con.execute(
        "INSERT INTO live_capture_log VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            g.game_id,
            kind,
            _opt_naive(req),
            _opt_naive(recv),
            g.tipoff,
            event_id,
            status,
            n_rows,
            credits,
            note[:300],
        ],
    )


def run_schedule_tick(
    slate: date, *, dry_run: bool = False, cfg: LiveConfig | None = None, **kw: Any
) -> list[LiveReport]:
    """One tick over the whole active schedule (``--kind all``), close first. Each kind is its own
    idempotent pass; a no-op unless the flag is set. ``**kw`` goes to :func:`run_live_capture`."""
    c = cfg or load_live_config()
    return [
        run_live_capture(slate, e.kind, dry_run=dry_run, cfg=c, **kw)
        for e in due_order(c.active_schedule())
    ]
