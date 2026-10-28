"""The scheduled reporter poll: ``python -m nba.ingest.reporters tick`` (capture fixes 3 and 4).

Not on the prediction path. Nothing in ``nba.daily`` imports this module; it reads the cached
schedule, the raw store and ``reporter_facts.duckdb`` and never opens ``nba.duckdb``.

Cadence (per team-game, minutes before the real tip)
----------------------------------------------------
``T-90`` and ``T-40`` (already planned in docs/REPORTER_FEED.md), plus ``T-70`` (new), and a
``sweep`` at 13:30 ET for games that tip at least two hours after it, so a morning shootaround post
is read before the pre-game polls. A tick is a 5-minute launchd interval; a poll is due at its
offset and runs at the first tick at or after it, so ``T-90`` lands in [T-90, T-85] and ``T-70`` in
[T-70, T-65], both inside the [T-90, T-60] window the rule needs. After a sleep only the LATEST
due label runs (no burst), and it is retried at most three times when it does not end ok.

League-wide handles (team null) are due whenever any slate game has a due label, so a mid-slate
game has its own poll too.

Coverage: a team-game is covered iff every required handle has an ok poll inside
[tip - 90 min, tip - 60 min] (:func:`nba.ingest.reporter_store.compute_coverage`). Required =
the team's own handles; a team with none (CHA, MIL, POR today) is served by the league-wide
handles. A final ``team_game_coverage`` row is written once per team-game after T-60, and a
coverage miss at T-55 appends one line to ``data/ops/ALERTS.md``.

Heartbeat: ``data/ops/heartbeat/reporters.json`` is written at START (``end_ts`` null) and at END
with ``rc``; ``nba.ops.watchdog`` judges it (an optional job: silent until installed).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from nba.daily.schedule import ScheduledGame
from nba.ingest import reporter_facts as rf
from nba.ingest import reporter_store as rs
from nba.ingest.reporters import (
    Allowlist,
    BudgetExceeded,
    HandlePoll,
    KeyMissingError,
    PollResult,
    Post,
    XClient,
    read_raw,
)

log = logging.getLogger("nba.reporter_tick")
ET = ZoneInfo("America/New_York")

OFFSETS_MIN: dict[str, int] = {"T-90": 90, "T-70": 70, "T-40": 40}
SWEEP_ET = time(13, 30)
SWEEP_MIN_GAP = timedelta(minutes=120)
MAX_TRIES = 3
ALERT_AT = timedelta(minutes=55)
RECOVER_DAYS = 2  # raw days re-derived into facts after a poll (idempotent; heals a crash)

RC_OK, RC_REFUSED, RC_STORE, RC_NO_SCHEDULE = 0, 2, 3, 5


@dataclass(frozen=True)
class DuePoll:
    label: str
    handle: str
    team_id: int | None
    due_at: datetime  # aware UTC


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo is not None else t.replace(tzinfo=UTC)


def _naive(t: datetime) -> datetime:
    return t.astimezone(UTC).replace(tzinfo=None) if t.tzinfo is not None else t


def slate_for(games: list[ScheduledGame], now: datetime) -> list[ScheduledGame]:
    """Games whose ET tip date is ``now``'s ET date."""
    day = _aware(now).astimezone(ET).date()
    return [g for g in games if g.game_date_et == day]


def _candidates(tip: datetime, day: Any) -> list[tuple[datetime, str]]:
    """Due times (aware UTC) of one game: the three offsets, and the sweep if it is early enough."""
    tip_u = _aware(tip)
    out = [(tip_u - timedelta(minutes=m), lab) for lab, m in OFFSETS_MIN.items()]
    sweep = datetime.combine(day, SWEEP_ET, tzinfo=ET).astimezone(UTC)
    if tip_u - sweep >= SWEEP_MIN_GAP:
        out.append((sweep, "sweep"))
    return out


def required_handles(allow: Allowlist, team_id: int) -> tuple[str, ...]:
    own = [r.handle for r in allow.reporters.values() if r.team_id == team_id]
    return tuple(sorted(own or [r.handle for r in allow.reporters.values() if r.team_id is None]))


def plan_due(
    allow: Allowlist,
    games: list[ScheduledGame],
    now: datetime,
    polls: list[rs.PollRecord],
) -> list[DuePoll]:
    """Which handles should be polled at this tick (pure; no I/O, no request)."""
    now = _aware(now)
    slate = slate_for(games, now)
    if not slate:
        return []
    day = now.astimezone(ET).date()
    team_tip: dict[int, datetime] = {}
    for g in slate:
        for tid in (g.home_team, g.away_team):
            team_tip[tid] = _aware(g.tipoff)
    last_tip = max(_aware(g.tipoff) for g in slate)
    due: list[DuePoll] = []
    for rep in allow.reporters.values():
        if rep.team_id is not None:
            if rep.team_id not in team_tip:
                continue
            tip = team_tip[rep.team_id]
            cands = _candidates(tip, day)
            deadline = tip
        else:
            cands = [c for g in slate for c in _candidates(g.tipoff, day)]
            deadline = last_tip
        ready = [c for c in cands if c[0] <= now < deadline]
        if not ready:
            continue
        due_at, label = max(ready)
        mine = [p for p in polls if p.handle == rep.handle and _aware(p.started_at) >= due_at]
        if any(p.ok for p in mine):
            continue
        if sum(1 for p in mine if p.label == label) >= MAX_TRIES:
            continue
        due.append(DuePoll(label, rep.handle, rep.team_id, due_at))
    return sorted(due, key=lambda d: (d.due_at, d.handle))


# ----------------------------------------------------------------------------------------------
# Lock and heartbeat
# ----------------------------------------------------------------------------------------------


def acquire_lock(lock: Path) -> bool:
    """``mkdir`` lock with the owner pid inside; a dead owner's lock is cleared, a live one is
    never stolen."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock.mkdir()
    except FileExistsError:
        try:
            owner = int((lock / "pid").read_text().strip())
            os.kill(owner, 0)
            return False
        except (OSError, ValueError):
            (lock / "pid").unlink(missing_ok=True)
            try:
                lock.rmdir()
                lock.mkdir()
            except OSError:
                return False
    (lock / "pid").write_text(str(os.getpid()))
    return True


def release_lock(lock: Path) -> None:
    (lock / "pid").unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        lock.rmdir()


def write_heartbeat(
    hb_dir: Path,
    start: datetime,
    end: datetime | None,
    rc: int | None,
    detail: str,
    extra: dict[str, Any] | None = None,
) -> None:
    hb_dir.mkdir(parents=True, exist_ok=True)
    rec: dict[str, Any] = {
        "job": "reporters",
        "mode": "tick",
        "host": socket.gethostname(),
        "start_ts": _aware(start).isoformat(),
        "end_ts": _aware(end).isoformat() if end is not None else None,
        "rc": rc,
        "detail": detail,
    }
    rec.update(extra or {})
    tmp = hb_dir / "reporters.json.tmp"
    tmp.write_text(json.dumps(rec, sort_keys=True))
    os.replace(tmp, hb_dir / "reporters.json")


def append_alert(alerts: Path, marker: str, message: str, now: datetime) -> bool:
    """One line in ``ALERTS.md`` per ``marker`` (never repeated). True if written."""
    alerts.parent.mkdir(parents=True, exist_ok=True)
    if alerts.exists() and marker in alerts.read_text():
        return False
    with alerts.open("a") as f:
        f.write(f"- {now.astimezone().strftime('%Y-%m-%d %H:%M')} [reporters] {marker} {message}\n")
    return True


# ----------------------------------------------------------------------------------------------
# The tick
# ----------------------------------------------------------------------------------------------


@dataclass
class TickResult:
    rc: int
    detail: str
    due: list[DuePoll] = field(default_factory=list)
    outcomes: list[HandlePoll] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    coverage_rows: int = 0
    alerts: int = 0


def recent_raw_posts(raw_dir: Path, now: datetime, days: int = RECOVER_DAYS) -> list[Post]:
    """Every post in the raw files of the last ``days`` UTC days, one per post_id (the earliest
    capture), in a fixed order."""
    keep = {(_aware(now) - timedelta(days=d)).strftime("%Y-%m-%d") for d in range(days)}
    seen: dict[str, Post] = {}
    for day_dir in sorted(raw_dir.glob("*")):
        if day_dir.name not in keep or not day_dir.is_dir():
            continue
        for f in sorted(day_dir.glob("*.parquet")):
            for p in read_raw(f):
                cur = seen.get(p.post_id)
                if cur is None or p.fetched_at < cur.fetched_at:
                    seen[p.post_id] = p
    return sorted(seen.values(), key=lambda p: (p.fetched_at, p.post_id))


def derive_facts(con: Any, raw_dir: Path, now: datetime, ctx: rf.Context) -> dict[str, int]:
    """Re-derive facts from the recent raw files into the store (idempotent)."""
    posts = recent_raw_posts(raw_dir, now)
    if not posts:
        return {}
    since = min(p.created_at for p in posts).strftime("%Y-%m-%d")
    first_seen = rs.first_seen_index([raw_dir, raw_dir.parent / "raw_backfill"], since_day=since)
    facts, rejects, counts = rf.pretip_facts_for_posts(posts, ctx, first_seen)
    claimed, _ = rf.claims_for_posts(posts, ctx)
    cfacts = rf.claimed_store_facts(claimed, first_seen)
    written, dup = rs.insert_facts(con, [*facts, *cfacts])
    rs.insert_rejects(con, rejects, _naive(now))
    out = dict(counts)
    out["facts_written"], out["facts_duplicate"] = written, dup
    return out


def run_tick(
    *,
    now: datetime,
    allow: Allowlist,
    games: list[ScheduledGame] | None,
    make_client: Callable[[], XClient],
    make_ctx: Callable[[], rf.Context],
    store_path: Path,
    data_dir: Path,
    ops_dir: Path,
    alerts_path: Path | None = None,
) -> TickResult:
    """One scheduled tick: plan, poll, log, derive facts, finalise coverage, write the heartbeat.
    Always writes a heartbeat END (a refusal has a nonzero ``rc`` the watchdog alerts on)."""
    now = _aware(now)
    hb_dir = ops_dir / "heartbeat"
    write_heartbeat(hb_dir, now, None, None, "start")
    lock = data_dir / "poll.lock"
    if not acquire_lock(lock):
        write_heartbeat(
            hb_dir, now, datetime.now(UTC), RC_OK, "skipped: another tick holds the lock"
        )
        return TickResult(RC_OK, "skipped: lock held")
    res = TickResult(RC_OK, "idle")
    try:
        res = _tick(now, allow, games, make_client, make_ctx, store_path, data_dir, alerts_path)
    except KeyMissingError as exc:
        res = TickResult(RC_REFUSED, f"refused: {exc}")
    except rs.StoreError as exc:
        res = TickResult(RC_STORE, f"store: {exc}")
    except BudgetExceeded as exc:
        res = TickResult(RC_REFUSED, f"budget: {exc}")
    except Exception as exc:  # a tick that crashes still leaves a heartbeat with a nonzero rc
        log.exception("reporters tick failed")
        res = TickResult(RC_STORE, f"crashed: {type(exc).__name__}: {exc}")
    finally:
        release_lock(lock)
    ok_by_team: dict[str, str] = {}
    for o in res.outcomes:
        if o.ok:
            ok_by_team[o.handle] = _naive(o.started_at).isoformat()
    write_heartbeat(
        hb_dir,
        now,
        datetime.now(UTC),
        res.rc,
        res.detail,
        {
            "polled": [d.handle for d in res.due],
            "last_ok_poll": ok_by_team,
            "refusals": [o.handle for o in res.outcomes if o.status.startswith("refused")],
            "coverage_rows": res.coverage_rows,
        },
    )
    return res


def _tick(
    now: datetime,
    allow: Allowlist,
    games: list[ScheduledGame] | None,
    make_client: Callable[[], XClient],
    make_ctx: Callable[[], rf.Context],
    store_path: Path,
    data_dir: Path,
    alerts_path: Path | None,
) -> TickResult:
    if games is None:
        return TickResult(RC_NO_SCHEDULE, "schedule cache missing: nothing can be planned")
    slate = slate_for(games, now)
    if not slate:
        return TickResult(RC_OK, "idle: no games on today's ET slate")
    con = rs.open_store(store_path)
    try:
        polls = rs.read_polls(con, since=_naive(now) - timedelta(hours=36))
        due = plan_due(allow, games, now, polls)
        res = TickResult(RC_OK, "no poll due", due=due)
        if due:
            client = make_client()
            client.budget.per_run = max(client.budget.per_run, allow.posts_per_day)
            result: PollResult = client.poll([d.handle for d in due], isolate_errors=True)
            res.outcomes = result.outcomes
            label_of = {d.handle: d for d in due}
            reached = set()
            for o in result.outcomes:
                reached.add(o.handle)
                rs.log_poll(
                    con,
                    rs.PollRecord(
                        label_of[o.handle].label,
                        o.handle,
                        o.team_id,
                        _naive(o.started_at),
                        _naive(o.ended_at) if o.ended_at else None,
                        o.status,
                        len(o.posts),
                        o.pages,
                        _naive(o.start_time),
                        o.detail[:300],
                    ),
                )
            for d in due:
                if d.handle not in reached:
                    rs.log_poll(
                        con,
                        rs.PollRecord(
                            d.label,
                            d.handle,
                            d.team_id,
                            _naive(now),
                            _naive(now),
                            "refused:not_reached",
                            0,
                            0,
                            None,
                            (result.stopped or "")[:300],
                        ),
                    )
            if result.posts or any(o.ok for o in result.outcomes):
                res.counts = derive_facts(con, client.raw_dir, now, make_ctx())
            bad = [o for o in result.outcomes if not o.ok]
            res.detail = (
                f"polled {len(due)} handles, {len(result.posts)} posts, "
                f"{len(bad)} not ok" + (f" ({bad[0].status})" if bad else "")
            )
        polls = rs.read_polls(con, since=_naive(now) - timedelta(hours=36))
        res.coverage_rows, res.alerts = _coverage(con, allow, slate, polls, now, alerts_path)
        return res
    finally:
        con.close()


def _coverage(
    con: Any,
    allow: Allowlist,
    slate: list[ScheduledGame],
    polls: list[rs.PollRecord],
    now: datetime,
    alerts_path: Path | None,
) -> tuple[int, int]:
    """Final coverage rows once T-60 has passed; one alert line at T-55 for a miss."""
    rows = alerts = 0
    for g in slate:
        tip = _aware(g.tipoff)
        for tid in (g.home_team, g.away_team):
            cov = rs.compute_coverage(
                g.game_id, tid, _naive(g.tipoff), required_handles(allow, tid), polls
            )
            if now > tip - timedelta(minutes=60) and rs.write_coverage(con, cov, _naive(now)):
                rows += 1
            if (
                alerts_path is not None
                and tip - ALERT_AT <= now < tip
                and cov.required  # a team nobody covers is counted, not alerted every night
                and not cov.covered
                and append_alert(
                    alerts_path,
                    f"coverage {g.game_id} team:{tid}",
                    f"no ok poll of {','.join(cov.required) or 'any handle'} in [T-90, T-60]; "
                    "its facts cannot count for the T-60 arm",
                    now,
                )
            ):
                alerts += 1
    return rows, alerts
