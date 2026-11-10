"""ESPN freshness poller: measurement only, LIVE games only, own thread, own client.

Question it answers (instrumentation, no decision logic): is the ~142 s gap between ESPN summary
updates server-side staleness or our 60 s cadence? Every ~``interval_s`` seconds, for each game
ESPN's scoreboard says is in progress, it fetches the summary (and the core-API newest play) and
records our fetch time against the source's own last-play wallclock.

Isolation from the capture tick: separate ``httpx.Client`` with its own timeouts, no retries
inside a cycle, per-endpoint exponential backoff on 429/5xx/network errors, every failure
counted and logged in ``fresh_errors.log``; ``FreshPoller.run`` never raises. Raw bodies go to its
own ``RawStore`` (``<out>/fresh``; one writer per store), only when the body changed (sha256),
with ``fetched_at`` and the source stamps in the manifest note; every fetch, changed or not, is
a row in ``fresh.jsonl``. Conditional requests (ETag / If-Modified-Since) are sent when the
server ever offered a validator. Public GET only; no header but User-Agent and validators; no
credential can be attached.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import statistics
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from nba.live.http import USER_AGENT
from nba.live.store import RawStore

ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
SUMMARY = "espn_summary_fresh"
CORE_PLAYS = "espn_core_plays_fresh"
ENDPOINTS = (SUMMARY, CORE_PLAYS)
TIMEOUT = httpx.Timeout(8.0, read=5.0)
AUTH_STOP_AFTER = 5  # consecutive 401/403 before the poller stops itself
MAX_BACKOFF_S = 300.0


def parse_ts(s: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


@dataclass(frozen=True)
class LastPlay:
    play_id: str
    wallclock: datetime | None
    period: int | None
    clock: str | None
    n_plays: int | None = None


def last_play_summary(body: dict[str, Any]) -> tuple[LastPlay | None, str | None]:
    """(newest play, game state pre|in|post) from a site-API summary body."""
    comp = (body.get("header", {}).get("competitions") or [{}])[0]
    state = comp.get("status", {}).get("type", {}).get("state")
    plays = body.get("plays") or []
    if not plays:
        return None, state
    p = plays[-1]
    return (
        LastPlay(
            str(p.get("id", "")),
            parse_ts(p["wallclock"]) if p.get("wallclock") else None,
            (p.get("period") or {}).get("number"),
            (p.get("clock") or {}).get("displayValue"),
            len(plays),
        ),
        state,
    )


def last_play_core(body: dict[str, Any]) -> LastPlay | None:
    """Newest play from a core-API ``plays?limit=1&page=<count>`` body (or None if no item)."""
    items = body.get("items") or []
    if not items:
        return None
    p = items[-1]
    return LastPlay(
        str(p.get("id", "")),
        parse_ts(p["wallclock"]) if p.get("wallclock") else None,
        (p.get("period") or {}).get("number"),
        (p.get("clock") or {}).get("displayValue"),
        body.get("count"),
    )


def live_event_ids(scoreboard: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for ev in scoreboard.get("events", []):
        try:
            if ev["status"]["type"]["state"] == "in":
                out.append(str(ev["id"]))
        except (KeyError, TypeError):
            continue
    return out


@dataclass
class Fetched:
    status: int
    body: bytes
    fetched_at: datetime
    not_modified: bool
    headers: dict[str, str]


@dataclass
class Endpoint:
    """Per-URL conditional-request memory and backoff."""

    etag: str | None = None
    last_modified: str | None = None
    fails: int = 0
    next_ok: float = 0.0
    saw_304: bool = False
    saw_validator: bool = False


@dataclass
class Stats:
    cycles: int = 0
    requests: int = 0
    ok: int = 0
    not_modified: int = 0
    errors: int = 0
    backoffs: int = 0
    saved: int = 0
    live_games: int = 0
    last_cycle_ts: str = ""
    last_error: str = ""
    last_success_ts: str = ""
    last_scoreboard_ok_ts: str = ""
    consecutive_fail: int = 0
    consecutive_auth: int = 0
    parse_bad: int = 0
    last_cycle_s: float = 0.0
    stopped_reason: str = ""
    by_endpoint: dict[str, int] = field(default_factory=dict)


class FreshPoller:
    def __init__(
        self,
        day: date,
        out: Path,
        *,
        client: httpx.Client | None = None,
        interval_s: float = 18.0,
        scoreboard_every_s: float = 45.0,
        now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.day = day
        self.out = out
        out.mkdir(parents=True, exist_ok=True)
        self.store = RawStore(out / "fresh")
        self.client = client or httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        self.interval_s = interval_s
        self.scoreboard_every_s = scoreboard_every_s
        self.now = now_fn
        self.clock = clock
        self.sleep = sleep
        self.st = Stats()
        self.stop = threading.Event()
        self.eps: dict[str, Endpoint] = {}
        self.prev: dict[tuple[str, str], tuple[str, str]] = {}  # (endpoint, game) -> (sha, play id)
        self.live: list[str] = []
        self._sb_at = float("-inf")
        self.thread: threading.Thread | None = None

    # ---------------------------------------------------------------- plumbing
    def log_error(self, what: str) -> None:
        self.st.errors += 1
        self.st.last_error = what[:200]
        # a full disk must not kill the thread
        with contextlib.suppress(OSError), (self.out / "fresh_errors.log").open("a") as f:
            f.write(f"{self.now().isoformat()} {what}\n")

    def bad(self, what: str) -> None:
        """A body that parsed as JSON but lacks the fields we measure with: loud, own counter."""
        self.st.parse_bad += 1
        self.log_error(f"parse_bad {what}")

    def get(self, url: str, params: dict[str, Any] | None = None) -> Fetched | None:
        """One GET, no retry. None when backing off or on a failure (counted, backoff armed)."""
        key = url + json.dumps(params or {}, sort_keys=True)
        ep = self.eps.setdefault(key, Endpoint())
        if self.stop.is_set() or self.clock() < ep.next_ok:
            return None
        headers: dict[str, str] = {}
        if ep.etag:
            headers["If-None-Match"] = ep.etag
        if ep.last_modified:
            headers["If-Modified-Since"] = ep.last_modified
        self.st.requests += 1
        try:
            r = self.client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            self._fail(ep, f"{type(exc).__name__}: {str(exc)[:100]} {url}")
            return None
        fetched_at = self.now()
        if r.status_code not in (200, 304):
            if r.status_code in (401, 403):
                self.st.consecutive_auth += 1
                if self.st.consecutive_auth >= AUTH_STOP_AFTER:
                    self.st.stopped_reason = f"http {r.status_code} x{self.st.consecutive_auth}"
                    self.stop.set()
            else:
                self.st.consecutive_auth = 0
            self._fail(ep, f"http {r.status_code} {url}")
            return None
        ep.fails = 0
        ep.next_ok = 0.0
        self.st.consecutive_fail = 0
        self.st.consecutive_auth = 0
        self.st.last_success_ts = fetched_at.isoformat()
        hdrs = dict(r.headers)
        if r.status_code == 304:
            ep.saw_304 = True
            self.st.not_modified += 1
            return Fetched(304, b"", fetched_at, True, hdrs)
        ep.etag = r.headers.get("etag") or ep.etag
        ep.last_modified = r.headers.get("last-modified") or ep.last_modified
        ep.saw_validator = ep.saw_validator or bool(r.headers.get("etag") or ep.last_modified)
        self.st.ok += 1
        return Fetched(200, r.content, fetched_at, False, hdrs)

    def _fail(self, ep: Endpoint, what: str) -> None:
        ep.fails += 1
        self.st.consecutive_fail += 1
        ep.next_ok = self.clock() + min(MAX_BACKOFF_S, self.interval_s * 2**ep.fails)
        self.st.backoffs += 1
        self.log_error(what)

    # ---------------------------------------------------------------- one cycle
    def refresh_live(self) -> None:
        if self.clock() - self._sb_at < self.scoreboard_every_s:
            return
        self._sb_at = self.clock()
        r = self.get(f"{ESPN}/scoreboard", {"dates": self.day.strftime("%Y%m%d")})
        if r is None:
            return
        self.st.last_scoreboard_ok_ts = r.fetched_at.isoformat()
        if r.not_modified:
            return
        try:
            self.live = live_event_ids(json.loads(r.body))
        except ValueError as exc:
            self.log_error(f"scoreboard parse {exc}")

    def record(
        self,
        endpoint: str,
        game: str,
        r: Fetched,
        lp: LastPlay | None,
        state: str | None,
        sha: str | None,
    ) -> None:
        prev = self.prev.get((endpoint, game))
        changed = bool(lp and (prev is None or prev[1] != lp.play_id))
        lag = (r.fetched_at - lp.wallclock).total_seconds() if lp and lp.wallclock else None
        row: dict[str, Any] = {
            "endpoint": endpoint,
            "game": game,
            "fetched_at": r.fetched_at.isoformat(),
            "status": r.status,
            "not_modified": r.not_modified,
            "state": state,
            "play_id": lp.play_id if lp else None,
            "play_wallclock": lp.wallclock.isoformat() if lp and lp.wallclock else None,
            "period": lp.period if lp else None,
            "clock": lp.clock if lp else None,
            "n_plays": lp.n_plays if lp else None,
            "lag_s": lag,
            "changed": changed,
            "first_poll": prev is None,
            "hdr": {
                k: r.headers[k]
                for k in ("date", "age", "cache-control", "etag", "last-modified")
                if k in r.headers
            },
        }
        with (self.out / "fresh.jsonl").open("a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
        if sha:
            self.prev[(endpoint, game)] = (sha, lp.play_id if lp else "")
        self.st.by_endpoint[endpoint] = self.st.by_endpoint.get(endpoint, 0) + 1

    def poll_summary(self, game: str) -> None:
        r = self.get(f"{ESPN}/summary", {"event": game})
        if r is None:
            return
        if r.not_modified:
            self.record(SUMMARY, game, r, None, None, None)
            return
        try:
            lp, state = last_play_summary(json.loads(r.body))
        except ValueError as exc:
            self.log_error(f"summary {game} parse {exc}")
            return
        if lp is not None and (not lp.play_id or lp.wallclock is None):
            self.bad(f"summary {game}: newest play lacks id or wallclock")
            return
        sha = hashlib.sha256(r.body).hexdigest()
        prev = self.prev.get((SUMMARY, game))
        self.record(SUMMARY, game, r, lp, state, sha)
        if prev is None or prev[0] != sha:
            self.store.save(
                SUMMARY,
                game,
                r.body,
                http_status=r.status,
                received_at=r.fetched_at,
                note={
                    "fetched_at": r.fetched_at.isoformat(),
                    "play_id": lp.play_id if lp else None,
                    "play_wallclock": lp.wallclock.isoformat() if lp and lp.wallclock else None,
                    "period": lp.period if lp else None,
                    "clock": lp.clock if lp else None,
                },
            )
            self.st.saved += 1

    def poll_core(self, game: str) -> None:
        """Newest play via the core API: ``plays?limit=1`` for the count, then that page."""
        base = f"{CORE}/events/{game}/competitions/{game}/plays"
        head = self.get(base, {"limit": 1})
        if head is None or head.not_modified:
            return
        try:
            head_body = json.loads(head.body)
            if "count" not in head_body:
                self.bad(f"core plays {game}: no count in head body")
                return
            count = int(head_body["count"] or 0)
        except (ValueError, TypeError, AttributeError) as exc:
            self.log_error(f"core plays {game} parse {exc}")
            return
        if count <= 0:
            return
        r = self.get(base, {"limit": 1, "page": count})
        if r is None or r.not_modified:
            return
        try:
            lp = last_play_core(json.loads(r.body))
        except ValueError as exc:
            self.log_error(f"core plays {game} parse {exc}")
            return
        if lp is None or not lp.play_id or lp.wallclock is None:
            self.bad(f"core plays {game}: item missing, or no inline id/wallclock ($ref only?)")
            return
        sha = hashlib.sha256(r.body).hexdigest()
        prev = self.prev.get((CORE_PLAYS, game))
        self.record(CORE_PLAYS, game, r, lp, None, sha)
        if prev is None or prev[0] != sha:
            self.store.save(
                CORE_PLAYS,
                game,
                r.body,
                http_status=r.status,
                received_at=r.fetched_at,
                note={
                    "fetched_at": r.fetched_at.isoformat(),
                    "play_id": lp.play_id if lp else None,
                    "play_wallclock": lp.wallclock.isoformat() if lp and lp.wallclock else None,
                    "count": count,
                },
            )
            self.st.saved += 1

    def cycle(self) -> None:
        self.refresh_live()
        self.st.live_games = len(self.live)
        for game in list(self.live):
            if self.stop.is_set():
                break
            self.poll_summary(game)
            self.poll_core(game)
        self.st.cycles += 1
        self.st.last_cycle_ts = self.now().isoformat()

    def run(self) -> None:
        """Loop until ``stop`` is set; never raises."""
        while not self.stop.is_set():
            t0 = self.clock()
            try:
                self.cycle()
            except Exception:
                self.log_error("cycle " + traceback.format_exc().replace("\n", " | ")[:800])
            self.st.last_cycle_s = round(self.clock() - t0, 3)
            with contextlib.suppress(OSError), (self.out / "fresh_cycles.jsonl").open("a") as f:
                f.write(
                    json.dumps(
                        {
                            "at": self.now().isoformat(),
                            "cycle_s": self.st.last_cycle_s,
                            "live": self.st.live_games,
                            "requests": self.st.requests,
                        }
                    )
                    + "\n"
                )
            self.stop.wait(max(1.0, self.interval_s - (self.clock() - t0)))

    # ---------------------------------------------------------------- thread + heartbeat
    def start(self) -> None:
        self.thread = threading.Thread(target=self.run, name="espn-fresh", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=15)
        self.client.close()

    def heartbeat_fields(self) -> dict[str, Any]:
        s = self.st
        return {
            "fresh_alive": bool(self.thread and self.thread.is_alive()),
            "fresh_cycles": s.cycles,
            "fresh_requests": s.requests,
            "fresh_errors": s.errors,
            "fresh_backoffs": s.backoffs,
            "fresh_304": s.not_modified,
            "fresh_saved": s.saved,
            "fresh_live_games": s.live_games,
            "fresh_last_cycle_ts": s.last_cycle_ts,
            "fresh_last_error": s.last_error,
            "fresh_last_success_ts": s.last_success_ts,
            "fresh_last_scoreboard_ok_ts": s.last_scoreboard_ok_ts,
            "fresh_consecutive_fail": s.consecutive_fail,
            "fresh_parse_bad": s.parse_bad,
            "fresh_last_cycle_s": s.last_cycle_s,
            "fresh_stopped_reason": s.stopped_reason,
        }


# -------------------------------------------------------------------- freshness report
def _q(x: list[float], q: float) -> float | None:
    if not x:
        return None
    s = sorted(x)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def freshness_report(jsonl: Path, interval_s: float | None = None) -> dict[str, Any]:
    """Per-endpoint summary of one night's ``fresh.jsonl``.

    ``first_seen_lag_s``: fetch time minus the newest play's wallclock, on fetches where that play
    was new to us (the first fetch of a game is excluded: its play may be old). With polls ``d``
    seconds apart, lag = source delay + U(0, d), so true availability delay lies in [lag - d, lag]
    (a 50 s median at d = 60 s means about 20 s of source delay). ``update_gap_s``: time between
    successive new-play fetches. ``unchanged_rate``: share of consecutive live fetches returning
    the same newest play. Descriptive only; n on every line.
    """
    rows: list[dict[str, Any]] = []
    torn = 0
    for line in jsonl.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            torn += 1
            continue
        if isinstance(row, dict) and "endpoint" in row and "game" in row:
            rows.append(row)
        else:
            torn += 1
    out: dict[str, Any] = {"interval_s": interval_s, "n_unparseable_lines": torn, "endpoints": {}}
    for ep in sorted({r["endpoint"] for r in rows}):
        er = [r for r in rows if r["endpoint"] == ep]
        by: dict[str, list[dict[str, Any]]] = {}
        for r in er:
            by.setdefault(r["game"], []).append(r)
        lag: list[float] = []
        gaps: list[float] = []
        pairs = same = 0
        for rs in by.values():
            last_change: datetime | None = None
            for r in rs:
                if r["status"] == 304:
                    pairs += 1
                    same += 1
                    continue
                if r.get("state") not in (None, "in"):
                    continue
                t = parse_ts(r["fetched_at"])
                if not r["first_poll"]:
                    pairs += 1
                    if not r["changed"]:
                        same += 1
                if r["changed"] and not r["first_poll"]:
                    if r["lag_s"] is not None:
                        lag.append(float(r["lag_s"]))
                    if last_change and t:
                        gaps.append((t - last_change).total_seconds())
                if r["changed"] and t:
                    last_change = t
        out["endpoints"][ep] = {
            "n_fetches": len(er),
            "n_games": len(by),
            "n_not_modified": sum(1 for r in er if r["status"] == 304),
            "unchanged_rate": (same / pairs) if pairs else None,
            "unchanged_pairs": pairs,
            "first_seen_lag_s": {
                "n": len(lag),
                "median": statistics.median(lag) if lag else None,
                "p90": _q(lag, 0.9),
            },
            "update_gap_s": {
                "n": len(gaps),
                "median": statistics.median(gaps) if gaps else None,
                "p90": _q(gaps, 0.9),
            },
        }
    return out
