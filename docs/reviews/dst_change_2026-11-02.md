# DST change 2026-11-01: why the clocks and the stale alerts disagree (diagnosis, 2026-11-02)

Steward diagnosis. No live-path edit, no merge, no commit, no scheduler touched. Evidence is read from
`data/ops/*.log`, `data/ops/heartbeat/*`, `data/ops/ALERTS.md`, the plists in `~/Library/LaunchAgents`,
`data/kalshi/snapshot.{log,err}` and a read-only open of `nba.duckdb` (`forward_predictions`,
`forward_scores`, `forward_scores_elig`; season 2026 only, no 2025 read).

## Bottom line

1. **Nothing was lost and nothing was mis-stamped.** Every job ran at the same UTC instant before and after
   the change; every game that tipped since 11-01 has a pre-tip row, a T-60-eligible row 70 min old, and was scored.
2. **The stale alerts are false and have one cause**: launchd's `StartCalendarInterval` on this Mac behaves as a
   *fixed UTC-4 clock* (it did not follow the fall-back), while the watchdog judges the slots in
   `America/New_York` with DST (`data/ops/schedule_tz`). From 11-01 the two differ by exactly one hour, so every
   slot launchd fires is "one hour early" in the watchdog's frame.
3. **One real, small consequence is coming**: the last pretip slot is now 20:50 ET instead of 21:50 ET, so a
   22:30 / 23:00 ET tip loses its fresher T-60 row (100 / 130 min stale instead of 70). 38 of 883 remaining
   games through 2027-03-13 (28 at 22:30, 10 at 23:00). **First one: 2026-11-04 22:30 ET.**
4. **The 08:20 props_analysis failure is a read/write lock collision with the Kalshi snapshot job**, which
   started 3 s earlier. `nba/parlay/analysis.py:336,339` is the one Kalshi/NBA reader on the pretip path that
   has no lock retry. Report-file refresh only; the 08:50 run rewrote it.

## 1. What clock each component uses

| Component | Clock | Evidence |
|---|---|---|
| launchd calendar jobs (morning, pretip, app-export) | **fixed UTC-4 in effect**: `Hour H` fires at `(H+4):mm` UTC all year | morning start 10-30 07:00 CDT, 10-31 07:00 CDT, 11-01 06:00 CST, 11-02 06:00 CST: all 12:00Z. Pretip first/last start 10-31 08:20 / 20:50 CDT and 11-01 07:20 / 19:50 CST: 13:20Z / 01:50Z both days, 26 runs each day. App-export 11-02 10:35 CST (was 11:35 CDT) |
| launchd interval jobs (kalshi-snapshot 900 s, lineups 300 s, watchdog 600 s, jarvis 120 s, live-capture-start 600 s) | elapsed seconds | not affected |
| `ops/nba_daily.sh` | `date` = Mac local (now CST) for log lines, log file names and heartbeat (`%z` is written, so offsets are explicit); `TODAY_ET` / `YESTERDAY_ET` use `TZ=America/New_York` | `ops/nba_daily.sh:30-31`, `:61` (HB_START). DST-correct |
| `nba_lineups.sh`, `nba_live_capture.sh` | `HOUR_ET` / `TODAY_ET` via `TZ=America/New_York` | DST-correct |
| forward store, schedule, injury probe, lineups source, coverage | naive UTC stored; ET via `ZoneInfo("America/New_York")` (`nba/daily/schedule.py:18`, `nba/lineups/source.py:34`, `nba/ops/coverage.py:39`) | no fixed offsets found in `nba/` (grep for `hours=-4/5/6`, `-04:00`, `EDT/EST` returns only labels) |
| `nba/ops/watchdog.py` slots | `schedule_tz()` = env `NBA_SCHEDULE_TZ`, else file `data/ops/schedule_tz`, else `America/Chicago` (`watchdog.py:44,188-201`). The Mac's file says **`America/New_York`** (written 10-11) | `data/ops/schedule_tz` |
| watchdog game-day probe | ET (`watchdog.py:54`) | fine |
| `nba/explain/slate.py` briefings | `America/Chicago` for the numbers, **hard-coded literal "CDT"** in the text (lines 611, 617, 702-716, 783, 790, 831-841, 868-880) | `reports/briefing/2026-11-02_report.md:3,13,15` says "17:20 CDT" for a CST clock. Cosmetic, not mine to edit |

Why launchd is on a frozen clock: the Mac booted 2026-07-04 01:31 and `/etc/localtime` was re-pointed to
`America/Chicago` on 07-08; launchd (PID 1) kept the zone it captured at boot (Eastern, per the 10-11 finding in
`docs/DAILY_PIPELINE.md` "launchd calendar timezone"). 10-11 could not tell "Eastern with DST rules" from
"fixed UTC-4" because both were EDT. 11-01 can: Eastern with DST would fire `Hour 8` at 13:00Z (07:00 CST); it
fired at 12:00Z. So the note in DAILY_PIPELINE ("fires on Eastern time") is wrong in the way that matters:
it is a fixed offset, not a zone. The mechanism inside launchd is not proven (a captured offset, or next-fire
computed as +24 h from the last fire, both predict the same thing); the *behaviour* is observed on two days and
both predict that it stays at 12:00Z until launchd restarts. Discriminator, free: if the Mac is not rebooted,
`Hour 8` still fires at 12:00Z on 2027-03-14 (which is then 08:00 EDT again).

### Alert by alert

The watchdog prints `last start` in the heartbeat's own offset (CST) and `expected by` in the slot's zone (ET)
(`watchdog.py:328-331`), so "last start 06:00, expected by 08:00" is 07:00 EST vs 08:00 EST: one hour, not two.

| Alert (local time) | Mechanism | DST? |
|---|---|---|
| `daily-morning stale` 11-01 07:51, 13:51, 19:52, 11-02 01:52, 07:52, 13:53 | launchd 12:00Z; watchdog slot 08:00 EST = 13:00Z; start < slot, +45 min grace = 08:45 EST = 07:45 CST. Re-alerts every 6 h (`REALERT_AFTER`) | yes |
| `app-export stale` 11-01 12:11, 14:11, 20:12; 11-02 02:12, 08:12, 12:13, 14:13 | plist `Hour 12/14` fires 16:35Z / 18:35Z; watchdog slots 12:35 / 14:35 EST = 17:35Z / 19:35Z; +30 min grace | yes |
| `daily-pretip stale` 11-01 21:22, 11-02 03:22 | pretip is fine all day (launchd Hour H+1 lands on watchdog slot H); only the tail differs: watchdog still expects 21:20/21:50 EST (02:20Z/02:50Z), launchd's last start is 01:50Z. The 10-min check never lands in the 5-minute window (20:45-20:50 CST) where the 21:20 slot is the due one, so the first hit is for the 21:50 slot at 21:22 CST (reproduced with `evaluate()`) | yes |
| `props_analysis failed` 08:20, `daily-pretip last run exited rc=1` 08:22 | lock collision, section 3; the 08:50 heartbeat cleared the rc | no |
| `kalshi-snapshot stale` 11-02 15:43 (and 10-31 17:30) | interval job, no zone. One 15-min run missing: snapshot log has 21:13:41Z then 21:44:00Z (15:13 -> 15:44 CST). Same failure class as the 7 "Could not set lock" tracebacks in `snapshot.err` since 10-08 (a reader held the file when the writer started). Holder at that minute not identifiable after the fact (no pretip step ran at 15:28; candidates: jarvis every 120 s, live-capture start 15:32) | no |

## 2. Did any job run at the wrong time, skip, or mis-stamp data?

Wrong time: relative to UTC, no. Relative to the ET/CT wall clock, everything moved one hour earlier on 11-01
(morning 08:00 ET -> 07:00 ET; pretip 09:20-21:50 ET -> 08:20-20:50 ET). Consequences, checked:

* **Morning settle before box scores exist: no.** Settle runs after `ingest`; 11-01: `refused_no_box 0, refused_thin_box 0,
  refused_box_mismatch 0, refused_stale 0`, 12 win / 1992 prop rows scored; 11-02: 6 win / 1056 prop rows,
  all refused counters 0. Coverage check `OK scoring: 0 of 1908 forward_scores rows scored before tip+4h`
  (the 4 h guard held). The last game of 11-01 (0022600157, 21:00 ET) ended about 23:30 ET; settle ran 12:00Z.
* **Skips: none.** 26 pretip runs on 10-31 and 26 on 11-01 (full 08:20-20:50 grid), 21 so far on 11-02, no
  `skipped (n consecutive)` line, no `*.skip.json`. Morning steps 11-02: all 22 steps `rc 0 ok`.
  `predict rc=4` after a slate has tipped is the documented informational exit.
* **Forward data (read-only query, 36 games with tipoff 10-30 .. 11-03 UTC)**: `made_at` and `tipoff` are naive UTC
  (written from `datetime.now(UTC)`), identical convention before and after; **0 rows with made_at > tipoff**;
  every game has a pre-tip row. For the games that tipped since the change (0022600154-157, n = 4 games, all
  four scored in `forward_scores`), the latest row with `made_at <= tip - 60 min` is 70 min before tip, the same as
  every game that tipped 10-25 to 11-02 12:00Z (70 min each, n = 61 games, 0 exceptions). `forward_scores_elig` has T-60 and T-15 lead rows for all of them.
  Injury report stamps are ET `as_of` (coverage: "latest usable as_of 17:45 ET = 75 min before tip 19:00 ET" on 11-02),
  so the known-at conversion handled EST.
* **One real change in coverage (not yet a loss).** Under a UTC-fixed launchd the pretip window in ET is
  08:20-20:50 EST. T-60 comparator staleness by ET tip time, now vs the EDT grid: up to 22:00 ET 70 min (same);
  22:30 ET 100 min (was 70); 23:00 ET 130 min (was 70). Remaining games through 2027-03-13 with ET tip >= 22:30:
  38 of 883 (28 + 10), first 2026-11-04 22:30 ET. The row exists and is scored; it is just staler, and the
  FORWARD_PREREG design ("a :00/:30 tip has a run >= 70 min ahead", `docs/DAILY_PIPELINE.md` "Timing: why :20 and :50")
  no longer holds for those tips. The lineups-driven T-30 / T-15 arms are unaffected (interval job).
  Self-heals on 2027-03-14 if launchd is not restarted before then.
* **Cosmetic**: briefings label CST times "CDT" (`nba/explain/slate.py`). Elsewhere `%Z` is used and is right
  (`nba/explain/known.py:28`).

## 3. The lock conflict

`pretip_20261102.log` 08:20:50: `nba.parlay analyze` died at `nba/parlay/analysis.py:339`
`ATTACH '.../data/kalshi/kalshi.duckdb' AS kal (READ_ONLY)`: `Conflicting lock is held in ...python3.12 (PID 2238)`.

* A *read-only* attach conflicts only with a **writer**. The only writer of that file is `python -m nba.kalshi snapshot`
  (`guard_real_db`, `nba/kalshi/__main__.py:91-101`). `snapshot.log` stamps each run with its *start* time:
  `2026-11-02T14:20:47Z` = 08:20:47 CST. The attach was at 08:20:50 CST, inside that run (about 17-19 s long; the
  period is 15 min 17 s, so its phase drifts relative to the :20/:50 pretip grid). Not the ":13" run; that was 15:13 CST,
  a different day-part, and only the last log write before the missing run.
* The other Kalshi/NBA readers on that path already retry: `nba/parlay/__main__.py:300-301` (`parlay_shadow`) and
  `nba/markets/asof.py:67` / `nba/ops/coverage.py:118` use `attach_read_only_with_retry` / copy fallbacks.
  `cmd_analyze` -> `build_analysis` opens both files with bare `ATTACH`. Exposure per slot is about (19 s + 8 s)/917 s = 3 %,
  26 slots a day: roughly one collision every one or two days; this is the first one logged.
* Needs a retry? Yes, the same bounded one `nba.db.connect.attach_read_only_with_retry` gives the sibling step (2 s doubling to
  30 s, total `NBA_DB_LOCK_WAIT_S`, 300 s in `nba_daily.sh`). A snapshot holds 20 s, so the retry succeeds at the second or third try.
  Impact today was nil (report files only; 08:50 rewrote them), but it also sets the pretip heartbeat rc=1 and writes an ALERT.
* The snapshot writer has the symmetric weakness: `nba/kalshi/__main__.py:130` `con = connect(args.db)` fails at once if a reader
  (jarvis `open_ro`, pretip steps) holds the file, losing that 15-min snapshot (7 of ~2400 runs since 10-08, and likely the 15:29 gap).

## 4. Smallest fixes

Which touch the live path (import closure of the `ops/*.sh` entrypoints): `nba/parlay/analysis.py` and
`nba/explain/slate.py` (both run from `nba_daily.sh`). `watchdog.py`, `nba/kalshi/__main__.py`, the plists and
`data/ops/schedule_tz` are independent launchd jobs or machine-local files. None of these feeds a forecast, so the
replay hash should not move, but any `nba/` change still takes the two gates (oracle + `tests/test_layering.py`;
`attach_read_only_with_retry` is already imported by `nba/parlay/__main__.py`, so no new edge expected).

| # | Fix | Where | Owner / who acts | Live path |
|---|---|---|---|---|
| A | Make the watchdog frame match the real launchd clock: `printf 'Etc/GMT+4\n' > data/ops/schedule_tz` (IANA `Etc/GMT+4` = UTC-4, no DST; ZoneInfo accepts it). Silences all three false stale families immediately and is correct all year while launchd is not restarted. After any reboot/relogin run `rm data/ops/schedule_tz` (the plists then mean local Central with DST; re-check the first morning start) | `data/ops/schedule_tz` (untracked) | maintainer (or steward on his go) | no |
| B | Restore the late pretip slots under the fixed clock: pretip hours `9..22` instead of `9..21` (adds 22:20/22:50 launchd = 21:20/21:50 EST). Three places must change together: `ops/install_launchd.sh:51` (`-le 21` -> `-le 22`), `nba/ops/watchdog.py:98` (`range(9, 22)` -> `range(9, 23)`), `ops/vm/install_cron.sh` (same hour list), then `sh ops/install_launchd.sh` by hand. **Do before 2026-11-04 evening.** Harmless extra runs on EDT days and after a Central restart (a spare slot costs about 1 minute and exits 4 when everything has tipped) | scheduler: maintainer. Code: steward | `watchdog.py` no; installer no |
| C | Lock retry for the props analysis: `nba/parlay/analysis.py:336` and `:339` -> `attach_read_only_with_retry(con, nba_db, "nba")` / `(con, kalshi_db, "kal")`; add `from nba.db.connect import attach_read_only_with_retry` after line 21 | steward + gates | yes (pretip step) |
| D | Snapshot waits for a reader instead of failing: `nba/kalshi/__main__.py:130` `connect(args.db)` -> `connect_with_retry(args.db, max_wait_s=60)` for the `snapshot` command only (import at `:32`) | not the steward's area (kalshi); recommend | no |
| E | Watchdog message in one frame: `nba/ops/watchdog.py:330-331` print `start.astimezone(due.tzinfo)` (and the zone abbreviation), so "last start 07:00, expected by 08:00 EST" reads as one hour. Optional: when a stale job's start is within 60 min before the slot, raise one explicit "launchd calendar offset differs from schedule_tz" alert instead of five generic stale alerts | steward | no |
| F | Doc corrections: `docs/DAILY_PIPELINE.md` "launchd calendar timezone" (446-457) and the header of `ops/install_launchd.sh:2` ("Times are the Mac's local time"): launchd is on a fixed UTC-4 clock on this Mac; add the 11-01 observation and the winter ET table (morning 07:00 ET, pretip 08:20-20:50 ET) | steward (docs are mine) | no |
| G | "CDT" literals in `nba/explain/slate.py` -> `%Z` or "Central" | owner of `nba/explain` | yes (briefing text only) |

Alternative to A+B, maintainer's call: reboot or relogin so launchd re-reads Central, then `rm data/ops/schedule_tz`.
Plists then mean Central with DST (pretip 09:20-21:50 CT = 10:20-22:50 ET covers every late tip; morning 08:00 CT = 09:00 ET;
one 10:30 ET tip in the schedule would have no T-60 slot). Unknown: whether a fresh launchd follows DST for these
plists; the first DST day after the restart is the test (2027-03-14). Not recommended mid-week of games without a
look at the watchdog afterwards.

### Test that pins DST behaviour (draft, not added to the tree)

Proposed `tests/ops/test_watchdog_dst.py` (steward-owned area). Verified here in an ephemeral env: 15 passed; ruff check and
ruff format clean. It pins (a) in the zone launchd really uses (`Etc/GMT+4`) the 12:00Z morning start and 01:50Z last pretip are never
stale on 10-31, 11-01, 11-02, 2027-01-15, 03-13, 03-14, 03-15; (b) in `America/New_York` the morning job is falsely stale exactly on
the EST days (2026-11-01 .. 2027-03-13) and no other day, which is the incident; (c) slot arithmetic in UTC across both DST edges.
Note (a) and (c) pass on today's code (the code is right, the machine file is wrong), so what they guard is the frame, not a bug; a
real red test needs fix E (message in one frame) or the offset-mismatch alert.

```python
"""DST pins for the watchdog slot clock (incident 2026-11-01: launchd kept firing at fixed UTC)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from nba.ops.watchdog import JOBS, JobSpec, evaluate, last_due

ET = ZoneInfo("America/New_York")
FIXED = ZoneInfo("Etc/GMT+4")  # UTC-4 all year: what launchd's calendar actually uses here
MORNING = next(s for s in JOBS if s.name == "daily-morning")
PRETIP = next(s for s in JOBS if s.name == "daily-pretip")
JOBS_UNDER_TEST = (MORNING, PRETIP)
MORNING_UTC = (12, 0)  # launchd 'Hour 8'
LAST_PRETIP_UTC = (1, 50)  # launchd 'Hour 21:50', next UTC day


def _hb(d: Path, job: str, start: datetime) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{job}.json").write_text(
        json.dumps(
            {
                "job": job,
                "mode": "x",
                "start_ts": start.isoformat(),
                "end_ts": (start + timedelta(minutes=5)).isoformat(),
                "rc": 0,
            }
        )
    )


def _stale_keys(tmp: Path, day: date, tz: ZoneInfo, *, check_at_utc: tuple[int, int]) -> set[str]:
    morning = datetime(day.year, day.month, day.day, *MORNING_UTC, 3, tzinfo=UTC)
    hb = tmp / f"hb_{day}_{tz.key}"
    _hb(hb, "daily-morning", morning)
    _hb(hb, "daily-pretip", datetime(day.year, day.month, day.day, *LAST_PRETIP_UTC, 3, tzinfo=UTC))
    now = datetime(day.year, day.month, day.day, *check_at_utc, tzinfo=UTC)
    out = evaluate(now, hb, "", now - timedelta(days=3), jobs=JOBS_UNDER_TEST, tz=tz)
    return {a.key for a in out if a.key.endswith(":stale")}


DAYS = [
    date(2026, 10, 31),
    date(2026, 11, 1),
    date(2026, 11, 2),
    date(2027, 1, 15),
    date(2027, 3, 13),
    date(2027, 3, 14),
    date(2027, 3, 15),
]


@pytest.mark.parametrize("day", DAYS)
def test_fixed_offset_zone_never_false_stale(tmp_path: Path, day: date) -> None:
    assert _stale_keys(tmp_path, day, FIXED, check_at_utc=(18, 0)) == set()


@pytest.mark.parametrize("day", DAYS)
def test_eastern_zone_is_wrong_only_while_est(tmp_path: Path, day: date) -> None:
    est = date(2026, 11, 1) <= day <= date(2027, 3, 13)
    keys = _stale_keys(tmp_path, day, ET, check_at_utc=(18, 0))
    assert ("daily-morning:stale" in keys) is est


def test_slot_utc_across_dst_in_each_zone() -> None:
    def due(spec: JobSpec, y: int, m: int, d: int, hh: int, mm: int, tz: ZoneInfo) -> datetime:
        return last_due(spec, datetime(y, m, d, hh, mm, tzinfo=UTC), tz).astimezone(UTC)

    assert due(MORNING, 2026, 10, 31, 20, 0, ET) == datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    assert due(MORNING, 2026, 11, 2, 20, 0, ET) == datetime(2026, 11, 2, 13, 0, tzinfo=UTC)
    assert due(MORNING, 2027, 3, 13, 20, 0, ET) == datetime(2027, 3, 13, 13, 0, tzinfo=UTC)
    assert due(MORNING, 2027, 3, 14, 20, 0, ET) == datetime(2027, 3, 14, 12, 0, tzinfo=UTC)
    for d in ((2026, 10, 31), (2026, 11, 2), (2027, 3, 13), (2027, 3, 14)):
        assert due(MORNING, *d, 20, 0, FIXED) == datetime(*d, 12, 0, tzinfo=UTC)
    assert due(PRETIP, 2026, 11, 2, 12, 0, FIXED) == datetime(2026, 11, 2, 1, 50, tzinfo=UTC)
```

For fix C, a test in `tests/daily/test_overlap_hardening.py` style (its `_holder` helper keeps a writer on a temp file): monkeypatch
`nba.parlay.analysis.attach_read_only_with_retry` with a fake that raises a lock `duckdb.IOException` once and then attaches, call
`build_analysis(...)` on the fixture DB from `tests/fixtures/loader.py::build_fixture_db`, and assert two attach calls per file and no
exception. Written only after C is approved (it needs the import to exist). Not run here.

## 5. Maintainer commands

```
# A (now, reversible): watchdog frame = the clock launchd really uses
printf 'Etc/GMT+4\n' > /Users/devin/Downloads/nba-prediction/data/ops/schedule_tz
# B (before 2026-11-04 evening): after the steward changes the three hour lists, reinstall by hand
sh /Users/devin/Downloads/nba-prediction/ops/install_launchd.sh
# observe: next watchdog check should log no new stale lines
tail -5 /Users/devin/Downloads/nba-prediction/data/ops/ALERTS.md
# gates for C/E (when a branch exists)
uv run pytest tests/test_layering.py --no-cov -q
python -m nba.daily.replay_season --season 2025 --source nba.duckdb --db data/rehearsal/oracle_<n>.duckdb --out-dir data/rehearsal/oracle_<n> --max-dates 20
python -m nba.daily.canonical_dump --db data/rehearsal/oracle_a.duckdb --db data/rehearsal/oracle_<n>.duckdb
```

## 6. Drafted rows (not appended)

* DECISIONS: "2026-11-02 | watchdog schedule frame = fixed UTC-4 (`Etc/GMT+4`) while launchd is unrestarted; extend pretip to hour 22 so tips at 22:30/23:00 ET keep a >= 70 min T-60 row | because launchd's StartCalendarInterval did not follow the 2026-11-01 fall-back | reverse: after a reboot or relogin, delete `data/ops/schedule_tz` and re-check the first morning start; revisit on 2027-03-14."
* BEST_PRACTICES (incident rule): "Calibrate a watchdog's clock against observed heartbeat UTC times, not against a belief about the scheduler's zone. 2026-11-01: a zone name (America/New_York) explained launchd for 21 days and failed on the first DST change; the fixed offset explained both."
* PREDICTIONS_LEDGER: none (no prior stated before a result).
* TEST_LEDGER / HOLDOUT_ACCESS_LOG: none. No 2025 read; 2026 forward tables read only.

## 7. Not done / unknown

* Mechanism inside launchd (captured offset vs +24 h next-fire) not proven; the behaviour is observed on two mornings and predicts the same thing either way.
* Which process held `kalshi.duckdb` for the 15:29 CST snapshot miss is not recoverable (PID 2238 at 08:20:50 is gone; the writer identification for that minute is by timing).
* `ruff` / `mypy` / full `pytest` were not run on the tree: no file in the repo changed. The draft test was run in an ephemeral env only (`.venv` has no pytest/ruff).
