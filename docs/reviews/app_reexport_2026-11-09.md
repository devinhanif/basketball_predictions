# Adversary review: afternoon app re-export (branch agent/steward-afternoon-reexport, 4572973)

Date: 2026-11-09 (review run 21:20-21:50 CT). Reviewer role: adversary. Scope: `nba/ops/app_export.py`,
`nba/ops/watchdog.py` (JobSpec `app-export`), `ops/launchd/local.nba.app-export.plist`, `tests/ops/test_app_export.py`,
`docs/DAILY_PIPELINE.md`, `docs/DECISIONS.md` (diff vs main). Live-path change. Replay oracle not run here (main session
runs it). No 2025 data read; no process or launchd job touched; every DuckDB open was `read_only=True` on a scratch copy or a
scratch file.

Verdict: BLOCKED

Adversary verdict: **WOUNDED**. Nothing leaks: the known-at rules live in `nba.export` and hold (planted post-tip and
after-`made_at` quotes are excluded; tested and re-read). The swap is atomic on this Mac. But (a) the stated benefit is much
smaller than the docs claim, because write-on-change keeps most forecast rows at their morning `made_at` and the export only
pairs a line with `received_at <= made_at` of that row; and (b) running the export 20+ times a day during the evening creates
a new failure: a market store held by a capture writer makes the build succeed with **zero lines**, and the wrapper swaps that
export over a good one with rc 0 and no alert. That is the 11-09 symptom (empty swipe deck) coming back by a different route.
Fix F1 and F2 (small, wrapper-only) before install; F3 is the real fix for freshness and belongs to `nba.export` (comes back here).

**Top finding.** The re-export cannot surface most new lines. Replaying today's slate on a scratch copy: lines existed for
366-409 of ~1,012 open primary prop keys during the afternoon, but the export rule pairs lines for only 28 of them at the
10:38 CT build and at most 165 even with a rebuild after every pretip run (table below).

## Playbook check (docs/MODEL_PLAYBOOK.md)

Model class: none. This is an ops / live-path change (scheduling and publication of an existing read-only export), not a model.
Cross-cutting questions: (1) information or capacity: information freshness only, no new information enters; (2) data enough:
n/a; (3) test validity: the tests are unit tests of the gate and swap with a fake builder; they cannot see the write-on-change
interaction or the market-store lock (findings F1, F3); (4) lineage and inherited guards: inherits `nba.export` known-at rules
(`received_at <= made_at`, `< tipoff`, `made_at < tipoff`) and the holdout guard, unchanged; (5) scored against: nothing is
scored; the app shows these rows. Class checks: n/a.

## Findings (severity-ranked)

### F1 BLOCKER: a market-store lock publishes a zero-line export over a good one, silently
* Evidence. `nba/export/build.py` 321-328: when `market_asof.duckdb` stays write-locked past `open_odds_read_only`'s 20 s
  (`nba/export/reader.py` 578-587), `_read_lines` returns `LinesBundle([], 0, "locked")` and the build exits 0.
  `nba/ops/app_export.py` 348-358 swaps any rc 0 build and writes the state file, so the next slot sees "nothing newer" until the
  next pretip END. The writers of that file during the evening: the live capture on every 5-min lineups tick
  (`nba/odds/live_close.py` 760, `open_store` held across `_pull_all`, i.e. across vendor calls), the pulls orchestrator, and the
  pretip `market_capture` step. Before this branch the export ran once at ~10:35 CT with no games near tip; now it runs at
  :05/:35 through the evening, inside the t20/t45/t60 capture windows (`configs/odds_live.yaml` 88-107).
* Fix (app_export.py, after `rc == 0`, before `swap_dir`): read `stage/manifest.json`; if `lines.status != "ok"` and the current
  `latest/manifest.json` has `lines.status == "ok"` for the same `export_date`, do not swap; treat it as `RC_LOCKED` (retry in
  the same loop, then rc 2, alert). Add a test: FakeBuild that writes a manifest with `lines.status: "locked"` over an "ok"
  export must leave `latest` byte-identical and exit 2.

### F2 MAJOR: the export's read-only hold on market_asof outlasts the capture writers' retry budget
* Evidence (scratch copy, idle Mac, warm page cache, so a lower bound): `reader.prop_lines` held the market store read-only for
  **38.3 s** for the 11-09 window (53,785 prop rows, 610,579 exported quotes). The live capture `open_store` gives up after 6 x 5 s
  = 30 s (`nba/odds/live_close.py` 478-490; same in `nba/odds/capture.py` 124-134); `nba.markets capture` (`open_out`,
  `nba/markets/capture.py` 264) does not retry at all. DuckDB lock semantics confirmed in scratch (duckdb 1.5.6): a read-only
  connection in another process makes a writer fail with "Could not set lock ... Conflicting lock is held"; a second reader is fine;
  a writer blocks readers. A capture window is 5 minutes wide and "hit by exactly one tick" (config comment), so a failed open loses
  that snapshot kind for that game (t20 for 7:30 tips lands at 7:07-7:12, inside the :05 build's line read).
* Fix, cheapest first: (a) in `nba.export`, read `live_book_odds` into memory (one filtered `SELECT ... WHERE game_id IN (...)`),
  close the connection, then run the Python pairing loop (hold drops to the query time); or (b) build from a snapshot: `cp`
  `market_asof.duckdb` (4.0 GB, 8.2 s here) only when no `.wal` exists and open the copy; or (c) skip slots whose line-read would
  fall within 10 min of any capture window (fragile; not recommended). (a) is an `nba.export` diff and comes back to the adversary.

### F3 MAJOR (claim, not safety): write-on-change + `received_at <= made_at` caps the freshness gain at ~40%
* Evidence. `nba/daily/store.py` 205-239: a prediction group is not re-inserted when unchanged, so its stored `made_at` stays at
  its last change (today's pretip log: most runs wrote 16 rows and deduped 2,600-2,920). `reader.prop_lines` (reader.py 655-657)
  pairs a quote only if `received_at <= made_at` of that row. Replay on scratch copies of `nba.duckdb` and `market_asof.duckdb`
  (copied 21:23 CT, mtime 20:59, no WAL; queries restricted to `forward_predictions` / `live_book_odds` for the 8 games of
  2026-11-09, model `props_context_residual`; script `q2.py` in the scratchpad):

  | export at (UTC) | open keys | keys with lines under export rule | keys with any pre-tip line received by then |
  |---|---|---|---|
  | 16:38 (today's real build, 10:38 CT) | 1008 | 28 | 366 |
  | 18:35 | 1012 | 48 | 371 |
  | 19:35 | 1012 | 108 | 377 |
  | 21:05 | 1012 | 128 | 377 |
  | 22:35 | 1016 | 150 | 388 |
  | 23:35 (last slot before the first tip) | 1016 | 165 | 409 |

  Descriptive counts from one slate (n = 8 games, ~1,012 keys); not a statistical claim, no CI. The re-export moves the deck from
  28 to at most 165 paired keys; ~245 keys with posted lines stay unpaired all evening.
* The docstring (app_export.py 20-23), DAILY_PIPELINE.md line 11-16 and the DECISIONS row say "the newest pre-tip lines and
  forecasts therefore reach latest after every pretip run". False for deduped rows. Fix the text now (one sentence: "lines are
  paired only up to the forecast row's own made_at; rows unchanged since the morning keep morning lines").
* The real fix is in `nba.export`: pair each latest row with quotes up to the made_at of the newest run that *covered* that game
  (the dedup run), which is honest because the store's own contract says the latest stored row at any cutoff has exactly the
  content the full log would have had. That needs a per-run record (run_id, made_at, games) the export can read; check whether
  the pipeline already stores one before adding it. Known-at change: pre-register, then back to the adversary.

### F4 MINOR: contention with the nba.duckdb writers is tight, not broken
* `connect_with_retry` (nba/db/connect.py) is what writers use; pretip exports `NBA_DB_LOCK_WAIT_S=300` (ops/nba_daily.sh 209),
  not 15 min (the 15-min figure in PROJECT_STATUS is the morning backup's `lsof` loop, 310-312). T-30 waits 600 s, T-15 240 s
  (ops/nba_lineups.sh). A pretip run that cannot open fails its `predict` step (rc non-zero, ALERTS line, no forecasts that slot).
* Observed builds: 189-200 s (11-04 ... 11-09) and 493 s (11-08) wall, start to swap; the read-only hold on nba.duckdb is the
  reader phase only (build.py 382-405), so at most that. Pretip runs end ~:51-:52 (today's log), so the :05 slot starts clear.
* Worst case: 3 lock-retry attempts (each up to 30 s open wait + 60 s sleep) then one build hung to `BUILD_TIMEOUT_S` 900: release
  by ~:24.5, inside pretip's 300 s budget (deadline :25) by ~30 s. `BUILD_TIMEOUT_S` applies per attempt and only a timeout or
  lock ends the loop, so there is no total budget.
* Fix: a total wall deadline for the run, `DEADLINE_S = 600` from start (slot + 10 min), with each attempt's timeout =
  `min(BUILD_TIMEOUT_S, deadline - elapsed)`; give up (rc 2, keep last export) when the remainder is < 240 s. This guarantees release
  before :20 and :50 regardless of retries. Optional and stronger: hold no nba.duckdb lock at all by opening a `cp` snapshot
  (1.7 GB, 3.4 s here) when `nba.duckdb.wal` is absent; the morning backup step already uses this pattern.

### F5 MINOR: no single-instance lock; a manual run can race the launchd run
* launchd does not start a second instance of one label, so two slots cannot overlap. A manual `python -m nba.ops.app_export`
  can: two readers double the hold; an older snapshot that finishes last becomes `latest` and its earlier `build_start_ts`
  overwrites the newer one (self-heals at the next pretip END). Fix: `mkdir data/ops/app_export_lock` with pid, same pattern as
  `acquire_lock` in ops/nba_daily.sh 182-192; a second run records a skip.

### F6 MINOR: orphaned staging directories after a crash
* `.latest.stage.<pid>` (and `.latest.old.<pid>` on the fallback path) are removed only by the same pid. A SIGKILL mid-build
  leaves ~54 MB each. The disk is 96% used (17 GB free before this review's scratch copies, which were deleted). Fix: at start,
  remove `data/export/.latest.stage.*` and `.latest.old.*` whose pid is not alive.

### F7 MINOR: state-file write failure escapes as an uncaught exception
* `_write_json(hb_dir / STATE_FILE, ...)` sits in the `else:` of the swap `try` (app_export.py 354-358); an `OSError` there (disk
  full) is not caught, so the run ends with a traceback and no END heartbeat or ALERTS line. Fix: wrap it in
  `contextlib.suppress(OSError)` with a `say(...)`; the manifest's `made_at` is a safe fallback reference (rebuild_reason 205).

### F8 MINOR: a no-game day is treated as "unknown tips"
* `todays_tips` raises when nothing can be read and returns `{}` when the slate is empty (watchdog.py 464-466). `rebuild_reason`
  treats `{}` like an exception (app_export.py 222), so on a day with no games every pretip END triggers a full build (up to 22
  builds, each holding both DBs). Fix: `if not tips: return False, "no games today"` for the `{}` return, and keep the
  "pretip trigger alone" fallback only for the exception path. Playoff / Cup knockout ids are not `REGULAR_SEASON_PREFIX`, so the
  same applies on those days; decide whether exports should run then.

### F9 MINOR: the last pretip run of a late slate is never exported
* Schedule clock Etc/GMT+4 is fixed: hours 12-22 = 11:05-21:35 ET in winter. Pretip runs until 22:50 schedule (21:50 ET). For a
  22:00 or 22:30 ET tip the 21:50 ET run is never exported (today the last tip was 03:00 UTC = 22:00 ET). Fix: add 23:05 to the
  plist and to `hours`/`minutes` in the JobSpec (or a 23 hour with minute 5 only; the JobSpec takes a product of hours x minutes,
  so a separate slot list or accepting 23:35 too).

### F10 MINOR: alert noise
* A failed re-export does not move the reference, so every later slot rebuilds and fails again: up to ~20 ALERTS lines and 20
  long read holds in one evening for one persistent cause. Fix: after 2 consecutive build failures for the same pretip END,
  record a skip instead of rebuilding until a newer pretip END.
* `max_skips=2` with 30-min slots alerts after 1 h of a held-back morning instead of 2 h. Acceptable (morning ends ~06:15 CT on
  every day since 11-01), noted.

## Check table

| # | Check | Command / evidence | n | Result | Pass |
|---|---|---|---|---|---|
| 1 | DuckDB lock semantics | `python locktest.py` (scratch file, duckdb 1.5.6), exit 0 | 4 opens | RO in another process blocks a writer; RO+RO fine; RW blocks RO | confirms risk |
| 2 | Known-at (planted post-tip / after made_at quote) | `.venv/bin/python -m pytest -q --no-cov tests/export` in the branch worktree | 99 tests | 99 passed, exit 0; `test_prop_lines_are_as_of_each_prediction_and_pre_tip` plants a post-tip quote and an after-made_at quote, both excluded and counted | PASS |
| 3 | Trigger cannot export after the last tip | code read: `rebuild_reason` 222-223; `nba.export` excludes `made_at >= tipoff` rows (reader.py 179) and quotes not `< tipoff` and `< commence_time` | n/a | after the last tip no rebuild; before it, tipped games keep only pre-tip rows | PASS |
| 4 | Atomic swap | `python swaptest.py` (scratch): 299 swaps with a reader thread polling `latest/manifest.json` | 79,559 reads | 0 absent with RENAME_SWAP; fallback path 21,066 / 103,285 absent | PASS on macOS |
| 5 | Freshness claim | `python q2.py` on scratch copies (table in F3) | 8 games, ~1,012 keys | 28 -> 165 paired keys of 366-409 with lines | FAIL (claim overstated) |
| 6 | Market-store hold | `python q3.py` on scratch copies | 610,579 quotes | 38.3 s hold vs 30 s writer budget | FAIL (F2) |
| 7 | Branch tests | `.venv/bin/python -m pytest -q -p no:cacheprovider --no-cov tests/ops/test_app_export.py tests/ops/test_watchdog.py` | 48 | 48 passed, exit 0 (with coverage on: 48 passed, exit 1 from the coverage gate, not a test failure) | PASS |
| 8 | ruff / format | `.venv/bin/ruff check ...` exit 0; `ruff format --check ...` exit 0 (3 changed files) | 3 files | clean | PASS |
| 9 | mypy (item 7) | branch: `.venv/bin/mypy` exit 1, 38 errors in 3 files, all `research/scratch/` (gate_line_move_dnp_fair_2026_10_23.py, gate_line_move_fav_dip_2026_10_21.py, star_floor_gate_2026_10_21.py; 22/13/7 output lines incl. notes); main checkout, same 3 files: exit 1, 38 errors; `git diff --stat main 4572973 -- research/` empty | 756 files | pre-existing on main, not from this branch | n/a |

Not run, per instructions: the replay oracle, any export build against live files, anything touching launchd or running jobs.

## Tests: meaningful?

Mostly. The new tests pin the trigger (pretip END after the last build start), idempotence against the state file, the last-tip
stop, "pretip still running is not a trigger", failed re-export keeps `latest` + alerts + rc 1, and the exchange leaving nothing
behind. Gaps: (1) no test where the build succeeds with `lines.status` "locked"/"absent" (F1); (2) no test of `tips_fn` raising
(unknown tips path) or returning `{}` (F8); (3) no test of the swap-failure branch inside `run` (coverage shows 351-353 unhit) or
the unreadable state file (212-213); (4) the fake builder cannot exercise the real lock or the write-on-change interaction, so the
tests cannot see F2/F3. `_tips` in the test is `# type: ignore[no-untyped-def]`; fine for a helper.

## API across swaps (item 5)

Acceptable. `store_key` keys on the manifest's mtime/size; `ExportStore` verifies sha256 of both files against the manifest and
cross-checks `meta.made_at` against the manifest, so a request straddling an exchange sees either the old or the new export, or a
single 503 ("does not match the manifest") and the next request reloads; a failed reload leaves the key unchanged, so it retries.
The old tables are read once and closed, so `rmtree` of the swapped-out directory is safe. One residual: on the two-rename
fallback (not this Mac) `has_export` can see no manifest and `load_store` serves the **fixtures** for that request and caches them
under key `("",0,0)` until the next manifest appears. Fix if the fallback ever runs on a server: on a configured export dir with no
manifest, raise `ExportRefused` (503) rather than fall back to fixtures.

## Watchdog (item 4)

Correct. Optional job stays silent until a heartbeat exists or the label is loaded (watchdog.py 292). Grace 25 < 30-min slot gap,
and a no-op slot writes START/END in ~1 s, so a skipped launchd slot is caught at slot + 25. Etc/GMT+4 has no DST, matching the
other jobs (see F9 for the ET consequence). A run cannot exceed ~20 min (F4), so it cannot cause a missed slot; with F4's deadline
it cannot exceed 10.

## The single most likely failure

An evening re-export coincides with a live capture holding `market_asof.duckdb` (t20/t45/t60 windows sit inside :05-:15 and
:35-:45), the build records `lines.status = "locked"`, exits 0, and replaces the deck with an empty one until the next pretip run;
the capture side can also lose a 5-minute snapshot because the export's 38 s read outlasts its 30 s retry.

## What was not verified

* The split of the 190-490 s build between the nba.duckdb reader phase and the later phases (would need running the export, which
  reads 2025 box-score display history; not done). F4 bounds it by the whole build.
* How often the live capture actually holds `market_asof.duckdb` at :05-:15 / :35-:45 (needs per-tick write-hold timings from the
  lineups log); F1/F2 are mechanism plus one measured hold, not an observed incident.
* Load-dependent hold times (measured on an idle Mac with a warm cache).
* `renameat2` on Linux (code read only; constants RENAME_EXCHANGE = 2, AT_FDCWD = -100 are right).

## Recommended changes (not applied)

1. F1 guard in `nba/ops/app_export.py` + test (blocker).
2. F4 total deadline (`DEADLINE_S = 600`) + F5 lock + F6 orphan cleanup + F7 suppress + F8 empty-slate skip, same file.
3. F3 text fix in the docstring, DAILY_PIPELINE.md and the DECISIONS row now; F2(a) and F3's pairing fix as a separate,
   pre-registered `nba.export` change, reviewed here before merge (it touches the known-at pairing).
4. F9: add a 23:05 slot (plist + JobSpec + the plist/spec consistency test).

## Drafted DECISIONS row (not appended)

| 2026-11-09 21:50 | Adversary review of agent/steward-afternoon-reexport (docs/reviews/app_reexport_2026-11-09.md): WOUNDED, BLOCKED for install until the wrapper refuses to swap a lines-"locked" export over an "ok" one (F1) and has a total run deadline (F4). Freshness gain is real but capped (28 -> 165 of ~400 keys with posted lines on 11-09) by write-on-change plus received_at <= made_at; the pairing fix is a separate pre-registered nba.export change. Reverse: F1/F4 fixed and re-reviewed. |
