# Review: app export gate on data steps (A1), branch agent/steward-export-gate @ c46eaa2

Date: 2026-10-14. Reviewer role: adversary (code review, not a model attack). Scope: `git diff main...agent/steward-export-gate`
(5 files: `ops/nba_daily.sh`, `nba/ops/app_export.py`, `docs/DAILY_PIPELINE.md`, `tests/ops/test_app_export.py`,
`tests/ops/test_daily_script_flags.py`). Purpose: DECISIONS 2026-10-14 20:31 (A1).

Verdict: APPROVED WITH EDITS

No blocker. The shell change cannot change the live job's exit code, alerts or HB_RC, and every way the gate can go wrong
that I found today fails closed (it skips). Three edits close one fail-open path that does not exist yet (duplicate step
names), one loose exemption, and one drift that is fail-closed but would leave the export dead without a test catching it.

## Playbook check

Not a model: an ops gate. The five questions in short: information or capacity: n/a. Enough data: n/a. Test validity:
the tests are synthetic plus one real-script run; there was no test linking the script's step names to the Python list
(must-fix 1). Lineage and guards inherited: the morning heartbeat stale/ended checks are unchanged; the shared `nba_daily.sh`
lock is reused. Scored against: the A1 wording ("every data step succeeded, Drive backup may fail, fail closed otherwise").

## Checks

| # | Check | Command / evidence | Result |
|---|---|---|---|
| 1 | Live job behaviour unchanged | read `ops/nba_daily.sh` @ c46eaa2 lines 52-57, 90-97, 120 | PASS. `step()` still does `return $rc`; `HB_RC` and `alert` untouched; the new `failed` var is unused elsewhere; under `set -u` (no `set -e`) a failed `printf >>` cannot end the job |
| 2 | Truncation vs lock | line 120 sits after `acquire_lock` (114) and the heartbeat START (119) | PASS. Probe: with the lock held, a second morning run left `daily-morning.steps` byte-identical |
| 3 | Pretip clobbering | `HB_JOB=daily-$MODE`, so pretip writes `daily-pretip.steps` | PASS. Probe: a pretip run after a morning run left `daily-morning.steps` identical; nothing reads `daily-pretip.steps`; the watchdog globs only `*.skip.json` (`nba/ops/watchdog.py:521`) |
| 4 | Stale file / other run | first line must equal `start_ts <heartbeat start_ts>`; the stale end check runs first | PASS. A second-resolution start_ts cannot repeat under the lock; a new morning run that starts between the export's two reads makes the header mismatch, so it skips |
| 5 | Job died midway | SIGKILL: heartbeat has no END, so the run is "not ended" and skips. SIGTERM: the trap writes END with rc 143; any data step not reached is missing from REQUIRED_STEPS, so it skips | PASS, with a note: a SIGTERM *after* `data_check` (in the 15-min lsof wait or rclone) exports with heartbeat rc 143. Consistent with A1 (all data steps done) |
| 6 | Real script into real gate | scratch probe `probe.py` (real `nba_daily.sh`, stub uv/rclone) | all ok: gate `None`; rclone exit 1: heartbeat rc 1, gate `None` (exports, the A1 intent); `fetch_tracking` exit 1: gate "morning data step failed: fetch_tracking" |
| 7 | `backup_` exemption | `NON_DATA_PREFIX = "backup_"` (app_export.py) | WEAK. Probe: `backup_local 1 failed` is exempted. Today's four `backup_*` steps are all Drive uploads, so nothing is wrong yet, but the docs promise `backup_*_drive` and the code checks less (must-fix 3) |
| 8 | Duplicate step names | dict overwrite in `data_step_problem` | FAIL-OPEN (latent). Probe: `report 1 failed` followed by `report 0 ok` returns `None`. No morning step name repeats today (must-fix 2) |
| 9 | REQUIRED_STEPS vs script | the 7 names all occur in the morning branch (lines 160-186); the postgame `fetch_*`/`postgame_load` and opt-in `facts` are, correctly, not required (they are conditional) but block if they ran and failed | PASS today. Drift is untested: the only real-script test asserts `settle`, `facts`, `data_check`; renaming `checkpoint`/`report`/`parlay_settle`/`data_snapshot`/`data_diff` passes every test and the export skips forever (alert after 2 days) (must-fix 1) |
| 10 | Facts opt-in | `facts` rc 1 means failed and blocks; rc 4 means ok; absent when off | PASS, matches A1 "every data step". Devin should know that a facts or stats.nba.com fetch failure blocks the app export (it already did under the old rc==0 gate) |
| 11 | Tests non-vacuous | each parametrized skip case runs with heartbeat `rc=1`, so the old rc gate cannot be what passes them; the backup-only test exports with rc=1; removing the header, REQUIRED or prefix logic breaks `other_run`, `truncated` or the backup test (checked by reading) | PASS except the coverage gap in row 9 |
| 12 | Oracle-skip claim | `grep -rn 'app_export\|nba_daily' --include='*.py' nba scripts ops` finds only a comment in `nba/daily/replay_season.py:69` and one in `nba/ops/watchdog.py:98`; replay_season's imports (lines 33-65) contain no `nba.ops.app_export`; its one `subprocess.run` (line 941) re-invokes its own worker | CLAIM HOLDS. The replay never imports the export or runs the shell script |

Verification (unpiped, worktree `.claude/worktrees/steward-export-gate`):
`uv run --no-sync pytest -q --no-cov -p no:cacheprovider tests/ops/test_app_export.py tests/ops/test_daily_script_flags.py` gave 32 passed, exit 0
(with coverage on, the subset run exits 1 only on the repo-wide 70% floor: 32 passed). `ruff check` exit 0, `ruff format --check` exit 0,
`mypy nba/ops/app_export.py` exit 0. The full suite was not run.

## Must-fix (the steward applies them; I did not)

1. **Contract test, script against gate.** `tests/ops/test_daily_script_flags.py::test_morning_writes_a_step_record_tied_to_the_heartbeat`:
   add a facts-off run (`_run_morning(tmp_path, nightly="", facts_rc=0)`) and assert
   `app_export.data_step_problem(root/"data/ops/heartbeat", json.loads(hb)["start_ts"]) is None`, plus that a facts rc 1 run
   returns `"morning data step failed: facts"`. This ties `REQUIRED_STEPS` to the real step names.
2. **Make a failure sticky.** `nba/ops/app_export.py`, `data_step_problem`, the line `status[parts[0]] = parts[2]`: replace with
   `if status.get(parts[0], "ok") == "ok": status[parts[0]] = parts[2]`, so a later `ok` never hides an earlier failure; add a test row.
3. **Exempt exactly what the docs say.** Same function, the `failed` comprehension: exempt only
   `n.startswith("backup_") and n.endswith("_drive")` (the docs and the DECISIONS wording say Drive backups), and rename the
   constant or add `NON_DATA_SUFFIX = "_drive"`; add a test that `backup_local 1 failed` blocks.

Minor, no action needed: a SIGTERM after `data_check` exports with heartbeat rc 143 (row 5); acceptable under A1.

## Single biggest risk

Silent drift between `REQUIRED_STEPS` and the script's step names: fail-closed, so the risk is that the app export stays dead
with a two-day skip alert rather than serving wrong data. Must-fix 1 removes it.

## Not verified

Full pytest suite; the launchd plist path in a real `launchctl` run; behaviour on the VM (`ops/vm/`); the replay oracle itself (not
re-run; the claim that it is unaffected was checked by import and grep only, which is enough for a change that adds no code the replay reaches).

## Drafted rows

None (an ops review; no ledger or DECISIONS row needed beyond A1).
