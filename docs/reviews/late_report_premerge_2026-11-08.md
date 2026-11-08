# Pre-merge adversary review: agent/modeler-late-report (870d8dc), 2026-11-08

Subject: LATE_REPORT shadow `props_context_residual_late` (run-t15 `--log-late-shadow`), its settle
eligibility, and the JARVIS display line. Live path (ops/nba_lineups.sh every 5 min; settle in the morning job).
Background read: DECISIONS 2026-11-02 19:44 (approval, statuses out + doubtful), 2026-11-02 21:01 (built),
2026-11-08 10:45 (do-all scope item 4); NEXT_SESSION top block item 3.

Worktree: /Users/devin/Downloads/nba-wt-advlate, detached at 870d8dc, tracked data/ moved to the scratchpad
and replaced by a symlink to the main checkout's data/ (`ls -la data` -> `data -> /Users/devin/Downloads/nba-prediction/data`).
Replay oracle NOT run (holdout row first, main session). No season-2025 forecast read. Branch not modified.

**Adversary class: WOUNDED** (real and leak-free; merge-able after the one major fix below; no change to production rows or frozen scoring)

Verdict: BLOCKED

Blocked on M1 only; it becomes APPROVED when M1 is fixed and the oracle is IDENTICAL.

This is a code review of a shadow with no result yet. No gain is claimed, so the result-attack checks (shuffle,
knockout, replication, integer support, BH, power) have nothing to bite on. They are listed as n/a with that reason.

## Answers to the brief

1. **Production forward rows, forward_scores, FORWARD_PREREG scoring: unchanged.**
   - The shadow writes only `model_name = 'props_context_residual_late'`. The test asserts zero rows under any other
     name (tests/daily/test_late_report.py:122-124).
   - Every consumer that picks a production number filters by model name and `comparison_only`:
     parlay top_source.py:146,415 (`PROPS_MODEL and not comparison_only`), export best_model.py:327,371, explain/slate.py
     (`MODELS` list), shadow_llm payload, forward/scorecard.py (named models), checkpoint.py:234 (named model),
     ops/coverage.py:238. Readers with no model filter (export reader.py:153, report.py:378/524, coverage.py:429)
     only display or count. They will gain a `props_context_residual_late` section or rows, but no primary number moves.
   - `forward_scores`: `settle_pending` already scores every model it finds. The late rows land there through
     unchanged code. Existing rows are untouched.
   - `forward_scores_elig`: the diff adds one model name to PREREG_MODELS and one CASE branch
     (`WHEN model_name = ? THEN made_at < tipoff`) placed after the T-30 and T-15 branches and before ELSE. The
     parameter order matches the placeholders (settle.py:355-358). Rows for every other model are computed exactly as before.
   - docs/FORWARD_PREREG_2026_27.md and docs/prereg/FORWARD_T15_2026_27.md are not in the diff. The frozen BH
     family (m = 9) is named arm by arm, so a new model name cannot enter it.
   - **Oracle expectation: IDENTICAL (canonical dump 89889f6d).** Why: replay_season.py never calls run-t15 or
     nba.daily.late_report. The test proves this through the AST (test_late_report.py:258-276). Replay does call
     settle_pending -> settle_eligible_pending (settle.py:155), so the modified SQL runs. But with zero late rows the new
     branch matches nothing, and every other branch is byte-for-byte the same predicate with the same bound values.
     JARVIS is not in the replay. If the oracle comes back DIFFERENT, suspect the worktree data/ symlink first
     (memory: worktree-data-dir), not this diff.

2. **Known-at: correct.**
   - Decision window `[tip - 15, tip)` uses the real tips from `schedule_from_lineups`, the same schedule as the T-15 arm.
     A run at `made_at >= tip` records `run_after_tipoff` and writes nothing (late_report.py:356-361). The store
     independently refuses a batch with any `made_at >= tipoff` (LeakageError).
   - The report is the latest snapshot of that game with `as_of <= made_at` in ET (serve_pretip_flagged,
     lead 0, age <= 36 h). It never uses a later report, and a second guard raises if one slipped through (late_report.py:153-155).
   - The lineup comes only from a snapshot fetched strictly before tip - 15 (load_game_lineup, the T-15 rule).
   - Attacks run (scratch, below): a lineup fetched at tip - 12 is not used; a report stamped exactly at made_at is
     used and one stamped 1 s later is not; a run at exactly tip - 15 is ready and every row has made_at < tipoff. 3/3 pass.
   - Note: `made_at` is the run's start time. A run that starts before tip and finishes after it still writes rows
     stamped before tip. That is correct, because the information set is frozen at made_at. Live runs take seconds;
     it is recorded here only so nobody calls it a leak later.

3. **Failure isolation: good for the tick and the morning job, with one minor JARVIS gap.**
   - nba_lineups.sh: the shadow runs in-process after `print(t30)`, inside `_late_shadow`'s `try/except Exception`,
     and the import also sits inside the try. `main` returns 0 whatever happens (__main__.py:449-451). The planted
     RuntimeError test proves one ALERTS line and False (test_late_report.py:238-255). The T-15 arm's rows are
     committed before the shadow starts, so a shadow crash cannot remove them.
   - DuckDB lock: **no new connection or lock**. The shadow reuses run-t15's write connection and the T-15 lock.
     The added hold time is one context forecast with a seeded cache hit. On the cache-miss path, today's
     production fits took 6.2-6.5 s (data/models/context_residual/2026-11-0x/meta.json fit_seconds) and the T-30 fits 6.7-7.9 s,
     so the added hold is seconds, against run-t15's 240 s wait budget and the 30 min T-15 lock staleness.
     Contention with the kalshi snapshot and pretip jobs grows by those seconds per due tick and no more.
   - JARVIS: `annotate` opens nba.duckdb read-only with one attempt, and any exception returns the plays
     unchanged. Measured on a private copy of nba.duckdb (94,641 forward rows; 7 games from 11-07; row count printed,
     no scores): `read_late` 0.464 s cold, 0.035-0.038 s warm. So the read-only hold that could delay a writer is under 0.5 s.
     On a locked store, open_ro sleeps 1.5 s once and then gives up.
     Gap m1: `_late_lines` (runner.py:124-128) evaluates `rt.cfg.paths["nba_db"]`, `rt.kb.name` and the import
     outside annotate's try. Each is present today, so this is not a live crash, but it is the pattern behind the 10-28
     every-tick crash.
   - Morning settle: the new SQL is covered end to end by test_settle_scores_late_rows_with_crps_and_eligibility
     (real settle_pending on a fixture DB). A placeholder mismatch would have failed it.

4. **Settle change: alters no existing scored table row.** It only adds `forward_scores_elig` rows with
   `model_name = 'props_context_residual_late'`, `lead_min = 0`. That is an eligibility definition written in code
   before the scoring prereg exists. Fine as data capture, but the prereg must adopt it explicitly or override it (n3).

5. **Status scope: out + doubtful, as approved (DECISIONS 2026-11-02 19:44).** `LATE_STATUSES = ("out", "doubtful")`
   at nba/daily/late_report.py:80. It is used for both exclusion and teammate-out flags (late_report.py:149, 211). It is
   stamped on every row (`report_statuses`, late_report.py:273) and asserted in tests (test_late_report.py:112, 139-149).
   The code implements the approved design exactly.
   **Note, not a defect:** the models were trained with OUT-only teammate flags, so serving DOUBTFUL as out is a
   train/serve difference. It is disclosed in the module docstring (lines 14-16) and on each row. The scoring prereg
   must handle it (n1).

6. **Tests: the stale-row case and the after-tip refusal are both covered.**
   - Stale row: tests/jarvis/test_late_report.py:34,77 (production row 18 h old, late arm excluded the player ->
     "George OUT (production 16.7)").
   - After tip: test_refused_after_tipoff_and_not_yet (run at TIP -> 2 games `run_after_tipoff`, 0 rows) and
     test_store_refuses_a_late_batch_with_any_row_at_or_after_tip.
   - Not covered: the `roster_source == "official"` branch (late_report.py:377, the live setting through 11-30,
     ops/roster_source.sh). I checked its call by hand against pipeline.py:655-666 and the positional order matches.
     The LeakageError re-raise (399-401) and the no_model_rows branch (405-408) are also uncovered.

## Defects (severity-ranked)

| id | file:line | severity | finding | smallest fix |
|---|---|---|---|---|
| M1 | nba/jarvis/late_report.py:83-85 | **major** | The display says `"<name> OUT"` for every excluded player, but exclusion is OUT **or DOUBTFUL** (late_report.py:80) and `report_excluded` stores no status. Devin reads this line on game night to place money, and a doubtful player shown as OUT is a wrong fact. The parallel branch agent/market-out-gap (worktree nba-wt-outgap, uncommitted tests "doubtful_is_not_treated_as_out") will stop JARVIS offering OUT players and keep offering doubtful ones. Once both merge, the "OUT" line will fire mostly on DOUBTFUL players. | Change the text to `f"latest report: {name} OUT/DOUBTFUL{tail}"` and update tests/jarvis/test_late_report.py:67,77. Better but larger: store `{pid: status}` in `report_excluded` (late_report.py:260) and print the real status. |
| m1 | nba/jarvis/runner.py:124-128 | minor | The import, `rt.cfg.paths["nba_db"]` and `rt.kb.name` sit outside annotate's never-raise guard. A missing key or a broken import would raise in `_tick`/`run_slate` every tick, the same shape as the 10-28 incident. | Wrap the body: `try: ... except Exception: return plays`. |
| m2 | nba/jarvis/late_report.py:52-67 | minor | The numeric line compares the late mean with production's **latest** row, which can be the stale pre-exclusion row (the 11-02 gap): a player production dropped at T-60 and the late report restored. The line then reads "X 22.0 vs 21.5" against a number production no longer stands behind. | In read_late, keep a production mean only if its made_at equals that game's max production made_at; otherwise show `(production: no current row)`. |
| m3 | nba/daily/__main__.py:265-272 | minor | The shadow seeds from DEFAULT_T30_CACHE even when run-t15 is given `--model-cache`, and it repeats `fetch_schedule_nba_api` (a second stats.nba.com call) under `--schedule-nba-api`. Neither is used by nba_lineups.sh. | Pass `seed_t30_cache=args.model_cache or DEFAULT_T30_CACHE` and reuse the slate the T-15 arm already fetched. |
| m4 | nba/daily/__main__.py:276-285 | minor | A persistent shadow failure appends one ALERTS line on every due tick (about 3 per game, ~30 a night). It is noisy but harmless. | Append at most one line per date per message (check the last ALERTS line or a dated sentinel in data/ops). |
| m5 | tests/daily/test_late_report.py:288 | minor | New mypy error: `Module "nba.props.forward" does not explicitly export attribute "build_features"` (the other 5 mypy errors are pre-existing on main). | `import nba.props.forward as fwd` then `monkeypatch.setattr(fwd, "build_features", spy)` with `# type: ignore[attr-defined]` on line 288, or export the name. |
| m6 | nba/daily/late_report.py:377 | minor | The official-roster branch (the live setting) has no test. `_official_roster` can call `autofill_players_static`, which writes players_static through the shadow's connection. In practice the T-15 arm has filled it a second earlier, so this should be a no-op. | One test with `roster_source="official"` and a stub fetcher/static_fetch asserting no new players_static rows after a T-15 run. |

## Notes for the scoring prereg (not defects)

- n1 Train/serve: DOUBTFUL counted as out at serve, OUT only in training (approved, disclosed).
- n2 Population: the late arm writes no row for an excluded player, while production has a (possibly stale) row
  that settles as DNP or scored. Pairing must be on the intersection or define DNP handling on both sides
  (T001 was retracted on DNP handling).
- n3 Eligibility: `lead_min = 0`, `made_at < tipoff` (settle.py LATE_MODEL branch) is already in forward_scores_elig;
  the prereg should adopt or override it.
- n4 Missing decisions are correlated with T-15 failures. The shadow runs only when run-t15 runs and succeeds, and a
  last-game window missed on all three ticks leaves no decision row at all (no `run_after_tipoff` is written once
  `due` is false). Coverage must be counted from the schedule, not from forward_late_decisions.
- n5 JARVIS line threshold MIN_DIFF = 1.0 pt of mean was chosen by the builder (not in the approval). It is display only.

## Check table

| check | command / evidence | n | CI | result |
|---|---|---|---|---|
| 1 shuffled labels | n/a: no result exists; shadow has 0 settled rows | 0 | n/a | n/a |
| 2 planted future (report, lineup, boundary) | `pytest --no-cov $SCRATCH/attack/test_attack_late.py` (worktree rootdir) | 3 tests | n/a | PASS (3 passed, exit 0) |
| 2b lag +1 day | n/a: no score to drop | - | - | n/a |
| 3 missingness | code read: `report_as_of` NULL = no report ≤ made_at (pre-tip knowable); lineups_known from snapshot < tip-15; exclusion statuses pre-tip | - | - | PASS (code read); n4 recorded |
| 4 knockout | n/a: no gain | - | - | n/a |
| 5 replication | n/a: no gain | - | - | n/a |
| 6 integer support | rows carry `p_ge_full` and 19-q grid (test_late_report.py:113); elig scores RPS from p_ge_full like every arm | - | - | PASS (same support as comparators) |
| branch tests | `.venv-1/bin/python -m pytest -q -p no:cacheprovider tests/daily/test_late_report.py tests/jarvis/test_late_report.py` | 14 | - | 14 passed, exit 0 |
| neighbour suites | `.venv-1/bin/python -m pytest -q -p no:cacheprovider --no-cov tests/jarvis tests/lineups tests/daily` | 628 | - | 628 passed, exit 0 (345 s) |
| ruff | `.venv-1/bin/ruff check <9 changed files>` | 9 files | - | exit 0 |
| format | `.venv-1/bin/ruff format --check <9 files>` | 9 files | - | exit 0 |
| mypy | `.venv-1/bin/mypy <9 files>` | 9 files | - | exit 1: 1 new error (m5), 5 pre-existing (same 5 on main) |
| JARVIS read cost | `$SCRATCH/time_read_late.py` on a private copy of nba.duckdb (deleted after) | 7 games, 944 keys | - | 0.464 s cold, ~0.04 s warm |
| oracle | not run (main session; holdout row first) | - | - | expectation IDENTICAL |

$SCRATCH = /private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/0a211085-ea8b-44ad-99a3-242e82119de2/scratchpad

## Playbook check

Model class: gradient-boosted trees (LightGBM context residual), served unchanged with a different pre-tip
information set. No new fit (production and T-15 caches are seeded; a miss refits the same class into the shadow's own dir).
1. Information or capacity: information (a later official report, T-15 instead of T-60). This is the kind of arm our record favours.
2. Enough data: same training as production (≈116k rows through 11-07); not a data-hungry class.
3. Test validity: no test yet. The future test must pair on a common population (n2), handle the doubtful
   train/serve difference (n1), use real-tip gating (it does) and integer support (it does), and count coverage from the schedule (n4).
   Power: unknown until the late-report change rate is known. Most games have no status change between T-60 and T-15, so
   the per-row effect is diluted, and the prereg should state the MDE on games where the late report differs.
4. Lineage: the T-15 lineups-known arm plus production's report rule with lead 0 and the doubtful status. Inherited
   guards: store LeakageError, write-on-change dedup, per-game decision table, oracle exclusion test. All carried forward.
5. Scored against: production `props_context_residual` (and on lineup games the T-15 arm) on RPS. It is not scored at a
   market line, and a CRPS gain here says nothing yet about the line (T477).

## Biggest risk

M1: a wrong fact on Devin's betting screen (DOUBTFUL shown as OUT). It gets more likely once the out-gap branch
merges. No leak or production-number risk found.

## Not verified

- Live cache-hit behaviour (fingerprint equality of the seeded production cache under real data). The lineup
  test shows the late arm reproduces the T-15 numbers on the fixture, but I did not run the shadow against nba.duckdb.
- Real run time of the shadow on a full 14-game night (estimated from fit_seconds and T-15 log timings, not measured).
- The out-gap branch is unfinished. Its interaction with M1 is inferred from its uncommitted tests.
- Replay oracle (by instruction).

## Recommended changes (for the main session; not applied)

1. Fix M1 (one-line text change plus 2 test lines), then merge after the oracle.
2. m1 (try around `_late_lines`) in the same commit: cheap insurance on a JARVIS path.
3. m2-m6 can wait; record them as loose ends.
4. Merge order: either order works, but if out-gap merges first, M1 must be in.

## Drafted rows (not appended)

DECISIONS (draft): `| 2026-11-08 hh:mm | Adversary pre-merge read of agent/modeler-late-report 870d8dc: WOUNDED (no leak, production rows and FORWARD_PREREG scoring unchanged, oracle expected IDENTICAL); merge after M1 (display "OUT" for DOUBTFUL players) is fixed and the oracle is IDENTICAL. Review docs/reviews/late_report_premerge_2026-11-08.md. | Follows advice (DECISIONS 2026-11-02 19:44 sequence: build -> adversary -> oracle -> Devin go). | Oracle DIFFERENT, or a T-15 tick exceeding its lock budget after install. |`
