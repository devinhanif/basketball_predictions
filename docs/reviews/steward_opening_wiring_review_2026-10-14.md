# Code review: agent/steward-opening-wiring (da08b57) before merge, 2026-10-14

Reviewer: adversary. Scope: `git diff main...origin/agent/steward-opening-wiring` (base 4398e08, 24 files, +3,827/-8). This is a code review before merge, not an attack on a result: no result exists yet. I read the code only, ran the unit tests in `tests/daily`, `tests/ops` and `tests/test_layering.py`, and ran four probe scripts in my scratchpad. I ran no replay and opened no season-2025 data. The only database read was `data/backups/nba_20261014.duckdb` (`read_only=True`, `season <= 2024`, a season count).

**Classification: WOUNDED.** The live path is safe to merge: it writes no production row, and nothing in it runs on a regular-season slate before 2026-10-20. But two parts are only conditionally ready. (1) The combo step's wall time is unmeasured and has no bound, and it runs while the pretip job holds the database. (2) The reader has three substantive departures from the frozen rule. None of them blocks the merge. One must be closed before opening night, and the reader fixes before the interim look on 11-15.

Verdict: APPROVED

Approved for merge with the conditions below, and only if the main session's oracle replay (89889f6d) and proof P3 come back clean. No finding is a merge blocker. Each finding carries two labels: a severity (blocker, major or minor) and a kind, **SUBSTANTIVE** (it changes behaviour or a verdict) or **COSMETIC**.

## Advice or momentum
This review follows the advice in the CLAUDE.md rule "build, then adversary, then review, then decide": it is the review before merge of a branch that touches the live path.

## Frozen inputs (rule integrity)
I recomputed every frozen hash on 4398e08, on 05e1b50 and on the branch head, with `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/COMBO_STOCKS_SHADOW.md | shasum -a 256` and `shasum -a 256` on each input. Exit 0 throughout.

| object | expected | branch da08b57 | pass |
|---|---|---|---|
| rule text above the results line | b9b38cbe | b9b38cbe848f... | yes |
| `configs/combo_stocks_shadow.yaml` | 8932ccec | 8932ccec5877... | yes |
| `nba/props/combo_g1.py` | 792635d0 | 792635d02311... | yes |
| `p1_reference.npz` | 1356c78a | 1356c78a29d3... | yes |
| `select_2023/select.json`, `R_chosen.npz` | 84619e3e, e535258c | 84619e3e..., e535258c... | yes |

The diff touches no file under `docs/prereg/` or `configs/`.

## 1. Can the combo or AVG shadow perturb production?

**Writes.** The branch never writes `forward_predictions` or `forward_scores`. `grep` for INSERT, UPDATE or DELETE on `forward_` tables in `nba/daily/combo_shadow/*.py`, `nba/eval/combo_shadow_live.py` and `nba/ops/app_export.py` finds nothing (exit 1). The writer inserts only into `forward_combo_shadow` (`store.py:116-118`), `forward_combo_heartbeat` (`store.py:140-154`) and `forward_combo_meta` (`store.py:169`). Settle inserts only into `forward_combo_scores` (`settle.py:101-103`). The canonical dump reads only the two production tables (`canonical_dump.py:62-66`).

**Order.** Production rows are appended at `pipeline.py:232-235`, stamped at `:240` and settled at `:242`. Every shadow runs after that, and the combo step runs last (`pipeline.py:263-269`), so it cannot delay or change a primary row of the same run. No step comes after it.

**Shared state.**
- `preds` is passed in but never used (`pipeline.py:459`; COSMETIC: remove the parameter).
- `wide` is a polars frame, so `.filter` and `.select` return new frames (`run.py:132`, `:452`).
- `report_out` is copied into a new dict of new sets (`run.py:123`).
- `shared` is only read, or filled once by `_wide_roster_frame` (`pipeline.py:320-338`).
- The model cache goes to its own directory, `<cache>/<date>_combo` (`run.py:458`). Production's directories are `<date>` and `<date>_lt` (`forward.py:770`, `:883`), and the stamps read only `<date>` (`stamps.py:230`).

**Exceptions.** `run_combo_shadow` catches `Exception` around the whole step (`run.py:361-399`) and around the heartbeat table write (`:402-411`). The final heartbeat-file write (`run.py:418`) is outside that try, but `pipeline._combo_shadow` catches it (`pipeline.py:483-502`).
- What still escapes: a native crash or an out-of-memory kill of the in-process LightGBM fit kills the predict process. The primary rows are already committed at that point, but `_print_summary` and `alert_shadow_errors` (`__main__.py:299-300`) are lost. The step's non-zero rc still alerts (`ops/nba_daily.sh:83-86`).
- The full and pplay shadows already carry this risk (minor). The AVG shadow runs its inference in a child process with a 240 s timeout (`avg_shadow/run.py:38`, `:258-260`). The combo step does not.

**Locks: finding S1 (major, SUBSTANTIVE; condition before 2026-10-20).** Nothing bounds the combo step's wall time.
- **Where the time goes.** It runs inside the predict process, which holds the DuckDB write connection, and inside the pretip `mkdir` lock (`ops/nba_daily.sh:92-111`, `:136-138`). Every run rebuilds production's full feature frame with no cache (`run.py:448-456`: `_history_frames` + `build_features` + `add_stock_features` + a full-history blk/stl query at `:135-138`). The first run of each day, and any run after new box rows arrive, also refits both NB heads on all played history (`run.py:192-208`).
- **The measured budget** (`docs/DAILY_PIPELINE.md:307`, `:319`): a cold pretip takes 339 s, and `run-t30` waits at most 600 s for the database. This branch also turns the AVG shadow on, with a bundle installed (up to a 240 s child timeout). That makes about 579 s before the combo step adds anything.
- **P4 (cost) is not measured on real data**; the branch's own DAILY_PIPELINE text says so.
- **If it runs long:** the T-30 tick defers with rc 75, and those lineup-arm rows are lost (lineups at T-30 are the project's stated lever). The app export retries. Past 30 minutes, the next `:20` or `:50` pretip slot finds the lock and skips (`ops/nba_daily.sh:104-107`). Production then loses that slot, and its newest on-time row is 30 minutes older.
- **When it bites:** live on 2026-10-20, when the first in-season cold run happens. Matinee slates (noon tips) put the cold 09:20 run next to a T-30 tick.
- **Fix (minimal):**
  - (a) The main session reads `model_status.combo_shadow` from the P3 replay state (`seconds`, `feature_seconds`, `cache_hit`) for a cold date and a warm date. P3 already records them (`run.py:401`, `:478-480`).
  - (b) Before 10-20, add a time budget in `pipeline._combo_shadow`. Pass the run's start time from `run_daily` and record `skipped` with detail "time budget" when the elapsed time exceeds a set limit (suggest 420 s), or when any upcoming tip is within 35 minutes (the T-30 tick needs the database). A budget skip is a missing row that G-COV counts, which is honest.

## 2. Does the writer match the frozen rule?

**Matches the rule:**
- **Config and base normals.** The writer refuses any config or base normals other than the frozen ones (`config.py:24-25`, `:64-76`), on the live path through `load_config()` (`run.py:375`).
- **Refused after tip.** Rows made at or after tip are refused twice: at the filter (`run.py:373`, `made_at < g.tipoff`) and in the store (`store.py:88`).
- **Regular season only.** Only `002` games are written (`run.py:373`); preseason never reaches the step (`schedule.py:71-79`).
- **n_prior >= 5** comes from `stat_frame` (`context_residual.py:753`).
- **As-of reads.** History reads use `game_date < run_date` (`run.py:135-138`, `:268-272`; `forward.py:577-590`). Production marginals use `made_at <=` the run's own stamp (`run.py:234-242`), so an on-time shadow row implies on-time production inputs (rule 4).
- **Grids.** Grids are rebuilt from `p_ge_full` with n = 199 only (`run.py:243-251`). The joint call uses the frozen R plus an R = I call over the same base normals (`run.py:331-336`). RECENT covers played games only (`run.py:271`).
- **Settle.** For each (game, player, target) it takes the latest row with `made_at <= tipoff_utc - 60 min` (`settle.py:30-34`), on final games only (`:43`). It is idempotent (`:44-45`). Unplayed rows get `played = false` and no CRPS. Missing players get `not_in_box`. Every arm uses the same exact integer CRPS (`settle.py:49-59`), the same function the reader imports (`combo_shadow_live.py:30`).
- **Counting start.** It requires P1-P4, which is stricter than the rule's P1-P3 (`store.py:172-178`). That is acceptable.
- **Optional arms.** DIRECT and RECENT_CAL are not built (NULL), which the rule allows ("droppable").

**Deviations and gaps (none touches a scored number today):**
- **M5 (minor, SUBSTANTIVE).** `tip_source` is hard-coded `"real"` (`run.py:503`), so the reader gate `tip_source_not_real` (`combo_shadow_live.py:398`) is vacuous. Live, the schedule comes from the real feed. Under `--schedule-from-db` (replays), tips are a 19:00 ET proxy (`schedule.py:86-112`) and are still labelled real. The same pattern is inherited from `avg_shadow/run.py:308`. Fix: pass the tip source from the caller, and use `"proxy"` when `schedule_from_db` is in use.
- **M6 (minor, SUBSTANTIVE).** The write-on-change signature leaves out `tipoff_utc` (`store.py:64`), and settle judges a row by its own stored tip (`settle.py:33`). Suppose a game is moved to an earlier tip and the grids do not change. The old row keeps the old tip and can count as on time against it. This is inherited from the full and avg stores. Fix: add `tipoff_utc` to `SIG_COLUMNS`.
- **M7 (minor, SUBSTANTIVE).** A permanent skip is silent. "No production rows" returns `skipped` with count `keep` (`run.py:396`, `:439-445`), so a broken production join (model name, `n != 199`) would never raise the watchdog's `combo_shadow:failed`. Only the briefing's 14-day heartbeat counts would show it (`display.py:30-32`). Fix: on a slate with a regular-season game, count `skipped: no production rows` as bad (`count="inc"`).
- **M8 (minor, SUBSTANTIVE; P4).** Storage is unmeasured. My estimate is about 1.8 KB of JSON per row (scratch computation on Poisson-shaped grids: blk 1,237 B to pra 2,252 B). At 7 rows per servable player that is about 2.3 MB raw per write wave for a 180-player slate, or roughly 0.4-2 GB raw a season depending on how often production changes. The database is 648 MB today, and its Drive upload this morning took 98 minutes and failed. P4 must report the `mb_written_est` field (`run.py:511-514`) on real slates.
- COSMETIC: the briefing's "settled" count includes rows with `played = false` (`display.py:47`).
- COSMETIC: `HB_DIR` is a relative path (`run.py:57`). It works only because `ops/nba_daily.sh:17` changes into the repo root first (inherited).

## 3. Reader `nba/eval/combo_shadow_live.py` against the rule text

**Matches the rule:**
- `n_min = 3,000`: below it the target is INCONCLUSIVE (`:507-515`).
- PASS needs every condition (a) through (h) (`:493-506`, `:516-517`).
- FAIL means not PASS and the larger half-width <= floor_t (`:518-519`).
- BH runs over the seven game-clustered one-sided p values (`:542-547`).
- The bootstrap is the shared one, imported from `full_shadow_live` with 2,000 draws and seed 0 (`full_shadow_live.py:44-45`, `:83-125`).
- G1 and the slice guard come from `combo_g1`, sorted by date, game and player (`combo_g1.py:41-81`).
- The slices follow 6(e) (`:419-427`).
- Interim on 2026-11-15 and final on the first Sunday from 2027-01-10 with >= 3,000 rows (`:581-609`).
- The later half is the second half of the rows sorted by (date, game, player) (`:458-466`). The docstring states this choice was made before any live row (`:11-14`). That is acceptable, but a choice the frozen text leaves open belongs in DECISIONS (drafted below).
- The second-look bound `delta + 1.96 se` (`:430-435`) is a declared reading of "z = 1.96 in the half-width and in (b)".

**Findings:**
- **S2 (major, SUBSTANTIVE; fix before the 11-15 interim).** Rows written before the counting start are scored. `_SQL` (`:84-114`) and `load_live` never read `forward_combo_meta`; `grep -n counting nba/eval/combo_shadow_live.py` finds nothing. The rule says "Counting starts only after proofs P1-P3 pass". The writer is ON from the merge. If P3 or P4 has not passed by 10-20 and the counting start is set later (say 10-24), the rows from 10-20 to 10-23 would enter the look. If the counting start is never set, the reader still returns verdicts. Fix:
  - In `load_live`, add `AND g.game_date >= ?` bound to `store.get_meta(con)["counting_start"]`.
  - In `read_look`, raise `LookNotDue` when the counting start is unset.
  - Add a test with a warm-up row before the counting start.
  - `avg_shadow_live` and `full_shadow_live` have the same gap (inherited): flag it to their owner.
- **S3 (major, SUBSTANTIVE; fix before 11-15).** The interim integrity kill is too eager. `decide_target` kills on `not gates_ok` (`:489`), and `score_live` folds coverage `ok` into `gates_ok` (`:555-566`). That `ok` means overall >= 0.95 and starter or bench >= 0.90 (`:339-343`), and staleness also enters (`:414`). So a target at 94% coverage is KILLED at the interim even when S beats RECENT.
  - Probe C confirms this: delta -0.02, upper bound -0.01, coverage gate missed, verdict `KILL`.
  - The rule reads: "any failure blocks PASS; a persistent one is an integrity kill"; G-COV "kill below 80%".
  - Fix: at the interim, KILL iff the contrast kill, or coverage `kill` (overall < 0.80), or a hard stamp gate (`rows_after_tip > 0`, `id_mismatch > 0`, `repro_q19_fail > 0`). Coverage between 80% and 95% is "flagged, continue".
  - `tests/daily/test_combo_shadow_reader.py:189-191` enshrines the current behaviour and must change with it.
- **M1 (minor, SUBSTANTIVE).** At the second look, condition (g) uses z = 1.96 (`:504-506`, `_upper(si, z)`). The rule sets 1.96 only "in the half-width and in (b)", so (g) should keep the bootstrap one-sided 95% bound. Fix: `_upper(si, Z1)`.
- **M2 (minor, SUBSTANTIVE).** After an end-of-season final, the reader still schedules a second look; probe A returns `second` for the Sunday after the last regular-season Sunday. The rule says "no second look". Fix: in `look_due` (`:607`), require that the final was read before `last_regular_sunday` (for example a history tag `final_end_of_season` that suppresses `second`).
- **M3 (minor).** If every target first reaches 1,500 rows on or after 2027-01-10, the interim branch takes precedence over the final on that Sunday (probe B returns `interim`). This is unlikely. Fix: evaluate the final branch first when `today >= FINAL_DATE`.
- **M4 (minor, SUBSTANTIVE but descriptive).** Several rule items are missing or narrowed:
  - The missingness table leaves out the rule's "DNP-on-report" split and is computed for PRA only (`:352-365`).
  - G-COV's "90% in any slice" is implemented for starter and bench only (`:339-343`).
  - "model_id changes only on a refit date" is not checked (`:370-415`).
  - The 19-atom CRPS that rule 5 asks to print beside the main metric is not printed.

  Fix: add these items. They are descriptive or gates, and no verdict number changes.

## 4. App export (`nba/ops/app_export.py`, `ops/launchd/local.nba.app-export.plist`)

- **Read-only and holdout.** The job never opens DuckDB. The build it runs opens `nba.duckdb` only through `reader.open_read_only` (`export/reader.py:52-56`) and refuses season 2025 before reading (`export/guard.py:41-54`; `export/__main__.py:27`). The job passes no `--season`, so the default 2026 applies (`app_export.py:116-127`). It writes only `data/export/` and its own heartbeat and log. Pass.
- **Locked database.** The build exits 2 on a lock after a 30 s wait (`export/__main__.py:44-46`). The job retries 4 times, a minute apart (`app_export.py:205-218`), keeps the previous export, and records `failed` with rc 2 (`:230-232`). The swap is atomic up to two renames, with rollback (`:93-104`). Pass. Residual risk (minor): while the export holds its read-only connection, a pretip writer waits at most 300 s (`ops/nba_daily.sh:118`). The export build time was not measured; the `:35` slots sit 15 minutes before `:50`.
- **Gate: finding A1 (major, SUBSTANTIVE; Devin's call).**
  - The gate requires the morning heartbeat's `rc == 0` (`app_export.py:77-78`). Today that heartbeat is rc 1 (`data/ops/heartbeat/daily-morning.json`), and the morning log shows why: `end backup_db_drive rc=1` and `end backup_lineups_drive rc=1`. Every data step was rc 0: settle, checkpoint, report, facts, parlay_settle, data_snapshot, data_diff, data_check.
  - Under this gate, both slots skip, the watchdog raises `app-export:skipped` after two skips (`watchdog.py:101-108`), and the app stays a day stale on every day the Drive upload fails.
  - **Yes, the gate should key on the data steps.** Minimal change, with no edit to the live script: in `morning_gate`, when `rc != 0`, read `data/ops/morning_<YYYYMMDD>.log` and accept it when (i) `end settle rc=0` and `end report rc=0` are present, and (ii) every `end <step> rc=<n>` line with n != 0 belongs to a step named `backup_*_drive`. Record the reason "morning rc=1 from Drive backup only; data steps ok".
  - The log format is fixed by `step()` (`ops/nba_daily.sh:73-76`).
  - A heavier alternative is a data-steps marker written by `ops/nba_daily.sh` after `data_check`, but that touches the live path and needs the oracle and Devin's go.
  - Whether a failed `data_check` (the leak check) should block publishing is a separate question: I would keep it blocking.
- **Plist.** Correct:
  - The label matches `JOBS["app-export"]`.
  - `WorkingDirectory` is the main checkout.
  - It launches through `uv`, not `/bin/sh` (the TCC rule).
  - stdout and stderr go to `data/ops/launchd_app-export.{out,err}`.
  - The slots are 12:35 and 14:35 in launchd's captured zone. That zone is ET per `data/ops/schedule_tz` (`America/New_York`), the same zone the watchdog judges in (`watchdog.py:39-45`, `:156-169`).

  Minor: the plist is not in `ops/install_launchd.sh`'s uninstall list (it is installed by hand, so this is acceptable, but note it).
- **A2 (minor, SUBSTANTIVE: a false alert).** Once the label is loaded, the watchdog alerts `app-export:stale` until the first slot has run, at any time except the 30 minutes after a slot. The cause: `reference = first_seen` is the watchdog's first-ever check (`watchdog.py:259`, `:292`; `check` at `:439`). Probe D at 09:00 and 16:00 ET returned `['app-export:stale']`. Fix: add `launchctl kickstart gui/$(id -u)/local.nba.app-export` to the install line (plist comment line 5 and the DAILY_PIPELINE install text), so a heartbeat exists at once.

## 5. Watchdog

- **Before 10-20: no false alerts.** The combo and AVG heartbeat files are written only when there is an upcoming scorable slate (`pipeline.py:484`, and `schedule.py:71-79` drops 001). Before that the file is absent and the job is silent (`watchdog.py:338`, `if not hb: continue`). `app-export` is `optional`: silent until a heartbeat exists or the label is loaded (`watchdog.py:259`). After install, see A2.
  - Probe caveat: a manual run of `python -m nba.ops.app_export` before install writes a heartbeat, after which the job is judged like an installed job.
- **Changes to existing alert behaviour:**
  - Only the `:skipped` message text changes; it now carries the recorded reason (`watchdog.py:280-290`). Keys are unchanged, so dedupe raises no extra alert.
  - `SHADOW_JOBS` gains `combo_shadow` with threshold 3 (`:147`). The `hung` alert fires at 30 minutes (`:324`).
  - New as a live source: the flag turns AVG on, so the AVG monthly-install alert (past the 3rd) now applies from November. Expect a 2026-11-04 alert if the November bundle is not installed.
  - `drill` writes only in a temporary directory (`watchdog.py:494-555`). The test checks byte equality of the source.

## 6. Tests

Command, unpiped, from the worktree on da08b57:
`/Users/devin/Downloads/nba-prediction/.venv/bin/python -m pytest tests/daily tests/ops tests/test_layering.py -q -p no:cacheprovider`
Result: **385 passed, 13 warnings in 350 s, exit 1.** The exit 1 is only the repo-wide `fail-under 70` coverage floor, which a partial selection cannot reach ("Total coverage: 14.01%"). No test failed.
Ruff check and ruff format: exit 0 (19 files). Mypy on the changed modules: exit 0 (11 files).

- **T1 (minor, SUBSTANTIVE).** `test_shadow_failure_is_recorded_and_never_raises` (`tests/daily/test_combo_shadow.py:521-527`) still passes when the combo step is deleted. It asserts only `s.n_rows_written > 0`. My mutation probe replaced `pipeline._combo_shadow` with a no-op and the test passed (`scratchpad/attack/test_vacuity.py`, 2 passed, exit 0). It does catch an exception that escapes, which is the probe's second case. Fix:
  - Assert that `"combo_shadow" in s.model_status` or that a `combo shadow:` entry is present.
  - Add a test that monkeypatches `run_combo_shadow` to raise and asserts the `shadow_errors` text and unchanged `canonical_frames`.
- COSMETIC: `assert T3 != T4` (`test_combo_shadow.py:544`) tests nothing.
- Missing negative tests:
  - The reader with a row before the counting start (S2).
  - Interim coverage at 85%, which should continue (S3).
  - No second look after an end-of-season final (M2).
  - A manifest `made_at` with the real `Z` suffix (`export/build.py:350`; the fake build writes it without; Python 3.12 parses both, so the gap is untested rather than broken).
- **Non-vacuous and good:**
  - Planted future with a positive control (a past edit moves the grid).
  - Dedup.
  - Tip refusal at both layers.
  - Canonical-frame equality with the shadow ON and rows > 0.
  - P1 bit for bit in two chunkings.
  - The watchdog's failed and hung thresholds.
  - Drill source byte equality.
  - Plist and watchdog kept in step.

## Per-check table (the six checks; this is a code review, so most are design checks)

| check | command / evidence | n | CI | result |
|---|---|---|---|---|
| 1 shuffled labels | n/a (no result) | - | - | n/a |
| 2 planted future | `test_planted_future_changes_nothing_and_a_past_edit_does` (in the 385); as-of SQL read line by line (`run.py:135-138`, `:234-242`, `:268-272`) | fixture | - | pass |
| 3 missingness | reader design: G-COV and the missingness table (`:310-366`); DNP-on-report split missing, PRA only (M4) | - | - | partial |
| 4 knockout | n/a (no result) | - | - | n/a |
| 5 replication | n/a | - | - | n/a |
| 6 integer support | same `crps_rows` for S, RECENT, INDEP in settle and reader (`settle.py:56-59`, reader `:30`, `:449`); every arm is a 199-point integer grid | - | - | pass |
| rule integrity | hashes above | - | - | pass |
| holdout hygiene | no 2025 read here; export refuses 2025 | - | - | pass |

**Corrected family and power note:** no family was run here, so there is no BH to correct. The rule's own family is m = 7, and the reader implements it as written. Power is unchanged from the rule (n_min 3,000 per target; at 3,000 rows the minimum detectable effect is about 0.0049 for blk and 0.081 for PRA, bound by about 80 date clusters).

## Playbook check (docs/MODEL_PLAYBOOK.md)

Model classes: a gradient-boosted tree count head (LightGBM Poisson mean with negative-binomial dispersion), a Gaussian copula joint model, and empirical recency baselines. Sections: "Gradient-boosted trees", "Count models", "Set and joint models", "Quantile and distribution layers".

1. **Information or capacity?** The wiring adds neither. It must not leak, and it does not: every read is pre-slate or pre-stamp (cited above).
2. **Enough data for the class?** The rule's gate (n_min 3,000 per target; about 9,700 rows expected at week 12) was checked before the freeze. Not re-run here.
3. **Test validity.** Real tips live, but the label is not verified (M5). There is a stamp rule at T-60, and every arm is on the same integer support. Two reader defects threaten validity: rows before the counting start (S2), and an interim kill wider than the rule (S3). An invalid look would not be a null, so fix both before 11-15.
4. **Lineage and inherited guards.** The code inherits the sibling shadows' structure and also their gaps: `tipoff_utc` missing from the dedup signature, the counting start unused by the reader, `tip_source` hard-coded. Fix them here and flag them in the siblings.
5. **What is it scored against?** RECENT, plus INDEP for the four combos, on the exact integer CRPS of the stored grid. Nothing is scored against a market price.

## The one biggest risk

The combo step has no bound, and every run rebuilds production's feature frame from scratch, inside the pretip database hold. On opening night, with AVG now live too, the cold run can push a T-30 lineups tick past its 600 s wait, or a pretip run into the next slot. Nothing alerts until rows are already missing. S1's condition (a P3 timing reading plus a time budget) closes this.

## What was not verified
- The combo step's wall time on the real database, cold and warm (P4). P3 records it in `model_status.combo_shadow.seconds`.
- Storage per slate on real rows (P4); mine is an estimate.
- The oracle hash and P3 (the main session is running them). AVG and combo have never run together in one replay: P3 runs with AVG off, and AVG's own P4 ran with combo absent.
- Whether `nba.datamanifest diff` and `check` flag the eight new tables (`forward_combo_*`, `forward_avg_*`) that the next pretip run creates (`manifest.py:313-317` picks them up; earlier shadow tables were added the same way).
- The export build's duration, LightGBM memory under the current swap pressure, and the real tip source of the live schedule feed.

## Recommended changes (not applied by me)

| id | severity | when | file:line | change |
|---|---|---|---|---|
| S1 | major, SUBSTANTIVE | before 2026-10-20 | `pipeline.py:453-502` | time budget plus the P3 timing reading |
| S2 | major, SUBSTANTIVE | before 2026-11-15 | `combo_shadow_live.py:84-114`, `:637-659` | filter on the counting start; refuse a look when it is unset |
| S3 | major, SUBSTANTIVE | before 2026-11-15 | `combo_shadow_live.py:483-491`, `:555-566` | interim KILL only on the contrast, coverage < 0.80, or a hard stamp gate |
| A1 | major, SUBSTANTIVE (Devin) | before the plist is installed | `app_export.py:61-79` | gate on the data steps; allow a Drive-only rc 1 |
| A2 | minor | at install | plist line 5, DAILY_PIPELINE | `launchctl kickstart` after bootstrap |
| M1-M8, T1 | minor | before 11-15 | as cited | as described |
| COSMETIC | - | any | `pipeline.py:459`, `test_combo_shadow.py:544`, `display.py:47` | remove the unused `preds`; delete the filler assert; count only played rows as settled |

## Drafted rows (not appended)

DECISIONS (for Devin; the main session appends if he agrees):
`2026-10-14 | App export gate keys on the morning data steps, not the morning rc | Today's rc 1 came only from backup_db_drive / backup_lineups_drive (Drive DNS); every data step was rc 0; the rc-only gate would skip the export and alert on every Drive flake | Reverse if a failed upload ever hid a data problem, or if the export must never publish without an off-machine backup`

DECISIONS (record of a steward choice the frozen text leaves open):
`2026-10-14 | COMBO_STOCKS_SHADOW reader: "later half of the rows by date" = the second half of the eligible rows in the fixed order (game_date, game_id, player_id); second-look z = 1.96 applied to (b) and the half-width only | chosen before any live row (combo_shadow_live.py docstring) | a new rule id if changed after a live row exists`

PREDICTIONS_LEDGER (adversary prior, before P3 lands): `P(P3 canonical hash = 89889f6d with --combo-shadow) = 0.95; P(at least one replayed date with combo heartbeat ok and rows_written > 0) = 0.85` (the 0.15 is mostly the replay's production rows failing the `n = 199` check or the regular-season filter).

Scratch files, outside the repo: `scratchpad/attack/probe_logic.py`, `scratchpad/attack/test_vacuity.py`, `scratchpad/pytest.txt`, `scratchpad/wiring.diff`.
