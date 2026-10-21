# Adversary review: export `line_cal` field (agent/steward-export-main-line-cal, head 2d5da83)

Date: 2026-10-21. Base: main 5ce5f15. What was asked: an additive, display-only per-prop export field carrying the frozen
LINE_CALIBRATION P(over) (T493, map sha8 443743a1, fit through 2025-06-30) at the DraftKings main line, meant to equal the parlay
panel's `p_cal` bit for bit.

Verdict: APPROVED WITH EDITS

Top finding: the function is the producer's, but the inputs are not always the same. The export keys the main line to the
*displayed* row's run and `made_at`, while `p_over` always comes from the newest *production* row. On the real 2026-10-20 slate,
95 of 143 `line_cal` values (66%) sit on `props_context_residual_t15` rows. On those rows the DraftKings line was received *after* the
production row's `made_at` (15:44 / 17:50 production runs, lines up to 18:47 / 22:46). The producer never builds that leg: it requires
`q[7] <= prod["made_at"]` (`nba/parlay/top_source.py:468`, `:479`). The contract's claim "the card and the parlay panel cannot disagree on
a leg" (`docs/contracts/line_cal_export_v1.md:15`) is therefore false for those rows. Nothing leaks: every line is still pre-tip and
display only. The rows the app actually shows are mostly fine: on primary rows, 12 of 424 carry a value, all of them keyed to the
production run, with the line received before production's `made_at`.

## Model class and playbook check

Class: quantile/distribution layer, specifically recalibration at the line (MODEL_PLAYBOOK "Quantile and distribution layers"). The
class's typical failure is a shape error at the line, which is why the frozen map exists.
- Information or capacity: neither. The branch fits nothing and serves an existing frozen map. Its only risk is serving it on inputs
  that differ from the scored or served definition.
- Enough data for this class: n/a (no fit). The map's cells were fitted on 2023-2024 under T493.
- Test validity: the equality test is valid but narrow. It covers one fixture row where the line, the run and the production row all
  line up, so it cannot see the timing divergence below.
- Lineage and inherited guards: the field copies `rung_cal_fields` (main line, production row, grid reproduction check), so it inherits
  rung_cal's displayed-row keying. That keying suits rung_cal, which claims no panel parity; it does not suit a field whose purpose is
  parity.
- Scored against: nothing (display). The scored object is T493's harness (199-point grid). See F3 for how the served `z` differs.
- Class check (reliability at the line): out of scope for a display field. The live scorecard owns it.

## Findings (severity-ranked)

**F1 (major): main line keyed to the displayed row, not to the production row whose P(over) is used.**
`nba/export/line_cal_fields.py:71-72`: `key = (p.get("lines_run_id") or p["run_id"], ...)` and `rcf.main_line(..., p["made_at"], ...)`.
For a non-production row (t15, int, lt, recency, an AVG row from another run), the line can be newer than the production forecast that
supplies `p_over`, so the value is not one the panel can show. Real data, slate 2026-10-20: 143 values; 95 on t15 rows with
`price_at > production made_at`; 48 with the line before production's `made_at`.
Fix: before the loop, compute one value per `(game, player, stat)` from the production row itself, i.e. with
`key = (prod_row["run_id"], game, player, stat)` and `rcf.main_line(by_run[key], prod_row["made_at"], prod_row["tipoff"])`. Then write that
same `line_cal` (or the same reason) onto every row of the prop. Add a test: a t15-style row whose run has a newer DraftKings line than the
production run must carry the production-keyed value, not a value built from its own line. Count `n_line_cal` per row as now, and also
report per distinct prop in the manifest (`n_props_distinct`, `n_line_cal_distinct`) so the 143-vs-96 inflation is visible.

**F2 (minor): the contract overstates parity.** `docs/contracts/line_cal_export_v1.md:15` and `:22`. Even after F1, the export and the
producer pick "the" main line differently, so they can still disagree:
- (a) The producer takes every line in the newest board snapshot per (book, market, game) at or before production's `made_at`. If the
  newest board drops the player, there is no leg. The export takes the newest two-sided pair per player strictly before `made_at`, so a
  player the newest board dropped still gets an older line.
- (b) The comparison is `<=` in the producer and `<` in the export.
- (c) The export requires a persisted 199-point grid that reproduces `p_ge_full`. The producer requires no grid, so a recency-routed row
  is a panel leg but a `no_production_grid` NULL here.
- (d) The export has no `now`, while the producer reads only rows and quotes stamped at or before its run clock. A panel built at 17:00
  and an export built at 23:00 can use different production rows.

Fix: reword line 15 to "for the same production row and the same DraftKings line, the card equals the panel's `p_cal` bit for bit", and
list (a)-(d) as known non-parity cases. No code change is needed for (a)-(d). (c) is a deliberate integrity check; keep it.

**F3 (minor; a finding about the producer, not this branch, and Devin should hear it): the served `z` does not use the frozen rule's
median and spread source.** `docs/prereg/LINE_CALIBRATION.md:26-27` says `z` is "read from production's continuous 199-point grid",
interpolated at positions 49.25 / 99 / 148.75 (`:166`). The producer (`nba/parlay/top_source.py:187-197`, `quartiles`) reads tau
0.25 / 0.5 / 0.75 off the stored 19-point `q_grid` instead. The T493 holdout score came from the harness's 199-point recovery, so the
served object differs from the scored one in letter. Measured on real season-2026 rows (slate 2026-10-20; n = 96 distinct
(prop, main line) pairs carrying `line_cal`):

| quantity | max | mean |
|---|---|---|
| \|q19 - q199\| at the quartiles | 0.00366 | |
| \|dz\| | 0.00067 | |
| \|dp_cal\| | 7.6e-5 | 1.6e-5 |

The largest |dp_cal| is under a tenth of the app's 0.1-pp display resolution. This is a deterministic difference, not a sample
estimate, so no CI applies. The steward's choice (match the panel, q_grid) is the right call for a field whose purpose is parity, and
it is numerically immaterial. The fix belongs to the producer, not this branch: either switch `top_source.quartiles` to
`lc.sorted_grid_quantiles` on the persisted q199, or record a DECISIONS row accepting the 19-point source as equivalent (the max
|dp_cal| of 7.6e-5 is the evidence). If the producer changes, this field follows automatically, because it calls the same function.

**F4 (minor): the contract does not say older exports lack the columns.** `docs/contracts/line_cal_export_v1.md:3-4` says "OPTIONAL,
additive" and "schema_version stays 2", but not that exports built before this branch have no `line_cal` / `line_cal_null_reason`
columns and that a reader must treat an absent column as NULL. No app code reads `line_cal` yet; `export_store.py` reads `rung_cal`
via `r.get(...)`, which tolerates absence. Fix: add that sentence after line 4.

**F5 (note): on 2026-10-20 the producer built zero legs** (`dropped: team_not_in_game 100`; 324 chosen props had no quote at or before
the 15:44 / 17:50 production `made_at`, because capture started at 17:31). Bit parity could therefore not be shown on real data, only on
the fixture. Out of scope for this branch. The `team_not_in_game` count deserves its own look by the producer's owner.

Scope (check 1): clean. Exactly the five named files (`git diff --stat main...agent/steward-export-main-line-cal`: 5 files, +325/-1):
no `nba/parlay`, no scoring code, no `data/`. `build.py` adds only the two columns, one call and one manifest key.

## Check table

All commands ran in the steward worktree `wt_export` at 2d5da83 (PYTHONDONTWRITEBYTECODE=1, no cache), unpiped, exit codes shown.
Attack scripts are in the scratchpad (`attack/lc_parity.py`, `lc_rows.py`, `lc_z.py`, `lc_diag.py`). They read `nba.duckdb` and
`data/markets/market_asof.duckdb` with `read_only=True` for season 2026 only (Window seasons=(2026,), slate 2026-10-20). No 2025 row
or outcome was read.

| # | Check | Command | Result | Pass |
|---|---|---|---|---|
| 1 | Scope | `git diff --stat main...agent/steward-export-main-line-cal` | 5 files, exactly the named set | PASS |
| 2 | Exactness, fixture | `pytest ... -s` (`test_p_over_cal_equals_the_parlay_producers_p_cal_for_the_same_leg`) | p_over_raw 0.4623115577889447, p_over_cal 0.4849950867977915, `==` producer leg (bit equal) | PASS |
| 2 | Exactness, real rows | `uv run --no-sync python attack/lc_parity.py` / `lc_rows.py`, exit 0 | 143 values on 1,860 rows; 95 on t15 rows with line after production `made_at` (not a producer leg); producer built 0 legs that day, so no real-row bit comparison was possible | WOUNDED (F1) |
| 3 | q19 vs rule's q199 | `attack/lc_z.py`, exit 0 | n=96: max\|dz\| 0.00067, max\|dp_cal\| 7.6e-5 | PASS for the branch; F3 for the producer |
| 4 | Null reasons | test file lines 92-123, 126-146; real run | fixed list of 10; every reason exercised; `n_line_cal + sum(null_reasons) == n_props` asserted end to end and held on real data (143 + 1,717 = 1,860) | PASS |
| 5 | Schema / older exports | read `build.py:117-122`, contract | `SCHEMA_VERSION = 2` unchanged; absent-column tolerance not stated | PASS with F4 |
| 6 | Tests | `pytest tests/export tests/parlay tests/test_layering.py --no-cov -q -p no:cacheprovider` | `282 passed, 2 skipped, 64801 warnings in 22.28s`, exit 0 | PASS |
| 6 | Lint | `ruff check --no-cache` on line_cal_fields.py, build.py, test file | `All checks passed!`, exit 0 | PASS |
| 6 | Format | `ruff format --check --no-cache` (same 3) | `3 files already formatted`, exit 0 | PASS |
| 6 | Types | `mypy --cache-dir=/dev/null nba/export/line_cal_fields.py nba/export/build.py` | `Success: no issues found in 2 source files`, exit 0 (tests are outside the mypy `files` config; adding the test file reports 4 pre-existing errors in test_best_model.py / test_rung_cal_fields.py) | PASS |
| 7 | Known-at | `rung_cal_fields.py:60` (`ts < made_at and ts < tipoff`); `reader.py` prop_lines (`received_at < tipoff AND received_at < commence_time`, `r[8] <= made_at`) | every line used is pre-tip and before the displayed row's `made_at`. Relative to the production row it can be later (F1): a parity issue, not a leak | PASS |

Multiplicity and power: n/a. No statistical claim is made. The numbers are deterministic equality and deviation measurements.

## What I could not verify

- Bit parity against a real panel leg: the producer built no legs on 2026-10-20 (F5), and 2026-10-21 had no rows yet.
- That the app will label and handle the field as the contract says: no app code reads `line_cal` yet.
- rung_cal's own spread definition (`rc.grid_med_spr` differs from (q75-q25)/1.349 by up to 0.147 on these rows). It belongs to
  ALT_RUNG_CAL's map, which this branch does not touch. Not reviewed.

## Recommended changes (for the main session; none applied by me)

1. F1: key `line_cal` to the production row's run and `made_at`; one value per prop, copied to every row of the prop; add the t15
   test; add distinct-prop counts to the manifest.
2. F2 and F4: two contract edits (reword the parity sentence and list the non-parity cases; add the absent-column sentence).
3. F3: Devin's call on the producer. Either switch `top_source.quartiles` to the persisted q199 at the rule's positions, or log a
   DECISIONS row accepting the 19-point source (max |dp_cal| 7.6e-5, n=96).
After 1 and 2, a diff-check by the adversary is enough; a full re-review is not needed.

## Drafted DECISIONS row (not appended)

| 2026-10-21 | The parlay producer reads the LINE_CALIBRATION median and spread from the stored 19-point q_grid, not from the 199-point grid that the frozen rule text names (LINE_CALIBRATION.md:26-27). Accepted as equivalent / to be switched (Devin's choice). | Measured on 96 real season-2026 (prop, DraftKings main line) pairs: max \|dz\| 0.00067, max \|dp_cal\| 7.6e-5, under a tenth of the display resolution. Reverse if any served cell's \|dp_cal\| exceeds 0.001. |

## Re-check a0b15dd (2026-10-21)

Verdict: APPROVED

Read with `git diff 2d5da83..a0b15dd`: 3 files (line_cal_fields.py, the test file, the contract). No new scope.
- **F1 fixed.** `_one()` computes one value per (game, player, stat) from the production row. It keys quotes to that row's run
  (`lines_run_id`, which equals the production row's own `run_id`) and calls `rcf.main_line(..., made_at + 1 microsecond, tipoff)`. Python
  datetimes and DuckDB TIMESTAMP both have microsecond resolution, so `ts < made_at + 1 µs` is exactly `ts <= made_at`; a later quote cannot
  get in. The reader also filters `received_at <= made_at` on the same run. The value is copied to every row of the prop, and
  `no_production_row` is checked first. The new test `test_a_later_row_of_the_prop_gets_the_production_rows_line_never_a_later_one` covers
  four cases: a later run's 24.5 line is never used (22.5 is); both rows go NULL with `no_book_line` when production's run has no line; a
  quote stamped exactly at `made_at` counts; and the distinct counts.
- **Real slate 2026-10-20** (read-only, season 2026; `attack/lc_rows.py`, exit 0): 60 `line_cal` values on 1,860 rows (12 distinct
  props x 5 rows). Rows whose line postdates the production `made_at`: **0** (was 95 of 143). Primary rows with a value: 12 of 424,
  unchanged.
- **F2 and F4 fixed** in the contract: parity is now stated for the same production row and line, non-parity cases (a)-(d) are listed,
  and older exports lacking the columns or the manifest key are read as NULL. Distinct counts are documented.
- **F3 unchanged, by design.** It is a producer question for Devin; the drafted DECISIONS row above still applies.
- Checks in wt_export at a0b15dd, all unpiped:

| Command | Result | Exit |
|---|---|---|
| `pytest tests/export tests/parlay tests/test_layering.py --no-cov -q` | `283 passed, 2 skipped, 64801 warnings in 22.56s` | 0 |
| `ruff check` | clean | 0 |
| `ruff format --check` | clean | 0 |
| `mypy` (line_cal_fields.py, build.py) | `Success: no issues found in 2 source files` | 0 |

Nits (non-blocking; fix in passing):
1. `docs/contracts/line_cal_export_v1.md:24-27` still lists `no_book_line, ambiguous_main_line` before `no_production_row` and says
   "checked in that order", but `_one()` now checks `no_production_row` first. Move `no_production_row` to the head of the list.
2. In the new test, `_board(24.5, late) and [...]` evaluates to the right-hand list only. Pass the list directly.
