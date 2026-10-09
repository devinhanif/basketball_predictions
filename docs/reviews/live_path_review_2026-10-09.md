# Live-path code review, 2026-10-09 (before opening night 2026-10-20)

Scope: nba/daily, nba/props/forward.py + context_residual.py + full_support.py, nba/lineups,
nba/ingest|parse/availability.py, nba/markets, nba/parlay (shadow), nba/ops/watchdog.py, ops/*.
Method: read the code and the commits since 2026-10-09 06:00; two fixture-only probes (below);
`pytest tests/daily tests/lineups tests/markets` = 116 passed; `compileall` of nba/daily, props,
lineups, markets is clean (pipeline.py compiles; the f7a8c11 breakage is repaired by 8ea12aa).
No source was modified and no real DB was written.

## Verdict by category

| Category | Result |
|---|---|
| 1 Leakage (made_at >= tip, post-tip info in T-60/T-30 rows) | none found (see notes) |
| 1 LeakageError coverage of every arm | none found: primary, recency, _int, _lt, injury-elo all go into ONE `append_predictions` batch (pipeline.py:182); _t30 has its own batch (t30.py:397). Whole batch refused if any row has made_at >= tipoff |
| 2 Primary vs shadow separation | 1 major (M2): shadow errors can downgrade the primary |
| 3 Settle / scoring | 1 BLOCKER (B1: T30 arm never scored); rest clean |
| 4 Failure isolation, exit codes | M2, M3, m1 |
| 5 Concurrency | M5, M6 |
| 6 Time zones / DST | M1 (cadence), m2 (watchdog DST); all slate/ET/UTC conversions in code otherwise correct |
| 7 Ops scripts | M6, m1, m6; POSIX syntax OK |

## BLOCKER

### B1. The T-30 arm is never eligible for the pre-registered scoring table, so every T30 hypothesis is dead
- nba/daily/settle.py:171 (`made_at <= tipoff - lead`, lead 30 for `props_context_residual_t30`) vs
  nba/daily/t30.py run_t30 (`if made_at < cutoff: not_yet`, so every T30 row has made_at >= tip-30).
- Scenario: the lineups tick at 19:32 for a 20:00 tip writes T30 rows with made_at = 19:32 = tip-28.
  Eligibility needs made_at <= 19:30. The row is excluded from `forward_scores_elig`, so
  `paired_deltas(t30_*)` (checkpoint.py:211 reads only that table) has zero pairs, the min-n gates
  (60/120 dates) can never be met, and no alert fires. Only a row made at exactly tip-30:00.000 qualifies.
- Reproduced on a fixture (scratchpad t30elig.py): T30 rows at tip-29 min and tip-31 min and a T-60 row
  at tip-61 min; `settle_eligible_pending` selected only the tip-31 row. A tip-31 row cannot occur live.
- No test covers it (tests/lineups/test_t30.py never calls `settle_eligible_pending`).
- Fix: the T30 information cutoff is the snapshot, not made_at. For the T30 arm use
  `made_at < tipoff` and require `snapshot_fetched_at < tipoff - 30 min` (JSON field already stored,
  settle.py:216), i.e. lead 0 on made_at plus a snapshot test; or stamp made_at = cutoff. Add a test that runs
  run_t30 on the fixture, then settles, and asserts rows > 0. Rows already written (none live yet) stay
  valid; fix before 10-20 so that no T30 date is lost.

## MAJOR

### M1. Hourly :30 cadence makes the "T-60 production" row really T-90..T-120
- ops/install_launchd.sh:48 and ops/vm/install_cron.sh (minute 30 of every hour, same minute in ET);
  nba/daily/pipeline.py:120 (made_at = run start); settle.py:171.
- Scenario: tip 19:30 ET -> comparator needs made_at <= 18:30:00. The 18:30 run starts at 18:30:05 (plus any
  lsof wait, which can be 15 min) and is ineligible, so the scored row is the 17:30 run (T-120). Tip 19:00 ->
  the 17:30 run (T-90). Every :00 or :30 tip loses the closest run. The report cutoff inside that older run is
  its own made_at, so production is credited with a 90-120 min old information set while the T30 arm gets lineups
  plus the newer report. This inflates any T30-vs-T60 gain, and the docs call the arm T-60.
- Fix: schedule pretip at :20 and :50 (made_at about T-70 for :00 and :30 tips; runtime about 5.5 min per the rehearsal
  fits), or pass `--now` is NOT an option live. Update the watchdog JobSpec (at_minute, hours) and the prereg note.

### M2. Shadow-arm errors can downgrade the primary rows
- nba/daily/pipeline.py:543-556 (`_frame_preds` on the _int and _lt frames) run inside `_context_props`, which is
  wrapped by the try at 322-348. Any exception there (KeyError in `by_game[...]`, `json.loads` of a malformed
  field) discards the finished context primary and re-emits it as `recency_fallback` with
  `fallback_reason="context model failed"`.
- nba/props/forward.py:871-879: the int-variant rows are built inside the same loop as the primary rows
  (`to_integer_support`, `_ctx_row`); an error there aborts predict_slate_context. Only the _lt branch (forward.py:891-907)
  is guarded.
- `p_ge_full` is computed on the primary path now (forward.py `_row_from_dist` ~371, `_ctx_row` ~779);
  `full_support.from_dist` does `int(round(nan))` which raises on a NaN p_ge. Low probability, but it is a new
  unguarded step in primary code (the f7a8c11 claim "additive" is true for values, not for failure modes).
- Fix: build primary first; compute int/_lt/p_ge_full inside narrow try/except that drops only the shadow rows
  (and writes None for `p_ge_full`, which settle already treats as `unscorable`). Add a test that injects an exception in
  the int and lt builders and asserts the primary rows are identical to a run without the flags.

### M3. A failed ingest or schedule call aborts the whole run before any prediction
- pipeline.py:125-136: `incremental_ingest(...)` and `schedule_fn(...)` are not in a try; `fetch(season_str)`
  (`refresh_season_games`) and `fetch_schedule_nba_api` are single stats.nba.com calls with no fallback.
- Scenario: stats.nba.com times out at 17:30 -> traceback, rc 1, no rows for the whole hour; with hourly cadence
  (M1) the next run may already be after T-60 for the evening games, so those games have no eligible primary at all.
- Fix: wrap ingest in try (log `ingest: failed`, continue with the DB as is, features are strictly pre-slate anyway),
  and on schedule failure fall back to the lineups store tips (`schedule_from_lineups`) or the last
  `persist_live_tips` file; keep rc 1 so the alert still fires.

### M4. Train/serve mismatch of the "report" feature (primary arm)
- Serve: nba/daily/predict.py:272-304 `slate_report_outs` takes the league-wide `max(as_of)` snapshot, any game, any
  team, <= 36 h old, and gives every slate game `has_report=1` with the OUT set of the whole league.
- Train: nba/sim/usage_redistribution.py:603-626 `latest_pretip_flagged` is per game_id; a game with no rows of its own is
  "no report".
- Scenario: the 11:00 ET cutoff for a noon game; the newest snapshot is last night's 23:00 report covering only last night's
  games. A team that did not play last night gets `has_report=1, nobody out` even though its star is out on the
  morning report that is not yet published. Training never saw "report exists but my game is not in it".
  After 8ea12aa the upcoming game rows now carry the slate game_id (injury.py extra_games), so a per-game query is possible.
- Fix: in `slate_report_outs` take the latest snapshot that has rows for that game_id (same rule as training) and mark absent
  otherwise. Verify with a fixture where the newest snapshot omits one slate game. I did not measure the size of the effect.

### M5. DuckDB writer races between the lineups job and the daily job (no shared lock, check-then-act)
- ops/nba_lineups.sh:59-64 and ops/nba_daily.sh:85-89 both do `lsof nba.duckdb` and then start a writer. Two different lock
  dirs (`lock`, `lineups_lock`) mean the daily job does not exclude run-t30, and the lineups job does not exclude
  `predict`.
- Scenario: a 19:00 tip -> T-30 = 18:30 = the pretip start time. Both pass the lsof check in the same second; one
  gets "Could not set lock on nba.duckdb". If it is `predict`, the hour's primary predictions are lost (step alerts, no retry, M1
  makes the next run too late for some games). Also `market_capture`/`parlay_shadow` open nba.duckdb read-only between steps, and
  a run-t30 that started in the gap makes them fail (idempotent, so only an alert). The ingest queue's `try_load` has the same
  TOCTOU.
- Fix: one lock for any nba.duckdb writer (e.g. both scripts `mkdir data/ops/nba_db_lock` around the DB-touching command, with
  a short wait/retry in lineups instead of "defer to next tick"), and a 2-3 attempt retry around the `predict` step on the
  specific IOException. Also stop the history ingest queue before 10-20 (it holds nba.duckdb during load steps; pretip would
  wait 15 min then exit 1).

### M6. A skipped (lock-held) run exits 0 and overwrites the real heartbeat
- ops/nba_daily.sh:44-45 installs the heartbeat trap BEFORE the lock; 71-78 `exit 0` on "another job holds lock" runs
  `hb_end 0`, which writes `{start: now, end: now, rc: 0}` over the heartbeat of the job that is actually running (or hung).
  Same in ops/nba_lineups.sh:34 and 41-49.
- Scenario: the 08:00 morning job runs 100 min (rclone, post-game fetch); the 09:30 and 10:30 pretips are skipped with a log
  line only; the watchdog sees a fresh rc-0 heartbeat for `daily-pretip` and stays quiet; the day's early T-60 rows are missing
  without any alert. A stale lock left by SIGKILL or power loss blocks every pretip for up to 180 min the same way
  (no pid or liveness check).
- Fix: do not write the heartbeat (or write rc 75 with a distinct field) on the skip path, call `alert` when a pretip is skipped, and
  record the owner pid in the lock dir so a dead owner is cleared immediately.

### M7. Parlay shadow can log in-play prices
- nba/parlay/__main__.py:72-81 takes the latest `kalshi_prices` row per ticker with no `ts < tip` bound and `load_slate` includes
  games already tipped; shadow.py:56 is first-write-wins per `date|ticker|side`.
- Scenario: a market that was unmapped or unpriced earlier gets its first shadow row at 21:30 ET for a 19:00 game using a live price;
  that row enters the shadow track record and, if flagged, `log_trade` (paper_trades) with a price that already reflects the game.
- Fix: skip any game with `now >= tip` and any price with `ts >= tip` in `evaluate` before logging. Scope note: this is the shadow
  path, not the forward prop test.

## MINOR

- m1. nba_daily.sh:61-63 treats rc 2 of `predict` and `refs` as informational. argparse usage errors and unmatched-team results
  also exit 2, so a mistyped flag in the script would be silently ignored. Give "tipped games refused" and
  "officials unmapped" a distinct code (e.g. 3) or match on output. `checkpoint --look auto` returning 2 (GateNotMet) alerts and sets HB_RC.
- m2. nba/ops/watchdog.py:93 builds yesterday's slots with `now.replace(hour=h) + timedelta(days=-1)` on a fixed-offset tzinfo
  (`astimezone()` of a local naive). On 2026-11-01 (fall back) yesterday's slots are 1 h late, so a spurious "daily-pretip stale" /
  "daily-morning stale" alert is likely until the first run after 09:30 CST (deduped 6 h). Spring-forward is the mirror case
  (a real miss could be masked). All other ET/UTC code uses zoneinfo and is correct, including `candidate_slots`, `to_report_dt`, `slate_for_date`.
  The roster switch `[ "$TODAY_ET" \< "2026-11-04" ]` was tested under sh, dash and bash.
- m3. nba/lineups/__main__.py:42-49 writes the schedule-checked marker before the call succeeds; one transient failure means no schedule
  tips for the day and the status-text tips (pre-game only) are the only source. Write the marker after success or after N failures.
- m4. Official rosters are cached per date (first fetch at 10:30 ET serves all day, including run-t30). Late signings or waivers never
  reach later hourly runs. Accept or key the cache by hour.
- m5. made_at is stamped at run start (pipeline.py:120) while rosters, report probes and box-score pulls happen later in the run. Conservative for
  the report (bounded by made_at), but a roster fetched 4 min after made_at is labelled as earlier information. Document it.
- m6. Inner `"$UV" run python ...` steps lack `--no-sync` (the outer launchd call has it). A pyproject/lock change would trigger a sync
  (network) in the middle of a pretip step. `uv lock --check --offline` passes today.
- m7. `cp nba.duckdb data/backups/...` (nba_daily.sh:127) ignores a leftover `.wal`; take the backup only after confirming no `nba.duckdb.wal`
  exists, or use `EXPORT`/`COPY FROM DATABASE`.
- m8. run_t30 that raises (not LeakageError) records no decision and retries every 5 min until tip, appending an ALERTS.md line per tick.
  A game decided only after a crash between `append_predictions` and `_record` can get two row sets (harmless to scoring, which takes the latest).
- m9. Model cache write (forward.py `_load_or_fit_models`) is not atomic (pickle then meta.json); the fingerprint is n_rows + max_date only, so
  edits to availability/players_static for old games do not invalidate it.
- m10. A pretip that finishes the report probe loop without a report still returns "ok" slot when the PDF exists but parses to 0 rows
  (injury.py: returns slot after `puller`); status reads "ok" with no new rows.
- m11. Lineups job is idle 03:00-09:00 ET and the first pretip is 10:30 ET; any tip before about 11:30 ET (international game) gets no T-60 row.
  Not expected on the 2026-27 opening weeks, listed for completeness.

## Checked and clean (with evidence)

- Leakage: history frames filter `game_date < slate` (forward.py:655-675); availability history joins `games` (excludes the slate);
  report rule `min(made_at, tip-60)` for props (predict.py:285) and the lead filter inside `predict_games`; candidate_slots are strictly before
  the first tip; T30 snapshot = latest fetched strictly before tip-30 (lineups/store.py:175-186) and inactive/starters come only from that snapshot
  (t30.py:244-248); the _lt shadow attaches realized `minutes` to TRAINING rows only (forward.py `_lower_tail_rows`); the Kalshi as-of
  state filters `ts < made_at` and a stale copy can only be older (markets/asof.py).
- `to_integer_support` does not mutate its input; `from_samples(q)` equals `from_samples(to_integer_support(q))` exactly (0 mismatches in 800
  random trials), so the _int p_ge_full is consistent, and the _int decision metric is pinball19 (checkpoint.py:64-67), not a degenerate zero delta.
- `crps_int_surv` tail algebra (y > K) and `pit_bounds` are correct; settle is idempotent (NOT EXISTS on the natural key in both tables);
  DNP = no `minutes > 0` row in a game that has box rows; `has_box` gates waiting for ingestion; win rows score without a box.
- Same-run pairing for _int/_lt uses run_id and counts mismatches rather than dropping silently.
- Heartbeat/EXIT trap composition in both scripts is correct (the second trap re-calls `hb_end`); `step` captures rc correctly with `set -u`; no `set -e`
  so a failing shadow step does not stop later steps; market_capture, refs and parlay_shadow run after `predict` and cannot affect it.

## Not verified

- Live behaviour of the daily-lineups feed (`Inactive` rows, timestamp meaning, publication time) and the real 2026-27 `gameDateTimeUTC`
  values (TBD tips may be placeholders, which would make `made_at < tipoff` fail or pass wrongly).
- Whether the Mac sleeps through launchd slots (no `caffeinate`/`pmset` in install_launchd.sh); coalesced missed slots are lost.
- Size of the M4 effect on primary CRPS; whether kalshi snapshot writes collide with `parlay evaluate`'s read-only ATTACH (no copy fallback there,
  unlike nba/markets).
- Anything on the VM (cron) beyond reading install_cron.sh; the two hosts must never both write.
- Probes used: scratchpad `t30elig.py` (fixture DB, in-memory) and the inline full_support equivalence check. No real DB opened for write.
