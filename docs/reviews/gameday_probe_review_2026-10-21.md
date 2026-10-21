# Adversary review: watchdog game-day probe (plan item B), 2026-10-21

Branch `agent/steward-gameday-probe`, head `eabb2bd` (one commit on `18bc03f`). Files: `nba/ops/watchdog.py` (+210/-5),
`tests/ops/test_watchdog_gameday.py` (new, 15 test functions; the steward's report says 16), `docs/DAILY_PIPELINE.md` (+10).
Reviewer worktree: `agent/adversary-gameday-probe`. Line numbers below are the branch's `nba/ops/watchdog.py`.

Verdict: APPROVED WITH EDITS

This is not "clean" in the sense of Devin's pre-authorization: one major edit (M1) should land before merge, or Devin waives it.
Nothing here can harm the live path. The probe runs inside a try, its waits are bounded (measured 20.1 s on a held lock), it never
writes, and every path to `check()` returns. All the findings are about alerts that get missed or alerts that are noise.

## Playbook check
Model class: none. This is operations code in the watchdog, not a model, so the playbook's class checks and the six statistical checks
(shuffled labels, planted future, missingness, knockout, replication, integer support) do not apply. The five cross-cutting questions
reduce to this. Information: the probe reads only facts it can know at run time (pmset, pgrep, schedule files, `games`, the heartbeat).
Test validity: injected fakes plus one real DuckDB round trip. See the untested branches below. Lineage: it inherits `connect_ro`'s
read-only open and the watchdog's dedupe. Scored against: the 2026-10-20 incident (asleep on battery, morning job 07:07-12:24 CDT).

## Checks run (all unpiped; exit codes shown)

| # | Check | Command (from the steward worktree, read-only: `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, `--no-cache`) | Result | Pass |
|---|---|---|---|---|
| 1 | Scope | `git diff --stat main...agent/steward-gameday-probe`; `git diff --stat 18bc03f main -- nba/ops tests/ops tests/test_layering.py docs/DAILY_PIPELINE.md`; `git merge-tree --write-tree main agent/steward-gameday-probe` | 3 files, only B; `data/` not in the commit; no drift on main since base; merges clean (exit 0) | pass |
| 2 | Ops tests | `uv run --no-sync pytest tests/ops -q --no-cov -p no:cacheprovider` | `124 passed in 30.38s`, exit 0 | pass |
| 3 | Layering | `uv run --no-sync pytest tests/test_layering.py -q --no-cov -p no:cacheprovider` | `10 passed in 4.28s`, exit 0; imports of polars, duckdb and `nba.ops.coverage` are lazy and inside `nba.ops` | pass |
| 4 | Lint | `uv run --no-sync ruff check nba/ops/watchdog.py tests/ops/test_watchdog_gameday.py --no-cache` | `All checks passed!`, exit 0 | pass |
| 5 | Format | `uv run --no-sync ruff format --check <same two files> --no-cache` | `2 files already formatted`, exit 0 | pass |
| 6 | Types | `uv run --no-sync mypy nba/ops/watchdog.py --no-incremental --cache-dir=/dev/null` | `Success: no issues found in 1 source file`, exit 0 | pass |
| 7 | Attack script | `uv run --no-sync python scratchpad/attack/attack_probe.py` (synthetic temp files only) | exit 0; results A1-A7 below | see findings |
| 8 | Predicted 10-21 lines | read-only `todays_slate` + `games_in_db` on the live data and `nba.duckdb` (`read_only=True`) | 11 game_ids on 10-21 ET, 0 present in `games` | slate alert fires (as designed) |

I did not run the full suite (4148 passed / 1 pre-existing failure, per the steward). The diff touches only `nba/ops` and its tests.

Attack results (A = attack):
- A1. Parquet branch across DST (2026-11-01), with tips at 23:30 EDT, 01:30 EDT, 23:30 EST and 00:30 EST. Each lands on its correct ET date. pass.
- A2. Schedule cache missing, or corrupt JSON, with no live-tips parquet. `todays_slate` returns `[]` and `evaluate_game_day` returns `[]`
  even with battery on and caffeinate off. **The probe falls silent without saying it could not compute (M1).**
- A3. `pgrep` returncode 0/1/2/3 maps to running True/False/False/False. An error exit reads as "not running" (m1).
- A4. pmset "Battery Power" gives True. "AC Power", "UPS Power" and empty output give False. A missing pmset raises FileNotFoundError,
  which becomes `probe-battery`. pass. A desktop Mac reports "AC Power", so it is silent. pass.
- A5. Dedupe. A flap (alert, then clear, then alert again on the same ET day) does not re-alert. +13 h on the same day does not re-alert.
  Keys from other days are pruned at ET midnight. pass. The undated key `gameday:probe` (line 643) alerts once and never again while
  it persists (+7 h, +14 h, +30 h all silent) (m2).
- A6. A real write lock on a temp DuckDB raised `DbBusy` after 20.1 s and produced no alert. pass. Worst case per check is about
  10 + 10 + 20 s, well inside the 600 s cadence.
- A7. A missing DB file raises IOException (not a lock error), which is re-raised and becomes `probe-slate`. pass.

## Findings (severity-ranked)

**M1 (major): the probe is silent on a game day whose schedule cannot be read.** `watchdog.py:417-420` swallows
`OSError`/`ValueError` on the cache. `:426` skips a missing parquet. `:496` (`if not slate: return []`) then treats "no readable
source" as "no games". This breaks the requirement that the probe never stays silent when it cannot compute. A renamed, deleted or
half-written `schedule_<season>.json` (the season string is derived at `:414-415`) would disable all four checks with no trace.
Fix: in `todays_slate`, track whether at least one source was read. If neither exists or parses, `raise FileNotFoundError(f"no
readable schedule source for {today}")`, so the existing `except` at `:489` emits `gameday:<date>:probe`. Add a test where the data
dir holds no schedule files and expect key `gameday:2026-10-21:probe`. A corrupt cache with a good parquet should still work: raise
only when both fail. The cost is one alert per day if the files are missing on a non-game day, which is correct, because then we do
not know whether it is a game day.

**M2 (major, Devin's call, not a code defect): two of the four checks fire on most game days by construction, which trains people to
dismiss the notification.** `slate` (`:529-538`) fires on every game day until scheduled games are written to `games`. The DAILY_PIPELINE
text says this. `morning` "started but has not ended" at 09:00 ET would have fired on 6 of the 11 mornings from 10-10 to 10-20. Runs
ended at 13:20, 09:19, 11:08, 11:54, 19:32 and 12:24 CDT; healthy runs take 5-20 min. Most of the gap is `fetch_officials` hanging
40 min to 4 h (morning logs for 10-17, 10-18 and 10-20). So the 09:00 ET morning alert is true, not a false positive. It is not too
early: the job starts about 07:00 CDT = 08:00 ET and has had 60 min. But until `fetch_officials` gets a timeout it is a near-daily
alert. Both share the 200-char macOS notification (`:650`) with the battery and caffeinate lines, the ones that matter on a sleep
night. Options, safest first: (a) merge as is and accept the noise until A(4) or the scheduled-games fetch lands (the default if
Devin says nothing); (b) gate `slate` behind a module constant `GAMEDAY_SLATE_CHECK = False` until the fetch exists, with a test
that it is off; (c) put battery/caffeinate alerts first in the notifier string. I recommend (c) plus naming the running step in the
morning message (edit m4).

**m1 (minor): a `pgrep` error is reported as "caffeinate is not running".** `watchdog.py:403-408`. Fix:
`rc = subprocess.run([...]).returncode; if rc not in (0, 1): raise RuntimeError(f"pgrep exit {rc}")` and `return rc == 0`. The
existing `except` turns the raise into `probe-caffeinate`. Test: patch `subprocess.run` to return rc 2 and expect `probe-caffeinate`,
not `caffeinate`.

**m2 (minor): `check()`'s crash key `gameday:probe` has no date** (`:643`), so `dedupe` treats it as once-a-day (prefix match,
`:584`). The prune at `:580` never keeps it, so it re-alerts only after it clears. A probe that crashes every check alerts once and
then stays silent for days (A5). Fix: `Alert(f"{GAMEDAY_PREFIX}{now.astimezone(ET):%Y-%m-%d}:probe-crash", ...)`, so it fires once
per ET day.

**m3 (minor): the alert text could be read as "the fetch broke today".** `:536-537` says "the schedule fetch does not write scheduled
games". Fix: "...game_ids missing (expected: `games` gets a row only once a game starts; not a fetch failure)". For 2026-10-21 the line
will read `game day 2026-10-21: games table lacks today's slate: 11 of 11 game_ids missing; ...` (verified read-only: 11 ids, 0
present).

**m4 (minor): the morning "not started" message points at a path that does not exist.** `:554` names "data/ops/daily-morning logs".
The logs are `data/ops/morning_<YYYYMMDD>.log` and the step marker is `data/ops/heartbeat/daily-morning.steps`. Fix the path. For the
"started but has not ended" text (`:561`), append the elapsed minutes and the current step from `daily-morning.steps`, so a sleep and a
`fetch_officials` hang can be told apart.

**m5 (minor): untested branches.** A regression in any of these would pass the suite: the live-tips parquet branch of `todays_slate`
(`:426-433`) and DST (no test crosses 11-01); the real `on_battery` parse and the `caffeinate_running` exit codes (only lambdas are
tested); a flap within a day and the pruning of other days' keys in `dedupe` (`:577-581`; the dedupe test never clears the condition);
the real lock path to `DbBusy` in `games_in_db`; the missing-sources path (M1). Fix: port attacks A1, A3, A4 and A5 into
`tests/ops/test_watchdog_gameday.py`. Each is under 15 lines, uses tmp files and `mock.patch` of `subprocess.run`, and needs no network.

**m6 (minor, note): the steward's report says 16 tests; the file has 15 `def test_`.** Bookkeeping only.

Not findings (checked, fine): the ET date is computed once from the same `now` in both `evaluate_game_day` and `dedupe`, so key and
prune agree across CDT/ET midnight; the probe is silent at 23:30 CDT = 00:30 ET, because that is before 09:00 ET of the new day; the
heartbeat's `-0500` offset parses (`datetime.fromisoformat`, Python 3.11+); "no start today" and "start without end", including a stale
`end_ts` < `start_ts`, both alert; the read-only connection is held for milliseconds, and live writers retry on the lock
(`nba/db/connect.py` `connect_with_retry`, 180 s), so this new reader every 10 minutes cannot starve the pretip job; the DB path
(`data/..`/`nba.duckdb`) resolves to the repo root under launchd's `WorkingDirectory`; the VM cron does not run the watchdog, so a
missing pmset is not a nightly alert there; non-regular-season ids are excluded (see the limitation below).

## Most likely failure mode if this approval is wrong
The schedule cache or the season-derived file name goes missing, and the probe quietly stops judging game days (M1). This is the same
pattern as "a job that cannot start cannot alert", one level down.

## Limitations, and what I could not verify
- The probe runs on the same Mac. A Mac asleep on battery runs no watchdog, so the alert can only fire once it wakes. That was the
  10-20 failure. It is inherent and not fixable here; an off-machine heartbeat would be.
- `REGULAR_SEASON_PREFIX = "002"`: the probe is silent for play-in (005), playoffs (004) and Cup knockout games, if their ids are not
  002. That is acceptable for October; it needs a decision before April.
- The `caffeinate` check passes on any process named caffeinate, including the `caffeinate -i` started by `ops/run_ingest_queue.sh`,
  which does not stop lid or system sleep the way `-dimsu` does. Devin's current one is `caffeinate -dimsu -t 36000`, started 23:10
  CDT 10-20. **It expires at 09:10 CDT (10:10 ET) on 10-21**, after which the probe will raise `gameday:2026-10-21:caffeinate` once.
- Not run: the full pytest suite, the replay oracle (running separately), or an actual launchd firing of the new code.
- `pmset` output on battery was not observed live (the Mac is on AC). The parse rests on the documented string "Now drawing from
  'Battery Power'".

## Recommended changes (for the main session; not applied by me)
1. M1 fix plus its test (before merge, unless Devin waives it).
2. m1 and m2 (three lines each), m3 and m4 text, m5 tests. Can follow in a second commit on the same branch.
3. M2: Devin picks (a), (b) or (c); the default is (a).

Drafted DECISIONS row (not appended):
`2026-10-21 | B game-day probe merged (eabb2bd + edits) | adversary APPROVED WITH EDITS (docs/reviews/gameday_probe_review_2026-10-21.md); M1 silent-on-missing-schedule fixed | reverse if the probe's daily noise (slate, morning) leads to a missed battery/caffeinate alert`
