# Live capture merge review (2026-10-24)

Reviewer: adversary. Subject: getting the in-game capture `nba/live/` onto main so the launcher
(`agent/steward-live-capture-launchd`, 23577a1) can schedule it.

Verdict: APPROVED

**Recommendation: (b) extract.** I built `agent/market-live-capture-only` from main (e72b640).
- It carries `nba/live/` and `tests/live/` byte-identical to cc1c4c3, the code running tonight.
- It adds one negative test file, `tests/live/test_no_orders.py`.
- It adds no paper trader, no plist, no `configs/` and no `nba/daily` change, so no replay oracle
  is needed and no HOLDOUT_ACCESS_LOG row was written.

**Approval covers merging this branch only.** Installing the launcher is a separate gate: it must
not happen while the hand-started 2026-10-24 capture (pid 42952) is running (finding B1).

## Why not (a), the whole branch

`agent/market-paper-trader` (cc1c4c3) is 39 files and 6,402 lines on top of main. Besides
`nba/live`, it carries:
- `nba/papertrade/` (12 modules, its own DuckDB), unreviewed;
- `configs/kalshi_aliases.yaml` (+117) and `configs/kalshi_aliases_candidates_2026-10-21.yaml`. The
  alias file is a live-path config that main has edited since (dc8200a), so merging it needs an
  oracle and its own review.
- `configs/papertrade*.yaml` and `docs/prereg/PAPER_TRADER.md` (a prereg draft);
- `ops/launchd/local.nba.live-capture.plist`, the old fixed-10:00 template that must not reach
  main (duplicate-writer risk, below);
- `ops/launchd/local.nba.papertrade.plist`.

The capture depends on none of it:
- `nba/live` imports only `nba.live.*`, the standard library and `httpx` (`httpx>=0.27` is in
  pyproject; 0.28.1 is in main's venv).
- No test outside `tests/live` imports it.
- The only coupling is a read of `data/papertrade/open_legs*.json` in `Capture.leg_tickers`
  (`nba/live/capture.py:368-383`). That file is optional: when it is missing or unreadable the
  capture reads it as no legs.

## The task's checks

| # | Check | Command / evidence | Result |
|---|---|---|---|
| 1 | Writes only write-once raw under `data/raw/live_<date>/` plus its heartbeat | Read `nba/live/store.py`, `capture.py`, `offer.py`, `__main__.py`; `test_end_to_end_capture_and_offer_only_get_allowed_paths` asserts every file written is under `raw/` or is the heartbeat | PASS, with one note. Body files are write-once (`store.py:45-51`: an existing path gets a numeric suffix). `manifest.jsonl`, `ticks.jsonl`, `errors.log` and `cashout_offers.jsonl` are append-only logs in the same folder. `heartbeat.json` there, and `data/ops/heartbeat/live_capture.json`, are replaced atomically. Reads: `data/papertrade/open_legs*.json`. |
| 2 | Never opens nba.duckdb; no orders; no credentials | `tests/live/test_no_orders.py` (new): forbidden-string grep, allow-listed paths, GET-only HTTP layer, an AST check that no `duckdb`/`nba.db`/`nba.daily`/`nba.papertrade`/`research`/`os` import exists and that nothing reads `environ`/`getenv`, and an end-to-end sweep, tick and offer over a transport that fails on any non-GET request, other host, off-list path, `Authorization` or `Cookie` header | PASS |
| 3 | Idempotent on restart | `test_restart_on_same_day_never_overwrites_a_raw_file`: two Capture instances, one out dir, one frozen clock (same-second names). 0 duplicate manifest paths; every sha256 matches. Real data, read-only (`scratchpad/attack/gaps.py`): live_2026-10-22 has 19,811 manifest rows, 0 duplicate paths, 0 missing files; 10-23 has 908/0/0; 10-24 has 718/0/0 so far. | PASS for files. Content is not idempotent: see m2. |
| 4 | Heartbeat matches the launcher watchdog (date, status, mtime freshness) | `scratchpad/attack/hb_contract.py` loads the launcher's own `nba/ops/live_capture.py` from 23577a1 and runs it on the real heartbeat. | PASS. Live 2026-10-24 heartbeat at 27 s age gives `problem=None`. Status vocabulary: waiting, running and schedule_unreachable are fresh-OK and stale after 6 min; failed always alerts; done and no_games are terminal. Real tick gaps are 60 s on every night (0 gaps over 300 s). Merged tree: `tests/ops/test_live_capture.py` + `tests/live` + layering gives 48 passed. |
| 5 | No duplicate writer from the old `local.nba.live-capture.plist` | The template is not on this branch. `ls ~/Library/LaunchAgents \| grep nba` and `launchctl list \| grep nba` show neither live-capture plist installed. | PASS for the template. The duplicate-writer risk is real elsewhere: see B1 and M1. |
| 6 | Layering test passes | `tests/test_layering.py`, 10 tests, in the 33 passed below | PASS. `nba.live.__main__` is not yet an ENTRYPOINT (m1). |

### Verification (unpiped, in the branch worktree)

```
.venv/bin/ruff check nba/live tests/live                          -> All checks passed!  rc=0
.venv/bin/ruff format --check nba/live tests/live                 -> 11 files already formatted  rc=0
.venv/bin/mypy nba/live tests/live                                -> Success: no issues found in 11 source files  rc=0
.venv/bin/python -m pytest -q --no-cov tests/live tests/test_layering.py -> 33 passed  rc=0
merge simulation (this branch + 23577a1, discarded):
  pytest tests/live tests/ops/test_live_capture.py tests/test_layering.py -> 48 passed  rc=0
```

The repo-wide `ruff check .` (83 errors), `ruff format --check .` (8 files) and `mypy` (38 errors
in 3 files) fail on files already on main (`research/scratch/*_2026-10-2x.py`,
`app/fixtures/build_export_best_real.py`). None of those failures is in `nba/live` or `tests/live`.
They are not this branch's to fix and are recorded as a separate loose end.

Full suite: not completed. It was at 1 % after 3 min 20 s, running beside another session's
full suite, and I stopped it. Instead I ran every test that scans the `nba/` tree, since an
additive package can only break those. The files were found with
`grep -rlE 'rglob\(|walk_packages|iter_modules' tests`: `tests/daily/test_tip_source_default.py`,
`tests/ingest/test_reporters.py`, `tests/kalshi/test_db_safety.py`, `tests/odds/test_combo_pull.py`,
`tests/odds/test_the_odds_api.py`, `tests/shadow_llm/test_cli_and_hook.py` and `tests/test_layering.py`,
plus all of `tests/ops` and `tests/live`:

```
.venv/bin/python -m pytest -q --no-cov tests/live tests/test_layering.py tests/daily/test_tip_source_default.py \
  tests/ingest/test_reporters.py tests/kalshi/test_db_safety.py tests/odds/test_combo_pull.py \
  tests/odds/test_the_odds_api.py tests/ops tests/shadow_llm/test_cli_and_hook.py -> 315 passed  rc=0
```

## Findings (severity-ranked)

**B1 (blocker for the install timing, not for the merge): installing the launcher tonight starts a
second writer for 2026-10-24.**
- The running capture (pid 42952, started 21:34:58 CDT from wt_live at cc1c4c3) writes main's
  `data/` through the worktree's `data` symlink.
- It holds no `data/ops/live_capture_lock` (`ls` returns "No such file or directory").
- Its heartbeat says `status=running`, `date=2026-10-24`, `window_end=06:00Z`.
- The launcher's `plan()` returns `('start', 'first tip 22:00Z, window to 06:00Z')` now, because
  `running` is not in `FINISHED` (`nba/ops/live_capture.py:87`), and `acquire_lock` succeeds.
- So a `launchctl bootstrap` with `RunAtLoad` before about 00:00 ET (23:00 CDT) would run a second
  `nba.live capture --date 2026-10-24` into the same folder.

Fix: install only after pid 42952 has exited and
`data/ops/heartbeat/live_capture.json` shows `"date": "2026-10-24", "status": "done"`. The window
ends at 06:00Z (01:00 CDT) plus the end sweep, so tomorrow morning is the simple choice. Do not stop
42952.

**M1 (major; launcher branch and a follow-up in nba/live): single-writer depends on every start
path taking the launcher's lock, and two start paths do not.**
- The capture itself takes no lock.
- The 10-23 and 10-24 captures were started by hand.
- The launcher's watchdog alert text tells a human to run `uv run python -m nba.live capture --date
  {day}`, which bypasses the lock (the `live_why` Alert string in `nba/ops/watchdog.py` on
  23577a1).

`RawStore` is not safe for two processes. `scratchpad/attack/dup_writer.py` ran two writers on one
folder and one UTC second: one writer raised
`FileNotFoundError ... 20261024T230000Z_1.json.gz.tmp`. Both writers pick the same `.tmp` name
(`store.py:49`), and `Path.rename` replaces an existing target on POSIX, so a write-once body can
be overwritten in the race. Inside a tick, that exception costs the rest of the tick.

Fixes (recommended, not applied):
1. Launcher branch: drop the `(or uv run python -m nba.live capture --date ...)` clause from the
   alert and keep only `launchctl kickstart`.
2. Follow-up branch in `nba/live`:
   - `Capture.run` takes `fcntl.flock(LOCK_EX | LOCK_NB)` on `<out>/.writer.lock` and, if the lock
     is held, beats nothing and returns 0 with "already running for <date>". This makes the
     invariant hold for every start path.
   - `RawStore.save` writes `tmp = f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"` and
     publishes it with `os.link(tmp, path)` (fails with `FileExistsError`, then retry with the next
     suffix) instead of `rename`.

**m1 (minor): `tests/test_layering.py` ENTRYPOINTS does not include `nba.live.__main__`.** Once
the launcher runs it, the capture is live path, and the research-import ratchet should cover it.
Fix: add `"nba.live.__main__",` to `ENTRYPOINTS` in the launcher branch. That branch is the commit
that makes the capture scheduled; today `nba.live` imports no research, so the test stays green.

**m2 (minor): a restart is file-idempotent but not content-idempotent.** `State` is in memory. A
restarted capture does three things again:
- reruns the start sweep (about 280 requests);
- re-saves `kalshi_rules`;
- re-pulls public trades from `min_ts=None` (up to 5 pages per ticker), so the same `trade_id`
  appears in more than one `kalshi_trades` file.

Fix: every reader of `kalshi_trades` dedupes on `trade_id`. Whoever owns the T510/T510b/LONGSHOT
scorers should confirm the scorers do so. Nothing on this branch reads it.

**m3 (minor, launcher): a capture that dies after 00:00 ET is restarted for the new date.**
`TODAY_ET` rolls over, so the previous slate's tail (to last tip + 4 h) is lost, and the watchdog
judges only `local.date()`. Fix: `plan` also returns `start` for yesterday's date while yesterday's
window is still open, and the launcher passes that date.

**m4 (minor, launcher docs):** `docs/DAILY_PIPELINE.md` (launcher section, "Dependency") and the
plist comment name `agent/market-paper-trader` as the source of `nba.live`; point them at
`agent/market-live-capture-only`.

The launcher branch also conflicts with current main in `docs/DAILY_PIPELINE.md`. Both sides
append a section at the end of the file; keep both.

**i1 (info):** no beat is written during a sweep, whose budget is up to 420 s against
`STALE_AFTER` = 5 min. The real sweeps took under 60 s (all tick gaps are 60 s), so this is
theoretical.

A `schedule_unreachable` capture beats every 300 s, so the watchdog will flag it when it is just
past 5 min old. That is fine: an unreachable schedule deserves the alert.

## Playbook check

Model class: none. This is data-capture infrastructure, not a model.
1. Information or capacity: it adds information. It records in-game prices that cannot be
   backfilled; four frozen forward rules plus two drafts need them on at least 72 % of nights.
2. Enough data for this class: n/a.
3. Test validity: the tests use recorded fixtures and a mock transport. No network, no 2025 data.
4. Lineage and inherited guards: the no-orders guard is inherited from
   `tests/papertrade/test_no_orders.py`, ported so it ships with the package.
5. What it is scored against: n/a. No claim, no metric.

The six statistical checks (shuffled labels, planted future, missingness, knockout, replication,
integer support) do not apply: there is no claimed win. BH family: none. Power/MDE: n/a.

## Most likely failure mode if this approval is wrong

Two writers on one night's folder. Either the launcher is installed before 42952 exits (B1), or
someone follows the watchdog's direct-command advice while the launcher's capture runs (M1).
Bodies could then be lost or overwritten in a same-second race, and every Kalshi request would be
doubled against the public rate limit.

## Not verified

- Long-run behaviour of the launcher under launchd (TCC, caffeinate, sleep). It is not installed,
  by instruction.
- That the downstream scorers dedupe trades by `trade_id` (m2).
- The paper trader, `configs/kalshi_aliases*` and the playbook arm on cc1c4c3. None of these was
  reviewed, and none is approved by this review.
- The full test suite was not run to completion; the 315 tree-scanning and ops tests were. Repo-wide lint and type failures exist on main.

## Merge and install sequence (main session)

1. `git merge --no-ff agent/market-live-capture-only` into main. No oracle is needed: there is no
   `nba/daily`, `ops/` or `configs/` change. Push.
2. Launcher branch, before its own merge:
   - rebase or merge main and resolve the `docs/DAILY_PIPELINE.md` append conflict (keep both);
   - apply M1 fix 1 (alert text), m1 (ENTRYPOINTS) and m4 (doc pointer);
   - run its gates. It touches `ops/` and `nba/ops/watchdog.py`, so it needs a HOLDOUT_ACCESS_LOG
     row first, a replay oracle IDENTICAL on its head, and Devin's go (live path).
3. Merge the launcher branch. Do NOT merge `ops/launchd/local.nba.live-capture.plist` from
   `agent/market-paper-trader` or its siblings, now or later.
4. Install only after `ps -axo pid,command | grep "nba.live capture"` shows no process, and the
   heartbeat reads `"date": "2026-10-24", "status": "done"` (expected after about 01:05 CDT
   10-25). Install is Devin's step:
   `cp ops/launchd/local.nba.live-capture-start.plist ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.nba.live-capture-start.plist && launchctl kickstart -k gui/$(id -u)/local.nba.live-capture-start`.
   The kickstart should log `early start at ...` for 2026-10-25 in
   `data/ops/heartbeat/live-capture-launch.json`.
5. From then on, start captures only through `launchctl kickstart`, never by hand, until the M1
   follow-up lands.

## Drafted rows (not appended)

DECISIONS (draft):
`| 2026-10-24 | nba.live merged alone (agent/market-live-capture-only, extracted byte-identical from cc1c4c3 plus capture-scoped no-orders/restart tests); paper trader, alias configs and the old fixed-10:00 live-capture plist stay off main. Launcher install waits for the hand-started 10-24 capture to finish (second-writer risk). | Adversary review docs/reviews/live_capture_merge_review_2026-10-24.md (APPROVED; B1 install timing, M1 single-writer lock). | A paper-trader review that approves it as a whole; the M1 lock landing in nba/live. |`
