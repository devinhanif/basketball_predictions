# Adversary review: ESPN fresh poller enable switch (branch agent/market-espn-poller-enable, be2d18f8)

Date: 2026-11-10. Scope: LIVE PATH (ops/nba_live_capture.sh starts the nightly game capture). Question: is the
branch safe to merge with `espn_fresh_interval_s: 0`? Launchd and the running capture were not touched; no season 2025;
no DuckDB opened. All attacks ran on a `git archive be2d18f8` copy in scratch with stubbed `uv` and `caffeinate`.

**Result: WOUNDED.** The capture always starts with `--fresh-interval-s 0` in every failure case I could build, so the
nightly capture itself is safe. But the new pre-step **always fails in production**: the launcher calls
`python -m nba.ops.live_capture fresh-interval` without `--date`, and `--date` is `required=True` for every subcommand.
So every capture start writes a false `ALERT: fresh poller not started: 0 fresh-interval check failed` to
data/ops/ALERTS.md, even with the config at 0. The switch also can never turn on: setting 15 does nothing. No test runs
the CLI path the launcher uses, which is why this got through.

Verdict: BLOCKED

(It is blocked on a one-token fix plus one test. The capture-start safety claim holds.)

## Playbook check

Model class: none. This is an ops/launcher change with no forecast, metric or claim, so the six statistical checks
(shuffled labels, planted future, missingness, knockout, replication, integer support) do not apply. The five
cross-cutting questions: information/capacity n/a; data appetite n/a; test validity (the tests miss the launcher's real
CLI call, see F1); lineage (it inherits the poller's auth latch and backoff from nba/live/espn_fresh.py, unchanged);
scored against n/a.

## Findings (severity-ranked)

| # | Sev | Finding | Exact line | Exact fix |
|---|---|---|---|---|
| F1 | **major (merge blocker)** | `fresh-interval` exits 2 under the launcher (`the following arguments are required: --date`). It falls back to 0 (safe), but writes a false ALERT on every capture start every game night, and makes the switch dead (15 is never passed). | ops/nba_live_capture.sh:74 (`... live_capture fresh-interval 2>>"$LOG"`); nba/ops/live_capture.py `main`: `ap.add_argument("--date", ..., required=True)` | Either pass `--date "$TODAY_ET"` on :74, or (better) make `--date` optional and require it only for `plan`. Add a test calling `lc.main(["fresh-interval"])` (no --date) that asserts exit 0 and stdout `0 off (config 0)`. Add a launcher test with a stub `NBA_UV` (as in Attack 1) that asserts `--fresh-interval-s 0` and **no** ALERT line on the default config. |
| F2 | major (before any flip to non-zero) | The fixture gate only checks that the files exist. Files containing `{}` and `not json at all` give `15 on`. The config comment says "...and tests/live/test_espn_core_real.py passes on them", but nothing enforces that at launch. A file that exists but is not committed (untracked in the main checkout) also passes. | nba/ops/live_capture.py `fresh_interval`: `missing = [f for f in REAL_FIXTURES if not (fixture_dir / f).is_file()]` | Inside the `try`, parse each fixture with `last_play_core(json.loads(...))` and require `lp is not None and lp.play_id and lp.wallclock`; otherwise return `0, "refused: fixture <f> does not parse"`. Optionally also require `git ls-files --error-unmatch`. Extend `test_fresh_interval_default_off_and_refused_without_real_fixtures`: `{}` fixtures must be refused. The current test asserts `{}` gives `(15.0, "on")`, so it locks the weak gate in place. |
| F3 | minor | The shell sanitiser accepts any `[0-9.]` token. `5` and `1.2.3` are passed straight to `nba.live capture`, which then exits 2 via `ap.error`/float parse, so the capture would die on every tick all night. Current Python output cannot produce these (`{:g}` after the >=15 check), so this is defence in depth only. | ops/nba_live_capture.sh:76-78 | Accept a non-zero value only when Python said it was on: add `case "$FRESH_OUT" in *" on") ;; *) FRESH_S=0 ;; esac` after the existing case. |
| F4 | minor | Fresh-poller trouble is reported through `problem()`, so the watchdog shows it under the `livecap` alert text "live capture is not running ... Start it: launchctl kickstart". In fact the capture is running, the lock is held, and kickstart does nothing. Only shows once the poller is on. | nba/ops/live_capture.py `problem`: `return fresh_problem(hb, now)`; nba/ops/watchdog.py ~L693-704 message | Call `fresh_problem` separately in the watchdog under its own key (e.g. `livefresh@HHMM`) with remediation text such as "poller degraded; capture unaffected; see fresh_errors / fresh_stopped_reason". |
| F5 | minor | `fresh_last_game_success_ts` is a single timestamp across all games, so one game failing while others succeed never alerts. After an auth latch, `fresh_live_games` stays frozen above 0, so "thread is dead" repeats every 30 min until the window closes (true, but noisy). | nba/live/espn_fresh.py:250, :408 | Acceptable for v1. If wanted, keep a per-game last-success and alert on the oldest live game. |
| F6 | minor | The new `uv run` has no timeout. A hang delays the capture. The `plan` step already had this exposure, and the watchdog would see no capture heartbeat inside the window ("no heartbeat"), so it is not silent. macOS has no `timeout(1)`. | ops/nba_live_capture.sh:74 | Leave as is, or wrap with a Python-side timeout. Not needed for merge. |

## Attack 1: can the pre-step stop or delay the capture? (PASS for start-safety, FAIL F1)

Harness (scratch, not in repo): `.../scratchpad/poller/run.sh` runs the committed `ops/nba_live_capture.sh` under
`env -i PATH=/usr/bin:/bin HOME=/nonexistent` (launchd-like) with `NBA_UV` set to a stub that answers `plan` with
`start`, plays each fresh-interval failure mode, and records the `nba.live capture` argv.

```
/bin/sh .../scratchpad/poller/run.sh ; exit=0
```

| mode (stub fresh-interval) | launcher rc | capture invoked with | ALERT |
|---|---|---|---|
| exit 1 + stderr traceback | 0 | `--fresh-interval-s 0` | yes ("check failed") |
| exit 0, empty stdout | 0 | 0 | no |
| stdout `WARNING something` | 0 | 0 | no |
| `15 on` | 0 | 15 | no |
| `nan on` | 0 | 0 | no |
| `-5 on` | 0 | 0 | no |
| `1.2.3 on` | 0 | **1.2.3** (capture would exit 2) | no (F3) |
| `5 on` | 0 | **5** (capture refuses, exit 2) | no (F3) |
| `0 refused: ...` | 0 | 0 | yes |
| stub SIGKILLed | 0 | 0 | yes |
| **real Python module (be2d18f8)** | 0 | 0 | **yes, false: `--date` required (F1)** |

Real-module reproduction (cwd = extracted tree, same as the launcher's `cd "$ROOT"`):
```
python -m nba.ops.live_capture fresh-interval            -> usage error, exit=2
python -m nba.ops.live_capture fresh-interval --date 2026-11-10  -> "0 off (config 0)", exit=0
```
Shell semantics: the script is `/bin/sh` with `set -u` and no `set -e`. `FRESH_OUT=$(...) || FRESH_OUT=...` cannot
abort, and both variables are always set. Paths are relative to `$ROOT` after `cd "$ROOT" || exit 1`, so cwd under
launchd is fine. `import yaml` is inside the `try`, so a missing pyyaml gives 0. The extra Python start adds about 1 s.

## Attack 2: watchdog fresh_problem() (PASS with F4/F5)

- Off: `heartbeat_fields()` is only merged when a poller exists (nba/live/capture.py:252), and the existing test
  `keys_on - keys_off == {"fresh_alive"}` confirms no `fresh_alive` key when off. So `fresh_problem` returns None. Silent.
- No live games: `fresh_live_games <= 0` returns None. Silent.
- Normal night: success is stamped on 200 **and 304** for game endpoints (espn_fresh.py:249-250), so halftime and
  timeouts (unchanged bodies) keep it fresh. The 5 min staleness equals the 300 s backoff cap, so it fires only after
  real repeated failure. FINISHED status skips the check. No false-alarm path found.
- Fields read (`fresh_alive`, `fresh_live_games`, `fresh_last_game_success_ts`, `fresh_consecutive_fail`,
  `fresh_stopped_reason`) all exist in `heartbeat_fields()` (espn_fresh.py:454-474). Empty-string timestamp parses to None
  and is handled.

## Attack 3: the fixture gate (PARTIAL: real for absence, not for content, F2)

- Branch tree: `git ls-tree -r be2d18f8 | grep -c fixtures/espn_core_real` gives 0. Fixtures are absent, so non-zero is refused (unit test).
- Junk fixtures (`{}` and `not json at all`) with config 15 give `15 on` (exit 0). The gate checks existence, not content.
- The 15 s floor is enforced twice (fresh_interval and nba/live/__main__.py `ap.error`). Correct.

## Attack 4: tests meaningful? (PARTIAL)

Run in the branch worktree (.claude/worktrees/agent-a8f583920ad6c8c22):
```
.venv/bin/python -m pytest -q -p no:cacheprovider tests/ops/test_fresh_enable.py tests/live/test_espn_core_real.py \
  tests/ops/test_live_capture.py tests/live/test_espn_fresh.py
-> 39 passed, 3 skipped; exit=1 (coverage fail-under on a partial run, not a test failure)
```
The 3 skips are the real-fixture tests (absent by design). Unit coverage of `fresh_interval` and `fresh_problem` is
good. Gaps: (a) no test of `main(["fresh-interval"])` the way the launcher calls it (F1 would have been caught); (b) no
launcher shell test; (c) a test asserts `{}` fixtures turn the poller on (F2).

## What was not verified

- Real launchd run (not touched, as instructed). Real `uv run` under the launchd environment was approximated with
  `env -i` and the venv Python, not `uv` itself.
- ruff/mypy/full pytest on the branch were not run (only the four relevant test files).
- The poller's behaviour against the real ESPN core API (no real bodies exist yet).

## Single biggest risk

If merged as is: a false ALERT in ALERTS.md at every game-night capture start. That teaches the reader to ignore the
`[live-capture]` alerts, including the real "capture exited rc=..." one. The capture itself keeps running.

## Recommended changes (not applied by me)

1. F1 fix + CLI test + stub-launcher test (needed before merge).
2. F2 content gate (needed before anyone sets a non-zero value).
3. F3 and F4 (cheap; do them together with F1).

No ledger, DECISIONS or holdout rows drafted (ops change, no claim).
