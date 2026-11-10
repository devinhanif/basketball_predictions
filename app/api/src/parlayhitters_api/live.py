"""Live play-by-play, read from the capture's ESPN summary bodies on disk. No network, no store.

Independent of the export store on purpose: nothing here loads ``ExportStore`` (a cold start of
that is ~15 s), so the live routes answer from the first request. The capture writes write-once
gzip bodies under ``<root>/live_<date>/espn_summary/<event_id>/<UTC stamp>.json.gz`` (about every
60 s) and, when the freshness poller is on, the same under
``<root>/live_<date>/fresh/espn_summary_fresh/<event_id>/``. The file name stamp is the fetch
time. The newest body across both wins; a tie goes to the fresh one.

Latency design: one shared watcher per game that has subscribers (never one poll per client). It
``stat``s the two directories every ``poll_s`` (0.2 s; two stat calls, no listing) and lists and
parses only when a directory changed; the parse runs in a worker thread so the event loop never
blocks. A new body is turned
into a small delta (new or edited plays, changed box rows, score and status) that is serialised
once and put on every subscriber's bounded queue. A subscriber that cannot keep up is dropped and
reconnects to a fresh snapshot.

Mapping logic copied (not imported) from ``nba/live/capture.py`` (``ESPN_TO_KALSHI``) so the app
keeps the wall: ESPN abbreviations GS/NY/SA/NO/UTAH/WSH become the tricodes the app uses.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import threading
import time
import zlib
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("parlayhitters.live")

LIVE_ENV = "APP_LIVE_DIR"
POLL_ENV = "APP_LIVE_POLL_S"
CHECKPOINT_ENV = "APP_CHECKPOINT_SNAPSHOT"
DEFAULT_ROOT = Path(__file__).resolve().parents[4] / "data" / "raw"
DEFAULT_CHECKPOINT_SNAPSHOT = (
    Path(__file__).resolve().parents[4] / "data" / "jarvis" / "ingame_checkpoints_latest.json"
)

# Copied from nba/live/capture.py ESPN_TO_KALSHI (ESPN abbreviation -> the tricode the app uses).
ESPN_TO_TRI = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"}

EVENT_RE = re.compile(r"[0-9]{6,12}")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
STAMP_RE = re.compile(r"^(\d{8}T\d{6}Z)(?:_(\d+))?\.json\.gz$")

ATTRIBUTION = "Play-by-play: ESPN. Not affiliated."
DELAY_LABEL = (
    "Delayed feed. It runs behind the arena: the newest play is usually tens of seconds to a few "
    "minutes old by the time you see it. Each number below says how old it is."
)
CHECKPOINT_LABEL = "UNPROVEN: no live record; most nights the market already knows this."

SNAPSHOT_PLAYS = 80  # newest plays in a snapshot; older ones come from /plays?before=
MAX_SUBS_TOTAL = 300
MAX_SUBS_PER_GAME = 150
MAX_WATCHED_GAMES = 30
QUEUE_MAX = 64
HEARTBEAT_S = 15.0
PARSE_CACHE_MAX = 64
MAX_BODY_BYTES = 20_000_000  # decompressed; a larger body is refused, not parsed


# ----------------------------------------------------------------------------- locating bodies


def live_root() -> Path:
    return Path(os.environ.get(LIVE_ENV) or DEFAULT_ROOT)


def poll_seconds() -> float:
    try:
        v = float(os.environ.get(POLL_ENV, "0.2"))
    except ValueError:
        return 0.2
    return min(5.0, max(0.05, v))


@dataclass(frozen=True)
class BodyRef:
    path: Path
    source: str  # "fresh" | "capture"
    stamp: str  # UTC second of the fetch, e.g. 20261110T014124Z
    suffix: int

    @property
    def fetched_at(self) -> datetime:
        return datetime.strptime(self.stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)

    @property
    def order(self) -> tuple[str, int, int]:
        return (self.stamp, self.suffix, 1 if self.source == "fresh" else 0)


def _newest_in(directory: Path, source: str) -> BodyRef | None:
    best: BodyRef | None = None
    try:
        with os.scandir(directory) as it:
            for e in it:
                m = STAMP_RE.match(e.name)
                if m is None:
                    continue  # a .tmp, or anything else
                ref = BodyRef(Path(e.path), source, m.group(1), int(m.group(2) or 0))
                if best is None or ref.order > best.order:
                    best = ref
    except OSError:
        return None
    return best


def event_dirs(root: Path, date: str, event_id: str) -> tuple[Path, Path]:
    night = root / f"live_{date}"
    return night / "espn_summary" / event_id, night / "fresh" / "espn_summary_fresh" / event_id


def newest_body(root: Path, date: str, event_id: str) -> BodyRef | None:
    cap, fresh = event_dirs(root, date, event_id)
    a, b = _newest_in(cap, "capture"), _newest_in(fresh, "fresh")
    if a is None or b is None:
        return a or b
    return b if b.order >= a.order else a


def _mtime(p: Path) -> int | None:
    try:
        return p.stat().st_mtime_ns
    except OSError:
        return None


def locate_event(root: Path, event_id: str) -> str | None:
    """The slate date whose capture directory holds this event (newest few nights only)."""
    try:
        nights = sorted(
            (p.name for p in root.iterdir() if p.name.startswith("live_")), reverse=True
        )
    except OSError:
        return None
    for name in nights[:4]:
        date = name[len("live_") :]
        if DATE_RE.fullmatch(date) and (root / name / "espn_summary" / event_id).is_dir():
            return date
    return None


def list_event_ids(root: Path, date: str) -> list[str]:
    base = root / f"live_{date}" / "espn_summary"
    try:
        return sorted(p.name for p in base.iterdir() if EVENT_RE.fullmatch(p.name) and p.is_dir())
    except OSError:
        return []


# ----------------------------------------------------------------------------- parsing


class BodyError(ValueError):
    """The body is not an ESPN summary we can read."""


def _tri(abbr: object) -> str:
    s = str(abbr or "")
    return ESPN_TO_TRI.get(s, s)


def _int(v: object, default: int = 0) -> int:
    try:
        return int(str(v).strip().lstrip("+"))
    except (ValueError, TypeError):
        return default


def clock_seconds(txt: str) -> float | None:
    """'3:39' or '41.2' -> seconds left in the period."""
    try:
        if ":" in txt:
            m, s = txt.split(":", 1)
            return int(m) * 60 + float(s)
        return float(txt)
    except ValueError:
        return None


def elapsed_minutes(period: int, clock: str) -> float | None:
    left = clock_seconds(clock)
    if left is None or period < 1:
        return None
    if period <= 4:
        return round(((period - 1) * 720 + (720 - left)) / 60, 2)
    return round((2880 + (period - 5) * 300 + (300 - left)) / 60, 2)


@dataclass
class GameState:
    event_id: str
    date: str
    state: str  # pre | in | post
    tip_utc: str | None
    away: dict[str, Any]
    home: dict[str, Any]
    status: dict[str, Any]
    plays: dict[int, dict[str, Any]]
    box: dict[str, dict[str, Any]]  # espn athlete id -> row
    source: str
    stamp: str
    fetched_at: str
    last_play_at: str | None
    play_lag_s: float | None

    def meta(self, now: datetime) -> dict[str, Any]:
        fetched = datetime.fromisoformat(self.fetched_at)
        return {
            "source": self.source,
            "fetched_at": self.fetched_at,
            "last_play_at": self.last_play_at,
            "play_lag_s": self.play_lag_s,
            "server_now": now.isoformat(),
            "age_s": round(max(0.0, (now - fetched).total_seconds()), 1),
        }

    def summary(self, now: datetime) -> dict[str, Any]:
        return {
            "game_id": self.event_id,
            "state": self.state,
            "tip_utc": self.tip_utc,
            "away": self.away,
            "home": self.home,
            "status": self.status,
            "n_plays": len(self.plays),
            **self.meta(now),
        }


def _box_rows(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for team in (body.get("boxscore") or {}).get("players") or []:
        abbr = _tri((team.get("team") or {}).get("abbreviation"))
        for block in team.get("statistics") or []:
            keys = block.get("keys") or block.get("names") or []
            for a in block.get("athletes") or []:
                ath = a.get("athlete") or {}
                pid = str(ath.get("id") or "")
                if not pid:
                    continue
                vals = dict(zip(keys, a.get("stats") or [], strict=False))
                fg3 = str(vals.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted", ""))
                row: dict[str, Any] = {
                    "id": pid,
                    "name": str(ath.get("displayName") or ath.get("shortName") or ""),
                    "team": abbr,
                    "starter": bool(a.get("starter")),
                    "min": _int(vals.get("minutes"))
                    if vals.get("minutes") not in (None, "")
                    else None,
                    "pts": _int(vals.get("points")),
                    "reb": _int(vals.get("rebounds")),
                    "ast": _int(vals.get("assists")),
                    "fg3m": _int(fg3.split("-")[0]) if "-" in fg3 else 0,
                    "stl": _int(vals.get("steals")),
                    "blk": _int(vals.get("blocks")),
                    "tov": _int(vals.get("turnovers")),
                    "pf": _int(vals.get("fouls")),
                    "fg": str(vals.get("fieldGoalsMade-fieldGoalsAttempted", "")),
                    "fg3": fg3,
                    "ft": str(vals.get("freeThrowsMade-freeThrowsAttempted", "")),
                    "pm": str(vals.get("plusMinus", "")),
                }
                if a.get("didNotPlay"):
                    row["dnp"] = str(a.get("reason") or "did not play")
                out[pid] = row
    return out


def _elapsed(state: str, period: int, clock: str) -> float | None:
    """Game minutes played; a finished game has played its whole length, whatever the clock says."""
    if state == "pre":
        return None
    got = elapsed_minutes(period, clock)
    if state == "post":
        full = 48.0 + max(0, period - 4) * 5.0
        return max(got or 0.0, full)
    return got


def parse_body(raw: bytes, ref: BodyRef, date: str, event_id: str) -> GameState:
    try:
        body = json.loads(raw)
        comp = body["header"]["competitions"][0]
        status_t = comp["status"]
        competitors = comp["competitors"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise BodyError(f"unreadable summary: {exc}") from exc
    by_side: dict[str, dict[str, Any]] = {}
    team_abbr: dict[str, str] = {}
    for c in competitors:
        t = c.get("team") or {}
        abbr = _tri(t.get("abbreviation"))
        team_abbr[str(t.get("id"))] = abbr
        by_side[str(c.get("homeAway"))] = {
            "abbr": abbr,
            "name": str(t.get("name") or ""),
            "score": _int(c.get("score")),
        }
    if "home" not in by_side or "away" not in by_side:
        raise BodyError("no home/away competitors")
    period = _int(status_t.get("period"))
    clock = str(status_t.get("displayClock") or "")
    stype = status_t.get("type") or {}
    state = str(stype.get("state") or "pre")
    plays: dict[int, dict[str, Any]] = {}
    last_wall: datetime | None = None
    for p in body.get("plays") or []:
        n = _int(p.get("sequenceNumber"), -1)
        if n < 0:
            continue
        per = _int((p.get("period") or {}).get("number"))
        plays[n] = {
            "n": n,
            "p": per,
            "c": str((p.get("clock") or {}).get("displayValue") or ""),
            "t": str(p.get("text") or ""),
            "h": _int(p.get("homeScore")),
            "a": _int(p.get("awayScore")),
            "tm": team_abbr.get(str((p.get("team") or {}).get("id"))),
            "s": 1 if p.get("scoringPlay") else 0,
            "k": str((p.get("type") or {}).get("text") or ""),
        }
        w = p.get("wallclock")
        if w:
            with contextlib.suppress(ValueError):
                wt = datetime.fromisoformat(str(w).replace("Z", "+00:00"))
                if last_wall is None or wt > last_wall:
                    last_wall = wt
    fetched = ref.fetched_at
    lag = round((fetched - last_wall).total_seconds(), 1) if last_wall else None
    return GameState(
        event_id=event_id,
        date=date,
        state=state,
        tip_utc=str(comp.get("date")) if comp.get("date") else None,
        away=by_side["away"],
        home=by_side["home"],
        status={
            "period": period,
            "clock": clock,
            "detail": str(stype.get("shortDetail") or stype.get("detail") or ""),
            "elapsed_min": _elapsed(state, period, clock),
        },
        plays=plays,
        box=_box_rows(body),
        source=ref.source,
        stamp=ref.stamp,
        fetched_at=fetched.isoformat(),
        last_play_at=last_wall.isoformat() if last_wall else None,
        play_lag_s=lag,
    )


def load_state(ref: BodyRef, date: str, event_id: str) -> GameState:
    try:
        packed = ref.path.read_bytes()
        d = zlib.decompressobj(wbits=31)
        raw = d.decompress(packed, MAX_BODY_BYTES + 1)
    except (OSError, EOFError, zlib.error) as exc:
        raise BodyError(f"cannot read {ref.path.name}: {exc}") from exc
    if len(raw) > MAX_BODY_BYTES:
        raise BodyError(f"{ref.path.name} is larger than {MAX_BODY_BYTES} bytes decompressed")
    return parse_body(raw, ref, date, event_id)


# ----------------------------------------------------------------------------- messages


def _tail(plays: dict[int, dict[str, Any]], n: int) -> list[dict[str, Any]]:
    return [plays[k] for k in sorted(plays)[-n:]]


def snapshot_payload(gs: GameState, seq: int, now: datetime) -> dict[str, Any]:
    plays = _tail(gs.plays, SNAPSHOT_PLAYS)
    rows: dict[str, list[dict[str, Any]]] = {"away": [], "home": []}
    for r in gs.box.values():
        side = "home" if r["team"] == gs.home["abbr"] else "away"
        rows[side].append(r)
    return {
        "seq": seq,
        "game_id": gs.event_id,
        "date": gs.date,
        "state": gs.state,
        "tip_utc": gs.tip_utc,
        "away": gs.away,
        "home": gs.home,
        "status": gs.status,
        "box": rows,
        "plays": plays,
        "n_plays": len(gs.plays),
        "first_n": plays[0]["n"] if plays else None,
        "meta": gs.meta(now),
        "attribution": ATTRIBUTION,
        "delay_note": DELAY_LABEL,
    }


def delta_payload(prev: GameState, new: GameState, seq: int, now: datetime) -> dict[str, Any]:
    """Only what changed since ``prev``. A new body always carries a fresh ``meta`` (its age and
    lag are information even when no play changed); ``changed`` says whether anything else did."""
    d: dict[str, Any] = {"seq": seq, "game_id": new.event_id, "state": new.state}
    changed = new.state != prev.state
    if new.away != prev.away or new.home != prev.home:
        d["away"], d["home"] = new.away, new.home
        changed = True
    if new.status != prev.status:
        d["status"] = new.status
        changed = True
    plays = [p for n, p in sorted(new.plays.items()) if prev.plays.get(n) != p]
    if plays:
        d["plays"] = plays
        d["n_plays"] = len(new.plays)
        changed = True
    box = [r for pid, r in new.box.items() if prev.box.get(pid) != r]
    if box:
        d["box"] = box
        changed = True
    d["meta"] = new.meta(now)
    d["changed"] = changed
    return d


def sse(event: str, data: dict[str, Any], seq: int | None = None) -> str:
    head = f"id: {seq}\n" if seq is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


# ----------------------------------------------------------------------------- the hub


@dataclass(eq=False)
class Subscriber:
    queue: asyncio.Queue[str | None] = field(default_factory=lambda: asyncio.Queue(QUEUE_MAX))
    dead: bool = False


@dataclass
class GameWatch:
    event_id: str
    date: str
    state: GameState | None = None
    seq: int = 0
    subs: set[Subscriber] = field(default_factory=set)
    task: asyncio.Task[None] | None = None
    dir_key: tuple[int | None, int | None] = (None, None)
    last_order: tuple[str, int, int] = ("", 0, 0)


class HubFull(Exception):
    pass


class UnknownGame(Exception):
    pass


class LiveHub:
    """One per process. Must be used from a single event loop."""

    def __init__(self, root: Path | None = None, poll_s: float | None = None) -> None:
        self.root = root if root is not None else live_root()
        self.poll_s = poll_s if poll_s is not None else poll_seconds()
        self.games: dict[str, GameWatch] = {}
        self.n_subs = 0

    # -- subscription -------------------------------------------------------------------------

    async def subscribe(self, event_id: str) -> tuple[Subscriber, list[str], bool]:
        """(subscriber, first messages, already_final). Raises UnknownGame / HubFull."""
        if self.n_subs >= MAX_SUBS_TOTAL:  # every cap is checked before any entry exists
            raise HubFull("too many viewers")
        gw = self.games.get(event_id)
        if gw is None:
            if len(self.games) >= MAX_WATCHED_GAMES:
                raise HubFull("too many games are being watched")
            date = await asyncio.to_thread(locate_event, self.root, event_id)
            if date is None:
                raise UnknownGame(event_id)
            gw = self.games.get(event_id) or GameWatch(event_id, date)
            self.games[event_id] = gw
        elif len(gw.subs) >= MAX_SUBS_PER_GAME:
            raise HubFull("too many viewers")
        try:
            if gw.state is None:
                await self._refresh(gw)
            if gw.state is None:
                raise UnknownGame(event_id)
            if self.n_subs >= MAX_SUBS_TOTAL or len(gw.subs) >= MAX_SUBS_PER_GAME:
                raise HubFull("too many viewers")  # filled while we were reading the body
        except BaseException:
            self._drop_if_idle(gw)
            raise
        sub = Subscriber()
        now = datetime.now(UTC)
        first = [sse("snapshot", snapshot_payload(gw.state, gw.seq, now), gw.seq)]
        final = gw.state.state == "post"
        if final:
            first.append(sse("final", {"game_id": event_id, "seq": gw.seq}))
        else:
            gw.subs.add(sub)
            self.n_subs += 1
            if gw.task is None or gw.task.done():
                gw.task = asyncio.create_task(self._watch(gw))
        self._drop_if_idle(gw)  # a final game keeps no entry
        return sub, first, final

    def _drop_if_idle(self, gw: GameWatch) -> None:
        """Remove an entry nobody is watching and no task is serving (a refusal, or a final)."""
        if (
            not gw.subs
            and (gw.task is None or gw.task.done())
            and self.games.get(gw.event_id) is gw
        ):
            del self.games[gw.event_id]

    def unsubscribe(self, event_id: str, sub: Subscriber) -> None:
        gw = self.games.get(event_id)
        if gw is not None and sub in gw.subs:
            gw.subs.discard(sub)
            self.n_subs -= 1

    # -- watching -----------------------------------------------------------------------------

    def _keys(self, gw: GameWatch) -> tuple[int | None, int | None]:
        cap, fresh = event_dirs(self.root, gw.date, gw.event_id)
        return _mtime(cap), _mtime(fresh)

    async def _refresh(self, gw: GameWatch) -> bool:
        """Look for a newer body; True when the state moved. Never raises."""
        gw.dir_key = self._keys(gw)  # before listing: a change during the listing is seen next time
        try:
            ref = await asyncio.to_thread(newest_body, self.root, gw.date, gw.event_id)
            if ref is None or ref.order <= gw.last_order:
                return False
            new = await asyncio.to_thread(load_state, ref, gw.date, gw.event_id)
        except BodyError as exc:
            log.warning("live %s: %s", gw.event_id, exc)
            return False
        except Exception:  # a watcher must never die on one bad body
            log.exception("live %s: refresh failed", gw.event_id)
            return False
        gw.last_order = ref.order
        prev = gw.state
        gw.state = new
        if prev is None:
            return True
        gw.seq += 1
        msg = sse("delta", delta_payload(prev, new, gw.seq, datetime.now(UTC)), gw.seq)
        self._broadcast(gw, msg)
        if new.state == "post":
            self._broadcast(gw, sse("final", {"game_id": gw.event_id, "seq": gw.seq}))
            self._broadcast(gw, None)
        return True

    def _broadcast(self, gw: GameWatch, msg: str | None) -> None:
        for sub in list(gw.subs):
            try:
                sub.queue.put_nowait(msg)
            except asyncio.QueueFull:
                sub.dead = True  # too slow: it reconnects to a snapshot
                gw.subs.discard(sub)
                self.n_subs -= 1
                while not sub.queue.empty():  # stale deltas are useless: end the stream now
                    sub.queue.get_nowait()
                sub.queue.put_nowait(None)

    async def _watch(self, gw: GameWatch) -> None:
        try:
            while gw.subs:
                await asyncio.sleep(self.poll_s)
                if self._keys(gw) != gw.dir_key:
                    await self._refresh(gw)
                if gw.state is not None and gw.state.state == "post":
                    break
        finally:
            for sub in list(gw.subs):
                self.unsubscribe(gw.event_id, sub)
            if not gw.subs:
                self.games.pop(gw.event_id, None)

    # -- one-shot reads (no subscription) -----------------------------------------------------

    async def current(self, event_id: str) -> GameState | None:
        gw = self.games.get(event_id)
        if gw is not None and gw.state is not None:
            return gw.state
        date = await asyncio.to_thread(locate_event, self.root, event_id)
        if date is None:
            return None
        return await asyncio.to_thread(_load_newest, self.root, date, event_id)


def _load_newest(root: Path, date: str, event_id: str) -> GameState | None:
    ref = newest_body(root, date, event_id)
    if ref is None:
        return None
    try:
        return _cached_load(ref, date, event_id)
    except BodyError:
        return None


_parse_cache: OrderedDict[Path, GameState] = OrderedDict()
_parse_lock = threading.Lock()


def _cached_load(ref: BodyRef, date: str, event_id: str) -> GameState:
    with _parse_lock:
        hit = _parse_cache.get(ref.path)
        if hit is not None:
            _parse_cache.move_to_end(ref.path)
            return hit
    state = load_state(ref, date, event_id)
    with _parse_lock:
        _parse_cache[ref.path] = state
        while len(_parse_cache) > PARSE_CACHE_MAX:
            _parse_cache.popitem(last=False)
    return state


_list_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_list_lock = threading.Lock()
LIST_TTL_S = 1.0


def games_on(root: Path, date: str) -> dict[str, Any]:
    """Every game the capture holds for a slate date, with its latest state (no plays). Cached for a
    second so many viewers cost one read."""
    with _list_lock:
        hit = _list_cache.get(f"{root}|{date}")
        if hit is not None and time.monotonic() - hit[0] < LIST_TTL_S:
            return hit[1]
    now = datetime.now(UTC)
    games: list[dict[str, Any]] = []
    for eid in list_event_ids(root, date):
        ref = newest_body(root, date, eid)
        if ref is None:
            continue
        try:
            games.append(_cached_load(ref, date, eid).summary(now))
        except BodyError:
            continue
    games.sort(key=lambda g: (str(g["tip_utc"]), g["game_id"]))
    out: dict[str, Any] = {
        "date": date,
        "games": games,
        "server_now": now.isoformat(),
        "attribution": ATTRIBUTION,
        "delay_note": DELAY_LABEL,
    }
    if not games:
        out["note"] = "No live capture for this date."
    with _list_lock:
        _list_cache[f"{root}|{date}"] = (time.monotonic(), out)
        if len(_list_cache) > 16:
            _list_cache.pop(next(iter(_list_cache)))
    return out


def plays_before(state: GameState, before: int, limit: int) -> list[dict[str, Any]]:
    older = [state.plays[k] for k in sorted(state.plays) if k < before]
    return older[-limit:]


# ----------------------------------------------------------------------------- checkpoints


def checkpoint_snapshot_path() -> Path:
    return Path(os.environ.get(CHECKPOINT_ENV) or DEFAULT_CHECKPOINT_SNAPSHOT)


_ck_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
_ck_lock = threading.Lock()
CHECKPOINT_TTL_S = 15.0


def _load_snapshot(path: Path) -> dict[str, Any] | None:
    """The published checkpoint snapshot (docs/contracts/ingame_checkpoint_snapshot_v1.md).

    Cached 15 s per process.

    The app never opens the JARVIS DuckDB store: a reader there can make the live writer fail."""
    key = str(path)
    with _ck_lock:
        hit = _ck_cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < CHECKPOINT_TTL_S:
            return hit[1]
    data: dict[str, Any] | None = None
    try:
        raw = path.read_bytes()
        if len(raw) <= 5_000_000:
            parsed = json.loads(raw)
            if (
                isinstance(parsed, dict)
                and parsed.get("version") == 1
                and isinstance(parsed.get("games"), dict)
            ):
                data = parsed
    except (OSError, ValueError):
        data = None
    with _ck_lock:
        _ck_cache[key] = (time.monotonic(), data)
    return data


def read_checkpoint(event_id: str, game_state: str, path: Path | None = None) -> dict[str, Any]:
    """The newest in-game checkpoint rows for a game from the published snapshot, if one exists and
    holds rows for it. Only whitelisted fields are copied out: no price, ask, bid or edge."""
    base: dict[str, Any] = {"game_id": event_id, "label": CHECKPOINT_LABEL, "available": False}
    if game_state == "post":
        return {**base, "reason": "hidden once the game is final"}
    snap = _load_snapshot(path or checkpoint_snapshot_path())
    if snap is None:
        return {**base, "reason": "no checkpoint snapshot"}
    g = snap["games"].get(event_id)
    if not isinstance(g, dict) or not g.get("rows"):
        return {**base, "reason": "no checkpoint rows for this game"}
    try:
        rows = [
            {
                "player": str(r["player"]),
                "stat": str(r["stat"]),
                "k": int(r["k"]),
                "p_model": float(r["p_model"]),
                "cur": int(r["cur"]),
                "minutes": int(r["minutes"]),
                "fouls": int(r["fouls"]),
            }
            for r in g["rows"][:200]
        ]
        return {
            **base,
            "available": True,
            "checkpoint": int(g["checkpoint"]),
            "e_used": float(g["e_used"]),
            "support": str(g["support"]),
            "period": int(g["period"]),
            "clock": str(g["clock"]),
            "known_at": str(g["known_at"]),
            "rows": rows,
        }
    except (KeyError, TypeError, ValueError):
        return {**base, "reason": "checkpoint snapshot not readable"}
