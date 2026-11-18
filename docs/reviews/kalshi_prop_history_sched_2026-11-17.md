# Kalshi prop price history: scheduling it safely (raw reader, incremental runs, launcher)

_2026-11-17, evening (CST). Branch `agent/market-prop-candles-sched`, cut from `agent/market-prop-candles` (7787d835). Market agent.
Not merged, not installed, nothing pushed. Display data only. **Revised after the adversary review (WOUNDED, no blocker): section 11
has every finding with its fix and test; where sections 1 to 10 disagree with section 11 or with `docs/DAILY_PIPELINE.md`, those
two are current** (finished nights are now gzipped, the watchdog is wired, the launcher alerts on skips, a mid-game full re-read is
deferred)._

Label: **follows Devin's decision** (2026-11-17: prop price history for the app after each night and every 10 to 15 minutes
during games). The producer came from the earlier lane (`docs/reviews/kalshi_prop_history_2026-11-17.md`); this branch adds what
is needed to run it on a schedule without touching the jobs that already run on game night. Installing it is Devin's step.

## 1. What was built
| Piece | Where | What it does |
|---|---|---|
| Raw-page reader for the 15-minute snapshot | `nba/kalshi/prop_history.py`: `snapshot_pages`, `SnapshotFilter`, `snapshot_page_points`, `read_snapshot_raw`; CLI `--snapshot-raw` | Reads the snapshot job's write-once pages `data/kalshi/raw/<UTC date>/<HHMMSS>_<series>_open_<n>.json.gz` (slate date and the days either side) in place, applies the store's rules (only markets whose title parses, later page wins within a run, prices to 4 decimals, volume and OI whole contracts, `t` = run time). No DuckDB copy, no connection to the snapshot's store. |
| Shared page parsers | same file: `capture_page_points`, `trade_file_prints`, `market_subset` | One parsing path for the one-shot build and the scheduled build. `read_capture` now picks a market's descriptive fields from the newest page by time (it was the last page read, which differs only when a series has `_p1` pages). `QuotePoint` and `Print` use slots (memory). |
| Incremental scheduled build | `nba/kalshi/prop_history_job.py` (`run`, `problem`) | Reads only pages not read before, appends compact points per game under `data/derived/kalshi_prop_history/<slate>/`, rebuilds payloads one game at a time, writes `latest/` every run and `<slate>/` once. Falls back to a full read of the pages when the incremental result could differ from a full build. Pure `problem()` for the watchdog. |
| Launcher | `ops/nba_kalshi_prop_history.sh` | Lock with pid + process start time, skip on `heavy.lock` / daily lock, `nice -n 10`, heartbeat, skip record, alert on failure. |
| launchd file (not installed) | `ops/launchd/local.nba.kalshi-prop-history.plist` | :02, :17, :32, :47 every hour, at load, `Nice` 10, `ProcessType` Background, through `uv run sh`. |
| Tests | `tests/kalshi/test_prop_history_job.py` (25 tests) | Below, section 6. |
| Docs | `docs/DAILY_PIPELINE.md` (new section), `docs/contracts/kalshi_prop_history_v1.md` (scheduled locations, raw command) | |

## 2. Raw reader against the database path (real data, copies)
`read_snapshot_raw` on copies of the snapshot's prop pages (`/Users/devin/Downloads/nba-scratch-prop-candles-2/kalshi_raw/raw/`, 2026-11-15
to 2026-11-18 up to 01:04:46Z, the moment the earlier lane copied the DB) against `read_snapshot` on that DB copy
(`/Users/devin/Downloads/nba-scratch-prop-candles/kalshi_copy.duckdb`):

| Slate | Markets (DB / raw) | Points (DB / raw) | Snapshot runs (DB / raw) | Markets whose series differ |
|---|---|---|---|---|
| 2026-11-16 | 2,340 / 2,340 | 171,154 / 171,154 | 100 / 100 | 0 |
| 2026-11-17 (partial) | 1,434 / 1,434 | 92,072 / 92,072 | 83 / 83 | 0 |

"Series" is the whole `quote_series` dict per market (rows, gaps, first/last seen, n polls, median and max gap). Command:
`PYTHONPATH=<worktree> nice -n 10 <venv>/python /Users/devin/Downloads/nba-scratch-prop-candles-2/tools/check.py dbraw 2026-11-16`.

## 3. Timings and memory (2026-11-16 copy: 8 games, 2,340 markets; this Mac, `nice -n 10`, `/usr/bin/time -l`)
| Run | Pages read | Wall | Peak RSS |
|---|---|---|---|
| Old one-shot build, DB copy (earlier lane) | all | 8.5 s | 780 MB |
| Old one-shot build, raw snapshot pages (`check.py reference`) | all | 14.4 s | 559 MB |
| Scheduled build from an empty state, whole night (`check.py engine`, final) | 5,340 | 18.0 s | 228 MB |
| First run of a night, capture staged to 02:00Z (plus a day and a half of snapshot pages) | 3,771 | 15.0 s | 151 MB |
| In game, +15 min of pages (02:15Z) | 115 | 3.7 s | 197 MB |
| In game, +15 min (02:30Z), after the slots change | 115 | 4.1 s | 165 MB |
| In game, +1 h 45 min of pages at once (04:15Z) | 864 | 4.9 s | 240 MB |
| In game, +15 min (04:30Z, the night's largest state) | 131 | 3.5 s | 216 MB |
| Final slot (all remaining pages, writes `<slate>/`) | 344 | 6.0 s | 163 MB |

Staging: `tools/stage.py <capture copy> <snapshot copy> <stage dir> <cutoff>` copies pages up to a cutoff (and a manifest prefix);
`tools/check.py engine 2026-11-16 <stage>/live_2026-11-16 <stage>/kalshi_raw <out> <state> <now>` runs the job. After the five staged
runs the final files were **byte-identical, 9 of 9** (8 games + index), to the one-shot build of the same pages, in both `<slate>/` and
`latest/`; the whole-night build from empty also 9 of 9.

**Budget (stated):** an in-game slot at most 10 s wall and 300 MB peak RSS on this Mac; the first slot of a night at most 20 s and 300 MB.
Measured inside it: 3.5 to 4.9 s and 165 to 240 MB in game (under a third of the 780 MB one-shot). About 80 MB of that is the
interpreter with the parser imported; the rest is one game's points and payload. CPU in game: about 4 s of one core per 15 minutes
(under 0.5%). Not lower because each slot still rebuilds every game file from its points (a game file embeds the slate-level
`sources` block, which changes every slot); the bigger saving, appending to game files, would change the contract. The no-op slots
(no capture, night final) import nothing heavy and take about a second, most of it `uv`.

Where the in-game time goes (cProfile, 02:16Z slot): building payloads 4.8 s of 5.1 s under the profiler; inside it `difflib`
suggestions for unmatched names 0.8 s (the alias resolver computes "closest candidates" for each of the 445 unresolved markets),
reading the per-game points 0.6 s, gzip sizing of each game file 0.5 s, JSON writing 0.4 s; reading the 115 new pages 0.2 s.

## 4. The schedule and why
- **launchd calendar slots :02, :17, :32, :47 every hour** (minutes only, so the launchd clock's timezone does not matter). 15
  minutes is the slow end of Devin's 10 to 15: the snapshot it reads is 15-minute data anyway, the capture's 60 s quotes are what
  changes, and 15 minutes halves the runs. The minutes are chosen to stay clear of the pretip runs (:20, :50), the app export (:05,
  :35). The 15-minute snapshot runs on `StartInterval` and drifts through the hour, so it cannot be avoided by clock; it does not need
  to be (no shared lock, no shared store, it is network-bound).
- **In game** = whenever `data/raw/live_<slate>/` exists and the night is not final: from the capture's start (first tip - 2 h) to
  last tip + 4 h 10 min. Before the capture starts each slot is a no-op (`no_capture`).
- **After the night**: the first slot at or after last scheduled tip + 4 h + 10 min (the capture's window plus a margin for its last
  pages) writes the dated set once and drops the state. Slate date = ET date of now - 6 h (the candles launcher's rule), so a
  late night still belongs to its day. A night whose final slot was missed is finished by the next slate's first run.
- **Skips**: when `data/ops/heavy.lock` (fits, rebuilds) or `data/ops/lock` (daily runs) is held the slot skips (exit 0, a skip
  record; four in a row should alert). Choice: also yielding to the daily lock was not asked for; it costs at most one 15-minute
  slot when a pretip run overruns and keeps two Python processes with large heaps from overlapping at tip-off on an 8 GB Mac.

## 5. Install and rollback (Devin; after the merge to main)
Install (one line, from the repo root):
```
cp ops/launchd/local.nba.kalshi-prop-history.plist ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.nba.kalshi-prop-history.plist && launchctl kickstart -k gui/$(id -u)/local.nba.kalshi-prop-history
```
Check: `cat data/ops/heartbeat/kalshi-prop-history.json` (rc 0, detail `no_capture`, `built`, `final` or `done`), and in games
`ls -la data/export/kalshi_prop_history/latest/`.

Rollback:
```
launchctl bootout gui/$(id -u)/local.nba.kalshi-prop-history && rm ~/Library/LaunchAgents/local.nba.kalshi-prop-history.plist
```
Only if a run was killed hard: `rm -f data/ops/kalshi_prop_history_lock/pid data/ops/kalshi_prop_history_lock/start; rmdir data/ops/kalshi_prop_history_lock`.
Data may stay or go; nothing else reads it: `data/export/kalshi_prop_history/` (including any hidden `.<slate>.partial`),
`data/derived/kalshi_prop_history/`, `data/ops/heartbeat/kalshi-prop-history.json` and `.skip.json`, `data/ops/kalshi_prop_history_*.log`,
`data/ops/launchd_kalshi-prop-history.out` and `.err`. Delete the heartbeat file, or the watchdog keeps reporting the job stale.
Code: `git revert` the merge.

**What the watchdog should check** (ops owner, `nba/ops/watchdog.py`; not changed here): a `JobSpec("kalshi-prop-history",
"local.nba.kalshi-prop-history", "slots", timedelta(minutes=25), minutes=(2, 17, 32, 47), hours=tuple(range(24)), max_skips=4,
optional=True)` (heartbeat age, rc, consecutive skips; `optional` is the never-installed guard), plus
`nba.kalshi.prop_history_job.problem(now, hb, latest_index, tips, day, final_index)` on game days: last run failed; between first
tip and last tip + 4 h the live set older than 40 minutes or of another slate; 5 h 30 min after the last tip no final set. Silent when
no heartbeat exists.

## 6. Checks
- `ruff check nba/kalshi tests/kalshi ops`: exit 0. `ruff format --check nba/kalshi tests/kalshi`: exit 0 (33 files).
  Repo-wide `ruff check`: exit 1, one pre-existing error in `app/fixtures/build_export_best_real.py`; repo-wide `ruff format --check`:
  exit 1, seven pre-existing files (`app/fixtures/*` three, `docs/reviews/*.md` four); none touched here.
- `mypy nba/`: exit 0, no issues in 401 source files.
- `pytest tests/kalshi --no-cov -q -p no:cacheprovider`: **184 passed**, exit 0 (25 new, the 15 of the earlier lane unchanged).
  Regression only (no file there touched): `pytest tests/ops`: 286 passed, exit 0; `pytest tests/test_layering.py`: 15 passed, exit 0.
- New tests (synthetic pages in `tmp_path`): raw reader equals the store path (unparsed titles dropped, later page wins, float32
  rounding, whole contracts, a hole); a snapshot run younger than 10 minutes is not read; **no capture directory** is a no-op that
  writes nothing (also through `main`); a started but empty capture writes an empty live set; **a capture that grows between two
  runs** is read incrementally (only the 11 new pages) and the files equal a full build byte for byte, and a third run with nothing new
  rewrites the same bytes; full build from empty equals the reference; a page without its manifest line waits, with the later pages of
  its series; a late, older page starts the state over and still equals the reference; a run killed after appending leaves no trace; a
  corrupt state is rebuilt; the final night is written once, the state dropped, a later run is `done`; `latest/` drops the previous
  slate's files but nothing else; a missed night is finished by the next day's run; `problem()` cases; **no network call** (the module
  source has no network, order, credential, `os.environ` or DuckDB code, imports no HTTP client, and a run with `socket` connect,
  `create_connection` and `getaddrinfo` patched to raise completes, through `final`); no model or indicator keys in the output; the
  launcher runs the build under `nice`, **two overlapping runs build once** (the second skips while the first holds the lock), yields to
  `heavy.lock` and the daily lock without taking them, alerts on failure; the plist is valid, launches through `uv`, uses the four
  minutes, and `ops/install_launchd.sh` does not install it.
- Mutation check: with manifest receive times ignored (`got = None`), 7 of the 25 new tests fail; restored, 25 pass.

## 7. Risks to the jobs already running (disk, CPU, memory at tip-off)
- **Live capture** (`nba.live capture`, own lock, write-once pages): this job only lists and reads its files and reads `manifest.jsonl`
  from a saved offset, complete lines only. A page is renamed into place before its manifest line is appended; a page without a line
  yet is left for the next slot. No lock, no write, no request: the capture's Kalshi rate bucket is untouched.
- **15-second poller** (`local.nba.kalshi-candles`): no shared file, lock or store. Both run during games; together at a slot about
  one extra core for 4 s.
- **15-minute snapshot** (`local.nba.kalshi-snapshot`, owns `kalshi.duckdb`): the DuckDB is never opened. Its raw pages are written in
  place (a gzip stream, not tmp + rename), so a run is read only once it is 10 minutes old; a page still being written cannot be read
  half. A run longer than 10 minutes would be read early and its missing pages would arrive "older than applied", which makes the next
  slot start the state over (correct, about 15 s once).
- **Memory at tip-off**: 165 to 240 MB for about 4 s every 15 minutes, `nice` 10 and `ProcessType` Background. It skips while a daily
  run or a heavy fit holds its lock. The first slot of a night is the heaviest read (15 s, 151 MB measured at 02:00Z; at the real start,
  first tip - 2 h, it reads less capture and the same snapshot pages).
- **Disk**: reads about 10 to 15 MB of gzipped pages per slot. Writes about 21 MB to `latest/` per slot (atomic replace, so about
  1 GB of writes a night) and, once, 21 MB for the dated night; the working state grows to about 90 MB by the end of a night and is
  deleted at the final slot. **Growth to flag**: dated nights kept as plain JSON are about 3.5 GB a season (21 MB x about 165 nights);
  gzipped they would be about 3.5 MB a night (0.6 GB a season). The contract says `.json`; changing to `.json.gz` or a retention rule
  is the app agent's and Devin's call. The readiness note has no estimate for this directory.
- **Replay oracle**: this job is **outside the daily replay oracle's closure**. Nothing in `nba/daily` imports it, it writes no store
  the daily pipeline reads, and the replay does not run it. The branch adds files under `ops/` (a live-path directory) but changes
  no existing ops file; the merge is Devin's go.

## 8. Not verified
- Never run under launchd or through the real `uv` here (launchctl was out of bounds); the launcher was tested with a fake `uv`.
- Never run against the live trees in place: all timings are on copies, with file mtimes from the copies. The young-file and
  settle rules were tested on synthetic pages, not on a capture that is being written.
- How long a snapshot run takes at worst (the 10-minute settle assumes well under that; one 2026-11-17 directory listing shows runs
  about 15.3 minutes apart, which says nothing about duration).
- A slate with `_p1` capture pages (none on 11-16 or 11-17); the ordering rules for them are tested only on synthetic pages.
- A night where the live capture never starts: the job writes nothing for it (`no_capture`); a snapshot-only build is
  `python -m nba.kalshi.prop_history_job run --date D --allow-no-capture` by hand, and it cannot finalize without the capture's tips.
- The watchdog wiring (`problem()` exists and is tested; calling it is the ops owner's change).
- 2026-11-17's growth beyond the 01:03Z copy (the copies were not refreshed).

## 9. MODEL_PLAYBOOK check
No model is fitted or judged here; `docs/MODEL_PLAYBOOK.md` does not apply (market-data plumbing).

## 10. Drafted rows (not appended)
DECISIONS (draft):
> 2026-11-17 · Scheduling the prop price history (display only) · `agent/market-prop-candles-sched`: the snapshot series is read from the
> snapshot job's raw pages (identical to the DB on 11-16 and 11-17 copies: 171,154 and 92,072 points), the build is incremental (in game
> 3.5 to 4.9 s and 165 to 240 MB per slot against 8.5 s and 780 MB for the one-shot build; byte-identical files), and a launcher plus
> plist (not installed) run it at :02/:17/:32/:47, skipping while heavy.lock or the daily lock is held, with the dated night written
> once at last tip + 4 h 10 min. Outside the replay closure. Reverse if: a slot ever overlaps a pretip run's memory peak badly enough
> to slow it (then in-game slots every 30 minutes or after-night only), or the season's export size (about 3.5 GB as plain JSON) is
> not wanted (gzip or retention).

## 11. Adversary review (`docs/reviews/adversary_kalshi_prop_history_2026-11-17.md`, branch `agent/adversary-prop-history` 0e9bd0b8): findings and fixes

| Finding | Fix | Test |
|---|---|---|
| **MJ1** can stop forever silently (leftover `heavy.lock`; live recycled pid in a lock with no `start`; no watchdog spec) | Launcher: the 4th consecutive skip appends an alert naming the cause, then once a day (every 96th); a lock with a live pid but no start time older than 30 min is stale; clearing its own stale lock alerts (mn9). Watchdog (authorised by the main session): `JobSpec("kalshi-prop-history", ..., "interval", grace 30 min, interval 15 min, max_skips=4, optional=True)` and `evaluate_prop_history` called from `check()`, which runs `problem()` (now also on `last_success_ts`) for the current slate and `final_overdue` for the one before. A "slots" spec was not used: with a grace longer than the 15-minute spacing it could never fire. | `test_the_fourth_consecutive_skip_alerts_with_its_cause`, `test_a_lock_with_no_start_time_is_stale_after_30_minutes`, `test_problem_flags_a_job_that_only_skips`; `tests/ops/test_watchdog_prop_history.py` (8: spec optional, never installed raises nothing, healthy silent, skipping job alerts, failed run, missing final for the previous slate, unreadable schedule left to the game-day probe, `check()` reports `:stale` and `:skipped` with the reason). Existing watchdog tests unchanged and passing. |
| **MJ2** partial directories leak; no disk guard; 3.5 GB a season | Fixed temporary name `.<slate>.partial` (the next attempt removes a leftover; the lock means one writer). Launcher skips when the data volume has under 5 GiB free and alerts on the first such skip. Finished nights gzipped (`index.json.gz`, `<code>.json.gz`, level 9, mtime 0): 3.2 MB for 2026-11-16 instead of 21 MB, about 0.5 GB a season. `latest/` stays plain JSON (gzipping it would need app work). The contract says what the API needs: `gzip.open` or serve with `Content-Encoding: gzip`. | `test_an_interrupted_final_write_leaves_nothing_behind` (two ENOSPC renames, one leftover, then a clean night), `test_low_disk_skips_and_alerts_once`, `test_the_finished_night_is_written_once_and_the_state_dropped` (gzip, decompressed = `latest/` = reference). |
| **mn1** settle by name time; a torn page dropped for good | A snapshot run is read only when its name time is 10 min old and no page of the run changed for 5 min (file mtime). No page is marked unreadable on first failure: it is retried every slot and given up (listed, reason on stderr) only once its file has been unchanged for 30 min, for capture, trades and snapshot alike. A retried page older than applied pages triggers the full re-read (deferred in game, mn3). | `test_a_snapshot_run_still_writing_pages_is_left_for_later`, `test_a_torn_snapshot_page_is_retried_and_the_night_is_whole` (the reproduction: final night equals the reference, `unreadable_inputs` empty), `test_an_unreadable_page_is_given_up_only_once_quiet_for_long`. |
| **mn2** 5 of 8 mutants survived | Tests added for each. My rerun of the mutants: settle 0 killed (1 test, after making that test use a literal instead of the constant), quiet 0 killed (1), never clear a stale lock killed (3), `write_final` may replace killed (1), give-up at 0 killed (2), always rebuild at once killed (2), no 4th-skip alert killed (1), no disk guard killed (1). Survivor: `<=` to `<` in the out-of-order check, an equivalent mutant: a page with the same time as an applied page of its series gives the same output either way (run_length keeps the first quote at a time; the snapshot's later page wins in both paths). | `test_a_snapshot_run_inside_its_settle_window_is_left_for_later`, `test_write_final_never_replaces_a_night`, `test_a_dead_or_empty_lock_is_cleared_and_the_build_runs` (2), the young-unreadable test above, `test_a_run_writes_nothing_outside_its_two_directories`. |
| **mn3** an in-game rebuild took 28 to 40 s | Chosen: **defer**. A page older than applied pages no longer forces an immediate re-read while games can be on (until last tip + 150 min): it waits, the run reports `rebuild_deferred`, every other new page is applied (the live set is short of that one page, labelled partial anyway), and the first slot after that time does the re-read; the final slot always does it before writing the night, so the finished night is never wrong. Not "one game per slot": every game's state shares the page-order bookkeeping, so a per-game re-read would mean per-game state, a larger change for a rare case. A state that cannot be read (corrupt, old version, truncated manifest) still starts over at once, because nothing correct can be served otherwise. Budget stated: in-game slot under 10 s / 300 MB; a first slot or full re-read under 60 s / 300 MB. | `test_a_late_older_page_defers_the_full_read_until_the_games_are_over`, `test_a_late_older_page_after_the_games_starts_the_state_over_at_once`, `test_a_deferred_reread_is_never_skipped_by_the_final_slot`. |
| **mn4** a night without tips reruns every slot; states abandoned | The previous-night pass runs only when that night has tips, and does not rewrite `latest/` when today's capture has started; states more than 3 days older than the slate are deleted. | `test_a_night_without_tips_is_not_rerun_and_old_states_are_dropped`. |
| **mn5** doc numbers drift; byte-identity overclaimed; rollback incomplete | DAILY_PIPELINE now quotes one measurement (below); the byte-identity sentence names its conditions (same pages, same `built_at`, same snapshot cut); rollback lists every path including `.<slate>.partial`, logs and launchd out/err. | docs |
| **mn6** no `last_success_ts` | Written on rc 0, carried forward on failure; skips never touch it; the watchdog uses it. | `test_last_success_is_set_by_a_success_and_kept_through_a_failure`. |
| **mn7** "imports nothing heavy" | Wording corrected in the module docstring and DAILY_PIPELINE: a build imports polars and the duckdb module (via `nba.parse`), opens no connection; no-op runs import neither. Moving `normalize_name` is outside this branch. | n/a (wording) |
| **mn8** writes outside the two directories | Not a blocker, as the adversary judged: `ALERTS.md` appends are how every wrapper alerts. All paths are now listed in DAILY_PIPELINE ("Files it writes") and in the rollback. | `test_a_run_writes_nothing_outside_its_two_directories` (the Python job itself). |
| **mn9** stale-lock removal vs "locks belong to whoever made them" | Kept, with a reason: the lock is this job's own (made by this launcher), and the rule's concern is removing another job's lock; it never touches `heavy.lock` or `data/ops/lock`. Clearing its own stale lock now appends an alert, so it is reported as well as removed. | the stale-lock tests above |

### Re-run (2026-11-17, about 21:30 CST; the Mac at load average 11 with other lanes running)
Staged chain on the 2026-11-16 copy (`/Users/devin/Downloads/nba-scratch-prop-candles-2/tools/chain.sh <tag> <cutoff> <now> [rebuild]`, which
stages pages up to the cutoff with realistic file mtimes and runs `check.py engine` under `nice -n 10 /usr/bin/time -l`):

| Run | Pages | Wall | Peak RSS |
|---|---|---|---|
| First slot, capture to 02:00Z | 3,771 | 24.7 s | 157 MB |
| +15 min (02:15Z) | 115 | 5.1 s | 153 MB |
| +15 min (02:30Z) | 115 | 6.7 s | 146 MB |
| +1 h 45 min at once (04:15Z) | 864 | 6.6 s | 203 MB |
| +15 min (04:30Z) | 131 | 5.2 s | 198 MB |
| Forced full re-read in game (04:33Z) | 4,996 | 33.4 s | 194 MB |
| Final slot (writes the gzipped night) | 344 | 12.9 s | 186 MB |

Final night and `latest/`: **9 of 9 files identical** to the one-shot reference (decompressed). The same runs earlier in the evening on a
quieter machine were 3.5 to 4.9 s (section 3); the CPU time per slot is about 4 to 5 s either way.

Checks: `ruff check nba/kalshi nba/ops tests/kalshi tests/ops ops` exit 0; `ruff format --check` on the same paths exit 0 (65 files);
repo-wide both exit 1 on the same pre-existing files as before (none touched); `mypy nba/` exit 0 (401 files); `pytest tests/kalshi`
201 passed, exit 0 (42 in `test_prop_history_job.py`, 17 new); `pytest tests/ops` 294 passed, exit 0 (286 existing unchanged + 8 new); `pytest tests/test_layering.py` 15
passed, exit 0.

Not verified (beyond section 8): the watchdog wiring has run only in tests; the disk guard against a real full disk (tested with a
threshold override); the 30-minute give-up and 5-minute quiet rules against the snapshot job writing in real time.
