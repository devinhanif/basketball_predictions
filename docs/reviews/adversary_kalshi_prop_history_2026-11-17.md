# Adversary review: Kalshi prop price history, scheduled (`agent/market-prop-candles-sched`, d62f2c1b)

_2026-11-17, evening (CST). Adversary. Worktree `/Users/devin/Downloads/nba-wt-adv-prop-history` (branch `agent/adversary-prop-history`
cut from d62f2c1b). Copies only; no DuckDB under the main checkout opened; no `launchctl`; no network; nothing under `nba/`, `ops/`,
`configs/` written. Scratch: `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/3f32f746-8826-4256-901c-c93a4380dd7f/scratchpad/adv/`._

Verdict: WOUNDED

Merge gate: no blocker. The code is safe to merge, and once installed it cannot hurt a running job: it writes only its own
directories, opens no database, sends nothing, stays out of the replay closure, and holds memory under 250 MB. **Do not install it
until MJ1 and MJ2 are fixed.** As shipped it can stop silently forever (MJ1). It also has no disk guard and leaks hidden directories
when a final write is interrupted (MJ2). The headline correctness claims hold on my own reproduction, with one rare exception (mn1).

Top finding: **as shipped, nothing alerts.** A leftover `heavy.lock`, or a recycled pid in a lock with no `start` file, makes every
slot skip with no alert. I reproduced both: 6 of 6 and 2 of 2 slots skipped, with `ALERTS.md` absent. No watchdog spec covers the job,
and the docs' "four skips in a row should alert" is not implemented anywhere.

## Findings

### Blockers
None. I checked every path the job writes, every database and network call, the readers of the new paths, the replay closure, and
whether the job can disturb the capture, the poller, the snapshot, pretip, the app export or JARVIS. None of those checks found a
blocker (see "What survived").

### Major

**MJ1. Installed as shipped, the job can stop forever without telling anyone.**
- **Where:** `ops/nba_kalshi_prop_history.sh:40-50` (`note_skip` counts skips but never alerts), `:53-54` (skip while `heavy.lock` or
  `data/ops/lock` exists, with no age limit), `:64` (`[ -s "$LOCK/start" ] || return 1`: a live pid with no start file keeps the lock
  forever). No `JobSpec` for `kalshi-prop-history` exists in `nba/ops/watchdog.py`; the branch leaves it to the ops owner. The launcher
  writes failures to `data/ops/ALERTS.md`, but only the watchdog turns alerts into notifications.
- **Scenario 1:** a research fit is killed by OOM or a reboot and leaves `data/ops/heavy.lock` behind. This has happened: a 15-minute
  heavy.lock deadlock on 2026-11-08, `HOLDOUT_ACCESS_LOG.md` row 192. From then on every slot skips. The live set goes stale, no night
  is ever finalized, and nobody is told.
- **Scenario 2:** the launcher is killed between `echo $$ > pid` and writing `start` (`:59`). After a reboot that pid belongs to some
  other live process of the same user. `:64` then returns "held" forever.
- **Reproduced** (`lock_attack.sh`, below): case 7 gave `builds=0 skips=6 ALERTS.md=absent`; case 4 gave `builds=0 skips=2`.
- **Claims contradicted:** sched review section 4 ("four in a row should alert") and `docs/DAILY_PIPELINE.md` ("the watchdog alerts on
  consecutive skips" is implied but not wired). BEST_PRACTICES requires that "a job that cannot start cannot alert" and that health
  means a recent success.
- **Smallest fix (three lines, before install):**
  1. In `note_skip`, after the counter: `[ "$n" -eq 4 ] && alert "skipped 4 slots in a row: $1"`.
  2. Line 64 becomes `[ -s "$LOCK/start" ] || [ -n "$(find "$LOCK" -maxdepth 0 -mmin +30 2>/dev/null)" ] || return 1`, so a lock
     with no start file and older than 30 minutes is stale.
  3. The ops owner adds the drafted `JobSpec` and the `problem()` call to `nba/ops/watchdog.py` in the same install window. The
     heartbeat should carry `last_success_ts` (mn6).

**MJ2. Disk: no free-space guard, no retention, and hidden partial directories leak.**
- **Where:** `nba/kalshi/prop_history_job.py:571` builds the temporary name `out / f".{day.isoformat()}.partial-{os.getpid()}"`.
  Lines 572-573 remove a leftover only if it has *this* run's pid, which never happens. So every final write that fails or is killed
  before the rename at `:577` leaves a hidden copy of about 21 MB that no later run deletes. The rollback instructions do not mention
  these directories.
- **Reproduced** (`attack_c.sh`): two runs with the rename failing on ENOSPC, then one good run. Result: `2026-11-16/` was written,
  and `.2026-11-16.partial-52091` and `.2026-11-16.partial-52238` stayed behind at 21 MB each.
- **Growth by design:** dated nights are kept as plain JSON with no retention, about 3.5 GB a season. The builders disclosed this. The
  launcher has no free-space check, and this Mac was at 97% on 2026-11-10 (66% today). The live capture is the job that loses when the
  disk fills.
- **Smallest fix:**
  1. Line 571 becomes `tmp = out / f".{day.isoformat()}.partial"`. The job's lock guarantees one writer, and the existing
     `rmtree(tmp)` at 572-573 then cleans up after any earlier failure.
  2. In the launcher, before the build:
     `avail=$(df -Pk data | awk 'NR==2{print $4}'); if [ "$avail" -lt 5000000 ]; then note_skip "disk below 5 GB"; alert "disk below 5 GB, prop history paused"; exit 0; fi`
  3. Devin decides between `.json.gz` and a retention rule before install.

### Minor

- **mn1. The snapshot settle is keyed on the page's name time, so a torn page is lost for good.** `prop_history_job.py:398-403`
  filters snapshot pages on name time (the run's start). `:416-420` then marks an unreadable page as done with no age check (the
  capture branch at `:332-333` has one). The snapshot writer (`nba/kalshi/snapshot.py:74-81`) writes with `gzip.open` in place, and a
  page's name carries the run's start time. Measured from real file mtimes (`snapdur.py`, metadata only): **3 of 2,537 runs since
  2026-10-20 wrote pages more than 600 s after their name time**, worst 1,993 s (2026-11-10 05:37Z). If a slot reads during such a
  write, the page is dropped from the state for good and from the write-once dated night. Reproduced (`attack_ab.sh` case b): a
  truncated `015903_KXNBAPTS_open_0` page, restored before the next run, still left the final night missing that run. Result:
  `identical=0` of 9 against the full build, every game's PTS snapshot series `n_polls` 80 vs 81 and `max_gap_s` 1,859 vs 948, and the
  page listed in `unreadable_inputs`. It is visible, not silent, and rare. **Fix:** in the filter at `:399-403` add
  `and _age_s(p.path, now_ts) >= YOUNG_FILE_S`, and in the except at `:417` add
  `if _age_s(p.path, now_ts) < YOUNG_FILE_S: continue` before marking. A late page from an already-applied run then triggers the
  existing `RebuildNeeded`, which is correct. Also correct sched review section 7's "a page still being written cannot be read half".
- **mn2. The tests miss the safety paths. 5 of 8 mutants survive** (`mutate.py`, run on an exported copy):
  - M1 `SNAPSHOT_SETTLE_S = 0` survived.
  - M2 "never clear a stale lock" survived.
  - M3 "`write_final` may replace an existing night" survived.
  - M4 snapshot rebuild check `<` instead of `<=` survived (probably an equivalent mutant).
  - M5 "a young unreadable capture page is marked unreadable at once" survived.
  - M6 final margin removed, M7 heavy.lock yield removed and M8 stale-file removal removed were all killed.

  The builders' own mutation check covered one line. **Fix** (`tests/kalshi/test_prop_history_job.py`):
  - The launcher runs the build when the lock holds a dead pid, and when the lock is an empty directory.
  - `run_slate` ignores a snapshot page whose name time is inside the settle window (through the job, not `read_snapshot_raw`).
  - `write_final` returns False and leaves the bytes unchanged when `<slate>/` exists.
  - A young unreadable capture page is retried on the next run.
  - After a run, nothing exists under the root outside the two output directories.
- **mn3. A rebuild in game exceeds the stated budget.** A `RebuildNeeded` (late page, slow snapshot run, corrupt state) re-reads the
  night plus three UTC days of snapshot pages. Measured: 28.4 s at 03:02Z, and **40.4 s wall, 30.4 s CPU, 173 MB** at 04:32Z with the
  Mac at load 11 tonight. The budget says 20 s for a first slot and 10 s in game. Memory stays within budget, so no other job is at
  risk. **Fix:** state the rebuild cost in DAILY_PIPELINE. No code change needed.
- **mn4. A night with no tips never finalizes.** `prop_history_job.py:664-669` finalizes only when `base.tips` is non-empty.
  `:779-782` then re-runs that night in every slot of the next day, rewriting `latest/` with the old slate before the new slate's run
  rewrites it again, so for a few seconds the files flip between slates. Two days on, its state (up to about 90 MB) is abandoned for
  good. This is unlikely: the newest scoreboard page listed every game on 26 of 26 finished nights (`sb.py`). **Fix:** in `main()`,
  skip the previous-night pass when that night has no tips, and delete state directories more than 3 days older than the slate.
- **mn5. The docs drift and overclaim.**
  - `docs/DAILY_PIPELINE.md` cost line (3.3 to 3.9 s, 165 to 216 MB; 14.8 s; 17.8 s; 13.4 s) disagrees with the sched review and the
    commit message (3.5 to 4.9 s, 165 to 240 MB; 15.0 s; 18.0 s; 14.4 s).
  - "Byte-identical to `python -m nba.kalshi.prop_history ... --snapshot-raw`" holds only for the same `built_at` and settle cut. The
    CLI stamps `datetime.now` and applies no settle.
  - The rollback leaves out the `kalshi_prop_history_<date>.log` files, `launchd_kalshi-prop-history.{out,err}` and the
    `.<slate>.partial*` directories.

  **Fix:** one number set, quoted from one measurement, and the three rollback paths.
- **mn6. The heartbeat has no `last_success_ts`** (BEST_PRACTICES "Health means a recent success", 2026-11-09).
  `ops/nba_kalshi_prop_history.sh:34-39`. **Fix:** write `last_success_ts` on rc 0 and carry it forward on failure.
- **mn7. "Imports nothing heavy" is not quite true.** A build imports `polars` (`nba.kalshi.parse:22`) and `duckdb` plus
  `nba.db.connect` (via `nba.parse.availability` -> `nba.parse` package -> `nba.parse.loader`), which explains about 80 MB of the
  baseline. No database is opened and no socket is touched: verified with `duckdb.connect`, `socket.connect`, `create_connection`,
  `getaddrinfo` and every `open()` of a `.duckdb` path patched to raise (`guard_run.py`, for a build and a no-op, both exit 0).
  `test_no_network_order_or_credential_code_in_the_job` inspects only the job's own source. **Fix:** correct the wording, or import
  `normalize_name` from a lighter module (not this branch's area). Optional.
- **mn8. Writes outside the two directories.** The launcher also writes:
  - `data/ops/ALERTS.md` (shared, append-only),
  - `data/ops/kalshi_prop_history_<YYYYMMDD>.log`,
  - `data/ops/heartbeat/kalshi-prop-history{,.skip}.json`,
  - `data/ops/launchd_kalshi-prop-history.{out,err}`,
  - `data/ops/kalshi_prop_history_lock/`.

  The brief says anything beyond its directories, lock and heartbeat is a blocker. I did not rate this a blocker: appending a line to
  `ALERTS.md` is how every wrapper alerts (`nba_daily.sh`, `nba_kalshi_candles.sh`, `nba_live_capture.sh`), and the logs are the job's
  own files. Overrule this if you read the brief literally. **Fix:** list these paths in the DAILY_PIPELINE section and the rollback.
- **mn9. Stale-lock auto-removal conflicts with BEST_PRACTICES "Locks belong to whoever made them"** ("a stale lock is reported, not
  removed"). It matches `ops/nba_daily.sh:183-190`. Note only. It logs "cleared stale lock" to its own log, not to `ALERTS.md`.

## What survived (reproduced with my own scripts)

All commands ran from `/Users/devin/Downloads/nba-wt-adv-prop-history`. `PY=/Users/devin/Downloads/nba-wt-int-1117/.venv/bin/python`
(`import nba` resolves to this worktree; checked). `S=` the scratch path above. Each run is `nice -n 10`, and every exit code is shown
unpiped.

| # | Check | Command | n | Result | Pass |
|---|---|---|---|---|---|
| 1 | In-game slot, time and memory | `$S/stage.py` copies to 02:00Z and then 02:15Z; `/usr/bin/time -l $PY -m nba.kalshi.prop_history_job run --date 2026-11-16 --capture-dir $S/st1/live --snapshot-raw $S/st1/kraw --out $S/st1/out --state $S/st1/state --now 2026-11-17T02:17:00Z --no-previous` | 115 new pages, 8 games, 2,305 markets | **2.62 s, 161 MB** (claim ≤ 10 s, ≤ 300 MB); exit 0 | pass |
| 2 | First slot of the night | same, cut at 02:00Z, `--now 02:02Z` | 3,771 pages | 13.58 s, 163 MB; exit 0 | pass |
| 3 | Final slot | cut at 07:45Z, `--now 08:02Z` | 1,294 pages | `final`, 5.58 s, 222 MB; state deleted | pass |
| 4 | Incremental equals full build | `$S/oneshot.py $S/st1 2026-11-16 2026-11-17T08:02:00Z $S/ref1` (prop_history functions only); `$S/cmp.py` | 9 files | **9 of 9 identical** in `<slate>/` and in `latest/` (exit 0, 0) | pass |
| 5 | Raw reader equals the DB copy | `$S/dbraw.py` (read-only `read_snapshot` on `nba-scratch-prop-candles/kalshi_copy.duckdb` vs `read_snapshot_raw`) | 11-16: 2,340 markets, 171,154 points, 100 runs; 11-17: 1,434, 92,072, 83 | 0 differing series on both (exit 0, 0) | pass |
| 6 | Capture page arrives late, out of order | `$S/attack_ab.sh $S` case (a): page `KXNBAPTS_p0/20261117T020818Z` withheld at 02:17Z, restored at 02:32Z | 3 runs | `rebuilt: "capture page ... is older than pages already applied"`, final 9 of 9 identical | pass |
| 7 | Torn snapshot page older than the settle | `$S/attack_ab.sh $S` case (b) | 3 runs | final **0 of 9 identical**, one run of PTS snapshot lost (mn1) | **fail** |
| 8 | Interrupted final write | `$S/attack_c.sh $S` (`fail_final.py` raises ENOSPC on the partial rename) | 3 runs | 2 orphan `.2026-11-16.partial-<pid>` directories, 21 MB each (MJ2) | **fail** |
| 9 | No DB, no network | `$S/attack_d.sh $S` (`guard_run.py`) | a build (5,000 pages) and a no-op | exit 0, 0 with every DB and socket call raising | pass |
| 10 | Writes only its own directories | `$S/paths_audit.sh $S` (`--root` tree, `find -newer`) | 2 runs incl. a previous-night final | only `data/export/kalshi_prop_history/{latest,2026-11-16}` and `data/derived/` (state removed); inputs unchanged | pass |
| 11 | Readers of the new paths | recursive grep for the three names `kalshi_prop_history`, `prop_history_job`, `prop_history` over `*.py *.sh *.ts *.tsx *.js *.plist *.toml *.yaml *.yml` | whole repo | only the branch's own files and tests; no alert, EV, JARVIS, paper or app reader | pass |
| 12 | Replay oracle closure | import every `nba.daily.*` module, then check `sys.modules`; `git diff --stat main...HEAD -- nba/daily nba/ops ops/nba_daily.sh configs` | all `nba.daily` modules | no `prop_history` module imported; empty diff | pass |
| 13 | Launcher locks and signals | `$S/lock_attack.sh $S`, `$S/term.sh $S` (fake `uv`) | 8 cases | dead pid, empty lock and recycled pid with a start file are all cleared and run once; missing `uv` gives rc 127, an alert and the heartbeat; TERM waits for the child (exit 143 after 5 s, no orphan, lock removed). Recycled pid with no start file and a leftover heavy.lock skip forever with no alert (MJ1) | partial |
| 14 | Slate date and DST | BSD `TZ=America/New_York date -j -r <epoch> -v-6H` at 15 instants | 15 | a 23:00 ET tip finalizes at 03:10 ET under its own slate; on DST days the 06:00 ET flip moves to 05:00 EST (2026-11-01) or 07:00 EDT (2027-03-14), hours from any game window | pass |
| 15 | Snapshot run length vs the 600 s settle | `$S/snapdur.py data/kalshi/raw 2026-10-20` (file mtimes only, no file opened) | 2,537 runs, 78,492 files | p50 17.7 s, p90 23.3 s, p99 40.1 s, max 1,992.7 s; **3 runs over 600 s** | conditional (mn1) |
| 16 | Daily lock contention | pretip log 2026-11-16 | 4 pretip runs | each holds `data/ops/lock` under 1 min (:20 to :21, :50 to :51); no clash with :02/:17/:32/:47 | pass |
| 17 | Unresolved names | final index of check 3 | 2,340 markets | 454 unresolved, all listed, 0 with a `player_id`, 29 names, reason `name_not_in_reviewed_alias_table`; resolver exact-match only (`aliases.py`) | pass |
| 18 | Tips source | `$S/sb.py` (newest ESPN scoreboard page per finished night, read-only) | 26 nights (10-21 to 11-16) | newest page = max events on 26 of 26 | pass |
| 19 | Worst-case in-game rebuild | `--rebuild --now 2026-11-17T04:32:00Z` | 5,060 pages | 40.43 s wall, 30.35 s CPU, 173 MB at load 11 (mn3) | memory pass, time over budget |
| 20 | Branch tests | `$PY -m pytest tests/kalshi/test_prop_history.py tests/kalshi/test_prop_history_job.py --no-cov -q -p no:cacheprovider` | 40 | 40 passed, exit 0 | pass |
| 21 | Mutation | `$S/mutate.py $S/mut` (`git archive HEAD` copy) | 8 mutants | 3 killed, 5 survived (mn2) | fail |

Not affected, by reading: `data/export` and `data/derived` are not in `ops/vm/sync_from_mac.sh`'s path list. `nba.ops.app_export`
writes `data/export/latest` and prunes only `.latest.*.*`, so the names do not collide. The launchd plist has the same shape as
`local.nba.kalshi-candles` (uv, `WorkingDirectory`, no `KeepAlive`); it adds `Nice` 10 and `ProcessType` Background, and the launcher
adds `nice -n 10` on top (effective nice 19, which is fine). launchd does not start a second instance of a label that is still running,
so two scheduled fires cannot overlap.

## The single most likely failure mode
After install, the live Mac gets a stale `heavy.lock` or a lock with no start file from a crash or reboot. The job then skips every
slot with no notification, and the first anyone hears of it is an empty Market lens (MJ1). The second most likely is disk growth or
hidden partial directories on a nearly full disk (MJ2).

## Could not verify
- A real launchd launch through real `uv` under TCC, and the `ProcessType` Background throttling under load (`launchctl` was out of
  bounds). The launcher was tested with a fake `uv`.
- Behaviour on the live trees in place while they are being written. Everything ran on copies; torn reads were simulated by
  truncating files.
- A real disk-full condition (simulated with an ENOSPC-raising rename).
- `_p1` capture directories on real data (none on 11-16 or 11-17). By reading the code: a `p1` page renamed in the same UTC second as
  an already-applied `p0` page of the same poll triggers a correct but 30 s rebuild.
- How ESPN represents a postponed game on the scoreboard (affects `max(tips)`).
- Any app consumer: none exists yet.

## Playbook check (docs/MODEL_PLAYBOOK.md)
Model class: none. This is display-only market-data plumbing; no model is fitted, scored or promoted. The five questions:
- **Information or capacity:** neither. No forecast.
- **Data enough for the class:** n/a.
- **Test validity:** the "tests" here are equality and safety checks. The byte-identity test is valid; the safety paths are under-tested
  (mn2).
- **Lineage and inherited guards:** it inherits the alias resolver (exact match only), the capture's write-once store and the
  snapshot's in-place writer; the last is the guard it gets wrong (mn1).
- **Scored against:** nothing. No n or CI claim is made or needed, and none of the numbers above is an effect estimate.

## Recommended changes (not applied)
1. MJ1 fixes 1 and 2 in `ops/nba_kalshi_prop_history.sh`, plus the watchdog `JobSpec` and `problem()` wiring (ops owner) before
   `launchctl bootstrap`.
2. MJ2: a fixed partial name at `prop_history_job.py:571`, a free-space guard in the launcher, and Devin's gzip or retention call.
3. mn1: an mtime age check on snapshot pages (`:399-403`, `:417`).
4. mn2: the five tests listed.
5. mn5 and mn8: doc numbers and rollback paths.

## Drafted rows (not appended)
DECISIONS (draft):
> 2026-11-17 · Adversary on `agent/market-prop-candles-sched` (d62f2c1b): WOUNDED, no blocker. Safe to merge as code; install only
> after (1) skips alert and a start-less lock ages out, plus the watchdog JobSpec, and (2) a fixed partial-dir name and a free-space
> guard. Reproduced: in game 2.6 s and 161 MB; incremental = full build 9 of 9; raw = DB on 171,154 and 92,072 points. Broke: a torn
> snapshot page past the 600 s settle is lost from the write-once night (3 of 2,537 runs exceeded 600 s); 5 of 8 mutants survive.
> Reverse if: the fixes land and a re-review finds no new contradiction.
