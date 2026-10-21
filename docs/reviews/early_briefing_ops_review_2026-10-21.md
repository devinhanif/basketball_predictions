# Adversary review: early forecast + slate briefings + fetch timeouts (live path), 2026-10-21

Branch `agent/steward-early-briefing`, head `f929466`, one commit on `e67ce3f`. Files: `ops/nba_daily.sh`,
`tests/ops/test_daily_script_flags.py`, `docs/DAILY_PIPELINE.md`. Reviewer worktree: `agent/adversary-early-briefing`.

Verdict: APPROVED WITH EDITS

The design is right. Devin asked for an early edition; forecasting the slate before the slow post-game fetches is the
correct fix for today's incident, and the alarm wrapper does kill a hung fetch end to end through `uv`. Four edits
are required before merge. Two are in this branch (E1, E2), one is in the scribe branch (E3), and one is the merge
order (E4). The top finding is E1: the new `early_forecast` step is itself unbounded inside the lock. `nba.daily run`
ran for 7057 s (10-20) and 12244 s (10-19) this week, so the step meant to route around a hang can reproduce one.

## Severity-ranked findings

| # | Sev | Where | Finding | Exact fix |
|---|---|---|---|---|
| E1 | major | `ops/nba_daily.sh:206` | `early_forecast` runs unwrapped while holding `data/ops/lock`. It is the same network-bound call as pretip `predict` (stats.nba.com ingest, schedule, rosters, injury probing), and this week that call ran for 417, 914, 1011, 2519, **7057** (10-20 10:26 → 12:23, rc 5, stats.nba.com DNS failures) and **12244** s (10-19 13:20 → 16:44, rc 75). A repeat inside the morning job means today's incident again: the lock is held, every pretip slot skips, and the slate has no forecast. | `step early_forecast alarm_run "$EARLY_TIMEOUT_S" "$UV" run --no-sync python -m nba.daily run ...` with `EARLY_TIMEOUT_S=${NBA_EARLY_TIMEOUT_S:-1200}` next to the line-120 defaults (1200 s is 2.9× the 417 s cold run). Add a stub test: `*"nba.daily run"*) exec sleep 30;;` with `NBA_EARLY_TIMEOUT_S=1` gives `early_forecast 142 failed`, and `coverage_yesterday` still runs. |
| E2 | major | `ops/nba_daily.sh:118` | **`alarm_run` fails open.** When perl's `exec` fails (missing or moved `$UV`, a bad `NBA_UV` on the VM), perl falls off the end of `-e` and exits **0**. Measured: `perl -e 'alarm shift; exec @ARGV' 5 /nonexistent/uv run x` gave `perl_rc=0`; the same path without the wrapper gave rc 127. On main, a missing uv alerts. On this branch, all four fetches and `postgame_load` would record `ok` with no data fetched, and the app-export gate would go green on data that never arrived. | `perl -e 'alarm shift; exec @ARGV; print STDERR "alarm_run: cannot exec $ARGV[0]: $!\n"; exit 127' "$secs" "$@"`. Verified: missing binary gives 127, `sleep 5` under a 1 s alarm gives 142, `sh -c 'exit 3'` passes 3 through. Add a test with `NBA_UV=/nonexistent` asserting `fetch_tracking 127 failed`. |
| E3 | major (scribe branch) | `nba/explain/data.py:46-48` (`open_ro`), as used by `write_slate` on `agent/scribe-slate-briefing` | `briefing_report` runs immediately after pretip `predict`. A T-30 tick (`run-t30`, its own locks, not `data/ops/lock`) that has been waiting behind predict takes the `nba.duckdb` write lock as soon as predict closes; DAILY_PIPELINE says the two "overlap by construction" on mixed :00/:30 slates. `open_ro` is a bare `duckdb.connect(read_only=True)` with no bounded wait, so the briefing gets the lock error and returns 1 (EXIT_UNREADABLE). That alerts, and it also **overwrites the last good `_report.md` with a "could not be opened" page**. | Scribe: `open_ro` → `nba.db.connect.connect_with_retry(path, read_only=True)` (honours `NBA_DB_LOCK_WAIT_S`, 300 s in this job). On rc 1, leave an existing page in place: write to `<out>.tmp` and `os.replace` only on rc 0/4. |
| E4 | major (merge order) | `ops/nba_daily.sh:175,209` | `nba.explain` on main has only `game`. Merged first, `slate` is an argparse error: rc 2, an alert on **every** pretip run (up to 26 a day) and every morning, and `briefing_early` fails the app-export gate. | Merge `agent/scribe-slate-briefing` (with E3) first. Then merge this branch after the replay oracle and Devin's go. |
| m1 | minor | `ops/nba_daily.sh:111`; DAILY_PIPELINE new section, "Timeouts" bullet | The claim "`uv run` execs its child" is false. Probe: python pid 99181, ppid 99179 (= uv), so uv forks. It works anyway because uv 0.12.23 **forwards SIGALRM** to the child: python dies on the default action and uv returns 142, with no orphan left (`ps -p 99181` gave rc 1, and the `survived` marker was never written). Caveat: a child that installs a SIGALRM handler defeats the alarm (probe 2: handler ran, child finished, rc 0). No `nba/ingest` code does this (grep), but nothing guards it. | Reword to "uv forwards SIGALRM to its python child (checked with uv 0.12.23); a child that handles SIGALRM is not killed". Optional hardening: a perl wrapper that forks, waits, and on alarm sends `kill TERM` then `KILL` to the process group. |
| m2 | minor | `ops/nba_daily.sh:95-96`; test `test_briefing_and_early_forecast_rc4_informational_rc5_alerts` | The comment and test say briefing "rc 5 alerts", but `write_slate` returns 0 / 4 / 1. The test stubs a code the real command never returns. | Comment: "briefing rc 4 informational; rc 1 (DB unreadable) and 2 (usage) alert". Stub the briefing arm with `exit 1`. |
| m3 | minor | test `test_a_hung_fetch_...` | The stub pattern `*"source tracking"*` also matches `postgame_load` (`--source tracking ...`), so the load step sleeps the full 30 s under its default 1200 s alarm. The test takes **32.06 s**. The pattern in `test_exit_142_...` (`*"source officials"*`) also makes postgame_load "time out". | Use `*"postgame-fetch --source tracking"*` and `*"postgame-fetch --source officials"*`, and assert `postgame_load 0 ok`. |
| m4 | minor (recommendation; nba/ owner) | `nba/ops/app_export.py:47` `_is_drive_backup` / line 96 | Any failed non-Drive morning step now blocks the export, including `briefing_early`, which is text and not data. `briefing_report` is pretip and is not in the morning steps file, so it is not gated. | Exempt `briefing_*` the same way as `backup_*_drive`. Keep `early_forecast` in the gate (safe default; Devin's call, see needs_devin). |

Pre-existing, not introduced here, but they contradict the branch's stated rationale ("the lock stays held for the
whole job"):

- **P1 (major, pre-existing): the lock is stolen at 3 h.** `acquire_lock` clears a live owner's lock once the lock
  dir is more than 180 min old (`ops/nba_daily.sh:129`). Morning runs this week lasted ~3.9, 4.8, 12.3 and 5.3 h, so
  a pretip run steals it. The morning `EXIT` trap then `rmdir`s the pretip's lock (line 144), because it does not
  check `$LOCK/pid = $$`. With the alarms, the worst case is still settle (≤ 1396 s) + early (bounded only after
  E1) + 4×1800 + 1200 s, plus unbounded rclone uploads (6435 s on 10-18, 9614 s on 10-19). That can cross 180 min.
  Fix (separate diff): alarm the `backup_*_drive` steps, and release only a lock whose pid is ours.
- **P2:** killing a fetch mid-write can leave `nba.duckdb.wal`. DuckDB replays it on the next writer open, but the
  morning backup then logs "wal present; backup skipped". This is acceptable, but expect that alert on a timeout day.

## Timing (check 2)

All numbers come from `data/ops/morning_202610{16..21}.log` and `pretip_202610{19..21}.log`, read-only, local
times (CDT):

| Day | Morning start → end | Duration | Slow steps | Pretip lock-skips before first run |
|---|---|---|---|---|
| 10-16 | 07:00 → 07:18:44 | ~19 min | fetch_officials 647 s, postgame_load 321 s | n/a (log not read) |
| 10-17 | 07:12 → 11:08:42 | ~3.9 h | settle 1396 s, fetch_officials 11010 s, postgame_load 1431 s | n/a |
| 10-18 | 07:07 → 11:54:50 | ~4.8 h | settle 952 s, fetch_officials 2388 s, postgame_load 7093 s, backup_db_drive 6435 s | n/a |
| 10-19 | 07:12 → 19:32:54 | ~12.3 h | fetch_officials 21050 s, postgame_load 13709 s, backup_db_drive 9614 s | 4 (08:32–10:00) |
| 10-20 | 07:08 → 12:24:50 | ~5.3 h | fetch_officials 14590 s | 4 (08:28–10:04) |
| 10-21 | 07:00:02 → still running at 09:39 (fetch_tracking since 07:00:20) | 2.6 h+ | fetch_tracking | 3 (08:20, 08:50, 09:21) |

- **The early step's cost.** A cold first-of-day `nba.daily run` takes 339 s (DAILY_PIPELINE, measured) to 417 s
  (10-19 10:22 → 10:29). Warm runs take 27–53 s; the tail runs to 914 / 1011 / 2519 / 7057 / 12244 s (E1). Before
  the early step, ingest → report takes about 15 s on a normal day, so the early row lands around 07:06–07:08 CDT
  (08:06 ET). On a slow-settle day (10-17, 10-18) it lands around 07:25–07:40.
- **Watchdog 45-min grace.** The morning job already exceeds it on 4 of 5 days, all from fetch, load and backup. The
  extra ~6–7 min matters only on a 10-16-like day (19 → about 26 min), which is still under 45. **No new grace
  breach on a normal day.**
- **08:20 pretip slot.** It is already skipped every day 10-19 → 10-21. On a normal day the branch does not move the
  job into 08:20 (it ends around 07:26). On a bad day the slots skip as before (worst case with alarms is about
  07:08 + 9000 s ≈ 09:38, before backups). The difference is that the early row now exists. That is the point of
  the change, and it holds only if E1 bounds the early step itself.

## Scoring interaction (check 3)

- Every reader of `forward_predictions` picks the **latest** row (`ORDER BY made_at DESC, run_id DESC`). Checked:
  `nba/daily/settle.py:99,293`, `nba/daily/store.py:177`, `nba/daily/report.py:402`, `nba/ops/coverage.py:240`,
  every shadow store and live eval, and `nba/explain/data.py:146`. No reader uses `min(made_at)` or an ascending
  latest-row pick. `nba/markets/capture.py:171` orders ascending only for an export listing. **No code path
  prefers the early row.**
- The early row is a valid pre-tip row; the store refuses `made_at >= tip`. For a 19:00 ET tip, a ~08:05 ET row is
  superseded by the first later run whose values differ (dedup writes on change). It stays the scored T-60 row only
  when nothing changed, which is the existing rule-3/4 semantics.
- **Coverage change (not a rule change).** On a day like today, games that would have had no row now have an
  early-morning one. For an early weekend tip (12:00 ET, T-60 = 10:00 CDT) with the lock still held, the scored row
  is now the ~07:05 CDT row, built on whatever report existed then. This is honest and as-of, and the scored
  population still follows the frozen rule. It belongs in the scorecard's notes, not in a rule edit.
- **Injury.** `pipeline.py:224-242` calls `pull_latest_report(not_after=min(made_at, first tip))`, then
  `injury_status = "ok" if slot else "no_report_found"`. The slot is the latest report published by about 08:00
  ET, which is real and as-of. If none exists, it records `no_report_found` and the Elo injury arm falls back. Not
  verified live: whether a report usually exists by 08:00 ET (no early run has happened yet).

## Alarm mechanics (check 4)

```
$ perl -e 'alarm shift; exec @ARGV' 2 /opt/homebrew/bin/uv run --no-sync python -c "...os.getpid(), os.getppid(); time.sleep(8)..."
rc=142          pid file: 99181 99179     ps -p 99181 -> ps_rc=1 (gone)     'survived' marker: absent
$ perl -e 'alarm shift; exec @ARGV' 2 uv run --no-sync python child.py   # child installs a SIGALRM handler
rc=0            got_alrm, survived both written
$ perl -e 'alarm shift; exec @ARGV' 5 /nonexistent/uv run x ; echo $?
0                                                               # E2: fail-open
```

uv 0.12.23 forks python and forwards SIGALRM, so there is no orphan holding a DuckDB connection in the normal case.
The shell's `trap 'exit 143' INT TERM` is not involved, because SIGALRM goes to the exec'd uv, not to the shell.
No process-group kill is required for the code as it stands. It becomes required if any `nba/ingest` entry point
ever handles SIGALRM or spawns grandchildren.

## Check table

| # | Check | Command (exact) | Result | Pass |
|---|---|---|---|---|
| 1 | Scope | `git diff --stat main...agent/steward-early-briefing`; `git diff --name-only ... \| grep -c "^nba/\|^data/\|^configs/"` | 3 files (+164/−5); 0 under nba/, data/, configs/; 1 commit; 0 attribution matches | pass |
| 1b | Oracle | (no run) | nba/ untouched, so the replay oracle code path cannot move; the oracle row is already logged (`08ecb1f`) | pass by construction |
| 2 | Timing | `grep -E "(start\|end) \|skipped" data/ops/morning_2026101[6-9].log ...` (read-only) | table above | pass on a normal day; fails on the tail (E1) |
| 3 | Scoring | `grep -rln forward_predictions nba \| xargs grep -n "ORDER BY made_at\|min(made_at..."` | all latest-row | pass |
| 4 | Alarm through uv | perl probes above | 142, no orphan; fail-open on exec error | partial (E2) |
| 5 | rc rules | read `ops/nba_daily.sh:95-107`; tests | 142 has its own text and later steps run; 4 is informational for the three new steps; the no-games day stays quiet (rc 4 from both) | pass, with m2 |
| 6 | Export gate | read `nba/ops/app_export.py:81-102` | any non-Drive morning failure blocks; briefing_early is now included | m4 |
| 7 | Tests fail on main | branch test file pointed at main's script: `pytest ../onmain/test_branch_on_main.py -k "early or briefing or alarm or hung or 142" --no-cov -q -p no:cacheprovider` | `6 failed, 14 deselected in 64.09s`, rc 1 | pass (all 6 new tests are real regressions) |
| 7b | Hung test exercises alarm_run | `pytest ...::test_a_hung_fetch_... --durations=1` | `32.06s call`, `1 passed`, rc 0. Exercises alarm_run on a stub that `exec sleep`s; does not exercise uv's fork-and-forward (covered by my probe) | pass, with m3 |
| 8 | Suite + syntax (steward worktree, read-only) | `sh -n ops/nba_daily.sh`; `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/ops tests/test_layering.py --no-cov -q -p no:cacheprovider` | `sh_n_rc=0`; **`147 passed in 66.28s (0:01:06)`**, rc 0 | pass |
| 9 | Merge order | `git show agent/scribe-slate-briefing:nba/explain/__main__.py`; main's `nba/explain/__main__.py` has only `game` | scribe first | E4 |

**Untested branches.** The real `uv` fork-and-forward chain (only my manual probe covers it). `nba.explain`
missing or `slate` absent (rc 2 alert storm). `$UV` missing under `alarm_run` (E2). An `early_forecast` hang (E1).
The briefing during a T-30 writer (E3). The lock-steal-at-180-min path with the new step order (P1).

## Playbook check

Model class: none. This is an ops and live-path change; no model, feature, fit or score changes.
(1) Information or capacity: information, in the timing sense. A forecast exists earlier, built on the same
inputs as of 08:00 ET. (2) Data appetite: n/a. (3) Test validity: the stub-uv harness is valid for ordering and rc
rules, but not for the uv signal chain; my probe covers that. (4) Lineage and inherited guards: the early row
inherits every store guard (made_at < tip, dedup on change, latest-row readers). (5) Scored against: the
unchanged FORWARD_PREREG rules 3-4. The only effect is coverage, on lock-held days.

## Single most likely failure mode if this is merged as is

A stats.nba.com stall during `early_forecast`, the 2 h and 3.4 h hangs seen on 10-19 and 10-20, holds
`data/ops/lock` with no alarm, and today's incident repeats. Second: a moved `uv` turns every post-game fetch into a
silent `ok` (E2).

## What I could not verify

- Whether an injury report usually exists by 08:00 ET (no early run has happened).
- The real frequency of the T-30 / briefing collision (E3). It is inferred from "overlap by construction", not
  measured.
- uv versions other than 0.12.23.
- The VM path (`ops/vm/`).
- The replay oracle (not run; nba/ is untouched).

## Drafted rows (not appended)

DECISIONS (draft): "2026-10-21: the morning job forecasts the slate (early edition) before post-game fetches; every
network step inside the morning lock is alarm-bounded (fetch 1800 s, load 1200 s, early forecast 1200 s). Why: a
hung fetch (07:00:20) held the lock and left the slate unforecast. Reverse if the early row is shown to displace a
better-informed T-60 row (it cannot under latest-row readers) or if the alarms kill fetches that would have
completed (watch rc 142 frequency for fetch_officials, which ran > 1800 s on 4 of 5 days)."
