# Hardening review, 2026-10-09 (commits 02570a0, 153a7e1; working tree incl. uncommitted M4; 62d98be)

Reviewer: QA (independent). No source modified. Prior review: `docs/reviews/live_path_review_2026-10-09.md`.
Scope read: `git show 02570a0 153a7e1`, working-tree diffs (`nba/daily/predict.py`, `nba/sim/usage_redistribution.py`,
`nba/parlay/slate.py`), `62d98be` (game-window gate). M4 had NOT landed as a commit; it exists only as uncommitted edits.

Tests run: `pytest tests/daily/test_review_fixes.py tests/daily/test_hardening.py tests/ops tests/parlay/test_pretip_prices.py
tests/lineups tests/daily/test_checkpoint.py` = 90 passed (71 s; the coverage gate fails only because a subset was run);
`tests/daily/test_report_serving_rule.py` = 7 passed. Frozen prereg section sha256 re-computed:
`e323aa48...0bed10a`, matches the header (amendment did not touch it). Probe: DuckDB 1.5.6 on this machine raises
`IOException "Could not set lock on file ... Conflicting lock is held"` for BOTH a second writer and a READ-ONLY open while a
writer is open, so `is_lock_error` matches the real message.

## Verdict on the claimed fixes

| Fix | Result |
|---|---|
| B1 T-30 eligibility | Works. See "B1 detail"; two minor hardening gaps. |
| M1 :20/:50 | Consistent in launchd (`ops/install_launchd.sh:46-54`, 26 entries), VM cron (`ops/vm/install_cron.sh:38`), watchdog (`watchdog.py:64`). T-60 comparator is the freshest eligible row, with the contention caveat M-B. |
| M2 shadow isolation | Works for the exceptions it wraps; silent in alerting (m-3). |
| M3 degraded schedule cache | Works; no staleness bound (m-1). |
| M5 connect_with_retry | Primary writers covered; read-only openers are not (m-4); contention with run-t30 is systematic (M-B). |
| M6 pid locks / skip heartbeats | Works for the normal path; one theoretical race (m-2). |
| M7 pre-tip prices | Works (strict `ts < tip and ts < now`, tests cover both). |
| DST slots | Correct (`watchdog.py:107-114` rebuilds per local day with zoneinfo; test covers fall-back). |
| rc codes | Consistent: 4 informational, 5 degraded, 75 busy; argparse 2 now alerts. One stale doc (m-9). |
| roster cap (full official) | Additive for the recency path (test); context-model path not tested (m-7). |
| injury probe miss-cache | Can hide a legitimately published report (M-A). |
| T-30 roster rate limit | Limiter applied; per-process only (m-8). |

## MAJOR

### M-A. Injury-report negative cache is poisoned by any transient probe failure and by late-posted reports
- `nba/daily/injury.py:101-124` (cache write at the `hit_url is None` branch), `nba/ingest/availability.py:309-345`
  (`probe_report_url`: "any network error ... is treated as 'does not exist'").
- Scenario: at 17:55 ET the probe of the 17:30 slot times out (10 s) or hits a CDN 5xx; `probe` returns False, the slot is
  older than 30 min, so `misses.add(slot)` and the JSON is saved. Every later pretip and the whole rest of the day skips that
  slot, even after the report is really there. The pipeline then falls back to the previous slot's report (hours stale) and
  `slate_report_outs` serves it, so a player ruled OUT in the 17:30 report is predicted as playing. Same effect when the NBA posts a
  report more than 30 min after its slot stamp (`MISS_GRACE_MINUTES = 30` is a guess; the test in `test_hardening.py` uses
  a boolean probe so cannot catch this).
- Not a leak (stale, not future) but it silently degrades the primary arm's key conditioning input, once per transient error,
  with no alert.
- Fix: make the probe tri-state (exists / definitely absent (HTTP 404 or non-PDF 200) / error) and cache only "definitely absent".
  Also expire a cached miss after N hours, or re-probe the newest 2-3 slots every run regardless of the cache.
  Write the JSON atomically (tmp + replace).

### M-B. The :20/:50 pretip slots collide by construction with the T-30 ticks; nobody yields cleanly
- `ops/nba_daily.sh` (pretip holds `nba.duckdb` for the entire `nba.daily run`; `connect_with_retry` default 180 s,
  `nba/db/connect.py:41`), `ops/nba_lineups.sh:83-93` (`run-t30` wait 240 s, run inside the lineups lock).
- T-30 windows for :00 tips are :30-:00, which contain the :50 pretip; for :30 tips the window is :00-:30, which contains :20.
  With any slate that has both :00 and :30 tips (typical), every pretip overlaps an active `run-t30` tick. Whichever opens
  second waits; if the holder runs longer than the other's wait (pretip includes ingest, 48-slot injury probing at 2.5 s each
  on a cold cache, the model fit and two shadow arms; the t30 tick includes a cold T-30 model fit on the first decision of the day),
  the waiter exits rc 75. A lost :50 pretip means the T-60 comparator for every game tipping 20:00 or later that day
  is the :20 run (up to 100 min stale), and a lost t30 run is a missing T-30 row (the next tick may be after tip,
  which records `run_after_tipoff`).
- Second effect: `run-t30` runs inside the lineups lock, after `poll`. While it waits (up to 240 s) plus runs, the next 1-2
  ticks hit `acquire_lock` and are skipped, so lineup polling pauses exactly in the T-30 window. Snapshot gaps lower the
  chance a confirmed lineup is captured before tip-30 and distort the 2.1 announcement-timing descriptive stats.
- Could not verify: actual wall times of the pretip run and a cold `run-t30`. Measure both on the maintainer machine
  (`log` lines in `data/ops`), and compare to 180/240 s.
- Fix options: poll in its own lock/job and run `run-t30` as a separate job; or make `daily run` open the write connection late (after
  the long network phases) and hold it briefly; or raise the pretip wait above the worst-case t30 duration and lower the t30
  wait to tolerate it. Add a log line with queue wait time so contention is measurable.

### M-C. The live jobs execute an uncommitted working tree
- `git status`: `nba/daily/predict.py`, `nba/sim/usage_redistribution.py`, `nba/parlay/slate.py` modified (M4 per-game report rule,
  `as_of` filter), launchd calls `uv run --no-sync python -m nba.daily ...` from the checkout. The forward test is pre-registered; run
  metadata records a git SHA that will not describe the code that produced the rows.
- Review of the M4 edit itself: `serve_pretip_flagged` is built on `latest_pretip_flagged`, applies `as_of <= made_at` and
  `tip - lead` (usable_report_rows), `load_report_rows` does not filter statuses so an upgraded/cleared player correctly leaves the
  OUT set, and 7 tests pass. I found no leak. Remaining risk is only that it changes production outputs without a commit/SHA.
- Fix: commit (or stash) before opening night; make `build_run_metadata` record a dirty-tree flag.

## MINOR

- m-1. Schedule cache has no age or coverage bound. `nba/daily/schedule.py:106-116`, `pipeline.py:212-231`. A multi-day nba_api outage serves a
  week-old schedule: postponed or retimed games get stale tips, which are the leakage gate (`made_at < tipoff`, report cutoff),
  and new games are silently absent. rc 5 alerts, but the run still writes rows. Fix: store `saved_at`; refuse cache older
  than about 36 h, or when the run date's slate is empty/absent in it.
- m-2. Pid-lock stale-clear race (analysis only; I attempted an empirical 3-process test but the sandbox denied the script). If two jobs
  start simultaneously on a dead-owner lock: A `rm pid; rmdir; mkdir; echo pid`, B (already past the owner read) then does `rm -f pid`
  on A's fresh lock, `rmdir` succeeds (now empty), `mkdir` succeeds, so both hold the lock. Needs a crash plus simultaneous starts, and the
  DuckDB lock is the backstop. Also a tiny window between `mkdir` and `echo $$ > pid` (readers treat missing pid as live, which is safe).
  A recycled pid keeps a dead lock alive until 3 h / 20 min (documented). Fix if desired: `mv` the stale dir to a unique name
  instead of rm/rmdir/mkdir.
- m-3. Shadow failures are invisible to alerting. `__main__.py:149-152` prints them; rc stays 0 and the heartbeat is clean. If `_int` or `_lt`
  breaks every day, the prereg shadow arms silently have no rows. Fix: count `shadow_errors` into a one-line `ALERTS.md` entry or a distinct informational rc.
  I could not find a path where a shadow exception mutates primary rows; the remaining non-isolated risks are process-level (OOM/kill in the LT fit)
  and formatting code after the shadow blocks (`pipeline.py` `cache = ... fit_seconds ...:.0f`), which would route the whole slate to the recency fallback.
- m-4. Read-only openers get no retry while a writer is open (verified: read-only also fails): `_checkpoint` (`daily/__main__.py:157`, plain
  `connect(read_only=True)`) and `nba.parlay evaluate` (`ATTACH ... READ_ONLY`, `parlay/__main__.py:293-295`). The parlay step runs right after predict
  inside the pretip lock, but a `run-t30` writer (M-B) fails it with a traceback and no shadow row for that slot. Fix: reuse the lock-error
  retry (or the copy fallback in `nba/markets/asof.py`).
- m-5. `62d98be` game-window gate does not close M5's remainder. It covers only [first tip - 2.5 h, last tip + 0.5 h]; pretips run from 10:20 ET,
  so a queue `nba.duckdb` load started earlier can still hold the file past the 180 s wait; a load already in progress when the window begins is not
  interrupted; `load_tips` fails open (no tips cached, no window) with no log. Also `ET`-day grouping puts a post-midnight tip in the next day's window.
  Keep the operational rule: stop the queue before 2026-10-20.
- m-6. B1 hardening. (a) `_SELECT_ELIGIBLE` trusts the self-reported `prediction.snapshot_fetched_at`; the authoritative value is
  `forward_t30_decisions.snapshot_fetched_at` (written by the same run). A cross-check join would make the eligibility rule tamper-evident.
  In practice the stored value cannot be later than `tip - 30` because `latest_snapshot_before(cutoff)` is strict, and a later snapshot cannot replace it
  (run is idempotent per game via `forward_t30_decisions`). (b) The new test (`test_review_fixes.py:47-61`) covers snapshot < / > cutoff and missing; it has no
  case for `made_at >= tip` (excluded) or for "latest eligible row wins" over two T-30 runs. (c) The eligibility is now effectively tautological for rows written by the
  live path; the real protection is `append_predictions` (made_at < tip) and the strict snapshot cutoff. Fine, but state it.
  (d) Prereg section 2 text (line ~93) says the T-30 arm "reads a newer injury report than T-60", but `t30.py` uses `slate_report_outs(..., made_at)` with the same
  `min(now, tip-60)` cutoff; reconcile the wording (the code is the conservative one).
- m-7. Roster-cap claim "rows inside a fixed 18 unchanged" is tested only for `predict_slate` (recency). The context-residual path builds placeholder rows from that roster
  (`_with_slate_placeholders`); I read the team aggregates (`context_residual.py:178`, `:387`) and they use slate placeholders with stat 0 or availability, so I expect no change,
  but there is no test on `predict_slate_context`. Not verified.
- m-8. T-30 roster call: the limiter is per process, and each 5-min tick is a new process. If a team's official roster failed to fetch, every tick in the window
  retries that team (the failure is not negative-cached). Bounded (30 teams x 2.5 s worst case) but adds to the DB hold time in M-B. Not verified live.
- m-9. Docs: `docs/DAILY_PIPELINE.md:18` still says `run` exits 2 when games tipped (now 4). Prereg wording noted in m-6(d).
- m-10. Degraded ingest (`pipeline.py:153`): an ingest exception can leave partially loaded games (box rows for some players only); I did not verify
  `incremental_ingest` is transactional, and a DuckDB transaction aborted by the exception would break later statements on the same connection.
  Not verified. rc 5 on every pretip during a sustained outage also means an `alert()` notification each run (watchdog dedupes at 6 h; the script does not).
- m-11. `ensure_schedule_tips` (`lineups/__main__.py:52-62`): three failed schedule calls on consecutive 5-min ticks disable schedule tips for the day (a 15-min outage at 09:00 ET
  is enough); `int(fails.read_text() or 0)` raises on a corrupt marker file and would crash the tick before `poll`. Use time-based backoff and tolerate bad content.
- m-12. Skip heartbeat `*.skip.json` persists after the last run of a day (e.g. 21:50 with 3 skips) and re-alerts every 6 h overnight until the next successful acquire.
- m-13. Parlay shadow: `load_open_markets(before=now)` takes the latest price strictly before now, but there is no maximum price age, so a quote hours old can be compared to a fresh
  model probability. Not verified whether `price_singles` bounds it elsewhere.

## Clean categories (with evidence)

- As-of leakage in B1/M7/M4: none found. T-30 snapshot strictly < tip-30 (`lineups/store.py:181-183`), `made_at < tip` enforced at write (`LeakageError`) and in settle;
  injury cutoff `min(now, tip-60)` identical in both arms; parlay prices strictly pre-tip and pre-now with naive-UTC timestamps (kalshi snapshot stamps `datetime.now(UTC)` naive).
- M1 timing: for any tip minute, the newest :20/:50 run is at least 70 min before a :00/:30 tip; worst case 90 min for a tip at :20/:50. Plist, cron and watchdog agree; watchdog grace (25 min) < slot spacing (30 min).
- Lock-error detection and bounded wait: tests are real (a held lock in a subprocess), and the message format matches installed DuckDB.
- Exit-code plumbing: informational 4 only for `predict`/`refs`; argparse 2 and degraded 5 alert; `OK_RC["daily-pretip"] = {0, 4}`; morning script ends with exit 0 so launchctl status stays clean.
- Prereg frozen hash unchanged; amendment is append-only and explains the correction.
- DST: no ambiguous/nonexistent local slot in hours 8-21, `last_due` uses zoneinfo; the ET/CT 1-hour offset is constant across both DST changes.

## Not verified

- Real wall-clock durations of pretip, cold `run-t30`, injury cold-cache probing, and queue loads (drives M-B and m-5).
- Whether the Mac sleeps through slots; launchd coalescing (same caveat as the prior review).
- Real 2026-27 `gameDateTimeUTC` values; whether `live_tips_*.parquet` exists for the game-window gate on opening night.
- Empirical race for m-2 (sandbox denied the helper script). No real DB opened; only the in-memory fixture and tmp-dir DuckDB lock probe were used.
