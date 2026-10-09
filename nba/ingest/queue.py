"""Sequential, resumable ingest queue for the older-season (history) backfill.

ONE stats.nba.com puller at a time: every step is a child process run to completion
before the next starts, at >= 2.5 s/request, each with the circuit breaker. Steps are
idempotent (cached keys are never refetched), so the queue can be killed and restarted
at any time; ``data/ops/ingest_queue_state.json`` just skips steps already finished OK.

Priority (re-prioritised 2026-10-09, see docs/NEW_DATA_SOURCES.md, "History DB"):

  handover  stop the legacy chain (``run_postgame_backfill.sh``) immediately (resumable)
  a         current tracking, hustle for 2022-2024 only (the screen's need; -> cache)
  b         current-season officials 2022-2024 (referee features) -> ``postgame-load`` when
            nothing holds nba.duckdb
  c         history games 2021, 2020, 2019 -> box scores 2021, 2020, 2019 (-> history DB)
  d         history play-by-play 2021..2019, each parsed (heavy lock)
  e         current officials 2025 (+ load), shots, coaches, matchups (-> cache, each loaded
            only when no other process holds nba.duckdb)
  f         history tracking, hustle (the history DB only holds 2019-2021) (+ load)
  g         2025 (frozen holdout) tracking/hustle LAST

Seasons 2013-2018 are deliberately NOT queued (maintainer decision 2026-10-09: the training
history starts at 2019-20, the first season with official injury-report PDFs).

Older seasons are written ONLY to ``data/history/nba_history.duckdb`` and
``data/history/<source>/`` so production refits never see them.

Run: ``sh ops/run_ingest_queue.sh`` (nohup + caffeinate). Progress: data/ops/ingest_queue.log;
summary: data/ops/ingest_queue_status.md.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from nba.ingest.cache import YIELD_ENV, YIELD_GRACE_ENV

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "data" / "ops"
LOG = OPS / "ingest_queue.log"
STATUS = OPS / "ingest_queue_status.md"
STATE = OPS / "ingest_queue_state.json"
HEAVY_LOCK = OPS / "heavy.lock"
PROD_DB = ROOT / "nba.duckdb"
#: Lock dirs of the daily job / lineups collector: children pause per key while either exists
#: (plus a grace period) so the queue never competes for the ~600 requests/hour/IP quota.
YIELD_PATHS = [OPS / "lock", OPS / "lineups_lock"]
YIELD_GRACE_S = 300
PY = str(ROOT / ".venv" / "bin" / "python")
#: Seconds per request. stats.nba.com enforces a rolling quota of ~600 requests then blocks
#: for 30+ min, so pace below it (~600/h) rather than racing it. Never lower than 2.5.
RATE = "6.0"


HISTORY_SEASONS = [2021, 2020, 2019]  # newest first; 2013-2018 dropped (decision 2026-10-09)
STEP_RETRIES = 4
CHAIN_SOURCES = ("tracking", "hustle")
CURRENT_SCREEN_SEASONS = [2022, 2023, 2024]
CURRENT_DEFERRED_SEASONS = [2025]
CURRENT_OFFICIALS_SEASONS = [2022, 2023, 2024]
CURRENT_AFTER = ("shots", "coaches", "matchups")


def season_str(s: int) -> str:
    return f"{s}-{(s + 1) % 100:02d}"


@dataclass
class Step:
    name: str
    argv: list[str]  # args after ``python -m``; empty for internal steps
    kind: str = "fetch"  # fetch | load | parse | internal
    load_source: str | None = None  # kind == load: target source
    season: int | None = None
    state: str = "pending"  # pending | running | done | failed | deferred | skipped
    progress: str = ""
    started: float | None = None
    done_at: str = ""
    note: str = ""


def ingest(*args: str, history: bool = False) -> list[str]:
    base = ["nba.ingest", "--rate-limit-s", RATE]
    if history:
        base.append("--history")
    return [*base, *args]


def seasons_args(seasons: list[int]) -> list[str]:
    return [a for s in seasons for a in ("--seasons", str(s))]


def build_steps() -> list[Step]:
    steps: list[Step] = [Step("handover", [], "internal")]
    for src in CHAIN_SOURCES:  # screen needs 2022-2024 only; 2025 (holdout) is deferred to the end
        steps.append(
            Step(
                f"cur:{src}",
                ingest("postgame-fetch", "--source", src, *seasons_args(CURRENT_SCREEN_SEASONS)),
            )
        )
    steps += [
        Step(
            "cur:officials",
            ingest(
                "postgame-fetch", "--source", "officials", *seasons_args(CURRENT_OFFICIALS_SEASONS)
            ),
        ),
        Step(
            "load:officials",
            ingest("postgame-load", "--source", "officials"),
            "load",
            load_source="officials",
        ),
    ]
    steps += [
        Step(f"hist:games:{s}", ingest("games", "--season", season_str(s), history=True), season=s)
        for s in HISTORY_SEASONS
    ]
    steps += [
        Step(
            f"hist:boxscore:{s}",
            ingest("boxscore", "--season", season_str(s), history=True),
            season=s,
        )
        for s in HISTORY_SEASONS
    ]
    for s in HISTORY_SEASONS:
        steps.append(
            Step(f"hist:pbp:{s}", ingest("pbp", "--season", season_str(s), history=True), season=s)
        )
        steps.append(
            Step(
                f"hist:parse:{s}",
                ["nba.parse.history", "--season", str(s)],
                "parse",
                season=s,
            )
        )
    steps.append(
        Step(
            "cur:officials:2025",
            ingest(
                "postgame-fetch", "--source", "officials", *seasons_args(CURRENT_DEFERRED_SEASONS)
            ),
        )
    )
    steps.append(
        Step(
            "load:officials:2025",
            ingest("postgame-load", "--source", "officials"),
            "load",
            load_source="officials",
        )
    )
    for src in CURRENT_AFTER:
        steps.append(Step(f"cur:{src}", ingest("postgame-fetch", "--source", src)))
        steps.append(
            Step(
                f"load:{src}",
                ingest("postgame-load", "--source", src),
                "load",
                load_source=src,
            )
        )
    for src in CHAIN_SOURCES:
        steps.append(
            Step(
                f"hist:{src}",
                ingest("postgame-fetch", "--source", src, "--newest-first", history=True),
            )
        )
        steps.append(
            Step(
                f"hist:load:{src}",
                ingest("postgame-load", "--source", src, history=True),
                "load",
                load_source=src,
            )
        )
    for src in CHAIN_SOURCES:
        steps.append(
            Step(
                f"cur:{src}:2025",
                ingest("postgame-fetch", "--source", src, *seasons_args(CURRENT_DEFERRED_SEASONS)),
            )
        )
    return steps


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    OPS.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"{now()} {msg}\n")


def holders(path: Path) -> list[str]:
    """PIDs holding ``path`` open (empty if none / lsof missing)."""
    try:
        out = subprocess.run(["lsof", "-t", str(path)], capture_output=True, text=True, timeout=30)
    except Exception:
        return ["lsof-error"]
    return out.stdout.split()


def chain_state() -> tuple[bool, str | None]:
    """(chain shell running?, source its current python child is fetching)."""
    ps = subprocess.run(["ps", "-eo", "pid,command"], capture_output=True, text=True).stdout
    running = any("run_postgame_backfill.sh" in ln and "sh " in ln for ln in ps.splitlines())
    src = None
    for ln in ps.splitlines():
        m = re.search(r"nba\.ingest --rate-limit-s \S+ postgame-fetch --source (\w+)", ln)
        if m and "--history" not in ln:
            src = m.group(1)
    return running, src


def stop_chain() -> None:
    subprocess.run(["pkill", "-f", "run_postgame_backfill.sh"])
    time.sleep(1)
    subprocess.run(["pkill", "-f", r"nba\.ingest --rate-limit-s 2\.5 postgame-fetch"])
    time.sleep(3)


_PROG = re.compile(
    r"(?:PROGRESS \S+ \S* ?|^\w+: )(\d+)/(\d+).*?fetched=(\d+) failed=(\d+).*?([\d.]+)/s"
)


def parse_progress(line: str) -> tuple[int, int, int, int, float] | None:
    m = _PROG.search(line)
    if not m:
        return None
    i, n, fetched, failed, rate = m.groups()
    return int(i), int(n), int(fetched), int(failed), float(rate)


def fmt_eta(i: int, n: int, rate: float) -> str:
    if rate <= 0 or i >= n:
        return "-"
    h = (n - i) / rate / 3600
    return f"{h:.1f} h"


def read_state() -> dict[str, str]:
    try:
        return dict(json.loads(STATE.read_text()))
    except Exception:
        return {}


def write_state(steps: list[Step]) -> None:
    STATE.write_text(json.dumps({s.name: s.done_at for s in steps if s.state == "done"}, indent=1))


def write_status(steps: list[Step], headline: str) -> None:
    lines = [
        "# Ingest queue status",
        f"Updated: {now()}",
        f"Now: {headline}",
        "",
        "| step | state | progress | note |",
        "|---|---|---|---|",
    ]
    for s in steps:
        lines.append(f"| {s.name} | {s.state} | {s.progress} | {s.note} |")
    STATUS.write_text("\n".join(lines) + "\n")


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------


@dataclass
class Queue:
    steps: list[Step]
    child: subprocess.Popen[str] | None = None
    headline: str = "starting"
    deferred: list[Step] = field(default_factory=list)
    last_status: float = 0.0

    def status(self, force: bool = False) -> None:
        if force or time.monotonic() - self.last_status > 30:
            write_status(self.steps, self.headline)
            self.last_status = time.monotonic()

    def run_child(self, step: Step) -> int:
        cmd = [PY, "-m", *step.argv]
        log(f"START {step.name}: {' '.join(step.argv)}")
        step.state, step.started = "running", time.monotonic()
        self.headline = f"{step.name} (running since {now()})"
        self.status(force=True)
        self.child = subprocess.Popen(
            cmd,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={
                **os.environ,
                "PYTHONUNBUFFERED": "1",
                YIELD_ENV: os.pathsep.join(str(p) for p in YIELD_PATHS),
                YIELD_GRACE_ENV: str(YIELD_GRACE_S),
            },
        )
        assert self.child.stdout is not None
        for line in self.child.stdout:
            line = line.rstrip()
            self.status()
            p = parse_progress(line)
            if p:
                i, n, fetched, failed, rate = p
                eta = fmt_eta(i, n, rate)
                step.progress = f"{i}/{n} fetched={fetched} failed={failed} {rate:.2f}/s ETA {eta}"
                self.status()
                if i % 500 == 0:
                    log(f"{step.name}: {step.progress}")
            elif not line.startswith("FAILED"):
                log(f"{step.name}| {line[:300]}")
                step.note = line[:90]
            else:
                log(f"{step.name}| {line[:200]}")
        rc = self.child.wait()
        self.child = None
        return rc

    def finish(self, step: Step, rc: int) -> None:
        step.state = "done" if rc == 0 else "failed"
        step.done_at = now() if rc == 0 else ""
        if rc != 0:
            step.note = f"rc={rc} (failures/early stop; retried next pass)"
        log(f"END {step.name} rc={rc}")
        write_state(self.steps)
        self.status(force=True)

    def handover(self, step: Step) -> None:
        """Take over from the legacy chain immediately (it is resumable, killed between keys)."""
        step.state = "running"
        running, src = chain_state()
        if running or src is not None:
            stop_chain()
            step.note = f"stopped legacy chain (was on {src}) at {now()}"
        else:
            step.note = "legacy chain not running; nothing to hand over"
        log(f"handover: {step.note}")
        step.state, step.done_at = "done", now()
        write_state(self.steps)

    def try_load(self, step: Step) -> bool:
        """nba.duckdb loads only when nobody else holds it; history loads always."""
        if "--history" not in step.argv and holders(PROD_DB):
            step.state, step.note = "deferred", "nba.duckdb busy; will retry"
            return False
        return True

    def try_parse(self, step: Step) -> bool:
        try:
            HEAVY_LOCK.mkdir()
        except FileExistsError:
            step.state, step.note = "deferred", "heavy.lock held; will retry"
            return False
        return True

    def run_step(self, step: Step) -> None:
        if step.kind == "internal":
            self.handover(step)
            return
        if step.kind == "load" and not self.try_load(step):
            return
        locked = False
        if step.kind == "parse":
            if not self.try_parse(step):
                return
            locked = True
        try:
            rc = self.run_child(step)
            # A blocked/throttled source must not hand over to the next one during the same
            # block: retry the same step (30 min apart) before moving on.
            tries = 1
            while rc != 0 and step.kind == "fetch" and tries < STEP_RETRIES:
                log(f"{step.name}: rc={rc}; retry {tries}/{STEP_RETRIES - 1} in 30 min")
                step.note = f"rc={rc}; sleeping 30 min then retrying"
                self.status(force=True)
                time.sleep(1800)
                rc = self.run_child(step)
                tries += 1
            self.finish(step, rc)
        finally:
            if locked:
                HEAVY_LOCK.rmdir()

    def main(self, passes: int = 3) -> int:
        done = read_state()
        for s in self.steps:
            if s.name in done and s.kind != "load":
                s.state, s.done_at, s.note = "done", done[s.name], "done in an earlier run"
        for p in range(1, passes + 1):
            log(f"=== pass {p}/{passes} ===")
            for s in self.steps:
                if s.state == "done":
                    continue
                self.run_step(s)
                # opportunistically retry deferred loads/parses between steps
                for d in [x for x in self.steps if x.state == "deferred"]:
                    if d is not s:
                        d.state = "pending"
                        self.run_step(d)
            pending = [s for s in self.steps if s.state in ("failed", "deferred", "pending")]
            if not pending:
                break
            log(f"pass {p}: {len(pending)} steps not done: {[s.name for s in pending][:8]}")
            if p < passes:
                self.headline = "sleeping 30 min before retry pass"
                self.status(force=True)
                time.sleep(1800)
                for s in pending:
                    s.state = "pending"
        self.headline = "queue finished"
        self.status(force=True)
        return 0


def main() -> int:
    OPS.mkdir(parents=True, exist_ok=True)
    q = Queue(build_steps())

    def _term(signum: int, _frame: object) -> None:
        if q.child is not None:
            q.child.terminate()
        log(f"signal {signum}; exiting (resumable)")
        sys.exit(1)

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    return q.main()


if __name__ == "__main__":
    raise SystemExit(main())
